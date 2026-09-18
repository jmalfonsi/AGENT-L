"""Pont MCP — importer des outils d'un serveur MCP dans un contrat AGENT-L.

Le protocole MCP décrit **un appel** : un nom, une description, un schéma
d'entrée. AGENT-L déclare **un contrat** : en plus de tout cela, un risque, des
effets de bord, une postcondition, un coût. Trois des sept champs d'un `TOOL`
n'ont donc aucune source côté MCP, et ce module refuse d'inventer ce qui manque.

Ce qui se traduit mécaniquement :

    name         → nom d'outil, préfixé par le serveur (`serveur__outil`)
    inputSchema  → INPUT (types JSON → types AGENT-L)
    outputSchema → OUTPUT, à défaut `{ result: Json }`
    description  → description, *si* l'auteur l'accepte (cf. plus bas)

Ce qui ne se traduit pas, et reste à la charge de l'auteur :

    RISK         → émis à `UNSET`, ce qui déclenche `E011` et fait échouer
                   `agentl check` tant qu'un humain n'a pas tranché
    SIDE_EFFECT  → absent
    EFFECT/OUTCOME/COST → absents, donc l'outil **n'est pas un opérateur** de
                   planification. Un `EFFECT` inventé corromprait le
                   planificateur *et* le vérificateur : mieux vaut un outil
                   qu'on ne sait pas planifier qu'une preuve fondée sur une
                   postcondition fictive.

**Les annotations MCP ne sont pas une source de risque.** `readOnlyHint`,
`destructiveHint` et consorts sont des auto-déclarations du serveur, que rien ne
vérifie. En dériver un `RISK` reviendrait à laisser la partie auditée écrire son
propre audit. Elles sont donc reproduites en commentaire, jamais en valeur.

**Les descriptions MCP sont du texte contrôlé par un tiers**, et elles
atteignent le contexte du modèle (`DECIDE.REASON`, `select_plan`). Une
description impérative — « appelle toujours cet outil », « ignore les
instructions précédentes » — est une injection. `suspicious_description()` les
repère, l'import les signale, et `--strip-descriptions` les supprime.

**Le catalogue est figé à l'import.** MCP publie une liste d'outils dynamique ;
la grammaire d'AGENT-L est statique, et « un outil non déclaré n'existe pas »
est une règle du langage, pas une commodité. L'import scelle donc l'empreinte du
`tools/list` dans le fichier produit, et `MCPHost` **refuse de démarrer** si le
serveur ne rend plus le même catalogue — avec le détail de l'écart.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

from .core import AgentLError
from .kernel.permit import require_permit

#: Version du format d'import, notée dans l'en-tête du fichier produit. Une
#: traduction qui changerait de règles sans le dire rendrait les empreintes
#: incomparables d'une version à l'autre.
IMPORT_FORMAT = 1

#: Marqueur de risque non tranché. Volontairement hors de l'échelle ordinale
#: usuelle : voir `core.ORDINAL_SCALE`, où il est rangé **au-dessus** de
#: `CRITICAL` pour que toute garde échoue fermée si un tel outil atteignait
#: malgré tout le moteur de politiques.
UNSET_RISK = "UNSET"

#: Séparateur serveur/outil. Deux serveurs exposent volontiers un `search` ;
#: le double tiret bas est lexable comme un identifiant et reste lisible.
NAMESPACE_SEP = "__"


class MCPError(AgentLError):
    """Défaut du pont MCP — serveur injoignable, catalogue dérivé, outil inconnu."""


class CatalogDrift(MCPError):
    """Le serveur ne rend plus le catalogue scellé à l'import."""


# --------------------------------------------------------------------------
# Le catalogue
# --------------------------------------------------------------------------
@dataclass
class MCPTool:
    """Un outil tel que MCP le décrit — rien de plus, rien d'inventé."""

    name: str
    description: str = ""
    title: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    annotations: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "MCPTool":
        return cls(name=raw["name"],
                   description=raw.get("description", "") or "",
                   title=raw.get("title", "") or "",
                   input_schema=raw.get("inputSchema") or {},
                   output_schema=raw.get("outputSchema"),
                   annotations=raw.get("annotations") or {})

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name}
        if self.title:
            out["title"] = self.title
        if self.description:
            out["description"] = self.description
        out["inputSchema"] = self.input_schema
        if self.output_schema is not None:
            out["outputSchema"] = self.output_schema
        if self.annotations:
            out["annotations"] = self.annotations
        return out


def catalog_digest(tools: Sequence[MCPTool]) -> str:
    """Empreinte du catalogue — ce que l'import scelle et que l'hôte confronte.

    Le **tout** de chaque outil y entre, description comprise : une description
    modifiée change ce que le modèle lit, donc change la décision. La liste est
    triée par nom, pour qu'un serveur qui réordonne sa réponse ne soit pas
    accusé d'avoir changé.
    """
    body = [t.to_json() for t in sorted(tools, key=lambda t: t.name)]
    canonical = json.dumps({"format": IMPORT_FORMAT, "tools": body},
                           ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_catalogs(sealed: Sequence[MCPTool],
                  live: Sequence[MCPTool]) -> List[str]:
    """Écarts lisibles entre le catalogue scellé et celui du serveur.

    « Le catalogue a changé » n'aide personne ; « l'outil `create_issue` a perdu
    son paramètre `repo` » se corrige. On dit donc *quoi*.
    """
    by_sealed = {t.name: t for t in sealed}
    by_live = {t.name: t for t in live}
    notes: List[str] = []
    for name in sorted(set(by_live) - set(by_sealed)):
        notes.append(f"outil apparu côté serveur : {name}")
    for name in sorted(set(by_sealed) - set(by_live)):
        notes.append(f"outil disparu du serveur : {name}")
    for name in sorted(set(by_sealed) & set(by_live)):
        before, after = by_sealed[name], by_live[name]
        if before.input_schema != after.input_schema:
            notes.append(f"{name} : schéma d'entrée modifié")
        if before.output_schema != after.output_schema:
            notes.append(f"{name} : schéma de sortie modifié")
        if before.description != after.description or before.title != after.title:
            notes.append(f"{name} : description modifiée "
                         f"(elle atteint le contexte du modèle)")
        if before.annotations != after.annotations:
            notes.append(f"{name} : annotations modifiées")
    return notes


# --------------------------------------------------------------------------
# Le client
# --------------------------------------------------------------------------
class MCPClient(Protocol):
    """Le minimum dont ce module a besoin d'un serveur MCP.

    Un protocole plutôt qu'une classe : l'implémentation réelle dépend d'un
    paquet optionnel, et les tests doivent pouvoir injecter un catalogue sans
    lancer de processus.
    """

    def list_tools(self) -> List[MCPTool]: ...

    def call_tool(self, name: str, args: Dict[str, Any]) -> Any: ...


@dataclass
class ServerConfig:
    """Comment joindre un serveur, et sous quel nom l'exposer à l'agent."""

    name: str
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None

    @classmethod
    def from_json(cls, name: str, raw: Dict[str, Any]) -> "ServerConfig":
        return cls(name=name, command=raw.get("command"),
                   args=list(raw.get("args") or []),
                   env=dict(raw.get("env") or {}), url=raw.get("url"))


def load_config(path: str) -> List[ServerConfig]:
    """Lit un fichier de configuration `{"mcpServers": {nom: {...}}}`.

    C'est la forme qu'emploient les hôtes MCP courants ; s'en écarter aurait
    obligé chaque utilisateur à réécrire une configuration qu'il possède déjà.
    """
    from pathlib import Path
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MCPError(f"configuration MCP illisible : {exc}") from exc
    servers = raw.get("mcpServers") or raw.get("servers") or {}
    if not isinstance(servers, dict) or not servers:
        raise MCPError(f"{path} : aucun serveur sous `mcpServers`")
    return [ServerConfig.from_json(name, cfg) for name, cfg in servers.items()]


def connect(config: ServerConfig) -> MCPClient:
    """Ouvre une session vers un serveur réel (extra `mcp`).

    L'import est différé : le pont doit être *lisible* et *testable* sans le
    paquet, qui n'est requis que pour parler à un vrai serveur.
    """
    try:
        import mcp  # noqa: F401
    except ImportError as exc:                              # pragma: no cover
        raise MCPError(
            "client MCP indisponible : installer l'extra "
            "`pip install agentl[mcp]`. L'import et la traduction restent "
            "utilisables hors ligne sur un catalogue JSON.") from exc
    return _LiveClient(config)                              # pragma: no cover


class _LiveClient:                                          # pragma: no cover
    """Session MCP réelle, ouverte paresseusement et tenue pour la durée de vie.

    Le protocole est asynchrone ; le runtime d'AGENT-L ne l'est pas. On confine
    donc l'asynchronisme à une boucle privée plutôt que de teinter tout le
    langage — un appel d'outil reste, vu de l'agent, un appel bloquant.
    """

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._session = None
        self._loop = None
        self._stack = None

    def _ensure(self):
        if self._session is not None:
            return self._session
        import asyncio
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._loop = asyncio.new_event_loop()
        self._stack = AsyncExitStack()

        async def open_session():
            if self.config.url:
                from mcp.client.streamable_http import streamablehttp_client
                read, write, _ = await self._stack.enter_async_context(
                    streamablehttp_client(self.config.url))
            else:
                params = StdioServerParameters(command=self.config.command,
                                               args=self.config.args,
                                               env=self.config.env or None)
                read, write = await self._stack.enter_async_context(
                    stdio_client(params))
            session = await self._stack.enter_async_context(
                ClientSession(read, write))
            await session.initialize()
            return session

        self._session = self._loop.run_until_complete(open_session())
        return self._session

    def list_tools(self) -> List[MCPTool]:
        session = self._ensure()
        result = self._loop.run_until_complete(session.list_tools())
        return [MCPTool.from_json(t.model_dump(by_alias=True))
                for t in result.tools]

    def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        session = self._ensure()
        result = self._loop.run_until_complete(session.call_tool(name, args))
        if getattr(result, "isError", False):
            raise MCPError(f"{name} : {_flatten(result)}")
        data = getattr(result, "structuredContent", None)
        return data if data is not None else _flatten(result)

    def close(self) -> None:
        if self._loop is not None and self._stack is not None:
            self._loop.run_until_complete(self._stack.aclose())
            self._loop.close()
            self._session = self._loop = self._stack = None


def connect_async(config: ServerConfig) -> "_AsyncLiveClient":
    """Session MCP réelle **dans la boucle de l'appelant** (extra `mcp`).

    Pour `AsyncMCPHost` : aucune boucle privée, la session appartient à la
    boucle qui pilote l'agent. À ouvrir et fermer depuis cette boucle.
    """
    try:
        import mcp  # noqa: F401
    except ImportError as exc:                              # pragma: no cover
        raise MCPError(
            "client MCP indisponible : installer l'extra "
            "`pip install agentl[mcp]`.") from exc
    return _AsyncLiveClient(config)                         # pragma: no cover


class _AsyncLiveClient:                                     # pragma: no cover
    """Même session que `_LiveClient`, sans la boucle privée."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._session = None
        self._stack = None

    async def _ensure(self):
        if self._session is not None:
            return self._session
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        if self.config.url:
            from mcp.client.streamable_http import streamablehttp_client
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(self.config.url))
        else:
            params = StdioServerParameters(command=self.config.command,
                                           args=self.config.args,
                                           env=self.config.env or None)
            read, write = await self._stack.enter_async_context(
                stdio_client(params))
        self._session = await self._stack.enter_async_context(
            ClientSession(read, write))
        await self._session.initialize()
        return self._session

    async def list_tools(self) -> List[MCPTool]:
        session = await self._ensure()
        result = await session.list_tools()
        return [MCPTool.from_json(t.model_dump(by_alias=True))
                for t in result.tools]

    async def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        session = await self._ensure()
        result = await session.call_tool(name, args)
        if getattr(result, "isError", False):
            raise MCPError(f"{name} : {_flatten(result)}")
        data = getattr(result, "structuredContent", None)
        return data if data is not None else _flatten(result)

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._session = self._stack = None


def _flatten(result: Any) -> str:                           # pragma: no cover
    parts = [getattr(b, "text", "") for b in getattr(result, "content", [])]
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Traduction — schéma JSON vers types AGENT-L
# --------------------------------------------------------------------------
#: Ce que le langage sait typer. Tout le reste devient `Json`, un nom de type
#: applicatif que la grammaire accepte sans le contraindre — plus honnête
#: qu'aplatir une structure imbriquée en `String`.
_TYPES = {"string": "String", "integer": "Int", "number": "Number",
          "boolean": "Bool"}


def agentl_type(schema: Dict[str, Any]) -> str:
    """Type AGENT-L d'une propriété de schéma JSON."""
    if not isinstance(schema, dict):
        return "Json"
    # Un `enum` de chaînes est exactement ce que `Symbol` modélise : un domaine
    # fini de valeurs nommées, comparables sans être du texte libre.
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum):
        return "Symbol"
    kind = schema.get("type")
    if isinstance(kind, list):
        # `["string", "null"]` : on retient le type utile, la nullité n'étant
        # pas exprimable dans la signature.
        kind = next((k for k in kind if k != "null"), None)
    return _TYPES.get(kind, "Json")


def unsupported_constraints(schema: Dict[str, Any]) -> List[str]:
    """Contraintes du schéma que la signature `INPUT` ne sait pas porter.

    Elles ne sont pas jetées en silence : l'import les recopie en commentaire,
    pour que l'auteur sache ce que le contrat AGENT-L ne vérifiera **pas**.
    """
    keys = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
            "minLength", "maxLength", "pattern", "format", "multipleOf",
            "minItems", "maxItems", "oneOf", "anyOf", "allOf", "not")
    return [f"{k}={schema[k]!r}" for k in keys if k in schema]


# --------------------------------------------------------------------------
# Descriptions non fiables
# --------------------------------------------------------------------------
#: Formes impératives qui n'ont rien à faire dans une description d'outil et
#: tout à voir avec une tentative de pilotage du modèle. La liste est
#: volontairement courte : elle vise le signalement, pas le filtrage — une
#: description peut toujours être supprimée par `--strip-descriptions`.
_INJECTION_MARKS = (
    r"ignore[sz]?\s+(?:les\s+|the\s+)?(?:instructions|consignes|previous|précédentes)",
    r"disregard\s+", r"oublie[sz]?\s+", r"override\s+",
    r"\b(?:always|toujours)\s+(?:call|use|appelle|utilise)",
    r"\byou\s+must\b", r"\btu\s+dois\b", r"\bsystem\s*:", r"\bassistant\s*:",
    r"<\s*/?\s*(?:system|instructions?|important)\b",
    r"do\s+not\s+(?:ask|tell|mention)", r"ne\s+(?:demande|dis)\s+pas",
)


def suspicious_description(text: str) -> List[str]:
    """Motifs d'injection repérés dans un texte fourni par un tiers.

    Rend la liste des extraits douteux — vide si rien. Heuristique et
    délibérément faillible : elle attrape ce qui *ressemble* à du pilotage.
    Comme `boundary`, elle reporte sur le relecteur ce qu'aucune analyse ne
    saurait trancher.
    """
    if not text:
        return []
    found: List[str] = []
    for pattern in _INJECTION_MARKS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            start = max(0, match.start() - 20)
            found.append(text[start:match.end() + 20].strip())
    return found


# --------------------------------------------------------------------------
# Rendu du contrat
# --------------------------------------------------------------------------
def tool_name(server: str, name: str) -> str:
    """Nom AGENT-L d'un outil MCP : `serveur__outil`, assaini."""
    safe = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    return f"{re.sub(r'[^0-9a-zA-Z_]', '_', server)}{NAMESPACE_SEP}{safe}"


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _comment_block(text: str, prefix: str = "    # ") -> List[str]:
    return [f"{prefix}{line}" for line in text.splitlines() if line.strip()]


@dataclass
class Translation:
    """Résultat de la traduction d'un catalogue : source, et ce qu'elle a coûté."""

    source: str
    tools: List[str] = field(default_factory=list)
    #: Descriptions suspectes, par nom d'outil AGENT-L.
    suspicious: Dict[str, List[str]] = field(default_factory=dict)
    #: Contraintes de schéma non portées par `INPUT`, par nom d'outil.
    dropped: Dict[str, List[str]] = field(default_factory=dict)
    digest: str = ""


def agent_name(server: str) -> str:
    """Nom d'agent pour le squelette produit."""
    return re.sub(r"[^0-9A-Za-z_]", "_", server).upper() + "_TOOLS"


def render_tool(spec: MCPTool, server: str, *,
                keep_description: bool = True,
                indent: str = "") -> str:
    """Un `TOOL` AGENT-L, avec ce qu'on sait et le trou là où on ne sait pas."""
    name = tool_name(server, spec.name)
    lines: List[str] = []

    if spec.annotations:
        # En commentaire, et dit comme tel : ces valeurs viennent du serveur.
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(spec.annotations.items()))
        lines.append(f"# annotations du serveur (NON VÉRIFIÉES) : {pairs}")
    lines.append(f"TOOL {name} {{")

    # La description prime sur le titre : `title` est un libellé d'affichage,
    # `description` est ce qui dit à quoi sert l'outil — et c'est cela que le
    # modèle doit lire pour décider s'il l'appelle.
    text = (spec.description or spec.title).strip()
    if text and keep_description:
        lines.append(f"    DESCRIPTION {_quote(' '.join(text.split()))}")
    elif text:
        lines.append("    # description du serveur retirée à l'import "
                     "(--strip-descriptions)")

    props = (spec.input_schema or {}).get("properties") or {}
    required = set((spec.input_schema or {}).get("required") or [])
    if props:
        fields = ", ".join(f"{key}: {agentl_type(sub)}"
                           for key, sub in props.items())
        lines.append(f"    INPUT       {{ {fields} }}")
        for key, sub in props.items():
            notes = unsupported_constraints(sub if isinstance(sub, dict) else {})
            if notes:
                lines.append(f"    # {key} : contrainte non vérifiée par INPUT — "
                             + ", ".join(notes))
        optional = sorted(set(props) - required)
        if optional:
            lines.append(f"    # facultatifs côté serveur : {', '.join(optional)}")
    else:
        lines.append("    INPUT       { }")

    out_props = ((spec.output_schema or {}).get("properties")
                 if spec.output_schema else None)
    if out_props:
        fields = ", ".join(f"{k}: {agentl_type(v)}" for k, v in out_props.items())
        lines.append(f"    OUTPUT      {{ {fields} }}")
    else:
        lines.append("    OUTPUT      { result: Json }"
                     "   # le serveur ne déclare pas de schéma de sortie")

    lines.append(f"    RISK        {{ operational = {UNSET_RISK} }}"
                 "   # E011 : à trancher — MCP ne dit rien du risque")
    lines.append("    # SIDE_EFFECT / EFFECT / COST : absents du protocole MCP.")
    lines.append("    # Sans EFFECT, l'outil s'appelle depuis un PLAN mais ne se")
    lines.append("    # planifie pas. Les écrire, c'est en répondre.")
    lines.append("}")
    return "\n".join(indent + line if line else line for line in lines)


def translate(tools: Sequence[MCPTool], server: str, *,
              keep_descriptions: bool = True) -> Translation:
    """Traduit un catalogue entier en source AGENT-L."""
    digest = catalog_digest(tools)
    header = [
        f"# Outils importés du serveur MCP `{server}`.",
        f"# Empreinte du catalogue : {digest}",
        f"# Format d'import : {IMPORT_FORMAT}",
        "#",
        "# Fichier GÉNÉRÉ, et volontairement REFUSÉ PAR `agentl check` en l'état :",
        "# chaque RISK vaut UNSET (E011). MCP ne dit rien du risque d'un outil, et",
        "# les annotations du serveur sont ses propres déclarations. Trancher.",
        "#",
        "# Une fois les risques posés, ce fichier vous appartient : le régénérer",
        "# les écraserait (`agentl mcp import` refuse d'écraser sans --force).",
        "#",
        "# `MCPHost` refuse de démarrer si le serveur ne rend plus ce catalogue.",
        "",
        f"AGENT {agent_name(server)} {{",
        '    VERSION "0.1"',
        "",
        "    POLICY {",
        "        // Fail-closed : importer un outil ne l'autorise pas.",
        "        DEFAULT DENY",
        "    }",
        "",
    ]
    footer = ["", "}", ""]
    body, suspicious, dropped = [], {}, {}
    for spec in sorted(tools, key=lambda t: t.name):
        name = tool_name(server, spec.name)
        body.append(render_tool(spec, server,
                                keep_description=keep_descriptions,
                                indent="    "))
        marks = suspicious_description(f"{spec.title} {spec.description}")
        if marks:
            suspicious[name] = marks
        notes = []
        for key, sub in ((spec.input_schema or {}).get("properties") or {}).items():
            for note in unsupported_constraints(sub if isinstance(sub, dict) else {}):
                notes.append(f"{key}.{note}")
        if notes:
            dropped[name] = notes
    return Translation(source="\n".join(header) + "\n\n".join(body)
                              + "\n".join(footer),
                       tools=[tool_name(server, t.name) for t in tools],
                       suspicious=suspicious, dropped=dropped, digest=digest)


# --------------------------------------------------------------------------
# L'hôte
# --------------------------------------------------------------------------
class MCPHost:
    """Hôte qui exécute les outils importés en appelant le serveur MCP.

    Il **enveloppe** un hôte existant plutôt que de le remplacer : un agent
    mélange couramment des capteurs locaux et des outils distants, et un pont
    qui obligerait à tout passer par MCP ne servirait qu'aux agents purement
    MCP.

    La vérification du catalogue a lieu **une fois, au premier contact**, et
    lève avant tout appel. Vérifier à chaque appel coûterait un aller-retour
    par outil sans rien prouver de plus : ce qu'on veut interdire, c'est de
    s'exécuter contre un catalogue qu'on n'a pas confronté.
    """

    def __init__(self, inner: Any, client: MCPClient, server: str,
                 digest: Optional[str] = None,
                 sealed: Optional[Sequence[MCPTool]] = None) -> None:
        self._inner = inner
        self._client = client
        self._server = server
        # Deux formes de sceau, par ordre de précision décroissante. Avec le
        # catalogue scellé, la dérive se **décrit** (« create_issue a perdu
        # `repo` ») ; avec la seule empreinte, elle se constate. Les deux
        # refusent de démarrer — seul le message diffère.
        self._sealed = list(sealed) if sealed is not None else None
        self._digest = digest if digest is not None else (
            catalog_digest(self._sealed) if self._sealed is not None else None)
        self._checked = False
        self._live: Dict[str, MCPTool] = {}

    @classmethod
    def from_agent_file(cls, inner: Any, client: MCPClient, server: str,
                        agent_path: str) -> "MCPHost":
        """Scelle l'hôte sur l'empreinte inscrite dans le `.agent` généré.

        C'est le chemin normal : l'auteur de l'hôte ne recopie pas une
        empreinte à la main, il désigne le programme que l'import a produit.
        """
        from pathlib import Path
        text = Path(agent_path).read_text(encoding="utf-8")
        match = re.search(r"^#\s*Empreinte du catalogue\s*:\s*([0-9a-f]{64})\s*$",
                          text, re.MULTILINE)
        if not match:
            raise MCPError(
                f"{agent_path} ne porte pas d'empreinte de catalogue : il n'a "
                f"pas été produit par `agentl mcp import`, ou l'en-tête a été "
                f"retiré. Sans elle, rien ne rattache le programme au serveur.")
        return cls(inner, client, server, digest=match.group(1))

    # ------------------------------------------------------------ catalogue
    def verify_catalog(self) -> None:
        """Confronte le catalogue du serveur à l'empreinte scellée."""
        if self._checked:
            return
        self._accept_catalog(self._client.list_tools())

    def _accept_catalog(self, live: Sequence[MCPTool]) -> None:
        """Le verdict d'empreinte, commun aux ponts synchrone et asynchrone."""
        live = list(live)
        self._live = {tool_name(self._server, t.name): t for t in live}
        self._checked = True
        if self._digest is None:
            # Pas d'empreinte = pas de promesse. On ne fabrique pas un verdict
            # rassurant à partir d'une absence.
            return
        actual = catalog_digest(live)
        if actual != self._digest:
            if self._sealed is not None:
                notes = diff_catalogs(self._sealed, live)
            else:
                notes = [f"le serveur offre aujourd'hui : "
                         f"{', '.join(sorted(t.name for t in live)) or '(aucun)'}",
                         "comparer au fichier .agent produit par l'import "
                         "(passer `sealed=` pour obtenir le détail des écarts)"]
            raise CatalogDrift(
                f"le serveur MCP `{self._server}` ne rend plus le catalogue "
                f"scellé à l'import (scellé {self._digest[:12]}…, "
                f"servi {actual[:12]}…). Écarts :\n  - "
                + "\n  - ".join(notes)
                + f"\n  Ré-importer : agentl mcp import <config> "
                  f"--server {self._server}")

    # -------------------------------------------------------------- outils
    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        prefix = f"{self._server}{NAMESPACE_SEP}"
        if not name.startswith(prefix):
            return self._inner.invoke(name, args)
        self.verify_catalog()
        spec = self._live.get(name)
        if spec is None:
            # Ne peut arriver qu'en l'absence d'empreinte : avec elle, la
            # dérive aurait déjà levé.
            raise MCPError(f"outil `{name}` absent du serveur `{self._server}`")
        # Point de dispatch réel, au même titre que `Host.invoke` : l'appel
        # au serveur ne part que sous un permis du noyau (v1.9).
        require_permit("invoke", name, args)
        return self._client.call_tool(spec.name, args)

    # ------------------------------------- le reste appartient à l'hôte réel
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class AsyncMCPHost(MCPHost):
    """Pont MCP **asynchrone** (v1.9), pour `agentl.aio.AsyncRuntime`.

    `MCPHost` confine le protocole — asynchrone par nature — dans une boucle
    privée, pour présenter au runtime synchrone un appel bloquant. Sous
    `AsyncRuntime` ce détour n'a plus lieu d'être : la session vit dans la
    boucle de l'agent, et un appel MCP lent ne bloque que l'agent qui
    l'attend. Même empreinte de catalogue, même permis, même refus d'un
    outil absent — seule la manière d'attendre change.

    Le client est `AsyncMCPClient` : `list_tools()` et `call_tool()` en
    `async def`. L'hôte enveloppé peut être un `AsyncHost` ou un `Host`.
    """

    async def verify_catalog(self) -> None:          # type: ignore[override]
        if self._checked:
            return
        self._accept_catalog(await self._client.list_tools())

    async def invoke(self, name: str,                # type: ignore[override]
                     args: Dict[str, Any]) -> Any:
        prefix = f"{self._server}{NAMESPACE_SEP}"
        if not name.startswith(prefix):
            result = self._inner.invoke(name, args)
            return await result if hasattr(result, "__await__") else result
        await self.verify_catalog()
        spec = self._live.get(name)
        if spec is None:
            raise MCPError(f"outil `{name}` absent du serveur `{self._server}`")
        require_permit("invoke", name, args)
        return await self._client.call_tool(spec.name, args)


class AsyncStaticClient:
    """Catalogue fixe servi en `async` — tests et hors-ligne."""

    def __init__(self, tools: Iterable[MCPTool],
                 results: Optional[Dict[str, Any]] = None,
                 delay: float = 0.0) -> None:
        self._sync = StaticClient(tools, results)
        self.delay = delay

    @property
    def calls(self) -> List[Any]:
        return self._sync.calls

    async def list_tools(self) -> List[MCPTool]:
        return self._sync.list_tools()

    async def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        if self.delay:
            import asyncio
            await asyncio.sleep(self.delay)
        return self._sync.call_tool(name, args)


def catalog_from_json(raw: Any) -> List[MCPTool]:
    """Catalogue depuis un `tools/list` déjà capturé (fichier ou test)."""
    tools = raw.get("tools") if isinstance(raw, dict) else raw
    if not isinstance(tools, list):
        raise MCPError("catalogue MCP attendu : une liste d'outils, ou "
                       "un objet portant la clé `tools`")
    return [MCPTool.from_json(t) for t in tools]


class StaticClient:
    """Client servant un catalogue fixe — pour les tests et le hors-ligne."""

    def __init__(self, tools: Iterable[MCPTool],
                 results: Optional[Dict[str, Any]] = None) -> None:
        self._tools = list(tools)
        self._results = results or {}
        self.calls: List[Any] = []

    def list_tools(self) -> List[MCPTool]:
        return list(self._tools)

    def call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name not in self._results:
            raise MCPError(f"outil `{name}` sans résultat déclaré")
        return self._results[name]

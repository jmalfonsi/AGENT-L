"""Rejeu déterministe — enregistrer une exécution, puis la re-dériver.

Une trace permet de **lire** pourquoi l'agent a agi. Elle ne permet pas de
**re-dériver** la décision : c'est ce que ce module ajoute, et c'est ce qu'un
auditeur demande quand il dit « reproduisez cette décision ».

L'observation qui rend la chose bon marché : le cœur d'AGENT-L n'a **aucune
source de non-déterminisme propre** — ni horloge, ni tirage aléatoire, ni
identifiant volatil. Tout ce qui varie d'une exécution à l'autre traverse la
frontière de l'hôte ou celle du modèle, soit huit points d'entrée :

    Host.read()    Host.invoke()   Host.ask()    Host.approve()
    Host.drain()   DELEGATE        LLM.reason()  LLM.select_plan()

Journaliser ces huit-là suffit. Le contrat est donc :

    host, llm = RecordingHost(host, journal), RecordingLLM(llm, journal)
    Runtime(agent, host, llm).run()
    journal.save("run.json")

    # plus tard, sans le monde réel :
    journal = Journal.load("run.json")
    Runtime(agent, ReplayHost(journal), ReplayLLM(journal)).run()

et la propriété visée est vérifiable, pas déclarative : **la trace rejouée est
identique caractère pour caractère** à la trace enregistrée. Le journal porte
l'empreinte de la trace d'origine (`meta["trace_sha256"]`) ; `verify_trace()`
la confronte.

Cette empreinte dit que l'exécution se re-dérive ; elle ne dit pas que *ce
fichier-ci* est celui qui fut produit. C'est l'affaire du scellement
(`seal.py`) : chaque entrée porte un condensat chaîné, et `Journal.load()`
refuse par défaut un journal altéré — rejouer une pièce corrompue puis
annoncer « conforme » serait le pire des résultats. `seal(key=…)` y ajoute une
signature, seule à distinguer un journal authentique d'un journal fabriqué.

Deux exigences de fidélité, faciles à manquer :

  * **Les pannes se rejouent comme des pannes.** Le runtime est tolérant : un
    capteur qui lève ne fait pas tomber la boucle, il produit un `ERROR` dans
    la trace où figure `type(exc).__name__`. Un rejeu qui rendrait `None` au
    lieu de lever changerait la trace. On réinstancie donc une exception
    portant le nom d'origine.
  * **La divergence est une erreur, jamais un silence.** Si le programme
    rejoué demande autre chose que ce qui fut enregistré — capteur différent,
    outil différent, ordre différent — le rejeu s'arrête sur
    `ReplayDivergence`. Un rejeu qui « s'arrange » ne prouve rien.

Limite assumée : les valeurs franchissant la frontière doivent être
sérialisables (scalaires, `Symbol`, listes, dictionnaires). Un objet opaque
est enregistré par son `repr` et le journal se déclare **lacunaire**
(`journal.lossy`) — visible dans `meta`, jamais masqué.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .core import AgentLError, Symbol
from .seal import (ChainReport, SignatureReport, SigningKey, chain_hashes,
                   chain_head, sign, verify_chain, verify_signature)

#: Format 2 = format 1 + chaîne de hachage par entrée et sceau facultatif.
#: Les journaux de format 1 restent lisibles, mais jamais présentés comme
#: intègres : ils n'ont rien qui permette de l'établir.
JOURNAL_FORMAT = 2
READABLE_FORMATS = (1, 2)

#: Les huit points d'entrée journalisés. `delegate_lookup` est le neuvième
#: enregistrement, mais pas un point de non-déterminisme : il note seulement
#: si un sous-agent était présent, pour que son absence se rejoue aussi.
KINDS = ("read", "invoke", "ask", "approve", "drain", "delegate",
         "delegate_lookup", "reason", "select_plan")


#: Sentinelle : « cet appel ne vérifie pas ses arguments ». Distinguer de
#: `None`, qui est un argument enregistré légitime.
_NO_ARGS = object()


class ReplayError(AgentLError):
    """Défaut de rejeu — le journal ne colle pas à ce qu'on lui demande."""


class ReplayDivergence(ReplayError):
    """L'exécution rejouée s'écarte de l'exécution enregistrée."""


# --------------------------------------------------------------------------
# Encodage
# --------------------------------------------------------------------------
def encode(value: Any, lossy: Optional[List[str]] = None, path: str = "") -> Any:
    """Rend une valeur de frontière sérialisable, sans perdre les `Symbol`.

    Un `Symbol` n'est pas une chaîne : `healthy` et `"healthy"` se comparent
    égaux mais ne s'affichent pas pareil dans une trace. Le distinguer est
    donc une exigence de fidélité, pas une coquetterie de typage.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # Ni NaN ni ±inf en JSON : on les marque plutôt que de les perdre.
        if value != value or value in (float("inf"), float("-inf")):
            return {"$float": repr(value)}
        return value
    if isinstance(value, Symbol):
        return {"$sym": value.name}
    if isinstance(value, dict):
        return {"$dict": [[encode(k, lossy, path), encode(v, lossy, f"{path}.{k}")]
                          for k, v in value.items()]}
    if isinstance(value, (list, tuple)):
        return {"$list" if isinstance(value, list) else "$tuple":
                [encode(v, lossy, f"{path}[{i}]") for i, v in enumerate(value)]}
    if lossy is not None:
        lossy.append(f"{path or '<valeur>'} : {type(value).__name__}")
    return {"$opaque": repr(value), "$type": type(value).__name__}


def decode(value: Any) -> Any:
    """Réciproque de `encode`. Une valeur opaque revient en `_Opaque`."""
    if isinstance(value, dict):
        if "$sym" in value:
            return Symbol(value["$sym"])
        if "$float" in value:
            return float(value["$float"])
        if "$dict" in value:
            return {decode(k): decode(v) for k, v in value["$dict"]}
        if "$list" in value:
            return [decode(v) for v in value["$list"]]
        if "$tuple" in value:
            return tuple(decode(v) for v in value["$tuple"])
        if "$opaque" in value:
            return _Opaque(value["$opaque"], value.get("$type", "?"))
        return {k: decode(v) for k, v in value.items()}
    return value


class _Opaque:
    """Valeur qui n'a pas su franchir la sérialisation.

    Elle rend son `repr` d'origine — de sorte qu'une trace qui l'affiche reste
    identique — mais toute autre opération est un mensonge qu'on refuse de
    faire : mieux vaut une exception lisible qu'un rejeu qui invente.
    """

    __slots__ = ("_repr", "_type")

    def __init__(self, text: str, type_name: str) -> None:
        self._repr, self._type = text, type_name

    def __repr__(self) -> str:
        return self._repr

    def __str__(self) -> str:
        return self._repr

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, _Opaque) and other._repr == self._repr

    def __hash__(self) -> int:
        return hash(self._repr)


def _rebuild_error(name: str, message: str) -> BaseException:
    """Reconstitue une exception de même **nom** que celle enregistrée.

    Le runtime journalise `f"{type(exc).__name__}: {exc}"` : sans le nom
    d'origine, la trace rejouée diffère d'un caractère — donc diffère.
    """
    builtin = getattr(__builtins__, name, None) if not isinstance(__builtins__, dict) \
        else __builtins__.get(name)
    if isinstance(builtin, type) and issubclass(builtin, BaseException):
        return builtin(message)
    return type(name, (ReplayedError,), {})(message)


class ReplayedError(Exception):
    """Exception rejouée dont la classe d'origine n'est pas reconstructible."""


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------
@dataclass
class Entry:
    seq: int
    kind: str
    key: str
    args: Any = None
    value: Any = None
    error: Optional[List[str]] = None       # [nom de classe, message]
    #: Condensat chaîné, relu depuis le fichier. Jamais réutilisé pour vérifier
    #: — il est *ce qui est vérifié*, on le recalcule toujours.
    hash: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"seq": self.seq, "kind": self.kind, "key": self.key}
        if self.args is not None:
            out["args"] = self.args
        if self.error is not None:
            out["error"] = self.error
        else:
            out["value"] = self.value
        return out

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "Entry":
        return cls(seq=raw["seq"], kind=raw["kind"], key=raw["key"],
                   args=raw.get("args"), value=raw.get("value"),
                   error=raw.get("error"), hash=raw.get("h"))


@dataclass
class Journal:
    """Suite ordonnée des franchissements de frontière d'une exécution."""

    entries: List[Entry] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    lossy: List[str] = field(default_factory=list)
    #: Verdict d'intégrité établi au chargement. `None` pour un journal
    #: construit en mémoire : rien n'a encore été relu, donc rien n'est établi.
    chain_report: Optional[ChainReport] = None
    #: Clé de scellement, résolue **avant** l'exécution pour qu'une clé
    #: inutilisable arrête l'agent avant qu'il n'agisse (cf. `_new_journal`).
    #: `None` = journal chaîné mais non signé.
    signing_key: Optional[Any] = None
    _cursor: int = 0

    # ------------------------------------------------------------ écriture
    def record(self, kind: str, key: str, *, args: Any = None,
               value: Any = None, error: Optional[BaseException] = None) -> None:
        if kind not in KINDS:
            raise ReplayError(f"genre d'entrée inconnu : {kind}")
        entry = Entry(seq=len(self.entries), kind=kind, key=key,
                      args=encode(args, self.lossy, f"{kind}:{key}(args)"))
        if error is not None:
            entry.error = [type(error).__name__, str(error)]
        else:
            entry.value = encode(value, self.lossy, f"{kind}:{key}")
        self.entries.append(entry)

    # ------------------------------------------------------------ lecture
    def next(self, kind: str, key: str) -> Entry:
        """Consomme l'entrée suivante, en exigeant qu'elle soit *celle-là*."""
        if self._cursor >= len(self.entries):
            raise ReplayDivergence(
                f"journal épuisé : l'exécution rejouée demande {kind}:{key} "
                f"après {len(self.entries)} franchissements enregistrés")
        entry = self.entries[self._cursor]
        if entry.kind != kind or entry.key != key:
            raise ReplayDivergence(
                f"divergence au franchissement #{self._cursor} : "
                f"enregistré {entry.kind}:{entry.key}, "
                f"rejoué {kind}:{key}")
        self._cursor += 1
        return entry

    def replay_value(self, kind: str, key: str, args: Any = _NO_ARGS) -> Any:
        entry = self.next(kind, key)
        # Même outil, arguments différents = décision différente. Ne pas le
        # voir serait le seul moyen pour un rejeu de « réussir » à tort.
        if args is not _NO_ARGS and encode(args) != entry.args:
            raise ReplayDivergence(
                f"divergence au franchissement #{entry.seq} : {kind}:{key} "
                f"appelé avec {args!r}, enregistré avec {decode(entry.args)!r}")
        if entry.error is not None:
            raise _rebuild_error(entry.error[0], entry.error[1])
        return decode(entry.value)

    def rewind(self) -> "Journal":
        self._cursor = 0
        return self

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self.entries)

    @property
    def remaining(self) -> int:
        return len(self.entries) - self._cursor

    # --------------------------------------------------------- persistance
    def entry_bodies(self) -> List[Dict[str, Any]]:
        """Entrées sérialisées **sans** leur condensat — la matière à hacher."""
        return [e.to_json() for e in self.entries]

    @property
    def head(self) -> str:
        """Tête de chaîne des entrées présentes, recalculée à la demande."""
        return chain_head(self.entry_bodies())

    def to_json(self) -> Dict[str, Any]:
        bodies = self.entry_bodies()
        for body, digest in zip(bodies, chain_hashes(bodies)):
            body["h"] = digest
        meta = dict(self.meta)
        meta["format"] = JOURNAL_FORMAT
        if self.lossy:
            meta["lossy"] = sorted(set(self.lossy))
        # `setdefault` et non affectation : si le journal a été scellé puis
        # modifié, la tête scellée doit rester celle du sceau, pour que la
        # divergence se voie au lieu d'être réécrite en silence.
        meta.setdefault("chain_sha256", chain_head(bodies))
        return {"meta": meta, "entries": bodies}

    def dumps(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, indent=2,
                          sort_keys=False) + "\n"

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(self.dumps(), encoding="utf-8")
        return p

    @classmethod
    def loads(cls, text: str, *, strict: bool = True) -> "Journal":
        """Relit un journal, en vérifiant sa chaîne de hachage.

        `strict` (défaut) refuse un journal altéré : le rejouer produirait une
        démonstration à partir d'une pièce corrompue, ce qui est pire que pas
        de démonstration du tout. `strict=False` charge quand même — pour
        *examiner* le dommage — et laisse le verdict dans `chain_report`.
        """
        raw = json.loads(text)
        meta = raw.get("meta", {})
        fmt = meta.get("format")
        if fmt not in READABLE_FORMATS:
            raise ReplayError(
                f"format de journal {fmt!r} illisible par cette version "
                f"(attendus {', '.join(map(str, READABLE_FORMATS))})")

        j = cls(entries=[Entry.from_json(e) for e in raw.get("entries", [])],
                meta=meta)
        j.lossy = list(meta.get("lossy", []))
        if fmt == 1:
            j.chain_report = ChainReport(
                False, "journal au format 1 : antérieur au scellement, son "
                       "intégrité ne peut pas être établie")
        else:
            j.chain_report = verify_chain(raw)
            if strict and not j.chain_report.ok:
                raise ReplayError(f"journal altéré — {j.chain_report.render()}")
        return j

    @classmethod
    def load(cls, path: str | Path, *, strict: bool = True) -> "Journal":
        return cls.loads(Path(path).read_text(encoding="utf-8"), strict=strict)

    # ------------------------------------------------------------- empreinte
    def seal(self, trace_text: str, *, key: Optional[SigningKey] = None,
             **extra: Any) -> "Journal":
        """Scelle le journal : empreinte de trace, tête de chaîne, signature.

        L'empreinte de trace est ce que le rejeu confrontera — sans elle,
        « identique » n'est qu'une affirmation. La tête de chaîne est ce qu'un
        tiers confrontera au fichier — sans elle, « non modifié » n'est qu'une
        affirmation. La signature, si une clé est fournie, est ce qui rattache
        les deux à un émetteur.
        """
        self.meta["trace_sha256"] = sha256(trace_text)
        self.meta.update(extra)
        # La tête est figée ici : toute entrée ajoutée après coup fera diverger
        # le fichier de son sceau, et c'est exactement ce qu'on veut voir.
        self.meta.pop("chain_sha256", None)
        self.meta["chain_sha256"] = self.head
        self.meta["format"] = JOURNAL_FORMAT
        if self.lossy:
            self.meta["lossy"] = sorted(set(self.lossy))
        self.meta.pop("signature", None)
        if key is not None:
            self.meta["signature"] = sign(self.meta, key)
        return self

    def verify_seal(self, key: Optional[SigningKey] = None) -> SignatureReport:
        """Le sceau du journal tient-il face à `key` ?"""
        return verify_signature(self.meta, key)

    @property
    def signed(self) -> bool:
        return bool(self.meta.get("signature"))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_trace(journal: Journal, trace_text: str) -> bool:
    """La trace rejouée est-elle celle qui fut enregistrée ?"""
    expected = journal.meta.get("trace_sha256")
    if not expected:
        raise ReplayError("journal non scellé : aucune empreinte de trace")
    return expected == sha256(trace_text)


# --------------------------------------------------------------------------
# Enregistrement
# --------------------------------------------------------------------------
class _RecordingSubagents:
    """Vue enregistrante sur `host.subagents`.

    L'absence d'un sous-agent est un fait d'exécution comme un autre : elle
    produit un `ERROR` dans la trace, donc elle se journalise.
    """

    def __init__(self, inner: Dict[str, Callable], journal: Journal) -> None:
        self._inner, self._journal = inner, journal

    def get(self, name: str, default: Any = None) -> Any:
        fn = self._inner.get(name)
        self._journal.record("delegate_lookup", name, value=fn is not None)
        if fn is None:
            return default

        def wrapped(payload: Dict[str, Any]) -> Any:
            try:
                result = fn(payload)
            except BaseException as exc:                  # noqa: BLE001
                self._journal.record("delegate", name, args=payload, error=exc)
                raise
            self._journal.record("delegate", name, args=payload, value=result)
            return result

        return wrapped

    def __contains__(self, name: str) -> bool:
        return name in self._inner

    def __getitem__(self, name: str) -> Any:
        fn = self.get(name)
        if fn is None:
            raise KeyError(name)
        return fn


class RecordingHost:
    """Hôte transparent qui journalise ce qui franchit sa frontière.

    Il **délègue** au lieu d'hériter : un hôte réel est une instance déjà
    construite, souvent enrichie d'attributs propres au domaine. Envelopper
    laisse ces attributs accessibles ; sous-classer aurait obligé à les
    recopier.
    """

    def __init__(self, inner: Any, journal: Journal) -> None:
        self._inner = inner
        self._journal = journal

    # -- frontière journalisée ------------------------------------------
    def read(self, path: str) -> Any:
        return self._call("read", path, lambda: self._inner.read(path))

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        return self._call("invoke", name, lambda: self._inner.invoke(name, args),
                          args=args)

    def ask(self, question: str, reason: str = "") -> Any:
        return self._call("ask", question,
                          lambda: self._inner.ask(question, reason),
                          args={"reason": reason})

    def approve(self, request: Any) -> bool:
        key = request.render() if hasattr(request, "render") else str(request)
        return self._call("approve", key, lambda: self._inner.approve(request))

    def drain(self) -> List[Dict[str, Any]]:
        return self._call("drain", "", lambda: self._inner.drain())

    @property
    def subagents(self) -> Any:
        return _RecordingSubagents(self._inner.subagents, self._journal)

    def _call(self, kind: str, key: str, fn: Callable[[], Any],
              args: Any = None) -> Any:
        try:
            value = fn()
        except BaseException as exc:                      # noqa: BLE001
            self._journal.record(kind, key, args=args, error=exc)
            raise
        self._journal.record(kind, key, args=args, value=value)
        return value

    # -- tout le reste appartient à l'hôte enveloppé ---------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class RecordingLLM:
    """Même principe, côté oracle : ce que le modèle a répondu ce jour-là."""

    def __init__(self, inner: Any, journal: Journal) -> None:
        self._inner, self._journal = inner, journal

    def reason(self, task: str, context: Dict[str, Any],
               produce: Dict[str, str]) -> Dict[str, Any]:
        try:
            value = self._inner.reason(task, context, produce)
        except BaseException as exc:                      # noqa: BLE001
            self._journal.record("reason", task, error=exc)
            raise
        self._journal.record("reason", task, value=value)
        return value

    def select_plan(self, context: Dict[str, Any],
                    candidates: List[str]) -> Optional[str]:
        key = "|".join(candidates)
        try:
            value = self._inner.select_plan(context, candidates)
        except BaseException as exc:                      # noqa: BLE001
            self._journal.record("select_plan", key, error=exc)
            raise
        self._journal.record("select_plan", key, value=value)
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# --------------------------------------------------------------------------
# Rejeu
# --------------------------------------------------------------------------
class _ReplaySubagents:
    def __init__(self, journal: Journal) -> None:
        self._journal = journal

    def get(self, name: str, default: Any = None) -> Any:
        registered = self._journal.replay_value("delegate_lookup", name)
        if not registered:
            return default
        return lambda payload: self._journal.replay_value("delegate", name,
                                                          args=payload)

    def __contains__(self, name: str) -> bool:      # pragma: no cover - inusité
        return any(e.kind == "delegate_lookup" and e.key == name and e.value
                   for e in self._journal.entries)


class ReplayHost:
    """Hôte qui ne touche à rien : il relit le journal, dans l'ordre.

    Aucun capteur, aucun outil, aucun réseau. C'est précisément l'intérêt :
    un auditeur rejoue la décision sans accès au système d'origine.
    """

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def read(self, path: str) -> Any:
        return self.journal.replay_value("read", path)

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        return self.journal.replay_value("invoke", name, args=args)

    def ask(self, question: str, reason: str = "") -> Any:
        return self.journal.replay_value("ask", question, args={"reason": reason})

    def approve(self, request: Any) -> bool:
        key = request.render() if hasattr(request, "render") else str(request)
        return bool(self.journal.replay_value("approve", key))

    def drain(self) -> List[Dict[str, Any]]:
        return list(self.journal.replay_value("drain", "") or [])

    def emit(self, source: str, **payload: Any) -> None:
        """Un rejeu n'accepte pas d'événement neuf : il rejouerait autre chose."""
        raise ReplayError("émission d'événement interdite pendant un rejeu")

    @property
    def subagents(self) -> Any:
        return _ReplaySubagents(self.journal)


class ReplayLLM:
    """Oracle rejoué : les réponses du modèle telles qu'elles furent."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def reason(self, task: str, context: Dict[str, Any],
               produce: Dict[str, str]) -> Dict[str, Any]:
        return self.journal.replay_value("reason", task)

    def select_plan(self, context: Dict[str, Any],
                    candidates: List[str]) -> Optional[str]:
        return self.journal.replay_value("select_plan", "|".join(candidates))

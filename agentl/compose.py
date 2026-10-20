"""Composition : un agent AGENT-L en délègue un autre, sous droits bornés (v1.16).

`DELEGATE` (v0.3) appelait une fonction Python : un oracle opaque, qu'aucune
analyse ne voyait et dont l'hôte pouvait faire n'importe quoi. Ici le
délégué **est** un agent — un fichier `.agent`, lu, vérifié, et exécuté par un
`Runtime` imbriqué :

    DELEGATE forensic {
        AGENT     "forensic.agent"
        TASK      "analyser l'e-mail signalé"
        INPUT     { mail.reported }
        EXPECT    { malicious, threat_class }
        GRANT     { read_mail, tag_mail }
        MAX_TICKS 6
    }

Ce que la délégation ne fait pas, et c'est le point :

* **Monotonie des droits.** L'enfant n'atteint que les outils de `GRANT`, qui
  doivent être des outils du **parent** (`E018`). Chaque action de l'enfant
  passe par sa propre politique **puis** par celle du parent — jugée sur l'état
  du parent, avec le risque que le **parent** déclare (`Kernel.ceiling`). Le
  plus restrictif gagne : l'enfant ne relâche rien.
* **Hôte restreint, non hérité.** Un outil hors `GRANT` n'existe pas pour
  l'enfant (`GrantedHost`), il n'est pas seulement interdit.
* **Un seul chemin.** Les permis de l'enfant sont ceux d'un noyau comme les
  autres, et son journal est celui du parent : la reprise durable les rejuge
  (§44) comme n'importe quelle action.
* **Bornée.** `MAX_TICKS` borne l'exécution ; `MAX_DEPTH` (1 par défaut : pas de
  redélégation) borne la profondeur ; un cycle de fichiers est une erreur
  statique (`E020`). Séquentiel : une délégation s'exécute en entier avant la
  suite du parent, sans concurrence à raisonner (§37).
* **Le retour n'est pas fiable.** Il porte l'origine `DELEGATE`, comme celui
  d'une fonction : les champs de `EXPECT` ne masquent rien.

Le fichier délégué est cherché **sous** le répertoire du fichier parent : un
chemin absolu ou qui en sort est refusé. Son contenu (empreinte SHA-256) entre
dans l'identité du programme durable (`delegation_digest`) : le modifier entre
un crash et la reprise est une reprise refusée.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .core import UNDEFINED, AgentLError
from .kernel import provenance as P
from .nodes import Agent, DelegateStmt


class ComposeError(AgentLError):
    """Délégation impossible : fichier absent, hors périmètre, cyclique…"""


_CACHE: Dict[Tuple[str, str], Agent] = {}
_DECLARED = P.Prov({P.DECLARED})
_RUNTIME = P.Prov({P.RUNTIME})


# ---------------------------------------------------------------- fichiers
def resolve(stmt: DelegateStmt) -> Path:
    """Chemin du fichier délégué — sous le répertoire du parent, ou rien."""
    name = stmt.agent_file or ""
    root = Path(stmt.base_dir or Path.cwd()).resolve()
    if not name or Path(name).is_absolute():
        raise ComposeError(f"DELEGATE {stmt.agent} : AGENT doit être un chemin "
                           f"relatif au fichier du parent, pas {name!r}")
    path = (root / name).resolve()
    if root != path and root not in path.parents:
        raise ComposeError(f"DELEGATE {stmt.agent} : {name!r} sort du "
                           f"répertoire du parent ({root})")
    return path


def load(stmt: DelegateStmt) -> Tuple[Agent, str]:
    """`(agent, empreinte SHA-256 du fichier)` — l'agent est analysé une fois."""
    from .parser import parse_source
    path = resolve(stmt)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ComposeError(f"DELEGATE {stmt.agent} : {path} illisible "
                           f"({exc.strerror or exc})") from exc
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    key = (str(path), sha)
    if key not in _CACHE:
        try:
            program = parse_source(source, str(path))
        except AgentLError as exc:
            raise ComposeError(f"DELEGATE {stmt.agent} : {path.name} ne "
                               f"s'analyse pas — {exc}") from exc
        if len(program.agents) != 1:
            raise ComposeError(
                f"DELEGATE {stmt.agent} : {path.name} doit définir exactement "
                f"un agent, pas {len(program.agents)}")
        _CACHE[key] = program.agents[0]
    return _CACHE[key], sha


def delegations(agent: Agent) -> List[DelegateStmt]:
    """Les `DELEGATE … AGENT` d'un agent, où qu'ils soient."""
    from .analyzer import Analyzer
    return [stmt for stmt, _ in Analyzer(agent)._all_statements()
            if isinstance(stmt, DelegateStmt) and stmt.agent_file]


def closure(agent: Agent, _chain: Tuple[str, ...] = ()) -> List[Tuple[DelegateStmt, Agent, str]]:
    """Tout ce que `agent` délègue, récursivement — ou `ComposeError`.

    Un cycle (A délègue B qui délègue A) se détecte sur le **chemin** des
    fichiers, pas sur le nom des agents : deux fichiers peuvent définir deux
    agents homonymes sans cycle.
    """
    found: List[Tuple[DelegateStmt, Agent, str]] = []
    for stmt in delegations(agent):
        child, sha = load(stmt)
        here = str(resolve(stmt))
        if here in _chain:
            raise ComposeError(
                f"DELEGATE {stmt.agent} : cycle de délégation "
                f"({' → '.join(Path(p).name for p in (*_chain, here))})")
        found.append((stmt, child, sha))
        found.extend(closure(child, (*_chain, here)))
    return found


def delegation_digest(agents: Iterable[Agent]) -> str:
    """Identité des délégations d'un programme, `""` s'il n'en a pas.

    Entre dans `program_digest` : le `repr` d'un `DelegateStmt` n'en porte pas
    les champs `AGENT`/`GRANT`, pour ne pas changer l'empreinte des programmes
    qui n'en ont pas. Le contenu du fichier délégué en fait partie.
    """
    h = hashlib.sha256(b"agentl.compose.v1\n")
    any_ = False
    for agent in agents:
        try:
            chain = closure(agent)
        except ComposeError as exc:
            chain = []
            h.update(f"!{exc}\n".encode("utf-8"))
            any_ = True
        for stmt, _, sha in chain:
            any_ = True
            h.update(f"{agent.name}|{stmt.agent}|{stmt.agent_file}|"
                     f"{','.join(stmt.grant)}|{stmt.max_ticks}|"
                     f"{stmt.max_depth}|{sha}\n".encode("utf-8"))
    return h.hexdigest() if any_ else ""


# ------------------------------------------------------------- hôte restreint
class GrantedHost:
    """La vue de l'hôte du parent que `GRANT` accorde à l'enfant.

    Lecture, question et approbation passent au parent — l'humain qui répond
    est le même. L'invocation est limitée aux outils accordés ; les autres
    n'existent pas. L'enfant ne consomme pas les événements du parent
    (`drain`), et n'a aucun sous-agent Python.
    """

    trace_sink = None

    def __init__(self, inner: Any, grant: Iterable[str]) -> None:
        self._real = inner
        # L'hôte réel, sans l'enveloppe durable : `Kernel.recheck` relit les
        # capteurs sur lui, sans les écrire au journal (§44).
        self._inner = getattr(inner, "_inner", inner)
        self.grant = frozenset(grant)
        self.subagents: Dict[str, Any] = {}

    def read(self, path: str) -> Any:
        return self._real.read(path)

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        if name not in self.grant:
            raise KeyError(f"outil `{name}` non accordé (GRANT)")
        return self._real.invoke(name, args)

    def ask(self, question: str, reason: str = "") -> Any:
        return self._real.ask(question, reason)

    def approve(self, request: Any) -> Any:
        return self._real.approve(request)

    def drain(self) -> List[Dict[str, Any]]:
        return []

    def __getattr__(self, name: str) -> Any:
        # idempotent_tools, reconcilers, reconcile… : promesses de l'hôte du
        # parent, nécessaires à la reprise durable.
        return getattr(self._real, name)


# ---------------------------------------------------------------- exécution
def run_delegation(parent: Any, stmt: DelegateStmt) -> Dict[str, Any]:
    """Exécute l'enfant jusqu'à ses bornes, et rend les champs de `EXPECT`."""
    from .runtime import Runtime

    budget = getattr(parent, "_depth_budget", None)
    if budget is not None and budget <= 0:
        raise ComposeError(f"DELEGATE {stmt.agent} : profondeur de délégation "
                           f"épuisée (MAX_DEPTH)")
    allowed = stmt.max_depth if budget is None else min(budget, stmt.max_depth)

    child_agent, _ = load(stmt)
    parent._delegations = getattr(parent, "_delegations", 0) + 1
    parent.metrics["delegations"] = parent.metrics.get("delegations", 0) + 1
    run_id = (f"{parent.kernel.run_id}>{stmt.agent}"
              f"#{parent.state.tick}.{parent._delegations}")
    child = Runtime(child_agent, GrantedHost(parent.host, stmt.grant),
                    parent.llm, run_id=run_id,
                    tool_breaker=parent.tool_breaker,
                    tool_cooldown=parent.tool_cooldown)
    child.kernel.journal = parent.kernel.journal
    child.kernel.ceiling = (parent.kernel, parent.state)
    child._depth_budget = allowed - 1

    for name in stmt.inputs:
        value, label = parent.state.get_labeled(name)
        if value is not UNDEFINED:
            child.state.set_local(name, value, label)
    child.state.set_local("delegate.task", stmt.task, _DECLARED)

    declared = child_agent.loop.max_iter if child_agent.loop else None
    limit = min(stmt.max_ticks, declared or stmt.max_ticks) \
        if stmt.max_ticks else (declared or 6)
    try:
        child.run(max_ticks=limit)
    finally:
        # La trace de l'enfant entre dans celle du parent, au tick du parent :
        # un rejeu compare une seule trace, et un lecteur voit tout au même
        # endroit.
        for event in child.trace.events:
            text = (f"tick {event.tick}" if event.kind == "TICK"
                    else event.text)
            parent.trace.log(parent.state.tick,
                             "INFO" if event.kind == "TICK" else event.kind,
                             f"[{stmt.agent}] {text}", event.detail)

    result: Dict[str, Any] = {}
    for key in stmt.expect:
        value = child.state.get(key)
        if value is not UNDEFINED:
            result[key] = value
    return result


def verify_all(agent: Agent, depth: Optional[int] = None) -> List[Tuple[str, Any]]:
    """`verify` de l'agent **et** de tous ses délégués, chacun une fois.

    Le plafond du parent ne fait que restreindre l'enfant : un théorème que
    l'enfant tient seul (T1 : aucun appel interdit n'aboutit) tient a fortiori
    sous le parent. Chaque fichier se vérifie donc pour lui-même.
    """
    from .verifier import DEFAULT_DEPTH, verify
    depth = DEFAULT_DEPTH if depth is None else depth
    reports = [(agent.name, verify(agent, depth=depth))]
    seen = set()
    for stmt, child, _ in closure(agent):
        key = str(resolve(stmt))
        if key not in seen:
            seen.add(key)
            reports.append((f"{stmt.agent} ({stmt.agent_file})",
                            verify(child, depth=depth)))
    return reports


__all__ = ["ComposeError", "GrantedHost", "closure", "delegation_digest",
           "delegations", "load", "resolve", "run_delegation", "verify_all"]

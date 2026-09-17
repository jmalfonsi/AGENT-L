"""Vivacité d'une société d'agents — théorème T8.

Les théorèmes T1–T7 se démontrent **un agent à la fois**. C'était tolérable
tant que `DELEGATE` était la seule composition : un appel synchrone qui rend
une réponse ne change pas l'atteignabilité de l'appelant. La v0.6 a introduit
`MESSAGE` (asynchrone) et `SHARED` (état partagé), et avec eux une dépendance
que l'analyse par agent ne voit pas :

    A n'agit que sur réception de `m`, que seul B émet ;
    B n'émet `m` que sur réception de `n`, que seul A émet.

Chacun des deux agents est irréprochable pris isolément. T2 conclut, pour
chacun, que son but reste atteignable. Et le programme est bloqué. La promesse
« le but reste atteignable » était donc **fausse à l'échelle du programme** —
non par un défaut d'implémentation, mais parce que rien ne l'y vérifiait.

Ce module comble ce trou, et rien de plus.

## L'abstraction

Le pi-calcul et les réseaux de Petri sauraient traiter la question en général.
Le langage n'en a pas besoin : ses moyens de blocage sont peu nombreux et
nommés. On raisonne donc sur un objet plus petit, un **graphe d'attente** dont
les nœuds sont des *signaux* :

    msg:<nom>        un message qu'un agent peut émettre et un autre attendre
    shared:<clé>     une clé de la mémoire partagée

et dont les producteurs sont des *contextes* — un plan, un gestionnaire de
message, une écriture en mémoire — chacun gardé par les signaux dont il dépend.

La question « le programme peut-il se bloquer ? » devient alors : **quels
signaux sont productibles ?** C'est un plus petit point fixe, calculé par
saturation depuis les contextes déclenchables sans aucun message :

    productible(s)  ⟺  ∃ un contexte qui émet s et dont toutes les gardes
                       sont elles-mêmes productibles

Un signal hors du point fixe n'est **jamais** émis : ni au premier tick, ni au
millième. S'il porte un gestionnaire, ce gestionnaire est mort, et tout ce qui
en dépend l'est aussi. Un cycle d'attente est le cas particulier où plusieurs
signaux se gardent mutuellement sans point d'entrée extérieur.

## Direction de sûreté

La même que celle du solveur, et pour la même raison :

    T8 peut manquer un blocage,
    il n'en déclare jamais un à tort.

Concrètement : un signal est réputé productible dès qu'il **existe une forme
de chemin** vers lui, sans qu'on cherche à savoir si les gardes numériques de
ce chemin peuvent être vraies. Un agent dont les gardes sont contradictoires se
bloquera sans que T8 l'ait vu — faux négatif, acceptable. En revanche, un
signal déclaré non productible l'est parce qu'aucun chemin n'existe *de forme*,
ce qui suffit à conclure.

## Ce que T8 ne prouve pas

**L'échappatoire du LLM.** `DECIDE { REASON … }` autorise le modèle à proposer
n'importe quel plan déclaré (le runtime rejette les noms inventés, pas les noms
existants). Chez un tel agent, tout plan est donc atteignable, et aucun
blocage n'est démontrable. T8 le dit — « ◐ non prouvé » — plutôt que de rendre
un verdict rassurant sur une hypothèse fausse. Retirer l'échappatoire, ou la
garder en connaissance de cause, redevient un choix explicite de l'auteur.

**La famine partielle.** T8 raisonne sur « émis au moins une fois », pas sur
« émis aussi souvent qu'il le faut ». Un agent servi trop rarement ne sera pas
signalé.

**La granularité des clés partagées.** Une clé est un signal indivisible : T8
sait qu'elle a été écrite, pas *ce* qui y a été écrit. Deux agents qui se
coordonnent sur la valeur d'un enregistrement plutôt que sur son existence
sortent de la portée du théorème.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .nodes import (Agent, IfStmt, MessageStmt, Node, Plan, Program, Stmt,
                    ThenPlan)

#: Préfixes des deux espèces de signaux. Les préfixer les rend affichables sans
#: ambiguïté (`msg:check_host` ≠ `shared:check_host`) et permet de les mêler
#: dans un même point fixe — ce qui est nécessaire : un plan peut attendre un
#: message *et* une clé partagée.
MSG = "msg:"
SHARED = "shared:"


def signal_label(signal: str) -> str:
    if signal.startswith(MSG):
        return f"MESSAGE {signal[len(MSG):]}"
    if signal.startswith(SHARED):
        return f"SHARED.{signal[len(SHARED):]}"
    return signal


# --------------------------------------------------------------------------
# Contextes
# --------------------------------------------------------------------------
@dataclass
class Context:
    """Un lieu du programme qui peut émettre des signaux, et ce qui le garde.

    `gates` est une **disjonction de conjonctions** : chaque entrée est un
    ensemble de signaux qui, ensemble, suffisent à atteindre ce contexte. Un
    ensemble vide signifie « atteignable sans aucun message » — c'est le point
    d'entrée qui empêche un cycle d'être un blocage.
    """

    agent: str
    kind: str                      # plan | handler | memory | event | decide
    name: str
    gates: List[Set[str]] = field(default_factory=list)
    emits: Set[str] = field(default_factory=set)
    line: int = 0
    #: Chemins écrits par ce contexte — sert à distinguer un cycle qui
    #: progresse d'un cycle qui tourne à vide.
    writes: Set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return f"{self.agent}.{self.name}"

    def autonomous(self) -> bool:
        return any(not gate for gate in self.gates)

    def live(self, produced: Set[str]) -> bool:
        return any(gate <= produced for gate in self.gates)


@dataclass
class Finding:
    code: str
    severity: str                  # error | warning | info
    message: str
    hint: str = ""
    line: int = 0

    def render(self) -> str:
        mark = {"error": "✗", "warning": "!", "info": "·"}[self.severity]
        where = f"l.{self.line}" if self.line else "—"
        out = f"    {mark} {self.code}  {where:>6}  {self.message}"
        if self.hint:
            out += f"\n                      → {self.hint}"
        return out


@dataclass
class LivenessReport:
    """Verdict de T8 sur un programme entier."""

    holds: Optional[bool] = None   # None = non prouvé
    summary: str = ""
    findings: List[Finding] = field(default_factory=list)
    #: Signaux dont on a démontré qu'ils ne sont jamais émis.
    dead_signals: Set[str] = field(default_factory=set)
    #: Contextes dont on a démontré qu'ils ne s'exécutent jamais.
    dead_contexts: List[Context] = field(default_factory=list)

    @property
    def refuted(self) -> bool:
        return self.holds is False

    def render(self) -> str:
        badge = {True: "✔ DÉMONTRÉ", False: "✘ RÉFUTÉ",
                 None: "◐ NON PROUVÉ"}[self.holds]
        lines = ["\nT8 — Aucun agent n'attend un signal que nul ne produira",
                 f"  {badge} · {self.summary}"]
        lines += [f.render() for f in self.findings]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def _walk(stmts: Sequence[Stmt]):
    """Parcourt les instructions, gardes et boucles comprises.

    On réutilise le parcours du vérificateur : un `MESSAGE` sous un `IF` reste
    une émission possible, et supposer une branche morte serait se tromper dans
    le mauvais sens.
    """
    from .verifier import _walk_guarded

    for stmt, _, _ in _walk_guarded(list(stmts)):
        yield stmt


def _paths(node: Optional[Node]) -> Set[str]:
    from .verifier import _referenced_paths
    return _referenced_paths(node)


def _shared_gates(node: Optional[Node]) -> Set[str]:
    """Signaux `shared:` lus par une garde.

    Une garde qui lit `SHARED.blocked_hosts` attend que quelqu'un l'écrive :
    c'est une dépendance inter-agents au même titre qu'un message, et la
    manquer ferait conclure « pas de blocage » sur la moitié du problème.
    """
    out: Set[str] = set()
    for path in _paths(node):
        parts = path.split(".")
        if parts[0] == "SHARED" and len(parts) > 1:
            out.add(SHARED + parts[1])
    return out


def _emitted(stmts: Sequence[Stmt]) -> Set[str]:
    return {MSG + s.name for s in _walk(stmts) if isinstance(s, MessageStmt)}


def _written(stmts: Sequence[Stmt]) -> Set[str]:
    from .nodes import SetStmt
    return {s.target for s in _walk(stmts) if isinstance(s, SetStmt)}


def _targets(stmts: Sequence[Stmt]) -> Set[str]:
    return {s.plan for s in _walk(stmts) if isinstance(s, ThenPlan)}


def _plan_body(plan: Plan) -> List[Stmt]:
    return [stmt for step in plan.steps for stmt in step.body]


class _Extractor:
    """Traduit un programme en contextes gardés."""

    def __init__(self, program: Program) -> None:
        self.program = program
        self.contexts: List[Context] = []
        #: Agents dont l'échappatoire LLM rend tout plan atteignable.
        self.llm_open: Set[str] = set()

    def run(self) -> List[Context]:
        for agent in self.program.agents:
            self._agent(agent)
        return self.contexts

    # ------------------------------------------------------------ un agent
    def _agent(self, agent: Agent) -> None:
        plans = {p.name: p for p in agent.plans}
        # Comment chaque plan est atteint : liste de conjonctions de signaux.
        gates: Dict[str, List[Set[str]]] = {name: [] for name in plans}

        # 1. Garde de plan — le monde suffit à la déclencher, sauf si elle lit
        #    la mémoire partagée.
        for plan in agent.plans:
            if plan.when is not None:
                gates[plan.name].append(_shared_gates(plan.when))

        # 2. Règles DECIDE.
        if agent.decide is not None:
            for rule in agent.decide.rules:
                gate = _shared_gates(rule.cond)
                for target in _targets(rule.then):
                    if target in gates:
                        gates[target].append(set(gate))

            # 3. Échappatoire LLM : `REASON` autorise le modèle à proposer
            #    n'importe quel plan déclaré. On ne peut donc rien réfuter
            #    chez cet agent — et on le dit plutôt que de conclure.
            if agent.decide.reason is not None:
                self.llm_open.add(agent.name)
                for name in gates:
                    gates[name].append(set())

        # 4. Événements de l'hôte — le monde extérieur est un point d'entrée.
        for handler in agent.events:
            gate = _shared_gates(handler.when)
            for target in _targets(handler.body):
                if target in gates:
                    gates[target].append(set(gate))

        # 5. Gestionnaires de message : leur propre contexte, gardé par le
        #    message reçu.
        for handler in agent.messages:
            gate = {MSG + handler.name} | _shared_gates(handler.when)
            self.contexts.append(Context(
                agent=agent.name, kind="handler",
                name=f"ON MESSAGE {handler.name}", gates=[set(gate)],
                emits=_emitted(handler.body), writes=_written(handler.body),
                line=handler.line))
            for target in _targets(handler.body):
                if target in gates:
                    gates[target].append(set(gate))

        # 6. Repli de vérification — atteignable dès que l'agent agit.
        for target in _targets(agent.on_verify_fail):
            if target in gates:
                gates[target].append(set())

        # 7. Propagation `THEN p` de plan à plan, jusqu'au point fixe : un plan
        #    appelé par un plan atteignable est atteignable.
        changed = True
        while changed:
            changed = False
            for plan in agent.plans:
                for target in _targets(_plan_body(plan)):
                    if target not in gates:
                        continue
                    for gate in gates[plan.name]:
                        if gate not in gates[target]:
                            gates[target].append(set(gate))
                            changed = True

        for plan in agent.plans:
            body = _plan_body(plan)
            self.contexts.append(Context(
                agent=agent.name, kind="plan", name=plan.name,
                gates=gates[plan.name] or [], emits=_emitted(body),
                writes=_written(body), line=plan.line))

        # 8. Écritures en mémoire partagée : hors plan, en phase UPDATE_MEMORY,
        #    donc gardées par leur seul `WHEN`.
        for write in agent.memory.writes:
            if write.into != "SHARED":
                continue
            self.contexts.append(Context(
                agent=agent.name, kind="memory",
                name=f"INTO SHARED.{write.key}",
                gates=[_shared_gates(write.when)],
                emits={SHARED + write.key}, line=write.line))


# --------------------------------------------------------------------------
# Point fixe
# --------------------------------------------------------------------------
def producible(contexts: Sequence[Context]) -> Set[str]:
    """Plus petit point fixe des signaux effectivement émissibles.

    Saturation depuis les contextes déclenchables sans message. Le calcul
    termine : l'ensemble des signaux est fini et ne fait que croître.
    """
    produced: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for context in contexts:
            if not context.live(produced):
                continue
            fresh = context.emits - produced
            if fresh:
                produced |= fresh
                changed = True
    return produced


def _blocking_cycle(signal: str, contexts: Sequence[Context],
                    produced: Set[str]) -> List[str]:
    """Chaîne d'attente menant de `signal` à lui-même, si elle existe.

    C'est ce qui distingue un message que *personne* n'émet (une étourderie,
    déjà signalée par `W112`) d'un **interblocage** : là, tout le monde émet,
    mais chacun attend l'autre pour le faire.
    """
    # Successeurs : `s` attend `t` si tout contexte émettant `s` est gardé par
    # `t`. On ne suit que les gardes non satisfaites, seules bloquantes.
    def waits_on(target: str) -> Set[str]:
        out: Set[str] = set()
        for context in contexts:
            if target not in context.emits:
                continue
            for gate in context.gates:
                out |= (gate - produced)
        return out

    stack: List[Tuple[str, List[str]]] = [(signal, [signal])]
    seen: Set[str] = set()
    while stack:
        current, path = stack.pop()
        for nxt in sorted(waits_on(current)):
            if nxt == signal:
                return path + [signal]
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.append((nxt, path + [nxt]))
    return []


# --------------------------------------------------------------------------
# Livelock
# --------------------------------------------------------------------------
def _goal_paths(program: Program) -> Set[str]:
    out: Set[str] = set()
    for agent in program.agents:
        for goal in agent.goals:
            out |= _paths(goal.condition)
            for target in goal.targets:
                out |= _paths(target)
    return out


def _livelock_findings(contexts: Sequence[Context], produced: Set[str],
                       program: Program) -> List[Finding]:
    """Cycles de messages vivants qui ne font rien avancer.

    Un cycle qui tourne n'est pas un défaut : c'est même la forme normale d'une
    conversation. Il ne devient un livelock que si **rien de ce qu'il écrit
    n'est lu** — ni par un but, ni par une garde du cycle. On ne signale donc
    que ce cas-là, et en avertissement : l'agent pourrait avoir des effets par
    ses outils, que cette analyse ne suit pas.
    """
    findings: List[Finding] = []
    goal_paths = _goal_paths(program)

    # Cycle vivant : des gestionnaires qui s'émettent mutuellement.
    handlers = [c for c in contexts if c.kind == "handler" and c.live(produced)]
    for context in handlers:
        awaited = {s for gate in context.gates for s in gate if s.startswith(MSG)}
        # Le cycle le plus court et le plus fréquent : A répond à m par m'…
        for other in handlers:
            if other is context:
                continue
            other_awaited = {s for gate in other.gates for s in gate}
            if not (context.emits & other_awaited):
                continue
            if not (other.emits & awaited):
                continue
            written = context.writes | other.writes
            read = set()
            for participant in (context, other):
                for gate in participant.gates:
                    read |= gate
            if written & goal_paths:
                continue                       # le cycle fait avancer un but
            if written:
                # Il écrit quelque chose — on ne sait pas si c'est lu par une
                # garde d'outil ou de plan. Ne pas crier : direction de sûreté.
                continue
            findings.append(Finding(
                "V133", "warning",
                f"cycle de messages sans écriture entre {context.label} et "
                f"{other.label}",
                "les deux gestionnaires se relancent sans rien changer à "
                "l'état : le tour de rôle tournera jusqu'au plafond de ticks",
                context.line))
    # Un cycle A↔B est vu deux fois (une par sens) : on ne garde qu'une trace.
    unique: Dict[str, Finding] = {}
    for finding in findings:
        key = "|".join(sorted(finding.message.split(" entre ")[-1].split(" et ")))
        unique.setdefault(key, finding)
    return list(unique.values())


def _shared_never_read(program: Program, contexts: Sequence[Context]) -> List[Finding]:
    """Clés partagées écrites que nulle garde ne relit.

    Depuis la v1.6, `SHARED.<clé>` se lit dans une expression. Une clé qu'on
    écrit sans que personne ne la lise n'est donc plus une limite du langage
    mais une donnée morte : le coût de l'écriture et du versionnement est payé
    pour rien, et — plus gênant — l'auteur croit coordonner deux agents qui ne
    se coordonnent pas.

    Ce n'est pas un blocage : avertissement, jamais erreur.
    """
    written: Dict[str, Set[str]] = {}
    for agent in program.agents:
        for write in agent.memory.writes:
            if write.into == "SHARED":
                written.setdefault(SHARED + write.key, set()).add(agent.name)

    read: Set[str] = {signal for context in contexts
                      for gate in context.gates for signal in gate
                      if signal.startswith(SHARED)}
    # Une clé peut aussi être lue hors d'une garde de contexte — condition de
    # but, corps de plan. On balaie donc tout ce que le programme référence.
    for agent in program.agents:
        for goal in agent.goals:
            read |= {SHARED + p.split(".")[1] for p in _paths(goal.condition)
                     if p.startswith("SHARED.")}
        for stmt in _walk([s for plan in agent.plans for s in _plan_body(plan)]):
            for expr in _stmt_expressions(stmt):
                read |= {SHARED + p.split(".")[1] for p in _paths(expr)
                         if p.startswith("SHARED.")}

    orphans = sorted(set(written) - read)
    if not orphans:
        return []
    return [Finding(
        "V137", "warning",
        f"{signal_label(signal)} est écrit par "
        f"{', '.join(sorted(written[signal]))} et lu par personne",
        "l'écriture est tracée et versionnée, mais n'influence aucune "
        "décision : lire la clé dans une garde, ou cesser de l'écrire")
        for signal in orphans]


def _stmt_expressions(stmt: Stmt):
    from .analyzer import _stmt_exprs
    return _stmt_exprs(stmt)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def verify_liveness(program: Program) -> LivenessReport:
    """T8 — aucun agent n'attend un signal que nul ne produira."""
    report = LivenessReport()

    if len(program.agents) < 2:
        report.holds = True
        report.summary = ("un seul agent : ni message ni mémoire partagée à "
                          "coordonner")
        report.findings.append(Finding(
            "V134", "info", "programme mono-agent — T8 est vrai à vide"))
        return report

    extractor = _Extractor(program)
    contexts = extractor.run()
    produced = producible(contexts)

    # Signaux attendus par au moins un contexte, et jamais émis.
    awaited: Set[str] = {s for c in contexts for gate in c.gates for s in gate}
    dead = {s for s in awaited if s not in produced}
    report.dead_signals = dead
    report.dead_contexts = [c for c in contexts
                            if c.gates and not c.live(produced)]

    # Un cycle est trouvé une fois par signal qui le compose. Le rapporter
    # trois fois pour un cycle à trois n'apprend rien : on le canonise en le
    # faisant partir de son plus petit élément, et on n'en garde qu'un.
    seen_cycles: Set[Tuple[str, ...]] = set()

    for signal in sorted(dead):
        cycle = _blocking_cycle(signal, contexts, produced)
        emitters = [c.label for c in contexts if signal in c.emits]
        if cycle:
            ring = cycle[:-1]
            pivot = ring.index(min(ring))
            canonical = tuple(ring[pivot:] + ring[:pivot])
            if canonical in seen_cycles:
                continue
            seen_cycles.add(canonical)
            shown = list(canonical) + [canonical[0]]
            report.findings.append(Finding(
                "V130", "error",
                f"interblocage : {' → '.join(signal_label(s) for s in shown)}",
                "chaque émission de ce signal est gardée par un signal du "
                "même cycle : aucun point d'entrée ne l'amorce. Déclencher "
                "l'un d'eux depuis une garde de plan, un EVENT ou une règle "
                "DECIDE."))
        elif emitters:
            report.findings.append(Finding(
                "V131", "error",
                f"{signal_label(signal)} n'est jamais émis, bien que "
                f"{', '.join(sorted(emitters))} l'émette",
                "les contextes qui l'émettent ne s'exécutent jamais : ils "
                "attendent eux-mêmes un signal mort"))
        else:
            report.findings.append(Finding(
                "V132", "error",
                f"{signal_label(signal)} est attendu et aucun contexte ne "
                f"l'émet",
                "un agent attend indéfiniment ; l'émettre, ou retirer "
                "l'attente"))

    for context in sorted(report.dead_contexts, key=lambda c: c.label):
        missing = sorted({s for gate in context.gates for s in gate} - produced)
        report.findings.append(Finding(
            "V135", "error",
            f"{context.label} ne s'exécute jamais",
            f"attend {', '.join(signal_label(s) for s in missing)}",
            context.line))

    report.findings.extend(_livelock_findings(contexts, produced, program))
    report.findings.extend(_shared_never_read(program, contexts))

    errors = [f for f in report.findings if f.severity == "error"]
    if errors:
        report.holds = False
        report.summary = (f"{len(errors)} blocage(s) démontré(s) sur "
                          f"{len(program.agents)} agents")
        return report

    if extractor.llm_open:
        # Rien à réfuter, mais rien à démontrer non plus : chez ces agents,
        # tout plan est atteignable par proposition du modèle.
        report.holds = None
        report.summary = ("aucun blocage trouvé, mais l'échappatoire LLM "
                          "interdit de conclure")
        report.findings.append(Finding(
            "V136", "warning",
            f"DECIDE.REASON chez {', '.join(sorted(extractor.llm_open))} : "
            f"tout plan déclaré est atteignable par proposition du modèle",
            "T8 ne peut ni prouver ni réfuter un blocage tant qu'un plan peut "
            "être déclenché hors du déterministe"))
        return report

    warnings = [f for f in report.findings if f.severity == "warning"]
    report.holds = True
    report.summary = (f"{len(produced)} signal(aux) productible(s), aucune "
                      f"attente morte")
    if not warnings:
        report.findings.append(Finding(
            "V134", "info",
            "tout signal attendu possède un producteur atteignable"))
    return report

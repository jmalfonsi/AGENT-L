"""AST d'AGENT-L.

Arborescence produite par le parseur :

    Agent
     ├── goals[]        Goal
     ├── beliefs[]      BeliefDecl
     ├── observers[]    Observer
     ├── memory         MemorySpec (+ writes[])
     ├── tools[]        ToolDecl
     ├── policies[]     PolicyRule
     ├── plans[]        Plan → Step[] → Stmt[]
     ├── events[]       EventHandler
     ├── decide         Decide (rules[] + reason)
     ├── hypotheses[]   Hypothesis
     ├── loop           LoopSpec
     └── on_verify_fail FailHandler
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from .core import Symbol


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------
@dataclass
class Node:
    line: int = field(default=0, kw_only=True)


@dataclass
class Literal(Node):
    value: Any


@dataclass
class PathExpr(Node):
    parts: List[str]

    @property
    def dotted(self) -> str:
        return ".".join(self.parts)


@dataclass
class ListExpr(Node):
    items: List[Node]


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class UnOp(Node):
    op: str
    operand: Node


@dataclass
class CallExpr(Node):
    name: str
    args: List[Node] = field(default_factory=list)
    kwargs: Dict[str, Node] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Instructions
# --------------------------------------------------------------------------
@dataclass
class Stmt(Node):
    pass


@dataclass
class CallStmt(Stmt):
    call: CallExpr


@dataclass
class SetStmt(Stmt):
    target: str
    value: Node


@dataclass
class IfStmt(Stmt):
    cond: Node
    then: List[Stmt]
    otherwise: List[Stmt] = field(default_factory=list)


@dataclass
class VerifyStmt(Stmt):
    cond: Node
    on_fail: List[Stmt] = field(default_factory=list)
    label: Optional[str] = None


@dataclass
class Domain(Node):
    """Borne déclarée sur une sortie de LLM (v1.1).

    `kind` vaut RANGE (intervalle numérique) ou SET (énumération close).
    """

    kind: str
    low: Optional[float] = None
    high: Optional[float] = None
    values: List[Any] = field(default_factory=list)

    def render(self) -> str:
        if self.kind == "RANGE":
            return f"[{self.low:g}, {self.high:g}]"
        return "[" + ", ".join(str(v) for v in self.values) + "]"


@dataclass
class ReasonStmt(Stmt):
    task: str
    using: List[str] = field(default_factory=list)
    produce: Dict[str, str] = field(default_factory=dict)
    domains: Dict[str, Domain] = field(default_factory=dict)
    defaults: Dict[str, Node] = field(default_factory=dict)

    #: Mot-clé écrit dans le programme. `JUDGE` est un `REASON` dont les
    #: champs sont des jugements fermés : tout contrôle qui vise une sortie
    #: de modèle doit le voir, d'où l'héritage plutôt qu'un nœud parallèle.
    keyword: ClassVar[str] = "REASON"


@dataclass
class Question(Node):
    """Une question fermée posée à l'oracle (v1.10, `JUDGE`).

    Un `PRODUCE` déclare un **type** ; il ne dit pas ce que le champ veut
    dire. `kind: Symbol IN [question, enterprise_inquiry]` laisse le sens
    entier au nom du champ et à la consigne globale — le modèle doit deviner
    lequel des deux est visé, et les bancs montrent qu'il se trompe
    précisément là (`bench/jev_failures.md` : `holds_negative` lu comme
    « concerne les mentions négatives »). Une `Question` porte donc les deux
    choses que le type ne porte pas : la question (`instructions`) et le sens
    de chaque réponse possible (`criteria`, `levels`).

    Trois formes, et trois seulement, parce que ce sont celles qu'un oracle
    peut répondre **sans rien générer** :

      * `NOUL`   — une condition tient-elle ? (probabilité du oui)
      * `CHOICE` — laquelle de ces options, décrites une à une ?
      * `SCORE`  — où, sur ces niveaux ordonnés et décrits ?
    """

    name: str
    kind: str                                   # NOUL | CHOICE | SCORE
    instructions: str = ""
    criteria: Dict[str, str] = field(default_factory=dict)   # CHOICE
    levels: List[str] = field(default_factory=list)          # SCORE
    #: Sous cette probabilité, la réponse n'est **pas** retenue : le champ
    #: est déclaré absent et le `DEFAULT` du programme s'applique. Les bancs
    #: d'injection tranchent en faveur de l'abstention plutôt que d'un renvoi
    #: vers un modèle génératif, qui cède plus souvent au texte piégé.
    abstain_below: Optional[float] = None

    def type_name(self) -> str:
        return {"NOUL": "Bool", "CHOICE": "Symbol"}.get(self.kind, "Number")

    def domain(self) -> Optional["Domain"]:
        """Le domaine que le type seul ne donne pas — borne du runtime."""
        if self.kind == "CHOICE":
            return Domain("SET", values=[Symbol(v) for v in self.criteria],
                          line=self.line)
        if self.kind == "SCORE":
            return Domain("RANGE", 0.0, float(max(len(self.levels) - 1, 0)),
                          line=self.line)
        return None

    def payload(self) -> Dict[str, Any]:
        """Ce que l'oracle reçoit : la question, jamais le nom du champ seul."""
        out: Dict[str, Any] = {"kind": self.kind,
                               "instructions": self.instructions}
        if self.kind == "CHOICE":
            out["criteria"] = dict(self.criteria)
        elif self.kind == "SCORE":
            out["levels"] = list(self.levels)
        return out


@dataclass
class JudgeStmt(ReasonStmt):
    """`JUDGE { … }` — un `REASON` à questions fermées (v1.10).

    `produce`, `domains` et `defaults` sont dérivés des questions par le
    parseur : les contrôles statiques et le runtime qui bornent déjà les
    sorties de `REASON` s'appliquent sans changement, et une politique lit
    `judge.<champ>.p` comme elle lisait `confidence`.
    """

    questions: Dict[str, Question] = field(default_factory=dict)

    keyword: ClassVar[str] = "JUDGE"


@dataclass
class AskStmt(Stmt):
    addressee: str
    question: str = ""
    reason: str = ""
    timeout: float = 0.0
    default: Any = None


@dataclass
class DelegateStmt(Stmt):
    agent: str
    task: str = ""
    inputs: List[str] = field(default_factory=list)
    expect: List[str] = field(default_factory=list)


@dataclass
class MessageStmt(Stmt):
    """Émission d'un message vers un autre agent (v0.6)."""

    name: str
    to: Optional[str] = None          # None + broadcast=False → erreur statique
    broadcast: bool = False
    payload: Dict[str, Node] = field(default_factory=dict)


@dataclass
class MessageHandler(Node):
    """`ON MESSAGE <name> { WHEN … THEN … }` (v0.6)."""

    name: str
    when: Optional[Node] = None
    body: List[Stmt] = field(default_factory=list)


@dataclass
class LoopStmt(Stmt):
    body: List[Stmt]
    until: Optional[Node] = None
    max_iter: int = 100


@dataclass
class ForEachStmt(Stmt):
    """FOREACH item IN <expr> [MAX n] { ... } — v1.3.

    Le monde réel arrive en collections (des tickets, des lignes, des
    factures) alors que l'état d'AGENT-L est scalaire. `FOREACH` est la
    seule construction qui les relie, et elle le fait sans jamais introduire
    de valeur composite dans l'état : chaque élément est **projeté** en
    locales scalaires `item.<champ>` le temps d'une itération, puis déliée.
    L'analyse statique reste donc possible — le corps est un programme
    scalaire ordinaire, et une politique s'y évalue élément par élément.
    """

    var: str
    source: Node
    body: List[Stmt]
    max_iter: int = 200


@dataclass
class ControlStmt(Stmt):
    """RETRY n | ROLLBACK | ESCALATE | phase nue dans un LOOP."""

    kind: str
    arg: Any = None


@dataclass
class ThenPlan(Stmt):
    """THEN <plan> : déclenche un plan déclaré."""

    plan: str


# --------------------------------------------------------------------------
# Déclarations de haut niveau
# --------------------------------------------------------------------------
@dataclass
class Goal(Node):
    name: str
    mode: str = "MAINTAIN"          # MAINTAIN | ACHIEVE
    condition: Optional[Node] = None
    targets: List[Node] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class BeliefDecl(Node):
    path: str
    value: Node
    confidence: float = 1.0
    source: str = "declared"
    updated: Optional[str] = None


@dataclass
class Observer(Node):
    path: str
    when: Optional[Node] = None
    #: v1.8 — conduite déclarée quand le capteur ne rend rien.
    #: ``None`` (défaut) : le chemin reste indéfini, les gardes échouent
    #: fermé — la sémantique historique, inchangée. ``"ESCALATE"`` : la panne
    #: est nommée dans la trace et posée dans le monde. ``"DEGRADE"`` : la
    #: valeur de repli déclarée est substituée, tracée, à confiance basse.
    on_unknown: Optional[str] = None
    fallback: Optional[Node] = None


@dataclass
class MemoryWrite(Node):
    when: Optional[Node]
    store: List[str]
    into: str = "LONG_TERM"
    key: str = "records"          # v1.2 : compartiment nommé, versionné à part

    @property
    def target(self) -> str:
        return f"{self.into}.{self.key}"


@dataclass
class MemorySpec(Node):
    short_term: List[str] = field(default_factory=list)
    long_term: List[str] = field(default_factory=list)
    knowledge: List[str] = field(default_factory=list)
    shared: List[str] = field(default_factory=list)      # v0.6
    writes: List[MemoryWrite] = field(default_factory=list)


@dataclass
class ToolDecl(Node):
    name: str
    description: str = ""
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)
    side_effects: List[str] = field(default_factory=list)
    risk: str = "LOW"
    # --- v0.5 : l'outil devient un opérateur de planification ---------------
    bindings: Dict[str, str] = field(default_factory=dict)   # param → chemin
    requires: Optional[Node] = None                          # précondition
    effects: List["Effect"] = field(default_factory=list)    # postcondition sûre
    outcomes: List["Outcome"] = field(default_factory=list)  # v0.7 : incertaine
    cost: Optional[float] = None                             # sinon dérivé du risque
    #: v1.6 — durée d'exécution en **secondes**. Jamais dérivée : un coût se
    #: devine à partir du risque, une durée non. `None` signifie « non
    #: déclarée », pas « instantanée » — et le planificateur le signale dès
    #: que le temps entre dans son arbitrage.
    duration: Optional[float] = None
    attestations: Dict[str, str] = field(default_factory=dict)  # preuve → cible

    @property
    def is_operator(self) -> bool:
        """Un outil n'est planifiable que s'il déclare ce qu'il produit."""
        return bool(self.effects or self.outcomes)

    @property
    def branches(self) -> List["Outcome"]:
        """Vue unifiée : `EFFECT` = une issue certaine.

        Les probabilités sont renormalisées si leur somme s'écarte de 1 —
        l'analyseur signale l'écart, le runtime ne s'y casse pas.
        """
        if self.outcomes:
            total = sum(o.probability for o in self.outcomes) or 1.0
            return [Outcome(o.name, o.probability / total, o.effects,
                            line=o.line) for o in self.outcomes]
        return [Outcome("expected", 1.0, self.effects, line=self.line)]

    @property
    def is_uncertain(self) -> bool:
        return len(self.outcomes) > 1


@dataclass
class Effect(Node):
    """Postcondition déclarée : `path = expr`.

    `internal` (v1.7) marque une postcondition qui ne porte **pas sur le
    monde** mais sur la comptabilité de l'agent lui-même (`cycle.done`,
    `report.written`). Aucun capteur ne peut la démentir, et c'est légitime :
    elle est vraie parce que l'agent vient de l'écrire. La marquer distingue
    « rien ne peut vérifier cette prédiction » de « cette prédiction ne porte
    sur rien de vérifiable » — la première est un trou dans la preuve, la
    seconde une écriture d'état.
    """

    path: str
    value: Node
    internal: bool = False


@dataclass
class Outcome(Node):
    """Issue possible d'une action, avec sa probabilité (v0.7).

    `EFFECT { … }` est le cas dégénéré : une issue unique de probabilité 1.
    """

    name: str = "expected"
    probability: float = 1.0
    effects: List[Effect] = field(default_factory=list)


@dataclass
class UtilityTerm(Node):
    """`<condition> VALUE <n>` — additive sur les conditions vérifiées."""

    condition: Node
    value: float


@dataclass
class Redaction(Node):
    """`NEVER SEND <chemin>` — une interdiction de sortie, pas d'action.

    Le préfixe compte : `NEVER SEND credentials` couvre `credentials.token`.
    C'est la moitié manquante de la surface LLM. `USING` borne ce qu'un
    `REASON` **montre** ; il ne dit rien de `select_plan`, qui envoyait
    l'intégralité des croyances sans qu'aucune déclaration ne puisse s'y
    opposer. Une redaction porte sur toutes les sorties.
    """
    path: str = ""


@dataclass
class PolicyRule(Node):
    effect: str                      # NEVER | DENY | ALLOW | REQUIRE_APPROVAL
    target: str                      # nom d'outil ou "*"
    guard: Optional[Node] = None     # IF / WHEN


@dataclass
class Step(Node):
    name: str
    body: List[Stmt] = field(default_factory=list)


@dataclass
class Plan(Node):
    name: str
    steps: List[Step] = field(default_factory=list)
    when: Optional[Node] = None
    priority: int = 0


@dataclass
class EventHandler(Node):
    source: str
    when: Optional[Node] = None
    body: List[Stmt] = field(default_factory=list)


@dataclass
class Decide(Node):
    rules: List[IfStmt] = field(default_factory=list)
    reason: Optional[ReasonStmt] = None


@dataclass
class EvidenceItem(Node):
    """Un test observable, avec ses vraisemblances.

    `likelihood`  = P(e | h)      `given_not` = P(e | ¬h)
    `group`       nom du groupe de corrélation (v1.1) : au sein d'un groupe,
                  seule l'évidence la plus informative compte.
    """

    test: Node
    likelihood: float = 0.90
    given_not: float = 0.10
    label: str = ""
    group: str = ""


@dataclass
class Hypothesis(Node):
    name: str
    description: str = ""
    prior: float = 0.10
    evidence: List[EvidenceItem] = field(default_factory=list)
    threshold: float = 0.90
    explains_path: Optional[str] = None
    explains_value: Optional[Node] = None
    # v1.1 : plafond de déplacement total, en bits de log-cotes
    max_evidence: Optional[float] = None
    # v1.2 : a priori empirique tiré de la mémoire
    prior_bucket: Optional[str] = None
    prior_key: Optional[str] = None
    prior_filter: Optional[Node] = None

    @property
    def groups(self) -> List[str]:
        return sorted({e.group for e in self.evidence if e.group})


@dataclass
class PlannerSpec(Node):
    """Configuration du planificateur (v0.5)."""

    enabled: bool = True
    max_depth: int = 4
    max_nodes: int = 2000
    approval_cost: float = 10.0
    achieve: List[Node] = field(default_factory=list)
    target_confidence: float = 1.0        # v0.7 : P(but) exigée
    #: v1.6 — borne dure sur la durée cumulée d'un plan, en secondes. `None`
    #: = pas de contrainte de temps.
    deadline: Optional[float] = None
    #: v1.6 — conversion secondes → unités de coût dans le score. `0.0` par
    #: défaut : un programme antérieur planifie à l'identique.
    time_weight: float = 0.0


@dataclass
class LoopSpec(Node):
    body: List[Stmt] = field(default_factory=list)
    until: Optional[Node] = None
    max_iter: Optional[int] = None


@dataclass
class ScenarioStimulus(Node):
    kind: str
    name: str
    payload: List["Effect"] = field(default_factory=list)
    sender: str = "scenario"


@dataclass
class ScenarioAssertion(Node):
    kind: str
    target: str = ""


@dataclass
class Scenario(Node):
    """Critère d'acceptation porté par le programme (v1.4).

        SCENARIO attaque_actif_critique {
            GIVEN  { asset.criticality = CRITICAL, wazuh.alert_count = 37 }
            EXPECT { escalation.sent == yes } WITHIN 3
        }

    Deux usages, et c'est ce qui le rend rentable : **exécutable** par le
    runtime (`agentl test`) et **attaquable** par le vérificateur (T5).
    """

    name: str
    given: List["Effect"] = field(default_factory=list)   # monde initial
    expect: List[Node] = field(default_factory=list)      # attentes
    within: int = 1                                       # ticks accordés
    stimuli: List[ScenarioStimulus] = field(default_factory=list)
    assertions: List[ScenarioAssertion] = field(default_factory=list)


@dataclass
class Agent(Node):
    name: str
    version: str = "0.1"
    description: str = ""
    goals: List[Goal] = field(default_factory=list)
    beliefs: List[BeliefDecl] = field(default_factory=list)
    observers: List[Observer] = field(default_factory=list)
    memory: MemorySpec = field(default_factory=MemorySpec)
    tools: List[ToolDecl] = field(default_factory=list)
    policies: List[PolicyRule] = field(default_factory=list)
    policy_default: str = "ALLOW"
    plans: List[Plan] = field(default_factory=list)
    events: List[EventHandler] = field(default_factory=list)
    decide: Optional[Decide] = None
    hypotheses: List[Hypothesis] = field(default_factory=list)
    planner: Optional[PlannerSpec] = None
    messages: List[MessageHandler] = field(default_factory=list)
    utility: List[UtilityTerm] = field(default_factory=list)
    loop: Optional[LoopSpec] = None
    on_verify_fail: List[Stmt] = field(default_factory=list)
    scenarios: List[Scenario] = field(default_factory=list)
    #: v1.8 — `POLICY { NEVER SEND <chemin> }`. Chemins que rien n'expose au
    #: fournisseur de modèle, quel que soit le point de sortie : `REASON`
    #: avec ou sans `USING`, et `select_plan`, que `USING` n'a jamais couvert.
    redactions: List["Redaction"] = field(default_factory=list)

    def tool(self, name: str) -> Optional[ToolDecl]:
        return next((t for t in self.tools if t.name == name), None)

    def plan(self, name: str) -> Optional[Plan]:
        return next((p for p in self.plans if p.name == name), None)


@dataclass
class Program(Node):
    agents: List[Agent] = field(default_factory=list)

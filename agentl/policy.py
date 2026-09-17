"""Moteur de politiques.

Couche de sécurité **indépendante du LLM**. Aucune action ne touche un outil
sans être passée par `PolicyEngine.check`.

Ordre de résolution (le premier verdict négatif gagne) :

    1. NEVER  matchant et garde vraie  → DENIED (irrévocable)
    2. DENY   matchant et garde vraie  → DENIED
    3. si des règles ALLOW existent pour la cible :
           au moins une garde vraie    → autorisé
           sinon                       → DENIED (deny-by-default local)
    4. sinon : DEFAULT de l'agent (ALLOW par défaut)
    5. REQUIRE APPROVAL matchant       → APPROVAL_REQUIRED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core import Symbol
from .nodes import Agent, PolicyRule, ToolDecl
from .state import Evaluator, State
from .trivalent import UNKNOWN, applies_when_unknown, evaluate as _tri

ALLOWED = "ALLOWED"
DENIED = "DENIED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


@dataclass
class ActionRequest:
    """Proposition d'action soumise au moteur de politiques."""

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    risk: str = "LOW"
    side_effects: List[str] = field(default_factory=list)
    origin: str = "plan"          # plan | llm | event | decide
    confidence: Any = 1.0

    def render(self) -> str:
        rendered = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.tool}({rendered})"


@dataclass
class PolicyDecision:
    verdict: str
    rule: Optional[PolicyRule] = None
    reason: str = ""
    #: Locales recouvertes par un argument homonyme pendant l'évaluation des
    #: gardes (cf. `_ActionScope`). Vide dans le cas courant.
    shadowed_args: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOWED


class PolicyEngine:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.rules = agent.policies
        self.default = agent.policy_default

    # ------------------------------------------------------------------ API
    def check(self, request: ActionRequest, state: State) -> PolicyDecision:
        scope = _ActionScope(state, request)
        decision = self._check(request, scope)
        decision.shadowed_args = scope.shadowed_args
        return decision

    def _check(self, request: ActionRequest,
               scope: "_ActionScope") -> PolicyDecision:
        ev = Evaluator(scope)

        def matches(rule: PolicyRule) -> bool:
            """La règle s'applique-t-elle ? Sens de sûreté propre à l'effet."""
            if rule.target not in ("*", request.tool):
                return False
            truth = _tri(ev, rule.guard)
            if truth is UNKNOWN:
                return applies_when_unknown(rule.effect)
            return bool(truth)

        # Une garde qui ne se laisse pas trancher — donnée absente, capteur en
        # panne, arithmétique impossible — ne doit jamais faire échouer la
        # couche de sécurité *ouverte*. On échoue fermé, dans le sens propre à
        # chaque effet : une interdiction dont la garde est indéterminée
        # s'applique (on ne peut pas prouver la condition dangereuse fausse) ;
        # une autorisation indéterminée ne compte pas ; une approbation
        # indéterminée route vers l'humain.
        #
        # Jusqu'en v1.6 seules les *exceptions* suivaient cette règle. Une
        # donnée simplement absente rendait `False` en silence, et l'interdit
        # ne s'appliquait pas — le cas le plus fréquent passait à travers
        # l'intention. Voir `trivalent.py`.
        for rule in self.rules:
            if rule.effect == "NEVER" and matches(rule):
                return PolicyDecision(
                    DENIED, rule,
                    f"interdiction absolue NEVER {rule.target} (ligne {rule.line})",
                )

        for rule in self.rules:
            if rule.effect == "DENY" and matches(rule):
                return PolicyDecision(
                    DENIED, rule, f"DENY {rule.target} (ligne {rule.line})"
                )

        allows = [r for r in self.rules
                  if r.effect == "ALLOW" and r.target in ("*", request.tool)]
        if allows:
            satisfied = next(
                (r for r in allows
                 if _tri(ev, r.guard) is True), None)
            if satisfied is None:
                return PolicyDecision(
                    DENIED, None,
                    f"aucune règle ALLOW satisfaite pour {request.tool}",
                )
        elif self.default == "DENY":
            return PolicyDecision(
                DENIED, None,
                f"DEFAULT DENY : aucune autorisation déclarée pour {request.tool}",
            )

        for rule in self.rules:
            if rule.effect == "REQUIRE_APPROVAL" and matches(rule):
                return PolicyDecision(
                    APPROVAL_REQUIRED, rule,
                    f"approbation requise (ligne {rule.line})",
                )

        return PolicyDecision(ALLOWED, None, "autorisée")


class _ActionScope(State):
    """Vue de l'état enrichie des attributs de l'action en cours.

    Rend évaluables dans les gardes : `action.risk`, `action.tool`,
    `action.confidence`, `confidence`, ainsi que les arguments de l'appel
    (`service.environment` reste résolu depuis le monde).

    **L'argument prime sur la locale homonyme.** Jusqu'en v1.8 il était
    ajouté par `setdefault` : une locale portant le même nom l'emportait, et
    la garde jugeait une autre valeur que celle transmise à l'outil.

        SET target = safe
        delete(target=protected)
        POLICY { NEVER delete WHEN target == protected }

    L'outil recevait `protected`, la garde lisait `safe`, l'appel passait —
    et `verify` annonçait pourtant T1 « démontré ». C'était un contournement
    du `NEVER` par une simple affectation antérieure, sans rien de malveillant
    dans le programme : deux noms qui se rencontrent suffisaient. Le nom court
    étant l'idiome canonique de la SPEC (§7.1), la portée du défaut était
    celle de l'usage recommandé.

    Une garde de politique juge **l'appel en cours**. Dans cette portée, le
    nom d'un paramètre désigne ce qui part vers l'outil ; rien d'autre ne peut
    légitimement répondre à ce nom. La locale masquée reste lisible dans
    `shadowed_args` — le runtime la consigne, car deux déclarations qui se
    rencontrent en silence sont ce qui a produit le défaut.
    """

    def __init__(self, base: State, request: ActionRequest):
        self.world = base.world
        self.beliefs = base.beliefs
        self.memory = base.memory
        self.tick = base.tick
        self.untrusted = dict(getattr(base, "untrusted", {}))
        self.locals = dict(base.locals)
        self.locals.update({
            "action.tool": Symbol(request.tool),
            "action.risk": Symbol(request.risk),
            "action.origin": Symbol(request.origin),
            "action.confidence": request.confidence,
            "confidence": base.get("confidence") if "confidence" in base.locals
            else request.confidence,
        })
        #: Locales qu'un argument de l'appel a recouvertes, avec leur ancienne
        #: valeur. Vide dans le cas courant.
        self.shadowed_args: Dict[str, Any] = {}
        for key, value in request.args.items():
            self.locals[f"action.args.{key}"] = value
            if key in self.locals and _differs(self.locals[key], value):
                self.shadowed_args[key] = self.locals[key]
            self.locals[key] = value


def _differs(previous: Any, value: Any) -> bool:
    """Deux valeurs se distinguent-elles ? Une comparaison qui échoue vaut
    « oui » : mieux vaut signaler un masquage inexistant que taire un vrai."""
    try:
        return str(previous) != str(value)
    except Exception:  # pragma: no cover
        return True


def action_from_tool(decl: Optional[ToolDecl], name: str,
                     args: Dict[str, Any], origin: str,
                     confidence: Any = 1.0) -> ActionRequest:
    if decl is None:
        return ActionRequest(name, args, "UNKNOWN", [], origin, confidence)
    return ActionRequest(name, args, decl.risk, list(decl.side_effects),
                         origin, confidence)

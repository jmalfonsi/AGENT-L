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
# `ActionRequest` et `action_from_tool` vivent dans le noyau depuis la v1.9 ;
# réexportés ici, ils restent importables comme avant.
from .kernel.action import ActionRequest
from .kernel.gate import action_from_tool
from .kernel import provenance as P
from .kernel.provenance import UNKNOWN_LABEL, Prov
from .nodes import Agent, PolicyRule
from .state import Evaluator, State, label_key
from .trivalent import UNKNOWN, applies_when_unknown, evaluate as _tri

ALLOWED = "ALLOWED"
DENIED = "DENIED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


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
        # Étiquettes (v1.9) : celles de l'état, partagées sans copie, et par
        # dessus celles de l'action — ses arguments, sa décision.
        self.labels = getattr(base, "labels", {})
        self.attestations = getattr(base, "attestations", {})
        self._label_overlay: Dict[str, Prov] = {}
        declared = Prov({P.RUNTIME})
        confidence_label = (base.label_at(label_key("L", "confidence"))
                            if "confidence" in base.locals
                            and hasattr(base, "label_at") else declared)
        self.locals.update({
            "action.tool": Symbol(request.tool),
            "action.risk": Symbol(request.risk),
            "action.origin": Symbol(request.origin),
            "action.confidence": request.confidence,
            "confidence": base.get("confidence") if "confidence" in base.locals
            else request.confidence,
        })
        for key in ("action.tool", "action.risk", "action.origin"):
            self._label_overlay[label_key("L", key)] = declared
        for key in ("action.confidence", "confidence"):
            self._label_overlay[label_key("L", key)] = confidence_label
        provenance = getattr(request, "provenance", None) or {}
        #: Locales qu'un argument de l'appel a recouvertes, avec leur ancienne
        #: valeur. Vide dans le cas courant.
        self.shadowed_args: Dict[str, Any] = {}
        args_label = P.NONE
        for key, value in request.args.items():
            # Un argument sans étiquette — requête construite à la main —
            # est d'origine inconnue, donc non fiable.
            label = provenance.get(key, UNKNOWN_LABEL)
            args_label = args_label | label
            self.locals[f"action.args.{key}"] = value
            self._label_overlay[label_key("L", f"action.args.{key}")] = label
            if key in self.locals and _differs(self.locals[key], value):
                self.shadowed_args[key] = self.locals[key]
            self.locals[key] = value
            self._label_overlay[label_key("L", key)] = label
        #: Étiquette de l'action entière : ce qui l'a décidée, et ce qu'elle
        #: emporte. C'est ce que lisent `UNTRUSTED(action)` et consorts.
        self.action_label = provenance.get("$control", UNKNOWN_LABEL) \
            | args_label


def _differs(previous: Any, value: Any) -> bool:
    """Deux valeurs se distinguent-elles ? Une comparaison qui échoue vaut
    « oui » : mieux vaut signaler un masquage inexistant que taire un vrai."""
    try:
        return str(previous) != str(value)
    except Exception:  # pragma: no cover
        return True


__all__ = ["ALLOWED", "DENIED", "APPROVAL_REQUIRED", "ActionRequest",
           "PolicyDecision", "PolicyEngine", "action_from_tool"]

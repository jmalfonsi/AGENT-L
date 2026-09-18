"""Révision bayésienne des croyances (AGENT-L v0.4).

Jusqu'ici la confiance d'une croyance était **déclarée** : un nombre écrit à
la main dans le fichier source. C'est un aveu d'impuissance — l'agent affirme
une incertitude qu'il n'a pas mesurée.

Ce module la rend **dérivée**. Une `HYPOTHESIS` porte un a priori et une
liste de tests observables, chacun avec ses vraisemblances ; le postérieur
est calculé à chaque tick à partir de ce que l'agent perçoit réellement.

Forme en cotes (odds), numériquement stable et incrémentale :

    O(h)      = p / (1 - p)
    O(h | e)  = O(h) · Π_i  LR_i

    LR_i = P(e_i | h) / P(e_i | ¬h)               si e_i est observé vrai
         = (1 - P(e_i | h)) / (1 - P(e_i | ¬h))   si e_i est observé faux
         = 1                                       si e_i est indéterminé

    p(h | e) = O(h | e) / (1 + O(h | e))

L'indépendance conditionnelle des évidences sachant h est l'hypothèse du
classifieur bayésien naïf. Elle est fausse dès que deux capteurs observent la
même cause — ce qui, en supervision, est la règle et non l'exception :
`alert_count > 20` et `anomaly_score > 0.75` décrivent la même rafale.

Multiplier leurs rapports de vraisemblance surestime alors systématiquement
le postérieur. Tant que ce nombre sert à trier, c'est un défaut de
calibration. Dès qu'il alimente une garde de politique — et `ALLOW … IF
P(h) >= 0.95` fait exactement cela — c'est un défaut de **sûreté** : la
surconfiance fait franchir un seuil à un état qui ne le mérite pas.

La v1.1 offre donc deux garde-fous, tous deux déclaratifs :

  * `GROUP <nom> { … }` — corrélation *connue*. Au sein d'un groupe, les
    évidences ne s'additionnent pas : seule la plus informative compte. C'est
    le traitement conservateur usuel de l'évidence redondante.

  * `MAX_EVIDENCE <bits>` — corrélation *inconnue*. Plafonne le déplacement
    total des log-cotes, quelle que soit la quantité d'indices accumulés.

Aucun des deux ne rend le modèle exact. Les deux l'empêchent de mentir dans
le sens dangereux, et la trace montre ce qui a été absorbé ou écrêté.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .core import UNDEFINED
from .nodes import EvidenceItem, Hypothesis
from .state import Evaluator, State

EPS = 1e-6


def _clamp(p: float) -> float:
    return min(max(p, EPS), 1.0 - EPS)


@dataclass
class EvidenceOutcome:
    label: str
    observed: Optional[bool]      # None = indéterminé
    likelihood_ratio: float
    weight_bits: float            # log2(LR) brut
    group: str = ""
    absorbed: bool = False        # évincé par un plus fort de son groupe

    @property
    def effective_bits(self) -> float:
        return 0.0 if self.absorbed else self.weight_bits

    def render(self) -> str:
        if self.observed is None:
            return f"{self.label} = ? (sans effet)"
        sign = "+" if self.weight_bits >= 0 else ""
        base = (f"{self.label} = {'vrai' if self.observed else 'faux'} "
                f"({sign}{self.weight_bits:.2f} bits)")
        if self.absorbed:
            return base + f" — absorbé par le groupe {self.group}"
        return base


@dataclass
class Inference:
    name: str
    prior: float
    posterior: float
    threshold: float
    outcomes: List[EvidenceOutcome] = field(default_factory=list)
    prior_origin: str = "déclaré"
    capped: bool = False
    raw_posterior: Optional[float] = None      # avant garde-fous
    effective_shift: float = 0.0               # après absorption ET écrêtage

    @property
    def supported(self) -> bool:
        return self.posterior >= self.threshold

    @property
    def shift_bits(self) -> float:
        """Déplacement effectif, après absorption de groupe et écrêtage."""
        return self.effective_shift

    @property
    def raw_shift_bits(self) -> float:
        return sum(o.weight_bits for o in self.outcomes)

    @property
    def absorbed(self) -> List[EvidenceOutcome]:
        return [o for o in self.outcomes if o.absorbed]

    def render(self) -> str:
        arrow = "≥ seuil" if self.supported else "< seuil"
        head = (f"{self.name}: {self.prior:.2f} → {self.posterior:.3f} "
                f"({arrow} {self.threshold:.2f})")
        if self.raw_posterior is not None and abs(
                self.raw_posterior - self.posterior) > 5e-4:
            head += f" [naïf : {self.raw_posterior:.3f}]"
        return head

    def explain(self) -> str:
        """Les évidences par contribution décroissante en valeur absolue."""
        ranked = sorted(self.outcomes, key=lambda o: -abs(o.weight_bits))
        parts = [o.render() for o in ranked if o.observed is not None]
        if self.capped:
            parts.append(f"écrêté à {self.shift_bits:+.2f} bits "
                         f"(brut {self.raw_shift_bits:+.2f})")
        if self.prior_origin != "déclaré":
            parts.append(f"a priori {self.prior_origin}")
        return " | ".join(parts)


def _test(item: EvidenceItem, state: State) -> Optional[bool]:
    """Un test dont un opérande est indéfini reste **indéterminé**.

    C'est la différence essentielle avec `Evaluator.test`, qui renvoie faux :
    en inférence, ignorer une évidence et l'observer fausse n'ont pas du tout
    le même effet sur le postérieur.
    """
    ev = Evaluator(state)
    try:
        value = ev.eval(item.test)
    except Exception:                                  # noqa: BLE001
        return None
    if value is UNDEFINED or ev.notes:
        return None
    from .core import truthy
    return truthy(value)


def empirical_prior(hypothesis: Hypothesis,
                    state: State) -> Optional[Tuple[float, str]]:
    """A priori tiré de la mémoire (v1.2), avec lissage de Jeffreys.

    Un agent qui a vu quarante faux positifs de scan ne devrait pas démarrer
    avec le même a priori qu'un agent neuf. Le lissage évite qu'un historique
    vide ou unanime ne produise 0 ou 1.
    """
    if hypothesis.prior_bucket is None:
        return None
    bucket = state.memory.get(hypothesis.prior_bucket, {})
    records = bucket.get(hypothesis.prior_key or "records", [])
    if not isinstance(records, list) or not records:
        return None
    matches = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if hypothesis.prior_filter is None:
            matches += 1
            continue
        scope = State()
        scope.world = state.world
        scope.beliefs = state.beliefs
        scope.memory = state.memory
        scope.locals = dict(record)
        if Evaluator(scope).test(hypothesis.prior_filter):
            matches += 1
    total = len(records)
    value = (matches + 0.5) / (total + 1.0)
    origin = (f"empirique {matches}/{total} "
              f"({hypothesis.prior_bucket}.{hypothesis.prior_key})")
    return value, origin


def infer(hypothesis: Hypothesis, state: State) -> Inference:
    measured = empirical_prior(hypothesis, state)
    prior_value = measured[0] if measured else hypothesis.prior
    prior_origin = measured[1] if measured else "déclaré"
    prior = _clamp(prior_value)

    outcomes: List[EvidenceOutcome] = []
    for index, item in enumerate(hypothesis.evidence):
        observed = _test(item, state)
        label = item.label or _label(item, index)
        if observed is None:
            outcomes.append(EvidenceOutcome(label, None, 1.0, 0.0, item.group))
            continue
        lh, ln = _clamp(item.likelihood), _clamp(item.given_not)
        ratio = (lh / ln) if observed else ((1.0 - lh) / (1.0 - ln))
        outcomes.append(EvidenceOutcome(label, observed, ratio,
                                        math.log2(ratio), item.group))

    raw_shift = sum(o.weight_bits for o in outcomes)
    _absorb_groups(outcomes)
    shift = sum(o.effective_bits for o in outcomes)

    capped = False
    if hypothesis.max_evidence is not None:
        limit = abs(hypothesis.max_evidence)
        if abs(shift) > limit:
            shift = math.copysign(limit, shift)
            capped = True

    posterior = _from_log_odds(_log_odds(prior) + shift)
    raw_posterior = _from_log_odds(_log_odds(prior) + raw_shift)
    return Inference(hypothesis.name, prior_value, posterior,
                     hypothesis.threshold, outcomes, prior_origin, capped,
                     raw_posterior, shift)


def reachable_range(hypothesis: Hypothesis) -> Tuple[float, float]:
    """Bornes du postérieur, tous états du monde confondus (v1.1).

    Les vraisemblances étant déclarées, l'amplitude que le modèle peut
    atteindre est **calculable statiquement** : on somme, pour chaque
    évidence, la branche qui pousse le plus fort dans chaque direction, en
    appliquant l'absorption de groupe et l'écrêtage.

    C'est ce qui permet au vérificateur de constater qu'une garde
    `ALLOW … IF P(h) >= 0.95` est inatteignable quand le modèle plafonne à
    0,93 — une capacité morte que ni le typage ni la logique des gardes ne
    révèlent.
    """
    prior = _clamp(hypothesis.prior)
    positives: dict = {}
    negatives: dict = {}

    for index, item in enumerate(hypothesis.evidence):
        lh, ln = _clamp(item.likelihood), _clamp(item.given_not)
        if_true = math.log2(lh / ln)
        if_false = math.log2((1.0 - lh) / (1.0 - ln))
        best_up = max(if_true, if_false, 0.0)
        best_down = min(if_true, if_false, 0.0)
        key = item.group or f"__{index}"
        positives[key] = max(positives.get(key, 0.0), best_up)
        negatives[key] = min(negatives.get(key, 0.0), best_down)

    up = sum(positives.values())
    down = sum(negatives.values())
    if hypothesis.max_evidence is not None:
        limit = abs(hypothesis.max_evidence)
        up = min(up, limit)
        down = max(down, -limit)

    base = _log_odds(prior)
    return _from_log_odds(base + down), _from_log_odds(base + up)


def _absorb_groups(outcomes: List[EvidenceOutcome]) -> None:
    """Dans un groupe corrélé, seule l'évidence la plus informative compte."""
    groups: dict = {}
    for outcome in outcomes:
        if outcome.group and outcome.observed is not None:
            groups.setdefault(outcome.group, []).append(outcome)
    for members in groups.values():
        if len(members) < 2:
            continue
        strongest = max(members, key=lambda o: abs(o.weight_bits))
        for outcome in members:
            outcome.absorbed = outcome is not strongest


def _log_odds(probability: float) -> float:
    p = _clamp(probability)
    return math.log2(p / (1.0 - p))


def _from_log_odds(bits: float) -> float:
    bits = max(min(bits, 60.0), -60.0)
    odds = 2.0 ** bits
    return odds / (1.0 + odds)


def _label(item: EvidenceItem, index: int) -> str:
    from .runtime import _render_expr
    try:
        return _render_expr(item.test)
    except Exception:                                  # noqa: BLE001
        return f"e{index}"


def publish(inference: Inference, state: State, prov=None) -> None:
    """Expose le résultat aux expressions : gardes, VERIFY, objectifs.

    `prov` : provenance du postérieur — `INFERRED`, plus celle des évidences
    qu'il a lues (v1.9). Un postérieur nourri par un texte reçu n'est pas une
    mesure du monde.
    """
    for prefix in (inference.name, f"hypothesis.{inference.name}"):
        state.set_world(f"{prefix}.prior", inference.prior, prov)
        state.set_world(f"{prefix}.posterior", inference.posterior, prov)
        state.set_world(f"{prefix}.supported", inference.supported, prov)
        state.set_world(f"{prefix}.bits", inference.shift_bits, prov)
        state.set_world(f"{prefix}.capped", inference.capped, prov)

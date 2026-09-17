"""Tests AAA du TEMPS dans le planificateur (v1.6).

Le planificateur traitait une action de 45 s et une action de 4 min comme
équivalentes. Or arbitrer entre rapide-et-bruyant et lent-et-sûr *est* le
problème du domaine, pas un détail d'implémentation.

Deux leviers, volontairement distincts :

  * **`DEADLINE`** — borne dure. Un plan trop long n'est pas engendré, comme
    une action interdite : on n'écarte pas après coup ce qu'on peut ne pas
    produire.
  * **`TIME_WEIGHT`** — taux de change secondes → coût. Le langage fournit le
    taux, l'auteur le fixe : câbler une préférence pour la vitesse serait
    décider du domaine à sa place.

Et une propriété qui conditionne les deux : **la recherche doit comparer les
routes**. Elle ne le faisait pas — à effets égaux, le plan retenu dépendait de
l'ordre de déclaration des outils.

    python -m pytest tests/test_planner_time_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.analyzer import Analyzer
from agentl.parser import parse_source
from agentl.planner import Planner, fmt_duration
from agentl.state import State

#: Deux routes vers le même but : rapide-et-coûteuse, lente-et-bon-marché.
TWO_ROUTES = """
AGENT triage {{
    VERSION "1.0"
    GOAL contained {{ ACHIEVE threat.status == contained }}

    TOOL kill_switch {{
        INPUT {{ }}
        RISK   {{ operational = HIGH }}
        EFFECT {{ threat.status = contained }}
        COST 20
        DURATION 45s
    }}
    TOOL careful_isolation {{
        INPUT {{ }}
        RISK   {{ operational = LOW }}
        EFFECT {{ threat.status = contained }}
        COST 2
        DURATION 4min
    }}
    POLICY {{ DEFAULT ALLOW }}
    PLANNER {{ ENABLE ACHIEVE threat.status == contained {extra} }}
}}
"""


def _plan(extra: str = "", source: str = TWO_ROUTES):
    agent = parse_source(source.format(extra=extra), "<test>").agents[0]
    return Planner(agent).synthesize(State())


def _tools(result):
    return [action.tool for action in result.actions]


# --------------------------------------------------------------------------
# M1 — la recherche compare les routes
# --------------------------------------------------------------------------
class TestSearchComparesRoutes(unittest.TestCase):
    """Sans cette propriété, `DEADLINE` et `TIME_WEIGHT` seraient décoratifs :
    on ne peut pas arbitrer entre deux routes qu'on ne compare pas."""

    def test_the_cheapest_route_wins(self):
        self.assertEqual(_tools(_plan()), ["careful_isolation"])

    def test_the_result_does_not_depend_on_declaration_order(self):
        """Le défaut trouvé en v1.6 : un état déjà atteint était bloqué
        définitivement, fût-ce par la route la plus chère."""
        head = TWO_ROUTES[:TWO_ROUTES.index("    TOOL kill_switch")]
        fast = TWO_ROUTES[TWO_ROUTES.index("    TOOL kill_switch"):
                          TWO_ROUTES.index("    TOOL careful_isolation")]
        slow = TWO_ROUTES[TWO_ROUTES.index("    TOOL careful_isolation"):
                          TWO_ROUTES.index("    POLICY")]
        tail = TWO_ROUTES[TWO_ROUTES.index("    POLICY"):]

        swapped = _plan(source=head + slow + fast + tail)

        self.assertEqual(_tools(swapped), _tools(_plan()))

    def test_a_costlier_but_faster_route_is_kept_for_later_comparison(self):
        """Dominance de Pareto sur (coût, durée) : une route plus chère mais
        plus rapide reste utile sous une échéance, donc elle n'est pas jetée."""
        result = _plan("DEADLINE 2min")

        self.assertEqual(_tools(result), ["kill_switch"])


# --------------------------------------------------------------------------
# M2 — DEADLINE, la borne dure
# --------------------------------------------------------------------------
class TestDeadline(unittest.TestCase):

    def test_a_plan_beyond_the_deadline_is_not_produced(self):
        result = _plan("DEADLINE 2min")

        self.assertEqual(_tools(result), ["kill_switch"])
        self.assertLessEqual(result.duration, 120.0)

    def test_the_pruning_says_why_and_by_how_much(self):
        """« écarté » n'aide personne ; « 4min > 2min » se corrige."""
        result = _plan("DEADLINE 2min")

        notes = [n for n in result.pruned_by_policy if "échéance" in n]
        self.assertTrue(notes)
        self.assertIn("careful_isolation", notes[0])
        self.assertIn("4min", notes[0])
        self.assertIn("2min", notes[0])

    def test_a_generous_deadline_changes_nothing(self):
        self.assertEqual(_tools(_plan("DEADLINE 1h")), _tools(_plan()))

    def test_a_deadline_shorter_than_every_route_yields_no_plan(self):
        result = _plan("DEADLINE 10s")

        self.assertFalse(result.found)

    def test_the_plan_reports_its_duration(self):
        self.assertEqual(_plan().duration, 240.0)
        self.assertEqual(_plan("DEADLINE 2min").duration, 45.0)


# --------------------------------------------------------------------------
# M3 — TIME_WEIGHT, le taux de change
# --------------------------------------------------------------------------
class TestTimeWeight(unittest.TestCase):

    def test_a_zero_weight_plans_exactly_as_before(self):
        """La v1.6 doit être une généralisation stricte : un programme
        antérieur planifie à l'identique."""
        self.assertEqual(_tools(_plan("TIME_WEIGHT 0")), _tools(_plan()))
        self.assertEqual(_plan("TIME_WEIGHT 0").cost, _plan().cost)

    def test_a_heavy_weight_flips_the_choice(self):
        """2 + 0.1×240 = 26 contre 20 + 0.1×45 = 24.5."""
        self.assertEqual(_tools(_plan("TIME_WEIGHT 0.1")), ["kill_switch"])

    def test_a_light_weight_does_not_flip_it(self):
        """2 + 0.01×240 = 4.4 contre 20 + 0.01×45 = 20.45."""
        self.assertEqual(_tools(_plan("TIME_WEIGHT 0.01")),
                         ["careful_isolation"])

    def test_the_weighted_time_shows_up_in_the_cost(self):
        self.assertAlmostEqual(_plan("TIME_WEIGHT 0.1").cost, 24.5, places=6)

    def test_duration_is_reported_unweighted(self):
        """Le coût absorbe la pondération ; la durée reste une durée."""
        self.assertEqual(_plan("TIME_WEIGHT 0.1").duration, 45.0)


# --------------------------------------------------------------------------
# M4 — les unités
# --------------------------------------------------------------------------
class TestDurationUnits(unittest.TestCase):

    def test_units_are_normalised_to_seconds(self):
        agent = parse_source(TWO_ROUTES.format(extra=""), "<test>").agents[0]
        by_name = {tool.name: tool for tool in agent.tools}

        self.assertEqual(by_name["kill_switch"].duration, 45.0)
        self.assertEqual(by_name["careful_isolation"].duration, 240.0)

    def test_a_bare_number_is_refused(self):
        """L'unité est ce qui distingue une durée d'un coût. La deviner
        reviendrait à deviner l'échelle."""
        from agentl.core import ParseError

        with self.assertRaises(ParseError):
            parse_source(TWO_ROUTES.format(extra="").replace(
                "DURATION 45s", "DURATION 45"), "<test>")

    def test_an_undeclared_duration_is_none_not_zero(self):
        """`None` signifie « non déclarée », pas « instantanée » — la
        distinction est ce que W126 exploite."""
        agent = parse_source(TWO_ROUTES.format(extra="").replace(
            "        DURATION 45s\n", ""), "<test>").agents[0]

        self.assertIsNone({t.name: t for t in agent.tools}["kill_switch"].duration)

    def test_durations_render_readably(self):
        for seconds, expected in ((0.5, "500ms"), (45, "45s"), (120, "2min"),
                                  (165, "2min45s"), (3600, "1h"),
                                  (5400, "1h30")):
            with self.subTest(seconds):
                self.assertEqual(fmt_duration(seconds), expected)


# --------------------------------------------------------------------------
# M5 — W126 : un zéro non déclaré n'est pas une mesure
# --------------------------------------------------------------------------
class TestUndeclaredDurationIsFlagged(unittest.TestCase):

    def _codes(self, extra: str, strip: bool = True):
        source = TWO_ROUTES.format(extra=extra)
        if strip:
            source = source.replace("        DURATION 45s\n", "")
        agent = parse_source(source, "<test>").agents[0]
        return [d.code for d in Analyzer(agent).run()]

    def test_a_deadline_makes_the_omission_visible(self):
        """Sinon le planificateur préférerait l'opérateur muet pour la seule
        raison qu'il se tait."""
        self.assertIn("W126", self._codes("DEADLINE 2min"))

    def test_a_time_weight_makes_it_visible_too(self):
        self.assertIn("W126", self._codes("TIME_WEIGHT 0.1"))

    def test_without_a_time_model_the_omission_is_not_flagged(self):
        """Sans arbitrage temporel, l'omission ne biaise rien — et une alarme
        qui crie partout ne se lit plus."""
        self.assertNotIn("W126", self._codes(""))

    def test_a_fully_declared_program_is_not_flagged(self):
        self.assertNotIn("W126", self._codes("DEADLINE 2min", strip=False))

    def test_a_non_operator_tool_is_not_flagged(self):
        """Un outil sans EFFECT n'entre pas dans la recherche : lui réclamer
        une durée serait du bruit."""
        source = TWO_ROUTES.format(extra="DEADLINE 2min").replace(
            "    POLICY {",
            "    TOOL notify { INPUT { } RISK { operational = LOW } }\n"
            "    POLICY {")

        codes = [d.code for d in
                 Analyzer(parse_source(source, "<test>").agents[0]).run()]

        self.assertEqual(codes.count("W126"), 0)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()

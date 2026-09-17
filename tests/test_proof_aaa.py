"""Tests AAA de la COUCHE PREUVE (planificateur + vérificateur + solveur).

Chaque test cible une propriété de **soundness** : le vérificateur ne doit
jamais déclarer DÉMONTRÉ un théorème faux ni inventer un défaut, et le
planificateur ne doit jamais engendrer une action qu'une politique NEVER
interdit. Les cas sont adverses : ils décrivent des agents écrits pour piéger
la couche preuve.

Les quatre défauts corrigés (chacun avec un test qui échouait avant le
correctif) :

  1. V109 franchi par un `OR` — faux théorème inventé (T3 réfuté à tort).
  2. Planner : NEVER conditionné à `action.origin == plan` ignoré à la synthèse.
  3. V104 : garde tautologique (`IF 1 == 1`) prise pour une garde d'état (T4
     faussement démontré).
  4. V106 : but déclaré RÉFUTÉ sur une simple coupure de profondeur.
"""
from __future__ import annotations

import unittest

from agentl import Planner, parse_source, satisfiable, verify
from agentl.nodes import BinOp, Literal, PathExpr
from agentl.state import State


def agent(src: str):
    return parse_source(src).agents[0]


def theorem(report, key):
    return next(t for t in report.theorems if t.key == key)


# --------------------------------------------------------------------------
# 1. V109 — le seuil probabiliste sous un OR n'est PAS obligatoire
# --------------------------------------------------------------------------
class TestUnreachableThresholdSoundness(unittest.TestCase):
    TEMPLATE = """
    AGENT Gap {{
        GOAL g {{ MAINTAIN done == yes }}
        HYPOTHESIS h {{ PRIOR 0.05
            EVIDENCE {{ a > 1 LIKELIHOOD 0.7 GIVEN_NOT 0.3 }}
            THRESHOLD 0.5 }}
        TOOL act {{ OUTPUT {{ done: Symbol }} RISK HIGH }}
        POLICY {{ DEFAULT DENY  ALLOW act IF {guard} }}
        PLAN p WHEN 1 == 1 {{ STEP s {{ act() VERIFY done == yes }} }}
    }}
    """

    def codes(self, guard):
        report = verify(agent(self.TEMPLATE.format(guard=guard)))
        return {f.code for f in report.findings}

    def test_or_escape_does_not_invent_a_dead_capability(self):
        # P(h) plafonne bien en-dessous de 0.99, mais `override == yes` peut
        # satisfaire l'ALLOW : l'outil N'EST PAS mort. Réfuter T3 ici serait
        # inventer un défaut — direction interdite au vérificateur.
        self.assertNotIn("V109", self.codes("P(h) >= 0.99 OR override == yes"))
        self.assertTrue(theorem(
            verify(agent(self.TEMPLATE.format(
                guard="P(h) >= 0.99 OR override == yes"))), "T3").holds)

    def test_pure_unreachable_threshold_is_still_refuted(self):
        self.assertIn("V109", self.codes("P(h) >= 0.99"))

    def test_mandatory_conjoined_threshold_is_still_refuted(self):
        # Sous un AND, le seuil doit tenir : capacité morte, V109 attendu.
        self.assertIn("V109", self.codes("P(h) >= 0.99 AND override == yes"))


# --------------------------------------------------------------------------
# 2. Planner — un NEVER sur l'origine réelle de l'action est respecté
# --------------------------------------------------------------------------
class TestPlannerNeverInvariant(unittest.TestCase):
    def synth(self, policy):
        src = f"""
        AGENT PlanGap {{
            GOAL g {{ ACHIEVE done == yes }}
            TOOL act {{ RISK LOW EFFECT {{ done = yes }} COST 1 }}
            POLICY {{ {policy} }}
            PLANNER {{ ACHIEVE done == yes }}
        }}"""
        return Planner(agent(src)).synthesize(State())

    def test_never_on_plan_origin_excludes_the_action(self):
        # Un plan synthétisé s'exécute avec origin="plan" (runtime). Le
        # planificateur DOIT interroger la politique sous cette origine, sinon
        # il engendre une action qu'un NEVER interdit — violation de §17.
        result = self.synth("NEVER act IF action.origin == plan")
        self.assertFalse(result.found)
        self.assertNotIn("act", [a.tool for a in result.actions])

    def test_never_on_llm_origin_does_not_block_the_planner(self):
        # Contrôle : le planificateur n'est pas le LLM ; un NEVER visant
        # l'origine `llm` ne doit pas l'empêcher de synthétiser.
        result = self.synth("NEVER act IF action.origin == llm")
        self.assertTrue(result.found)
        self.assertEqual([a.tool for a in result.actions], ["act"])

    def test_no_gambling_on_uncertainty_across_belief_branches(self):
        # `probe` produit deux mondes équiprobables ; dans l'un, `act`
        # déclenche un NEVER. L'invariant renforcé (§20) exige que `act` soit
        # exclu ENTIÈREMENT, sans parier sur la branche favorable à 0.5.
        src = """
        AGENT Gamble {
            GOAL g { ACHIEVE done == yes }
            TOOL probe {
                RISK LOW
                OUTCOME hi WITH 0.5 { inspected = yes, threat = high }
                OUTCOME lo WITH 0.5 { inspected = yes, threat = low }
                COST 1 }
            TOOL act {
                RISK LOW REQUIRES { inspected == yes }
                EFFECT { done = yes } COST 1 }
            POLICY { NEVER act IF threat == high }
            PLANNER { ACHIEVE done == yes }
        }"""
        result = Planner(agent(src)).synthesize(State())
        self.assertNotIn("act", [a.tool for a in result.actions])
        self.assertLessEqual(result.goal_probability, 1e-9)


# --------------------------------------------------------------------------
# 3. T4 — une garde tautologique ne borne rien
# --------------------------------------------------------------------------
class TestLLMSurfaceSoundness(unittest.TestCase):
    def t4(self, guard):
        src = f"""
        AGENT Surface {{
            GOAL g {{ MAINTAIN safe == yes }}
            TOOL wipe {{ RISK CRITICAL SIDE_EFFECT {{ disk }} }}
            POLICY {{ ALLOW wipe IF {guard} }}
            DECIDE {{ REASON {{ TASK "choose" }} }}
            PLAN p {{ STEP s {{ wipe() }} }}
        }}"""
        return theorem(verify(agent(src)), "T4")

    def test_tautological_guard_is_flagged(self):
        # Le LLM peut déclencher un outil CRITICAL sous `IF 1 == 1` : la surface
        # N'EST PAS bornée par une garde d'état. T4 ne doit pas être démontré.
        t = self.t4("1 == 1")
        self.assertFalse(t.holds)
        self.assertIn("V104", [f.code for f in t.findings])

    def test_numeric_constant_guard_is_flagged(self):
        t = self.t4("2 > 1")
        self.assertFalse(t.holds)

    def test_real_state_guard_is_not_flagged(self):
        self.assertTrue(self.t4("threat.status == active").holds)

    def test_opaque_function_guard_is_not_invented_as_unguarded(self):
        # Une garde qui dépend d'une fonction opaque dépend de l'état : la
        # signaler serait inventer un défaut. Elle doit passer.
        self.assertTrue(self.t4("score(x) > 0").holds)


# --------------------------------------------------------------------------
# 4. T2 — une recherche bornée ne réfute jamais l'atteignabilité
# --------------------------------------------------------------------------
class TestReachabilityBoundedness(unittest.TestCase):
    CHAIN = """
    AGENT Chain {
        GOAL g { ACHIEVE s5 == yes }
        TOOL a1 { RISK LOW EFFECT { s1 = yes } COST 1 }
        TOOL a2 { RISK LOW REQUIRES { s1 == yes } EFFECT { s2 = yes } COST 1 }
        TOOL a3 { RISK LOW REQUIRES { s2 == yes } EFFECT { s3 = yes } COST 1 }
        TOOL a4 { RISK LOW REQUIRES { s3 == yes } EFFECT { s4 = yes } COST 1 }
        TOOL a5 { RISK LOW REQUIRES { s4 == yes } EFFECT { s5 = yes } COST 1 }
        POLICY { ALLOW a1 ALLOW a2 ALLOW a3 ALLOW a4 ALLOW a5 }
        PLANNER { ACHIEVE s5 == yes }
    }"""

    def test_depth_cutoff_is_bornce_not_refuted(self):
        # But atteignable en 5 opérateurs, recherche bornée à 4 : le verdict
        # doit être BORNÉ (holds None), jamais RÉFUTÉ. Réfuter serait un faux
        # théorème — l'inatteignabilité n'a pas été prouvée.
        t = theorem(verify(agent(self.CHAIN), depth=4), "T2")
        self.assertIsNone(t.holds)
        self.assertNotIn("V106", [f.code for f in t.findings])
        self.assertIn("V113", [f.code for f in t.findings])

    def test_sufficient_depth_proves_reachability(self):
        t = theorem(verify(agent(self.CHAIN), depth=6), "T2")
        self.assertTrue(t.holds)

    def test_exhausted_space_is_soundly_refuted(self):
        # Aucun EFFECT ne touche `unreachable` : l'espace est épuisé sans
        # atteindre le but. Là, la réfutation (V106) est légitime.
        src = """
        AGENT Imp {
            GOAL g { ACHIEVE unreachable == yes }
            TOOL a1 { RISK LOW EFFECT { s1 = yes } COST 1 }
            POLICY { ALLOW a1 }
            PLANNER { ACHIEVE unreachable == yes }
        }"""
        t = theorem(verify(agent(src), depth=4), "T2")
        self.assertFalse(t.holds)
        self.assertIn("V106", [f.code for f in t.findings])


# --------------------------------------------------------------------------
# 4bis. Point fixe de T2 (v1.4) — démontrer au lieu de constater
# --------------------------------------------------------------------------
class TestReachabilityFixpoint(unittest.TestCase):
    """L'espace d'états est fini (les `EFFECT` sont des affectations ground) :
    la recherche doit aller jusqu'à son point fixe. Sans lui, une absence de
    route ne se distingue pas d'une recherche trop courte — et le verdict
    reste « ◐ BORNÉ » là où il pourrait conclure."""

    #: Interdit sans repli, et l'agent ne prévoit rien : il calera en silence.
    STALLS = """
    AGENT Stalls {
        GOAL g { ACHIEVE contained == yes }
        TOOL isolate { RISK LOW EFFECT { contained = yes } COST 1 }
        POLICY { ALLOW isolate  NEVER isolate WHEN asset == CRITICAL }
        PLANNER { ACHIEVE contained == yes }
    }"""

    #: Le même, mais l'escalade est déclarée : plus aucune route, et pourtant
    #: l'agent fait ce qu'on attend de lui.
    ESCALATES = """
    AGENT Escalates {
        GOAL g { ACHIEVE contained == yes }
        TOOL isolate { RISK LOW EFFECT { contained = yes } COST 1 }
        TOOL warn    { RISK LOW }
        POLICY { ALLOW isolate  ALLOW warn  NEVER isolate WHEN asset == CRITICAL }
        PLAN escalate_to_human { STEP s { warn() } }
        DECIDE { RULES { IF planner.exhausted THEN escalate_to_human } }
        PLANNER { ACHIEVE contained == yes }
    }"""

    def test_default_search_reaches_its_fixpoint(self):
        """La chaîne de 5 opérateurs de la classe précédente est prouvée sans
        qu'on ait à choisir une profondeur."""
        t = theorem(verify(agent(TestReachabilityBoundedness.CHAIN)), "T2")
        self.assertTrue(t.holds)
        self.assertIn("point fixe", t.summary)

    def test_no_route_and_no_escalation_is_refuted(self):
        t = theorem(verify(agent(self.STALLS)), "T2")
        self.assertIs(t.holds, False)
        errors = [f for f in t.findings if f.code == "V105"
                  and f.severity == "error"]
        self.assertTrue(errors, [f.render() for f in t.findings])

    def test_no_route_but_declared_escalation_is_not_refuted(self):
        """Ce que le théorème interdit, c'est de caler en silence — pas de
        renoncer. Confondre les deux ferait crier le vérificateur sur des
        agents corrects."""
        t = theorem(verify(agent(self.ESCALATES)), "T2")
        self.assertIsNot(t.holds, False)
        self.assertIn("V112", [f.code for f in t.findings])

    def test_a_truncated_search_never_refutes(self):
        """Direction de sûreté : coupée avant son point fixe, la recherche ne
        peut rien réfuter, même sans escalade déclarée.

        Il faut pour cela un espace que la coupure atteint vraiment : sur
        `STALLS`, retirer l'unique opérateur vide la frontière au premier
        niveau — c'est un point fixe immédiat, donc une preuve légitime. La
        chaîne de cinq opérateurs, elle, se laisse tronquer.
        """
        from agentl.verifier import Verifier
        src = TestReachabilityBoundedness.CHAIN.replace(
            "POLICY { ALLOW a1", "POLICY { NEVER a5 ALLOW a1")
        t = theorem(Verifier(agent(src), depth=2).run(), "T2")
        self.assertIsNot(t.holds, False)
        self.assertTrue(all(f.severity != "error" for f in t.findings),
                        [f.render() for f in t.findings])

    def test_the_node_budget_also_stops_short_of_refuting(self):
        from agentl.verifier import Verifier
        report = Verifier(agent(self.STALLS), max_nodes=0).run()
        t = theorem(report, "T2")
        self.assertIsNot(t.holds, False)


# --------------------------------------------------------------------------
# 5. Solveur — jamais insatisfiable à tort (direction catastrophique)
# --------------------------------------------------------------------------
class TestSolverConservativeDirection(unittest.TestCase):
    def _mk(self, var, op, val):
        return BinOp(op, PathExpr([var]), Literal(val))

    def _concrete(self, op, a, b):
        return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b,
                ">": a > b, ">=": a >= b}[op]

    def test_never_declares_a_satisfiable_conjunction_unsatisfiable(self):
        import random
        ops = ["==", "!=", "<", "<=", ">", ">="]
        random.seed(20260726)
        false_unsat = 0
        for _ in range(4000):
            specs = [(random.choice(["x", "y"]), random.choice(ops),
                      random.randint(0, 5)) for _ in range(random.randint(1, 4))]
            conds = [self._mk(v, op, val) for v, op, val in specs]
            solver_sat = satisfiable(*conds)
            real_sat = any(
                all(self._concrete(op, {"x": xv, "y": yv}[v], val)
                    for v, op, val in specs)
                for xv in range(6) for yv in range(6))
            if real_sat and not solver_sat:
                false_unsat += 1
        self.assertEqual(false_unsat, 0)

    def test_known_contradictions_are_caught(self):
        # Le vérificateur s'appuie sur ces détections pour blanchir un chemin.
        self.assertFalse(satisfiable(self._mk("x", "==", 1),
                                     self._mk("x", "==", 2)))
        self.assertFalse(satisfiable(self._mk("x", ">", 5),
                                     self._mk("x", "<", 3)))

    def test_disjunction_is_not_falsely_contradictory(self):
        # x < 3 OR x > 5, avec x == 4 : reste satisfiable (la branche est
        # librement supposée). Un faux « insatisfiable » masquerait un chemin.
        guard = BinOp("OR", self._mk("x", "<", 3), self._mk("x", ">", 5))
        self.assertTrue(satisfiable(guard))


if __name__ == "__main__":
    unittest.main()

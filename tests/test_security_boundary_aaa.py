"""Tests AAA des TROIS FAILLES du moteur de politiques (audit de 2026-08-02).

Toutes trois portaient sur la même promesse — « aucune action ne touche un
outil sans être passée par le moteur de politiques, et une garde indécidable
échoue fermé » — et toutes trois la contredisaient en silence : la trace
montrait un appel autorisé, le certificat restait intact, et il était vide.

  1. **Garde sur donnée absente.** `on_error=True` ne couvrait que les
     exceptions. Une comparaison contre une valeur indéfinie ne lève pas :
     elle rend `False`. Le cas le plus fréquent — capteur en panne, chemin mal
     orthographié, observation pas encore faite — passait donc à travers
     l'intention, et l'interdit ne s'appliquait pas.
  2. **`DELEGATE` hors politique.** Le sous-agent était appelé directement
     depuis `host.subagents` : ni contrôle, ni risque, ni approbation. Sous
     `DEFAULT DENY`, il s'exécutait quand même.
  3. **Charge utile masquante.** Les payloads étaient liés en noms nus parmi
     les `locals`, prioritaires sur le monde et les croyances. Une donnée
     non fiable venue de l'hôte pouvait donc **désactiver un `NEVER`**.

Chaque module porte ici un **test de mutation** : on rétablit l'ancien
comportement et on vérifie que l'exploit revient. Un test de sécurité qui
passerait aussi bien sans le correctif ne prouve rien.

    python -m pytest tests/test_security_boundary_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.core import UNDEFINED, Symbol
from agentl.host import Host
from agentl.parser import parse_source
from agentl.policy import (ALLOWED, APPROVAL_REQUIRED, DENIED, PolicyEngine,
                           action_from_tool)
from agentl.runtime import Runtime
from agentl.state import Evaluator, State
from agentl.trivalent import UNKNOWN, applies_when_unknown, evaluate


def _agent(source: str):
    return parse_source(source, "<test>").agents[0]


def _decide(source: str, world: dict, tool: str = "restart"):
    agent = _agent(source)
    state = State()
    for path, value in world.items():
        state.set_world(path, value)
    request = action_from_tool(agent.tool(tool), tool, {}, "plan")
    return PolicyEngine(agent).check(request, state)


NEVER_ON_MISSING = """
AGENT t {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL restart { INPUT { } RISK { operational = HIGH } }
    POLICY {
        DEFAULT ALLOW
        NEVER restart WHEN maintenance.window == open
    }
}
"""


# --------------------------------------------------------------------------
# M1 — faille 1 : la garde indéterminée
# --------------------------------------------------------------------------
class TestGuardsFailClosed(unittest.TestCase):

    def test_a_never_applies_when_its_data_is_missing(self):
        """La propriété centrale : on ne peut pas prouver la condition
        dangereuse fausse, donc on bloque."""
        decision = _decide(NEVER_ON_MISSING, {})

        self.assertEqual(decision.verdict, DENIED)

    def test_a_never_still_applies_when_its_data_is_true(self):
        self.assertEqual(
            _decide(NEVER_ON_MISSING, {"maintenance.window": "open"}).verdict,
            DENIED)

    def test_a_never_does_not_apply_when_proven_false(self):
        """Fermé n'est pas bloqué : un interdit prouvé faux laisse passer,
        sinon l'outil ne sert qu'à débrancher l'agent."""
        self.assertEqual(
            _decide(NEVER_ON_MISSING, {"maintenance.window": "closed"}).verdict,
            ALLOWED)

    def test_an_allow_is_not_satisfied_by_an_undetermined_guard(self):
        source = """
AGENT t {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL restart { INPUT { } RISK { operational = HIGH } }
    POLICY { DEFAULT DENY  ALLOW restart IF service.env == staging }
}
"""
        self.assertEqual(_decide(source, {}).verdict, DENIED)
        self.assertEqual(_decide(source, {"service.env": "staging"}).verdict,
                         ALLOWED)

    def test_an_undetermined_approval_routes_to_the_human(self):
        source = """
AGENT t {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL restart { INPUT { } RISK { operational = HIGH } }
    POLICY {
        DEFAULT ALLOW
        REQUIRE APPROVAL FOR restart WHEN service.env == production
    }
}
"""
        self.assertEqual(_decide(source, {}).verdict, APPROVAL_REQUIRED)

    def test_the_mutation_restores_the_hole(self):
        """Sans la logique trivalente — indéfini traité comme faux — l'exploit
        revient. C'est ce que fait exactement l'ancien code."""
        agent = _agent(NEVER_ON_MISSING)
        rule = next(r for r in agent.policies if r.effect == "NEVER")
        evaluator = Evaluator(State())

        ancien = evaluator.test(rule.guard)          # sémantique d'avant
        nouveau = evaluate(evaluator, rule.guard)    # sémantique d'après

        self.assertFalse(ancien)                     # ← l'interdit ne tirait pas
        self.assertIs(nouveau, UNKNOWN)              # ← il tire désormais


# --------------------------------------------------------------------------
# M2 — la logique de Kleene elle-même
# --------------------------------------------------------------------------
class TestKleeneLogic(unittest.TestCase):

    def _truth(self, expression: str, world: dict):
        source = f"""
AGENT t {{
    VERSION "1.0"
    GOAL g {{ ACHIEVE done == yes }}
    TOOL x {{ INPUT {{ }} }}
    POLICY {{ DEFAULT ALLOW  NEVER x WHEN {expression} }}
}}
"""
        state = State()
        for path, value in world.items():
            state.set_world(path, value)
        rule = next(r for r in _agent(source).policies if r.effect == "NEVER")
        return evaluate(Evaluator(state), rule.guard)

    def test_a_single_false_settles_a_conjunction(self):
        """`UNKNOWN ∧ FALSE = FALSE` : inutile de connaître l'autre membre."""
        self.assertIs(self._truth("s.a == 1 AND s.b == 2", {"s.b": 3}), False)

    def test_a_conjunction_with_an_unknown_stays_unknown(self):
        self.assertIs(self._truth("s.a == 1 AND s.b == 2", {"s.b": 2}), UNKNOWN)

    def test_a_single_true_settles_a_disjunction(self):
        """Sans Kleene, toute garde touchant un chemin absent deviendrait une
        interdiction — et un outil qui bloque tout est débranché, pas sûr."""
        self.assertIs(self._truth("s.a == 1 OR s.b == 2", {"s.b": 2}), True)

    def test_a_disjunction_with_an_unknown_stays_unknown(self):
        self.assertIs(self._truth("s.a == 1 OR s.b == 2", {"s.b": 3}), UNKNOWN)

    def test_negating_an_unknown_stays_unknown(self):
        self.assertIs(self._truth("NOT s.a == 1", {}), UNKNOWN)

    def test_a_dotted_path_used_as_a_boolean_is_unknown_when_absent(self):
        """Même piège, autre forme syntaxique."""
        self.assertIs(self._truth("run.dry", {}), UNKNOWN)
        self.assertIs(self._truth("run.dry", {"run.dry": True}), True)

    def test_a_bare_identifier_is_a_symbolic_constant_not_an_unknown(self):
        """Limite assumée, et la raison d'être de `W128`. Un identifiant nu
        non résolu **est** une constante symbolique — c'est ainsi que `open`,
        `yes` et `contained` sont des valeurs. La trivalence ne peut donc rien
        pour `WHEN dry_run == no` : l'évaluateur n'a pas rendu « indéfini », il
        a rendu une constante. L'analyseur, lui, sait le voir."""
        self.assertIs(self._truth("dry_run == no", {}), False)

    def test_the_analyzer_flags_what_the_evaluator_cannot(self):
        from agentl.analyzer import Analyzer

        agent = _agent("""
AGENT t {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL wipe { INPUT { } }
    POLICY { DEFAULT ALLOW  NEVER wipe WHEN dry_run == no }
}
""")

        self.assertIn("W128", [d.code for d in Analyzer(agent).run()])

    def test_a_declared_name_raises_no_warning(self):
        """Une alarme qui crie sur les programmes corrects ne se lit plus."""
        from agentl.analyzer import Analyzer

        agent = _agent("""
AGENT t {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    OBSERVE { dry_run }
    TOOL wipe { INPUT { } }
    POLICY { DEFAULT ALLOW  NEVER wipe WHEN dry_run == no }
}
""")

        self.assertNotIn("W128", [d.code for d in Analyzer(agent).run()])

    def test_a_rule_without_a_guard_always_applies(self):
        """Fait de grammaire, pas indétermination."""
        self.assertIs(evaluate(Evaluator(State()), None), True)

    def test_unknown_refuses_to_be_a_boolean(self):
        """Le convertir en booléen est exactement l'erreur corrigée : mieux
        vaut une exception qu'un `False` silencieux."""
        with self.assertRaises(TypeError):
            bool(UNKNOWN)

    def test_the_safe_direction_depends_on_the_effect(self):
        self.assertTrue(applies_when_unknown("NEVER"))
        self.assertTrue(applies_when_unknown("DENY"))
        self.assertTrue(applies_when_unknown("REQUIRE_APPROVAL"))
        self.assertFalse(applies_when_unknown("ALLOW"))


# --------------------------------------------------------------------------
# M3 — faille 2 : DELEGATE traverse le moteur de politiques
# --------------------------------------------------------------------------
DELEGATING = """
AGENT t {{
    VERSION "1.0"
    GOAL g {{ ACHIEVE done == yes }}
    POLICY {{ {policy} }}
    PLAN p WHEN go == 1 {{
        STEP s {{ DELEGATE wiper {{ TASK "n'importe quoi" EXPECT {{ ok }} }} }}
    }}
}}
"""


def _delegate(policy: str, approve: bool = False):
    """Rend (le sous-agent a-t-il tourné, runtime)."""
    agent = _agent(DELEGATING.format(policy=policy))
    host = Host()
    ran = []

    def subagent(payload):
        ran.append(payload)                          # rm -rf, réseau, base…
        return {"ok": Symbol("yes")}

    host.subagents["wiper"] = subagent
    host.approver = lambda request: approve
    runtime = Runtime(agent, host)
    runtime.state.set_world("go", 1)
    runtime.tick()
    return bool(ran), runtime


class TestDelegateGoesThroughPolicy(unittest.TestCase):

    def test_default_deny_blocks_the_subagent(self):
        """Le cas de l'audit : le moteur de politiques n'était pas sur le
        chemin de `DELEGATE`."""
        ran, _ = _delegate("DEFAULT DENY")

        self.assertFalse(ran)

    def test_an_explicit_allow_lets_it_through(self):
        """Fermé n'est pas bloqué : autoriser doit rester possible."""
        ran, _ = _delegate("DEFAULT DENY  ALLOW wiper")

        self.assertTrue(ran)

    def test_a_never_is_irrevocable_for_a_subagent_too(self):
        ran, _ = _delegate("DEFAULT ALLOW  NEVER wiper")

        self.assertFalse(ran)

    def test_an_approval_is_required_and_fails_closed(self):
        refused, _ = _delegate(
            "DEFAULT ALLOW  REQUIRE APPROVAL FOR wiper", approve=False)
        accepted, _ = _delegate(
            "DEFAULT ALLOW  REQUIRE APPROVAL FOR wiper", approve=True)

        self.assertFalse(refused)
        self.assertTrue(accepted)

    def test_a_blocked_delegation_is_counted_and_traced(self):
        """Un refus muet serait indiscernable d'une panne."""
        _, runtime = _delegate("DEFAULT DENY")

        self.assertEqual(runtime.metrics["blocked"], 1)
        self.assertIn("DELEGATE wiper refusé", runtime.trace.render())

    def test_an_undeclared_subagent_is_assumed_critical(self):
        """Sans contrat, on ne devine pas l'innocuité d'une fonction Python."""
        agent = _agent(DELEGATING.format(policy="DEFAULT ALLOW"))
        self.assertIsNone(agent.tool("wiper"))
        self.assertEqual(Runtime.UNDECLARED_DELEGATE_RISK, "CRITICAL")

    def test_a_risk_guard_sees_the_assumed_risk(self):
        ran, _ = _delegate("DEFAULT ALLOW  NEVER wiper WHEN action.risk >= HIGH")

        self.assertFalse(ran)

    def test_the_analyzer_asks_for_a_contract(self):
        from agentl.analyzer import Analyzer

        codes = [d.code for d in
                 Analyzer(_agent(DELEGATING.format(policy="DEFAULT ALLOW"))).run()]

        self.assertIn("W127", codes)

    def test_a_policy_may_target_a_subagent_by_name(self):
        """Refuser `NEVER wiper` interdirait d'écrire la garde qui protège
        précisément ce chemin."""
        from agentl.analyzer import Analyzer

        codes = [d.code for d in
                 Analyzer(_agent(DELEGATING.format(
                     policy="DEFAULT ALLOW  NEVER wiper"))).run()]

        self.assertNotIn("E003", codes)


# --------------------------------------------------------------------------
# M4 — faille 3 : une charge utile ne masque rien
# --------------------------------------------------------------------------
SHADOWING = """
AGENT u {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    OBSERVE { asset.criticality }
    TOOL wipe { INPUT { } RISK { operational = HIGH } }
    POLICY { DEFAULT ALLOW  NEVER wipe WHEN asset.criticality == CRITICAL }
    EVENT feed.alert { THEN act }
    PLAN act { STEP s { wipe() } }
}
"""


class TestUntrustedPayloadsCannotShadow(unittest.TestCase):

    def _run(self, payload: dict, criticality: str = "CRITICAL"):
        agent = _agent(SHADOWING)
        host = Host()
        host.sensors["asset.criticality"] = lambda: criticality
        calls = []
        host.tools["wipe"] = lambda **kw: calls.append(kw)
        runtime = Runtime(agent, host)
        host.emit("feed.alert", **payload)
        runtime.tick()
        return bool(calls), runtime

    def test_a_payload_cannot_disable_a_never(self):
        """Le pire des trois : une donnée non fiable décidait d'une question
        de sécurité."""
        called, _ = self._run({"asset": {"criticality": "LOW"}})

        self.assertFalse(called)

    def test_a_flat_payload_key_cannot_shadow_either(self):
        called, _ = self._run({"asset.criticality": "LOW"})

        self.assertFalse(called)

    def test_the_interdiction_still_lifts_when_the_world_really_is_low(self):
        """Fermé n'est pas bloqué."""
        called, _ = self._run({}, criticality="LOW")

        self.assertTrue(called)

    def test_a_payload_still_fills_what_nothing_else_provides(self):
        """La correction ne doit pas rendre les charges utiles inutilisables :
        elles comblent, elles ne masquent plus."""
        agent = _agent("""
AGENT v {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    EVENT feed.alert { WHEN ticket_id != none THEN act }
    PLAN act { STEP s { SET seen = ticket_id } }
}
""")
        host = Host()
        runtime = Runtime(agent, host)
        host.emit("feed.alert", ticket_id="T-42")

        runtime.tick()

        self.assertEqual(runtime.state.get("seen"), "T-42")

    def test_the_prefixed_form_stays_available(self):
        """La provenance reste lisible dans le programme."""
        agent = _agent("""
AGENT v {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    EVENT feed.alert { THEN act }
    PLAN act { STEP s { SET seen = payload.ticket_id } }
}
""")
        host = Host()
        runtime = Runtime(agent, host)
        host.emit("feed.alert", ticket_id="T-42")

        runtime.tick()

        self.assertEqual(runtime.state.get("seen"), "T-42")

    def test_the_untrusted_namespace_is_consulted_last(self):
        state = State()
        state.set_world("x", "monde")
        state.untrusted["x"] = "payload"

        self.assertEqual(state.get("x"), "monde")

    def test_it_is_consulted_at_all(self):
        state = State()
        state.untrusted["x"] = "payload"

        self.assertEqual(state.get("x"), "payload")

    def test_a_belief_is_not_shadowed_either(self):
        state = State()
        state.set_belief("threat.status", Symbol("contained"))
        state.untrusted["threat.status"] = Symbol("unknown")

        self.assertEqual(state.get("threat.status"), Symbol("contained"))

    def test_the_mutation_restores_the_hole(self):
        """Lié parmi les locales — l'ancien comportement — le payload masque."""
        state = State()
        state.set_world("x", "monde")
        state.locals["x"] = "payload"

        self.assertEqual(state.get("x"), "payload")

    def test_a_rejected_event_leaves_nothing_behind(self):
        agent = _agent("""
AGENT v {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    EVENT feed.alert { WHEN severity >= CRITICAL THEN act }
    PLAN act { STEP s { SET seen = yes } }
}
""")
        host = Host()
        runtime = Runtime(agent, host)
        host.emit("feed.alert", severity=Symbol("LOW"), leftover="résidu")

        runtime.tick()

        self.assertIs(runtime.state.get("leftover"), UNDEFINED)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()

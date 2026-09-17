"""Suite de tests d'AGENT-L :  python -m unittest discover tests"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import (Analyzer, Host, MockLLM, ParseError, Planner, Runtime,
                    Society, Symbol, entails, infer, parse_file, parse_source,
                    satisfiable, verify)
from agentl.lexer import tokenize
from agentl.policy import ActionRequest, PolicyEngine
from agentl.state import Evaluator, State

SOC = ROOT / "examples" / "soc_analyst.agent"


def load(path) -> object:
    return parse_file(str(path)).agents[0]


# --------------------------------------------------------------------- lexer
class TestLexer(unittest.TestCase):
    def test_percent_is_a_fraction(self):
        self.assertAlmostEqual(tokenize("99.9%")[0].value, 0.999)

    def test_durations_normalise_to_seconds(self):
        kinds = [(t.kind, t.value) for t in tokenize("10s 5min 2h")[:3]]
        self.assertEqual(kinds, [("DUR", 10.0), ("DUR", 300.0), ("DUR", 7200.0)])

    def test_lowercase_is_never_a_keyword(self):
        self.assertEqual(tokenize("goal healthy none")[0].kind, "IDENT")
        self.assertEqual(tokenize("GOAL")[0].kind, "KW")

    def test_utf8_survives_string_literals(self):
        self.assertEqual(tokenize('"hôte confiné"')[0].value, "hôte confiné")


# -------------------------------------------------------------------- parser
class TestParser(unittest.TestCase):
    def test_soc_example_parses(self):
        agent = load(SOC)
        self.assertEqual(agent.name, "SOC_ANALYST")
        self.assertEqual(len(agent.tools), 7)
        self.assertEqual(len(agent.plans), 2)
        self.assertEqual([g.name for g in agent.goals], ["containment"])

    def test_study_shorthand_is_accepted(self):
        """Formes abrégées de l'étude : TOOL { f() } et STEP sans accolades."""
        src = """
        AGENT Legacy {
            TOOL { inspect_logs() restart_service(service: String) }
            PLAN diagnose {
                STEP 1: inspect_logs()
                STEP 2: restart_service("api")
            }
        }
        """
        agent = parse_source(src).agents[0]
        self.assertEqual([t.name for t in agent.tools],
                         ["inspect_logs", "restart_service"])
        self.assertEqual([s.name for s in agent.plans[0].steps], ["1", "2"])

    def test_risk_block_and_side_effects(self):
        agent = load(SOC)
        tool = agent.tool("isolate_endpoint")
        self.assertEqual(tool.risk, "HIGH")
        self.assertEqual(tool.side_effects,
                         ["network.acl", "endpoint.connectivity"])

    def test_syntax_error_reports_a_line(self):
        with self.assertRaises(ParseError) as ctx:
            parse_source("AGENT X { GOAL g { MAINTAIN } }")
        self.assertIn(":1", str(ctx.exception))


# ----------------------------------------------------------------- évaluateur
class TestEvaluator(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.state.set_world("service.latency", 320)
        self.state.set_belief("incident.severity", Symbol("HIGH"), 0.8)

    def eval(self, src: str):
        from agentl.parser import Parser
        return Evaluator(self.state).eval(Parser(tokenize(src)).expression())

    def test_ordinal_scale(self):
        self.assertTrue(self.eval("incident.severity >= MEDIUM"))
        self.assertFalse(self.eval("incident.severity >= CRITICAL"))

    def test_bare_identifier_becomes_a_symbol(self):
        self.assertEqual(self.eval("healthy"), Symbol("healthy"))

    def test_undefined_comparison_is_false_not_true(self):
        self.assertFalse(self.eval("nowhere.at.all > 3"))

    def test_belief_confidence_is_readable(self):
        self.assertAlmostEqual(self.eval("incident.severity.confidence"), 0.8)


# ------------------------------------------------------------------ politique
class TestPolicyEngine(unittest.TestCase):
    def setUp(self):
        self.agent = load(SOC)
        self.engine = PolicyEngine(self.agent)
        self.state = State()

    def request(self, posterior=0.93):
        self.state.set_world("credential_attack.posterior", posterior)
        return ActionRequest("isolate_endpoint", {"host": "PC-042"},
                             risk="HIGH", origin="llm")

    def test_never_is_irrevocable_even_at_full_confidence(self):
        self.state.set_world("asset.criticality", Symbol("CRITICAL"))
        decision = self.engine.check(self.request(0.999), self.state)
        self.assertEqual(decision.verdict, "DENIED")
        self.assertIn("NEVER", decision.reason)

    def test_low_posterior_fails_the_allow_guard(self):
        self.state.set_world("asset.criticality", Symbol("MEDIUM"))
        self.assertEqual(self.engine.check(self.request(0.40), self.state).verdict,
                         "DENIED")

    def test_medium_posterior_requires_approval(self):
        self.state.set_world("asset.criticality", Symbol("MEDIUM"))
        self.assertEqual(self.engine.check(self.request(0.93), self.state).verdict,
                         "APPROVAL_REQUIRED")

    def test_default_deny_blocks_undeclared_capability(self):
        decision = self.engine.check(
            ActionRequest("send_email", {}, risk="LOW"), self.state)
        self.assertEqual(decision.verdict, "DENIED")


# -------------------------------------------------------------------- runtime
class TestRuntime(unittest.TestCase):
    def run_scenario(self, ticks=3, **kwargs):
        from soc_analyst import build
        agent = load(SOC)
        host, llm, world = build(**kwargs)
        runtime = Runtime(agent, host, llm).run(max_ticks=ticks)
        return runtime, world

    def test_nominal_containment(self):
        runtime, world = self.run_scenario()
        self.assertTrue(world.contained)
        self.assertEqual(runtime.metrics["blocked"], 0)
        self.assertEqual(runtime.metrics["verify_fail"], 0)

    def test_critical_asset_is_never_isolated(self):
        runtime, world = self.run_scenario(criticality="CRITICAL",
                                           account_known=False)
        self.assertIsNone(world.action)
        pruning = [e for e in runtime.trace.of_kind("PLANNER")
                   if "NEVER" in e.detail]
        self.assertTrue(pruning)

    def test_refused_approval_stops_the_action(self):
        runtime, world = self.run_scenario(account_known=False,
                                           operator_approves=False)
        self.assertIsNone(world.action)
        self.assertGreater(runtime.metrics["blocked"], 0)

    def test_llm_cannot_invent_a_plan(self):
        src = """
        AGENT Fenced {
            TOOL ping { OUTPUT { ok: Symbol } RISK LOW }
            PLAN real { STEP s { ping() } }
            DECIDE { REASON { TASK "choisir" PRODUCE { choice: String } } }
        }
        """
        agent = parse_source(src).agents[0]
        host = Host()
        host.tools["ping"] = lambda: {"ok": Symbol("yes")}
        llm = MockLLM(plan_choice=lambda ctx, cands: "rm_minus_rf")
        runtime = Runtime(agent, host, llm).run(max_ticks=1)
        blocked = [e for e in runtime.trace.of_kind("BLOCKED")
                   if "rm_minus_rf" in e.text]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(runtime.metrics["tool_calls"], 0)

    def test_undeclared_tool_is_blocked_at_runtime(self):
        src = """
        AGENT Sneaky {
            TOOL ping { OUTPUT { ok: Symbol } RISK LOW }
            PLAN p WHEN 1 == 1 { STEP s { exfiltrate() } }
        }
        """
        agent = parse_source(src).agents[0]
        runtime = Runtime(agent, Host(), MockLLM()).run(max_ticks=1)
        self.assertEqual(runtime.metrics["blocked"], 1)

    def test_input_contract_is_enforced(self):
        src = """
        AGENT Typed {
            TOOL scale { INPUT { factor: Number } OUTPUT { done: Symbol } RISK LOW }
            PLAN p WHEN 1 == 1 { STEP s { scale("beaucoup") } }
        }
        """
        agent = parse_source(src).agents[0]
        host = Host()
        host.tools["scale"] = lambda factor: {"done": Symbol("yes")}
        runtime = Runtime(agent, host, MockLLM()).run(max_ticks=1)
        self.assertEqual(runtime.metrics["tool_calls"], 0)
        self.assertEqual(runtime.metrics["blocked"], 1)

    def test_memory_write_is_deduplicated(self):
        runtime, _ = self.run_scenario()
        records = runtime.state.memory["LONG_TERM"]["incidents"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["suspected_host"], "PC-042")

    def test_verify_reperceives_the_world_after_acting(self):
        """Un plan qui répare puis vérifie doit voir l'effet de sa réparation."""
        from maintenance import build
        agent = load(ROOT / "examples" / "maintenance.agent")
        host, llm, svc = build(approve=True)
        runtime = Runtime(agent, host, llm).run(max_ticks=3)
        self.assertTrue(svc.healthy)
        self.assertEqual(runtime.metrics["verify_fail"], 0)
        self.assertEqual(runtime.metrics["verify_pass"], 1)

    def test_delegate_honours_the_expect_contract(self):
        from maintenance import build
        agent = load(ROOT / "examples" / "maintenance.agent")
        host, llm, _ = build(approve=True)
        runtime = Runtime(agent, host, llm).run(max_ticks=1)
        self.assertEqual(runtime.state.get("db_agent.db_healthy"),
                         Symbol("yes"))

    def test_production_restart_requires_approval(self):
        from maintenance import build
        agent = load(ROOT / "examples" / "maintenance.agent")
        host, llm, svc = build(approve=False)
        runtime = Runtime(agent, host, llm).run(max_ticks=1)
        self.assertFalse(svc.healthy)
        self.assertGreater(runtime.metrics["blocked"], 0)


# ---------------------------------------------------- v0.4 : inférence bayésienne
class TestBayes(unittest.TestCase):
    def hypothesis(self, name="h"):
        agent = load(SOC)
        return next(h for h in agent.hypotheses if h.name == name)

    def state_with(self, alerts, anomaly, integrity):
        state = State()
        state.set_world("wazuh.alert_count", alerts)
        state.set_world("network.anomaly_score", anomaly)
        state.set_world("endpoint.integrity", Symbol(integrity))
        return state

    def test_evidence_raises_the_posterior_above_the_prior(self):
        result = infer(self.hypothesis("credential_attack"),
                       self.state_with(37, 0.91, "compromised"))
        self.assertAlmostEqual(result.prior, 0.05)
        self.assertGreater(result.posterior, 0.90)
        self.assertTrue(result.supported)

    def test_contrary_evidence_collapses_the_posterior(self):
        result = infer(self.hypothesis("credential_attack"),
                       self.state_with(3, 0.10, "clean"))
        self.assertLess(result.posterior, 0.01)
        self.assertFalse(result.supported)

    def test_missing_evidence_is_indeterminate_not_false(self):
        """Ignorer une évidence ≠ l'observer fausse : c'est tout l'enjeu."""
        partial = State()
        partial.set_world("wazuh.alert_count", 37)      # les deux autres manquent
        observed_false = self.state_with(37, 0.10, "clean")
        hypothesis = self.hypothesis("credential_attack")
        self.assertGreater(infer(hypothesis, partial).posterior,
                           infer(hypothesis, observed_false).posterior)
        outcomes = infer(hypothesis, partial).outcomes
        self.assertEqual(sum(1 for o in outcomes if o.observed is None), 2)

    def test_indeterminate_evidence_has_zero_weight(self):
        outcomes = infer(self.hypothesis("credential_attack"), State()).outcomes
        self.assertTrue(all(o.weight_bits == 0.0 for o in outcomes))
        self.assertAlmostEqual(
            infer(self.hypothesis("credential_attack"), State()).posterior, 0.05)

    def test_competing_hypotheses_diverge(self):
        state = self.state_with(37, 0.91, "compromised")
        attack = infer(self.hypothesis("credential_attack"), state).posterior
        benign = infer(self.hypothesis("benign_scan"), state).posterior
        self.assertGreater(attack, 0.9)
        self.assertLess(benign, 0.2)

    def test_posterior_feeds_policy_guards(self):
        """`ALLOW … IF P(credential_attack) >= 0.95` doit être exécutable."""
        from agentl.policy import ActionRequest, PolicyEngine
        agent = load(SOC)
        state = State()
        state.set_world("asset.criticality", Symbol("MEDIUM"))
        state.set_world("credential_attack.posterior", 0.40)
        request = ActionRequest("isolate_endpoint", {"host": "PC-042"},
                                risk="HIGH", origin="llm")
        engine = PolicyEngine(agent)
        self.assertEqual(engine.check(request, state).verdict, "DENIED")
        state.set_world("credential_attack.posterior", 0.999)
        self.assertEqual(engine.check(request, state).verdict, "APPROVAL_REQUIRED")

    def test_derived_confidence_replaces_declared_confidence(self):
        from soc_analyst import build
        agent = load(SOC)
        host, llm, _ = build()
        runtime = Runtime(agent, host, llm).run(max_ticks=1)
        belief = runtime.state.beliefs["threat.kind"]
        self.assertEqual(belief.value, Symbol("credential_attack"))
        self.assertGreater(belief.confidence, 0.90)
        self.assertTrue(belief.source.startswith("hypothesis:"))


# ------------------------------------------------- v0.5 : planificateur
class TestPlanner(unittest.TestCase):
    def prepared(self, **kwargs):
        """Un runtime après un tick : les entités sont identifiées."""
        from soc_analyst import build
        agent = load(SOC)
        host, llm, world = build(**kwargs)
        runtime = Runtime(agent, host, llm)
        runtime.run(max_ticks=1)
        return agent, runtime, world

    def test_planner_prefers_the_cheaper_less_risky_route(self):
        agent, runtime, _ = self.prepared()
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertTrue(result.found)
        self.assertEqual([a.tool for a in result.actions],
                         ["inspect_endpoint", "quarantine_account"])
        self.assertEqual(result.cost, 6)

    def test_never_keeps_the_action_out_of_every_branch(self):
        agent, runtime, _ = self.prepared(criticality="CRITICAL")
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertTrue(result.found)
        self.assertNotIn("isolate_endpoint", [a.tool for a in result.actions])

    def test_never_is_reported_when_the_search_reaches_it(self):
        agent, runtime, _ = self.prepared(criticality="CRITICAL",
                                          account_known=False)
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertTrue(any("NEVER" in note for note in result.pruned_by_policy))

    def test_approval_is_a_cost_not_a_wall(self):
        agent, runtime, _ = self.prepared(account_known=False)
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertEqual([a.tool for a in result.actions],
                         ["inspect_endpoint", "isolate_endpoint"])
        self.assertTrue(result.actions[-1].needs_approval)
        self.assertEqual(result.cost, 20)      # 2 + 8 + APPROVAL_COST 10

    def test_no_permitted_route_yields_no_plan(self):
        agent, runtime, _ = self.prepared(criticality="CRITICAL",
                                          account_known=False)
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertFalse(result.found)

    def test_preconditions_are_respected_in_order(self):
        """`quarantine_account` REQUIRES l'inspection : elle ne peut pas être
        la première action du plan."""
        agent, runtime, _ = self.prepared()
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertEqual(result.actions[0].tool, "inspect_endpoint")

    def test_unbindable_operator_is_skipped(self):
        agent, runtime, _ = self.prepared(account_known=False)
        planner = Planner(agent, agent.planner, runtime.policy)
        tool = agent.tool("quarantine_account")
        from agentl.planner import _Overlay
        self.assertIsNotNone(planner._bind(tool, _Overlay(runtime.state)))

    def test_synthesized_plan_ends_with_a_verification(self):
        agent, runtime, _ = self.prepared()
        planner = Planner(agent, agent.planner, runtime.policy)
        plan = planner.as_plan(planner.synthesize(runtime.state), "p")
        from agentl.nodes import VerifyStmt
        self.assertIsInstance(plan.steps[-1].body[0], VerifyStmt)

    def test_end_to_end_containment_via_synthesis(self):
        from soc_analyst import build
        agent = load(SOC)
        host, llm, world = build()
        runtime = Runtime(agent, host, llm).run(max_ticks=3)
        self.assertEqual(world.action, "quarantine_account")
        self.assertEqual(runtime.metrics["plans_synthesized"], 1)
        self.assertEqual(runtime.metrics["verify_fail"], 0)

    def test_agent_abstains_when_the_signal_is_weak(self):
        from soc_analyst import build
        agent = load(SOC)
        host, llm, world = build(alerts=4, anomaly=0.20, integrity="clean")
        runtime = Runtime(agent, host, llm).run(max_ticks=3)
        self.assertIsNone(world.action)
        self.assertEqual(runtime.metrics["tool_calls"], 0)

    def test_dead_end_escalates_exactly_once(self):
        from soc_analyst import build
        agent = load(SOC)
        host, llm, world = build(criticality="CRITICAL", account_known=False)
        runtime = Runtime(agent, host, llm).run(max_ticks=4)
        self.assertIsNone(world.action)
        tickets = [e for e in runtime.trace.of_kind("TOOL")
                   if "create_ticket" in e.text]
        self.assertEqual(len(tickets), 1)


# ------------------------------------------- v1.1 : calibration et bornes
class TestCalibration(unittest.TestCase):
    SRC = """
    AGENT Calib {{
        HYPOTHESIS h {{
            PRIOR 0.05
            {extra}
            EVIDENCE {{
                {evidence}
                endpoint.integrity == compromised LIKELIHOOD 0.80 GIVEN_NOT 0.05
            }}
            THRESHOLD 0.95
        }}
    }}
    """
    PAIR = ("wazuh.alert_count > 20        LIKELIHOOD 0.92 GIVEN_NOT 0.06\n"
            "network.anomaly_score > 0.75  LIKELIHOOD 0.85 GIVEN_NOT 0.20")

    def build(self, extra="", grouped=False):
        evidence = f"GROUP rafale {{ {self.PAIR} }}" if grouped else self.PAIR
        src = self.SRC.format(extra=extra, evidence=evidence)
        return parse_source(src).agents[0].hypotheses[0]

    def state(self):
        state = State()
        state.set_world("wazuh.alert_count", 37)
        state.set_world("network.anomaly_score", 0.91)
        state.set_world("endpoint.integrity", Symbol("compromised"))
        return state

    def test_correlated_evidence_no_longer_double_counts(self):
        """Le défaut de v1.0 : deux capteurs de la même rafale comptés comme
        deux témoins indépendants font franchir un seuil d'autorisation."""
        naive = infer(self.build(), self.state())
        grouped = infer(self.build(grouped=True), self.state())
        self.assertGreater(naive.posterior, 0.95)        # autorisait
        self.assertLess(grouped.posterior, 0.95)         # n'autorise plus
        self.assertTrue(naive.supported)
        self.assertFalse(grouped.supported)

    def test_absorbed_evidence_is_visible_in_the_trace(self):
        result = infer(self.build(grouped=True), self.state())
        absorbed = result.absorbed
        self.assertEqual(len(absorbed), 1)
        self.assertIn("absorbé", absorbed[0].render())
        self.assertEqual(absorbed[0].effective_bits, 0.0)

    def test_cap_bounds_unknown_correlation(self):
        capped = infer(self.build(extra="MAX_EVIDENCE 5"), self.state())
        self.assertTrue(capped.capped)
        self.assertAlmostEqual(capped.shift_bits, 5.0)
        self.assertLess(capped.posterior, capped.raw_posterior)

    def test_reachable_range_is_computable_statically(self):
        from agentl.bayes import reachable_range
        low, high = reachable_range(self.build(grouped=True))
        self.assertLess(high, 0.95)
        self.assertGreater(high, 0.90)
        self.assertLess(low, 0.01)
        observed = infer(self.build(grouped=True), self.state()).posterior
        self.assertLessEqual(observed, high + 1e-9)

    def test_group_absorption_is_conservative_not_averaging(self):
        """On garde le plus informatif, on ne moyenne pas : le résultat reste
        au moins aussi fort que la meilleure évidence seule."""
        grouped = infer(self.build(grouped=True), self.state())
        contributions = [o.effective_bits for o in grouped.outcomes]
        self.assertAlmostEqual(max(contributions), 4.0, places=2)


class TestOutputDomains(unittest.TestCase):
    def agent(self, domain=""):
        src = f"""
        AGENT Bound {{
            GOAL g {{ MAINTAIN done == yes }}
            TOOL act {{ INPUT {{ x: Number }} OUTPUT {{ done: Symbol }}
                        RISK HIGH }}
            POLICY {{ DEFAULT DENY  ALLOW act IF confidence >= 0.90 }}
            PLAN p WHEN 1 == 1 {{
                STEP s {{
                    REASON {{ TASK "t"
                              PRODUCE {{ confidence: Number{domain} }} }}
                    act(1)
                }}
            }}
        }}
        """
        return parse_source(src).agents[0]

    def run_with(self, agent, value):
        host = Host()
        host.tools["act"] = lambda x: {"done": Symbol("yes")}
        llm = MockLLM({"t": {"confidence": value}})
        return Runtime(agent, host, llm).run(max_ticks=1)

    def test_unbounded_output_can_force_a_guard(self):
        runtime = self.run_with(self.agent(), 5000)
        self.assertEqual(runtime.metrics["tool_calls"], 1)   # garde franchie

    def test_declared_domain_clamps_the_model(self):
        runtime = self.run_with(self.agent(" IN [0, 1]"), 5000)
        self.assertEqual(runtime.metrics["domain_clamps"], 1)
        self.assertAlmostEqual(runtime.state.get("confidence"), 1.0)

    def test_symbol_domain_rejects_invented_values(self):
        src = """
        AGENT Sym {
            TOOL t { OUTPUT { r: Symbol } RISK LOW }
            POLICY { ALLOW t }
            PLAN p WHEN 1 == 1 { STEP s {
                REASON { TASK "k" PRODUCE { kind: Symbol IN [scan, stuffing] } }
                t() } }
        }
        """
        agent = parse_source(src).agents[0]
        host = Host()
        host.tools["t"] = lambda: {"r": Symbol("ok")}
        llm = MockLLM({"k": {"kind": "exfiltration_totale"}})
        runtime = Runtime(agent, host, llm).run(max_ticks=1)
        self.assertEqual(runtime.metrics["domain_clamps"], 1)
        self.assertEqual(runtime.state.get("kind"), Symbol("stuffing"))

    def test_analyzer_flags_the_unbounded_case(self):
        codes = {d.code for d in Analyzer(self.agent()).run()}
        self.assertIn("W115", codes)
        codes = {d.code for d in Analyzer(self.agent(" IN [0, 1]")).run()}
        self.assertNotIn("W115", codes)


class TestThresholdCoherence(unittest.TestCase):
    def test_unreachable_policy_threshold_is_refuted(self):
        src = """
        AGENT Gap {
            GOAL g { MAINTAIN done == yes }
            HYPOTHESIS h { PRIOR 0.05
                EVIDENCE { a > 1 LIKELIHOOD 0.7 GIVEN_NOT 0.3 }
                THRESHOLD 0.5 }
            TOOL act { OUTPUT { done: Symbol } RISK HIGH }
            POLICY { DEFAULT DENY  ALLOW act IF P(h) >= 0.99 }
            PLAN p WHEN 1 == 1 { STEP s { act() VERIFY done == yes } }
        }
        """
        report = verify(parse_source(src).agents[0])
        codes = [f.code for f in report.findings]
        self.assertIn("V109", codes)

    def test_unreachable_hypothesis_threshold_is_warned(self):
        src = """
        AGENT Gap2 {
            GOAL g { MAINTAIN a == b }
            HYPOTHESIS h { PRIOR 0.30
                EVIDENCE { a > 1 LIKELIHOOD 0.6 GIVEN_NOT 0.3 }
                THRESHOLD 0.95 EXPLAINS k }
        }
        """
        codes = {d.code for d in Analyzer(parse_source(src).agents[0]).run()}
        self.assertIn("W116", codes)

    def test_reference_example_thresholds_are_coherent(self):
        self.assertEqual(verify(load(SOC)).refuted, [])
        self.assertEqual(Analyzer(load(SOC)).run(), [])


class TestMemoryFeedback(unittest.TestCase):
    """v1.2 — la mémoire cesse d'être en écriture seule."""

    def hypothesis(self):
        return next(h for h in load(SOC).hypotheses
                    if h.name == "credential_attack")

    def state_with_history(self, attacks: int, benign: int):
        state = State()
        records = ([{"threat.kind": Symbol("credential_attack")}] * attacks
                   + [{"threat.kind": Symbol("benign_scan")}] * benign)
        state.memory["LONG_TERM"] = {"incidents": records}
        return state

    def test_empty_history_falls_back_to_the_declared_prior(self):
        result = infer(self.hypothesis(), State())
        self.assertAlmostEqual(result.prior, 0.05)
        self.assertEqual(result.prior_origin, "déclaré")

    def test_history_shifts_the_prior(self):
        result = infer(self.hypothesis(), self.state_with_history(8, 2))
        self.assertGreater(result.prior, 0.70)
        self.assertIn("empirique 8/10", result.prior_origin)

    def test_smoothing_avoids_degenerate_priors(self):
        """Un historique unanime ne doit pas produire 0 ou 1."""
        unanimous = infer(self.hypothesis(), self.state_with_history(20, 0))
        never = infer(self.hypothesis(), self.state_with_history(0, 20))
        self.assertLess(unanimous.prior, 1.0)
        self.assertGreater(never.prior, 0.0)

    def test_effect_drift_is_detected(self):
        src = """
        AGENT Drift {
            GOAL g { MAINTAIN state == fixed }
            OBSERVE { state }
            BELIEF { state = broken CONFIDENCE 0.5 SOURCE prior }
            TOOL repair { OUTPUT { ok: Symbol } RISK LOW
                          EFFECT { state = fixed } COST 1 }
            POLICY { ALLOW repair }
            PLANNER { ENABLE ACHIEVE state == fixed }
            LOOP MAX 2 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS
                         SELECT_PLAN EXECUTE VERIFY }
        }
        """
        agent = parse_source(src).agents[0]
        host = Host()
        host.sensors["state"] = lambda: Symbol("broken")   # l'outil ment
        host.tools["repair"] = lambda: {"ok": Symbol("done")}
        runtime = Runtime(agent, host, MockLLM()).run(max_ticks=2)
        self.assertGreater(runtime.metrics["effect_drift"], 0)
        self.assertEqual(runtime.drift["repair→state"]["confirmé"], 0)

    def test_faithful_effect_is_not_flagged(self):
        from soc_analyst import build
        agent = load(SOC)
        host, llm, _ = build()
        runtime = Runtime(agent, host, llm).run(max_ticks=3)
        self.assertEqual(runtime.metrics["effect_drift"], 0)


# ------------------------------------------------- v0.6 : société d'agents
TEAM = ROOT / "examples" / "soc_team.agent"


class TestSociety(unittest.TestCase):
    def society(self, **kwargs):
        from soc_team import build
        hosts, llms, world = build(**kwargs)
        agents = parse_file(str(TEAM)).agents
        return Society(agents, hosts, llms), world

    def test_round_trip_between_two_agents(self):
        society, world = self.society()
        society.run(max_ticks=6, until="soc_analyst")
        self.assertTrue(world["contained"])
        names = [(e.sender, e.name) for e in society.log]
        self.assertEqual(names, [("soc_analyst", "check_host"),
                                 ("network_agent", "traffic_verdict")])

    def test_message_is_delivered_at_the_next_turn_not_the_same(self):
        """L'asynchronie est réelle : personne n'agit sur un message
        qu'il vient d'envoyer dans le même tick."""
        society, _ = self.society()
        society.tick()
        analyst = society.runtimes["soc_analyst"]
        self.assertEqual(analyst.state.beliefs["verdict"].value,
                         Symbol("pending"))
        self.assertEqual(len(society.runtimes["network_agent"].inbox), 0)

    def test_low_confidence_verdict_is_ignored(self):
        society, world = self.society(confidence=0.40)
        society.run(max_ticks=4, until="soc_analyst")
        self.assertFalse(world["contained"])

    def test_benign_verdict_blocks_the_action_by_policy(self):
        society, world = self.society(verdict="benign")
        society.run(max_ticks=4, until="soc_analyst")
        self.assertFalse(world["contained"])

    def test_shared_memory_is_one_object_versioned_per_key(self):
        society, _ = self.society()
        society.run(max_ticks=4, until="soc_analyst")
        for runtime in society.runtimes.values():
            self.assertIs(runtime.state.memory["SHARED"], society.shared)
        versions = society.shared_versions
        self.assertIn("blocked_hosts", versions)
        self.assertIn("incidents", versions)

    def test_independent_keys_do_not_collide(self):
        """v1.2 : la granularité du conflit est la clé, pas le compartiment.
        `blocked_hosts` n'est écrit que par l'analyste : aucun conflit."""
        society, _ = self.society()
        society.run(max_ticks=4, until="soc_analyst")
        self.assertEqual(society.shared_versions["blocked_hosts"], 1)

    def test_concurrent_write_on_a_shared_key_is_detected(self):
        """`incidents` est alimenté par les deux agents : le conflit est réel
        et doit apparaître, pas être absorbé."""
        society, _ = self.society()
        society.run(max_ticks=4, until="soc_analyst")
        self.assertGreaterEqual(society.shared_versions["incidents"], 2)
        self.assertGreaterEqual(society.metrics["shared_conflicts"], 1)

    def test_unknown_recipient_is_reported(self):
        src = """
        AGENT solo {
            TOOL ping { OUTPUT { ok: Symbol } RISK LOW }
            PLAN p WHEN 1 == 1 {
                STEP s { MESSAGE hello { TO ghost PAYLOAD { x = 1 } } }
            }
        }
        """
        from agentl.analyzer import check_program
        program = parse_source(src)
        codes = {d.code for d in check_program(program)}
        self.assertIn("E007", codes)


# ------------------------------------------- v0.7 : planification probabiliste
RISK = ROOT / "examples" / "soc_risk.agent"


class TestProbabilisticPlanner(unittest.TestCase):
    def prepared(self, **kwargs):
        from soc_risk import build
        agent = load(RISK)
        host, llm, world = build(**kwargs)
        runtime = Runtime(agent, host, llm)
        runtime.state.tick = 1
        runtime.phase_observe()
        runtime.phase_update_beliefs()
        return agent, runtime, world

    def plan(self, **kwargs):
        agent, runtime, world = self.prepared(**kwargs)
        planner = Planner(agent, agent.planner, runtime.policy)
        return planner.synthesize(runtime.state), world

    def test_outcomes_are_a_distribution(self):
        tool = load(RISK).tool("quarantine_account")
        self.assertTrue(tool.is_uncertain)
        self.assertAlmostEqual(sum(b.probability for b in tool.branches), 1.0)

    def test_planner_discovers_an_escalation_nobody_wrote(self):
        result, _ = self.plan()
        tools = [a.tool for a in result.actions]
        self.assertEqual(tools[0], "quarantine_account")
        self.assertIn("isolate_endpoint", tools[1:])
        self.assertGreaterEqual(result.goal_probability, 0.95)

    def test_the_costly_action_only_fires_where_needed(self):
        result, _ = self.plan()
        escalation = next(a for a in result.actions
                          if a.tool == "isolate_endpoint")
        self.assertLess(escalation.fire_probability, 0.5)

    def test_expected_utility_beats_the_single_shot_plan(self):
        """isolate seul : P=0.97 mais score 59. L'escalade score > 80.
        Sortir au premier plan conforme donnerait le mauvais."""
        result, _ = self.plan()
        self.assertGreater(result.score, 70)
        self.assertGreater(len(result.actions), 1)

    def test_forbidden_action_lowers_the_reachable_confidence(self):
        result, _ = self.plan(criticality="CRITICAL")
        self.assertEqual([a.tool for a in result.actions],
                         ["quarantine_account"])
        self.assertAlmostEqual(result.goal_probability, 0.70, places=2)
        self.assertIn("non atteinte", result.reason)

    def test_requires_is_re_evaluated_at_execution(self):
        """Un plan conforme ne doit pas rejouer une action devenue inutile."""
        from soc_risk import build
        agent = load(RISK)
        host, llm, world = build(quarantine_works=True)
        runtime = Runtime(agent, host, llm).run(max_ticks=2)
        self.assertEqual(world.actions, ["quarantine_account"])
        skipped = [e for e in runtime.trace.of_kind("INFO")
                   if "REQUIRES" in e.detail]
        self.assertTrue(skipped)

    def test_deterministic_programs_are_unaffected(self):
        """La v0.7 généralise la v0.5 : même plan, même coût."""
        from soc_analyst import build
        agent = load(SOC)
        host, llm, _ = build()
        runtime = Runtime(agent, host, llm)
        runtime.run(max_ticks=1)
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        self.assertEqual([a.tool for a in result.actions],
                         ["inspect_endpoint", "quarantine_account"])
        self.assertEqual(result.cost, 6)
        self.assertFalse(result.uncertain)


# ------------------------------------------------------- v1.0 : solveur
class TestSolver(unittest.TestCase):
    def expr(self, src):
        from agentl.lexer import tokenize
        from agentl.parser import Parser
        return Parser(tokenize(src)).expression()

    def test_contradictions_are_detected(self):
        for a, b in [("x == a", "x == b"), ("x == a", "x != a"),
                     ("n > 5", "n < 3"), ("sev >= HIGH", "sev == LOW"),
                     ("p >= 0.95", "p < 0.90")]:
            self.assertFalse(satisfiable(self.expr(a), self.expr(b)),
                             f"{a} ∧ {b} devrait être insatisfiable")

    def test_consistent_conjunctions_are_kept(self):
        for a, b in [("n > 5", "n < 8"), ("n >= 5", "n <= 5"),
                     ("sev >= HIGH", "sev == CRITICAL"), ("u == 1", "v == 2")]:
            self.assertTrue(satisfiable(self.expr(a), self.expr(b)))

    def test_disjunction_is_expanded(self):
        self.assertFalse(satisfiable(self.expr("x == a OR x == b"),
                                     self.expr("x != a"), self.expr("x != b")))

    def test_ordinal_entailment(self):
        self.assertTrue(entails([self.expr("sev == CRITICAL")],
                                self.expr("sev >= HIGH")))
        self.assertFalse(entails([self.expr("sev >= HIGH")],
                                 self.expr("sev == CRITICAL")))

    def test_unknown_stays_satisfiable(self):
        """Direction de sûreté : ce qu'on ne sait pas décider reste possible."""
        self.assertTrue(satisfiable(self.expr("f(x) > g(y)"),
                                    self.expr("f(x) < g(y)")))


# ---------------------------------------------------- v1.0 : vérificateur
UNSAFE = ROOT / "examples" / "unsafe.agent"


class TestVerifier(unittest.TestCase):
    def test_reference_agent_proves_all_four_theorems(self):
        report = verify(load(SOC))
        self.assertEqual(report.refuted, [])
        self.assertTrue(all(t.holds is not False for t in report.theorems))

    def test_unsafe_agent_passes_check_but_fails_verification(self):
        agent = load(UNSAFE)
        self.assertEqual([d for d in Analyzer(agent).run()
                          if d.severity == "error"], [])
        report = verify(agent)
        # 4 depuis la v1.4 : le point fixe de T2 démontre qu'aucune route ne
        # subsiste sous l'interdit, et l'agent ne déclare aucune escalade —
        # il calerait en silence. Avant le point fixe, c'était « ◐ BORNÉ ».
        self.assertEqual(len(report.refuted), 4)
        self.assertIn("T2", [t.key for t in report.refuted])

    def test_dead_branch_is_proved(self):
        codes = [f.code for f in verify(load(UNSAFE)).findings]
        self.assertIn("V101", codes)

    def test_negated_branch_is_proved_safe(self):
        """La branche ELSE nie la garde du NEVER : elle doit être blanchie,
        pas signalée. Un vérificateur qui crie partout ne sert à rien."""
        report = verify(load(UNSAFE))
        t1 = report.theorems[0]
        dead = [f for f in t1.findings if f.code == "V101"]
        self.assertEqual(len(dead), 2)          # 2 sur 5 sites, pas 5
        self.assertIn("5 site(s)", t1.summary)

    def test_unreachable_goal_under_prohibition(self):
        codes = [f.code for f in verify(load(UNSAFE)).findings]
        self.assertIn("V105", codes)

    def test_dead_capability_under_default_deny(self):
        findings = [f for f in verify(load(UNSAFE)).findings
                    if f.code == "V107"]
        self.assertEqual(len(findings), 1)
        self.assertIn("wipe_disk", findings[0].title)

    def test_llm_surface_is_reported(self):
        report = verify(load(UNSAFE))
        t4 = report.theorems[3]
        self.assertIs(t4.holds, False)
        self.assertIn("V104", [f.code for f in t4.findings])

    def test_route_search_respects_preconditions(self):
        report = verify(load(SOC))
        self.assertIn("inspect_endpoint → quarantine_account",
                      report.theorems[1].summary)

    def test_no_never_means_theorem_holds_trivially(self):
        src = """
        AGENT Plain {
            GOAL g { MAINTAIN a == b }
            TOOL t { OUTPUT { r: Symbol } RISK LOW }
            POLICY { ALLOW t }
            PLAN p WHEN 1 == 1 { STEP s { t() } }
        }
        """
        report = verify(parse_source(src).agents[0])
        self.assertIs(report.theorems[0].holds, True)


# ------------------------------------------------------------------ analyseur
class TestAnalyzer(unittest.TestCase):
    def test_reference_example_is_clean(self):
        self.assertEqual(Analyzer(load(SOC)).run(), [])

    def test_broken_example_is_rejected(self):
        diags = Analyzer(load(ROOT / "examples" / "broken.agent")).run()
        codes = {d.code for d in diags}
        self.assertLessEqual({"E001", "E002", "E003", "E004"}, codes)
        self.assertLessEqual({"W101", "W102", "W103", "W105", "W106"}, codes)

    def test_bad_likelihood_is_an_error(self):
        src = """
        AGENT H {
            HYPOTHESIS bad {
                PRIOR 0.1
                EVIDENCE { x > 1 LIKELIHOOD 1.4 GIVEN_NOT 0.2 }
                EXPLAINS y
            }
        }
        """
        codes = {d.code for d in Analyzer(parse_source(src).agents[0]).run()}
        self.assertIn("E006", codes)

    def test_planner_without_operators_is_flagged(self):
        src = """
        AGENT P {
            GOAL g { MAINTAIN a == b }
            TOOL t { OUTPUT { r: Symbol } RISK LOW }
            PLANNER { ENABLE }
        }
        """
        codes = {d.code for d in Analyzer(parse_source(src).agents[0]).run()}
        self.assertIn("W108", codes)

    def test_maintenance_example_has_no_errors(self):
        analyzer = Analyzer(load(ROOT / "examples" / "maintenance.agent"))
        analyzer.run()
        self.assertEqual(analyzer.errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# v1.3 — FOREACH : la collection perçue devient itérable
# ---------------------------------------------------------------------------

FOREACH_SRC = """
AGENT SWEEP {
    BELIEF { swept = no CONFIDENCE 1.00 SOURCE prior }
    TOOL list_items { OUTPUT { items } }
    TOOL touch {
        INPUT { id: String }
        RISK  { operational = LOW }
    }
    POLICY {
        DEFAULT ALLOW
        NEVER touch WHEN it.status == closed
    }
    PLAN sweep WHEN swept == no {
        STEP fetch { list_items() }
        STEP walk {
            FOREACH it IN items MAX 10 {
                IF it.status == open THEN { touch(id = it.id) }
            }
        }
    }
}
"""


class TestForEach(unittest.TestCase):
    def _run(self, items, max_clause="MAX 10"):
        src = FOREACH_SRC.replace("MAX 10", max_clause)
        agent = parse_source(src).agents[0]
        host = Host()
        touched = []
        host.tools["list_items"] = lambda: {"items": items}
        host.tools["touch"] = lambda id: touched.append(id) or {"ok": Symbol("yes")}
        Runtime(agent, host).run(max_ticks=1)
        return agent, touched

    def test_projects_each_element_and_filters(self):
        _, touched = self._run([
            {"id": "a", "status": "open"},
            {"id": "b", "status": "closed"},
            {"id": "c", "status": "open"},
        ])
        self.assertEqual(touched, ["a", "c"])

    def test_policy_is_evaluated_per_element(self):
        # Le NEVER porte sur le champ projeté : il doit écarter l'élément
        # fermé même si le filtre IF le laissait passer.
        src = FOREACH_SRC.replace("IF it.status == open THEN { touch(id = it.id) }",
                                  "touch(id = it.id)")
        agent = parse_source(src).agents[0]
        host = Host()
        touched = []
        host.tools["list_items"] = lambda: {"items": [
            {"id": "a", "status": "open"}, {"id": "b", "status": "closed"}]}
        host.tools["touch"] = lambda id: touched.append(id) or {"ok": Symbol("yes")}
        Runtime(agent, host).run(max_ticks=1)
        self.assertEqual(touched, ["a"])

    def test_bindings_do_not_leak_between_elements(self):
        # `b` n'a pas de champ `status` : il ne doit pas hériter de celui de `a`.
        _, touched = self._run([
            {"id": "a", "status": "open"},
            {"id": "b"},
        ])
        self.assertEqual(touched, ["a"])

    def test_max_bounds_the_traversal(self):
        items = [{"id": str(n), "status": "open"} for n in range(20)]
        _, touched = self._run(items, max_clause="MAX 3")
        self.assertEqual(len(touched), 3)

    def test_non_collection_source_is_reported_not_ignored(self):
        agent = parse_source(FOREACH_SRC).agents[0]
        host = Host()
        host.tools["list_items"] = lambda: {"items": Symbol("none")}
        host.tools["touch"] = lambda id: {"ok": Symbol("yes")}
        rt = Runtime(agent, host).run(max_ticks=1)
        self.assertTrue(any(e.kind == "ERROR" and "collection" in e.text
                            for e in rt.trace.events))

    def test_symbol_satisfies_a_string_contract(self):
        # Un identifiant perçu arrive en Symbol ; l'hôte doit recevoir une str.
        seen = []
        agent = parse_source(FOREACH_SRC).agents[0]
        host = Host()
        host.tools["list_items"] = lambda: {"items": [{"id": "a", "status": "open"}]}
        host.tools["touch"] = lambda id: seen.append(type(id)) or {"ok": Symbol("yes")}
        Runtime(agent, host).run(max_ticks=1)
        self.assertEqual(seen, [str])

    def test_calls_inside_foreach_are_visible_to_the_analyzer(self):
        # E001 : un outil non déclaré appelé dans le corps doit être vu.
        src = FOREACH_SRC.replace("touch(id = it.id)", "undeclared_tool(id = it.id)")
        codes = [d.code for d in Analyzer(parse_source(src).agents[0]).run()]
        self.assertIn("E001", codes)


class TestImpureCallInExpression(unittest.TestCase):
    """E009 — un appel d'outil ne s'évalue pas au milieu d'une expression.

    Le runtime le refusait déjà ; il ne le refusait qu'à l'exécution, au
    milieu d'un plan déjà commencé. Un défaut visible sur l'AST doit être
    signalé sur l'AST.
    """

    SRC = """
    AGENT X {
        TOOL fetch { OUTPUT { rows } }
        PLAN p WHEN true == true {
            STEP s { data = fetch() }
        }
    }
    """

    def test_assignment_from_a_call_is_an_error(self):
        diags = Analyzer(parse_source(self.SRC).agents[0]).run()
        self.assertIn("E009", [d.code for d in diags])

    def test_call_inside_a_condition_is_an_error(self):
        src = self.SRC.replace("data = fetch()", "IF fetch() == yes THEN { }")
        diags = Analyzer(parse_source(src).agents[0]).run()
        self.assertIn("E009", [d.code for d in diags])

    def test_plain_call_statement_is_clean(self):
        src = self.SRC.replace("data = fetch()", "fetch()")
        codes = [d.code for d in Analyzer(parse_source(src).agents[0]).run()]
        self.assertNotIn("E009", codes)

    def test_unevaluable_set_does_not_break_the_run(self):
        agent = parse_source(self.SRC).agents[0]
        host = Host()
        host.tools["fetch"] = lambda: {"rows": []}
        rt = Runtime(agent, host).run(max_ticks=1)
        self.assertTrue(any(e.kind == "ERROR" and "inévaluable" in e.text
                            for e in rt.trace.events))


class TestInputCoercion(unittest.TestCase):
    """Un contrat de type ne doit pas interdire le vocabulaire du langage."""

    SRC = """
    AGENT X {
        TOOL act {
            INPUT { id: String, public: Boolean }
            RISK  { operational = LOW }
        }
        PLAN p WHEN done == no {
            STEP s { act(id = ref, public = yes)  SET done = yes }
        }
        BELIEF { done = no CONFIDENCE 1.00 SOURCE prior }
    }
    """

    def _call(self, public_value):
        seen = {}
        agent = parse_source(self.SRC.replace("public = yes",
                                              f"public = {public_value}")).agents[0]
        host = Host()
        host.sensors["ref"] = lambda: Symbol("abc")
        host.tools["act"] = lambda id, public: seen.update(id=id, public=public) or {}
        rt = Runtime(agent, host)
        rt.state.set_local("ref", Symbol("abc"))
        rt.run(max_ticks=1)
        return seen

    def test_symbol_yes_satisfies_a_boolean_contract(self):
        self.assertEqual(self._call("yes").get("public"), True)

    def test_symbol_no_satisfies_a_boolean_contract(self):
        self.assertEqual(self._call("no").get("public"), False)

    def test_meaningless_symbol_is_still_refused(self):
        # `maybe` n'a pas de sens booléen : on refuse plutôt que de deviner.
        self.assertEqual(self._call("maybe"), {})


class TestProjectionBindsSymbols(unittest.TestCase):
    """Un `Symbol` rendu par l'hôte doit être projeté comme les autres.

    Il ne l'était pas : le champ restait indéfini, la comparaison devenait
    fausse, l'élément était filtré — sans erreur ni trace. Un défaut muet
    est le seul que ce langage ne doit pas tolérer.
    """

    SRC = """
    AGENT P {
        BELIEF { done = no CONFIDENCE 1.00 SOURCE prior }
        TOOL list_items { OUTPUT { items } }
        TOOL touch { INPUT { id: String } RISK { operational = LOW } }
        PLAN p WHEN done == no {
            STEP s {
                list_items()
                FOREACH it IN items {
                    IF it.flag == yes THEN { touch(id = it.id) }
                }
                SET done = yes
            }
        }
    }
    """

    def test_symbol_field_is_comparable(self):
        touched = []
        agent = parse_source(self.SRC).agents[0]
        host = Host()
        host.tools["list_items"] = lambda: {"items": [
            {"id": "a", "flag": Symbol("yes")},
            {"id": "b", "flag": Symbol("no")},
        ]}
        host.tools["touch"] = lambda id: touched.append(id) or {}
        Runtime(agent, host).run(max_ticks=1)
        self.assertEqual(touched, ["a"])

    def test_nested_symbol_field_is_comparable(self):
        touched = []
        src = self.SRC.replace("it.flag", "it.meta.flag")
        agent = parse_source(src).agents[0]
        host = Host()
        host.tools["list_items"] = lambda: {"items": [
            {"id": "a", "meta": {"flag": Symbol("yes")}},
            {"id": "b", "meta": {"flag": Symbol("no")}},
        ]}
        host.tools["touch"] = lambda id: touched.append(id) or {}
        Runtime(agent, host).run(max_ticks=1)
        self.assertEqual(touched, ["a"])


class TestBoundary(unittest.TestCase):
    """B00x — la décision métier n'a pas le droit de descendre dans l'hôte.

    C'est la seule règle du projet dont la violation ne produisait aucun
    signal : le programme restait bien formé, le vérificateur muet, et la
    garantie avait disparu.
    """

    def _check(self, source: str):
        import tempfile
        from agentl.boundary import check_host

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(source)
            path = handle.name
        return check_host(path)

    def _codes(self, source: str):
        return [f.code for f in self._check(source).findings if not f.waived]

    def test_business_comparison_is_refused(self):
        self.assertIn("B001", self._codes(
            "def f(row):\n    if row['status'] == 'Blocked':\n        return 1\n    return 0\n"))

    def test_absence_guard_is_accepted(self):
        # Vérifier qu'une donnée existe n'est pas décider ce qu'elle vaut.
        codes = self._codes(
            "def f(rows):\n    if not rows:\n        return None\n    return rows[0]\n")
        self.assertEqual(codes, [])

    def test_filtering_a_collection_is_refused(self):
        self.assertIn("B002", self._codes(
            "def f(items):\n    return [i for i in items if i['tier'] == 'Platinum']\n"))

    def test_numeric_threshold_is_refused(self):
        self.assertIn("B005", self._codes(
            "def f(deal):\n    return deal['amount'] >= 150000\n"))

    def test_deciding_function_name_is_refused(self):
        self.assertIn("B006", self._codes(
            "def should_escalate(x):\n    return x\n"))

    def test_sorting_is_flagged_as_prioritisation(self):
        self.assertIn("B004", self._codes(
            "def f(items):\n    return sorted(items, key=lambda i: i['score'])\n"))

    def test_a_justified_waiver_lifts_the_whole_statement(self):
        # La levée porte sur l'instruction, pas sur une ligne : une
        # compréhension multi-lignes à moitié levée serait le pire des cas.
        report = self._check(
            "def f(items):\n"
            "    # BOUNDARY-OK: recherche par identifiant, aucun critère métier\n"
            "    return [i for i in items\n"
            "            if i['id'] == 'abc123']\n")
        self.assertTrue(report.ok())
        self.assertTrue(report.waivers)
        self.assertIn("identifiant", report.waivers[0].reason)

    def test_a_waiver_without_reason_lifts_nothing(self):
        report = self._check(
            "def f(items):\n"
            "    # BOUNDARY-OK\n"
            "    return [i for i in items if i['tier'] == 'Platinum']\n")
        self.assertFalse(report.ok())

    def test_missing_host_is_an_error(self):
        import tempfile
        from pathlib import Path
        from agentl.boundary import check_pair

        with tempfile.TemporaryDirectory() as folder:
            agent = Path(folder) / "orphan.agent"
            agent.write_text("AGENT X { }")
            report = check_pair(agent)
            self.assertFalse(report.ok())
            self.assertEqual(report.findings[0].code, "B000")

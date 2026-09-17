"""AAA end-to-end smoke tests for the shipped examples/ and the CLI surface.

Objectif : garantir qu'AUCUN exemple du dépôt public n'est cassé, et couvrir
les modules laissés à découvert par tests/test_agentl.py :

    - agentl.cli        (aucune couverture avant ce fichier)
    - agentl.viz        (aucune couverture avant ce fichier)
    - agentl.trace_html (aucune couverture avant ce fichier)
    - agentl.llm._parse_json / _coerce (robustesse aux sorties LLM sales)

Tous les tests sont HORS-LIGNE : les hôtes basés sur Gemini ne sont jamais
exécutés (on vérifie seulement leur câblage), et aucun appel réseau n'est émis.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import (Analyzer, MockLLM, Runtime, Society, Symbol, UNDEFINED,
                    parse_file, verify)
from agentl.cli import main as cli_main
from agentl.llm import _coerce, _parse_json
from agentl.trace_html import render as render_trace
from agentl.viz import build_program, render_html

EXAMPLES = ROOT / "examples"

# Tout .agent livré doit passer l'analyse statique SANS erreur, à ces deux
# exceptions près qui sont des cas négatifs volontaires.
NEGATIVE_CHECK = {"broken.agent"}     # doit produire des E-diagnostics
NEGATIVE_VERIFY = {"unsafe.agent", "gmail_butler_naif.agent"}    # verify doit RÉFUTER

# Hôtes purement hors-ligne (MockLLM) exposant build().
OFFLINE_HOSTS = {
    "soc_analyst.agent": "soc_analyst",
    "maintenance.agent": "maintenance",
    "soc_risk.agent": "soc_risk",
    "supervisor.agent": "supervisor",
}

# Hôtes nécessitant un LLM réel (Gemini) : on ne les EXÉCUTE pas, on vérifie
# seulement que build() câble un hôte cohérent hors-ligne.
GEMINI_HOSTS = ("disk_sentinel", "service_medic", "idor_hunter")


def agent_files():
    return sorted(EXAMPLES.glob("*.agent"))


def quiet(fn, *a, **k):
    """Exécute fn en avalant stdout ; renvoie (résultat, texte capturé)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*a, **k)
    return result, buf.getvalue()


# --------------------------------------------------------------------- parsing
class TestEveryExampleParses(unittest.TestCase):
    def test_all_agent_files_parse(self):
        self.assertTrue(agent_files(), "aucun .agent trouvé — chemin cassé ?")
        for path in agent_files():
            with self.subTest(agent=path.name):
                program = parse_file(str(path))
                self.assertTrue(program.agents,
                                f"{path.name} : aucun agent produit")


# ------------------------------------------------------------- analyse statique
class TestStaticAnalysisCleanliness(unittest.TestCase):
    def test_shipped_agents_have_no_static_errors(self):
        for path in agent_files():
            if path.name in NEGATIVE_CHECK:
                continue
            with self.subTest(agent=path.name):
                for agent in parse_file(str(path)).agents:
                    errors = [d for d in Analyzer(agent).run()
                              if d.severity == "error"]
                    self.assertEqual(
                        errors, [],
                        f"{path.name} : erreurs statiques inattendues "
                        f"→ {[d.render() for d in errors]}")

    def test_broken_example_fails_cleanly_with_errors(self):
        agent = parse_file(str(EXAMPLES / "broken.agent")).agents[0]
        errors = [d for d in Analyzer(agent).run() if d.severity == "error"]
        self.assertTrue(errors,
                        "broken.agent doit produire au moins une erreur")
        # Échec PROPRE : des diagnostics structurés, jamais un traceback nu.
        for d in errors:
            self.assertRegex(d.render(), r"E\d{3}")

    def test_k8s_healer_regression_no_errors(self):
        """Garde-fou : K8S_HEALER référençait des plans inexistants (E002) et
        laissait drain_node mort (V107). L'exemple doit rester propre."""
        agent = parse_file(str(EXAMPLES / "K8S_HEALER.agent")).agents[0]
        errors = [d for d in Analyzer(agent).run() if d.severity == "error"]
        self.assertEqual(errors, [],
                         f"K8S_HEALER a régressé : {[d.render() for d in errors]}")
        self.assertFalse(verify(agent).refuted,
                         "K8S_HEALER : un théorème est réfuté")


# ---------------------------------------------------------------- vérification
class TestVerifierOnExamples(unittest.TestCase):
    def test_shipped_agents_are_not_refuted(self):
        for path in agent_files():
            if path.name in NEGATIVE_VERIFY or path.name in NEGATIVE_CHECK:
                continue
            with self.subTest(agent=path.name):
                for agent in parse_file(str(path)).agents:
                    self.assertFalse(
                        verify(agent).refuted,
                        f"{path.name} : théorème réfuté de façon inattendue")

    def test_unsafe_example_is_refuted(self):
        agent = parse_file(str(EXAMPLES / "unsafe.agent")).agents[0]
        report = verify(agent)
        self.assertTrue(report.refuted,
                        "unsafe.agent DOIT être réfuté (cas négatif)")
        # Sortie exploitable, pas un crash.
        self.assertIn("RÉFUTÉ", report.render())


# ------------------------------------------------------------------ CLI (0 % avant)
class TestCliSmoke(unittest.TestCase):
    SOC = str(EXAMPLES / "soc_analyst.agent")

    def test_check_returns_zero_for_valid_agent(self):
        rc, out = quiet(cli_main, ["check", self.SOC])
        self.assertEqual(rc, 0)
        self.assertIn("SOC_ANALYST", out)

    def test_check_returns_one_for_broken_agent(self):
        rc, out = quiet(cli_main, ["check", str(EXAMPLES / "broken.agent")])
        self.assertEqual(rc, 1)

    def test_verify_returns_one_for_unsafe_agent(self):
        rc, _ = quiet(cli_main, ["verify", str(EXAMPLES / "unsafe.agent")])
        self.assertEqual(rc, 1)

    def test_verify_returns_zero_for_safe_agent(self):
        rc, out = quiet(cli_main, ["verify", self.SOC])
        self.assertEqual(rc, 0)
        self.assertIn("théorème", out)

    def test_ast_dumps_tree(self):
        rc, out = quiet(cli_main, ["ast", self.SOC])
        self.assertEqual(rc, 0)
        self.assertIn("Program", out)

    def test_infer_prints_posteriors(self):
        rc, out = quiet(cli_main, ["infer", self.SOC, "--ticks", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("seuil", out)

    def test_plan_reports_operators(self):
        rc, out = quiet(cli_main, ["plan", str(EXAMPLES / "disk_sentinel.agent"),
                                   "--ticks", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Opérateurs", out)

    def test_run_end_to_end_with_offline_host(self):
        rc, out = quiet(cli_main, ["run", self.SOC,
                                   "--quiet"])
        self.assertEqual(rc, 0)
        self.assertIn("ticks=", out)

    def test_run_refuses_a_broken_program(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cli_main(["run", str(EXAMPLES / "broken.agent"), "--quiet"])
        self.assertEqual(rc, 1)
        self.assertIn("Exécution refusée", buf.getvalue())

    def test_verify_and_test_refuse_a_broken_program(self):
        """Aucun verdict sur un programme que `check` rejette.

        `verify` rendait huit théorèmes DÉMONTRÉS et un code nul sur
        `broken.agent` — vrais à vide, parce qu'un `NEVER` visant un outil
        inexistant n'a aucun site d'appel à examiner. Le code de retour était
        celui d'un programme sûr. `test` faisait de même sur ses scénarios.
        """
        for command in ("verify", "test"):
            with self.subTest(command=command):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    with contextlib.redirect_stdout(io.StringIO()):
                        rc = cli_main([command,
                                       str(EXAMPLES / "broken.agent")])
                self.assertEqual(rc, 1)
                self.assertIn(f"`{command}` refusé", buf.getvalue())
                # Les erreurs elles-mêmes doivent être rendues : refuser sans
                # dire quoi corriger déplace le problème au lieu de le poser.
                self.assertIn("E001", buf.getvalue())

    def test_verify_still_refutes_an_ill_intentioned_but_wellformed_program(self):
        """Le refus ne doit pas masquer la réfutation : `unsafe.agent` est bien
        formé et doit continuer d'échouer *par ses théorèmes*, pas par l'analyse.
        """
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc, out = quiet(cli_main, ["verify", str(EXAMPLES / "unsafe.agent")])
        self.assertEqual(rc, 1)
        self.assertNotIn("refusé", buf.getvalue())
        self.assertIn("RÉFUTÉ", out)

    def test_run_writes_html_journal(self):
        out_path = EXAMPLES / "__aaa_tmp_run.html"
        try:
            rc, _ = quiet(cli_main, ["run", str(EXAMPLES / "maintenance.agent"),
                                     "--quiet", "--html", str(out_path)])
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            self.assertIn("<html", out_path.read_text(encoding="utf-8").lower())
        finally:
            out_path.unlink(missing_ok=True)

    def test_run_events_carry_the_whole_trace_as_json_lines(self):
        """`--events` : les événements d'origine, sans glyphe à découper.

        Le fichier doit dire exactement ce que dit la trace texte — ni moins
        (un événement perdu fausse la chronologie), ni dans un autre ordre.
        """
        source = EXAMPLES / "maintenance.agent"
        out_path = EXAMPLES / "__aaa_tmp_run.events.jsonl"
        try:
            rc, out = quiet(cli_main, ["run", str(source), "--quiet",
                                       "--events", str(out_path)])
            self.assertEqual(rc, 0)
            rows = [json.loads(line) for line in
                    out_path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(rows)
            self.assertEqual([row["seq"] for row in rows],
                             list(range(1, len(rows) + 1)))
            self.assertEqual({row["agent"] for row in rows},
                             {parse_file(str(source)).agents[0].name})
            self.assertIn("TICK", {row["kind"] for row in rows})
            printed = [line for line in out.splitlines()
                       if line.startswith(("│", "┌─ tick"))]
            self.assertEqual(len(rows), len(printed))
        finally:
            out_path.unlink(missing_ok=True)

    def test_society_events_are_attributed_to_their_agent(self):
        """Dans une société, chaque agent redémarre ses ticks à 1 : sans le
        nom d'agent sur chaque ligne, deux traces se liraient l'une dans
        l'autre."""
        out_path = EXAMPLES / "__aaa_tmp_society.events.jsonl"
        try:
            rc, _ = quiet(cli_main, ["run", str(EXAMPLES / "soc_team.agent"),
                                     "--quiet", "--events", str(out_path)])
            self.assertEqual(rc, 0)
            rows = [json.loads(line) for line in
                    out_path.read_text(encoding="utf-8").splitlines()]
            agents = {row["agent"] for row in rows if row["kind"] == "TICK"}
            self.assertEqual(agents, {"soc_analyst", "network_agent"})
        finally:
            out_path.unlink(missing_ok=True)

    def test_unusable_events_path_is_refused_before_the_first_tick(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc, out = quiet(cli_main, [
                "run", str(EXAMPLES / "maintenance.agent"), "--quiet",
                "--events", str(EXAMPLES / "__absent__" / "e.jsonl")])
        self.assertEqual(rc, 2)
        self.assertIn("événements inutilisable", buf.getvalue())
        self.assertNotIn("tick", out)


# ------------------------------------------------- viz + trace_html (0 % avant)
class TestVizAndTraceHtml(unittest.TestCase):
    def test_build_program_and_render_html_for_every_example(self):
        for path in agent_files():
            with self.subTest(agent=path.name):
                program = parse_file(str(path))
                graph = build_program(program)
                self.assertIsInstance(graph, dict)
                html = render_html(graph)
                self.assertIn("<", html)
                self.assertGreater(len(html), 200)

    def test_viz_handles_multi_agent_program(self):
        program = parse_file(str(EXAMPLES / "soc_team.agent"))
        html = render_html(build_program(program))
        self.assertIn("<", html)

    def test_trace_html_render_after_a_real_run(self):
        from soc_analyst import build
        agent = parse_file(str(EXAMPLES / "soc_analyst.agent")).agents[0]
        host, llm, _ = build()
        runtime = Runtime(agent, host, llm).run(max_ticks=2)
        html = render_trace(runtime, agent)
        self.assertIn("<html", html.lower())
        self.assertGreater(len(html), 500)


# --------------------------------------------- exécution bout-en-bout hors-ligne
class TestOfflineHostsEndToEnd(unittest.TestCase):
    def test_offline_hosts_run_without_crashing(self):
        for agent_name, module_name in OFFLINE_HOSTS.items():
            with self.subTest(host=module_name):
                module = __import__(module_name)
                built = module.build()
                host, llm = built[0], built[1]
                agent = parse_file(str(EXAMPLES / agent_name)).agents[0]
                runtime = Runtime(agent, host, llm).run(max_ticks=3)
                self.assertGreaterEqual(runtime.state.tick, 1)
                # Un run sain n'accumule pas d'erreurs de dérive d'effet.
                self.assertEqual(runtime.metrics.get("effect_drift", 0), 0)

    def test_soc_team_society_runs_offline(self):
        from soc_team import build
        agents = parse_file(str(EXAMPLES / "soc_team.agent")).agents
        hosts, llms, world = build()
        society = Society(agents, hosts, llms)
        society.run(max_ticks=6, until="soc_analyst")
        self.assertTrue(world["contained"])


# ------------------------------------- hôtes Gemini : câblage vérifié hors-ligne
class TestGeminiHostsWireOffline(unittest.TestCase):
    """build() ne doit émettre AUCUN appel réseau : on peut donc construire
    l'hôte hors-ligne et vérifier son câblage sans dépendre de la clé API."""

    def test_gemini_hosts_build_and_wire_tools(self):
        for module_name in GEMINI_HOSTS:
            with self.subTest(host=module_name):
                module = __import__(module_name)
                built = module.build()
                host = built[0]
                self.assertTrue(host.tools,
                                f"{module_name} : aucun outil câblé")


# ------------------------------------------- robustesse LLM (_parse_json/_coerce)
class TestLlmCoercion(unittest.TestCase):
    def test_parse_json_strips_markdown_fence(self):
        self.assertEqual(_parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_parse_json_extracts_embedded_object(self):
        self.assertEqual(_parse_json('bla bla {"x": 2} fin'), {"x": 2})

    def test_parse_json_returns_empty_on_garbage(self):
        self.assertEqual(_parse_json("pas du json du tout"), {})

    def test_parse_json_rejects_non_object_json(self):
        self.assertEqual(_parse_json("[1, 2, 3]"), {})

    def test_coerce_forces_declared_schema(self):
        out = _coerce({"host": 42, "extra": "ignored"},
                      {"host": "String"})
        self.assertEqual(out, {"host": "42"})
        self.assertNotIn("extra", out)

    def test_coerce_fills_missing_keys_with_typed_defaults(self):
        out = _coerce({}, {"n": "Number", "flag": "Bool", "s": "String"})
        self.assertEqual(out, {"n": 0.0, "flag": False, "s": "unknown"})

    def test_coerce_produces_symbol_instances(self):
        out = _coerce({"verdict": "hostile"},
                      {"verdict": "Symbol IN [hostile, benign]"})
        self.assertEqual(out["verdict"], Symbol("hostile"))

    def test_coerce_survives_uncastable_number(self):
        out = _coerce({"n": "not-a-number"}, {"n": "Number"})
        self.assertIs(out["n"], UNDEFINED)

    def test_mock_llm_defaults_to_schema_when_unscripted(self):
        llm = MockLLM()
        out = llm.reason("anything", {}, {"k": "String"})
        self.assertEqual(out, {"k": "unknown"})


if __name__ == "__main__":
    unittest.main()

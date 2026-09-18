"""Tests AAA du STUDIO (agentl/studio/*, statiques compris).

Le studio est la seule partie d'AGENT-L qui *écoute sur une socket*, *exécute
du code Python arbitraire* (les modules hôtes) et *écrit sur le disque*. Les
propriétés visées ici sont donc, dans l'ordre :

  * confinement : rien hors de la racine `--root` n'est lisible, exécutable ni
    écrivable — ni pour les `.agent`, ni pour les hôtes (403, jamais 500) ;
  * fail-closed : une question humaine sans réponse vaut refus, un programme
    qui ne passe pas l'analyse statique n'est pas exécutable, `--no-save`
    ferme *les deux* voies d'écriture (HTTP et websocket) ;
  * contrat de flux (§2) : `seq` monotone et unique, `nodeId` émis toujours
    présent dans le graphe, aucun `NaN` sérialisé ;
  * robustesse : aucun traceback nu vers le client, aucune commande websocket
    ne tue la boucle de réception, `stop()` rend la main en moins d'une
    seconde même bloqué sur une approbation ;
  * air-gap : la coque web ne référence aucune ressource externe.

Tous les tests sont déterministes et rapides : aucun port fixe (TestClient),
aucun `sleep` arbitraire long, aucun fichier du dépôt modifié (les écritures
travaillent sur une copie dans un répertoire temporaire).

    python -m pytest tests/test_studio_aaa.py -q
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.core import AgentLError, Symbol
from agentl.studio import events as E
from agentl.studio.session import StaleRevision, StudioSession

EXAMPLES = ROOT / "examples"
STATIC = ROOT / "agentl" / "studio" / "static"
JS_MODULES = ("app.js", "bus.js", "canvas.js", "graph.js", "editor.js",
              "inspector.js", "timeline.js")

#: Programme minimal exigeant une approbation humaine à chaque tick : c'est le
#: seul moyen d'éprouver la boucle HITL (les exemples du dépôt s'approuvent
#: tout seuls via leur hôte, que le studio détourne justement).
HITL_SRC = """
AGENT HITL {
    VERSION "1"
    OBSERVE { sensor.flag }
    BELIEF { sensor.flag = no CONFIDENCE 0.9 SOURCE prior }
    GOAL done { MAINTAIN sensor.flag == yes }
    TOOL flip {
        OUTPUT { ok: Symbol }
        RISK HIGH
        EFFECT { sensor.flag = yes }
    }
    POLICY {
        DEFAULT DENY
        ALLOW flip
        REQUIRE APPROVAL FOR flip
    }
    PLAN fix WHEN sensor.flag != yes {
        STEP go { flip() }
    }
    LOOP UNTIL goal.satisfied MAX 2 {
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY
    }
}
"""

#: Budget de patience des attentes actives. Largement au-dessus du temps réel
#: observé (~2 s pour un run complet), mais borné : un test ne doit jamais
#: pendre indéfiniment si une propriété est violée.
DEADLINE = 20.0


# --------------------------------------------------------------------------
# Outillage commun
# --------------------------------------------------------------------------
def _copy_root(case: unittest.TestCase, *names: str) -> Path:
    """Racine temporaire contenant une copie des exemples demandés.

    Aucun test n'écrit dans le dépôt : `save` et les tests d'écriture par
    websocket travaillent ici.
    """
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    for name in names:
        shutil.copy(EXAMPLES / name, root / name)
    return root


def _pump(session: StudioSession, q, *, reply=None,
          until: str = "run.finished", stop_on_prompt: bool = False) -> list:
    """Draine la file jusqu'à `until`, en répondant éventuellement aux prompts."""
    msgs: list = []
    started = time.monotonic()
    while time.monotonic() - started < DEADLINE:
        for msg in session.drain(q):
            msgs.append(msg)
            if msg["type"] == "prompt":
                if stop_on_prompt:
                    return msgs
                if reply is not None:
                    session.reply(msg["promptId"], reply)
        if any(m["type"] == until for m in msgs):
            return msgs
        time.sleep(0.01)
    raise AssertionError(f"« {until} » jamais reçu en {DEADLINE} s "
                         f"({len(msgs)} messages)")


def _hitl_session(case: unittest.TestCase, timeout: float) -> StudioSession:
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    (root / "hitl.agent").write_text(HITL_SRC, encoding="utf-8")
    # La norme de nommage impose un hôte du même nom : ici un hôte vide, les
    # approbations venant du studio et non du module.
    (root / "hitl.py").write_text(
        "from agentl.host import Host\n\n\ndef build():\n    return Host()\n",
        encoding="utf-8")
    session = StudioSession(root=root, prompt_timeout=timeout)
    case.addCleanup(session.close)
    session.open("hitl.agent")
    return session


def _repo_session(case: unittest.TestCase, name: str = "soc_analyst.agent",
                  **kw) -> StudioSession:
    """Session en lecture seule sur le dépôt (aucun test n'y écrit)."""
    session = StudioSession(root=ROOT, **kw)
    case.addCleanup(session.close)
    session.open(f"examples/{name}")
    return session


# --------------------------------------------------------------------------
# M1 — ouverture, édition, révisions
# --------------------------------------------------------------------------
class TestSessionOpenAndEdit(unittest.TestCase):
    """Ouverture, analyse et discipline de révision du moteur de session."""

    def test_open_parses_and_builds_graph(self):
        s = _repo_session(self)
        self.assertEqual(s.relpath(s.file), "examples/soc_analyst.agent")
        self.assertTrue(s.source.strip())
        self.assertGreaterEqual(s.rev, 1)
        self.assertTrue(s.graph["nodes"], "graphe vide après ouverture")
        self.assertFalse([d for d in s.diags if d["severity"] == "error"])

    def test_set_source_increments_rev_and_emits(self):
        s = _repo_session(self)
        q = s.subscribe()
        before = s.rev
        result = s.set_source(s.source, rev=before)
        self.assertEqual(result["rev"], before + 1)
        self.assertEqual(s.rev, before + 1)
        types = [m["type"] for m in s.drain(q)]
        # §2 : une édition diffuse source, diagnostics et graphe.
        self.assertEqual(types, ["source", "diagnostics", "graph"])

    def test_stale_revision_is_refused(self):
        s = _repo_session(self)
        s.set_source(s.source, rev=s.rev)
        with self.assertRaises(StaleRevision):
            s.set_source("AGENT X { VERSION \"1\" }", rev=s.rev - 1)

    def test_set_source_without_rev_is_accepted(self):
        # `rev=None` = édition non concurrente (première frappe d'un client).
        s = _repo_session(self)
        before = s.rev
        s.set_source(s.source, rev=None)
        self.assertEqual(s.rev, before + 1)

    def test_non_string_source_is_agentl_error(self):
        s = _repo_session(self)
        with self.assertRaises(AgentLError):
            s.set_source(None)  # type: ignore[arg-type]

    def test_broken_file_gives_diagnostics_not_traceback(self):
        s = _repo_session(self, "broken.agent")
        errors = [d for d in s.diags if d["severity"] == "error"]
        self.assertTrue(errors, "examples/broken.agent doit produire des erreurs")
        for d in s.diags:
            self.assertIn(d["severity"], ("error", "warning"))
            self.assertIsInstance(d["code"], str)
            self.assertIsInstance(d["line"], int)
            self.assertNotIn("Traceback", d["message"])
            self.assertNotIn("File \"", d["message"])

    def test_unparsable_source_keeps_last_valid_graph(self):
        # Le canevas ne doit pas clignoter à chaque frappe intermédiaire.
        s = _repo_session(self)
        good = s.graph
        s.set_source("AGENT a {", rev=s.rev)
        self.assertTrue([d for d in s.diags if d["severity"] == "error"])
        self.assertEqual(s.graph, good)


# --------------------------------------------------------------------------
# M1 — confinement des chemins
# --------------------------------------------------------------------------
class TestSessionPathConfinement(unittest.TestCase):
    """Aucun chemin hors racine n'est ouvrable ni exécutable."""

    TRAVERSALS = ("../..", "../../etc/passwd", "examples/../../etc/passwd",
                  "/etc/passwd", "/")

    def test_resolve_path_refuses_traversal(self):
        s = StudioSession(root=ROOT)
        self.addCleanup(s.close)
        for candidate in self.TRAVERSALS:
            with self.subTest(path=candidate):
                with self.assertRaises(AgentLError):
                    s.resolve_path(candidate)

    def test_open_refuses_traversal(self):
        s = StudioSession(root=EXAMPLES)
        self.addCleanup(s.close)
        with self.assertRaises(AgentLError):
            s.open("../agentl/cli.py")

    def test_host_outside_root_is_refused(self):
        """L'hôte est déduit du nom : hors racine, il n'existe pas, donc pas de run.

        Un hôte est du code arbitraire ; la racine borne ce qu'on exécute, et
        la norme de nommage interdit d'aller le chercher ailleurs.
        """
        root = _copy_root(self, "soc_analyst.agent")
        s = StudioSession(root=root)
        self.addCleanup(s.close)
        s.open("soc_analyst.agent")
        self.assertFalse(s.host_status()["exists"])
        with self.assertRaises(AgentLError) as ctx:
            s.run(ticks=1)
        self.assertIn("soc_analyst.py", str(ctx.exception))
        self.assertFalse(s.running)

    def test_symlink_escaping_root_is_refused(self):
        root = _copy_root(self, "soc_analyst.agent")
        link = root / "escape.py"
        try:
            link.symlink_to(EXAMPLES / "soc_analyst.py")
        except OSError:                                 # pragma: no cover
            self.skipTest("liens symboliques indisponibles")
        s = StudioSession(root=root)
        self.addCleanup(s.close)
        s.open("soc_analyst.agent")
        with self.assertRaises(AgentLError):
            s.run(ticks=1)

    def test_host_that_is_a_directory_is_refused(self):
        """`X.py` doit être un fichier : un répertoire du même nom ne l'est pas."""
        root = _copy_root(self, "soc_analyst.agent")
        (root / "soc_analyst.py").mkdir()
        s = StudioSession(root=root)
        self.addCleanup(s.close)
        s.open("soc_analyst.agent")
        self.assertFalse(s.host_status()["exists"])
        with self.assertRaises(AgentLError):
            s.run(ticks=1)


# --------------------------------------------------------------------------
# M1 — refus d'exécution d'un programme fautif
# --------------------------------------------------------------------------
class TestSessionRefusesUnsoundRun(unittest.TestCase):
    def test_static_error_blocks_run(self):
        """Même règle que la CLI : un programme fautif n'est pas exécutable."""
        s = _repo_session(self, "broken.agent")
        with self.assertRaises(AgentLError) as ctx:
            s.run(ticks=1)
        self.assertIn("analyse statique", str(ctx.exception))
        self.assertFalse(s.running)

    def test_no_program_blocks_run(self):
        s = StudioSession(root=ROOT)
        self.addCleanup(s.close)
        with self.assertRaises(AgentLError):
            s.run(ticks=1)

    def test_warnings_alone_do_not_block(self):
        # `unsafe.agent` est bien formé (aucune erreur) : il doit rester
        # exécutable — c'est `verify`, pas `check`, qui le réfute. Le dépôt ne
        # lui donne pas d'hôte, on lui en fournit un vide dans une copie.
        root = _copy_root(self, "unsafe.agent")
        (root / "unsafe.py").write_text(
            "from agentl.host import Host\n\n\ndef build():\n    return Host()\n",
            encoding="utf-8")
        s = StudioSession(root=root)
        self.addCleanup(s.close)
        s.open("unsafe.agent")
        self.assertFalse([d for d in s.diags if d["severity"] == "error"])
        q = s.subscribe()
        s.run(ticks=1)
        _pump(s, q)


# --------------------------------------------------------------------------
# M1 — sérialisation (§2 : jamais de NaN, jamais d'exception)
# --------------------------------------------------------------------------
@dataclass
class _Sample:
    a: int
    b: float


class TestToJsonable(unittest.TestCase):
    """`to_jsonable` ne lève jamais et ne produit jamais de littéral non JSON."""

    def test_symbol_becomes_its_name(self):
        self.assertEqual(E.to_jsonable(Symbol("contained")), "contained")

    def test_non_finite_floats_become_null(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertIsNone(E.to_jsonable(value))

    def test_set_becomes_sorted_list(self):
        self.assertEqual(E.to_jsonable({"b", "a"}), ["a", "b"])
        self.assertEqual(E.to_jsonable(frozenset({1})), [1])

    def test_dataclass_becomes_object(self):
        self.assertEqual(E.to_jsonable(_Sample(a=1, b=float("nan"))),
                         {"a": 1, "b": None})

    def test_non_string_keys_and_cycles_survive(self):
        cyclic: dict = {}
        cyclic["self"] = cyclic
        cyclic[7] = {float("inf"): "x"}
        out = E.to_jsonable(cyclic)
        self.assertEqual(out["self"], "<cycle>")
        self.assertEqual(out["7"], {"inf": "x"})

    def test_arbitrary_object_falls_back_to_repr(self):
        class Weird:
            def __repr__(self) -> str:
                return "<weird>"

        self.assertEqual(E.to_jsonable(Weird()), "<weird>")

    def test_output_is_strict_json(self):
        payload = E.to_jsonable({"x": float("nan"), "y": {Symbol("s")},
                                 "z": _Sample(a=1, b=float("inf"))})
        text = json.dumps(payload, allow_nan=False)   # lèverait sur un NaN
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)


# --------------------------------------------------------------------------
# M1 — contrat de flux d'un run
# --------------------------------------------------------------------------
class TestNodeIdDivergence(unittest.TestCase):
    """DIVERGENCE CONTRAT (§3) — signalée, non corrigée.

    Le §3 documente `nodeId = "{agent}::{kind}::{key}"`. L'implémentation
    (`events.node_id`, alignée sur `agentl.viz`) produit `"{lane}:{key}"`, et
    `"{agent}|{lane}:{key}"` en programme multi-agents. C'est *viz* qui a
    raison au sens opérationnel — M4 réutilise `build_program()`, donc les
    identifiants doivent coïncider avec les siens — mais le contrat n'a pas été
    mis à jour. Ce test fige la règle réellement appliquée : si quelqu'un
    « corrige » l'un des deux côtés sans l'autre, il casse ici.
    """

    def test_node_id_follows_viz_not_the_contract_wording(self):
        from agentl.viz import build_program
        from agentl.parser import parse_source

        graph = build_program(parse_source(
            (EXAMPLES / "soc_analyst.agent").read_text(encoding="utf-8")))
        ids = {n["id"] if isinstance(n, dict) else n.id for n in graph["nodes"]}
        self.assertIn(E.node_id("SOC_ANALYST", "tool", "isolate_endpoint"), ids)
        self.assertNotIn("SOC_ANALYST::tool::isolate_endpoint", ids)

    def test_multi_agent_node_id_is_prefixed_by_the_agent(self):
        self.assertEqual(E.node_id("A", "tool", "t", multi=True), "A|tool:t")


class TestDiagnosticColumnDivergence(unittest.TestCase):
    """DIVERGENCE CONTRAT (§2) — signalée, non corrigée.

    Le schéma prévoit un champ `col` pour chaque diagnostic ; l'analyseur
    (`agentl.analyzer.Diagnostic`) ne porte pas de colonne, donc `col` vaut
    toujours `null`. L'éditeur (M5) ne peut souligner qu'une ligne entière.
    """

    def test_column_is_always_null(self):
        s = _repo_session(self, "broken.agent")
        self.assertTrue(s.diags)
        for d in s.diags:
            self.assertIn("col", d)
            self.assertIsNone(d["col"])


class TestRunStreamContract(unittest.TestCase):
    """Un run réel : ordre, unicité des `seq`, identité des `nodeId`."""

    @classmethod
    def setUpClass(cls):
        cls._session = StudioSession(root=ROOT)
        cls._session.open("examples/soc_analyst.agent")
        q = cls._session.subscribe()
        cls._session.run(ticks=3)
        cls.msgs = _pump(cls._session, q)

    @classmethod
    def tearDownClass(cls):
        cls._session.close()

    def test_run_started_then_finished(self):
        self.assertEqual(self.msgs[0]["type"], "run.started")
        self.assertEqual(self.msgs[0]["runId"], "r1")
        last = self.msgs[-1]
        self.assertEqual(last["type"], "run.finished")
        self.assertEqual(last["status"], "done")
        self.assertIsNone(last["error"])
        self.assertIn("ticks", last["metrics"])

    def test_seq_is_monotonic_and_unique(self):
        seqs = [m["seq"] for m in self.msgs if "seq" in m]
        self.assertGreater(len(seqs), 10)
        self.assertEqual(seqs, sorted(seqs), "seq non monotone")
        self.assertEqual(len(seqs), len(set(seqs)), "seq réutilisé")

    def test_every_emitted_node_id_exists_in_graph(self):
        """L'illumination du canevas (M4) ne doit désigner aucun nœud fantôme."""
        known = {n["id"] for n in self._session.graph["nodes"]}
        emitted = {m["nodeId"] for m in self.msgs
                   if m["type"] == "trace" and m.get("nodeId")}
        self.assertTrue(emitted, "aucun nodeId émis : le test ne prouve rien")
        self.assertFalse(emitted - known, f"nodeId hors graphe : {emitted - known}")

    def test_phases_are_balanced(self):
        depth = 0
        for msg in self.msgs:
            if msg["type"] != "phase":
                continue
            depth += 1 if msg["status"] == "enter" else -1
            self.assertIn(depth, (0, 1), "phases imbriquées ou déséquilibrées")
        self.assertEqual(depth, 0)

    def test_trace_kinds_belong_to_the_vocabulary(self):
        for msg in self.msgs:
            if msg["type"] == "trace":
                self.assertIn(msg["kind"], E.KINDS)

    def test_whole_stream_is_strict_json(self):
        json.dumps(self.msgs, allow_nan=False)

    def test_second_run_gets_a_fresh_run_id(self):
        s = _repo_session(self)
        q = s.subscribe()
        s.run(ticks=1)
        _pump(s, q)
        s.run(ticks=1)
        msgs = _pump(s, q)
        self.assertEqual(msgs[0]["runId"], "r2")

    def test_a_run_can_restart_as_soon_as_it_announces_its_end(self):
        """`run.finished` est émis depuis le thread du run, encore vivant :
        relancer à cet instant précis était refusé (« un run est déjà en
        cours ») — une course que la CI perdait de temps en temps."""
        s = _repo_session(self)
        q = s.subscribe()
        outcome: dict = {}

        def relaunch(msg):
            if msg["type"] == "run.finished" and "second" not in outcome:
                outcome["second"] = None
                try:
                    outcome["second"] = s.run(ticks=1)
                except AgentLError as exc:
                    outcome["second"] = exc

        s.on_event = relaunch
        s.run(ticks=1)
        _pump(s, q)
        deadline = time.monotonic() + DEADLINE
        while outcome.get("second") is None and time.monotonic() < deadline:
            time.sleep(0.01)
        s.on_event = None
        s.stop()
        self.assertEqual(outcome.get("second"), "r2")

    def test_no_second_run_slips_in_before_the_first_thread_starts(self):
        """Entre la création du thread et son `start()`, `is_alive()` est
        faux : un second `run()` passait, et deux runs partageaient la
        session."""
        s = _repo_session(self)
        s.subscribe()
        real_start = threading.Thread.start
        refused: list = []

        def start(thread):
            if thread.name.startswith("studio-run-") and not refused:
                try:
                    s.run(ticks=1)
                    refused.append(False)
                except AgentLError:
                    refused.append(True)
            return real_start(thread)

        with mock.patch.object(threading.Thread, "start", start):
            s.run(ticks=1)
        s.stop()
        self.assertEqual(refused, [True])

    def test_concurrent_run_is_refused(self):
        s = _hitl_session(self, timeout=60.0)
        q = s.subscribe()
        s.run(ticks=1)
        _pump(s, q, until="prompt", stop_on_prompt=True)
        with self.assertRaises(AgentLError):
            s.run(ticks=1)
        s.stop()


# --------------------------------------------------------------------------
# M1 — pilotage : stop, pause, pas à pas
# --------------------------------------------------------------------------
class TestSessionControl(unittest.TestCase):
    def test_stop_returns_within_one_second_even_while_prompting(self):
        """Un run bloqué sur une approbation doit rendre la main immédiatement."""
        s = _hitl_session(self, timeout=60.0)
        q = s.subscribe()
        s.run(ticks=1)
        _pump(s, q, until="prompt", stop_on_prompt=True)
        started = time.monotonic()
        stopped = s.stop(1.0)
        elapsed = time.monotonic() - started
        self.assertTrue(stopped, "le thread de run n'est pas mort en 1 s")
        self.assertLess(elapsed, 1.0)
        self.assertFalse(s.running)

    def test_stop_reports_stopped_status(self):
        s = _hitl_session(self, timeout=60.0)
        q = s.subscribe()
        s.run(ticks=1)
        msgs = _pump(s, q, until="prompt", stop_on_prompt=True)
        s.stop()
        msgs += _pump(s, q)
        final = [m for m in msgs if m["type"] == "run.finished"][-1]
        self.assertEqual(final["status"], "stopped")

    def test_step_mode_pauses_then_advances_then_resumes(self):
        s = _repo_session(self)
        q = s.subscribe()
        s.run(ticks=2, step_mode=True)
        seen = _pump(s, q, until="run.paused")
        self.assertTrue(s.paused)
        self.assertTrue([m for m in seen if m["type"] == "run.paused"])
        s.step()
        self.assertTrue([m for m in _pump(s, q, until="run.resumed")
                         if m["type"] == "run.resumed"])
        s.resume()
        _pump(s, q)
        self.assertFalse(s.running)

    def test_pause_suspends_a_running_run(self):
        s = _repo_session(self)
        q = s.subscribe()
        # Allure de démonstration : le run dure assez pour que `pause()` tombe
        # forcément *pendant* l'exécution — sans elle, un run de 6 ticks peut
        # s'achever avant la demande et « run.paused » n'arrive jamais.
        s.run(ticks=6, pace=0.05)
        s.pause()
        _pump(s, q, until="run.paused")
        self.assertTrue(s.paused)
        s.set_pace(0.0)
        s.resume()
        _pump(s, q)


# --------------------------------------------------------------------------
# M1 — boucle humaine (HITL)
# --------------------------------------------------------------------------
class TestHumanInTheLoop(unittest.TestCase):
    """Le studio EST l'approbateur : sa réponse fait foi, son silence refuse."""

    def test_prompt_is_emitted_with_node_and_payload(self):
        s = _hitl_session(self, timeout=0.3)
        q = s.subscribe()
        s.run(ticks=1)
        msgs = _pump(s, q)
        prompts = [m for m in msgs if m["type"] == "prompt"]
        self.assertEqual(len(prompts), 1)
        prompt = prompts[0]
        self.assertEqual(prompt["mode"], "approve")
        self.assertEqual(prompt["nodeId"], "tool:flip")
        self.assertEqual(prompt["payload"]["tool"], "flip")
        self.assertIn("flip", prompt["question"])
        known = {n["id"] for n in s.graph["nodes"]}
        self.assertIn(prompt["nodeId"], known)

    def test_reply_true_authorises_the_action(self):
        s = _hitl_session(self, timeout=10.0)
        q = s.subscribe()
        s.run(ticks=1)
        msgs = _pump(s, q, reply=True)
        final = [m for m in msgs if m["type"] == "run.finished"][-1]
        self.assertEqual(final["metrics"]["approvals"], 1)
        self.assertEqual(final["metrics"]["blocked"], 0)
        self.assertFalse([m for m in msgs if m["type"] == "trace"
                          and m["kind"] == "BLOCKED"])

    def test_reply_false_blocks_the_action(self):
        s = _hitl_session(self, timeout=10.0)
        q = s.subscribe()
        s.run(ticks=1)
        msgs = _pump(s, q, reply=False)
        final = [m for m in msgs if m["type"] == "run.finished"][-1]
        # `approvals` compte les demandes ; `blocked` compte les refus.
        self.assertEqual(final["metrics"]["blocked"], 1)
        self.assertTrue(any(m["type"] == "trace" and m["kind"] == "BLOCKED"
                            and "non approuvé" in m["text"] for m in msgs))

    def test_timeout_fails_closed(self):
        """Sans réponse, l'action est refusée — jamais autorisée par défaut."""
        s = _hitl_session(self, timeout=0.2)
        q = s.subscribe()
        started = time.monotonic()
        s.run(ticks=1)
        msgs = _pump(s, q)
        self.assertLess(time.monotonic() - started, DEADLINE)
        final = [m for m in msgs if m["type"] == "run.finished"][-1]
        self.assertEqual(final["metrics"]["blocked"], 1)
        self.assertEqual(final["metrics"]["tool_calls"], 0)
        blocked = [m for m in msgs if m["type"] == "trace"
                   and m["kind"] == "BLOCKED"]
        self.assertTrue(blocked, "aucun refus tracé après expiration")
        self.assertTrue(any("repli fermé" in m["detail"] for m in blocked))

    def test_reply_to_unknown_prompt_is_false_not_crash(self):
        s = _hitl_session(self, timeout=0.2)
        self.assertFalse(s.reply("p999", True))


# --------------------------------------------------------------------------
# M2 — serveur HTTP
# --------------------------------------------------------------------------
def _client(root: Path, file: str | None = None, allow_save: bool = True):
    """`TestClient` du studio. Toujours utilisé dans un `with` : sans le
    lifespan, la pompe d'événements ne tourne pas et `/ws` se fige."""
    from fastapi.testclient import TestClient

    from agentl.studio.server import create_app

    return TestClient(create_app(file, root=root, allow_save=allow_save))


class TestServerRoutes(unittest.TestCase):
    """Les routes du §4 répondent, et leur forme est celle du contrat."""

    def test_index_and_health(self):
        with _client(ROOT) as c:
            page = c.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("text/html", page.headers["content-type"])
            self.assertEqual(c.get("/api/health").json()["ok"], True)

    def test_session_shape(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            data = c.get("/api/session").json()
        for key in ("file", "source", "rev", "graph", "diags", "hostStatus",
                    "running"):
            self.assertIn(key, data)
        self.assertEqual(data["file"], "examples/soc_analyst.agent")
        self.assertFalse(data["running"])
        self.assertTrue(data["graph"]["nodes"])
        self.assertNotIn("hosts", data, "l'hôte ne se choisit plus")
        self.assertEqual(data["host"], "examples/soc_analyst.py")

    def test_files_lists_agents_only(self):
        """Plus de liste d'hôtes : la norme de nommage désigne le seul valable."""
        with _client(ROOT) as c:
            data = c.get("/api/files").json()
        self.assertIn("examples/soc_analyst.agent", data["files"])
        self.assertNotIn("hosts", data)

    def test_open_check_source_and_verify(self):
        with _client(ROOT) as c:
            opened = c.post("/api/open",
                            json={"path": "examples/soc_analyst.agent"})
            self.assertEqual(opened.status_code, 200)
            rev = opened.json()["rev"]

            checked = c.post("/api/check", json={"text": "AGENT a {"})
            self.assertEqual(checked.status_code, 200)
            self.assertTrue(checked.json()["diags"])

            edited = c.post("/api/source",
                            json={"text": opened.json()["source"], "rev": rev})
            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["rev"], rev + 1)

            report = c.post("/api/verify", json={"depth": 2})
            self.assertEqual(report.status_code, 200)
            body = report.json()
            for key in ("report", "theorems", "refuted"):
                self.assertIn(key, body)

    def test_open_missing_file_is_404(self):
        with _client(ROOT) as c:
            r = c.post("/api/open", json={"path": "examples/nope.agent"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.json())

    def test_stale_revision_is_409(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            rev = c.get("/api/session").json()["rev"]
            r = c.post("/api/source", json={"text": "AGENT a { VERSION \"1\" }",
                                            "rev": rev - 1})
        self.assertEqual(r.status_code, 409)
        self.assertIn("rev", r.json())

    def test_traversal_is_403_on_every_route(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            for route, payload in (
                    ("/api/open", {"path": "../../etc/passwd"}),
                    ("/api/open", {"path": "/etc/passwd"}),
                    ("/api/save", {"path": "../escaped.agent"}),
                    ("/api/save", {"path": "/tmp/escaped.agent"})):
                with self.subTest(route=route, path=payload["path"]):
                    r = c.post(route, json=payload)
                    self.assertEqual(r.status_code, 403)
                    self.assertIn("error", r.json())

    def test_invalid_json_is_400(self):
        with _client(ROOT) as c:
            r = c.post("/api/source", content=b"{ pas du json",
                       headers={"content-type": "application/json"})
            self.assertEqual(r.status_code, 400)
            # Un JSON valide mais non-objet est refusé de même.
            self.assertEqual(c.post("/api/source", json=[1, 2]).status_code, 400)

    def test_bad_field_types_are_400(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            self.assertEqual(
                c.post("/api/source", json={"text": 42}).status_code, 400)
            self.assertEqual(
                c.post("/api/source", json={"text": "x", "rev": "7"}).status_code,
                400)
            self.assertEqual(
                c.post("/api/verify", json={"depth": 99}).status_code, 400)
            self.assertEqual(
                c.post("/api/open", json={"path": ""}).status_code, 400)

    def test_oversized_body_is_413(self):
        from agentl.studio.server import MAX_SOURCE_BYTES

        payload = b'{"text": "' + b"x" * (MAX_SOURCE_BYTES + 16) + b'"}'
        with _client(ROOT) as c:
            r = c.post("/api/source", content=payload,
                       headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_errors_never_leak_a_traceback(self):
        with _client(ROOT) as c:
            for r in (c.post("/api/open", json={"path": "../x"}),
                      c.post("/api/source", content=b"{",
                             headers={"content-type": "application/json"}),
                      c.post("/api/verify", json={"text": "AGENT a {"})):
                message = json.dumps(r.json())
                self.assertNotIn("Traceback", message)
                self.assertNotIn("site-packages", message)


class TestServerSaving(unittest.TestCase):
    """`/api/save` est la seule écriture disque — et `--no-save` la ferme."""

    def test_save_writes_inside_root(self):
        root = _copy_root(self, "soc_analyst.agent")
        with _client(root, "soc_analyst.agent") as c:
            c.post("/api/source", json={"text": "AGENT S { VERSION \"9\" }"})
            r = c.post("/api/save", json={"path": "copie.agent"})
            self.assertEqual(r.status_code, 200)
        written = (root / "copie.agent").read_text(encoding="utf-8")
        self.assertIn("VERSION \"9\"", written)

    def test_no_save_forbids_the_route(self):
        root = _copy_root(self, "soc_analyst.agent")
        with _client(root, "soc_analyst.agent", allow_save=False) as c:
            r = c.post("/api/save", json={})
        self.assertEqual(r.status_code, 403)
        self.assertIn("--no-save", r.json()["error"])
        self.assertNotIn("copie.agent", [p.name for p in root.iterdir()])

    def test_save_onto_a_directory_is_400(self):
        root = _copy_root(self, "soc_analyst.agent")
        (root / "dossier").mkdir()
        with _client(root, "soc_analyst.agent") as c:
            r = c.post("/api/save", json={"path": "dossier"})
        self.assertEqual(r.status_code, 400)


# --------------------------------------------------------------------------
# M2 — websocket
# --------------------------------------------------------------------------
def _handshake(ws) -> list:
    """Consomme les quatre messages de resynchronisation initiale."""
    return [json.loads(ws.receive_text()) for _ in range(4)]


def _await_ws(ws, kind: str, limit: int = 4000) -> dict:
    for _ in range(limit):
        msg = json.loads(ws.receive_text())
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"« {kind} » jamais reçu sur le websocket")


class TestWebsocket(unittest.TestCase):
    def test_handshake_gives_a_full_snapshot(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as ws:
                msgs = _handshake(ws)
        self.assertEqual([m["type"] for m in msgs],
                         ["hello", "source", "diagnostics", "graph"])
        self.assertEqual(msgs[0]["session"]["file"],
                         "examples/soc_analyst.agent")
        self.assertTrue(msgs[3]["graph"]["nodes"])

    def test_ping_pong(self):
        with _client(ROOT) as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "ping"}))
                self.assertEqual(json.loads(ws.receive_text())["type"], "pong")

    def test_unknown_and_malformed_messages_never_kill_the_loop(self):
        """Une commande fautive doit répondre `error`, pas fermer le canal."""
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                for raw in ('{"type":"inconnue"}',
                            '{"type":"reply","promptId":7}',
                            '{"type":"edit","text":42}',
                            '{"type":"run","ticks":"beaucoup"}',
                            'ceci n est pas du json',
                            '[1,2,3]',
                            '{}'):
                    with self.subTest(raw=raw):
                        ws.send_text(raw)
                        reply = json.loads(ws.receive_text())
                        self.assertEqual(reply["type"], "error")
                        self.assertIsInstance(reply["message"], str)
                # Le canal est toujours vivant après toutes ces fautes.
                ws.send_text(json.dumps({"type": "ping"}))
                self.assertEqual(json.loads(ws.receive_text())["type"], "pong")

    def test_edit_is_broadcast_to_two_clients(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
                _handshake(a)
                _handshake(b)
                a.send_text(json.dumps({"type": "edit",
                                        "text": "AGENT DEUX { VERSION \"2\" }"}))
                first = _await_ws(a, "source")
                second = _await_ws(b, "source")
        self.assertIn("AGENT DEUX", first["text"])
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(first["rev"], second["rev"])

    def test_stale_edit_by_ws_answers_error(self):
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "edit", "text": "AGENT a {}",
                                         "rev": 0}))
                reply = _await_ws(ws, "error")
        self.assertIn("révision", reply["message"])

    def test_full_run_over_websocket(self):
        """Bout en bout : `run` → ticks, phases, traces, `run.finished`."""
        with _client(ROOT, "examples/soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "run", "ticks": 2}))
                seen: list = []
                for _ in range(4000):
                    seen.append(json.loads(ws.receive_text()))
                    if seen[-1]["type"] == "run.finished":
                        break
        kinds = {m["type"] for m in seen}
        self.assertEqual(seen[0]["type"], "run.started")
        self.assertEqual(seen[-1]["type"], "run.finished")
        self.assertEqual(seen[-1]["status"], "done")
        self.assertTrue({"tick", "phase", "trace", "state"} <= kinds)

    def test_run_of_a_broken_program_is_refused_over_ws(self):
        with _client(ROOT, "examples/broken.agent") as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "run", "ticks": 1}))
                reply = _await_ws(ws, "error")
        self.assertIn("analyse statique", reply["message"])

    def test_ws_save_honours_the_write_policy(self):
        root = _copy_root(self, "soc_analyst.agent")
        with _client(root, "soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "save", "path": "ws.agent"}))
                reply = _await_ws(ws, "saved")
        self.assertEqual(reply["path"], "ws.agent")
        self.assertTrue((root / "ws.agent").is_file())

        with _client(root, "soc_analyst.agent", allow_save=False) as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "save", "path": "refuse.agent"}))
                reply = _await_ws(ws, "error")
        self.assertIn("--no-save", reply["message"])
        self.assertFalse((root / "refuse.agent").exists())

    def test_ws_save_refuses_traversal(self):
        root = _copy_root(self, "soc_analyst.agent")
        with _client(root, "soc_analyst.agent") as c:
            with c.websocket_connect("/ws") as ws:
                _handshake(ws)
                ws.send_text(json.dumps({"type": "save",
                                         "path": "../evade.agent"}))
                reply = _await_ws(ws, "error")
        self.assertIn("hors du répertoire", reply["message"])
        self.assertFalse((root.parent / "evade.agent").exists())


class TestServerSerialisation(unittest.TestCase):
    def test_json_safe_drops_non_finite_floats(self):
        from agentl.studio.server import json_safe

        payload = json_safe({"a": float("nan"), "b": [float("inf"), 1.5],
                             "c": {Symbol("s")}})
        json.dumps(payload, allow_nan=False)
        self.assertIsNone(payload["a"])
        self.assertEqual(payload["b"], [None, 1.5])

    def test_json_safe_is_total(self):
        class Opaque:
            pass

        self.assertIsInstance(json_safe_of(Opaque()), str)


def json_safe_of(value):
    from agentl.studio.server import json_safe

    return json_safe(value)


# --------------------------------------------------------------------------
# M3/M4/M5 — coque statique
# --------------------------------------------------------------------------
class TestStaticAssets(unittest.TestCase):
    """La coque doit être servie, complète, et fonctionner hors ligne."""

    #: Seul « http » toléré : l'espace de noms SVG, qui n'est jamais résolu.
    ALLOWED = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink",
               "http://www.w3.org/1999/xhtml")

    def _assets(self) -> list[Path]:
        files = [STATIC / "index.html", STATIC / "css" / "studio.css"]
        files += [STATIC / "js" / name for name in JS_MODULES]
        return files

    def test_every_asset_is_served_with_200(self):
        with _client(ROOT) as c:
            self.assertEqual(c.get("/").status_code, 200)
            for name in JS_MODULES:
                with self.subTest(module=name):
                    r = c.get(f"/static/js/{name}")
                    self.assertEqual(r.status_code, 200)
                    self.assertTrue(r.text.strip())
            self.assertEqual(c.get("/static/css/studio.css").status_code, 200)

    def test_no_external_resource_anywhere(self):
        """Air-gap : aucun CDN, aucune police distante, aucun script externe."""
        pattern = re.compile(r"https?://[^\s\"'()<>]+")
        for path in self._assets():
            with self.subTest(asset=path.name):
                self.assertTrue(path.is_file(), f"{path} manquant")
                for url in pattern.findall(path.read_text(encoding="utf-8")):
                    self.assertTrue(
                        url.startswith(self.ALLOWED),
                        f"ressource externe dans {path.name} : {url}")

    def test_html_declares_no_external_subresource(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for attr in re.findall(r'(?:src|href)\s*=\s*"([^"]+)"', html):
            with self.subTest(ref=attr):
                self.assertFalse(attr.startswith(("http:", "https:", "//")),
                                 f"sous-ressource externe : {attr}")

    def test_relative_imports_resolve_to_existing_files(self):
        pattern = re.compile(r"""(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]""")
        checked = 0
        for name in JS_MODULES:
            path = STATIC / "js" / name
            for target in pattern.findall(path.read_text(encoding="utf-8")):
                with self.subTest(module=name, imports=target):
                    self.assertTrue(target.startswith("."),
                                    "import nu (résolu par un bundler absent)")
                    self.assertTrue((path.parent / target).resolve().is_file(),
                                    f"{name} importe {target}, introuvable")
                    checked += 1
        self.assertGreater(checked, 0, "aucun import détecté : test tautologique")

    def test_javascript_parses(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node absent : vérification syntaxique impossible")
        for name in JS_MODULES:
            with self.subTest(module=name):
                proc = subprocess.run(
                    [node, "--check", str(STATIC / "js" / name)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_theme_toggle_beats_the_media_query(self):
        """§1 : `data-theme` doit gagner dans les deux sens sur `prefers-*`."""
        css = (STATIC / "css" / "studio.css").read_text(encoding="utf-8")
        self.assertIn("prefers-color-scheme", css)
        self.assertIn('data-theme="dark"', css)
        self.assertIn('data-theme="light"', css)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
class TestCli(unittest.TestCase):
    def test_studio_help_works(self):
        proc = subprocess.run([sys.executable, "-m", "agentl", "studio", "--help"],
                              cwd=str(ROOT), capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--port", "--host", "--no-save", "--root", "--open"):
            self.assertIn(flag, proc.stdout)

    def test_cli_imports_without_fastapi_or_uvicorn(self):
        """Le studio est optionnel : la CLI doit s'importer sans ses paquets."""
        code = (
            "import sys\n"
            "class Block:\n"
            "    def find_module(self, name, path=None):\n"
            "        return self.find_spec(name, path)\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('fastapi', 'uvicorn', 'starlette'):\n"
            "            raise ImportError(name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Block())\n"
            "for mod in [m for m in sys.modules\n"
            "            if m.split('.')[0] in ('fastapi','uvicorn','starlette')]:\n"
            "    del sys.modules[mod]\n"
            "import agentl.cli\n"
            "assert hasattr(agentl.cli, 'main')\n"
            "print('ok')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()

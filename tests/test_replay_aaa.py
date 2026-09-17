"""Tests AAA du REJEU (replay.py).

Le rejeu est le socle de l'argument d'auditabilité : « reproduisez cette
décision ». Un test qui se contente de vérifier que le rejeu *ne plante pas*
ne prouve rien — les propriétés visées sont donc adverses :

  * **fidélité** : la trace rejouée est identique caractère pour caractère,
    sur les exemples réels du dépôt, y compris une société multi-agents ;
  * **les pannes se rejouent en pannes** : un capteur qui levait lève encore,
    avec le même nom de classe — sans quoi la ligne `ERROR` de la trace
    diffère ;
  * **la divergence est bruyante** : journal tronqué, ordre changé, arguments
    changés, valeur falsifiée — chacun doit produire une erreur ou un verdict
    d'écart, jamais un rejeu qui « s'arrange » ;
  * **le rejeu ne touche pas au monde** : `ReplayHost` n'a ni capteur ni
    outil, et refuse l'émission d'un événement neuf ;
  * **l'aller-retour JSON conserve les types** : `Symbol` n'est pas `str`, et
    une valeur qui ne sait pas se sérialiser déclare le journal lacunaire.

    python -m pytest tests/test_replay_aaa.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import Host, MockLLM, Runtime, Symbol
from agentl.parser import parse_file
from agentl.replay import (Journal, RecordingHost, RecordingLLM,
                           ReplayDivergence, ReplayError, ReplayHost,
                           ReplayLLM, decode, encode, sha256, verify_trace)
from agentl.society import Society

EXAMPLES = ROOT / "examples"

#: Exemples mono-agent dotés d'un hôte, hors bancs d'essai de l'analyseur.
REPLAYABLE = ("soc_analyst", "disk_sentinel", "maintenance", "soc_risk",
              "service_medic", "supervisor", "idor_hunter")


def _load_host(name: str):
    path = EXAMPLES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"replay_host_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.build()
    if isinstance(built, tuple):
        host, llm = (list(built) + [None, None])[:2]
    else:
        host, llm = built, None
    return host, llm or MockLLM()


def _record(name: str, ticks: int = 3):
    """Exécute l'exemple pour de vrai, en journalisant sa frontière."""
    program = parse_file(str(EXAMPLES / f"{name}.agent"))
    host, llm = _load_host(name)
    journal = Journal(meta={"source_sha256": "n/a"})
    runtime = Runtime(program.agents[0], RecordingHost(host, journal),
                      RecordingLLM(llm, journal), echo=False)
    runtime.run(max_ticks=ticks)
    trace = runtime.trace.render()
    journal.seal(trace)
    return program, journal, runtime, trace


def _replay(program, journal, ticks: int = 3):
    # `strict=False` : l'aller-retour JSON est ici un test de *fidélité de
    # sérialisation*, pas d'intégrité. Certains appelants passent exprès un
    # journal falsifié, dont la chaîne est déjà réputée rompue.
    journal = Journal.loads(journal.dumps(), strict=False) \
        if isinstance(journal, Journal) else journal
    runtime = Runtime(program.agents[0], ReplayHost(journal),
                      ReplayLLM(journal), echo=False)
    runtime.run(max_ticks=ticks)
    return journal, runtime, runtime.trace.render()


# --------------------------------------------------------------------------
# M1 — fidélité
# --------------------------------------------------------------------------
class TestFidelity(unittest.TestCase):

    def test_examples_replay_character_for_character(self):
        """La propriété centrale, sur les exemples réels du dépôt."""
        for name in REPLAYABLE:
            with self.subTest(name):
                program, journal, runtime, trace = _record(name)
                replayed, rt2, trace2 = _replay(program, journal)
                self.assertEqual(trace, trace2, f"{name} : trace divergente")
                self.assertTrue(verify_trace(replayed, trace2))
                self.assertTrue(replayed.exhausted,
                                f"{name} : {replayed.remaining} entrées non "
                                f"consommées — le rejeu a fait moins")
                self.assertEqual(runtime.metrics, rt2.metrics)

    def test_replay_needs_no_world(self):
        """Un auditeur rejoue sans accès au système d'origine."""
        program, journal, _, trace = _record("soc_analyst")
        replayed = Journal.loads(journal.dumps())
        host = ReplayHost(replayed)
        self.assertFalse(hasattr(host, "sensors"))
        self.assertFalse(hasattr(host, "tools"))
        _, _, trace2 = _replay(program, replayed)
        self.assertEqual(trace, trace2)

    def test_society_replays_identically(self):
        program = parse_file(str(EXAMPLES / "soc_team.agent"))
        host, llm = _load_host("soc_team")
        names = [a.name for a in program.agents]
        hosts = host if isinstance(host, dict) else {n: host for n in names}
        j = Journal(meta={"source_sha256": "n/a"})
        society = Society(
            program.agents,
            hosts={n: RecordingHost(h, j) for n, h in hosts.items()},
            llms={n: RecordingLLM(llm, j) for n in names}, echo=False)
        society.run(max_ticks=4)
        trace = society.render_traces()
        j.seal(trace)

        r = Journal.loads(j.dumps())
        replayed = Society(program.agents,
                           hosts={n: ReplayHost(r) for n in names},
                           llms={n: ReplayLLM(r) for n in names}, echo=False)
        replayed.run(max_ticks=4)
        self.assertEqual(trace, replayed.render_traces())
        self.assertTrue(r.exhausted)

    def test_journal_survives_the_json_round_trip(self):
        program, journal, _, trace = _record("soc_analyst")
        text = journal.dumps()
        json.loads(text)                       # JSON valide, pas du repr
        reloaded = Journal.loads(text)
        self.assertEqual(len(reloaded.entries), len(journal.entries))
        _, _, trace2 = _replay(program, reloaded)
        self.assertEqual(trace, trace2)


# --------------------------------------------------------------------------
# M2 — les pannes se rejouent en pannes
# --------------------------------------------------------------------------
class TestFailuresReplay(unittest.TestCase):

    def _boom_agent(self):
        src = """
AGENT BOOM {
    VERSION "1"
    OBSERVE { svc.state }
    BELIEF { svc.state = unknown CONFIDENCE 0.5 SOURCE prior }
    GOAL up { MAINTAIN svc.state == healthy }
    TOOL restart { OUTPUT { ok: Symbol } RISK LOW EFFECT { svc.state = healthy } }
    POLICY { DEFAULT DENY  ALLOW restart }
    PLAN fix WHEN svc.state != healthy { STEP go { restart() } }
    LOOP UNTIL goal.satisfied MAX 2 {
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY
    }
}
"""
        path = Path(self.tmp) / "boom.agent"
        path.write_text(src, encoding="utf-8")
        return parse_file(str(path))

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def test_a_sensor_that_raised_still_raises_with_the_same_class_name(self):
        program = self._boom_agent()
        host = Host()

        @host.sensor("svc.state")
        def _boom():
            raise ZeroDivisionError("capteur en panne")

        @host.tool("restart")
        def _restart():
            return {"ok": Symbol("done")}

        j = Journal(meta={"source_sha256": "n/a"})
        rt = Runtime(program.agents[0], RecordingHost(host, j),
                     RecordingLLM(MockLLM(), j), echo=False)
        rt.run(max_ticks=2)
        trace = rt.trace.render()
        j.seal(trace)
        # La panne doit être *dans* la trace : sinon le test ne prouve rien.
        self.assertIn("ZeroDivisionError", trace)

        r = Journal.loads(j.dumps())
        rt2 = Runtime(program.agents[0], ReplayHost(r), ReplayLLM(r), echo=False)
        rt2.run(max_ticks=2)
        self.assertEqual(trace, rt2.trace.render())

    def test_a_tool_that_raised_still_raises(self):
        program = self._boom_agent()
        host = Host()
        host.sensors["svc.state"] = lambda: Symbol("down")

        @host.tool("restart")
        def _restart():
            raise RuntimeError("service injoignable")

        j = Journal(meta={"source_sha256": "n/a"})
        rt = Runtime(program.agents[0], RecordingHost(host, j),
                     RecordingLLM(MockLLM(), j), echo=False)
        rt.run(max_ticks=2)
        trace = rt.trace.render()
        j.seal(trace)
        self.assertIn("service injoignable", trace)

        r = Journal.loads(j.dumps())
        rt2 = Runtime(program.agents[0], ReplayHost(r), ReplayLLM(r), echo=False)
        rt2.run(max_ticks=2)
        self.assertEqual(trace, rt2.trace.render())


# --------------------------------------------------------------------------
# M3 — la divergence est bruyante
# --------------------------------------------------------------------------
class TestDivergenceIsLoud(unittest.TestCase):

    # `strict=False` dans ce groupe : la chaîne de hachage refuserait ces
    # journaux dès le chargement (c'est son rôle, cf. TestSealedJournal). Ce
    # qu'on vérifie ici est la seconde ligne de défense — le rejeu lui-même
    # diverge, même si la chaîne avait été recalculée par le falsificateur.

    def test_truncated_journal_raises(self):
        program, journal, _, _ = _record("soc_analyst")
        raw = json.loads(journal.dumps())
        raw["entries"] = raw["entries"][:2]
        short = Journal.loads(json.dumps(raw), strict=False)
        with self.assertRaises(ReplayDivergence):
            _replay(program, short)

    def test_reordered_journal_raises(self):
        program, journal, _, _ = _record("soc_analyst")
        raw = json.loads(journal.dumps())
        raw["entries"][0], raw["entries"][1] = raw["entries"][1], raw["entries"][0]
        swapped = Journal.loads(json.dumps(raw), strict=False)
        with self.assertRaises(ReplayDivergence):
            _replay(program, swapped)

    def test_tampered_value_changes_the_trace_digest(self):
        """Falsifier une lecture ne fait pas échouer le rejeu — il produit une
        autre trace. C'est l'empreinte scellée qui l'attrape."""
        program, journal, _, trace = _record("soc_analyst")
        raw = json.loads(journal.dumps())
        for entry in raw["entries"]:
            if entry["kind"] == "read" and isinstance(entry.get("value"), int):
                entry["value"] = entry["value"] + 1000
                break
        else:
            self.skipTest("aucune lecture entière à falsifier")
        tampered = Journal.loads(json.dumps(raw), strict=False)
        _, _, trace2 = _replay(program, tampered)
        self.assertNotEqual(trace, trace2)
        self.assertFalse(verify_trace(tampered, trace2))

    def test_different_tool_arguments_diverge(self):
        journal = Journal()
        journal.record("invoke", "restart", args={"service": "web"},
                       value={"ok": True})
        host = ReplayHost(Journal.loads(journal.dumps()))
        with self.assertRaises(ReplayDivergence):
            host.invoke("restart", {"service": "db"})

    def test_unsealed_journal_cannot_claim_conformity(self):
        with self.assertRaises(ReplayError):
            verify_trace(Journal(), "peu importe")

    def test_unknown_journal_format_is_refused(self):
        with self.assertRaises(ReplayError):
            Journal.loads(json.dumps({"meta": {"format": 999}, "entries": []}))


# --------------------------------------------------------------------------
# M4 — le rejeu ne touche pas au monde
# --------------------------------------------------------------------------
class TestReplayIsInert(unittest.TestCase):

    def test_emitting_an_event_during_replay_is_refused(self):
        with self.assertRaises(ReplayError):
            ReplayHost(Journal()).emit("wazuh", alert=1)

    def test_an_unregistered_subagent_stays_unregistered(self):
        journal = Journal()
        journal.record("delegate_lookup", "SCRIBE", value=False)
        replayed = Journal.loads(journal.dumps())
        self.assertIsNone(ReplayHost(replayed).subagents.get("SCRIBE"))

    def test_recording_host_stays_transparent(self):
        """L'enregistrement enveloppe, il ne remplace pas : les attributs
        métier de l'hôte restent joignables."""
        host = Host()
        host.numero_de_ticket = 42
        wrapped = RecordingHost(host, Journal())
        self.assertEqual(wrapped.numero_de_ticket, 42)
        self.assertIs(wrapped.events, host.events)


# --------------------------------------------------------------------------
# M5 — encodage
# --------------------------------------------------------------------------
class TestEncoding(unittest.TestCase):

    def test_symbol_is_not_a_string_after_the_round_trip(self):
        value = decode(encode(Symbol("healthy")))
        self.assertIsInstance(value, Symbol)
        self.assertEqual(value, Symbol("healthy"))

    def test_nested_structures_survive(self):
        original = {"n": 3, "xs": [1.5, Symbol("HIGH"), None],
                    "d": {"k": True}, "t": (1, 2)}
        self.assertEqual(decode(encode(original)), original)

    def test_non_finite_floats_survive(self):
        for text in ("inf", "-inf"):
            self.assertEqual(decode(encode(float(text))), float(text))
        self.assertNotEqual(decode(encode(float("nan"))),
                            decode(encode(float("nan"))))   # NaN != NaN

    def test_dict_keys_that_are_not_strings_survive(self):
        original = {Symbol("HIGH"): 1, 2: "deux"}
        self.assertEqual(decode(encode(original)), original)

    def test_an_opaque_value_declares_the_journal_lossy(self):
        class Opaque:
            def __repr__(self):
                return "<opaque>"

        journal = Journal()
        journal.record("invoke", "f", value=Opaque())
        self.assertTrue(journal.lossy, "valeur non sérialisable non signalée")
        self.assertIn("lossy", journal.to_json()["meta"])
        # Elle reste lisible dans une trace — c'est tout ce qu'on lui promet.
        self.assertEqual(repr(decode(journal.entries[0].value)), "<opaque>")

    def test_sha256_is_stable(self):
        self.assertEqual(sha256("abc"), sha256("abc"))
        self.assertNotEqual(sha256("abc"), sha256("abd"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()

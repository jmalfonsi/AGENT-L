"""Régressions de la 1.17 : fiabilisation du cœur.

Chaque section reprend une sonde qui échouait avant le correctif.

1. **Garde de politique sur un nom simple non lié.** Un identifiant nu que
   rien ne résout vaut la constante symbolique du même nom. C'est voulu pour
   `open` ou `contained` ; c'était un contournement pour une variable que le
   programme n'a pas encore liée : `ALLOW wipe IF approved` autorisait sans
   que personne n'ait posé `approved`, `NEVER send WHEN draft_ok == no`
   tombait tant qu'aucun brouillon n'avait été relu. Les chemins pointés
   échouaient déjà fermé.
2. **`no` est faux.** `IF approved` était vrai quand `approved = no`.
3. **`NaN` dans une égalité.** `x != 5` était vrai sur une mesure invalide.
4. **Le tick ne tombe pas** sur une donnée inattendue de l'hôte ou sur une
   expression inévaluable — y compris après que l'outil a réellement agi.
5. **Une évidence sans mesure reste indéterminée.** `load != high` comptait
   comme *observée vraie* tant que le capteur n'avait rien rendu : l'hypothèse
   se confirmait sur une absence de donnée.
6. **Examiner un journal durable ne l'écrit pas**, et un journal abîmé est
   refusé comme tel plutôt que par une erreur interne.
8. **T1 ne tient plus une garde de plan pour vraie après un autre plan.** Les
   plans co-sélectionnés s'exécutent à la suite sans re-vérifier leur `WHEN`.
7. **Les charges utiles de société sont copiées en profondeur** par
   destinataire ; la règle d'arrêt, elle, est inchangée (décision du 2026-11-12).

    python -m pytest tests/test_v117_regressions.py -q
"""
from __future__ import annotations

import pytest

from agentl import Analyzer, Host, MockLLM, Symbol, parse_source
from agentl.core import UNDEFINED, truthy
from agentl.policy import (ALLOWED, DENIED, ActionRequest, PolicyEngine,
                           state_names)
from agentl.runtime import Runtime
from agentl.state import Evaluator, State
from agentl.trivalent import UNKNOWN, evaluate


# ====================================================================
# 1. Garde de politique sur un nom simple non lié
# ====================================================================
AGENT = """
AGENT a {
  OBSERVE { m.x }
  TOOL wipe { INPUT { target: String } RISK HIGH }
  TOOL ping { RISK LOW }
  POLICY {
    %s
  }
  %s
  PLAN p WHEN tick.go != no {
    STEP s { %s wipe(target="disk") }
  }
}
"""


def run(policy, before="", extra="", ticks=1, sensor=None):
    agent = parse_source(AGENT % (policy, extra, before)).agents[0]
    calls = []
    host = Host()
    host.tool("wipe")(lambda target: calls.append(target) or {})
    host.tool("ping")(lambda: {})
    if sensor is not None:
        host.sensors["m.x"] = sensor
    rt = Runtime(agent, host, MockLLM())
    rt.run(max_ticks=ticks)
    return calls, rt


def guard(text, state=None, variables=()):
    """Valeur trivalente d'une garde, hors de tout agent."""
    source = "AGENT g { TOOL t { RISK LOW } POLICY { NEVER t WHEN %s } }" % text
    node = parse_source(source).agents[0].policies[0].guard
    ev = Evaluator(state or State())
    ev.variables = frozenset(variables)
    return evaluate(ev, node)


class TestTruthPosition:
    def test_an_allow_does_not_open_on_a_name_nothing_ever_set(self):
        calls, rt = run("DEFAULT DENY\n ALLOW wipe IF approved")

        assert calls == []
        assert rt.metrics["blocked"] == 1

    def test_a_never_does_not_fall_on_the_negation_of_an_unset_name(self):
        calls, _ = run("DEFAULT ALLOW\n NEVER wipe WHEN NOT safe_mode")

        assert calls == []

    def test_a_set_name_is_read_for_what_it_holds(self):
        allowed, _ = run("DEFAULT DENY\n ALLOW wipe IF approved",
                         before="SET approved = yes")
        refused, _ = run("DEFAULT DENY\n ALLOW wipe IF approved",
                         before="SET approved = no")

        assert allowed == ["disk"]
        assert refused == []

    def test_boolean_words_keep_their_meaning(self):
        assert guard("yes") is True
        assert guard("true") is True
        assert guard("no") is False
        assert guard("false") is False
        assert guard("NOT no") is True

    def test_any_other_unset_name_is_unknown_in_both_directions(self):
        assert guard("approved") is UNKNOWN
        assert guard("NOT approved") is UNKNOWN
        assert guard("approved OR yes") is True          # Kleene : un vrai suffit
        assert guard("approved AND no") is False         # un faux suffit

    def test_the_analyzer_reports_the_frozen_rule(self):
        agent = parse_source(AGENT % (
            "DEFAULT DENY\n ALLOW wipe IF approved\n NEVER ping WHEN NOT locked",
            "", "")).agents[0]
        messages = [d.message for d in Analyzer(agent).run() if d.code == "W128"]

        assert any("`approved`" in m and "jamais accordée" in m for m in messages)
        assert any("`locked`" in m and "toujours" in m for m in messages)

    def test_the_analyzer_reports_an_always_true_allow(self):
        agent = parse_source(AGENT % (
            "DEFAULT DENY\n ALLOW wipe IF mode != safe", "", "")).agents[0]
        messages = [d.message for d in Analyzer(agent).run() if d.code == "W128"]

        assert any("toujours vrai" in m for m in messages)

    def test_no_new_warning_on_a_declared_or_boolean_name(self):
        agent = parse_source(AGENT % (
            "DEFAULT DENY\n ALLOW wipe IF approved AND yes",
            "", "SET approved = yes")).agents[0]

        assert [d for d in Analyzer(agent).run() if d.code == "W128"] == []


class TestUnboundProgramVariable:
    #: Le programme lie `klass` (un REASON le produit) : c'est une variable.
    REASON = ('REASON { TASK "classer" USING { m.x } '
              'PRODUCE { klass: Symbol IN [internal, external] } }')

    def test_state_names_are_what_the_program_binds_itself(self):
        agent = parse_source("""
AGENT a {
  OBSERVE { isolated  net.score }
  BELIEF { owner = unknown CONFIDENCE 0.1 SOURCE prior }
  TOOL scan { INPUT { host: String } OUTPUT { verdict: Symbol }
              EFFECT { scanned = yes } RISK LOW }
  PLAN p {
    STEP s {
      SET flag = yes
      SET deep.path = 1
      REASON { TASK "t" PRODUCE { klass: Symbol } }
      DELEGATE helper { TASK "t" EXPECT { advice } }
      FOREACH item IN net.score { SET inner = 1 }
    }
  }
  SCENARIO sc { GIVEN { given_only = 1 } EXPECT { flag == yes } WITHIN 1 }
}
""").agents[0]

        names = state_names(agent)

        assert {"isolated", "owner", "verdict", "scanned", "flag", "klass",
                "advice", "item", "inner"} <= names
        # Ni les chemins pointés, ni les paramètres INPUT (liés par l'appel
        # jugé), ni le monde d'essai d'un scénario.
        assert not {"net.score", "deep.path", "host", "given_only"} & names

    def test_a_never_holds_until_the_variable_is_bound(self):
        policy = "DEFAULT ALLOW\n NEVER wipe WHEN klass == external"
        # La sonde : le REASON vit dans un plan qui ne tourne pas.
        extra = "PLAN classify WHEN never.go == yes { STEP c { %s } }" % self.REASON

        calls, rt = run(policy, extra=extra)

        assert calls == []
        assert rt.metrics["blocked"] == 1

    def test_once_bound_the_comparison_decides(self):
        policy = "DEFAULT ALLOW\n NEVER wipe WHEN klass == external"

        calls, _ = run(policy, before="SET klass = internal")
        assert calls == ["disk"]
        calls, _ = run(policy, before="SET klass = external")
        assert calls == []

    def test_an_allow_is_not_satisfied_by_an_unbound_inequality(self):
        policy = "DEFAULT DENY\n ALLOW wipe IF klass != external"
        extra = "PLAN classify WHEN never.go == yes { STEP c { %s } }" % self.REASON

        assert run(policy, extra=extra)[0] == []
        assert run(policy, before="SET klass = internal")[0] == ["disk"]

    def test_membership_on_an_unbound_variable_is_unknown(self):
        state = State()
        state.set_world("blocked.list", [Symbol("a"), Symbol("b")])

        assert guard("who IN blocked.list", state, {"who"}) is UNKNOWN
        state.set_local("who", Symbol("a"))
        assert guard("who IN blocked.list", state, {"who"}) is True

    def test_a_constant_symbol_is_still_a_constant(self):
        state = State()
        state.set_world("asset.criticality", Symbol("CRITICAL"))

        # `CRITICAL`, `open` : rien ne les lie nulle part, ce sont des valeurs.
        assert guard("asset.criticality == CRITICAL", state) is True
        assert guard("asset.criticality != CRITICAL", state) is False

    def test_input_parameters_keep_the_canonical_idiom(self):
        # `target` est lié par l'appel jugé ; un outil qui n'a pas ce
        # paramètre n'est pas concerné par la règle (comportement inchangé).
        agent = parse_source(AGENT % (
            'DEFAULT ALLOW\n NEVER wipe WHEN target == "prod"\n'
            ' NEVER ping WHEN target == "prod"', "", "")).agents[0]
        engine = PolicyEngine(agent)

        assert "target" not in engine.variables
        assert engine.check(ActionRequest("wipe", {"target": "prod"}, "HIGH"),
                            State()).verdict == DENIED
        assert engine.check(ActionRequest("wipe", {"target": "tmp"}, "HIGH"),
                            State()).verdict == ALLOWED
        assert engine.check(ActionRequest("ping", {}, "LOW"),
                            State()).verdict == ALLOWED


# ====================================================================
# 2. `no` est faux
# ====================================================================
class TestNoIsFalse:
    def test_truthy_reads_the_boolean_vocabulary_of_the_language(self):
        assert truthy(Symbol("yes")) and truthy(Symbol("true"))
        assert not truthy(Symbol("no")) and not truthy(Symbol("No"))
        assert not truthy(Symbol("false")) and not truthy(Symbol("none"))
        assert truthy(Symbol("contained"))            # une valeur métier

    def test_a_plan_guard_does_not_fire_on_no(self):
        source = """
AGENT a {
  BELIEF { armed = no CONFIDENCE 1 SOURCE initial }
  TOOL fire { RISK LOW }
  PLAN p WHEN armed { STEP s { fire() } }
}
"""
        calls = []
        host = Host()
        host.tool("fire")(lambda: calls.append(1) or {})
        Runtime(parse_source(source).agents[0], host, MockLLM()).run(max_ticks=2)

        assert calls == []


# ====================================================================
# 3. NaN dans une égalité, élément indéfini dans une liste
# ====================================================================
class TestInvalidOperands:
    def test_nan_makes_an_equality_unknown(self):
        state = State()
        state.set_world("m.x", float("nan"))

        assert guard("m.x != 5", state) is UNKNOWN
        assert guard("m.x == 5", state) is UNKNOWN
        assert guard("m.x == m.x", state) is UNKNOWN

    def test_an_allow_does_not_open_on_a_nan_reading(self):
        calls, _ = run("DEFAULT DENY\n ALLOW wipe IF m.x != 5",
                       sensor=lambda: float("nan"))

        assert calls == []

    def test_a_finite_reading_still_decides(self):
        calls, _ = run("DEFAULT DENY\n ALLOW wipe IF m.x != 5",
                       sensor=lambda: 4)

        assert calls == ["disk"]

    def test_membership_with_an_undefined_element_is_kleene(self):
        state = State()
        state.set_world("ok.a", Symbol("alpha"))
        state.set_local("who", Symbol("alpha"))

        # Trouvé parmi les éléments connus : vrai. Sinon, l'élément indéfini
        # pourrait être celui-là : indéterminé, pas faux.
        assert guard("who IN [ok.a, ok.b]", state) is True
        state.set_local("who", Symbol("beta"))
        assert guard("who IN [ok.a, ok.b]", state) is UNKNOWN
        state.set_world("ok.b", Symbol("gamma"))
        assert guard("who IN [ok.a, ok.b]", state) is False


# ====================================================================
# 4. Le tick ne tombe pas
# ====================================================================
LOOP = """
AGENT a {
  OBSERVE { m.x %s }
  %s
  TOOL act { INPUT { n: Number } OUTPUT { ok: Bool } RISK LOW %s }
  %s
  PLAN p WHEN tick.go != no { STEP s { %s } }
}
"""


def live(observe="", decl="", tool="", extra="", body="act(n=1)", ticks=2,
         sensor=lambda: Symbol("oops"), setup=None, llm=None):
    """Exécute, et rend `(appels, erreurs de trace, runtime)` — sans lever."""
    source = LOOP % (observe, decl, tool, extra, body)
    agent = parse_source(source).agents[0]
    calls = []
    host = Host()
    host.tool("act")(lambda n: calls.append(n) or {"ok": True})
    if sensor is not None:
        host.sensors["m.x"] = sensor
    if setup is not None:
        setup(host)
    rt = Runtime(agent, host, llm or MockLLM())
    rt.run(max_ticks=ticks)
    errors = [e.text for e in rt.trace.events if e.kind == "ERROR"]
    return calls, errors, rt


class TestUnevaluableExpressions:
    """`m.x + 1` quand le capteur rend un symbole : EvalError. Chacune de ces
    expressions s'évaluait hors de tout filet."""

    def test_an_effect_that_cannot_be_computed_does_not_crash_after_the_action(self):
        calls, errors, rt = live(
            tool="EFFECT { job.count = job.count + 1  job.done = yes }", ticks=1)

        # L'outil a agi, son résultat est lié, la ligne TOOL existe…
        assert calls == [1]
        assert rt.metrics["tool_calls"] == 1
        assert [e for e in rt.trace.events if e.kind == "TOOL"]
        # … la postcondition incalculable n'est pas crue, l'autre si.
        assert "job.count" not in rt.state.beliefs
        assert rt.state.get("job.done") == Symbol("yes")
        assert any("EFFECT job.count" in text for text in errors)

    def test_a_reason_default(self):
        calls, errors, rt = live(
            body='REASON { TASK "t" PRODUCE { k: Number DEFAULT m.x + 1 } } act(n=1)')

        assert calls == [1, 1]
        assert any("DEFAULT de k" in text for text in errors)

    def test_an_ask_default_falls_back_to_no_answer(self):
        _, errors, rt = live(
            body='ASK operator { QUESTION "q" DEFAULT m.x + 1 }', ticks=1)

        assert rt.state.get("answer") == Symbol("no_answer")
        assert any("DEFAULT de ASK" in text for text in errors)

    def test_a_degrade_fallback_leaves_the_path_undefined(self):
        calls, errors, rt = live(observe="ON UNKNOWN DEGRADE m.y + 1",
                                 sensor=lambda: None, ticks=1)

        assert calls == [1]
        assert rt.state.get("m.x") is UNDEFINED
        assert rt.metrics["sensor_degraded"] == 0
        assert any("repli DEGRADE" in text for text in errors)

    def test_an_explains_value(self):
        calls, errors, rt = live(decl="""
  HYPOTHESIS h { PRIOR 0.9
    EVIDENCE { m.x == oops LIKELIHOOD 0.9 GIVEN_NOT 0.1 }
    THRESHOLD 0.5 EXPLAINS k.v = m.x + 1 }""", ticks=1)

        assert calls == [1]
        assert "k.v" not in rt.state.beliefs
        assert any("EXPLAINS de h" in text for text in errors)

    def test_an_initial_belief(self):
        calls, errors, rt = live(
            decl="BELIEF { b.x = -healthy CONFIDENCE 0.5 SOURCE prior }", ticks=1)

        assert calls == [1]
        assert "b.x" not in rt.state.beliefs      # pas de croyance inventée
        assert any("BELIEF b.x" in text for text in errors)

    def test_a_message_payload_is_not_sent_half_built(self):
        _, errors, rt = live(
            body="MESSAGE note { TO a PAYLOAD { v = m.x + 1 } }", ticks=1)

        assert rt.metrics["messages_sent"] == 0
        assert any("charge utile inévaluable" in text for text in errors)


class TestEvidenceStaysUndetermined:
    """Une évidence qu'on n'a pas pu observer ne déplace pas le postérieur."""

    HYPOTHESIS = """
AGENT a {
  OBSERVE { load }
  HYPOTHESIS overload {
    PRIOR 0.10
    EVIDENCE { %s LIKELIHOOD 0.90 GIVEN_NOT 0.05 }
    THRESHOLD 0.60
    EXPLAINS incident.kind
  }
  LOOP MAX 2 { OBSERVE UPDATE_BELIEFS UPDATE_HYPOTHESES }
}
"""

    def _infer(self, test, sensor=None):
        agent = parse_source(self.HYPOTHESIS % test).agents[0]
        host = Host()
        if sensor is not None:
            host.sensors["load"] = sensor
        rt = Runtime(agent, host, MockLLM())
        rt.run(max_ticks=1)
        return rt.inferences["overload"], rt

    @pytest.mark.parametrize("test", [
        "flagged",                      # nom que rien ne pose
        "NOT flagged",
        "load == high",                 # capteur déclaré, pas encore mesuré
        "load != high",
        "load IN [high, critical]",
        "NOT (load == low)",
    ])
    def test_an_unmeasured_name_is_not_an_observation(self, test):
        inference, rt = self._infer(test)

        assert inference.outcomes[0].observed is None
        assert inference.posterior == pytest.approx(0.10)
        assert not inference.supported
        assert "incident.kind" not in rt.state.beliefs      # pas d'EXPLAINS

    def test_an_invalid_measure_is_not_an_observation(self):
        inference, _ = self._infer("load != 5", sensor=lambda: float("nan"))

        assert inference.outcomes[0].observed is None

    def test_a_measured_name_is_observed_both_ways(self):
        seen, rt = self._infer("load == high", sensor=lambda: Symbol("high"))
        unseen, _ = self._infer("load == high", sensor=lambda: Symbol("low"))

        assert seen.outcomes[0].observed is True and seen.supported
        assert "incident.kind" in rt.state.beliefs
        assert unseen.outcomes[0].observed is False
        assert unseen.posterior < 0.10

    def test_a_constant_on_both_sides_still_compares(self):
        # Aucun des deux noms n'est une variable du programme : ce sont des
        # constantes, et la comparaison reste décidable.
        inference, _ = self._infer("high == high")

        assert inference.outcomes[0].observed is True


class TestPlannerFailsClosed:
    PLANNED = """
AGENT a {
  OBSERVE { m.x }
  GOAL g { ACHIEVE job.done == yes }
  TOOL act { %s EFFECT { %s } COST 1 RISK LOW }
  PLANNER { ENABLE ACHIEVE %s }
  LOOP MAX 3 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY }
}
"""

    def _run(self, requires, effect, achieve):
        agent = parse_source(self.PLANNED % (requires, effect, achieve)).agents[0]
        calls = []
        host = Host()
        host.tool("act")(lambda: calls.append(1) or {})
        host.sensors["m.x"] = lambda: Symbol("oops")
        rt = Runtime(agent, host, MockLLM())
        rt.run(max_ticks=2)
        return calls, rt

    def test_an_unevaluable_requires_is_not_established(self):
        calls, rt = self._run("REQUIRES { m.x + 1 > 0 }", "job.done = yes",
                              "job.done == yes")

        assert calls == []                       # outil non applicable
        assert rt.metrics["plans_synthesized"] == 0

    def test_an_unevaluable_goal_is_not_reached(self):
        calls, rt = self._run("", "job.done = yes", "m.x + 1 > 3")

        assert rt.metrics["ticks"] == 2          # la boucle a tenu

    def test_an_unevaluable_effect_is_not_presumed(self):
        calls, rt = self._run("", "job.count = m.x + 1  job.done = yes",
                              "job.done == yes")

        assert calls == [1]                      # l'effet calculable suffit
        assert rt.metrics["ticks"] >= 1


class TestHostBoundary:
    HANDLER = "EVENT alert { WHEN tick.go != no THEN p }"

    @pytest.mark.parametrize("event", [
        "garbage", None, 7,
        {"payload": {"a": 1}},                          # sans source
        {"source": 12, "payload": {}},                  # source non textuelle
        {"source": "alert", "payload": [1, 2]},
        {"source": "alert", "payload": 7},
        {"source": "alert", "payload": {1: 2}},         # clé non textuelle
    ])
    def test_a_malformed_event_is_dropped_and_named(self, event):
        source = """
AGENT a {
  TOOL act { INPUT { n: Number } RISK LOW }
  EVENT alert { WHEN tick.go != no THEN p }
  PLAN p { STEP s { act(n=1) } }
}
"""
        calls = []
        host = Host()
        host.tool("act")(lambda n: calls.append(n) or {})
        host.events.append(event)
        host.events.append({"source": "alert", "payload": {"a": 1}})
        rt = Runtime(parse_source(source).agents[0], host, MockLLM())
        rt.run(max_ticks=1)

        # Le fautif est écarté, le suivant est servi.
        assert calls == [1]
        assert [e.text for e in rt.trace.events if e.kind == "ERROR"] == [
            "événement non conforme ignoré"]

    def test_an_event_without_payload_is_an_empty_payload(self):
        source = """
AGENT a {
  TOOL act { INPUT { n: Number } RISK LOW }
  EVENT alert { WHEN tick.go != no THEN p }
  PLAN p { STEP s { act(n=1) } }
}
"""
        calls = []
        host = Host()
        host.tool("act")(lambda n: calls.append(n) or {})
        host.events.append({"source": "alert", "payload": None})
        host.events.append({"source": "alert"})
        rt = Runtime(parse_source(source).agents[0], host, MockLLM())
        rt.run(max_ticks=1)

        assert calls == [1]            # le plan est mis en file une fois
        assert not [e for e in rt.trace.events if e.kind == "ERROR"]


class TestDisplayDoesNotDriveTheRuntime:
    def test_an_ascii_terminal_does_not_stop_the_agent(self, monkeypatch):
        import io
        import sys

        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        monkeypatch.setattr(sys, "stdout", stream)
        source = """
AGENT a { TOOL act { INPUT { n: Number } RISK LOW }
  PLAN p WHEN tick.go != no { STEP s { act(n=1) } } }
"""
        calls = []
        host = Host()
        host.tool("act")(lambda n: calls.append(n) or {})
        rt = Runtime(parse_source(source).agents[0], host, MockLLM(), echo=True)
        rt.run(max_ticks=2)
        stream.flush()

        assert calls == [1, 1]
        assert b"tick 1" in stream.buffer.getvalue()

    def test_a_closed_stream_mutes_the_echo_not_the_loop(self, monkeypatch):
        import io
        import sys

        stream = io.StringIO()
        stream.close()
        monkeypatch.setattr(sys, "stdout", stream)
        source = """
AGENT a { TOOL act { INPUT { n: Number } RISK LOW }
  PLAN p WHEN tick.go != no { STEP s { act(n=1) } } }
"""
        calls = []
        host = Host()
        host.tool("act")(lambda n: calls.append(n) or {})
        rt = Runtime(parse_source(source).agents[0], host, MockLLM(), echo=True)
        rt.run(max_ticks=2)

        assert calls == [1, 1]
        assert rt.trace.echo is False

    def test_the_cli_survives_a_non_utf8_stream(self):
        import os
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONIOENCODING="ascii", PYTHONUTF8="0",
                   LC_ALL="C", LANG="C")
        for command in (["check"], ["verify"], ["run", "--ticks", "1"]):
            done = subprocess.run(
                [sys.executable, "-m", "agentl", *command[:1],
                 "examples/soc_analyst.agent", *command[1:]],
                cwd=root, env=env, capture_output=True)
            assert b"Traceback" not in done.stderr, done.stderr[-400:]
            assert done.returncode == 0



# ====================================================================
# 6. Journal durable : examen sans écriture, refus net d'un journal abîmé
# ====================================================================
class TestDurableJournalInspection:
    SOURCE = """
AGENT a {
  OBSERVE { m.x }
  TOOL ping { RISK LOW }
  PLAN p { STEP s { ping() } }
  LOOP MAX 2 { OBSERVE SELECT_PLAN EXECUTE }
}
"""

    def _journal(self, tmp_path):
        from agentl.durable import DurableRun, FileStore

        agent = parse_source(self.SOURCE).agents[0]
        host = Host()
        host.tool("ping")(lambda: {})
        host.sensors["m.x"] = lambda: 1
        DurableRun(agent, host, MockLLM(), store=FileStore(tmp_path)).run(
            max_ticks=2)
        return tmp_path / "wal.jsonl"

    def test_inspection_leaves_a_torn_tail_alone(self, tmp_path):
        from agentl.durable import DurableJournal, FileStore

        wal = self._journal(tmp_path)
        torn = wal.read_bytes()[:-15]            # dernière ligne déchirée
        wal.write_bytes(torn)

        journal = DurableJournal.open(FileStore(tmp_path), repair=False)

        assert wal.read_bytes() == torn          # rien n'a été écrit
        assert journal.entries                   # le préfixe sain est lu
        DurableJournal.open(FileStore(tmp_path))  # la reprise, elle, répare
        assert wal.read_bytes() != torn
        assert wal.read_bytes().endswith(b"\n")

    @pytest.mark.parametrize("damage", [
        lambda d: (d / "meta.json").write_text("{oops", encoding="utf-8"),
        lambda d: (d / "meta.json").write_text("[]", encoding="utf-8"),
        lambda d: (d / "meta.json").write_bytes(b""),
        lambda d: (d / "wal.jsonl").write_bytes(
            b"[1,2]\n" + (d / "wal.jsonl").read_bytes()),
        lambda d: (d / "wal.jsonl").write_bytes(
            (d / "wal.jsonl").read_bytes().replace(b"\n", b"\n42\n", 1)),
    ])
    def test_a_damaged_journal_is_refused_as_such(self, tmp_path, damage):
        from agentl.durable import DurableError, DurableJournal, FileStore

        self._journal(tmp_path)
        damage(tmp_path)

        with pytest.raises(DurableError):
            DurableJournal.open(FileStore(tmp_path))
        with pytest.raises(DurableError):
            DurableJournal.open(FileStore(tmp_path), repair=False)


class TestReplayJournalShape:
    """Un fichier qui n'est pas un journal est refusé par `ReplayError`."""

    @pytest.mark.parametrize("text", [
        "[]",
        '{"meta": []}',
        '{"meta": {"format": 2}, "entries": {"a": 1}}',
        '{"meta": {"format": 2}, "entries": [5]}',
        '{"meta": {"format": 2}, "entries": [{"seq": 0, "key": "k"}]}',
        '{"meta": {"format": 2, "ticks": "x"}, "entries": []}',
        '{"meta": {"format": 2, "source": 5}, "entries": []}',
        '{"meta": {"format": true}, "entries": []}',
    ])
    def test_a_malformed_journal_is_refused_as_such(self, text):
        from agentl.replay import Journal, ReplayError

        for strict in (True, False):
            with pytest.raises(ReplayError):
                Journal.loads(text, strict=strict)


# ====================================================================
# 7. Société : un message déposé est traité avant l'arrêt
# ====================================================================
class TestSocietySettles:
    TEAM = """
AGENT receiver {
  TOOL act { INPUT { v: String } RISK LOW }
  ON MESSAGE job { THEN handle }
  PLAN handle { STEP s { act(v = payload.v) } }
  LOOP MAX 6 { RECEIVE OBSERVE SELECT_PLAN EXECUTE }
}
AGENT sender {
  PLAN go { STEP s {
    MESSAGE job { TO receiver PAYLOAD { v = "x", tags = [1, 2] } }
    SET sent = yes
  } }
  DECIDE { IF sent != yes THEN go }
  LOOP MAX 6 { RECEIVE OBSERVE SELECT_PLAN EXECUTE }
}
"""

    def _society(self):
        from agentl.society import Society

        return Society(parse_source(self.TEAM).agents)

    def test_the_stopping_rule_is_unchanged(self):
        from agentl.society import settled

        society = self._society()
        # Aucun GOAL : `all([])`, la société est « réglée » — comportement
        # historique conservé, borné par `max_ticks`.
        assert settled(society.runtimes) is True
        assert sum(1 for _ in society.iter_ticks(6)) == 1

    def test_recipients_do_not_share_a_nested_payload(self):
        society = self._society()
        payload = {"tags": [1, 2]}

        society.send("sender", "job", payload, to="receiver")
        society.runtimes["receiver"].inbox[0]["payload"]["tags"].append(3)

        assert society.log[0].payload == {"tags": [1, 2]}
        assert payload == {"tags": [1, 2]}


# ====================================================================
# 8. Diagnostics W145 et W146
# ====================================================================
def _codes(source):
    return [d.code for d in Analyzer(parse_source(source).agents[0]).run()]


class TestW145DeadMessageHandler:
    SOURCE = """
AGENT a {
  TOOL act { RISK LOW }
  ON MESSAGE job { THEN handle }
  PLAN handle { STEP s { act() } }
  %s
}
"""

    def test_a_loop_without_receive_never_reads_its_inbox(self):
        assert "W145" in _codes(self.SOURCE % "LOOP MAX 3 { OBSERVE SELECT_PLAN EXECUTE }")

    def test_receive_in_the_loop_is_enough(self):
        assert "W145" not in _codes(
            self.SOURCE % "LOOP MAX 3 { RECEIVE OBSERVE SELECT_PLAN EXECUTE }")

    def test_the_default_cycle_receives(self):
        assert "W145" not in _codes(self.SOURCE % "")

    def test_no_handler_no_warning(self):
        source = self.SOURCE.replace("ON MESSAGE job { THEN handle }", "") \
                            % "LOOP MAX 3 { OBSERVE EXECUTE }"
        assert "W145" not in _codes(source)


class TestW146UnobservableEvidence:
    SOURCE = """
AGENT a {
  OBSERVE { sandbox.count  load }
  HYPOTHESIS h {
    PRIOR 0.1
    EVIDENCE { %s LIKELIHOOD 0.9 GIVEN_NOT 0.1 }
    THRESHOLD 0.5
    EXPLAINS threat.confirmed
  }
}
"""

    @pytest.mark.parametrize("test", [
        "sandbox.cuont > 3",            # faute de frappe
        "scanner.hits > 3",             # source jamais déclarée
        "flagged",                      # nom que rien ne pose
        "mode == high",                 # nom en tête de comparaison
    ])
    def test_flags_an_evidence_nothing_can_observe(self, test):
        assert "W146" in _codes(self.SOURCE % test)

    @pytest.mark.parametrize("test", [
        "sandbox.count > 3",            # observé
        "load == high",                 # observé, `high` est une constante
        "payload.score > 1",            # publié par le runtime
        "sensors.cpu.available == no",
        "threat.confirmed == yes",      # posé par l'EXPLAINS lui-même
    ])
    def test_stays_quiet_on_what_the_program_provides(self, test):
        assert "W146" not in _codes(self.SOURCE % test)


# ====================================================================
# 9. T1 : une garde de plan est jugée à la sélection, pas à l'exécution
# ====================================================================
class TestT1QueuedPlans:
    SOURCE = """
AGENT a {
  OBSERVE { b }
  TOOL wipe { RISK HIGH }
  POLICY { NEVER wipe WHEN b == 2 }
  PLAN first WHEN b <= 2 { STEP s { SET b = 2 } }
  PLAN second WHEN %s { STEP s { wipe() } }
  LOOP MAX 2 { OBSERVE SELECT_PLAN EXECUTE }
}
"""

    def _t1(self, when):
        from agentl import verify

        agent = parse_source(self.SOURCE % when).agents[0]
        return agent, next(t for t in verify(agent).theorems if t.key == "T1")

    def _blocked(self, agent):
        host = Host()
        host.tool("wipe")(lambda: {})
        host.sensors["b"] = lambda: 1
        rt = Runtime(agent, host, MockLLM())
        rt.run(max_ticks=1)
        return rt.metrics["blocked"]

    def test_a_cosselected_plan_can_age_the_guard_of_the_next(self):
        agent, t1 = self._t1("b < 2")

        assert self._blocked(agent) == 1          # le runtime bute bien
        assert any(f.code == "V102" for f in t1.findings)   # T1 le dit

    def test_a_plan_that_cannot_run_with_it_does_not_age_it(self):
        # `first` exige b <= 2, `second` b > 5 : jamais ensemble.
        _, t1 = self._t1("b > 5")

        assert not any(f.code == "V102" for f in t1.findings)


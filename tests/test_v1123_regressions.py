"""Régressions de la 1.12.3 : trois écarts entre ce que le runtime promet et ce
qu'il garantissait, reproduits le 2026-10-19 puis refermés.

    python -m pytest tests/test_v1123_regressions.py -q
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import Host, MockLLM, Runtime, Symbol, parse_source

SECRET = "sk-LIVE-123456"


# ==================================================== NEVER SEND suit la valeur
SEND_PROGRAM = '''
AGENT leak {
  GOAL g { MAINTAIN done == yes }
  OBSERVE { api.secret  ticket.text }
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  POLICY { DEFAULT ALLOW  NEVER SEND api.secret }
  PLAN go WHEN done != yes {
    STEP a { %s }
    STEP b { REASON { TASK "summarise" USING { %s ticket.text }
                      PRODUCE { summary: String DEFAULT "x" } } }
    STEP c { SET done = yes }
  }
  LOOP UNTIL goal.satisfied MAX 2 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE }
}
'''


class Spy(MockLLM):
    def __init__(self):
        super().__init__()
        self.seen = []

    def reason(self, task, context, produce=None, *a, **k):
        self.seen.append(context)
        return {"summary": "ok"}


def _sent_to_model(step: str, using: str) -> str:
    agent = parse_source(SEND_PROGRAM % (step, using)).agents[0]
    host = Host()
    host.sensors["api.secret"] = lambda: SECRET
    host.sensors["ticket.text"] = lambda: "hello"
    spy = Spy()
    Runtime(agent, host, spy).run(max_ticks=2)
    assert spy.seen, "le REASON n'a pas été appelé"
    return json.dumps(spy.seen, default=str, ensure_ascii=False)


def test_the_protected_path_itself_is_withheld():
    assert SECRET not in _sent_to_model("SET x = 1", "api.secret")


@pytest.mark.parametrize("step", [
    "SET note = api.secret",
    "SET tmp = api.secret\n    SET note = tmp",
])
def test_a_copy_of_a_protected_value_is_withheld(step):
    sent = _sent_to_model(step, "note")
    assert SECRET not in sent
    assert "⟦retenu⟧" in sent


def test_an_unrelated_value_is_still_sent():
    sent = _sent_to_model("SET note = ticket.text", "note")
    assert "hello" in sent and SECRET not in sent


def test_reading_a_secret_in_a_guard_does_not_blind_the_model():
    """Un test de présence (`api.secret != unknown`) ne rend pas secrètes les
    valeurs que la branche écrit : seul le flux explicite est suivi."""
    sent = _sent_to_model(
        "IF api.secret != unknown THEN { SET note = ticket.text }", "note")
    assert "hello" in sent and SECRET not in sent


# ============================== journal durable : une approbation de queue ne vaut pas
from agentl.durable import (DurableError, DurableRun, FileStore, MemoryStore,
                            SQLiteStore)
from agentl.seal import chain_step
from test_durable_aaa import SOURCE as _PAY, Bank, Crash, make_host

_APPROVED = _PAY.replace("DEFAULT ALLOW",
                         "DEFAULT ALLOW\n    REQUIRE APPROVAL FOR transfer")
_AGENT = parse_source(_APPROVED).agents[0]


class _DieAfterApprove:
    """Un magasin qui meurt juste après avoir écrit la réponse de l'approbateur."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def append(self, raw):
        self.inner.append(raw)
        if raw.get("kind") == "approve":
            raise Crash("coupure juste après la réponse de l'approbateur")


def _refuse_then_die(store, **options):
    """Première vie : l'approbateur refuse, le processus meurt aussitôt."""
    bank = Bank()
    host = make_host(bank, "plain")
    host.approver = lambda request: "no"
    with pytest.raises(Crash):
        DurableRun(_AGENT, host, store=_DieAfterApprove(store), run_id="r",
                   **options).run(max_ticks=1)
    assert bank.ledger == []
    return bank


def _forge_tail_approval(read_raws, write_raws, key=None):
    raws = read_raws()
    assert raws[-1]["kind"] == "approve"
    previous = ""
    for raw in raws:
        if raw["kind"] == "approve":
            raw["value"] = "yes"
        raw["h"] = chain_step(previous, raw, key) if key else \
            chain_step(previous, raw)
        previous = raw["h"]
    write_raws(raws)


def _resume(store, bank, **options):
    asked = []
    host = make_host(bank, "plain")
    host.approver = lambda request: asked.append(request.render()) or "no"
    DurableRun(_AGENT, host, store=store, **options).run(max_ticks=1)
    return asked


def test_a_forged_tail_approval_is_asked_again_in_memory():
    disk = MemoryStore()
    bank = _refuse_then_die(disk)

    def read():
        return [json.loads(line) for line in disk.lines]

    def write(raws):
        disk.lines[:] = [json.dumps(r) for r in raws]

    _forge_tail_approval(read, write)
    asked = _resume(disk, bank)
    assert asked == ["transfer(amount=100, to=acct-9)"]     # l'humain est reconsulté
    assert bank.ledger == []                                # et il refuse


def test_a_forged_tail_approval_is_asked_again_on_disk(tmp_path):
    store = FileStore(tmp_path / "run")
    bank = _refuse_then_die(store)
    store.close()
    wal = tmp_path / "run" / "wal.jsonl"
    _forge_tail_approval(
        lambda: [json.loads(l) for l in wal.read_text().splitlines()],
        lambda raws: wal.write_text("".join(json.dumps(r) + "\n" for r in raws)))
    asked = _resume(FileStore(tmp_path / "run"), bank)
    assert asked == ["transfer(amount=100, to=acct-9)"]
    assert bank.ledger == []


def test_a_forged_tail_approval_is_asked_again_in_sqlite(tmp_path):
    db = tmp_path / "wal.db"
    store = SQLiteStore(db, "r")
    bank = _refuse_then_die(store)
    import sqlite3
    con = sqlite3.connect(db)
    raws = [json.loads(b) for (b,) in con.execute(
        "SELECT body FROM entries WHERE run_id='r' ORDER BY seq")]
    _forge_tail_approval(lambda: raws, lambda r: None)
    for raw in raws:
        con.execute("UPDATE entries SET body=? WHERE run_id='r' AND seq=?",
                    (json.dumps(raw), raw["seq"]))
    con.commit()
    con.close()
    asked = _resume(SQLiteStore(db, "r"), bank)
    assert asked == ["transfer(amount=100, to=acct-9)"]
    assert bank.ledger == []


def test_an_approval_followed_by_its_action_is_still_served():
    """Le cas nominal ne change pas : approuvé puis exécuté, jamais reposé."""
    bank, disk, asked = Bank(), MemoryStore(), []
    host = make_host(bank, "plain")
    host.approver = lambda request: asked.append(1) or True
    DurableRun(_AGENT, host, store=disk).run(max_ticks=1)
    host2 = make_host(bank, "plain")
    host2.approver = lambda request: asked.append(2) or True
    DurableRun(_AGENT, host2, store=disk).run()
    assert asked == [1]


# ----------------------------------------------------------- chaîne à clé
KEY = b"cle-de-test-0123456789"


def test_a_keyed_journal_cannot_be_rewritten_without_the_key():
    disk = MemoryStore()
    bank = _refuse_then_die(disk, key=KEY)
    assert disk.load()[0]["keyed"] is True
    raws = [json.loads(line) for line in disk.lines]
    _forge_tail_approval(lambda: raws, lambda r: disk.lines.__setitem__(
        slice(None), [json.dumps(x) for x in r]))       # chaîne SHA-256 simple
    with pytest.raises(DurableError, match="altéré"):
        _resume(disk, bank, key=KEY)


def test_a_keyed_journal_resumes_with_its_key_and_only_with_it():
    disk = MemoryStore()
    bank = _refuse_then_die(disk, key=KEY)
    with pytest.raises(DurableError, match="clé"):
        _resume(disk, bank)                              # clé absente
    with pytest.raises(DurableError, match="altéré"):
        _resume(disk, bank, key=b"autre-cle")            # mauvaise clé
    assert _resume(disk, bank, key=KEY) == ["transfer(amount=100, to=acct-9)"]


def test_a_key_cannot_be_offered_to_an_unkeyed_journal():
    """Rétrogradation : qui fournit une clé veut un journal à clé."""
    disk = MemoryStore()
    bank = _refuse_then_die(disk)
    with pytest.raises(DurableError, match="pas à clé"):
        _resume(disk, bank, key=KEY)


# ================= W139 : une garde sur la confiance que rien ne produit est vide
from agentl.analyzer import Analyzer

_CONF = '''
AGENT c {
  GOAL g { MAINTAIN done == yes }
  OBSERVE { ticket.text }
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL wipe { OUTPUT { ok: Symbol } SIDE_EFFECT disk.data RISK CRITICAL }
  POLICY { DEFAULT DENY  %s }
  PLAN go WHEN done != yes {
    STEP a { %s }
    STEP b { wipe() }
    STEP c { SET done = yes }
  }
  LOOP MAX 2 { SELECT_PLAN EXECUTE }
}
'''
_PRODUCER = ('REASON { TASK "t" USING { ticket.text } '
             'PRODUCE { confidence: Number IN [0, 1] DEFAULT 0 } }')


def _codes(rule, step):
    agent = parse_source(_CONF % (rule, step)).agents[0]
    return [d.code for d in Analyzer(agent).run()]


@pytest.mark.parametrize("rule", [
    "ALLOW wipe IF action.confidence >= 0.95",
    "ALLOW wipe IF confidence >= 0.95",
    "REQUIRE APPROVAL FOR wipe WHEN action.confidence < 0.9\n    ALLOW wipe",
    "NEVER wipe WHEN action.confidence < 0.5\n    ALLOW wipe",
])
def test_a_confidence_guard_nothing_produces_is_flagged(rule):
    assert "W139" in _codes(rule, "SET x = 1")


def test_a_confidence_guard_with_a_producer_is_not_flagged():
    assert "W139" not in _codes("ALLOW wipe IF action.confidence >= 0.95",
                                _PRODUCER)


def test_a_guard_on_something_else_is_not_flagged():
    assert "W139" not in _codes("ALLOW wipe IF ticket.text == ok", "SET x = 1")


def test_the_confidence_check_also_runs_under_an_enabled_planner():
    source = (_CONF % ("ALLOW wipe IF action.confidence >= 0.95", "SET x = 1")
              ).replace("  LOOP MAX 2", "  PLANNER { ENABLE ACHIEVE done == yes }\n  LOOP MAX 2")
    agent = parse_source(source).agents[0]
    assert "W139" in [d.code for d in Analyzer(agent).run()]


# ------------------------------------------------------- CLI : journal à clé
def test_cli_durable_key_round_trip(tmp_path, capsys):
    from agentl.cli import main
    canonical = ROOT / "SKILLS/agentl-author/references/generated"
    for name in ("canonical.agent", "canonical.py"):
        (tmp_path / name).write_text((canonical / name).read_text())
    agent, run = str(tmp_path / "canonical.agent"), str(tmp_path / "run")
    assert main(["run", agent, "--durable", run, "--sign-key", "k-secret",
                 "--quiet"]) == 0
    assert main(["durable", "status", run, "--sign-key", "k-secret"]) == 0
    assert main(["durable", "status", run]) == 2            # clé absente
    assert main(["run", agent, "--durable", run, "--quiet"]) == 2
    assert main(["run", agent, "--durable", run, "--sign-key", "k-secret",
                 "--quiet"]) == 0                           # reprise d'un journal terminé
    out = tmp_path / "j.json"
    assert main(["durable", "export", run, "--sign-key", "k-secret",
                 "-o", str(out)]) == 0
    assert main(["replay", str(out), "--quiet"]) == 0

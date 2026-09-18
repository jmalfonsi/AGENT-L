"""Exécution durable — un crash à chaque point, jamais d'effet doublé (I9).

Le test central ne choisit pas ses pannes : il compte les écritures d'une
exécution de référence, puis rejoue l'exécution en tuant le « disque » à
**chacune** d'elles — avant qu'elle n'atteigne le journal, puis juste après —
et en tuant aussi l'outil au milieu de son effet. Chaque fois, un nouveau
processus reprend sur le même journal et le même monde. On vérifie :

* hôte qui honore la clé d'idempotence, ou qui sait réconcilier : le monde
  contient **exactement un** virement, et la trace finale est celle de
  l'exécution sans panne, caractère pour caractère ;
* hôte qui ne sait rien de tout cela : **jamais deux** virements ; une
  intention restée sans résultat est déclarée indéterminée, pas relancée.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from agentl import Host, MockLLM, Symbol, parse_source
from agentl.durable import (DurableError, DurableRun, FileStore, MemoryStore,
                            NOT_EXECUTED, SQLiteStore, resolutions_of)
from agentl.kernel import current_action
from agentl.replay import (Journal, ReplayDivergence, ReplayHost, ReplayLLM,
                           verify_trace)
from agentl.runtime import Runtime

SOURCE = """
AGENT payments {
  OBSERVE { queue.pending }
  TOOL transfer {
    INPUT       { amount: Number, to: String }
    OUTPUT      { receipt: String }
    SIDE_EFFECT { bank.ledger }
    RISK        HIGH
  }
  TOOL notify {
    INPUT  { msg: String }
    OUTPUT { sent: Symbol }
    RISK   LOW
  }
  POLICY {
    DEFAULT ALLOW
    NEVER transfer WHEN tools.transfer.in_doubt == true
  }
  PLAN pay WHEN queue.pending > 0 {
    STEP send { transfer(amount=100, to="acct-9") }
    STEP tell { notify(msg="paid") }
  }
}
"""
AGENT = parse_source(SOURCE).agents[0]
TICKS = 3


class Crash(BaseException):
    """Mort du processus — ni `Exception`, donc rien ne l'avale."""


class Bank:
    """Le monde extérieur : il survit aux crashs de l'agent."""

    def __init__(self) -> None:
        self.ledger: list = []
        self.by_key: dict = {}
        self.notes: list = []


def make_host(bank: Bank, mode: str, *, crash_in_tool: bool = False) -> Host:
    host = Host()
    host.sensors["queue.pending"] = lambda: 0 if bank.ledger else 1
    state = {"armed": crash_in_tool}

    def transfer(amount, to):
        key = current_action().idempotency_key
        if mode == "idempotent" and key in bank.by_key:
            return dict(bank.by_key[key])         # le service reconnaît la clé
        receipt = f"rcpt-{len(bank.ledger) + 1}"
        bank.ledger.append((amount, to, key))
        bank.by_key[key] = {"receipt": receipt}
        if state["armed"]:
            state["armed"] = False
            raise Crash("coupure pendant l'appel, après l'effet")
        return {"receipt": receipt}

    def notify(msg):
        key = current_action().idempotency_key
        if mode == "idempotent" and any(k == key for k, _ in bank.notes):
            return {"sent": Symbol("yes")}
        bank.notes.append((key, msg))
        return {"sent": Symbol("yes")}

    # Exactement une fois n'est promis que si **chaque** outil à effet honore
    # la clé ou sait se réconcilier : ici les deux, dans ces deux modes.
    host.tool("transfer", idempotent=(mode == "idempotent"))(transfer)
    host.tool("notify", idempotent=(mode == "idempotent"))(notify)

    if mode == "reconcile":
        @host.reconciler("transfer")
        def lookup(args, context):
            found = bank.by_key.get(context.idempotency_key)
            return dict(found) if found else NOT_EXECUTED

        @host.reconciler("notify")
        def lookup_note(args, context):
            done = any(k == context.idempotency_key for k, _ in bank.notes)
            return {"sent": Symbol("yes")} if done else NOT_EXECUTED
    return host


class CrashingStore:
    """Un disque qui meurt à la n-ième écriture — avant ou après elle."""

    def __init__(self, disk: MemoryStore, at: int, after: bool) -> None:
        self.disk, self.at, self.after, self.count = disk, at, after, 0

    def load(self):
        return self.disk.load()

    def write_meta(self, meta):
        self.disk.write_meta(meta)

    def append(self, raw):
        self.count += 1
        if self.count == self.at and not self.after:
            raise Crash(f"écriture #{self.at} perdue")
        self.disk.append(raw)
        if self.count == self.at and self.after:
            raise Crash(f"crash juste après l'écriture #{self.at}")


def reference(mode: str):
    bank, disk = Bank(), MemoryStore()
    run = DurableRun(AGENT, make_host(bank, mode), MockLLM(), store=disk,
                     run_id="ref")
    run.run(max_ticks=TICKS)
    return bank, disk, run.trace_text()


def crash_then_resume(mode: str, *, at: int = 0, after: bool = False,
                      crash_in_tool: bool = False):
    bank, disk = Bank(), MemoryStore()
    store = CrashingStore(disk, at, after) if at else disk
    first = DurableRun(AGENT, make_host(bank, mode, crash_in_tool=crash_in_tool),
                       MockLLM(), store=store, run_id="ref")
    with pytest.raises(Crash):
        first.run(max_ticks=TICKS)
    resumed = DurableRun(AGENT, make_host(bank, mode), MockLLM(), store=disk)
    resumed.run()
    return bank, resumed


# ------------------------------------------------------ crash à chaque point
N_WRITES = len(reference("idempotent")[1].lines)
POINTS = [(k, after) for k in range(1, N_WRITES + 1) for after in (False, True)]


@pytest.mark.parametrize("mode", ["idempotent", "reconcile"])
@pytest.mark.parametrize("at,after", POINTS)
def test_exactly_once_at_every_crash_point(mode, at, after):
    _, _, expected = reference(mode)
    bank, resumed = crash_then_resume(mode, at=at, after=after)
    assert len(bank.ledger) == 1
    assert len(bank.notes) == 1
    assert resumed.trace_text() == expected


@pytest.mark.parametrize("mode", ["idempotent", "reconcile"])
def test_exactly_once_when_the_tool_dies_after_its_effect(mode):
    _, _, expected = reference(mode)
    bank, resumed = crash_then_resume(mode, crash_in_tool=True)
    assert len(bank.ledger) == 1
    assert resumed.trace_text() == expected
    how = [r["how"] for r in resolutions_of(resumed.journal)]
    assert how == (["retry"] if mode == "idempotent" else ["reconciled"])


@pytest.mark.parametrize("at,after", POINTS)
def test_at_most_once_without_idempotency_at_every_crash_point(at, after):
    bank, resumed = crash_then_resume("plain", at=at, after=after)
    # I9 : aucune **action** — une clé d'idempotence — n'a d'effet deux fois.
    # Le programme peut, lui, décider d'autres actions : un `notify` par tick
    # tant que la file n'est pas vide en est une nouvelle à chaque fois.
    assert len(bank.ledger) <= 1
    for effects in (bank.ledger, bank.notes):
        keys = [entry[-1] if effects is bank.ledger else entry[0]
                for entry in effects]
        assert len(keys) == len(set(keys))
    trace = resumed.trace_text()
    for resolution in resumed.journal.resolutions:
        assert resolution["how"] == "in_doubt"
        tool = resolution["tool"]
        assert f"{tool}() : effet indéterminé" in trace
        assert resumed.runtime.state.get(f"tools.{tool}.in_doubt") is True


def test_a_tool_that_died_after_its_effect_is_in_doubt_not_replayed():
    bank, resumed = crash_then_resume("plain", crash_in_tool=True)
    assert len(bank.ledger) == 1                      # l'effet d'origine
    trace = resumed.trace_text()
    assert "transfer() : effet indéterminé" in trace
    assert "ActionInDoubt" in trace
    # Et la politique de l'auteur interdit de retenter à l'aveugle :
    rt = resumed.runtime
    assert rt.state.get("tools.transfer.in_doubt") is True
    assert rt.state.get("bank.ledger.dirty") is True


def test_the_crash_leaves_one_pending_intent_that_resume_settles():
    bank, disk = Bank(), MemoryStore()
    first = DurableRun(AGENT, make_host(bank, "plain", crash_in_tool=True),
                       MockLLM(), store=disk, run_id="p")
    with pytest.raises(Crash):
        first.run(max_ticks=TICKS)
    pending = first.journal.pending()
    assert [p["tool"] for p in pending] == ["transfer"]
    assert pending[0]["action_id"].startswith("p:payments:t1:")


# ------------------------------------------------------------ re-dérivation
def test_resume_does_not_call_the_model_or_the_world_again():
    bank, disk = Bank(), MemoryStore()
    llm = MockLLM()
    run = DurableRun(AGENT, make_host(bank, "plain"), llm, store=disk)
    run.run(max_ticks=TICKS)
    trace = run.trace_text()

    class Untouchable(Host):
        def read(self, path):
            raise AssertionError("capteur relu pendant la re-dérivation")

        def invoke(self, name, args):
            raise AssertionError("outil rappelé pendant la re-dérivation")

    silent = MockLLM()
    again = DurableRun(AGENT, Untouchable(), silent, store=disk)
    again.run()
    assert again.trace_text() == trace
    assert silent.calls == []


def test_a_changed_program_is_not_resumed():
    disk = MemoryStore()
    DurableRun(AGENT, make_host(Bank(), "plain"), store=disk).run(max_ticks=1)
    other = parse_source(SOURCE.replace("amount=100", "amount=900")).agents[0]
    with pytest.raises(DurableError, match="programme a changé"):
        DurableRun(other, make_host(Bank(), "plain"), store=disk)


def test_a_diverging_state_is_caught_at_the_checkpoint():
    disk = MemoryStore()
    DurableRun(AGENT, make_host(Bank(), "plain"), store=disk).run(max_ticks=2)
    resumed = DurableRun(AGENT, make_host(Bank(), "plain"), store=disk)
    resumed.runtime.state.set_world("hidden.nondeterminism", 42)
    with pytest.raises(ReplayDivergence, match="point de contrôle"):
        resumed.run()


def test_the_durable_journal_replays_with_plain_replay():
    bank, disk = Bank(), MemoryStore()
    run = DurableRun(AGENT, make_host(bank, "plain"), MockLLM(), store=disk)
    run.run(max_ticks=TICKS)
    journal = Journal.loads(run.export().dumps(), strict=False)
    replayed = Runtime(AGENT, ReplayHost(journal), ReplayLLM(journal))
    replayed.run(max_ticks=TICKS)
    assert replayed.trace.render() == run.trace_text()
    assert verify_trace(journal, replayed.trace.render())
    assert journal.exhausted


def test_an_approval_is_not_asked_twice():
    source = SOURCE.replace("DEFAULT ALLOW",
                            "DEFAULT ALLOW\n    REQUIRE APPROVAL FOR transfer")
    agent = parse_source(source).agents[0]
    bank, disk = Bank(), MemoryStore()
    asked = []
    host = make_host(bank, "plain")
    host.approver = lambda request: asked.append(request.render()) or True
    first = DurableRun(agent, host, store=disk)
    first.run(max_ticks=1)
    assert asked == ["transfer(amount=100, to=acct-9)"]

    host2 = make_host(bank, "plain")
    host2.approver = lambda request: asked.append("again") or True
    DurableRun(agent, host2, store=disk).run()
    assert asked == ["transfer(amount=100, to=acct-9)"]


# ---------------------------------------------------------------- magasins
def test_file_store_survives_a_torn_last_line(tmp_path):
    bank = Bank()
    first = DurableRun(AGENT, make_host(bank, "idempotent", crash_in_tool=True),
                       MockLLM(), store=FileStore(tmp_path / "run"), run_id="f")
    with pytest.raises(Crash):
        first.run(max_ticks=TICKS)
    wal = tmp_path / "run" / "wal.jsonl"
    with open(wal, "ab") as fh:
        fh.write(b'{"seq": 99, "kind": "inv')        # écriture déchirée
    resumed = DurableRun(AGENT, make_host(bank, "idempotent"), MockLLM(),
                         store=FileStore(tmp_path / "run"))
    resumed.run()
    assert len(bank.ledger) == 1
    assert resumed.status == "completed"
    meta = json.loads((tmp_path / "run" / "meta.json").read_text())
    assert meta["status"] == "completed" and meta["run_id"] == "f"


def test_file_store_refuses_a_tampered_journal(tmp_path):
    DurableRun(AGENT, make_host(Bank(), "plain"), MockLLM(),
               store=FileStore(tmp_path / "run")).run(max_ticks=2)
    wal = tmp_path / "run" / "wal.jsonl"
    lines = wal.read_text().splitlines()
    forged = [line.replace('"rcpt-1"', '"rcpt-666"') for line in lines]
    assert forged != lines
    wal.write_text("\n".join(forged) + "\n")
    with pytest.raises(DurableError, match="altéré"):
        DurableRun(AGENT, make_host(Bank(), "plain"),
                   store=FileStore(tmp_path / "run"))


def test_file_store_refuses_a_corrupted_middle_line(tmp_path):
    DurableRun(AGENT, make_host(Bank(), "plain"), MockLLM(),
               store=FileStore(tmp_path / "run")).run(max_ticks=2)
    wal = tmp_path / "run" / "wal.jsonl"
    lines = wal.read_text().splitlines()
    lines[1] = lines[1][:10]
    wal.write_text("\n".join(lines) + "\n")
    with pytest.raises(DurableError, match="illisible"):
        DurableRun(AGENT, make_host(Bank(), "plain"),
                   store=FileStore(tmp_path / "run"))


def test_sqlite_store_keeps_several_runs_and_resumes(tmp_path):
    db = tmp_path / "runs.db"
    bank = Bank()
    first = DurableRun(AGENT, make_host(bank, "reconcile", crash_in_tool=True),
                       MockLLM(), store=SQLiteStore(db, "a"), run_id="a")
    with pytest.raises(Crash):
        first.run(max_ticks=TICKS)
    DurableRun(AGENT, make_host(Bank(), "plain"), MockLLM(),
               store=SQLiteStore(db, "b"), run_id="b").run(max_ticks=1)
    resumed = DurableRun(AGENT, make_host(bank, "reconcile"), MockLLM(),
                         store=SQLiteStore(db, "a"))
    resumed.run()
    assert len(bank.ledger) == 1
    assert SQLiteStore.runs(db) == ["a", "b"]
    rows = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM entries WHERE run_id = 'a'").fetchone()[0]
    assert rows == len(resumed.journal.entries)


def test_idempotency_keys_are_stable_across_a_resume():
    seen = []
    bank, disk = Bank(), MemoryStore()
    host = make_host(bank, "idempotent", crash_in_tool=True)
    original = host.tools["transfer"]

    def spy(**kwargs):
        seen.append((current_action().idempotency_key,
                     current_action().attempt))
        return original(**kwargs)
    host.tools["transfer"] = spy
    with pytest.raises(Crash):
        DurableRun(AGENT, host, store=disk, run_id="k").run(max_ticks=TICKS)
    host2 = make_host(bank, "idempotent")
    original2 = host2.tools["transfer"]

    def spy2(**kwargs):
        seen.append((current_action().idempotency_key,
                     current_action().attempt))
        return original2(**kwargs)
    host2.tools["transfer"] = spy2
    DurableRun(AGENT, host2, store=disk).run()
    assert [k for k, _ in seen] == [seen[0][0]] * 2
    assert [a for _, a in seen] == [1, 2]

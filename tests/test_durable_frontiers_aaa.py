"""Exécution durable, toutes frontières (v1.9).

`test_durable_aaa.py` éprouve le protocole d'intention sur des outils. Ici,
un agent franchit **toutes** les frontières — capteur, modèle (REASON et
choix de plan), opérateur (ASK), approbation, sous-agent — et le disque meurt
à chacune de ses écritures. La reprise doit toujours aboutir, sans jamais
doubler un effet, sans rappeler le modèle ni reposer une question déjà
journalisée, et — quand aucune action n'est restée indéterminée — rendre la
trace de l'exécution sans panne, caractère pour caractère.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from agentl import Host, MockLLM, Symbol, parse_source
from agentl.cli import main as agentl_main
from agentl.durable import (DURABLE_FORMAT, DurableError, DurableRun,
                            FileStore, MemoryStore, SQLiteStore)
from agentl.kernel import current_action
from agentl.replay import ReplayDivergence

SOURCE = """
AGENT ops {
  OBSERVE { incident.open }
  TOOL isolate {
    INPUT       { host: String }
    OUTPUT      { done: Symbol }
    SIDE_EFFECT { network.acl }
    RISK        HIGH
  }
  TOOL forensic { RISK HIGH  INPUT { host: String } }
  POLICY {
    DEFAULT ALLOW
    REQUIRE APPROVAL FOR isolate
  }
  PLAN respond WHEN incident.open == yes AND handled != yes {
    STEP think  { REASON "triage" { USING { incident.open }
                    PRODUCE { target: String DEFAULT "none", confidence: Number DEFAULT 0 } } }
    STEP ask    { ASK operator "confirmer la cible ?" DEFAULT no }
    STEP act    { isolate(host=reason.target) }
    STEP deep   { DELEGATE forensic { TASK "analyse" INPUT { reason.target } EXPECT { verdict } } }
    STEP close  { SET handled = yes }
  }
  DECIDE { REASON "choose" { USING { incident.open } PRODUCE { x: Number DEFAULT 0 } } }
}
"""
AGENT = parse_source(SOURCE).agents[0]
TICKS = 3


class Crash(BaseException):
    pass


class World:
    def __init__(self):
        self.acl, self.forensics, self.questions, self.approvals = [], [], 0, 0


def make_host(world: World) -> Host:
    host = Host()
    host.sensors["incident.open"] = lambda: Symbol("yes")

    @host.tool("isolate", idempotent=True)
    def isolate(host):
        key = current_action().idempotency_key
        if key not in [k for k, _ in world.acl]:
            world.acl.append((key, host))
        return {"done": Symbol("yes")}

    def forensic(payload):
        world.forensics.append(dict(payload))
        return {"verdict": Symbol("clean")}
    host.subagents["forensic"] = forensic

    def asker(question, reason):
        world.questions += 1
        return Symbol("yes")
    host.asker = asker

    def approver(request):
        world.approvals += 1
        return True
    host.approver = approver
    return host


def make_llm():
    return MockLLM({"triage": {"target": "srv-7", "confidence": 0.9}},
                   plan_choice=lambda ctx, cands: None)


class CrashingStore:
    def __init__(self, disk, at, after):
        self.disk, self.at, self.after, self.count = disk, at, after, 0

    def load(self):
        return self.disk.load()

    def write_meta(self, meta):
        self.disk.write_meta(meta)

    def append(self, raw):
        self.count += 1
        if self.count == self.at and not self.after:
            raise Crash()
        self.disk.append(raw)
        if self.count == self.at and self.after:
            raise Crash()


def reference():
    disk, world = MemoryStore(), World()
    run = DurableRun(AGENT, make_host(world), make_llm(), store=disk,
                     run_id="r")
    run.run(max_ticks=TICKS)
    return run.trace_text(), len(disk.lines), world


REF_TRACE, N_WRITES, REF_WORLD = reference()


def test_the_reference_crosses_every_frontier():
    kinds = {json.loads(line)["kind"] for line in
             _run_to_end(MemoryStore()).lines}
    assert {"read", "reason", "ask", "approve", "intent", "invoke",
            "delegate_lookup", "delegate", "checkpoint",
            "select_plan"} <= kinds


def _run_to_end(disk):
    DurableRun(AGENT, make_host(World()), make_llm(), store=disk,
               run_id="r").run(max_ticks=TICKS)
    return disk


@pytest.mark.parametrize("at", range(1, N_WRITES + 1))
@pytest.mark.parametrize("after", [False, True])
def test_every_frontier_survives_a_crash_at_every_write(at, after):
    disk, world = MemoryStore(), World()
    first = DurableRun(AGENT, make_host(world), make_llm(),
                       store=CrashingStore(disk, at, after), run_id="r")
    with pytest.raises(Crash):
        first.run(max_ticks=TICKS)
    asked_before = world.questions
    llm = make_llm()
    resumed = DurableRun(AGENT, make_host(world), llm, store=disk)
    resumed.run()
    # Aucun effet doublé : pare-feu idempotent, sous-agent au plus une fois.
    assert len(world.acl) == 1
    assert len(world.forensics) <= 1
    # Une question journalisée n'est pas reposée.
    assert world.questions <= asked_before + 1
    if not resumed.journal.resolutions or all(
            r["how"] == "retry" for r in resumed.journal.resolutions):
        assert resumed.trace_text() == REF_TRACE
        assert len(world.forensics) == 1
    else:
        # Seul le sous-agent, sans promesse, peut rester indéterminé.
        assert {r["tool"] for r in resumed.journal.resolutions
                if r["how"] == "in_doubt"} == {"forensic"}
        assert "forensic() : effet indéterminé" in resumed.trace_text()


def test_a_completed_run_rederives_without_calling_anyone():
    disk = _run_to_end(MemoryStore())
    world, llm = World(), make_llm()
    again = DurableRun(AGENT, make_host(world), llm, store=disk)
    again.run()
    assert again.trace_text() == REF_TRACE
    assert llm.calls == [] and world.questions == 0 and world.approvals == 0
    assert world.acl == [] and world.forensics == []


# ------------------------------------------------------------ société
PING = """
AGENT a {
  MEMORY { SHARED { log }
           WRITE { WHEN sent == yes  STORE { sent }  INTO SHARED.log } }
  TOOL note { RISK LOW }
  PLAN go WHEN sent != yes { note()  SET sent = yes
                             MESSAGE ping { TO b  PAYLOAD { n = 1 } } }
}
AGENT b {
  TOOL note { RISK LOW }
  ON MESSAGE ping { note()  SET got = yes }
}
"""


def _society_hosts(notes):
    hosts = {}
    for name in ("a", "b"):
        host = Host()
        host.tool("note", idempotent=True)(
            lambda _n=name: notes.add((_n, current_action().idempotency_key))
            or {})
        hosts[name] = host
    return hosts


def test_a_durable_society_resumes_to_the_same_story():
    agents = parse_source(PING).agents
    notes, disk = set(), MemoryStore()
    ref = DurableRun(agents, _society_hosts(notes), store=disk, run_id="s")
    ref.run(max_ticks=3)
    expected, writes = ref.trace_text(), len(disk.lines)
    assert "ping ← a" in expected
    for at in range(1, writes + 1):
        notes, disk = set(), MemoryStore()
        with pytest.raises(Crash):
            DurableRun(agents, _society_hosts(notes),
                       store=CrashingStore(disk, at, True),
                       run_id="s").run(max_ticks=3)
        resumed = DurableRun(agents, _society_hosts(notes), store=disk)
        resumed.run()
        assert resumed.trace_text() == expected, at
        assert len(notes) == 2


# ------------------------------------------------------ refus explicites
def test_a_foreign_format_or_run_id_is_refused():
    disk = _run_to_end(MemoryStore())
    with pytest.raises(DurableError, match="autre|pas"):
        DurableRun(AGENT, make_host(World()), store=disk, run_id="other")
    disk.meta["format"] = "agentl.durable.v0"
    with pytest.raises(DurableError, match="format"):
        DurableRun(AGENT, make_host(World()), store=disk)


def test_entries_without_metadata_are_refused():
    disk = _run_to_end(MemoryStore())
    disk.meta = {}
    with pytest.raises(DurableError, match="méta"):
        DurableRun(AGENT, make_host(World()), store=disk)


def test_a_reordered_journal_is_refused():
    disk = _run_to_end(MemoryStore())
    disk.lines[2], disk.lines[3] = disk.lines[3], disk.lines[2]
    with pytest.raises(DurableError, match="séquence|altéré"):
        DurableRun(AGENT, make_host(World()), store=disk)


def test_resuming_with_another_tick_budget_is_refused():
    disk, world = MemoryStore(), World()
    with pytest.raises(Crash):
        DurableRun(AGENT, make_host(world), make_llm(),
                   store=CrashingStore(disk, 5, True)).run(max_ticks=3)
    with pytest.raises(DurableError, match="ticks"):
        DurableRun(AGENT, make_host(world), make_llm(),
                   store=disk).run(max_ticks=5)


def test_a_completed_run_cannot_grow():
    disk = _run_to_end(MemoryStore())
    run = DurableRun(AGENT, make_host(World()), make_llm(), store=disk)
    run.run()
    with pytest.raises(ReplayDivergence, match="terminée"):
        run.runtime.tick()


def test_file_store_accepts_a_complete_last_line_without_newline(tmp_path):
    store = FileStore(tmp_path / "r")
    DurableRun(AGENT, make_host(World()), make_llm(), store=store,
               run_id="f").run(max_ticks=1)
    wal = tmp_path / "r" / "wal.jsonl"
    data = wal.read_bytes()
    wal.write_bytes(data.rstrip(b"\n"))
    meta, entries = FileStore(tmp_path / "r").load()
    assert len(entries) == data.count(b"\n")
    assert wal.read_bytes().endswith(b"\n")


def test_file_store_refuses_a_blank_line_in_the_middle(tmp_path):
    store = FileStore(tmp_path / "r")
    DurableRun(AGENT, make_host(World()), make_llm(), store=store,
               run_id="f").run(max_ticks=1)
    wal = tmp_path / "r" / "wal.jsonl"
    lines = wal.read_text().splitlines()
    wal.write_text(lines[0] + "\n\n" + "\n".join(lines[1:]) + "\n")
    with pytest.raises(DurableError, match="vide"):
        FileStore(tmp_path / "r").load()


def test_sqlite_lists_no_run_in_an_empty_file(tmp_path):
    (tmp_path / "empty.db").write_bytes(b"")
    assert SQLiteStore.runs(tmp_path / "empty.db") == []


# ------------------------------------------------------------------ CLI
HOST_MODULE = '''
from agentl import Host, Symbol
from agentl.kernel import current_action

LEDGER = []

class Crash(BaseException):
    pass

def build():
    import os
    host = Host()
    host.sensors["queue.pending"] = lambda: 0 if LEDGER else 1

    @host.tool("transfer")
    def transfer(amount, to):
        LEDGER.append(current_action().idempotency_key)
        if os.environ.get("AGENTL_TEST_CRASH") == "1":
            raise Crash("coupure après l'effet")
        return {"receipt": "rcpt-1"}
    return host, None
'''

PAY = """
AGENT pay {
  OBSERVE { queue.pending }
  TOOL transfer {
    INPUT { amount: Number, to: String }  OUTPUT { receipt: String }
    SIDE_EFFECT { bank.ledger }  RISK HIGH
  }
  POLICY { DEFAULT ALLOW  NEVER transfer WHEN tools.transfer.in_doubt == true }
  PLAN p WHEN queue.pending > 0 { transfer(amount=100, to="acct-9") }
}
"""


def _cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = agentl_main(list(args))
    return code, out.getvalue(), err.getvalue()


def test_cli_durable_run_status_export_and_replay(tmp_path, monkeypatch):
    agent = tmp_path / "pay.agent"
    agent.write_text(PAY, encoding="utf-8")
    (tmp_path / "pay.py").write_text(HOST_MODULE, encoding="utf-8")
    runs = tmp_path / "runs"
    monkeypatch.setenv("AGENTL_TEST_CRASH", "1")
    with pytest.raises(BaseException) as crash:
        _cli("run", str(agent), "--ticks", "2", "--durable", str(runs),
             "--run-id", "cli", "--quiet")
    assert type(crash.value).__name__ == "Crash"

    code, out, _ = _cli("durable", "status", str(runs))
    assert code == 0 and "intention sans résultat : transfer" in out
    code, _, err = _cli("durable", "export", str(runs))
    assert code == 1 and "non terminée" in err

    monkeypatch.setenv("AGENTL_TEST_CRASH", "0")
    code, out, err = _cli("run", str(agent), "--ticks", "2", "--durable",
                          str(runs), "--quiet")
    assert code == 0
    assert "reprise de l'exécution durable cli" in err
    assert "INDÉTERMINÉE" in out
    code, out, _ = _cli("durable", "status", str(runs))
    assert "completed" in out and "INDÉTERMINÉE" in out

    exported = tmp_path / "journal.json"
    code, out, _ = _cli("durable", "export", str(runs), "-o", str(exported))
    assert code == 0 and exported.exists()
    code, out, _ = _cli("replay", str(exported), "--quiet")
    assert code == 0 and "rejeu conforme" in out
    meta = json.loads((runs / "meta.json").read_text())
    assert meta["format"] == DURABLE_FORMAT and meta["run_id"] == "cli"


def test_cli_durable_refusals(tmp_path):
    agent = tmp_path / "pay.agent"
    agent.write_text(PAY, encoding="utf-8")
    (tmp_path / "pay.py").write_text(HOST_MODULE, encoding="utf-8")
    code, _, err = _cli("run", str(agent), "--durable", str(tmp_path / "d"),
                        "--record", str(tmp_path / "j.json"))
    assert code == 2 and "exclusifs" in err
    code, _, err = _cli("durable", "status", str(tmp_path / "nothing"))
    assert code == 2 and "aucun journal" in err

"""Régressions de la 1.15 : une action reprise est rejugée sur le monde d'aujourd'hui.

Jusqu'en 1.14, une intention restée sans résultat était relancée (outil
idempotent) ou exécutée (réconciliateur : `NOT_EXECUTED`) sur la décision
re-dérivée du journal — donc sur les lectures du processus mort, quel que soit
le temps écoulé. Un compte gelé entre le crash et la reprise recevait quand
même le virement. La politique est désormais réévaluée sur les capteurs que
ses gardes nomment, relus en direct, et une approbation plus vieille que
`max_resume_age` est redemandée.

    python -m pytest tests/test_v115_regressions.py -q
"""
from __future__ import annotations

import json

import pytest

from agentl import Host, MockLLM, Symbol, parse_source
from agentl.durable import (DurableRun, MemoryStore, NOT_EXECUTED,
                            resolutions_of)
from agentl.kernel import current_action
from agentl.kernel.errors import ActionInDoubt, ActionRevoked, ApprovalLapsed
from agentl.replay import (Journal, ReplayHost, ReplayLLM, decode, encode,
                           verify_trace)
from agentl.runtime import Runtime
from agentl.seal import chain_step

SOURCE = """
AGENT payments {
  OBSERVE { queue.pending, account.frozen }
  TOOL transfer {
    INPUT       { amount: Number, to: String }
    OUTPUT      { receipt: String }
    SIDE_EFFECT { bank.ledger }
    RISK        HIGH
  }
  POLICY {
    DEFAULT ALLOW
    NEVER transfer WHEN account.frozen == yes
  }
  PLAN pay WHEN queue.pending > 0 {
    STEP send { transfer(amount=100, to="acct-9") }
  }
}
"""
AGENT = parse_source(SOURCE).agents[0]
APPROVAL = parse_source(SOURCE.replace(
    "DEFAULT ALLOW", "DEFAULT ALLOW\n    REQUIRE APPROVAL FOR transfer")).agents[0]


class Crash(BaseException):
    """Mort du processus — rien ne l'avale."""


class World:
    """Le monde extérieur : il survit aux crashs, et il change pendant."""

    def __init__(self) -> None:
        self.ledger: list = []
        self.by_key: dict = {}
        self.frozen = "no"
        self.reads: list = []


def make_host(world: World, mode: str, *, crash: str = "") -> Host:
    """`crash` : "before" (coupure avant l'effet), "after" (après), "" (aucun)."""
    host = Host()
    host.sensors["queue.pending"] = lambda: 0 if world.ledger else 1

    def frozen():
        world.reads.append("account.frozen")
        return Symbol(world.frozen)

    host.sensors["account.frozen"] = frozen
    armed = {"crash": crash}

    def transfer(amount, to):
        key = current_action().idempotency_key
        if mode in ("idempotent", "both") and key in world.by_key:
            return dict(world.by_key[key])
        if armed["crash"] == "before":
            armed["crash"] = ""
            raise Crash("coupure réseau avant l'effet")
        world.ledger.append((amount, to, key))
        world.by_key[key] = {"receipt": f"r{len(world.ledger)}"}
        if armed["crash"] == "after":
            armed["crash"] = ""
            raise Crash("coupure après l'effet")
        return dict(world.by_key[key])

    host.tool("transfer", idempotent=mode in ("idempotent", "both"))(transfer)
    if mode in ("reconcile", "both"):
        @host.reconciler("transfer")
        def lookup(args, context):
            found = world.by_key.get(context.idempotency_key)
            return dict(found) if found else NOT_EXECUTED
    return host


def crash_then(world: World, mode: str, crash: str, agent=AGENT,
               approver=None, ticks: int = 3) -> MemoryStore:
    disk = MemoryStore()
    host = make_host(world, mode, crash=crash)
    if approver is not None:
        host.approver = approver
    with pytest.raises(Crash):
        DurableRun(agent, host, MockLLM(), store=disk).run(max_ticks=ticks)
    return disk


def resume(world: World, mode: str, disk: MemoryStore, agent=AGENT,
           approver=None, **options):
    host = make_host(world, mode)
    if approver is not None:
        host.approver = approver
    run = DurableRun(agent, host, MockLLM(), store=disk, **options)
    return run, run.run(max_ticks=3)


def lines(runtime: Runtime) -> str:
    return runtime.trace.render()


# ------------------------------------------------- le défaut, reproduit
def test_an_idempotent_retry_is_refused_when_the_world_now_forbids_it():
    world = World()
    disk = crash_then(world, "idempotent", "before")
    world.frozen = "yes"                       # gelé pendant que l'agent était mort

    run, rt = resume(world, "idempotent", disk)

    assert world.ledger == []                  # 1.14 : le virement partait
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "in_doubt"
    assert resolution["recheck"]["verdict"] == "DENIED"
    assert resolution["recheck"]["facts"] == {"account.frozen": "yes"}
    # On ne sait pas si la requête d'avant le crash avait abouti : l'action
    # n'est pas relancée, et elle est indéterminée — pas « bloquée ».
    assert rt.state.get("tools.transfer.in_doubt") is True
    assert "réévaluée à la reprise" in lines(rt)


def test_an_idempotent_retry_still_happens_when_the_world_still_allows_it():
    world = World()
    disk = crash_then(world, "idempotent", "before")

    run, rt = resume(world, "idempotent", disk)

    assert len(world.ledger) == 1
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "retry"
    assert resolution["recheck"] == {"facts": {"account.frozen": "no"},
                                     "verdict": "ALLOWED", "approval": None}
    assert rt.state.get("tools.transfer.in_doubt") is False


def test_a_not_executed_action_is_revoked_not_in_doubt():
    world = World()
    disk = crash_then(world, "reconcile", "before")
    world.frozen = "yes"

    run, rt = resume(world, "reconcile", disk)

    assert world.ledger == []
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "revoked"
    # Le réconciliateur a dit « pas exécutée » : on le sait, rien n'est sale.
    assert rt.state.get("tools.transfer.in_doubt") is False
    assert rt.state.get("tools.transfer.denied") is True
    assert rt.state.get("last_action.blocked") is True
    # Une seule révocation : les ticks suivants sont des refus ordinaires.
    assert lines(rt).count("refusé à la reprise") == 1


def test_an_effect_that_already_happened_is_reconciled_whatever_the_facts():
    world = World()
    disk = crash_then(world, "reconcile", "after")
    world.frozen = "yes"

    run, rt = resume(world, "reconcile", disk)

    assert len(world.ledger) == 1              # pas d'effet nouveau, et il est su
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "reconciled"
    assert "recheck" not in resolution         # rien à rejuger : rien n'est exécuté


@pytest.mark.parametrize("crash,how,ledger", [("before", "revoked", 0),
                                              ("after", "reconciled", 1)])
def test_a_refused_retry_asks_the_reconciler_before_declaring_doubt(
        crash, how, ledger):
    world = World()
    disk = crash_then(world, "both", crash)
    world.frozen = "yes"

    run, rt = resume(world, "both", disk)

    assert len(world.ledger) == ledger
    assert [r["how"] for r in resolutions_of(run.journal)] == [how]
    assert rt.state.get("tools.transfer.in_doubt") is False


def test_a_sensor_down_at_resume_fails_closed():
    # Un seul tick : on juge la relance, pas la perception des ticks suivants.
    world = World()
    disk = crash_then(world, "reconcile", "before", ticks=1)
    host = make_host(world, "reconcile")

    def down():
        raise ConnectionError("capteur injoignable")
    host.sensors["account.frozen"] = down

    run = DurableRun(AGENT, host, MockLLM(), store=disk)
    rt = run.run(max_ticks=1)

    assert world.ledger == []
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "revoked"
    assert resolution["recheck"]["facts"] == {"account.frozen": None}
    assert rt.state.get("tools.transfer.denied") is True


def test_only_the_sensors_named_by_the_guards_are_read_again():
    source = SOURCE.replace("NEVER transfer WHEN account.frozen == yes",
                            "NEVER transfer WHEN tools.transfer.in_doubt == true")
    agent = parse_source(source).agents[0]
    world = World()
    disk = crash_then(world, "idempotent", "before", agent=agent)
    world.frozen = "yes"                       # aucune garde ne le lit

    run, _ = resume(world, "idempotent", disk, agent=agent)

    assert len(world.ledger) == 1
    (resolution,) = resolutions_of(run.journal)
    assert resolution["recheck"]["facts"] == {}


# ------------------------------------------------------ approbation périmée
def _approver(answers, log):
    def approve(request):
        log.append(request.render())
        return answers.pop(0)
    return approve


def test_a_recent_approval_is_not_asked_again():
    world, asked = World(), []
    disk = crash_then(world, "idempotent", "before", agent=APPROVAL,
                      approver=_approver([True], asked))

    run, _ = resume(world, "idempotent", disk, agent=APPROVAL,
                    approver=_approver([], asked))

    assert asked == ["transfer(amount=100, to=acct-9)"]
    assert len(world.ledger) == 1
    assert resolutions_of(run.journal)[0]["recheck"]["approval"] == "kept"


def test_an_approval_older_than_the_limit_is_asked_again():
    world, asked = World(), []
    disk = crash_then(world, "idempotent", "before", agent=APPROVAL,
                      approver=_approver([True], asked))

    run, _ = resume(world, "idempotent", disk, agent=APPROVAL,
                    approver=_approver([True], asked), max_resume_age=0)

    assert asked == ["transfer(amount=100, to=acct-9)"] * 2
    assert len(world.ledger) == 1
    assert resolutions_of(run.journal)[0]["recheck"]["approval"] == "renewed"


def test_a_lapsed_approval_refused_by_the_human_revokes_the_action():
    world, asked = World(), []
    disk = crash_then(world, "reconcile", "before", agent=APPROVAL,
                      approver=_approver([True], asked))

    run, rt = resume(world, "reconcile", disk, agent=APPROVAL,
                     approver=_approver(["no"], asked), max_resume_age=0)

    assert world.ledger == []
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "revoked"
    assert resolution["recheck"]["approval"] == "refused"
    assert rt.state.get("tools.transfer.unapproved") is True
    assert rt.state.get("tools.transfer.denied") is False


def test_an_intent_without_a_date_counts_as_lapsed():
    """Journal écrit avant la 1.15 : l'âge est inconnu, l'humain est reconsulté."""
    world, asked = World(), []
    disk = crash_then(world, "idempotent", "before", agent=APPROVAL,
                      approver=_approver([True], asked))
    _, raws = disk.load()
    previous = ""
    for raw in raws:
        if raw["kind"] == "intent":
            args = decode(raw["args"])
            args.pop("at")
            raw["args"] = encode(args)
        raw.pop("h")
        raw["h"] = previous = chain_step(previous, raw, None)
    disk.lines = [json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                  for raw in raws]

    run, _ = resume(world, "idempotent", disk, agent=APPROVAL,
                    approver=_approver([True], asked))

    assert len(asked) == 2
    assert resolutions_of(run.journal)[0]["recheck"]["approval"] == "renewed"


def test_an_unlimited_age_keeps_the_former_behaviour():
    world, asked = World(), []
    disk = crash_then(world, "idempotent", "before", agent=APPROVAL,
                      approver=_approver([True], asked))

    resume(world, "idempotent", disk, agent=APPROVAL,
           approver=_approver([], asked), max_resume_age=None)

    assert len(asked) == 1 and len(world.ledger) == 1


# ------------------------------------------------------------ déterminisme
@pytest.mark.parametrize("mode,frozen", [("idempotent", "yes"),
                                         ("idempotent", "no"),
                                         ("reconcile", "yes")])
def test_a_second_resume_replays_the_verdict_without_asking_the_world(
        mode, frozen):
    world = World()
    disk = crash_then(world, mode, "before")
    world.frozen = frozen
    first, rt1 = resume(world, mode, disk)
    trace = lines(rt1)

    world.frozen = "no" if frozen == "yes" else "yes"   # le monde change encore
    reads = len(world.reads)
    again, rt2 = resume(world, mode, disk)

    assert lines(rt2) == trace
    assert len(world.reads) == reads           # ni capteur relu, ni effet
    assert resolutions_of(again.journal) == resolutions_of(first.journal)


def test_a_revoked_resume_replays_with_plain_replay():
    world = World()
    disk = crash_then(world, "reconcile", "before")
    world.frozen = "yes"
    run, rt = resume(world, "reconcile", disk)

    journal = Journal.loads(run.export().dumps(), strict=False)
    replayed = Runtime(AGENT, ReplayHost(journal), ReplayLLM(journal))
    replayed.run(max_ticks=3)

    assert replayed.trace.render() == lines(rt)
    assert verify_trace(journal, replayed.trace.render())
    assert journal.exhausted


def test_the_errors_are_distinct_and_replayable():
    from agentl.replay import _rebuild_error
    assert issubclass(ApprovalLapsed, ActionRevoked)
    assert not issubclass(ActionRevoked, ActionInDoubt)
    assert ActionRevoked.unapproved is False and ApprovalLapsed.unapproved is True
    assert type(_rebuild_error("ApprovalLapsed", "x")) is ApprovalLapsed
    assert type(_rebuild_error("ActionRevoked", "x")) is ActionRevoked


# ------------------------------------- crash entre perception et intention
def _dies(request):
    raise Crash("le processus meurt pendant que l'humain réfléchit")


def _crash_while_waiting(world: World, agent=APPROVAL) -> MemoryStore:
    disk = MemoryStore()
    host = make_host(world, "plain")
    host.approver = _dies
    with pytest.raises(Crash):
        DurableRun(agent, host, MockLLM(), store=disk).run(max_ticks=3)
    _, raws = disk.load()
    assert [r["kind"] for r in raws] == ["read", "read", "drain"]
    return disk


def test_a_new_action_of_a_replayed_tick_is_judged_on_todays_world():
    """La perception du tick est rejouée, l'approbation redemandée en direct :
    sans rejugement, le virement partait sur le monde d'avant le crash."""
    world, asked = World(), []
    disk = _crash_while_waiting(world)
    world.frozen = "yes"

    run, rt = resume(world, "plain", disk, agent=APPROVAL,
                     approver=_approver([True], asked))

    assert world.ledger == []
    assert [r["how"] for r in resolutions_of(run.journal)] == ["revoked"]
    assert lines(rt).count("refusé à la reprise") == 1
    assert rt.state.get("tools.transfer.denied") is True


def test_a_new_action_of_a_replayed_tick_runs_when_the_world_allows_it():
    world, asked = World(), []
    disk = _crash_while_waiting(world)

    run, rt = resume(world, "plain", disk, agent=APPROVAL,
                     approver=_approver([True], asked))

    assert len(world.ledger) == 1
    assert asked == ["transfer(amount=100, to=acct-9)"]   # demandée une fois
    assert resolutions_of(run.journal) == []
    assert run.journal._stale_tick is False   # tick suivant : perception vive


def test_a_resume_at_a_tick_boundary_judges_nothing_again():
    world = World()
    disk = MemoryStore()
    DurableRun(AGENT, make_host(world, "plain"), MockLLM(),
               store=disk).run(max_ticks=1)
    run = DurableRun(AGENT, make_host(world, "plain"), MockLLM(), store=disk)
    assert run.journal._stale_tick is False


def test_a_delegation_of_a_replayed_tick_is_judged_too():
    source = """
AGENT boss {
  OBSERVE { account.frozen }
  TOOL helper { INPUT { } OUTPUT { done: Symbol } RISK HIGH }
  POLICY {
    DEFAULT ALLOW
    REQUIRE APPROVAL FOR helper
    NEVER helper WHEN account.frozen == yes
  }
  PLAN go WHEN account.frozen != unknown {
    STEP ask { DELEGATE helper { TASK "aider" INPUT { } EXPECT { done } } }
  }
}
"""
    agent = parse_source(source).agents[0]
    world, calls = World(), []

    def host_with(approver):
        host = Host()
        host.sensors["account.frozen"] = lambda: Symbol(world.frozen)
        host.subagents["helper"] = lambda payload: calls.append(1) or {
            "done": Symbol("yes")}
        host.approver = approver
        return host

    disk = MemoryStore()
    with pytest.raises(Crash):
        DurableRun(agent, host_with(_dies), MockLLM(), store=disk).run(1)
    world.frozen = "yes"
    run = DurableRun(agent, host_with(lambda r: True), MockLLM(), store=disk)
    rt = run.run(1)

    assert calls == []
    assert "DELEGATE helper refusé à la reprise" in lines(rt)
    assert rt.state.get("tools.helper.denied") is True


# ------------------------------------------------------------------- CLI
_CLI_HOST = '''
import os
from agentl import Host, Symbol
from agentl.durable import NOT_EXECUTED

class Crash(BaseException):
    pass

def build():
    host = Host()
    host.sensors["queue.pending"] = lambda: 1
    host.sensors["account.frozen"] = lambda: Symbol(
        os.environ.get("AGENTL_TEST_FROZEN", "no"))

    @host.tool("transfer")
    def transfer(amount, to):
        if os.environ.get("AGENTL_TEST_CRASH") == "1":
            raise Crash("coupure avant l'effet")
        return {"receipt": "r1"}

    @host.reconciler("transfer")
    def lookup(args, context):
        return NOT_EXECUTED
    return host, None
'''


def test_cli_reports_a_revoked_action_and_takes_the_age_option(
        tmp_path, monkeypatch, capsys):
    from agentl.cli import main
    agent = tmp_path / "frozen_pay.agent"
    agent.write_text(SOURCE, encoding="utf-8")
    (tmp_path / "frozen_pay.py").write_text(_CLI_HOST, encoding="utf-8")
    runs = str(tmp_path / "runs")
    monkeypatch.setenv("AGENTL_TEST_CRASH", "1")
    with pytest.raises(BaseException) as crash:
        main(["run", str(agent), "--ticks", "1", "--durable", runs, "--quiet"])
    assert type(crash.value).__name__ == "Crash"

    monkeypatch.setenv("AGENTL_TEST_CRASH", "0")
    monkeypatch.setenv("AGENTL_TEST_FROZEN", "yes")
    capsys.readouterr()
    assert main(["run", str(agent), "--ticks", "1", "--durable", runs,
                 "--max-resume-age", "0", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "RÉVOQUÉE — rejugée à la reprise et refusée" in out
    assert "politique DENIED" in out
    assert main(["durable", "status", runs]) == 0
    assert "RÉVOQUÉE" in capsys.readouterr().out


def test_the_resolution_label_names_the_recheck():
    from agentl.cli import _resolution_label
    assert _resolution_label({"how": "retry"}) == \
        "relancée avec la même clé d'idempotence"
    assert _resolution_label({"how": "retry", "recheck": {
        "verdict": "APPROVAL_REQUIRED", "approval": "renewed"}}) == (
        "relancée avec la même clé d'idempotence (politique APPROVAL_REQUIRED, "
        "approbation redemandée et accordée)")
    assert _resolution_label({"how": "in_doubt", "recheck": {
        "verdict": "DENIED", "approval": None}}).startswith(
        "INDÉTERMINÉE — rejugée à la reprise et refusée")

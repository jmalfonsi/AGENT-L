"""1.16 : `DELEGATE … AGENT` — un agent en délègue un autre, sous droits bornés.

    python -m pytest tests/test_v116_delegation.py -q
"""
from __future__ import annotations

import pytest

from agentl import Host, MockLLM, Symbol
from agentl.compose import ComposeError, delegation_digest
from agentl.core import UNDEFINED
from agentl.parser import parse_file
from agentl.runtime import Runtime

CHILD = """
AGENT forensic {
  %(observe)s
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL read_mail { INPUT { } OUTPUT { subject: String } RISK LOW }
  TOOL tag_mail  { INPUT { label: String } OUTPUT { ok: Symbol }
                   SIDE_EFFECT { mail.tags } RISK %(tag_risk)s }
  TOOL wipe      { INPUT { } OUTPUT { ok: Symbol } RISK CRITICAL }
  POLICY { DEFAULT ALLOW %(child_policy)s }
  PLAN inspect WHEN done == no {
    STEP a { read_mail() }
    STEP b { tag_mail(label="phishing") }
    %(extra)s
    STEP c { SET malicious = yes  SET threat_class = phishing  SET done = yes }
  }
}
"""

PARENT = """
AGENT boss {
  OBSERVE { mail.reported, mail.sender }
  TOOL read_mail { INPUT { } OUTPUT { subject: String } RISK LOW }
  TOOL tag_mail  { INPUT { label: String } OUTPUT { ok: Symbol }
                   SIDE_EFFECT { mail.tags } RISK HIGH }
  TOOL wipe      { INPUT { } OUTPUT { ok: Symbol } RISK CRITICAL }
  POLICY {
    DEFAULT ALLOW
    NEVER wipe
    %(parent_policy)s
  }
  PLAN investigate WHEN mail.reported == yes {
    STEP delegate {
      DELEGATE forensic {
        AGENT     "forensic.agent"
        TASK      "analyser l'e-mail signalé"
        INPUT     { mail.sender }
        EXPECT    { malicious, threat_class }
        GRANT     { %(grant)s }
        %(options)s
      }
    }
  }
}
"""


class World:
    def __init__(self) -> None:
        self.calls: list = []
        self.approved: list = []


def make_host(world: World, answers=(True,)) -> Host:
    host = Host()
    host.sensors["mail.reported"] = lambda: Symbol("yes")
    host.sensors["mail.sender"] = lambda: Symbol("evil@example.com")
    answers = list(answers)

    def tool(name, result):
        def fn(**kw):
            world.calls.append(name)
            return result
        return fn

    host.tool("read_mail")(tool("read_mail", {"subject": "urgent"}))
    host.tool("tag_mail")(tool("tag_mail", {"ok": Symbol("yes")}))
    host.tool("wipe")(tool("wipe", {"ok": Symbol("yes")}))
    host.approver = lambda request: world.approved.append(request.render()) \
        or (answers.pop(0) if answers else False)
    return host


def write(tmp_path, *, grant="read_mail, tag_mail", parent_policy="",
          child_policy="", options="", tag_risk="LOW", extra="",
          child_name="forensic.agent", observe=""):
    (tmp_path / child_name).write_text(
        CHILD % dict(tag_risk=tag_risk, child_policy=child_policy,
                     extra=extra, observe=observe), encoding="utf-8")
    parent = tmp_path / "boss.agent"
    parent.write_text(PARENT % dict(grant=grant, parent_policy=parent_policy,
                                    options=options), encoding="utf-8")
    return parse_file(str(parent)).agents[0]


def run(tmp_path, ticks=2, host=None, world=None, **kw):
    agent = write(tmp_path, **kw)
    world = world or World()
    rt = Runtime(agent, host or make_host(world), MockLLM())
    rt.run(max_ticks=ticks)
    return rt, world


# ------------------------------------------------------------- le cas nominal
def test_the_child_runs_its_own_tools_and_returns_its_expect(tmp_path):
    rt, world = run(tmp_path, ticks=1)

    assert world.calls == ["read_mail", "tag_mail"]
    assert rt.state.get("forensic.malicious") == Symbol("yes")
    assert rt.state.get("forensic.threat_class") == Symbol("phishing")
    assert rt.metrics["delegations"] == 1
    text = rt.trace.render()
    assert "[forensic] " in text and "tag_mail(label=phishing)" in text


def test_the_return_is_untrusted_for_the_parent(tmp_path):
    rt, _ = run(tmp_path, ticks=1)

    _, label = rt.state.get_labeled("forensic.malicious")
    assert "DELEGATE" in {str(s) for s in label.sources}
    _, bare = rt.state.get_labeled("malicious")
    assert "DELEGATE" in {str(s) for s in bare.sources}


def test_the_task_and_inputs_reach_the_child_with_their_provenance(tmp_path):
    seen = {}
    host = make_host(World())

    class Spy(MockLLM):
        pass
    agent = write(tmp_path, extra="")
    rt = Runtime(agent, host, Spy())
    # l'enfant lit l'entrée posée par le parent
    (tmp_path / "forensic.agent").write_text(
        CHILD.replace("STEP c {", "STEP c { SET sender_seen = mail.sender  ")
        % dict(tag_risk="LOW", child_policy="", extra="", observe=""),
        encoding="utf-8")
    parent = tmp_path / "boss.agent"
    parent.write_text(PARENT.replace("EXPECT    { malicious, threat_class }",
                                     "EXPECT    { malicious, sender_seen }")
                      % dict(grant="read_mail, tag_mail", parent_policy="",
                             options=""), encoding="utf-8")
    rt = Runtime(parse_file(str(parent)).agents[0], host, MockLLM())
    rt.run(max_ticks=1)

    assert rt.state.get("forensic.sender_seen") == Symbol("evil@example.com")


# ------------------------------------------------- l'enfant n'atteint que GRANT
def test_a_tool_outside_grant_does_not_exist_for_the_child(tmp_path):
    rt, world = run(tmp_path, ticks=1, grant="read_mail",
                    extra="STEP w { wipe() }")

    assert "tag_mail" not in world.calls and "wipe" not in world.calls
    text = rt.trace.render()
    assert "non accordé (GRANT)" in text


def test_the_granted_view_is_not_the_parents_host(tmp_path):
    from agentl.compose import GrantedHost
    host = make_host(World())
    view = GrantedHost(host, ["read_mail"])
    assert view.subagents == {} and view.drain() == []
    with pytest.raises(KeyError):
        view.invoke("tag_mail", {})


# ---------------------------------------------------- le plafond du parent
def test_the_parent_policy_caps_the_child(tmp_path):
    rt, world = run(tmp_path, ticks=1,
                    parent_policy="NEVER tag_mail WHEN mail.sender == evil")

    # `evil@example.com` est un Symbol ; la garde compare l'identifiant.
    assert world.calls == ["read_mail", "tag_mail"]   # garde fausse : passe

    rt, world = run(tmp_path, ticks=1,
                    parent_policy='NEVER tag_mail WHEN mail.reported == yes')
    assert world.calls == ["read_mail"]
    assert "plafond du parent" in rt.trace.render()


def test_the_child_cannot_relax_what_the_parent_denies(tmp_path):
    rt, world = run(tmp_path, ticks=1,
                    parent_policy="DENY tag_mail",
                    child_policy="ALLOW tag_mail")

    assert world.calls == ["read_mail"]
    assert rt.state.get("last_action.blocked") is not None


def test_default_deny_in_the_parent_means_default_deny_for_the_child(tmp_path):
    rt, world = run(tmp_path, ticks=1,
                    parent_policy="", options="")
    assert len(world.calls) == 2                      # base : DEFAULT ALLOW

    agent = write(tmp_path)
    agent.policy_default = "DENY"
    world2 = World()
    rt2 = Runtime(agent, make_host(world2), MockLLM())
    rt2.run(max_ticks=1)
    assert world2.calls == []                         # ni read_mail, ni tag_mail


def test_the_parent_declares_the_risk_the_child_cannot_understate(tmp_path):
    """tag_mail : RISK LOW chez l'enfant, HIGH chez le parent. Une règle du
    parent sur `action.risk` voit HIGH — l'enfant n'abaisse pas le risque."""
    rule = "REQUIRE APPROVAL FOR tag_mail WHEN action.risk == HIGH"
    rt, world = run(tmp_path, ticks=1, tag_risk="LOW", parent_policy=rule)

    assert world.approved and "tag_mail" in world.approved[0]
    assert world.calls == ["read_mail", "tag_mail"]

    # la même règle sur le risque que l'enfant déclare ne mord pas : preuve que
    # la décision vient bien de la déclaration du parent
    world2 = World()
    agent = write(tmp_path, tag_risk="LOW", parent_policy=rule)
    agent.tools[1].risk = "LOW"                       # le parent s'aligne
    rt2 = Runtime(agent, make_host(world2), MockLLM())
    rt2.run(max_ticks=1)
    assert world2.approved == []


def test_a_refused_approval_stops_the_childs_action(tmp_path):
    world = World()
    rt, _ = run(tmp_path, ticks=1, world=world,
                host=make_host(world, answers=(False,)),
                parent_policy="REQUIRE APPROVAL FOR tag_mail")

    assert world.calls == ["read_mail"]
    assert "non approuvé" in rt.trace.render()


def test_a_critical_tool_the_parent_forbids_stays_forbidden_even_if_granted(tmp_path):
    rt, world = run(tmp_path, ticks=1, grant="read_mail, wipe",
                    extra="STEP w { wipe() }")

    assert "wipe" not in world.calls           # NEVER wipe, chez le parent
    assert "plafond du parent" in rt.trace.render()


# ------------------------------------------------------------ bornes et refus
def test_max_ticks_bounds_the_child(tmp_path):
    source = CHILD.replace("SET done = yes", "SET other = yes")
    (tmp_path / "forensic.agent").write_text(
        source % dict(tag_risk="LOW", child_policy="", extra="", observe=""),
        encoding="utf-8")
    parent = tmp_path / "boss.agent"
    parent.write_text(PARENT % dict(grant="read_mail, tag_mail",
                                    parent_policy="", options="MAX_TICKS 2"),
                      encoding="utf-8")
    world = World()
    rt = Runtime(parse_file(str(parent)).agents[0], make_host(world), MockLLM())
    rt.run(max_ticks=1)

    assert world.calls == ["read_mail", "tag_mail"] * 2   # 2 ticks, pas 6


@pytest.mark.parametrize("name,expected", [
    ("/etc/passwd", "chemin relatif"),
    ("../outside.agent", "sort du répertoire"),
    ("absent.agent", "illisible"),
])
def test_a_bad_path_is_a_refusal_not_a_crash(tmp_path, name, expected):
    agent = write(tmp_path)
    parent = tmp_path / "boss.agent"
    parent.write_text(PARENT.replace("forensic.agent", name)
                      % dict(grant="read_mail", parent_policy="", options=""),
                      encoding="utf-8")
    world = World()
    rt = Runtime(parse_file(str(parent)).agents[0], make_host(world), MockLLM())
    rt.run(max_ticks=1)

    assert world.calls == []
    assert expected in rt.trace.render()


def test_a_file_with_two_agents_is_refused(tmp_path):
    agent = write(tmp_path)
    (tmp_path / "forensic.agent").write_text(
        "AGENT a { }\nAGENT b { }\n", encoding="utf-8")
    world = World()
    rt = Runtime(agent, make_host(world), MockLLM())
    rt.run(max_ticks=1)
    assert "exactement un agent" in rt.trace.render()


GRANDCHILD = """
AGENT inner {
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL read_mail { INPUT { } OUTPUT { subject: String } RISK LOW }
  POLICY { DEFAULT ALLOW }
  PLAN go WHEN done == no { STEP a { read_mail() }  STEP b { SET deep = yes  SET done = yes } }
}
"""
MIDDLE = """
AGENT forensic {
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL read_mail { INPUT { } OUTPUT { subject: String } RISK LOW }
  POLICY { DEFAULT ALLOW }
  PLAN go WHEN done == no {
    STEP a { DELEGATE inner { AGENT "inner.agent"  TASK "plus profond"
                              EXPECT { deep }  GRANT { read_mail } } }
    STEP b { SET malicious = yes  SET done = yes }
  }
}
"""


def test_depth_is_one_by_default_and_opt_in(tmp_path):
    (tmp_path / "inner.agent").write_text(GRANDCHILD, encoding="utf-8")
    (tmp_path / "forensic.agent").write_text(MIDDLE, encoding="utf-8")
    parent = tmp_path / "boss.agent"
    body = PARENT % dict(grant="read_mail", parent_policy="", options="%s")
    parent.write_text(body % "", encoding="utf-8")
    world = World()
    rt = Runtime(parse_file(str(parent)).agents[0], make_host(world), MockLLM())
    rt.run(max_ticks=1)
    assert "profondeur de délégation épuisée" in rt.trace.render()
    assert world.calls == []

    parent.write_text(body % "MAX_DEPTH 2", encoding="utf-8")
    world = World()
    rt = Runtime(parse_file(str(parent)).agents[0], make_host(world), MockLLM())
    rt.run(max_ticks=1)
    assert world.calls == ["read_mail"]
    assert rt.state.get("forensic.malicious") == Symbol("yes")


def test_a_cycle_of_files_is_refused(tmp_path):
    (tmp_path / "a.agent").write_text(
        'AGENT a { PLAN p { STEP s { DELEGATE b { AGENT "b.agent" TASK "x" } } } }\n')
    (tmp_path / "b.agent").write_text(
        'AGENT b { PLAN p { STEP s { DELEGATE a { AGENT "a.agent" TASK "x" } } } }\n')
    from agentl.compose import closure
    agent = parse_file(str(tmp_path / "a.agent")).agents[0]
    with pytest.raises(ComposeError, match="cycle"):
        closure(agent)


# --------------------------------------------------------- empreinte du programme
def test_the_childs_content_is_part_of_the_programs_identity(tmp_path):
    agent = write(tmp_path)
    before = delegation_digest([agent])
    assert before and before == delegation_digest([agent])

    (tmp_path / "forensic.agent").write_text(
        (tmp_path / "forensic.agent").read_text() + "\n// modifié\n")
    agent2 = parse_file(str(tmp_path / "boss.agent")).agents[0]
    assert delegation_digest([agent2]) != before


def test_a_program_without_agent_delegations_keeps_its_digest():
    from agentl import parse_source
    from agentl.durable import program_digest
    agent = parse_source(
        "AGENT x { TOOL t { INPUT { } OUTPUT { } RISK LOW } }").agents[0]
    assert delegation_digest([agent]) == ""
    assert program_digest([agent])           # inchangé : pas de section compose


def test_python_function_delegation_still_works():
    from agentl import parse_source
    agent = parse_source("""
AGENT old {
  OBSERVE { go }
  TOOL helper { INPUT { } OUTPUT { done: Symbol } RISK LOW }
  PLAN p WHEN go == yes { STEP s { DELEGATE helper { TASK "aider" EXPECT { done } } } }
}""").agents[0]
    host = Host()
    host.sensors["go"] = lambda: Symbol("yes")
    host.subagents["helper"] = lambda payload: {"done": Symbol("yes")}
    rt = Runtime(agent, host, MockLLM())
    rt.run(max_ticks=1)
    assert rt.state.get("helper.done") == Symbol("yes")


# ------------------------------------------------------- exécution durable
class Crash(BaseException):
    """Mort du processus — rien ne l'avale."""


class Bank:
    """Le monde extérieur : il survit aux crashs, et il change pendant."""

    def __init__(self) -> None:
        self.tags: list = []
        self.by_key: dict = {}
        self.reads: list = []
        self.frozen = "no"


def durable_host(bank: Bank, *, crash: bool = False) -> Host:
    from agentl.kernel import current_action
    host = Host()
    host.sensors["mail.reported"] = lambda: Symbol("yes")
    host.sensors["mail.sender"] = lambda: Symbol("evil@example.com")
    host.sensors["account.frozen"] = lambda: bank.reads.append(1) or Symbol(bank.frozen)
    host.tool("read_mail", idempotent=True)(
        lambda: bank.reads.append("read_mail") or {"subject": "urgent"})
    armed = {"crash": crash}

    def tag_mail(label):
        key = current_action().idempotency_key
        if key in bank.by_key:
            return dict(bank.by_key[key])
        if armed["crash"]:
            armed["crash"] = False
            raise Crash("coupure avant l'effet")
        bank.tags.append((label, key))
        bank.by_key[key] = {"ok": Symbol("yes")}
        return dict(bank.by_key[key])
    host.tool("tag_mail", idempotent=True)(tag_mail)
    return host


def durable_agent(tmp_path, *, child_policy="", parent_policy=""):
    agent = write(tmp_path, child_policy=child_policy, parent_policy=parent_policy,
                  observe="OBSERVE { account.frozen }")
    parent = tmp_path / "boss.agent"
    parent.write_text(parent.read_text().replace(
        "OBSERVE { mail.reported, mail.sender }",
        "OBSERVE { mail.reported, mail.sender, account.frozen }"))
    return parse_file(str(parent)).agents[0]


def crash_in_child(tmp_path, bank, **kw):
    from agentl.durable import DurableRun, MemoryStore
    agent = durable_agent(tmp_path, **kw)
    disk = MemoryStore()
    with pytest.raises(Crash):
        DurableRun(agent, durable_host(bank, crash=True), MockLLM(),
                   store=disk).run(max_ticks=1)
    return disk


def resume_after(tmp_path, bank, disk, **kw):
    from agentl.durable import DurableRun
    agent = durable_agent(tmp_path, **kw)
    run = DurableRun(agent, durable_host(bank), MockLLM(), store=disk)
    return run, run.run(max_ticks=1)


def test_a_crash_inside_the_child_resumes_exactly_once(tmp_path):
    from agentl.durable import resolutions_of
    bank = Bank()
    disk = crash_in_child(tmp_path, bank)
    assert bank.tags == []

    run, rt = resume_after(tmp_path, bank, disk)

    assert len(bank.tags) == 1                       # une fois, pas deux
    assert bank.reads.count("read_mail") == 1        # l'action finie n'est pas rejouée
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "retry"
    # l'identifiant dit d'où vient l'action : parent > enfant
    assert ">forensic#" in resolution["action_id"]
    assert rt.state.get("forensic.malicious") == Symbol("yes")


def test_the_child_shares_the_parents_journal(tmp_path):
    from agentl.durable import DurableRun, MemoryStore
    bank, disk = Bank(), MemoryStore()
    DurableRun(durable_agent(tmp_path), durable_host(bank), MockLLM(),
               store=disk).run(max_ticks=1)
    _, raws = disk.load()
    intents = [r for r in raws if r["kind"] == "intent"]
    assert [r["key"] for r in intents] == ["read_mail", "tag_mail"]
    from agentl.replay import decode
    assert all(">forensic#" in decode(r["args"])["action_id"] for r in intents)


def test_resume_rejudges_the_childs_action_under_the_childs_own_policy(tmp_path):
    from agentl.durable import resolutions_of
    policy = "NEVER tag_mail WHEN account.frozen == yes"
    bank = Bank()
    disk = crash_in_child(tmp_path, bank, child_policy=policy)
    bank.frozen = "yes"

    run, rt = resume_after(tmp_path, bank, disk, child_policy=policy)

    assert bank.tags == []
    (resolution,) = resolutions_of(run.journal)
    assert resolution["how"] == "in_doubt"
    assert resolution["recheck"]["verdict"] == "DENIED"


def test_resume_rejudges_the_childs_action_under_the_parents_policy_alone(tmp_path):
    """La règle n'existe que chez le parent : le plafond la lit en direct,
    sur le capteur qu'elle nomme."""
    from agentl.durable import resolutions_of
    policy = "NEVER tag_mail WHEN account.frozen == yes"
    bank = Bank()
    disk = crash_in_child(tmp_path, bank, parent_policy=policy)
    bank.frozen = "yes"

    run, _ = resume_after(tmp_path, bank, disk, parent_policy=policy)

    assert bank.tags == []
    (resolution,) = resolutions_of(run.journal)
    assert resolution["recheck"]["verdict"] == "DENIED"


def test_changing_the_childs_file_between_crash_and_resume_is_refused(tmp_path):
    from agentl.durable import DurableError
    bank = Bank()
    disk = crash_in_child(tmp_path, bank)
    child = tmp_path / "forensic.agent"
    child.write_text(child.read_text().replace(
        'label="phishing"', 'label="exfiltration"'))
    from agentl.durable import DurableRun
    agent = parse_file(str(tmp_path / "boss.agent")).agents[0]   # sans réécrire

    with pytest.raises(DurableError, match="programme a changé"):
        DurableRun(agent, durable_host(bank), MockLLM(), store=disk)
    assert bank.tags == []


def test_a_durable_run_without_a_crash_replays_plain(tmp_path):
    from agentl.durable import DurableRun, MemoryStore
    from agentl.replay import (Journal, ReplayHost, ReplayLLM, verify_trace)
    bank, disk = Bank(), MemoryStore()
    agent = durable_agent(tmp_path)
    run = DurableRun(agent, durable_host(bank), MockLLM(), store=disk)
    run.run(max_ticks=1)

    journal = Journal.loads(run.export().dumps(), strict=False)
    replayed = Runtime(agent, ReplayHost(journal), ReplayLLM(journal))
    replayed.run(max_ticks=1)

    assert replayed.trace.render() == run.trace_text()
    assert verify_trace(journal, replayed.trace.render())


# ------------------------------------------------------------ analyse statique
def codes(agent):
    from agentl.analyzer import Analyzer
    return [d.code for d in Analyzer(agent).run()]


def test_a_clean_delegation_raises_none_of_the_new_diagnostics(tmp_path):
    agent = write(tmp_path, tag_risk="HIGH")
    found = codes(agent)
    assert not {"E020", "E018", "E019", "W142", "W143", "W127"} & set(found)
    assert "W144" in found                      # tag_mail est HIGH chez le parent


def test_grant_of_a_tool_the_parent_does_not_declare_is_an_error(tmp_path):
    agent = write(tmp_path, grant="read_mail, exfiltrate")
    assert "E018" in codes(agent)


@pytest.mark.parametrize("name,code", [
    ("absent.agent", "E020"),
    ("../outside.agent", "E020"),
])
def test_an_unusable_file_is_a_static_error(tmp_path, name, code):
    write(tmp_path)
    parent = tmp_path / "boss.agent"
    parent.write_text(parent.read_text().replace("forensic.agent", name))
    assert code in codes(parse_file(str(parent)).agents[0])


def test_a_child_with_its_own_errors_is_reported(tmp_path):
    agent = write(tmp_path)
    (tmp_path / "forensic.agent").write_text(
        "AGENT forensic {\n  PLAN p { STEP s { ghost() } }\n}\n")
    assert "E019" in codes(agent)


def test_a_cycle_is_a_static_error(tmp_path):
    (tmp_path / "a.agent").write_text(
        'AGENT a { PLAN p { STEP s { DELEGATE b { AGENT "b.agent" TASK "x" } } } }\n')
    (tmp_path / "b.agent").write_text(
        'AGENT b { PLAN p { STEP s { DELEGATE a { AGENT "a.agent" TASK "x" } } } }\n')
    found = codes(parse_file(str(tmp_path / "a.agent")).agents[0])
    assert "E020" in found


def test_a_child_that_understates_a_granted_tools_risk_is_flagged(tmp_path):
    agent = write(tmp_path, tag_risk="LOW")          # HIGH chez le parent
    assert "W142" in codes(agent)
    assert "W142" not in codes(write(tmp_path, tag_risk="HIGH"))


def test_nested_delegation_needs_enough_depth(tmp_path):
    (tmp_path / "inner.agent").write_text(GRANDCHILD)
    (tmp_path / "forensic.agent").write_text(MIDDLE)
    parent = tmp_path / "boss.agent"
    body = PARENT % dict(grant="read_mail", parent_policy="", options="%s")
    parent.write_text(body % "")
    assert "W143" in codes(parse_file(str(parent)).agents[0])
    parent.write_text(body % "MAX_DEPTH 2")
    assert "W143" not in codes(parse_file(str(parent)).agents[0])


def test_verify_covers_the_parent_and_every_delegate(tmp_path):
    from agentl.compose import verify_all
    agent = write(tmp_path)
    labels = [label for label, _ in verify_all(agent)]
    assert labels[0] == "boss" and len(labels) == 2
    assert "forensic.agent" in labels[1]


def test_the_cli_verifies_the_closure_and_fails_on_a_refuted_child(tmp_path, capsys):
    from agentl.cli import main
    write(tmp_path)
    assert main(["verify", str(tmp_path / "boss.agent")]) == 0
    out = capsys.readouterr().out
    assert "délégué : forensic" in out

    # un enfant dont T1 est réfuté : NEVER wipe, et un appel qui l'atteint
    (tmp_path / "forensic.agent").write_text(
        CHILD % dict(tag_risk="LOW", child_policy="NEVER wipe", observe="",
                     extra="STEP w { wipe() }"))
    assert main(["verify", str(tmp_path / "boss.agent")]) == 1


def test_the_cli_refuses_a_cyclic_closure_cleanly(tmp_path, capsys):
    (tmp_path / "a.agent").write_text(
        'AGENT a { PLAN p { STEP s { DELEGATE b { AGENT "b.agent" TASK "x" } } } }\n')
    (tmp_path / "b.agent").write_text(
        'AGENT b { PLAN p { STEP s { DELEGATE a { AGENT "a.agent" TASK "x" } } } }\n')
    from agentl.cli import main
    assert main(["verify", str(tmp_path / "a.agent")]) in (1, 2)


# ------------------------------------------ scénario d'acceptation (audit §8)
SCOUT = """
AGENT scout {
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL read_ticket { INPUT { } OUTPUT { target: String } RISK LOW }
  POLICY { DEFAULT ALLOW }
  PLAN look WHEN done == no {
    STEP a { read_ticket() }
    STEP b { SET target = result.read_ticket.target  SET done = yes }
  }
}
"""
RESPONDER = """
AGENT responder {
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL quarantine { INPUT { host: String } OUTPUT { ok: Symbol }
                    SIDE_EFFECT { host.state } RISK CRITICAL }
  POLICY { DEFAULT ALLOW }
  PLAN act WHEN done == no {
    STEP a { quarantine(host=target) }
    STEP b { SET acted = yes  SET done = yes }
  }
}
"""
SUPERVISOR = """
AGENT supervisor {
  OBSERVE { alert.open, trusted.host }
  TOOL read_ticket { INPUT { } OUTPUT { target: String } RISK LOW }
  TOOL check_host { INPUT { host: String } OUTPUT { ok: Symbol } RISK LOW }
  TOOL quarantine { INPUT { host: String } OUTPUT { ok: Symbol }
                    SIDE_EFFECT { host.state } RISK CRITICAL }
  POLICY {
    DEFAULT ALLOW
    NEVER quarantine WHEN UNTRUSTED(host) AND NOT ATTESTED(host, check_host)
    REQUIRE APPROVAL FOR quarantine
  }
  PLAN handle WHEN alert.open == yes {
    STEP find {
      DELEGATE scout { AGENT "scout.agent"  TASK "trouver la cible"
                       EXPECT { target }  GRANT { read_ticket } }
    }
    STEP respond {
      DELEGATE responder { AGENT "responder.agent"  TASK "isoler la cible"
                           INPUT { %(source)s }  EXPECT { acted }
                           GRANT { quarantine } }
    }
  }
}
"""


def team(tmp_path, source):
    (tmp_path / "scout.agent").write_text(SCOUT)
    (tmp_path / "responder.agent").write_text(RESPONDER)
    (tmp_path / "supervisor.agent").write_text(SUPERVISOR % dict(source=source))
    return parse_file(str(tmp_path / "supervisor.agent")).agents[0]


def team_host(world, injected="prod-db", answers=(True,)):
    host = Host()
    host.sensors["alert.open"] = lambda: Symbol("yes")
    host.sensors["trusted.host"] = lambda: "staging-7"
    answers = list(answers)
    host.tool("read_ticket")(
        lambda: world.calls.append("read_ticket") or {"target": injected})
    host.tool("quarantine")(
        lambda host: world.calls.append(f"quarantine:{host}") or {"ok": Symbol("yes")})
    host.approver = lambda request: world.approved.append(request.render()) \
        or (answers.pop(0) if answers else False)
    return host


def test_acceptance_injection_through_a_delegate_does_not_pick_the_target(tmp_path):
    """Le retour de `scout` est une donnée non fiable : l'enfant `responder`
    n'a aucune règle contre l'injection, le parent oui — et l'humain, même
    complaisant, n'est pas sollicité pour une cible injectée."""
    world = World()
    agent = team(tmp_path, "target")
    rt = Runtime(agent, team_host(world), MockLLM())
    rt.run(max_ticks=1)

    assert world.calls == ["read_ticket"]            # aucune quarantaine
    assert world.approved == []
    assert "plafond du parent" in rt.trace.render()
    assert rt.metrics["delegations"] == 2            # séquentiel : les deux ont tourné


def test_acceptance_the_legitimate_path_goes_through_approval(tmp_path):
    world = World()
    agent = team(tmp_path, "trusted.host")
    # la cible d'un capteur fiable : `target` côté responder vient de l'entrée
    (tmp_path / "responder.agent").write_text(
        RESPONDER.replace("host=target", "host=trusted.host"))
    rt = Runtime(agent, team_host(world), MockLLM())
    rt.run(max_ticks=1)

    assert world.calls == ["read_ticket", "quarantine:staging-7"]
    assert world.approved == ["quarantine(host=staging-7)"]
    assert rt.state.get("responder.acted") == Symbol("yes")


def test_acceptance_a_refusing_human_stops_the_critical_action(tmp_path):
    world = World()
    agent = team(tmp_path, "trusted.host")
    (tmp_path / "responder.agent").write_text(
        RESPONDER.replace("host=target", "host=trusted.host"))
    rt = Runtime(agent, team_host(world, answers=(False,)), MockLLM())
    rt.run(max_ticks=1)

    assert world.calls == ["read_ticket"]            # aucune quarantaine
    assert world.approved == ["quarantine(host=staging-7)"]
    # Ce que l'enfant affirme (`acted = yes`, posé par un SET qu'un refus
    # n'arrête pas) est une donnée non fiable pour le parent : seul le monde
    # dit si l'action a eu lieu. Le parent le vérifie avec VERIFY, pas avec
    # un EXPECT.
    _, label = rt.state.get_labeled("responder.acted")
    assert "DELEGATE" in {str(src) for src in label.sources}


def test_a_risky_grant_says_what_static_analysis_does_not_cover(tmp_path):
    assert "W144" in codes(write(tmp_path, grant="read_mail, tag_mail"))
    assert "W144" not in codes(write(tmp_path, grant="read_mail"))


# ------------------------------------------------------------ exécution asynchrone
def test_delegation_works_under_the_async_runtime(tmp_path):
    import asyncio
    from agentl.aio import AsyncHost, AsyncRuntime

    agent = write(tmp_path, tag_risk="HIGH")
    calls = []
    host = AsyncHost()

    async def reported():
        return Symbol("yes")

    async def sender():
        return Symbol("evil@example.com")

    async def read_mail():
        calls.append("read_mail")
        return {"subject": "urgent"}

    async def tag_mail(label):
        calls.append("tag_mail")
        return {"ok": Symbol("yes")}

    host.sensors["mail.reported"] = reported
    host.sensors["mail.sender"] = sender
    host.tools["read_mail"] = read_mail
    host.tools["tag_mail"] = tag_mail
    runtime = AsyncRuntime(agent, host, MockLLM())

    asyncio.run(runtime.run(max_ticks=1))

    assert calls == ["read_mail", "tag_mail"]
    assert runtime.runtime.state.get("forensic.malicious") == Symbol("yes")

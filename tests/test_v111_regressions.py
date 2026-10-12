"""Régressions de la 1.11 : ce que `verify`, `JUDGE` et le Studio affirmaient
à tort, reproduit puis refermé (vérification du 2026-10-12).

    python -m pytest tests/test_v111_regressions.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import Host, MockLLM, Runtime, UNDEFINED, parse_source, verify
from agentl.analyzer import Analyzer
from agentl.solver import contradictory, state_names

JUDGE_PROGRAM = '''
AGENT triage {
  VERSION "1.11"
  GOAL fini { MAINTAIN m.done == yes }
  TOOL reply { INPUT { text: String } SIDE_EFFECT yes RISK MODERATE }
  POLICY {
    DEFAULT DENY
    ALLOW reply WHEN judge.kind.p >= 0.90
  }
  PLAN classer WHEN m.done != yes {
    STEP juger {
      JUDGE "Trier la mention" {
        USING { m.content }
        kind: CHOICE "De quoi parle la mention ?" {
          question: "une question produit"
          other: "autre chose"
        } DEFAULT other
        score: SCORE "Mécontentement ?" [ "calme", "agacé", "furieux" ]
        neg: NOUL "Demande-t-il de suspendre ?" DEFAULT true
      }
      reply(text: "ok")
    }
  }
  LOOP MAX 1 { SELECT_PLAN EXECUTE }
}
'''


def _judge(answer):
    agent = parse_source(JUDGE_PROGRAM).agents[0]
    host = Host()
    sent = []
    host.tool("reply")(lambda text: sent.append(text) or {"sent": True})
    runtime = Runtime(agent, host, MockLLM({"Trier": answer}))
    runtime.state.set_local("m.content", "bonjour")
    runtime.run(max_ticks=1)
    return runtime.state, sent


# ======================================================== JUDGE : p suit la valeur
def test_a_retained_answer_keeps_its_probability():
    state, sent = _judge({"kind": {"value": "question", "p": 0.95}})
    assert state.get("judge.kind.p") == 0.95
    assert sent == ["ok"]


@pytest.mark.parametrize("answer", [
    {"kind": {"value": "rm -rf", "p": 0.99}},          # hors domaine
    {"neg": {"value": "peut-être", "p": 0.99}},        # illisible pour un Bool
    {"score": {"value": 7, "p": 0.99}},                # écrêté
], ids=["out-of-domain", "illegible-bool", "clamped-score"])
def test_a_rejected_answer_takes_its_probability_with_it(answer):
    state, sent = _judge(answer)
    field = next(iter(answer))
    assert state.get(f"judge.{field}.p") is UNDEFINED
    assert state.get(f"judge.{field}.confidence") is UNDEFINED
    assert sent == [], "la garde sur p ne doit pas s'ouvrir sur un repli"


# ================================================================== VFY-02
SHADOW = '''
AGENT shadow {
  VERSION "1.11"
  GOAL paid { MAINTAIN pay.done == yes }
  OBSERVE { pay.done }
  BELIEF { decision = unknown CONFIDENCE 0.10 SOURCE prior }
  TOOL pay {
    INPUT { amount: Number }
    SIDE_EFFECT { bank.transfer }
    RISK { financial = HIGH }
    EFFECT { pay.done = yes }
  }
  POLICY { DEFAULT ALLOW
           NEVER pay WHEN decision != approved }
  PLAN go WHEN pay.done != yes {
    STEP read {
      REASON {
        TASK "Lire"
        USING { pay.done }
        PRODUCE { decision: Symbol IN [approved, unknown] DEFAULT unknown,
                  approved: Symbol IN [approved, unknown] DEFAULT unknown }
      }
    }
    STEP act { pay(amount: 100) }
  }
  LOOP MAX 1 { OBSERVE SELECT_PLAN EXECUTE }
}
'''


def test_check_warns_when_a_symbol_is_also_a_state_variable():
    agent = parse_source(SHADOW).agents[0]
    codes = [d.code for d in Analyzer(agent).run()]
    assert "W137" in codes


def test_a_declared_name_is_not_read_as_a_constant_by_the_solver():
    agent = parse_source(SHADOW).agents[0]
    guard = agent.policies[-1].guard                    # decision != approved
    premise = parse_source(SHADOW.replace(
        "NEVER pay WHEN decision != approved",
        "NEVER pay WHEN decision == approved")).agents[0].policies[-1].guard
    # En constante : `decision == approved` exclut `decision != approved`.
    assert contradictory([premise], guard)
    # En variable (le runtime lit `approved` dans l'état) : rien n'est exclu.
    with state_names({"decision", "approved"}):
        assert not contradictory([premise], guard)


def test_the_idiomatic_constant_comparison_is_untouched():
    import re
    clean = re.sub(r",\s*approved: Symbol IN \[approved, unknown\] DEFAULT "
                   r"unknown", "", SHADOW)
    assert clean != SHADOW
    agent = parse_source(clean).agents[0]
    assert "W137" not in [d.code for d in Analyzer(agent).run()]


# ======================================================================== T1
DENY_PROGRAM = '''
AGENT d {
  VERSION "1.11"
  GOAL g { MAINTAIN t.done == yes }
  OBSERVE { x.ready }
  TOOL t { RISK LOW EFFECT { t.done = yes } }
  POLICY { DEFAULT ALLOW
           %s t WHEN x.ready == yes }
  PLAN p WHEN t.done != yes { STEP s { t() } }
  LOOP MAX 1 { OBSERVE SELECT_PLAN EXECUTE }
}
'''


@pytest.mark.parametrize("effect", ["NEVER", "DENY"])
def test_deny_is_an_exposure_for_t1_like_never(effect):
    agent = parse_source(DENY_PROGRAM % effect).agents[0]
    report = verify(agent)
    t1 = next(t for t in report.theorems if t.key == "T1")
    assert any(f.code == "V102" for f in t1.findings), \
        f"{effect} : T1 doit voir l'appel exposé"


# ======================================================================== T9
OBSERVE_WHEN = '''
AGENT o {
  VERSION "1.11"
  GOAL g { MAINTAIN t.done == yes }
  OBSERVE { t.done WHEN cycle.tick > 99 }
  TOOL t { RISK LOW EFFECT { t.done = yes } }
  PLAN p WHEN t.done != yes { STEP s { t() } }
  LOOP MAX 1 { OBSERVE SELECT_PLAN EXECUTE }
}
'''


def test_a_conditional_observe_does_not_make_an_effect_refutable():
    agent = parse_source(OBSERVE_WHEN).agents[0]
    t9 = next(t for t in verify(agent).theorems if t.key == "T9")
    assert not t9.holds
    assert [f.code for f in t9.findings] == ["V152"]


def test_an_unconditional_observe_still_covers_the_effect():
    agent = parse_source(OBSERVE_WHEN.replace(" WHEN cycle.tick > 99", "")).agents[0]
    t9 = next(t for t in verify(agent).theorems if t.key == "T9")
    assert t9.holds


# ====================================================================== seal
def test_keygen_creates_its_directory_and_never_overwrites(tmp_path):
    pytest.importorskip("cryptography")
    from agentl.seal import generate_ed25519
    target = tmp_path / "nouveau" / "cles"
    priv, pub = generate_ed25519(target, "k")
    assert priv.stat().st_mode & 0o777 == 0o600 and pub.is_file()
    with pytest.raises(FileExistsError):
        generate_ed25519(target, "k")


# ==================================================================== Studio
@pytest.fixture()
def studio(tmp_path):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from agentl.studio.server import create_app
    (tmp_path / "a.agent").write_text(
        'AGENT a { VERSION "1.11" GOAL g { MAINTAIN x.y == yes } }\n',
        encoding="utf-8")
    (tmp_path / "a.py").write_text("# hôte\n", encoding="utf-8")
    return TestClient(create_app(str(tmp_path / "a.agent"), root=tmp_path)), tmp_path


def test_studio_refuses_a_foreign_origin_in_http(studio):
    client, root = studio
    reply = client.post("/api/save", content='{"path": "a.py"}',
                        headers={"Origin": "http://evil.example",
                                 "Content-Type": "text/plain"})
    assert reply.status_code == 403
    assert (root / "a.py").read_text(encoding="utf-8") == "# hôte\n"


def test_studio_refuses_a_foreign_origin_on_the_websocket(studio):
    from starlette.websockets import WebSocketDisconnect
    client, _ = studio
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
                "/ws", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_json()


def test_studio_refuses_a_rebound_host(studio):
    client, _ = studio
    reply = client.get("/api/health", headers={"Host": "evil.example:8765"})
    assert reply.status_code == 403


def test_studio_requires_json_for_a_body(studio):
    client, _ = studio
    reply = client.post("/api/check", content='{"text": "x"}',
                        headers={"Content-Type": "text/plain"})
    assert reply.status_code == 415
    ok = client.post("/api/check", json={"text": "AGENT a { }"})
    assert ok.status_code == 200


def test_studio_only_saves_agent_programs(studio):
    client, root = studio
    reply = client.post("/api/save", json={"path": "a.py"})
    assert reply.json().get("error") or reply.status_code >= 400
    assert (root / "a.py").read_text(encoding="utf-8") == "# hôte\n"
    assert client.post("/api/save", json={}).status_code == 200


def test_studio_accepts_its_own_origin(studio):
    client, _ = studio
    reply = client.get("/api/health", headers={"Origin": "http://testserver"})
    assert reply.status_code == 200

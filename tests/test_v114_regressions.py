"""Régressions de la 1.14 : la dette de mutation ne peut que baisser.

    python -m pytest tests/test_v114_regressions.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import copy                                              # noqa: E402

import check_mutation                                    # noqa: E402
from agentl.mutate import mutate_file                    # noqa: E402

# La mesure réelle des 20 exemples (≈ 35 s, 160 s sous couverture) est faite par
# l'étape CI `tools/check_mutation.py` ; ici, la logique de comparaison.
_STATE = {"judged": True, "unjudged": [], "mutants": 2,
          "survivors": ["A · NEVER : NEVER x"]}


def _run(monkeypatch, tmp_path, baseline, now, capsys):
    snapshot = tmp_path / "baseline.json"
    snapshot.write_text(json.dumps({"examples": baseline}), encoding="utf-8")
    monkeypatch.setattr(check_mutation, "BASELINE", snapshot)
    monkeypatch.setattr(check_mutation, "measure", lambda: now)
    code = check_mutation.main([])
    return code, capsys.readouterr().out


def test_an_unchanged_state_passes(monkeypatch, tmp_path, capsys):
    code, _ = _run(monkeypatch, tmp_path, {"a.agent": _STATE},
                   {"a.agent": copy.deepcopy(_STATE)}, capsys)
    assert code == 0


def test_a_new_undefended_rule_is_a_regression(monkeypatch, tmp_path, capsys):
    now = copy.deepcopy(_STATE)
    now["survivors"].append("A · ALLOW : ALLOW y")
    code, out = _run(monkeypatch, tmp_path, {"a.agent": _STATE},
                     {"a.agent": now}, capsys)
    assert code == 1 and "règle non défendue nouvelle" in out


def test_a_defended_rule_asks_to_tighten_the_baseline(monkeypatch, tmp_path,
                                                      capsys):
    now = copy.deepcopy(_STATE)
    now["survivors"] = []
    code, out = _run(monkeypatch, tmp_path, {"a.agent": _STATE},
                     {"a.agent": now}, capsys)
    assert code == 0 and "resserrer la référence" in out


def test_a_judged_example_that_becomes_unjudgeable_fails(monkeypatch, tmp_path,
                                                         capsys):
    now = {"judged": False, "unjudged": ["scénarios déjà rouges"],
           "mutants": 0, "survivors": []}
    code, out = _run(monkeypatch, tmp_path, {"a.agent": _STATE},
                     {"a.agent": now}, capsys)
    assert code == 1 and "n'est plus jugeable" in out


def test_an_example_absent_from_the_baseline_fails(monkeypatch, tmp_path,
                                                   capsys):
    code, out = _run(monkeypatch, tmp_path, {},
                     {"a.agent": copy.deepcopy(_STATE)}, capsys)
    assert code == 1 and "absent de la référence" in out


def test_the_shipped_baseline_lists_every_example():
    base = json.loads(check_mutation.BASELINE.read_text(encoding="utf-8"))
    listed = set(base["examples"])
    shipped = {p.name for p in (ROOT / "examples").glob("*.agent")
               if p.stem not in check_mutation.NEGATIVE}
    assert shipped == listed


def test_soc_analyst_defends_all_its_policy_rules():
    report = mutate_file(str(ROOT / "examples" / "soc_analyst.agent"))
    assert report.mutants and not report.survivors


# ============================================ provenance statique (W141, V154)
from agentl import parse_source, verify                  # noqa: E402
from agentl.analyzer import Analyzer                     # noqa: E402
from agentl import provflow                              # noqa: E402

_FLOW = '''
AGENT f {
  GOAL g { MAINTAIN done == yes }
  OBSERVE { inbox.ready }
  BELIEF { done = no CONFIDENCE 1.0 SOURCE prior }
  TOOL read_ticket { OUTPUT { requester: String } RISK LOW }
  TOOL close_account {
    INPUT { account: String%s }
    OUTPUT { closed: Symbol }
    SIDE_EFFECT { crm.accounts }
    RISK { operational = CRITICAL }
    EFFECT { done = yes }
  }
  POLICY { DEFAULT ALLOW%s }
  PLAN go WHEN done != yes {
    STEP a { read_ticket() }
    STEP b { SET who = requester
             %s }
  }
  LOOP MAX 2 { SELECT_PLAN EXECUTE }
}
'''


def _flow(attest="", policy="", call="close_account(account = who)"):
    return parse_source(_FLOW % (attest, policy, call)).agents[0]


def _w141(agent):
    return [d for d in Analyzer(agent).run() if d.code == "W141"]


def test_a_tool_output_copied_by_set_reaches_a_risky_argument():
    found = provflow.flows(_flow())
    assert [(f.tool, f.param, f.sources) for f in found] == \
        [("close_account", "account", ("TOOL",))]
    assert len(_w141(_flow())) == 1


def test_a_literal_argument_is_not_a_flow():
    assert not _w141(_flow(call='close_account(account = "acct-9")'))


def test_an_attested_argument_is_covered():
    agent = _flow(attest="\n    proof: String ATTESTS account",
                  call="close_account(account = who, proof = who)")
    assert not _w141(agent)


def test_a_provenance_guard_in_the_policy_covers_the_tool():
    agent = _flow(policy="\n    NEVER close_account WHEN UNTRUSTED(action)")
    assert not _w141(agent)


def test_w141_does_not_repeat_what_w119_or_w140_already_say():
    source = _FLOW % ("", "", "close_account(account = payload.who)")
    source = source.replace("  PLAN go WHEN done != yes {",
                            "  EVENT ticket.created { close_account(account = payload.who) }\n"
                            "  PLAN go WHEN done != yes {")
    codes = [d.code for d in Analyzer(parse_source(source).agents[0]).run()]
    assert "W140" in codes and codes.count("W141") == 0


def test_t6_says_how_many_untrusted_flows_it_did_not_judge():
    t6 = next(t for t in verify(_flow()).theorems if t.key == "T6")
    assert t6.holds is True                       # le verdict ne change pas
    assert any(f.code == "V154" for f in t6.findings)
    clean = next(t for t in verify(_flow(call='close_account(account = "a")')
                                   ).theorems if t.key == "T6")
    assert not any(f.code == "V154" for f in clean.findings)

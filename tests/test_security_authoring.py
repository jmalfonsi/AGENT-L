"""Régressions des garanties de sûreté d’auteur AGENT-L v1.5."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentl import Analyzer, MockLLM, Runtime, parse_source, verify
from agentl.boundary import check_pair
from agentl.core import UNDEFINED, Symbol
from agentl.host import Host
from agentl.policy import _ActionScope, action_from_tool
from agentl.state import State

LINUX = Path(__file__).resolve().parents[1] / "examples" / "linux_defender.agent"

def _codes(source: str) -> set[str]:
    return {d.code for d in Analyzer(parse_source(source).agents[0]).run()}

def _linux_mutation(old: str, new: str) -> set[str]:
    source = LINUX.read_text(encoding="utf-8")
    assert old in source
    return _codes(source.replace(old, new, 1))

def test_default_and_attests_are_preserved_by_parser_and_mock_llm():
    agent = parse_source("""
    AGENT guarded {
      TOOL act { INPUT { target: String, proof: String ATTESTS target } RISK LOW }
      PLAN p { STEP s { REASON {
        TASK "classify" PRODUCE { verdict IN [safe, other] DEFAULT other }
      } } }
    }
    """).agents[0]
    reason = agent.plans[0].steps[0].body[0]
    assert agent.tool("act").attestations == {"proof": "target"}
    assert reason.defaults["verdict"].dotted == "other"
    assert str(Runtime(agent, llm=MockLLM())._run_reason(reason)["verdict"]) == "other"

def test_w119_llm_target_without_attestation():
    source = LINUX.read_text(encoding="utf-8")
    source = source.replace("evidence_token: String ATTESTS pid", "evidence_token: String", 1)
    source = source.replace("pid = alert.target_pid", "pid = classification", 1)
    assert "W119" in _codes(source)
    # Par clé et non par position : T9 s'est ajouté en queue de liste, et un
    # index négatif faisait porter l'assertion sur un autre théorème.
    theorems = {t.key: t for t in verify(parse_source(source).agents[0]).theorems}
    assert theorems["T6"].holds is False

def test_w120_high_risk_requires_approval():
    assert "W120" in _linux_mutation("        REQUIRE APPROVAL FOR rollback_action\n", "")

def test_w121_verify_must_reobserve_effect():
    assert "W121" in _linux_mutation("        remediation.unresolved_count\n", "")

def test_w122_foreach_target_requires_correlated_attestation():
    assert "W122" in _linux_mutation("evidence_token: String ATTESTS pid", "evidence_token: String")

def test_w123_termination_guard_checks_unresolved_work():
    assert "W123" in _linux_mutation(
        "ALLOW close_cycle IF remediation.unresolved_count == 0",
        "ALLOW close_cycle IF 1 == 1")

def test_w123_does_not_confuse_rollback_done_with_loop_termination():
    assert "W123" not in _codes(LINUX.read_text(encoding="utf-8"))

def test_w124_risky_effect_requires_safety_scenario():
    assert "W124" in _linux_mutation(", rollback.done != yes", "")

def test_w125_raw_text_requires_injection_guard():
    source = LINUX.read_text(encoding="utf-8")
    source = source.replace(
        "IF alert.untrusted_instruction == no AND injection_detected == no\n                   AND classification == alert.category AND alert.action_kind == kill_malicious_process",
        "IF classification == alert.category AND alert.action_kind == kill_malicious_process", 1)
    assert "W125" in _codes(source)

def _pair(tmp_path: Path, host: str, agent: str | None = None):
    agent_path = tmp_path / "guard.agent"
    agent_path.write_text(agent or """
    AGENT guard {
      OBSERVE { fact }
      TOOL danger { INPUT { target: String } RISK LOW }
    }
    """, encoding="utf-8")
    agent_path.with_suffix(".py").write_text(host, encoding="utf-8")
    return check_pair(agent_path)

def test_b008_b010_b012_destructive_action_contract(tmp_path):
    report = _pair(tmp_path, """
from agentl import Host
import os
host = Host()
host.sensors["fact"] = lambda: 1
def danger(target):
    os.kill(int(target), 9)
host.tools["danger"] = danger
""")
    assert {"B008", "B010", "B012"} <= {f.code for f in report.findings}

def test_b009_rejects_shell_true(tmp_path):
    report = _pair(tmp_path, """
from agentl import Host
import subprocess
host = Host()
host.sensors["fact"] = lambda: 1
def danger(target):
    subprocess.run(target, shell=True)
host.tools["danger"] = danger
""")
    assert "B009" in {f.code for f in report.findings}

def test_b011_requires_log_cursor_or_dedup(tmp_path):
    report = _pair(tmp_path, """
from agentl import Host
from pathlib import Path
host = Host()
host.sensors["fact"] = lambda: Path("/var/log/auth.log").read_text()
host.tools["danger"] = lambda target: {}
""")
    assert "B011" in {f.code for f in report.findings}

def test_b013_requires_external_llm_opt_in_for_raw_data(tmp_path):
    report = _pair(tmp_path, """
from agentl import Host
host = Host()
from openai import OpenAI
client = OpenAI()
raw_log_message = "secret"
client.responses.create(input=raw_log_message)
host.sensors["fact"] = lambda: raw_log_message
host.tools["danger"] = lambda target: {}
""")
    assert "B013" in {f.code for f in report.findings}

def test_b014_requires_exact_agent_host_registries(tmp_path):
    report = _pair(tmp_path, """
from agentl import Host
host = Host()
host.sensors["extra"] = lambda: 1
host.tools["other"] = lambda target: {}
""")
    findings = [f for f in report.findings if f.code == "B014"]
    assert len(findings) == 4

# B014 est le seul contrôle du module qui puisse accuser à tort : il conclut
# d'un registre lu statiquement. Les quatre cas suivants sont les formes
# d'enregistrement qu'il ne voyait pas, et sur lesquelles il annonçait
# manquant ce que l'hôte fournit — l'invention que le module s'interdit.

def test_b014_reads_registration_by_function_name(tmp_path):
    """`for fn in (a, b): h.tools[fn.__name__] = fn` — aucune clé littérale."""
    report = _pair(tmp_path, """
from agentl import Host
host = Host()
host.sensors["fact"] = lambda: 1
def danger(target):
    return {}
for fn in (danger,):
    host.tools[fn.__name__] = fn
""")
    assert not [f for f in report.findings if f.code == "B014"]

def test_b014_reports_incomplete_when_the_builder_is_external(tmp_path):
    """Un hôte qui délègue son montage ne prouve pas un registre vide."""
    report = _pair(tmp_path, """
import other_host
def build():
    return other_host.build()
""")
    findings = [f for f in report.findings if f.code == "B014"]
    assert findings and all("INCOMPLETE" in f.message for f in findings)
    assert not report.complete and not report.ok()

def test_b014_reports_incomplete_on_a_computed_registry_key(tmp_path):
    """Clé calculée : l'inventaire est partiel, donc muet sur les manquants."""
    report = _pair(tmp_path, """
from agentl import Host
host = Host()
host.sensors["fact"] = lambda: 1
for name in _discover():
    host.tools[name] = _load(name)
""")
    findings = [f for f in report.findings if f.code == "B014"]
    assert len(findings) == 1 and "INCOMPLETE" in findings[0].message
    assert not report.complete and not report.ok()

def test_b014_accepts_a_delegate_target_implemented_as_a_subagent(tmp_path):
    """W127 exige un TOOL pour un DELEGATE ; il vit dans `subagents`."""
    report = _pair(tmp_path, """
from agentl import Host
host = Host()
host.sensors["fact"] = lambda: 1
host.tools["danger"] = lambda target: {}
host.subagents["helper"] = lambda inputs: {}
""", agent="""
    AGENT guard {
      OBSERVE { fact }
      TOOL danger { INPUT { target: String } RISK LOW }
      TOOL helper { RISK LOW }
      PLAN p { STEP s { DELEGATE helper { TASK "trier" } } }
    }
    """)
    assert not [f for f in report.findings if f.code == "B014"]

def test_b014_still_reports_a_tool_no_registry_implements(tmp_path):
    """Le sens utile reste actif : un contrat rompu doit rester visible."""
    report = _pair(tmp_path, """
from agentl import Host
host = Host()
host.sensors["fact"] = lambda: 1
host.subagents["danger"] = lambda inputs: {}
""")
    messages = [f.message for f in report.findings if f.code == "B014"]
    assert any("outils hôte manquants : danger" in m for m in messages)


# --------------------------------------------------------------------------
# T1 — masquage d'argument (audit externe, 2026-08-03)
#
# `SET target = safe` puis `delete(target=protected)` : jusqu'en v1.8 la garde
# `NEVER delete WHEN target == protected` lisait la locale et laissait passer
# l'appel, tandis que `verify` annonçait T1 « démontré ». Une garde de
# politique juge l'appel en cours : dans cette portée l'argument prime.
# --------------------------------------------------------------------------

_MASKING = """
AGENT masq {
  BELIEF { app.ok = no CONFIDENCE 1.00 SOURCE internal }
  TOOL delete { INPUT { target: Symbol } RISK HIGH }
  POLICY {
    DEFAULT ALLOW
    NEVER delete WHEN target == protected
  }
  PLAN p WHEN app.ok == no {
    SET target = safe
    delete(target=protected)
  }
}
"""

class _Recorder(Host):
    def __init__(self):
        super().__init__()
        self.calls = []
    def read(self, path):
        return None
    def invoke(self, name, args):
        self.calls.append((name, dict(args)))
        return None

def _run(source: str) -> _Recorder:
    host = _Recorder()
    Runtime(parse_source(source).agents[0], host).tick()
    return host

def test_a_local_does_not_mask_the_argument_a_never_guards():
    """Le défaut central : l'appel interdit ne doit pas aboutir."""
    assert _run(_MASKING).calls == []

def test_the_masked_local_is_named_in_the_trace():
    """Deux noms qui se rencontrent en silence sont ce qui a produit le défaut."""
    host = _Recorder()
    runtime = Runtime(parse_source(_MASKING).agents[0], host)
    runtime.tick()
    said = [e.detail for e in runtime.trace.of_kind("POLICY")]
    assert any("`target` masque la locale safe" in d for d in said)

def test_the_scope_still_resolves_a_local_no_argument_names():
    """L'argument prime sur son homonyme, pas sur le reste de l'état."""
    host = _run("""
    AGENT keep {
      BELIEF { app.ok = no CONFIDENCE 1.00 SOURCE internal }
      TOOL delete { INPUT { target: Symbol } RISK HIGH }
      POLICY {
        DEFAULT ALLOW
        NEVER delete WHEN mode == audit
      }
      PLAN p WHEN app.ok == no {
        SET mode = audit
        delete(target=whatever)
      }
    }
    """)
    assert host.calls == []

def test_an_argument_without_a_homonym_reports_no_masking():
    """Pas de bruit dans le cas courant : rien n'a été recouvert."""
    host = _Recorder()
    runtime = Runtime(parse_source("""
    AGENT plain {
      BELIEF { app.ok = no CONFIDENCE 1.00 SOURCE internal }
      TOOL delete { INPUT { target: Symbol } RISK LOW }
      POLICY { DEFAULT ALLOW  NEVER delete WHEN target == protected }
      PLAN p WHEN app.ok == no { delete(target=safe) }
    }
    """).agents[0], host)
    runtime.tick()
    assert host.calls == [("delete", {"target": Symbol("safe")})]
    assert runtime.trace.of_kind("POLICY") == []

def test_action_args_stays_available_alongside_the_short_name():
    """Le chemin explicite de la SPEC §7.1 n'est pas remplacé, il coexiste."""
    request = action_from_tool(None, "delete", {"target": Symbol("protected")}, "plan")
    state = State()
    state.set_local("target", Symbol("safe"))
    scope = _ActionScope(state, request)
    assert str(scope.get("action.args.target")) == "protected"
    assert str(scope.get("target")) == "protected"
    assert str(scope.shadowed_args["target"]) == "safe"


# --------------------------------------------------------------------------
# NEVER SEND — le parent d'un secret (audit externe, 2026-08-03)
#
# `NEVER SEND credentials.token` ne disait rien de `credentials` : envoyer le
# composite parent livrait le secret, sans redaction ni diagnostic. La
# protection se contournait en désignant le nœud du dessus — exactement ce que
# fait `USING { credentials }`.
# --------------------------------------------------------------------------

_SECRET = {"token": "SECRET", "user": "alice"}

def _focus(never_send: str, value, using: str = "credentials"):
    agent = parse_source(f"""
    AGENT leak {{
      OBSERVE {{ credentials }}
      POLICY {{ DEFAULT ALLOW  NEVER SEND {never_send} }}
      PLAN p {{ REASON {{ TASK "t" USING {{ {using} }} PRODUCE {{ v: Symbol }} }} }}
    }}""").agents[0]
    runtime = Runtime(agent, Host())
    runtime.state.set_world("credentials", value)
    return runtime, runtime.llm_context(using=[using])

def test_sending_the_parent_no_longer_leaks_the_forbidden_leaf():
    """Le défaut : le secret partait dans le composite du dessus."""
    _, context = _focus("credentials.token", _SECRET)
    assert "SECRET" not in str(context)

def test_the_siblings_of_a_secret_still_reach_the_model():
    """Retenir tout `credentials` amputerait ce que nul n'a protégé."""
    _, context = _focus("credentials.token", _SECRET)
    assert "alice" in context["focus"]["credentials"]
    assert "⟦retenu⟧" in context["focus"]["credentials"]

def test_a_nested_leaf_is_reached_through_the_levels():
    _, context = _focus("credentials.a.b", {"a": {"b": "SECRET", "c": "ok"}})
    assert "SECRET" not in str(context)
    assert "ok" in context["focus"]["credentials"]

def test_an_opaque_composite_is_withheld_whole():
    """On ne sait pas où est la feuille : le sens fermé retient tout."""
    class Opaque:
        def __repr__(self):
            return "<objet portant SECRET>"
    _, context = _focus("credentials.token", Opaque())
    assert context["focus"]["credentials"] == Runtime.REDACTED

def test_the_partial_redaction_is_traced_and_counted():
    runtime, _ = _focus("credentials.token", _SECRET)
    assert runtime.metrics["redactions"] == 1
    assert any("credentials.token retenu" in e.text
               for e in runtime.trace.of_kind("BLOCKED"))

def test_select_plan_context_protects_the_parent_too():
    """`USING` n'est pas le seul point de sortie : le contexte complet part
    à `select_plan` avec l'intégralité des croyances."""
    agent = parse_source("""
    AGENT sel {
      BELIEF { credentials = unset CONFIDENCE 1.00 SOURCE internal }
      POLICY { DEFAULT ALLOW  NEVER SEND credentials.token }
      PLAN p { SET app.ok = yes }
    }""").agents[0]
    runtime = Runtime(agent, Host())
    runtime.state.beliefs["credentials"].value = dict(_SECRET)
    assert "SECRET" not in str(runtime.llm_context())

def test_w130_reports_a_using_that_names_the_parent_of_a_secret():
    codes = _codes("""
    AGENT leak {
      OBSERVE { credentials }
      POLICY { DEFAULT ALLOW  NEVER SEND credentials.token }
      PLAN p { REASON { TASK "t" USING { credentials } PRODUCE { v: Symbol } } }
    }""")
    assert "W130" in codes

def test_w130_stays_silent_when_no_redaction_meets_the_using():
    codes = _codes("""
    AGENT clean {
      OBSERVE { credentials }
      POLICY { DEFAULT ALLOW  NEVER SEND autre.chose }
      PLAN p { REASON { TASK "t" USING { credentials } PRODUCE { v: Symbol } } }
    }""")
    assert "W130" not in codes


# --------------------------------------------------------------------------
# UNDEFINED — deux absences ne se comparent pas (audit externe, 2026-08-03)
#
# `_eq` repliait sur `a is b`, vrai sur le singleton : `WHEN missing.a ==
# missing.b` déclenchait le plan, et sous `DEFAULT ALLOW` cela conduisait à
# une action réelle. La correction s'arrête là où l'ignorance est bilatérale :
# une absence face à une valeur connue reste « différente », faute de quoi
# `request.sent != yes` — l'idiome d'amorçage de tous les programmes livrés —
# deviendrait définitivement faux.
# --------------------------------------------------------------------------

def _guard_fires(guard: str) -> bool:
    agent = parse_source(f"""
    AGENT u {{
      POLICY {{ DEFAULT ALLOW }}
      PLAN p WHEN {guard} {{ SET fired = yes }}
    }}""").agents[0]
    runtime = Runtime(agent, Host())
    runtime.tick()
    return any(e.text.startswith("exécution de p") for e in runtime.trace.events)

def test_two_absent_paths_are_not_equal():
    assert not _guard_fires("missing.a == missing.b")

def test_two_absent_paths_are_not_unequal_either():
    """Le piège symétrique : `!=` par simple complément rouvrirait le défaut."""
    assert not _guard_fires("missing.a != missing.b")

def test_an_absent_path_still_differs_from_a_known_value():
    """`request.sent != yes` doit rester vrai avant le premier envoi, sans quoi
    aucun plan d'amorçage ne se déclenche plus jamais."""
    assert _guard_fires("missing.a != yes")
    assert not _guard_fires("missing.a == yes")

def test_a_verify_does_not_succeed_by_ignorance():
    """Régime strict de VERIFY : l'absence de preuve n'est pas une preuve."""
    agent = parse_source("""
    AGENT v {
      BELIEF { app.ok = no CONFIDENCE 1.00 SOURCE internal }
      POLICY { DEFAULT ALLOW }
      PLAN p WHEN app.ok == no { VERIFY { incident.resolved != open } }
    }""").agents[0]
    runtime = Runtime(agent, Host())
    runtime.tick()
    assert runtime.metrics["verify_fail"] == 1
    assert runtime.metrics["verify_pass"] == 0

def test_policy_guards_keep_their_trivalent_reading():
    """La sûreté des interdits ne dépend pas de `_eq` : un NEVER dont la garde
    est indéterminée s'applique, il ne devient pas faux."""
    calls = []
    class _H(Host):
        def read(self, path):
            return None
        def invoke(self, name, args):
            calls.append(name)
            return None
    agent = parse_source("""
    AGENT p {
      BELIEF { app.ok = no CONFIDENCE 1.00 SOURCE internal }
      TOOL wipe { INPUT { t: Symbol } RISK HIGH }
      POLICY { DEFAULT ALLOW  NEVER wipe WHEN missing.a == missing.b }
      PLAN p WHEN app.ok == no { wipe(t=x) }
    }""").agents[0]
    Runtime(agent, _H()).tick()
    assert calls == []


# --------------------------------------------------------------------------
# Coercition booléenne (audit externe, 2026-08-03)
#
# `bool("false")` valait `True`, comme `bool("no")` et `bool("off")`. Un
# modèle qui répond « false » en toutes lettres produisait l'inverse de sa
# réponse, en silence.
# --------------------------------------------------------------------------

def _bool(raw, typ: str = "Bool"):
    from agentl.llm import _coerce
    return _coerce({"x": raw}, {"x": typ})["x"]

def test_a_spelled_out_negation_is_false():
    for raw in ("false", "no", "off", "0", "non", "faux", "FALSE", " false "):
        assert _bool(raw) is False, raw

def test_a_spelled_out_affirmation_is_true():
    for raw in ("true", "yes", "on", "1", "oui", "vrai", "TRUE", " true "):
        assert _bool(raw) is True, raw

def test_real_booleans_and_numbers_are_untouched():
    assert _bool(True) is True and _bool(False) is False
    assert _bool(1) is True and _bool(0) is False

def test_an_unreadable_answer_falls_back_on_the_declared_default():
    """Hors vocabulaire n'est pas « négatif » : c'est une absence de réponse,
    et `DEFAULT` est ce que le programme a prévu pour ce cas."""
    assert _bool("peut-être") is UNDEFINED
    assert _bool("peut-être", "Bool DEFAULT true") is True


# --------------------------------------------------------------------------
# Gardes — évaluation sûre partout (audit externe, 2026-08-03)
#
# `_safe_test` ne couvrait que trois sites : une garde arithmétique sur un
# capteur rendant une chaîne faisait remonter une EvalError hors du tick.
# --------------------------------------------------------------------------

class _StringSensor(Host):
    def read(self, path):
        return "chaud"

_ARITHMETIC_GUARD = """
AGENT g {{
  OBSERVE {{ {observe} }}
  BELIEF {{ app.ok = no CONFIDENCE 1.00 SOURCE internal }}
  POLICY {{ DEFAULT ALLOW }}
  {body}
}}
"""

#: La même garde arithmétique — impossible sur un capteur rendant une chaîne —
#: posée à chacun des sites qui appelaient `Evaluator.test` en direct.
_BAD = "sensor.value + 1 > 2"
_GUARD_SITES = {
    "plan":    ("sensor.value", f"PLAN p WHEN {_BAD} {{ SET done = yes }}"),
    "if":      ("sensor.value",
                f"PLAN p WHEN app.ok == no {{ IF {_BAD} THEN {{ SET d = y }} }}"),
    "verify":  ("sensor.value",
                f"PLAN p WHEN app.ok == no {{ VERIFY {{ {_BAD} }} }}"),
    "observe": (f"sensor.value WHEN {_BAD}",
                "PLAN p WHEN app.ok == no { SET d = y }"),
    "goal":    ("sensor.value", f"GOAL but {{ MAINTAIN {_BAD} }}"),
    "decide":  ("sensor.value",
                "PLAN p WHEN app.ok == no { SET d = y }\n"
                f"  DECIDE {{ RULES {{ IF {_BAD} THEN p }} }}"),
}

def test_no_guard_site_lets_an_evaluation_error_escape_the_tick():
    for site, (observe, body) in _GUARD_SITES.items():
        source = _ARITHMETIC_GUARD.format(observe=observe, body=body)
        runtime = Runtime(parse_source(source).agents[0], _StringSensor())
        runtime.tick()          # ne doit pas lever
        runtime.tick()

def test_an_unevaluable_guard_fails_closed_and_says_so():
    observe, body = _GUARD_SITES["plan"]
    runtime = Runtime(
        parse_source(_ARITHMETIC_GUARD.format(observe=observe, body=body)).agents[0],
        _StringSensor())
    runtime.tick()
    assert not any(e.text.startswith("exécution de p") for e in runtime.trace.events)
    assert any("repli fermé" in e.detail for e in runtime.trace.of_kind("ERROR"))

"""Adversarial corpus for AUDIT.md (BND-A01 through BND-A07).

The host snippets are parsed, never imported or executed.
"""
from pathlib import Path

import pytest

from agentl.boundary import check_host, check_pair, render


def host_report(tmp_path, source, **kwargs):
    path = tmp_path / "host.py"
    path.write_text(source, encoding="utf-8")
    return check_host(path, **kwargs)


def codes(report):
    return {f.code for f in report.findings if not f.waived}


@pytest.mark.parametrize("predicate", [
    "x.blocked", "blocked", "eligible(x)", "not x.blocked",
    "not eligible(x)", "x is not None and x.blocked",
])
def test_truthiness_is_not_an_absence_proof(tmp_path, predicate):
    assert "B002" in codes(host_report(
        tmp_path, f"result = [x for x in xs if {predicate}]"))


@pytest.mark.parametrize("predicate", [
    "x is not None", "None is not x", "not (x is None)",
    "x is not None and x.value is not None",
])
def test_explicit_absence_guards_are_accepted(tmp_path, predicate):
    assert "B002" not in codes(host_report(
        tmp_path, f"result = [x for x in xs if {predicate}]"))


@pytest.mark.parametrize(("source", "code"), [
    ('if "Urgent" == ticket.priority: pass', "B001"),
    ('if 90 <= score: pass', "B005"),
    ('if risk > 0.95: pass', "B005"),
    ('if severity > 5: pass', "B005"),
    ('if score > -0.5: pass', "B005"),
    ('THRESHOLD = 90\nif score > THRESHOLD: pass', "B005"),
    ('THRESHOLD = 0.95\nLIMIT = THRESHOLD\nif score > LIMIT: pass', "B005"),
    ('BLOCKED = {"CN", "RU"}\nif country in BLOCKED: pass', "B001"),
    ('if country in ("CN", "RU"): pass', "B001"),
    ('if 0 < score < 1: pass', "B005"),
])
def test_comparisons_cannot_hide_constants(tmp_path, source, code):
    assert code in codes(host_report(tmp_path, source))


@pytest.mark.parametrize("source", [
    'if role == "Admin": print("BOUNDARY-OK: harmless")',
    '"""BOUNDARY-OK: harmless"""\nif role == "Admin": pass',
    '# BOUNDARY-OK\nif role == "Admin": pass',
])
def test_only_justified_python_comments_can_waive(tmp_path, source):
    assert "B001" in codes(host_report(tmp_path, source))


def test_inline_waiver_does_not_spill_into_next_statement(tmp_path):
    report = host_report(tmp_path,
        'if role == "Admin": pass  # BOUNDARY-OK: protocol tag\n'
        'if role == "Root": pass\n')
    assert any(f.line == 1 and f.waived for f in report.findings)
    assert any(f.line == 2 and not f.waived for f in report.findings)


def test_multiline_inline_waiver_stays_on_its_own_statement(tmp_path):
    report = host_report(tmp_path,
        'result = [x for x in xs\n'
        '          if x.blocked]  # BOUNDARY-OK: fixture selection\n'
        'if role == "Root": pass\n')
    assert any(f.code == "B002" and f.waived for f in report.findings)
    assert any(f.code == "B001" and not f.waived for f in report.findings)


def test_transitive_local_imports_and_cycles_are_analysed_without_execution(tmp_path):
    (tmp_path / "rules.py").write_text(
        'from helpers import eligible\nraise RuntimeError("do not execute")\n')
    helper = tmp_path / "helpers.py"
    helper.write_text('import rules\ndef eligible(x):\n    return x.risk > 0.2\n')
    report = host_report(tmp_path, 'from rules import eligible\n'
        'def observe(x):\n    return {"eligible": eligible(x)}\n')
    findings = [f for f in report.findings if f.code == "B005"]
    assert len(findings) == 1
    assert findings[0].path == helper
    assert "helpers.py" in render(report)
    assert set(report.analyzed_paths) == {tmp_path / "host.py", tmp_path / "rules.py", helper}


def test_relative_imports_and_package_initializers(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('from .rules import eligible\n')
    (package / "rules.py").write_text('def eligible(x):\n    return x.score > 2\n')
    assert "B005" in codes(host_report(tmp_path, 'from pkg import eligible\n'))


def test_imported_constant_is_resolved(tmp_path):
    (tmp_path / "settings.py").write_text('LIMIT = 0.95\n')
    assert "B005" in codes(host_report(tmp_path,
        'from settings import LIMIT as threshold\nif risk > threshold: pass\n'))


def test_unparseable_local_helper_is_a_visible_failure(tmp_path):
    (tmp_path / "rules.py").write_text('def broken(:\n')
    report = host_report(tmp_path, 'import rules\n')
    assert not report.ok()
    assert not report.complete


def test_dynamic_import_is_not_a_clean_report(tmp_path):
    report = host_report(tmp_path, 'import importlib\nrules = importlib.import_module(name)\n')
    assert not report.ok()
    assert not report.complete
    assert "B016" in codes(report)


def test_external_dependencies_are_reported_and_optionally_rejected(tmp_path):
    report = host_report(tmp_path, 'import vendor_rules\n')
    assert "vendor_rules" in report.external_imports
    assert "vendor_rules" in render(report)
    strict = host_report(tmp_path, 'import vendor_rules\n', external_policy="error")
    assert not strict.ok()


def test_import_cannot_follow_a_symlink_outside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text('if score > 0.5: pass\n')
    (project / "rules.py").symlink_to(outside)
    report = host_report(project, 'import rules\n', project_root=project)
    assert not report.complete
    assert outside not in report.analyzed_paths


@pytest.mark.parametrize("operation", [
    'import os as operating_system\noperating_system.kill(pid, 9)',
    'from os import kill\nkill(pid, 9)',
    'from shutil import rmtree as erase\nerase(target)',
    'import os\nterminate = os.kill\nterminate(pid, 9)',
    'path.unlink()',
    'from pathlib import Path\nPath(target).unlink()',
    'import shutil\nshutil.rmtree(target)',
    'import os\nos.replace(source, target)',
])
def test_destructive_aliases_and_methods_are_checked(tmp_path, operation):
    assert {"B008", "B010", "B012"} <= codes(host_report(tmp_path, operation))


@pytest.mark.parametrize("decoy", [
    '# dry_run validate rollback\n',
    'note = "dry_run validate rollback"\n',
    'def validate_email(email): return email\n',
    'def unrelated():\n    validate(target)\n    record_rollback(target)\n',
])
def test_unrelated_words_and_calls_do_not_protect_actions(tmp_path, decoy):
    report = host_report(tmp_path, decoy + 'import os\nos.kill(pid, 9)\n')
    assert {"B008", "B010", "B012"} <= codes(report)


def test_each_destructive_site_needs_its_own_protection(tmp_path):
    report = host_report(tmp_path, '''import os
def guarded(target, dry_run=True):
    if dry_run:
        return
    validate_target(target)
    record_rollback(target)
    os.remove(target)
def unguarded(target):
    os.remove(target)
''')
    assert not [f for f in report.findings if f.code in {"B008", "B010", "B012"} and f.line == 7]
    assert {"B008", "B010", "B012"} <= {f.code for f in report.findings if f.line == 9}


@pytest.mark.parametrize("protection", [
    'validate_target(other)\nrecord_rollback(other)',
    'if flag:\n    validate_target(target)\n    record_rollback(target)',
    'def nested():\n    validate_target(target)\n    record_rollback(target)',
    'validate_target(target)\nrecord_rollback(target)\ntarget = other',
])
def test_protection_must_dominate_and_refer_to_current_target(tmp_path, protection):
    body = '\n'.join('    ' + line for line in protection.splitlines())
    report = host_report(tmp_path, 'import os\ndef act(target, other, flag):\n'
        + body + '\n    os.remove(target)\n')
    assert {"B010", "B012"} <= codes(report)


@pytest.mark.parametrize("signature", ['dry_run=False', 'dry_run', 'real_actions_enabled=True'])
def test_unsafe_or_unknown_default_does_not_count_as_opt_in(tmp_path, signature):
    guard = 'not real_actions_enabled' if 'real_actions_enabled' in signature else 'dry_run'
    report = host_report(tmp_path, f'import os\ndef act(target, {signature}):\n'
        f'    if {guard}: return\n    os.remove(target)\n')
    assert "B008" in codes(report)


@pytest.mark.parametrize("call", ['sp.run(command, shell=True)', 'run(command, shell=True)',
    'sp.Popen(command, shell=True)', 'sp.check_output(command, shell=True)'])
def test_shell_aliases_are_checked(tmp_path, call):
    assert "B009" in codes(host_report(tmp_path,
        'import subprocess as sp\nfrom subprocess import run\n' + call))


def test_log_read_decoys_do_not_supply_a_cursor(tmp_path):
    report = host_report(tmp_path, 'from pathlib import Path\n'
        '# cursor offset dedup seen_event\n'
        'raw = Path("/var/log/auth.log").read_text()\n')
    assert "B011" in codes(report)


def test_llm_data_flow_and_alias_need_a_real_opt_in(tmp_path):
    report = host_report(tmp_path, 'from openai import OpenAI as Client\n'
        'client = Client()\nraw_log = read_input()\npayload = raw_log\n'
        '# allow_external consent external_llm\n'
        'client.responses.create(input=payload)\n')
    assert "B013" in codes(report)


def test_llm_decoy_string_is_not_a_data_sink(tmp_path):
    report = host_report(tmp_path, 'description = "OpenAI raw_log message"\n')
    assert "B013" not in codes(report)


def test_dynamic_registry_has_an_explicit_incomplete_verdict(tmp_path):
    path = tmp_path / "host.agent"
    path.write_text('AGENT a { TOOL work { INPUT { } } }')
    path.with_suffix('.py').write_text('from agentl import Host\nh = Host()\n'
        'for tool in discover_plugins():\n    h.tools[tool.name] = tool\n')
    report = check_pair(path)
    assert not report.ok()
    assert not report.complete
    assert any(f.code == "B014" and "INCOMPLETE" in f.message for f in report.findings)
    assert "frontière tenue" not in render(report)


def test_registry_in_local_builder_is_checked(tmp_path):
    path = tmp_path / "host.agent"
    path.write_text('AGENT a { TOOL work { INPUT { } } }')
    path.with_suffix('.py').write_text('from builder import build\n')
    (tmp_path / 'builder.py').write_text('from agentl import Host\n'
        'def build():\n    h = Host()\n    h.tools["work"] = lambda: None\n    return h\n')
    assert "B014" not in codes(check_pair(path))


def test_relative_constants_and_reexported_destructive_aliases(tmp_path):
    package = tmp_path / 'pkg'
    package.mkdir()
    (package / '__init__.py').write_text('from .actions import erase\n')
    (package / 'settings.py').write_text('LIMIT = 0.15\n')
    (package / 'actions.py').write_text('from .settings import LIMIT\n'
        'from os import remove as erase\nif risk > LIMIT: pass\n')
    report = host_report(tmp_path, 'from pkg import erase\nerase(target)\n')
    assert {'B005', 'B008', 'B010', 'B012'} <= codes(report)


def test_filter_builtin_and_comprehension_variants(tmp_path):
    report = host_report(tmp_path, 'a = filter(predicate, xs)\n'
        'b = {x for x in xs if x.blocked}\n'
        'c = {x.id: x for x in xs if x.blocked}\n'
        'd = (x for x in xs if x.blocked)\n')
    assert len([f for f in report.findings if f.code == 'B002']) == 4


def test_waiver_continuation_comments_are_preserved(tmp_path):
    report = host_report(tmp_path, '# BOUNDARY-OK: parse protocol code\n'
        '# published by the upstream service\nif status == "OK": pass\n')
    assert report.ok() and report.waivers


@pytest.mark.parametrize('gap', ['\n', '"docstring"\n'])
def test_waiver_cannot_jump_over_unrelated_text(tmp_path, gap):
    assert not host_report(tmp_path, '# BOUNDARY-OK: protocol code\n'
        + gap + 'if status == "OK": pass\n').ok()


@pytest.mark.parametrize('imports', [
    'import importlib as loader\nm = loader.import_module("rules")',
    'from importlib import import_module as load\nm = load("rules")',
    'm = __import__("rules")',
])
def test_literal_dynamic_imports_are_followed(tmp_path, imports):
    (tmp_path / 'rules.py').write_text('if risk > .1: pass\n')
    report = host_report(tmp_path, imports)
    assert 'B005' in codes(report)
    assert report.complete


@pytest.mark.parametrize('source', ['exec(source)', 'eval(source)', 'from rules import *'])
def test_unresolved_code_never_produces_a_clean_verdict(tmp_path, source):
    report = host_report(tmp_path, source)
    assert not report.ok() and not report.complete


def test_namespace_package_and_src_layout(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    source = tmp_path / 'src' / 'namespace'
    source.mkdir(parents=True)
    (source / 'rules.py').write_text('if risk > .1: pass\n')
    assert 'B005' in codes(host_report(tmp_path, 'from namespace import rules'))


def test_missing_and_invalid_entrypoint_are_reported(tmp_path):
    assert not check_host(tmp_path / 'missing.py').ok()
    report = host_report(tmp_path, 'def invalid(:\n')
    assert not report.ok() and not report.complete
    assert 'B016' in codes(report)


def test_invalid_external_policy_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='external_policy'):
        host_report(tmp_path, '', external_policy='allow-everything')


def test_constants_are_scoped_and_named_containers_are_supported(tmp_path):
    report = host_report(tmp_path, 'LIMIT = .95\n'
        'def f(LIMIT):\n    return risk > LIMIT\n'
        'def g():\n    LIMIT: float = -.1\n    return risk > LIMIT\n'
        'BLOCKED = {"CN": 1, "RU": 2}\nif country in BLOCKED: pass\n')
    assert not [f for f in report.findings if f.line == 3 and f.code == 'B005']
    assert any(f.line == 6 and f.code == 'B005' for f in report.findings)
    assert any(f.code == 'B001' for f in report.findings)


@pytest.mark.parametrize('guard', ['not dry_run', 'dry_run is False', 'False == dry_run'])
def test_guarded_action_has_a_safe_default_and_matching_protections(tmp_path, guard):
    report = host_report(tmp_path, f'''import os
def act(target, *, dry_run=True):
    if {guard}:
        validate_target(target)
        persist_rollback(target)
        os.remove(target)
''')
    assert not {'B008', 'B010', 'B012'} & codes(report)


def test_opt_in_flag_with_early_exception_is_a_gate(tmp_path):
    report = host_report(tmp_path, '''import os
async def act(target, real_actions_enabled=False):
    if not real_actions_enabled:
        raise PermissionError()
    validate_target(target)
    save_rollback(target)
    os.remove(target)
''')
    assert not {'B008', 'B010', 'B012'} & codes(report)


@pytest.mark.parametrize('before', [
    'if not dry_run or override:\n    pass',
    'assert not dry_run',
    'try:\n    validate_target(target)\nexcept Exception:\n    pass',
    'for x in xs:\n    validate_target(target)',
])
def test_partial_protection_does_not_survive_control_flow(tmp_path, before):
    body = '\n'.join('    ' + line for line in before.splitlines())
    report = host_report(tmp_path, 'import os\ndef act(target, dry_run=True):\n'
        + body + '\n    os.remove(target)\n')
    assert {'B008', 'B010'} <= codes(report)


def test_log_cursor_is_bound_to_the_read_handle(tmp_path):
    report = host_report(tmp_path, '''with open('/var/log/auth.log') as events:
    events.seek(saved_offset)
    raw = events.read()
with open('/var/log/audit.log') as other:
    raw = other.read()
''')
    findings = [f for f in report.findings if f.code == 'B011']
    assert len(findings) == 1 and findings[0].line == 5


def test_llm_opt_in_protects_only_the_guarded_path(tmp_path):
    report = host_report(tmp_path, '''from openai import OpenAI
client = OpenAI()
def send(raw_log, allow_external=False):
    if not allow_external:
        return
    payload = raw_log
    client.responses.create(input=payload)
def leak(raw_log, allow_external=False):
    client.responses.create(input=raw_log)
''')
    findings = [f for f in report.findings if f.code == 'B013']
    assert len(findings) == 1 and findings[0].line == 9


def test_a_guard_at_lambda_creation_does_not_protect_later_execution(tmp_path):
    report = host_report(tmp_path, '''import os
def factory(target, dry_run=True):
    if dry_run: return
    return lambda: os.remove(target)
''')
    assert 'B008' in codes(report)


def test_dynamic_decorator_is_also_incomplete(tmp_path):
    path = tmp_path / 'host.agent'
    path.write_text('AGENT a { TOOL work { INPUT { } } }')
    path.with_suffix('.py').write_text('from agentl import Host\nh = Host()\n'
        '@h.tool(discover_name())\ndef work(): pass\n')
    report = check_pair(path)
    assert not report.complete and not report.ok()
    assert any('INCOMPLETE' in f.message for f in report.findings if f.code == 'B014')


def test_cli_fails_for_dynamic_registry_and_external_policy(tmp_path, capsys):
    from agentl.cli import main

    path = tmp_path / 'host.agent'
    path.write_text('AGENT a { }')
    path.with_suffix('.py').write_text('import vendor\nfrom agentl import Host\nh = Host()\n')
    assert main(['boundary', str(path), '--project-root', str(tmp_path),
                 '--external-policy', 'error']) == 1
    assert 'vendor' in capsys.readouterr().out


def test_bound_destructive_method_alias_cannot_hide_the_action(tmp_path):
    assert {'B008', 'B010', 'B012'} <= codes(host_report(tmp_path,
        'erase = path.unlink\nerase()\n'))


def test_subagent_registries_are_never_silently_merged_or_excluded(tmp_path):
    path = tmp_path / 'host.agent'
    path.write_text('AGENT a { TOOL work { INPUT { } } }')
    path.with_suffix('.py').write_text('import child\nfrom agentl import Host\n'
        'h = Host()\nh.tools["work"] = lambda: None\n')
    (tmp_path / 'child.agent').write_text('AGENT child { TOOL other { INPUT { } } }')
    (tmp_path / 'child.py').write_text('from agentl import Host\nh = Host()\n'
        'h.tools["other"] = lambda: None\n')
    report = check_pair(path)
    assert not report.complete
    assert not any('non déclarés' in f.message for f in report.findings)
    assert any('plusieurs hôtes' in f.message for f in report.findings)


def test_unresolved_relative_import_is_incomplete(tmp_path):
    package = tmp_path / 'pkg'
    package.mkdir()
    (package / '__init__.py').write_text('from .missing import eligible\n')
    report = host_report(tmp_path, 'import pkg\n')
    assert not report.complete and 'B016' in codes(report)


def test_project_root_excludes_entrypoint_explicitly(tmp_path):
    root = tmp_path / 'elsewhere'
    root.mkdir()
    report = host_report(tmp_path, 'if score > 2: pass', project_root=root)
    assert not report.complete and report.analyzed_paths == []


def test_diagnostic_baseline_detects_a_new_finding_in_an_already_red_host():
    from tools.check_boundary_examples import regressions
    baseline = {'host.agent': ['host.py:1 B008 known']}
    added = regressions({'host.agent': ['host.py:1 B008 known', 'helper.py:2 B010 new']}, baseline)
    assert added == {'host.agent': ['helper.py:2 B010 new']}
    assert regressions({'host.agent': []}, baseline) == {}


def test_small_host_pass_is_explicitly_scoped(tmp_path):
    report = host_report(tmp_path, 'import os\ndef observe(x): return x\n')
    output = render(report)
    assert report.ok()
    assert 'aucun diagnostic bloquant sur la surface analysée' in output
    assert 'hors analyse : os' in output
    assert 'frontière tenue' not in output


@pytest.mark.parametrize('command', [
    '["iptables", "-A", "INPUT", "-s", target, "-j", "DROP"]',
    '["ip", "link", "set", "dev", target, "down"]',
    'command',
])
def test_dynamic_argv_does_not_hide_a_destructive_command(tmp_path, command):
    assert {'B008', 'B010', 'B012'} <= codes(host_report(tmp_path,
        'import subprocess\nsubprocess.run(' + command + ')\n'))

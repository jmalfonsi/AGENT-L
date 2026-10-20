"""1.15.2 : seul le noyau atteint l'hôte — vérifié sur l'arbre syntaxique.

    python -m pytest tests/test_v1152_noncircumvention.py -q

Le contrôle (`tools/check_noncircumvention.py`) a des dents : chaque violation
plausible ci-dessous doit être attrapée, sinon il ne prouve rien.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_noncircumvention as nc                      # noqa: E402


def test_the_package_has_no_path_around_the_kernel():
    assert nc.scan() == []


def test_the_allow_lists_hold_no_stale_scope():
    assert nc.stale_allowances() == []


def test_the_kernel_has_exactly_one_call_site_for_each_boundary():
    import ast
    tree = ast.parse((ROOT / nc.GATE).read_text(encoding="utf-8"))
    accessed = [n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and n.attr in nc.DYNAMIC]
    assert sorted(accessed) == ["invoke", "subagents"]


@pytest.mark.parametrize("name,source", [
    ("appel direct de l'hôte depuis le runtime",
     "class Runtime:\n    def call_tool(self, n, a):\n"
     "        return self.host.invoke(n, a)\n"),
    ("appel direct depuis une fonction de module",
     "def go(host):\n    return host.invoke('x', {})\n"),
    ("appel dans une méthode qui ne s'appelle pas invoke",
     "class Wrapper:\n    def run(self, n, a):\n"
     "        return self._inner.invoke(n, a)\n"),
    ("sous-agent appelé hors du noyau",
     "class Runtime:\n    def go(self, n, p):\n"
     "        return self.host.subagents[n](p)\n"),
    ("registre des sous-agents lu par un module neuf",
     "def peek(host):\n    return host.subagents.get('x')\n"),
    ("accès dynamique par getattr",
     "def go(host):\n    return getattr(host, 'invoke')('x', {})\n"),
    ("accès dynamique à subagents",
     "def go(host):\n    return getattr(host, 'subagents')\n"),
    ("accès dynamique par __getattribute__",
     "def go(host):\n    return host.__getattribute__('invoke')\n"),
])
def test_each_circumvention_is_caught(name, source):
    assert nc.scan_source("agentl/new_module.py", source), name


@pytest.mark.parametrize("source", [
    # une enveloppe d'hôte : elle transmet, sous le permis du noyau
    "class Wrap:\n    def invoke(self, n, a):\n        return self._inner.invoke(n, a)\n",
    "class Wrap:\n    async def invoke(self, n, a):\n        return await self._inner.invoke(n, a)\n",
])
def test_a_pass_through_wrapper_is_not_a_violation(source):
    assert nc.scan_source("agentl/new_module.py", source) == []


def test_the_kernel_scope_is_exact():
    ok = ("class Kernel:\n    def execute(self, p, host):\n"
          "        return host.invoke(p.target, p.args)\n")
    other = ok.replace("def execute", "def sneaky")
    assert nc.scan_source(nc.GATE, ok) == []
    assert nc.scan_source(nc.GATE, other)          # même fichier, autre méthode
    assert nc.scan_source("agentl/runtime.py", ok)  # même code, autre fichier


def test_an_allowance_is_a_scope_not_a_file():
    source = ("class StudioSession:\n    def other(self, host):\n"
              "        return host.invoke('x', {})\n")
    assert nc.scan_source("agentl/studio/session.py", source)


def test_the_cli_reports_and_fails(monkeypatch, capsys):
    monkeypatch.setattr(nc, "scan", lambda: ["agentl/x.py:3 : `.invoke` accédé"])
    assert nc.main([]) == 1
    assert "contournement" in capsys.readouterr().out
    monkeypatch.setattr(nc, "scan", lambda: [])
    assert nc.main([]) == 0

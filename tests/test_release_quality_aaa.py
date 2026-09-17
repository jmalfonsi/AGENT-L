"""Contrats de livraison que la documentation Grade AAA rend obligatoires."""
from __future__ import annotations

import re
from pathlib import Path

import agentl


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_version_is_consistent_across_release_surfaces():
    pyproject = re.search(
        r'(?m)^version = "([^"]+)"$', _text("pyproject.toml"),
    )
    assert pyproject is not None
    version = pyproject.group(1)

    assert agentl.__version__ == version
    assert f"**v{version}**" in _text("README.md")
    assert _text("docs/SPEC.md").startswith(
        f"# AGENT-L — sémantique de référence (v{version})",
    )
    assert f"## [{version}]" in _text("CHANGELOG.md")


def test_quality_policy_keeps_its_non_certification_boundary():
    quality = _text("docs/QUALITY.md")

    assert "n'est ni une certification externe" in quality
    assert "Non-garanties explicites" in quality
    assert "python3 -m pytest -q" in quality

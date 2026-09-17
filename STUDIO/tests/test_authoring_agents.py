from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from backend import services, templates
from backend.config import GRAMMAR_CONTRACT
from backend.runtime_template import (RUNTIME_LLM_SOURCE, RUNTIME_TEMPLATE_MARKER,
                                      RUNTIME_TEMPLATE_VERSION)


def test_gemini_runtime_uses_header_auth_and_json_mode() -> None:
    assert '"x-goog-api-key": key' in RUNTIME_LLM_SOURCE
    assert '"responseMimeType": "application/json"' in RUNTIME_LLM_SOURCE
    assert ":generateContent?key=" not in RUNTIME_LLM_SOURCE


def test_runtime_adapter_is_versioned_and_upgraded_in_place(tmp_path: Path) -> None:
    """S-1 : un correctif du gabarit doit descendre dans les projets existants.

    L'adaptateur n'était écrit que s'il était absent : la clé Gemini est
    restée en query string sur disque alors que le gabarit était corrigé.
    """
    root = tmp_path / "projet"
    root.mkdir()
    legacy = root / "runtime_llm.py"
    legacy.write_text(
        'url = f"{model}:generateContent?key={key}"\n', encoding="utf-8")
    assert templates.deployed_template_version(legacy) == "1"

    assert templates.ensure_runtime_adapter(root, {"entrypoint": ""}) is True
    assert templates.deployed_template_version(legacy) == RUNTIME_TEMPLATE_VERSION
    assert "x-goog-api-key" in legacy.read_text(encoding="utf-8")
    assert ":generateContent?key=" not in legacy.read_text(encoding="utf-8")

    archived = list((root / ".history").rglob("runtime_llm.py"))
    assert len(archived) == 1
    assert ":generateContent?key=" in archived[0].read_text(encoding="utf-8")

    # Deuxième passage : la version correspond déjà, rien n'est réécrit.
    assert templates.ensure_runtime_adapter(root, {"entrypoint": ""}) is False


def test_generated_adapter_compiles_and_carries_its_marker() -> None:
    compile(RUNTIME_LLM_SOURCE, "runtime_llm.py", "exec")
    assert RUNTIME_LLM_SOURCE.startswith(f"{RUNTIME_TEMPLATE_MARKER}{RUNTIME_TEMPLATE_VERSION}")


def test_gemini_adapter_names_its_non_nominal_failures() -> None:
    """A-8 : un blocage de sécurité levait une KeyError illisible."""
    for marker in ("blockReason", "MAX_TOKENS", "finishReason", "RuntimeBudgetExceeded"):
        assert marker in RUNTIME_LLM_SOURCE


def test_authoring_context_carries_the_whole_grammar_contract() -> None:
    """C-1 : l'auteur tourne sans outil ; hors du prompt, rien n'existe."""
    author = {"slug": "agentl-author", "name": "agentl-author",
              "content": Path(services.AUTHOR_SKILL).read_text(encoding="utf-8")}
    context = services.authoring_context([author])
    grammar = GRAMMAR_CONTRACT.read_text(encoding="utf-8")
    assert grammar in context
    # Le SKILL.md fait plus de 20 000 octets : l'ancienne coupe à 12 000 en
    # perdait 40 %, au milieu d'une phrase.
    assert len(author["content"]) > 12_000
    assert author["content"] in context


def test_extract_json_ignores_the_cli_preamble() -> None:
    """C-7 : partir de la première accolade rencontrée produit un JSON invalide."""
    noisy = (
        'thinking {incomplet\n'
        'event: {"type":"token_count","total":12}\n'
        '{"summary":"prêt","content":"# Skill"}\n'
    )
    assert services._extract_json(noisy) == {"summary": "prêt", "content": "# Skill"}


class FakeProcess:
    """`Popen` factice : un processus déjà terminé, qui rend une sortie fixe."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    def communicate(self, timeout=None):
        return self._stdout, ""

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout=None):
        return self.returncode


def test_codex_author_runs_read_only_with_structured_output(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_popen(args, **kwargs):
        seen["args"] = args
        # Codex écrit son message final dans le fichier demandé, pas sur stdout.
        target = args[args.index("--output-last-message") + 1]
        Path(target).write_text(
            json.dumps({"summary": "prêt", "content": "# Skill\n\nProcédure complète."}),
            encoding="utf-8")
        return FakeProcess(stdout="préambule bruyant {")

    monkeypatch.setattr(services.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(services.subprocess, "Popen", fake_popen)
    result = services.run_authoring_agent(
        engine="codex", model="", effort="high", budget=1,
        prompt="Crée un skill", schema=services.SKILL_DRAFT_SCHEMA,
        cwd=tmp_path, timeout=60,
    )

    args = seen["args"]
    assert args[:2] == ["/usr/bin/codex", "exec"]
    assert "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in args
    assert "--output-last-message" in args
    assert result["mode"] == "codex"
    assert result["content"].startswith("# Skill")
    assert result["cost_measured"] is False


def test_claude_code_author_disables_tools_and_parses_schema(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    envelope = {
        "structured_output": {"summary": "prêt", "content": "# Skill\n\nProcédure complète."},
        "usage": {"input_tokens": 120, "output_tokens": 80},
        "total_cost_usd": 0.012,
    }

    def fake_popen(args, **kwargs):
        seen["args"] = args
        return FakeProcess(stdout=json.dumps(envelope))

    monkeypatch.setattr(services.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(services.subprocess, "Popen", fake_popen)
    result = services.run_authoring_agent(
        engine="claude-code", model="claude-sonnet", effort="high", budget=0.5,
        prompt="Crée un skill", schema=services.SKILL_DRAFT_SCHEMA,
        cwd=tmp_path, timeout=60,
    )

    args = seen["args"]
    assert args[:2] == ["/usr/bin/claude", "--print"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--permission-mode") + 1] == "plan"
    assert "--no-session-persistence" in args
    assert result["mode"] == "claude-code"
    assert result["cost_usd"] == 0.012
    assert result["cost_measured"] is True


def test_stopping_a_run_reaches_the_authoring_cli(monkeypatch, tmp_path: Path) -> None:
    """EXE-1 : la requête HTTP ne porte plus l'auteur ; Arrêter doit l'atteindre."""
    terminated = threading.Event()

    class Blocking(FakeProcess):
        def communicate(self, timeout=None):
            terminated.wait(10)
            return "", ""

        def poll(self):
            return -15 if terminated.is_set() else None

        def terminate(self) -> None:
            self.returncode = -15
            terminated.set()

    monkeypatch.setattr(services.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(services.subprocess, "Popen", lambda args, **kwargs: Blocking())
    handle = services.RunHandle("auteur")
    threading.Timer(0.2, handle.cancel).start()
    with pytest.raises(services.AuthoringCancelled):
        services.run_authoring_agent(
            engine="claude-code", model="", effort="high", budget=1,
            prompt="Crée un skill", schema=services.SKILL_DRAFT_SCHEMA,
            cwd=tmp_path, timeout=60, handle=handle,
        )
    assert terminated.is_set()


def test_default_authoring_agent_follows_what_is_installed(monkeypatch) -> None:
    """C-6 : Codex était le défaut sans être installé."""
    monkeypatch.setattr(services.shutil, "which",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert services.default_authoring_agent() == "claude-code"
    monkeypatch.setattr(services.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert services.default_authoring_agent() == "codex"


def test_autoloop_model_is_inherited_from_a_usable_runtime() -> None:
    """La boucle ne sait piloter que gemini-* et claude-*."""
    assert services.autoloop_model_for({
        "runtimeProvider": "google-gemini", "runtimeModel": "gemini-3.1-flash-lite",
    }) == "gemini-3.1-flash-lite"
    assert services.autoloop_model_for({
        "runtimeProvider": "openai", "runtimeModel": "un-modele-openai",
    }) == ""
    assert services.autoloop_model_for({
        "runtimeProvider": "mock", "runtimeModel": "mock-deterministic",
        "autoloopModel": "claude-opus-5",
    }) == "claude-opus-5"


def test_deployed_projects_carry_the_current_adapter() -> None:
    """Le test qui manquait : il regarde le disque, pas la constante."""
    from backend.config import PROJECTS_ROOT

    for adapter in PROJECTS_ROOT.glob("*/runtime_llm.py"):
        version = templates.deployed_template_version(adapter)
        assert version == RUNTIME_TEMPLATE_VERSION, f"{adapter} est resté en v{version}"
        assert ":generateContent?key=" not in adapter.read_text(encoding="utf-8")

from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient


def load_isolated_app(tmp_path: Path):
    os.environ["AGENTL_STUDIO_DATA_DIR"] = str(tmp_path / "studio-data")
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]
    return importlib.import_module("backend.main")


def test_health_reports_real_versions(tmp_path: Path) -> None:
    """Les versions sont dérivées du dépôt, jamais recopiées en littéral."""
    module = load_isolated_app(tmp_path)
    from agentl import __version__

    contract = Path(
        module.REPO_ROOT, "SKILLS/agentl-author/references/generated/grammar-contract.md"
    ).read_text(encoding="utf-8")
    with TestClient(module.app) as client:
        health = client.get("/api/health").json()
        assert health["agentlVersion"] == __version__
        assert f"`{health['contractVersion']}`" in contract
        assert health["defaultAuthoringAgent"] in {"codex", "claude-code"}
        skills = client.get("/api/skills").json()
        assert {skill["slug"] for skill in skills} >= {
            "agentl-author", "policy-guard", "society-architect", "resilience-tester"
        }
        assert client.get("/api/projects").json()[0]["slug"] == "approval-guard"


def test_system_skills_come_from_files(tmp_path: Path) -> None:
    """A-9 : un skill système se relit sur disque, pas dans du code Python."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        skills = {skill["slug"]: skill for skill in client.get("/api/skills").json()}
    for slug in ("policy-guard", "society-architect", "resilience-tester"):
        on_disk = Path(module.REPO_ROOT, "SKILLS", slug, "SKILL.md").read_text(encoding="utf-8")
        assert skills[slug]["content"] == on_disk


def test_project_creation_files_and_path_isolation(tmp_path: Path) -> None:
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        skills = client.get("/api/skills").json()
        author = next(skill for skill in skills if skill["slug"] == "agentl-author")
        response = client.post("/api/projects", json={
            "name": "Agent facturation",
            "description": "Contrôle des demandes",
            "skills": [author["id"]],
            "config": {"architecture": "single", "maxTicks": 4, "generateOnCreate": False},
        })
        assert response.status_code == 201, response.text
        project = response.json()
        files = client.get(f"/api/projects/{project['id']}/files").json()
        assert {row["name"] for row in files} >= {
            "approval_guard.agent", "approval_guard.py", "runtime_llm.py", "project.agentl.json"
        }
        traversal = client.get(f"/api/projects/{project['id']}/file", params={"path": "../../pyproject.toml"})
        assert traversal.status_code == 400
        host = client.get(f"/api/projects/{project['id']}/file", params={"path": "approval_guard.py"})
        assert "build_runtime_llm(llm)" in host.json()["content"]


def test_renamed_project_stays_deletable(tmp_path: Path) -> None:
    """C-3 : le garde-fou compare au slug stocké, pas au nom courant."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        project = client.post("/api/projects", json={
            "name": "Agent Facturation", "skills": [],
            "config": {"generateOnCreate": False},
        }).json()
        root = Path(project["path"])
        assert client.patch(f"/api/projects/{project['id']}", json={"name": "Zebra Renommé"}).status_code == 200
        assert client.delete(f"/api/projects/{project['id']}").status_code == 204
        assert not root.exists()


def test_real_quality_suite_run_and_replay(tmp_path: Path) -> None:
    module = load_isolated_app(tmp_path)
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        suite = client.post(f"/api/projects/{project_id}/runs", json={"command": "quality-suite"})
        assert suite.status_code == 200, suite.text
        assert suite.json()["status"] == "passed"
        assert [gate["name"] for gate in suite.json()["metadata"]["gates"]] == ["check", "test", "verify", "boundary"]
        assert suite.json()["metadata"]["budgetSeconds"] > 0

        execution = client.post(f"/api/projects/{project_id}/runs", json={"command": "run"})
        assert execution.status_code == 200, execution.text
        run = execution.json()
        assert run["status"] == "passed"
        assert run["metadata"]["journal"]["entries"] > 0
        assert run["metadata"]["journal"]["crossings"]
        assert any(event["kind"] == "action" for event in run["metadata"]["events"])

        replay = client.post(f"/api/projects/{project_id}/runs", json={"command": "replay", "run_id": run["id"]})
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "passed"
        assert "rejeu conforme — trace identique" in replay.json()["output"]


def test_society_trace_is_attributed_per_agent(tmp_path: Path) -> None:
    """C-4 : sans le nom porté par la bannière, les ticks des deux agents se confondent."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        project = client.post("/api/projects", json={
            "name": "Société probe", "skills": [],
            "config": {"architecture": "multi", "generateOnCreate": False},
        }).json()
        run = client.post(f"/api/projects/{project['id']}/runs", json={"command": "run"}).json()
        assert run["status"] == "passed", run["output"][-2000:]
        agents = {event["agent"] for event in run["metadata"]["events"] if event["agent"]}
        assert len(agents) == 2, agents
        for agent in agents:
            ticks = [e["tick"] for e in run["metadata"]["events"] if e["agent"] == agent and e["kind"] == "tick"]
            assert ticks == sorted(ticks)


def test_roles_shape_the_scaffold(tmp_path: Path) -> None:
    """A-6 : les rôles saisis nomment les agents, et le résultat passe check."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        project = client.post("/api/projects", json={
            "name": "Facturation", "skills": [],
            "config": {"architecture": "multi", "generateOnCreate": False, "roles": [
                {"name": "Validateur Métier", "mission": "Valider les factures entrantes"},
                {"name": "Comptable", "mission": "Imputer et archiver"},
            ]},
        }).json()
        assert project["scaffold"]["agents"] == ["validateur_metier", "comptable"]
        source = client.get(f"/api/projects/{project['id']}/file",
                            params={"path": "agent_society.agent"}).json()["content"]
        assert "AGENT validateur_metier {" in source
        assert 'DESCRIPTION "Valider les factures entrantes"' in source
        assert "soc_analyst" not in source
        gate = client.post(f"/api/projects/{project['id']}/runs", json={"command": "check"}).json()
        assert gate["status"] == "passed", gate["output"]


def test_authoring_and_runtime_models_are_configured_independently(tmp_path: Path) -> None:
    module = load_isolated_app(tmp_path)
    project = module.store.list_projects()[0]
    with TestClient(module.app) as client:
        response = client.patch(f"/api/projects/{project['id']}", json={"config": {
            "authoringAgent": "claude-code",
            "authoringModel": "claude-code-model",
            "runtimeProvider": "google-gemini",
            "runtimeModel": "gemini-runtime-model",
            "runtimeApiKeyEnv": "GEMINI_API_KEY",
        }})
        assert response.status_code == 200, response.text
        config = response.json()["config"]
        assert config["authoringAgent"] == "claude-code"
        assert config["runtimeProvider"] == "google-gemini"
        assert config["authoringModel"] != config["runtimeModel"]

        disk = Path(project["path"], "project.agentl.json").read_text(encoding="utf-8")
        assert '"runtimeApiKeyEnv": "GEMINI_API_KEY"' in disk
        assert "build_runtime_llm" in Path(project["path"], "approval_guard.py").read_text(encoding="utf-8")

        invalid = client.patch(f"/api/projects/{project['id']}", json={"config": {
            "runtimeProvider": "google-gemini", "runtimeModel": "",
        }})
        assert invalid.status_code == 400


def test_key_env_and_base_url_are_constrained(tmp_path: Path) -> None:
    """S-2 : nommer un secret arbitraire et une URL de sortie était possible."""
    module = load_isolated_app(tmp_path)
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        refused = client.patch(f"/api/projects/{project_id}", json={"config": {
            "runtimeProvider": "openai-compatible", "runtimeModel": "x",
            "runtimeApiKeyEnv": "AWS_SECRET_ACCESS_KEY",
            "runtimeBaseUrl": "http://attacker.example/v1",
        }})
        assert refused.status_code == 400
        assert "AWS_SECRET_ACCESS_KEY" in refused.json()["detail"]

        elsewhere = client.patch(f"/api/projects/{project_id}", json={"config": {
            "runtimeProvider": "google-gemini", "runtimeModel": "g",
            "runtimeBaseUrl": "http://attacker.example/v1",
        }})
        assert elsewhere.status_code == 400

        unknown = client.patch(f"/api/projects/{project_id}", json={"config": {"portRobots": 7}})
        assert unknown.status_code == 400

        accepted = client.patch(f"/api/projects/{project_id}", json={"config": {
            "runtimeProvider": "openai-compatible", "runtimeModel": "local",
            "runtimeApiKeyEnv": "MISTRAL_API_KEY",
            "runtimeBaseUrl": "http://localhost:11434/v1",
        }})
        assert accepted.status_code == 200


def test_host_header_is_validated(tmp_path: Path) -> None:
    """A-10 : la défense contre le rebinding DNS d'un service local."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        assert client.get("/api/health").status_code == 200
        hostile = client.get("/api/health", headers={"host": "studio.attacker.example"})
        assert hostile.status_code == 400


def test_history_is_listed_and_restorable(tmp_path: Path) -> None:
    """A-1 : une sauvegarde qu'on ne peut pas restaurer n'en est pas une."""
    module = load_isolated_app(tmp_path)
    project = module.store.list_projects()[0]
    with TestClient(module.app) as client:
        original = client.get(f"/api/projects/{project['id']}/file",
                              params={"path": "approval_guard.agent"}).json()["content"]
        client.put(f"/api/projects/{project['id']}/file", params={"path": "approval_guard.agent"},
                   json={"content": original + "\n// marque\n"})
        entries = client.get(f"/api/projects/{project['id']}/history").json()
        entry = next(row for row in entries if row["path"] == "approval_guard.agent")
        archived = client.get(f"/api/projects/{project['id']}/history/file",
                              params={"stamp": entry["stamp"], "path": entry["path"]}).json()
        assert archived["content"] == original

        restored = client.post(f"/api/projects/{project['id']}/history/restore",
                               json={"stamp": entry["stamp"], "path": entry["path"]})
        assert restored.status_code == 200
        current = client.get(f"/api/projects/{project['id']}/file",
                             params={"path": "approval_guard.agent"}).json()["content"]
        assert current == original


def test_autoloop_receives_a_writer_and_an_output(tmp_path: Path) -> None:
    """C-2 : sans --model ni -o, la boucle ne pouvait rien corriger."""
    module = load_isolated_app(tmp_path)
    project = module.store.list_projects()[0]
    with TestClient(module.app) as client:
        client.patch(f"/api/projects/{project['id']}", json={"config": {
            "runtimeProvider": "google-gemini", "runtimeModel": "gemini-3.1-flash-lite",
            "runtimeApiKeyEnv": "GEMINI_API_KEY", "timeoutSeconds": 120,
        }})
        run = client.post(f"/api/projects/{project['id']}/runs", json={"command": "autoloop"}).json()
        argv = run["metadata"]["argv"]
        assert "--model" in argv and argv[argv.index("--model") + 1] == "gemini-3.1-flash-lite"
        assert "-o" in argv
        budget = float(argv[argv.index("--budget") + 1])
        # Un budget égal au timeout fait tuer la boucle à l'instant où elle
        # s'arrêterait d'elle-même : son rapport serait perdu.
        assert budget < 120


def test_background_run_streams_and_can_be_cancelled(tmp_path: Path) -> None:
    """A-5 : le run vit côté serveur ; la requête HTTP ne le porte plus."""
    module = load_isolated_app(tmp_path)
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        started = client.post(f"/api/projects/{project_id}/runs",
                              json={"command": "run", "wait": False}).json()
        assert started["status"] == "running"
        with client.stream("GET", f"/api/runs/{started['id']}/stream") as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
        assert "event: end" in body
        assert client.get(f"/api/runs/{started['id']}").json()["status"] in {"passed", "failed"}
        assert client.delete(f"/api/runs/{started['id']}").status_code == 200


def test_draft_is_validated_by_the_four_gates(tmp_path: Path) -> None:
    """A-2 : `check` seul laissait passer ce que boundary refuse."""
    module = load_isolated_app(tmp_path)
    project = module.store.list_projects()[0]
    with TestClient(module.app) as client:
        agent = client.get(f"/api/projects/{project['id']}/file",
                           params={"path": "approval_guard.agent"}).json()["content"]
        host = client.get(f"/api/projects/{project['id']}/file",
                          params={"path": "approval_guard.py"}).json()["content"]
        verdict = client.post(f"/api/projects/{project['id']}/draft/validate", json={
            "summary": "inchangé", "agent_source": agent, "host_source": host,
        }).json()
        assert verdict["passed"] is True
        assert [gate["name"] for gate in verdict["gates"]] == ["check", "test", "verify", "boundary"]

        broken = client.post(f"/api/projects/{project['id']}/draft/apply", json={
            "summary": "cassé", "agent_source": "AGENT casse { VERSION \"1.8\" }", "host_source": host,
        }).json()
        assert broken["applied"] is False
        assert client.get(f"/api/projects/{project['id']}/file",
                          params={"path": "approval_guard.agent"}).json()["content"] == agent


def test_project_creation_invokes_selected_authoring_agent(monkeypatch, tmp_path: Path) -> None:
    module = load_isolated_app(tmp_path)
    called: dict[str, object] = {}

    def fake_author(project, prompt, skills, remaining, **_):
        called.update({"engine": project["config"]["authoringAgent"], "skills": len(skills), "prompt": prompt})
        root = Path(project["path"])
        return {
            "summary": "Projet initial produit par l’agent auteur.",
            "agent_source": (root / "approval_guard.agent").read_text(encoding="utf-8"),
            "host_source": (root / "approval_guard.py").read_text(encoding="utf-8"),
            "mode": "claude-code", "model": "cli-default", "cost_usd": 0.01,
            "cost_measured": True, "duration_ms": 12, "usage": {"input_tokens": 10, "output_tokens": 10},
        }

    monkeypatch.setattr(module, "assistant_draft", fake_author)
    with TestClient(module.app) as client:
        author = next(skill for skill in client.get("/api/skills").json() if skill["slug"] == "agentl-author")
        response = client.post("/api/projects", json={
            "name": "Produit par Claude",
            "description": "Démontrer la création par agent auteur",
            "skills": [author["id"]],
            "config": {"authoringAgent": "claude-code", "generateOnCreate": True},
        })
        assert response.status_code == 201, response.text
        assert response.json()["authoring"]["applied"] is True
        assert response.json()["authoring"]["engine"] == "claude-code"
        assert called["engine"] == "claude-code"
        assert called["skills"] == 1
        runs = client.get("/api/runs", params={"project_id": response.json()["id"]}).json()
        assert any(run["command"] == "authoring-create" and run["status"] == "passed" for run in runs)


def test_authoring_budget_counts_the_initial_generation(monkeypatch, tmp_path: Path) -> None:
    """C-5 : la création initiale, souvent la plus chère, échappait au plafond."""
    module = load_isolated_app(tmp_path)

    def fake_author(project, prompt, skills, remaining, **_):
        root = Path(project["path"])
        return {
            "summary": "coûteux",
            "agent_source": (root / "approval_guard.agent").read_text(encoding="utf-8"),
            "host_source": (root / "approval_guard.py").read_text(encoding="utf-8"),
            "mode": "claude-code", "model": "m", "cost_usd": 3.0, "cost_measured": True,
            "duration_ms": 5, "usage": {},
        }

    monkeypatch.setattr(module, "assistant_draft", fake_author)
    with TestClient(module.app) as client:
        created = client.post("/api/projects", json={
            "name": "Budget brûlé", "skills": [],
            "config": {"authoringAgent": "claude-code", "generateOnCreate": True,
                       "authoringBudgetUsd": 2.5},
        }).json()
        assert module._authoring_spent(created["id"]) == 3.0
        refused = client.post(f"/api/projects/{created['id']}/assistant",
                              json={"prompt": "encore une modification"})
        assert refused.status_code == 402


def test_system_skills_are_editable_and_survive_a_restart(tmp_path: Path) -> None:
    """Le semis tourne à chaque démarrage : il ne doit plus écraser une édition."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        skill = next(s for s in client.get("/api/skills").json() if s["slug"] == "policy-guard")
        assert skill["source"] == "system"
        assert not skill["customized"]

        edited = client.put(f"/api/skills/{skill['id']}", json={
            "name": skill["name"], "description": "version maison",
            "domain": skill["domain"],
            "content": "# Policy Guard\n\nRègle maison : refuser sans preuve signée.\n",
        })
        assert edited.status_code == 200, edited.text
        assert edited.json()["customized"] == 1
        assert edited.json()["source"] == "system"
        assert edited.json()["slug"] == "policy-guard"
        assert "Règle maison" in edited.json()["content"]

        origin = client.get(f"/api/skills/{skill['id']}/origin").json()
        assert origin["differs"] is True
        assert origin["content"] == skill["content"]

        # Un skill système ne se supprime pas : il reviendrait au démarrage.
        assert client.delete(f"/api/skills/{skill['id']}").status_code == 403

    # Redémarrage : le semis repasse sur la base existante.
    restarted = load_isolated_app(tmp_path)
    with TestClient(restarted.app) as client:
        after = next(s for s in client.get("/api/skills").json() if s["slug"] == "policy-guard")
        assert "Règle maison" in after["content"], "l’édition a été écrasée par le semis"

        restored = client.post(f"/api/skills/{after['id']}/reset").json()
        assert restored["content"] == skill["content"]
        assert restored["customized"] == 0
        assert client.get(f"/api/skills/{after['id']}/origin").json()["differs"] is False


def test_edited_system_skill_reaches_projects_and_the_authoring_prompt(tmp_path: Path) -> None:
    """Une édition doit atteindre ce qui consomme le skill, pas rester en base."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        skill = next(s for s in client.get("/api/skills").json() if s["slug"] == "policy-guard")
        marker = "# Policy Guard\n\nInvariant maison très reconnaissable.\n"
        client.put(f"/api/skills/{skill['id']}", json={
            "name": skill["name"], "description": skill["description"],
            "domain": skill["domain"], "content": marker,
        })
        project = client.post("/api/projects", json={
            "name": "Consommateur", "skills": [skill["id"]],
            "config": {"generateOnCreate": False},
        }).json()
        copied = Path(project["path"], ".agentl", "skills", "policy-guard", "SKILL.md")
        assert copied.read_text(encoding="utf-8") == marker

        from backend.services import authoring_context

        assert "Invariant maison" in authoring_context(module.store.get_skills([skill["id"]]))


def test_skill_draft_leaves_a_trace_even_when_it_fails(monkeypatch, tmp_path: Path) -> None:
    """Un draft ne laissait rien : réussi ou raté, il n'y avait rien à consulter."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        def failing(**kwargs):
            raise RuntimeError("Codex CLI n’est pas installé sur le serveur")

        monkeypatch.setattr(module, "skill_draft", failing)
        refused = client.post("/api/skills/draft", json={
            "name": "Juridique", "domain": "business", "authoring_agent": "codex",
        })
        assert refused.status_code == 502
        runs = client.get("/api/runs").json()
        trace = next(run for run in runs if run["command"] == "skill-draft")
        assert trace["status"] == "failed"
        assert trace["project_id"] is None
        assert trace["metadata"]["skill"] == "Juridique"
        assert "n’est pas installé" in trace["output"]

        def working(**kwargs):
            return {"summary": "prêt", "content": "# Juridique\n\nRègles.\n",
                    "mode": "claude-code", "model": "cli", "cost_usd": 0.21,
                    "cost_measured": True, "duration_ms": 42_000, "usage": {}}

        monkeypatch.setattr(module, "skill_draft", working)
        ok = client.post("/api/skills/draft", json={
            "name": "Juridique", "domain": "business", "authoring_agent": "claude-code",
        })
        assert ok.status_code == 200
        done = next(run for run in client.get("/api/runs").json()
                    if run["command"] == "skill-draft" and run["status"] == "passed")
        assert done["metadata"]["costUsd"] == 0.21
        assert done["metadata"]["characters"] == len("# Juridique\n\nRègles.\n")
        # Le contenu produit n'est pas enregistré tant qu'il n'est pas relu.
        assert done["metadata"]["saved"] is False
        assert not any(s["slug"] == "juridique" for s in client.get("/api/skills").json())


def test_runs_table_migrates_to_a_nullable_project(tmp_path: Path) -> None:
    """Une base créée avant la trace des drafts refusait un run sans projet."""
    import sqlite3

    data_dir = tmp_path / "studio-data"
    data_dir.mkdir(parents=True)
    legacy = sqlite3.connect(data_dir / "studio.sqlite3")
    legacy.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
            path TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}',
            skills_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE skills (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT 'general',
            content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'custom',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, command TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, duration_ms INTEGER,
            exit_code INTEGER, output TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}');
        INSERT INTO projects VALUES ('p1','Ancien','ancien','','active','/tmp/x','{}','[]','2026-01-01','2026-01-01');
        INSERT INTO runs VALUES ('r1','p1','check','passed','2026-01-01',NULL,NULL,0,'ok','{}');
    """)
    legacy.commit(); legacy.close()

    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        # L'exécution d'origine survit à la reconstruction de la table.
        assert any(run["id"] == "r1" for run in client.get("/api/runs").json())
        orphan = module.store.create_run(None, "skill-draft")
        assert orphan["project_id"] is None


# --------------------------------------------------------------------------
# Phase 2 de l'audit du 2026-09-14 : fiabiliser l'exécution
# --------------------------------------------------------------------------

def services_module():
    return sys.modules["backend.services"]


def read_source(client, project_id: str, path: str = "approval_guard.agent") -> str:
    return client.get(f"/api/projects/{project_id}/file", params={"path": path}).json()["content"]


def test_a_crashing_gate_still_ends_its_run(monkeypatch, tmp_path: Path) -> None:
    """EXE-2 : sans `finally`, une exception laissait la suite « en cours » à vie."""
    module = load_isolated_app(tmp_path)

    def explode(*args, **kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr(services_module(), "_stream_process", explode)
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        suite = client.post(f"/api/projects/{project_id}/runs", json={"command": "quality-suite"}).json()
        assert suite["status"] == "failed"
        assert "disque plein" in suite["output"]
        with client.stream("GET", f"/api/runs/{suite['id']}/stream") as response:
            assert "event: end" in "".join(response.iter_text())


def test_runs_left_running_by_a_stop_are_marked_interrupted(tmp_path: Path) -> None:
    """EXE-3 : le registre vit en mémoire ; après un arrêt, rien ne finissait ces runs."""
    module = load_isolated_app(tmp_path)
    project_id = module.store.list_projects()[0]["id"]
    orphan = module.store.create_run(project_id, "run")

    restarted = load_isolated_app(tmp_path)
    run = restarted.store.get_run(orphan["id"])
    assert run["status"] == "interrupted"
    assert "serveur s'est arrêté" in run["output"]
    with TestClient(restarted.app) as client:
        with client.stream("GET", f"/api/runs/{orphan['id']}/stream") as response:
            body = "".join(response.iter_text())
    assert "event: end" in body and '"interrupted"' in body


def test_stream_resumes_where_it_stopped(tmp_path: Path) -> None:
    """EXE-4 : un flux coupé reprend à sa position, sans tout renvoyer."""
    module = load_isolated_app(tmp_path)
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        run = client.post(f"/api/projects/{project_id}/runs", json={"command": "check"}).json()
        with client.stream("GET", f"/api/runs/{run['id']}/stream") as response:
            full = "".join(response.iter_text())
        ids = [int(line[4:]) for line in full.splitlines() if line.startswith("id: ")]
        assert ids and ids == sorted(ids)
        with client.stream("GET", f"/api/runs/{run['id']}/stream",
                           params={"from": ids[-1]}) as response:
            rest = "".join(response.iter_text())
    before_end, _, _ = rest.partition("event: end")
    assert "data: " not in before_end
    assert "event: end" in rest


def test_gate_warnings_are_counted_and_located(tmp_path: Path) -> None:
    """EXE-6 : `check` passait avec W120 comme sans rien — « PASSE » en vert."""
    module = load_isolated_app(tmp_path)
    with TestClient(module.app) as client:
        project = client.post("/api/projects", json={
            "name": "Société diagnostiquée", "skills": [],
            "config": {"architecture": "multi", "generateOnCreate": False},
        }).json()
        suite = client.post(f"/api/projects/{project['id']}/runs",
                            json={"command": "quality-suite"}).json()
    check = suite["metadata"]["gates"][0]
    assert check["name"] == "check" and check["status"] == "passed"
    assert check["warnings"] >= 1 and check["errors"] == 0
    w120 = next(row for row in suite["metadata"]["diagnostics"] if row["code"] == "W120")
    assert w120["severity"] == "warning" and w120["gate"] == "check"
    assert isinstance(w120["line"], int) and w120["agent"]


def test_draft_validation_shares_one_budget(monkeypatch, tmp_path: Path) -> None:
    """EXE-8 : chaque porte recevait le délai complet, soit quatre délais pleins."""
    module = load_isolated_app(tmp_path)
    project = module.store.list_projects()[0]
    granted: list[int] = []

    def slow_gate(handle, args, cwd, timeout):
        granted.append(timeout)
        time.sleep(2)
        return 0

    with TestClient(module.app) as client:
        client.patch(f"/api/projects/{project['id']}", json={"config": {"timeoutSeconds": 5}})
        agent = read_source(client, project["id"])
        host = read_source(client, project["id"], "approval_guard.py")
        monkeypatch.setattr(services_module(), "_stream_process", slow_gate)
        verdict = client.post(f"/api/projects/{project['id']}/draft/validate", json={
            "summary": "lent", "agent_source": agent, "host_source": host,
        }).json()
    assert granted[0] <= 5 and granted == sorted(granted, reverse=True)
    assert len(granted) < 4
    assert verdict["passed"] is False
    assert any(gate["status"] == "skipped" for gate in verdict["gates"])


def test_runs_on_one_project_take_turns(monkeypatch, tmp_path: Path) -> None:
    """EXE-9 : deux runs simultanés effaçaient chacun le métrage de l'autre."""
    module = load_isolated_app(tmp_path)
    services = services_module()
    spans: list[tuple[float, float]] = []

    def gate(handle, args, cwd, timeout):
        began = time.monotonic()
        time.sleep(0.4)
        spans.append((began, time.monotonic()))
        return 0

    monkeypatch.setattr(services, "_stream_process", gate)
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        first = client.post(f"/api/projects/{project_id}/runs", json={"command": "check", "wait": False}).json()
        second = client.post(f"/api/projects/{project_id}/runs", json={"command": "check", "wait": False}).json()
        done = [services.await_run(module.store, run["id"], 30) for run in (first, second)]
    assert [run["status"] for run in done] == ["passed", "passed"]
    (_, first_end), (second_start, _) = sorted(spans)
    assert second_start >= first_end
    assert "En attente" in done[1]["output"]


def test_applying_while_a_run_holds_the_project_is_refused(monkeypatch, tmp_path: Path) -> None:
    """EXE-9 : une proposition ne s'écrit plus pendant que les portes relisent les sources."""
    module = load_isolated_app(tmp_path)
    services = services_module()
    project_id = module.store.list_projects()[0]["id"]

    def endless(handle, args, cwd, timeout):
        while not handle.cancelled:
            time.sleep(0.05)
        return -15

    with TestClient(module.app) as client:
        agent = read_source(client, project_id)
        host = read_source(client, project_id, "approval_guard.py")
        monkeypatch.setattr(services, "_stream_process", endless)
        run = client.post(f"/api/projects/{project_id}/runs", json={"command": "run", "wait": False}).json()
        time.sleep(0.3)
        refused = client.post(f"/api/projects/{project_id}/draft/apply", json={
            "summary": "trop tôt", "agent_source": agent, "host_source": host,
        })
        assert refused.status_code == 409
        assert client.delete(f"/api/runs/{run['id']}").json()["cancelled"] is True
        assert services.await_run(module.store, run["id"], 10)["status"] == "cancelled"


def test_deleting_a_project_stops_its_runs_first(monkeypatch, tmp_path: Path) -> None:
    """EXE-9 : supprimer faisait un `rmtree` sous un run actif."""
    module = load_isolated_app(tmp_path)
    services = services_module()

    def endless(handle, args, cwd, timeout):
        while not handle.cancelled:
            time.sleep(0.05)
        return -15

    with TestClient(module.app) as client:
        project = client.post("/api/projects", json={
            "name": "À supprimer", "skills": [], "config": {"generateOnCreate": False},
        }).json()
        monkeypatch.setattr(services, "_stream_process", endless)
        run = client.post(f"/api/projects/{project['id']}/runs", json={"command": "run", "wait": False}).json()
        time.sleep(0.3)
        assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    handle = services.REGISTRY.get(run["id"])
    assert handle.finished.is_set() and handle.result["status"] == "cancelled"
    assert not Path(project["path"]).exists()
    assert not (services.RUNS_ROOT / run["id"]).exists()


def test_only_the_latest_runs_keep_their_artifacts(monkeypatch, tmp_path: Path) -> None:
    """EXE-9 : `runs/<id>` n'était jamais purgé ; la ligne de run, elle, reste."""
    monkeypatch.setenv("AGENTL_STUDIO_KEEP_ARTIFACTS", "2")
    module = load_isolated_app(tmp_path)
    services = services_module()
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        runs = [client.post(f"/api/projects/{project_id}/runs", json={"command": "check"}).json()
                for _ in range(3)]
    kept = [run["id"] for run in runs if (services.RUNS_ROOT / run["id"]).is_dir()]
    assert kept == [runs[1]["id"], runs[2]["id"]]
    assert len(module.store.list_runs(project_id)) == 3


def test_run_timeline_comes_from_structured_events(tmp_path: Path) -> None:
    """EXE-10 : la chronologie ne dépend plus du découpage des glyphes du terminal."""
    module = load_isolated_app(tmp_path)
    services = services_module()
    project_id = module.store.list_projects()[0]["id"]
    with TestClient(module.app) as client:
        run = client.post(f"/api/projects/{project_id}/runs", json={"command": "run"}).json()
    assert run["status"] == "passed"
    assert "--events" in run["metadata"]["argv"]
    events_path = Path(run["metadata"]["artifacts"]["events"])
    assert run["metadata"]["events"] == services.read_events(events_path)
    assert any(event["kind"] == "action" for event in run["metadata"]["events"])


def test_project_creation_returns_at_once_and_generation_can_be_stopped(monkeypatch, tmp_path: Path) -> None:
    """EXE-1 : la création retenait la requête jusqu'à dix minutes, sans arrêt possible."""
    module = load_isolated_app(tmp_path)
    services = services_module()
    writing = threading.Event()

    def stubborn_author(project, prompt, skills, remaining, *, handle):
        writing.set()
        while not handle.cancelled:
            time.sleep(0.05)
        raise services.AuthoringCancelled("arrêté")

    monkeypatch.setattr(module, "assistant_draft", stubborn_author)
    with TestClient(module.app) as client:
        began = time.monotonic()
        created = client.post("/api/projects", json={
            "name": "Génération longue", "skills": [], "wait": False,
            "config": {"generateOnCreate": True},
        }).json()
        assert time.monotonic() - began < 5
        run = created["authoringRun"]
        assert run["status"] == "running" and run["command"] == "authoring-create"
        assert writing.wait(5)
        original = read_source(client, created["id"])
        assert client.delete(f"/api/runs/{run['id']}").json()["cancelled"] is True
        done = services.await_run(module.store, run["id"], 10)
        assert done["status"] == "cancelled"
        assert read_source(client, created["id"]) == original


def test_assistant_proposal_is_read_back_from_its_run(monkeypatch, tmp_path: Path) -> None:
    """EXE-1 : la proposition vit dans le run, et rien n'est écrit sans validation."""
    module = load_isolated_app(tmp_path)
    services = services_module()
    project = module.store.list_projects()[0]

    def author(project, prompt, skills, remaining, *, handle):
        root = Path(project["path"])
        return {
            "summary": "relu", "mode": "codex", "model": "cli", "cost_usd": 0,
            "cost_measured": False, "duration_ms": 3, "usage": {},
            "agent_source": (root / "approval_guard.agent").read_text(encoding="utf-8") + "\n// relu\n",
            "host_source": (root / "approval_guard.py").read_text(encoding="utf-8"),
        }

    monkeypatch.setattr(module, "assistant_draft", author)
    with TestClient(module.app) as client:
        run = client.post(f"/api/projects/{project['id']}/assistant",
                          json={"prompt": "relis le programme", "wait": False}).json()
        done = services.await_run(module.store, run["id"], 30)
        assert done["status"] == "passed" and done["command"] == "authoring-draft"
        draft = client.get(f"/api/runs/{run['id']}/draft").json()
        assert draft["current"]["agent"] + "\n// relu\n" == draft["agent_source"]
        assert draft["application"] is None
        assert "// relu" not in read_source(client, project["id"])
        assert client.get(f"/api/projects/{project['id']}").json()["entrypoint"] == "approval_guard.agent"


from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_factory
import codegen_backend
import spec_analysis


SPEC = """Chaque matin, relever les demandes de congés déposées dans l'outil RH.
Pour chaque demande, vérifier le solde de jours du salarié. Si le solde est
suffisant et que le manager a donné son accord écrit, valider la demande.
Ne jamais valider une demande sans accord écrit du manager. Toute demande de
plus de dix jours consécutifs exige la validation du directeur des ressources
humaines. Envoyer au salarié un courriel de confirmation. La recette est
réussie si aucune demande sans accord n'est validée et si chaque demande
validée a produit exactement un courriel."""


def _raw_analysis():
    dimensions = {
        item["id"]: {
            "status": "present", "evidence": "cité", "gap": "", "questions": [],
        }
        for item in spec_analysis.DIMENSIONS
    }
    return {
        "title": "Validation des congés", "summary": "Résumé",
        "externalSystems": ["Outil RH"], "risks": ["courriel"],
        "dimensions": dimensions,
    }


def _prepare(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_factory, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(
        agent_factory, "analyze",
        lambda _prompt, *, workdir: (
            _raw_analysis(), {"sessionId": "analysis-test", "workdir": str(workdir)},
        ),
    )
    monkeypatch.setattr(codegen_backend, "skill_status", lambda _skills: {
        "agentl-author": {"resolved": True},
    })


def test_reanalyse_cree_une_revision_sans_perdre_le_build_valide(tmp_path, monkeypatch):
    """Une précision modifie le besoin, jamais l'historique déjà produit."""
    _prepare(monkeypatch, tmp_path)
    project = agent_factory.analyze_spec(
        text=SPEC, path=None, framework_ids=["agent_l"], project_id="prj_versions01")
    project["builds"] = [{
        "id": "build_v1", "frameworkId": "agent_l", "passed": True,
        "attemptCount": 1, "attempts": [], "artifacts": ["a.agent", "a.py"],
        "startedAt": "2026-01-01T00:00:00Z", "finishedAt": "2026-01-01T00:01:00Z",
    }]
    current = tmp_path / project["id"] / "agent_l"
    current.mkdir()
    (current / "a.agent").write_text("version un", encoding="utf-8")
    (current / "a.py").write_text("def build(): pass", encoding="utf-8")
    agent_factory._save_project(project)

    revised = agent_factory.answer_questions(project["id"], [{
        "question": "Quel seuil ?", "answer": "Le seuil confirmé est 0,08 g RMS.",
    }])

    assert revised["specRevision"] == 2
    assert [build["id"] for build in revised["builds"]] == ["build_v1"]
    assert revised["builds"][0]["version"] == 1
    assert revised["builds"][0]["specRevision"] == 1
    archived = tmp_path / project["id"] / revised["builds"][0]["artifactDir"] / "a.agent"
    assert archived.read_text(encoding="utf-8") == "version un"
    state = revised["frameworkStates"]["agent_l"]
    assert state["upToDate"] is False
    assert state["outdated"] is True
    assert state["publishedVersion"] == 1
    assert state["nextVersion"] == 2
    summary = agent_factory.project_list()["projects"][0]
    assert summary["specRevision"] == 2
    assert summary["builds"][0]["outdated"] is True


def test_generations_successives_sont_versionnees_sans_ecraser_v1(tmp_path, monkeypatch):
    """Chaque clic fabrique une version immuable et ne publie que si elle passe."""
    _prepare(monkeypatch, tmp_path)
    authored = {"count": 0}

    def fake_author(_prompt, *, workdir):
        authored["count"] += 1
        marker = f"version {authored['count']}"
        (workdir / "validation_des_conges.agent").write_text(marker, encoding="utf-8")
        (workdir / "validation_des_conges.py").write_text("def build(): pass", encoding="utf-8")
        return {"sessionId": f"session-{authored['count']}", "report": marker}

    monkeypatch.setattr(agent_factory, "author", fake_author)
    monkeypatch.setattr(agent_factory, "validate", lambda *_args: {
        "passed": True, "diagnosis": "ok", "steps": [], "files": {},
    })
    project = agent_factory.analyze_spec(
        text=SPEC, path=None, framework_ids=["agent_l"], project_id="prj_versions02")

    first = agent_factory.generate(project["id"], "agent_l")
    second = agent_factory.generate(project["id"], "agent_l")
    stored = agent_factory._load_project(project["id"])

    assert (first["version"], second["version"]) == (1, 2)
    assert [build["id"] for build in stored["builds"]] == [first["id"], second["id"]]
    assert (tmp_path / project["id"] / first["artifactDir"] /
            "validation_des_conges.agent").read_text(encoding="utf-8") == "version 1"
    assert (tmp_path / project["id"] / second["artifactDir"] /
            "validation_des_conges.agent").read_text(encoding="utf-8") == "version 2"
    assert (tmp_path / project["id"] / "agent_l" /
            "validation_des_conges.agent").read_text(encoding="utf-8") == "version 2"


def test_version_refusee_ne_remplace_pas_la_derniere_version_validee(tmp_path, monkeypatch):
    _prepare(monkeypatch, tmp_path)
    authored = {"count": 0}

    def fake_author(_prompt, *, workdir):
        authored["count"] += 1
        (workdir / "validation_des_conges.agent").write_text(
            f"version {authored['count']}", encoding="utf-8")
        (workdir / "validation_des_conges.py").write_text("def build(): pass", encoding="utf-8")
        return {"sessionId": "session", "report": "rapport"}

    verdicts = iter([True, False])
    monkeypatch.setattr(agent_factory, "author", fake_author)
    monkeypatch.setattr(agent_factory, "validate", lambda *_args: {
        "passed": next(verdicts), "diagnosis": "constat", "steps": [], "files": {},
    })
    project = agent_factory.analyze_spec(
        text=SPEC, path=None, framework_ids=["agent_l"], project_id="prj_versions03")

    first = agent_factory.generate(project["id"], "agent_l", attempts=1)
    second = agent_factory.generate(project["id"], "agent_l", attempts=1)

    assert first["passed"] is True
    assert second["passed"] is False
    assert (tmp_path / project["id"] / "agent_l" /
            "validation_des_conges.agent").read_text(encoding="utf-8") == "version 1"
    assert len(agent_factory._load_project(project["id"])["builds"]) == 2

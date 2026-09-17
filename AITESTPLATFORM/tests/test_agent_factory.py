"""Tests de la fabrique : ingestion, grille de complétude, validation.

Aucun test n'appelle l'agent de codage. Ce qui est vérifié ici, c'est ce que la
plateforme garantit *autour* de lui : refuser une entrée inexploitable, refuser
une génération sur un cahier des charges muet, et ne jamais déclarer valide un
agent dont la chaîne `agentl` n'est pas passée.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_validation  # noqa: E402
import spec_analysis  # noqa: E402
from spec_intake import SpecError, load_spec  # noqa: E402

SPEC = """Chaque matin, relever les demandes de congés déposées dans l'outil RH.
Pour chaque demande, vérifier le solde de jours du salarié. Si le solde est
suffisant et que le manager a donné son accord écrit, valider la demande.
Ne jamais valider une demande sans accord écrit du manager. Toute demande de
plus de dix jours consécutifs exige la validation du directeur des ressources
humaines. Envoyer au salarié un courriel de confirmation. La recette est
réussie si aucune demande sans accord n'est validée et si chaque demande
validée a produit exactement un courriel."""


# --------------------------------------------------------------------------- ingestion

def test_texte_trop_court_refuse():
    with pytest.raises(SpecError, match="trop court"):
        load_spec(text="Faire un agent.")


def test_texte_et_fichier_simultanes_refuses():
    with pytest.raises(SpecError, match="soit un texte"):
        load_spec(text=SPEC, path="/tmp/x.txt")


def test_empreinte_stable_apres_normalisation(tmp_path):
    direct = load_spec(text=SPEC)
    variante = load_spec(text=SPEC.replace("\n", "\r\n") + "   \n\n\n")
    assert direct["specHash"] == variante["specHash"]


def test_extension_non_prise_en_charge(tmp_path):
    fichier = tmp_path / "cdc.docx"
    fichier.write_bytes(b"x" * 200)
    with pytest.raises(SpecError, match="Format non pris en charge"):
        load_spec(path=str(fichier))


def test_fichier_markdown_lu(tmp_path):
    fichier = tmp_path / "cdc.md"
    fichier.write_text(SPEC, encoding="utf-8")
    spec = load_spec(path=str(fichier))
    assert spec["origin"]["kind"] == "file"
    assert "congés" in spec["text"]


# --------------------------------------------------------------------------- grille

def _analyse(**overrides):
    dimensions = {item["id"]: {"status": "present", "evidence": "cité", "gap": "", "questions": []}
                  for item in spec_analysis.DIMENSIONS}
    dimensions.update(overrides)
    return {"title": "Validation des congés", "summary": "…",
            "externalSystems": ["Outil RH"], "risks": ["envoi de courriel"],
            "dimensions": dimensions}


def test_grille_complete_est_prete():
    verdict = spec_analysis.evaluate(_analyse(), ["agent_l"])
    assert verdict["ready"] is True
    assert verdict["verdict"] == "ready"
    assert verdict["coverage"] == 1.0
    assert verdict["blockingQuestions"] == []


def test_dimension_bloquante_absente_empeche():
    verdict = spec_analysis.evaluate(
        _analyse(interdits={"status": "absent", "evidence": "", "gap": "aucun interdit",
                            "questions": ["Quelles actions sont interdites ?"]}),
        ["agent_l"])
    assert verdict["ready"] is False
    assert verdict["missingRequired"] == ["interdits"]
    assert [q["question"] for q in verdict["blockingQuestions"]] == \
        ["Quelles actions sont interdites ?"]


def test_interdits_non_bloquants_hors_agent_l():
    """`interdits` n'est exigé que par AGENT-L, seul framework qui les prouve."""
    absent = {"status": "absent", "evidence": "", "gap": "", "questions": ["?"]}
    assert spec_analysis.evaluate(_analyse(interdits=absent), ["langgraph"])["ready"] is True
    assert spec_analysis.evaluate(_analyse(interdits=absent), ["agent_l"])["ready"] is False


def test_partiel_remonte_en_question_non_bloquante():
    verdict = spec_analysis.evaluate(
        _analyse(exceptions={"status": "partial", "evidence": "x", "gap": "cas ambigu non traité",
                             "questions": ["Que faire si le solde est inconnu ?"]}),
        ["agent_l"])
    assert verdict["ready"] is True
    assert verdict["blockingQuestions"] == []
    assert len(verdict["advisoryQuestions"]) == 1


def test_statut_inconnu_traite_comme_absent():
    verdict = spec_analysis.evaluate(
        _analyse(objectif={"status": "peut-être", "evidence": "", "gap": "", "questions": []}),
        ["agent_l"])
    assert verdict["dimensions"][0]["status"] == "absent"
    assert verdict["ready"] is False


def test_analyse_vide_ne_leve_pas():
    verdict = spec_analysis.evaluate({}, ["agent_l"])
    assert verdict["ready"] is False
    assert len(verdict["missingRequired"]) == len(
        [d for d in spec_analysis.DIMENSIONS if "agent_l" in d["required_for"]])


def test_prompt_marque_les_dimensions_bloquantes():
    prompt = spec_analysis.build_prompt(SPEC, ["agent_l"])
    assert "interdits — Interdits et approbations" in prompt
    assert "[BLOQUANT]" in prompt
    assert SPEC[:60] in prompt


def test_rappel_auteur_liste_les_questions_non_bloquantes():
    verdict = spec_analysis.evaluate(
        _analyse(volumetrie={"status": "partial", "evidence": "", "gap": "",
                             "questions": ["Combien de demandes par jour ?"]}),
        ["agent_l"])
    rendu = spec_analysis.render_for_author(verdict)
    assert "Combien de demandes par jour ?" in rendu
    assert "sans inventer" in rendu


# --------------------------------------------------------------------------- validation

def test_paire_manquante_refusee(tmp_path):
    (tmp_path / "congés.agent").write_text("AGENT x { }", encoding="utf-8")
    rapport = agent_validation.validate(tmp_path, "agent_l", "congés")
    assert rapport["passed"] is False
    assert "Invariant de nommage" in rapport["diagnosis"]
    assert rapport["files"]["host"] is None


def test_programme_sans_scenario_echoue(tmp_path):
    (tmp_path / "a.agent").write_text('AGENT a { VERSION "1.4" }', encoding="utf-8")
    (tmp_path / "a.py").write_text("def build():\n    return None\n", encoding="utf-8")
    rapport = agent_validation.validate(tmp_path, "agent_l", "a")
    assert rapport["passed"] is False
    scenarios = next(s for s in rapport["steps"] if s["id"] == "scenarios")
    assert scenarios["passed"] is False


def test_programme_invalide_interrompt_la_chaine(tmp_path):
    (tmp_path / "a.agent").write_text(
        'AGENT a {\n  VERSION "1.4"\n  SCENARIO s { }\n  ceci nest pas du agent-l\n}',
        encoding="utf-8")
    (tmp_path / "a.py").write_text("def build():\n    return None\n", encoding="utf-8")
    rapport = agent_validation.validate(tmp_path, "agent_l", "a")
    assert rapport["passed"] is False
    ids = [s["id"] for s in rapport["steps"]]
    assert "check" in ids
    # les étapes situées après un échec bloquant sont marquées sautées,
    # jamais réussies par défaut
    assert all(s["skipped"] for s in rapport["steps"] if s.get("skipped"))
    assert not any(s["passed"] for s in rapport["steps"] if s.get("skipped"))


def test_module_generique_sans_build_refuse(tmp_path):
    (tmp_path / "a_langgraph.py").write_text("VALEUR = 1\n", encoding="utf-8")
    rapport = agent_validation.validate(tmp_path, "langgraph", "a")
    assert rapport["passed"] is False
    assert rapport["steps"][0]["exitCode"] == 3


def test_module_generique_avec_build_accepte(tmp_path):
    (tmp_path / "a_langgraph.py").write_text("def build():\n    return object()\n", encoding="utf-8")
    rapport = agent_validation.validate(tmp_path, "langgraph", "a")
    assert rapport["passed"] is True
    assert "non prouvée" in rapport["diagnosis"]


def test_module_generique_absent(tmp_path):
    rapport = agent_validation.validate(tmp_path, "crewai", "a")
    assert rapport["passed"] is False
    assert rapport["files"]["module"] is None


# --------------------------------------------------------------------------- orchestration

def test_generation_refusee_si_incomplet(tmp_path, monkeypatch):
    import agent_factory

    monkeypatch.setattr(agent_factory, "PROJECTS_ROOT", tmp_path)
    projet = {
        "id": "prj_test0001", "createdAt": "x", "updatedAt": "x", "stem": "a",
        "frameworkIds": ["agent_l"], "builds": [],
        "analysis": {"ready": False, "missingRequired": ["interdits"],
                     "title": "t", "summary": "", "externalSystems": [], "risks": [],
                     "dimensions": [], "advisoryQuestions": []},
    }
    (tmp_path / "prj_test0001").mkdir()
    (tmp_path / "prj_test0001" / "project.json").write_text(
        json.dumps(projet), encoding="utf-8")

    with pytest.raises(ValueError, match="incomplet"):
        agent_factory.generate("prj_test0001", "agent_l")


def test_framework_hors_cible_refuse(tmp_path, monkeypatch):
    import agent_factory

    monkeypatch.setattr(agent_factory, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "prj_test0002").mkdir()
    (tmp_path / "prj_test0002" / "project.json").write_text(json.dumps({
        "id": "prj_test0002", "createdAt": "x", "updatedAt": "x", "stem": "a",
        "frameworkIds": ["agent_l"], "builds": [],
        "analysis": {"ready": True, "missingRequired": []},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="ne fait pas partie"):
        agent_factory.generate("prj_test0002", "crewai")


def test_identifiant_projet_invalide_refuse(monkeypatch):
    import agent_factory

    with pytest.raises(ValueError, match="invalide"):
        agent_factory._project_dir("../../etc")


def test_radical_replie_les_accents():
    import agent_factory

    assert agent_factory._slug("Validation quotidienne des congés") == \
        "validation_quotidienne_des_conges"
    assert agent_factory._slug("Suivi d'incidents — niveau 2") == "suivi_d_incidents_niveau_2"
    assert agent_factory._slug("!!!") == "agent"


CANONIQUE = Path.home() / "AGENT-L" / "SKILLS" / "agentl-author" / "references" / "generated"


@pytest.mark.skipif(not (CANONIQUE / "canonical.agent").is_file(),
                    reason="exemple canonique du skill absent")
def test_chaine_accepte_un_agent_sain(tmp_path):
    for nom in ("canonical.agent", "canonical.py"):
        (tmp_path / nom).write_text((CANONIQUE / nom).read_text(encoding="utf-8"), encoding="utf-8")
    rapport = agent_validation.validate(tmp_path, "agent_l", "canonical")
    assert rapport["passed"] is True
    assert all(step["passed"] for step in rapport["steps"])


@pytest.mark.skipif(not (CANONIQUE / "canonical.agent").is_file(),
                    reason="exemple canonique du skill absent")
def test_chaine_rejette_une_politique_affaiblie(tmp_path):
    """Un `NEVER` retiré doit faire tomber la chaîne, sinon elle ne prouve rien.

    C'est le seul test qui distingue une validation réelle d'un tampon : si la
    suppression d'un interdit passe encore au vert, c'est qu'un `IF` applicatif
    double la POLICY et que `verify` ne porte pas.
    """
    source = (CANONIQUE / "canonical.agent").read_text(encoding="utf-8")
    mute = "\n".join(ligne for ligne in source.splitlines()
                     if "NEVER apply_request WHEN request.capacity <= 0" not in ligne)
    assert mute != source, "la règle visée n'existe plus dans l'exemple canonique"
    # même mutation que le préflight du skill : c'est elle dont le
    # scénario négatif canonique dépend.
    (tmp_path / "canonical.agent").write_text(mute, encoding="utf-8")
    (tmp_path / "canonical.py").write_text(
        (CANONIQUE / "canonical.py").read_text(encoding="utf-8"), encoding="utf-8")

    rapport = agent_validation.validate(tmp_path, "agent_l", "canonical")
    assert rapport["passed"] is False
    echec = next(s for s in rapport["steps"] if s["blocking"] and not s["passed"])
    assert echec["id"] in {"test", "verify"}


# --------------------------------------------------------------------------- skills

def test_chaque_framework_a_son_skill():
    import agent_factory
    from framework_skills import FRAMEWORK_LABELS, FRAMEWORK_SKILLS

    assert set(FRAMEWORK_SKILLS) == set(agent_factory.FRAMEWORK_IDS)
    assert set(FRAMEWORK_LABELS) == set(agent_factory.FRAMEWORK_IDS)
    assert len(set(FRAMEWORK_SKILLS.values())) == len(FRAMEWORK_SKILLS)


def test_chaque_framework_a_son_brief():
    import agent_factory

    autres = set(agent_factory.FRAMEWORK_IDS) - {"agent_l"}
    assert set(agent_factory.GENERIC_BRIEFS) == autres


@pytest.mark.parametrize("framework_id", ["agent_l", "langgraph", "crewai", "openai_agents"])
def test_le_brief_invoque_le_skill_du_framework(framework_id):
    """Un brief qui n'appelle pas son skill rend la méthode facultative."""
    import agent_factory
    from framework_skills import FRAMEWORK_SKILLS

    projet = {"stem": "mon_agent", "analysis": spec_analysis.evaluate(_analyse(), [framework_id])}
    prompt = agent_factory._build_prompt(projet, framework_id, SPEC, None)

    assert f"/{FRAMEWORK_SKILLS[framework_id]}" in prompt
    assert "mon_agent" in prompt
    assert SPEC[:60] in prompt


@pytest.mark.parametrize("framework_id", ["langgraph", "crewai", "openai_agents"])
def test_les_briefs_generiques_exigent_gardes_et_test_negatif(framework_id):
    """Sans preuve statique, la garde dans l'outil est le seul rempart réel."""
    import agent_factory

    prompt = agent_factory._build_prompt(
        {"stem": "a", "analysis": spec_analysis.evaluate(_analyse(), [framework_id])},
        framework_id, SPEC, None)
    assert "garde" in prompt.lower()
    assert "négatif" in prompt
    assert "aucune preuve statique" in prompt


def test_le_constat_precedent_est_joint_a_la_reparation():
    import agent_factory

    precedent = {
        "diagnosis": "Échec bloquant à l'étape « verify »",
        "steps": [
            {"id": "verify", "meaning": "Preuve sur l'AST", "passed": False,
             "skipped": False, "output": "T1 RÉFUTÉ : apply_request atteignable"},
            {"id": "boundary", "meaning": "Frontière", "passed": False,
             "skipped": True, "output": ""},
        ],
    }
    prompt = agent_factory._build_prompt(
        {"stem": "a", "analysis": spec_analysis.evaluate(_analyse(), ["agent_l"])},
        "agent_l", SPEC, precedent)

    assert "T1 RÉFUTÉ : apply_request atteignable" in prompt
    assert "Ne modifie pas les SCENARIO pour les faire" in prompt
    # une étape sautée n'a rien constaté : elle n'a pas à peser sur la réparation
    assert "Frontière" not in prompt


def test_skill_absent_est_signale_sans_bloquer(monkeypatch, tmp_path):
    """Un skill manquant doit se voir dans le résultat, pas empêcher la course."""
    import codegen_backend

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    etat = codegen_backend.skill_status(("agentl-author",))
    assert etat["agentl-author"]["resolved"] is False

    (tmp_path / ".claude" / "skills" / "agentl-author").mkdir(parents=True)
    assert codegen_backend.skill_status(("agentl-author",))["agentl-author"]["resolved"] is False
    (tmp_path / ".claude" / "skills" / "agentl-author" / "SKILL.md").write_text("x")
    assert codegen_backend.skill_status(("agentl-author",))["agentl-author"]["resolved"] is True

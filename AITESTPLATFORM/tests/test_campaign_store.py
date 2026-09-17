"""Persistance des campagnes : une campagne perdue, ce sont des exécutions
réelles déjà payées que l'utilisateur ne reverra jamais."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import campaign_store  # noqa: E402

ID = "camp_abcdef123456"


def record(**overrides) -> dict:
    base = {
        "campaignId": ID, "name": "1 tâche × 4 frameworks", "status": "running",
        "kind": "benchmark", "taskIds": ["hr.a"], "frameworkIds": ["agent_l"],
        "runIds": [], "matrixSummary": {},
    }
    base.update(overrides)
    return base


def test_save_puis_load_restitue_l_enregistrement(tmp_path: Path):
    campaign_store.save(tmp_path, record())
    loaded = campaign_store.load(tmp_path, ID)
    assert loaded["campaignId"] == ID
    assert loaded["createdAt"] and loaded["updatedAt"]


def test_la_fusion_conserve_les_champs_absents_du_nouvel_objet(tmp_path: Path):
    campaign_store.save(tmp_path, record(taskIds=["hr.a", "hr.b"], name="nom initial"))
    campaign_store.save(tmp_path, {"campaignId": ID, "status": "completed"})
    loaded = campaign_store.load(tmp_path, ID)
    assert loaded["status"] == "completed"
    assert loaded["taskIds"] == ["hr.a", "hr.b"]
    assert loaded["name"] == "nom initial"


def test_les_run_ids_s_accumulent_au_fil_de_la_campagne(tmp_path: Path):
    campaign_store.save(tmp_path, record())
    campaign_store.save(tmp_path, {"campaignId": ID, "runIds": ["run_1"]})
    campaign_store.save(tmp_path, {"campaignId": ID, "runIds": ["run_1", "run_2"]})
    assert campaign_store.load(tmp_path, ID)["runIds"] == ["run_1", "run_2"]


def test_createdat_n_est_pas_ecrase_par_une_mise_a_jour(tmp_path: Path):
    premier = campaign_store.save(tmp_path, record())
    second = campaign_store.save(tmp_path, {"campaignId": ID, "status": "completed"})
    assert second["createdAt"] == premier["createdAt"]


@pytest.mark.parametrize("mauvais", ["court", "../evasion", "avec espace", "a" * 200, None, 42, "point.point"])
def test_les_identifiants_hostiles_sont_refuses(tmp_path: Path, mauvais):
    with pytest.raises(ValueError):
        campaign_store.save(tmp_path, {"campaignId": mauvais})


def test_l_identifiant_ne_peut_pas_sortir_du_repertoire(tmp_path: Path):
    with pytest.raises(ValueError):
        campaign_store.campaign_path(tmp_path, "../../etc/passwd")


def test_statut_inconnu_refuse(tmp_path: Path):
    with pytest.raises(ValueError):
        campaign_store.save(tmp_path, record(status="bizarre"))


def test_type_de_campagne_inconnu_refuse(tmp_path: Path):
    with pytest.raises(ValueError):
        campaign_store.save(tmp_path, record(kind="autre"))


def test_l_ecriture_est_atomique_sans_fichier_temporaire_residuel(tmp_path: Path):
    campaign_store.save(tmp_path, record())
    restes = list((tmp_path / "campaigns").glob("*.tmp"))
    assert restes == []


def test_un_fichier_illisible_ne_fait_pas_tomber_la_lecture(tmp_path: Path):
    campaign_store.save(tmp_path, record())
    campaign_store.campaign_path(tmp_path, ID).write_text("pas du json", encoding="utf-8")
    assert campaign_store.load(tmp_path, ID) is None
    # Et la pagination l'ignore au lieu d'échouer.
    assert campaign_store.page(tmp_path)["total"] == 0


def test_campagne_absente_rend_none(tmp_path: Path):
    assert campaign_store.load(tmp_path, "camp_inexistant01") is None


def test_pagination_du_plus_recent_au_plus_ancien(tmp_path: Path):
    for index, moment in enumerate(["2026-08-01T00:00:00Z", "2026-08-03T00:00:00Z", "2026-08-02T00:00:00Z"]):
        campaign_store.save(tmp_path, record(campaignId=f"camp_00000000{index}", createdAt=moment))
    page = campaign_store.page(tmp_path, limit=2, offset=0)
    assert page["total"] == 3
    assert [item["campaignId"] for item in page["campaigns"]] == ["camp_000000001", "camp_000000002"]
    suite = campaign_store.page(tmp_path, limit=2, offset=2)
    assert [item["campaignId"] for item in suite["campaigns"]] == ["camp_000000000"]


def test_le_resume_ne_transporte_pas_la_matrice_complete(tmp_path: Path):
    campaign_store.save(tmp_path, record(runIds=["a", "b"], matrixSummary={"agent_l": {"lourd": [1] * 100}}))
    resume = campaign_store.page(tmp_path)["campaigns"][0]
    assert resume["runCount"] == 2
    assert "matrixSummary" not in resume


def test_pagination_ignore_les_fichiers_au_nom_non_conforme(tmp_path: Path):
    campaign_store.save(tmp_path, record())
    # Trop court pour le motif d'identifiant, et un nom porteur de séparateurs.
    (tmp_path / "campaigns" / "court.json").write_text(json.dumps(record()), encoding="utf-8")
    (tmp_path / "campaigns" / "avec espace.json").write_text(json.dumps(record()), encoding="utf-8")
    assert campaign_store.page(tmp_path)["total"] == 1


def test_repertoire_absent_rend_une_page_vide(tmp_path: Path):
    assert campaign_store.page(tmp_path) == {"campaigns": [], "total": 0, "limit": 50, "offset": 0}

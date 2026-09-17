"""Séparation des régimes de comparaison.

Un run « énoncé seul » et un run « parité de plan » ne répondent pas à la même
question. Les moyenner produirait un chiffre qui ne mesure rien, et c'est le
genre d'erreur qui ne se voit pas sur un tableau de bord.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import campaign_integrity  # noqa: E402
import campaign_store  # noqa: E402
import history_index  # noqa: E402
import stats as stats_module  # noqa: E402
from task_facade import baseline_instructions  # noqa: E402

V3 = "automationbench-native-prompt-facade-v3.1"
V4 = "automationbench-plan-parity-facade-v4"


def row(**overrides) -> dict:
    base = {
        "id": "run_x", "taskId": "hr.a", "taskNumber": 1, "taskTitle": "A", "domain": "hr",
        "frameworkId": "langgraph", "frameworkName": "LangGraph", "status": "completed",
        "valid": True, "success": True, "partialCredit": 1.0, "taskCompleted": 1.0,
        "toolCallCount": 4, "llmCallCount": 3, "executionTimeMs": 1000, "totalTokens": 100,
        "protocolVersion": V3, "regime": "prompt_only",
        "createdAt": "2026-08-01T00:00:00Z", "error": None,
    }
    base.update(overrides)
    return base


# --- consignes transmises ---------------------------------------------------

def test_sans_briefing_les_consignes_restent_celles_du_protocole_officiel():
    assert baseline_instructions("SYS") == "SYS"


def test_le_briefing_est_ajoute_sans_etre_reformule():
    resultat = baseline_instructions("SYS", "PLAN EXACT")
    assert resultat.startswith("SYS")
    assert resultat.endswith("PLAN EXACT")


def test_un_briefing_vide_ne_change_pas_les_consignes():
    # Un plan vide doit se comporter comme « pas de plan », jamais comme un
    # plan silencieusement tronqué.
    assert baseline_instructions("SYS", "") == "SYS"
    assert baseline_instructions("SYS", None) == "SYS"


# --- agrégats ---------------------------------------------------------------

def test_les_regimes_ne_sont_jamais_moyennes_ensemble():
    rows = [row(id="a", regime="prompt_only", partialCredit=0.0),
            row(id="b", regime="plan_parity", protocolVersion=V4, partialCredit=1.0)]
    resultat = stats_module.compute(rows)
    assert resultat["totalRuns"] == 1
    assert resultat["regime"] == "prompt_only"
    assert resultat["byFramework"][0]["averagePartialCredit"] == 0.0


def test_le_regime_demande_est_celui_qui_est_calcule():
    rows = [row(id="a", regime="prompt_only", partialCredit=0.0),
            row(id="b", regime="plan_parity", protocolVersion=V4, partialCredit=1.0)]
    resultat = stats_module.compute(rows, regime="plan_parity")
    assert resultat["totalRuns"] == 1
    assert resultat["byFramework"][0]["averagePartialCredit"] == 1.0


def test_le_regime_est_tranche_avant_la_version_de_protocole():
    """Le piège : `plan_parity` porte une version plus récente. Si la version
    était tranchée en premier, elle viderait `prompt_only` de tous ses runs."""
    rows = [row(id="a", regime="prompt_only", protocolVersion=V3, partialCredit=0.5),
            row(id="b", regime="plan_parity", protocolVersion=V4, partialCredit=1.0)]
    resultat = stats_module.compute(rows)
    assert resultat["totalRuns"] == 1
    assert resultat["protocolVersions"] == [V3]


def test_le_comptage_par_regime_porte_sur_tout_l_historique():
    # L'interface doit pouvoir dire « l'autre régime a des runs » même quand
    # le régime affiché est vide.
    rows = [row(id="a", regime="plan_parity", protocolVersion=V4)]
    resultat = stats_module.compute(rows, regime="prompt_only")
    assert resultat["totalRuns"] == 0
    assert resultat["regimeCounts"] == {"plan_parity": 1}


def test_all_reunit_explicitement_les_deux_regimes():
    rows = [row(id="a", regime="prompt_only", partialCredit=0.0),
            row(id="b", regime="plan_parity", protocolVersion=V4, partialCredit=1.0)]
    resultat = stats_module.compute(rows, regime=stats_module.ALL_REGIMES,
                                    protocol=stats_module.ALL_PROTOCOLS)
    assert resultat["totalRuns"] == 2


def test_un_run_sans_regime_compte_comme_enonce_seul():
    rows = [row(id="ancien")]
    del rows[0]["regime"]
    resultat = stats_module.compute(rows)
    assert resultat["totalRuns"] == 1
    assert resultat["regimeCounts"] == {"prompt_only": 1}


# --- historique -------------------------------------------------------------

def test_le_filtre_d_historique_distingue_les_regimes():
    entries = [row(id="a", regime="prompt_only"), row(id="b", regime="plan_parity")]
    assert [e["id"] for e in history_index.filtered(entries, regime="plan_parity")] == ["b"]


def test_un_run_ancien_est_retrouve_par_le_regime_par_defaut():
    ancien = row(id="a")
    del ancien["regime"]
    assert history_index.filtered([ancien], regime="prompt_only")


def test_la_ligne_legere_transporte_le_regime():
    assert "regime" in history_index.LIGHT_FIELDS
    assert "briefingChars" in history_index.LIGHT_FIELDS


def test_la_normalisation_donne_un_regime_aux_runs_anciens():
    normalise = campaign_integrity.normalize_history_run({"id": "a", "protocolVersion": V3})
    assert normalise["regime"] == "prompt_only"
    assert normalise["briefingChars"] == 0


def test_la_normalisation_conserve_un_regime_deja_present():
    normalise = campaign_integrity.normalize_history_run(
        {"id": "a", "protocolVersion": V4, "regime": "plan_parity", "briefingChars": 2901})
    assert normalise["regime"] == "plan_parity"
    assert normalise["briefingChars"] == 2901


# --- campagnes --------------------------------------------------------------

def campaign(**overrides) -> dict:
    base = {
        "campaignId": "camp_abcdef123456", "name": "essai", "status": "running",
        "kind": "benchmark", "taskIds": ["hr.a"], "frameworkIds": ["langgraph"],
        "runIds": [], "matrixSummary": {},
    }
    base.update(overrides)
    return base


def test_une_campagne_sans_regime_est_enregistree_comme_enonce_seul(tmp_path: Path):
    campaign_store.save(tmp_path, campaign())
    assert campaign_store.load(tmp_path, "camp_abcdef123456")["regime"] == "prompt_only"


def test_un_regime_inconnu_est_refuse(tmp_path: Path):
    with pytest.raises(ValueError):
        campaign_store.save(tmp_path, campaign(regime="parite_totale"))


def test_le_regime_survit_a_une_mise_a_jour_partielle(tmp_path: Path):
    campaign_store.save(tmp_path, campaign(regime="plan_parity"))
    campaign_store.save(tmp_path, {"campaignId": "camp_abcdef123456", "status": "completed"})
    assert campaign_store.load(tmp_path, "camp_abcdef123456")["regime"] == "plan_parity"


def test_le_resume_de_campagne_porte_le_regime(tmp_path: Path):
    campaign_store.save(tmp_path, campaign(regime="plan_parity"))
    assert campaign_store.page(tmp_path)["campaigns"][0]["regime"] == "plan_parity"

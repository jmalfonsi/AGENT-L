"""Agrégats et export : ces chiffres sont ceux que l'utilisateur lira pour
trancher entre deux frameworks. Une moyenne fausse est pire qu'une absence."""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import export_formats  # noqa: E402
import stats as stats_module  # noqa: E402

V3 = "automationbench-native-prompt-facade-v3.1"
V2 = "automationbench-prepared-facade-v2"


def row(**overrides) -> dict:
    base = {
        "id": "run_x", "taskId": "hr.a", "taskNumber": 1, "taskTitle": "A", "domain": "hr",
        "frameworkId": "agent_l", "frameworkName": "AGENT-L", "status": "completed",
        "valid": True, "success": True, "partialCredit": 1.0, "taskCompleted": 1.0,
        "toolCallCount": 4, "llmCallCount": 3, "executionTimeMs": 1000, "totalTokens": 100,
        "protocolVersion": V3, "createdAt": "2026-08-01T00:00:00Z", "error": None,
    }
    base.update(overrides)
    return base


# --- règles de calcul -------------------------------------------------------

def test_les_runs_invalides_sont_exclus_des_moyennes():
    rows = [
        row(id="a", partialCredit=1.0),
        row(id="b", partialCredit=0.0, valid=False, status="invalid", success=False),
    ]
    result = stats_module.compute(rows)
    block = result["byFramework"][0]
    assert block["totalRuns"] == 2
    assert block["validRuns"] == 1
    assert block["invalidRuns"] == 1
    # La moyenne ne doit pas être tirée vers 0,5 par le run invalide.
    assert block["averagePartialCredit"] == 1.0
    assert block["officialSuccessRate"] == 1.0


def test_moyenne_sur_ensemble_vide_vaut_none_et_pas_zero():
    result = stats_module.compute([row(valid=False, status="invalid", success=False)])
    block = result["byFramework"][0]
    assert block["averagePartialCredit"] is None
    assert block["averageTimeMs"] is None
    assert block["officialSuccessRate"] is None


def test_metrique_absente_ignoree_sans_compter_comme_zero():
    rows = [row(id="a", llmCallCount=None), row(id="b", llmCallCount=4)]
    block = stats_module.compute(rows)["byFramework"][0]
    assert block["averageLlmCalls"] == 4.0


def test_un_booleen_n_est_pas_compte_comme_un_nombre():
    """`success=True` ne doit jamais être moyenné comme 1."""
    assert stats_module.average([{"x": True}, {"x": False}], "x") is None


# --- versions de protocole --------------------------------------------------

def test_les_versions_de_protocole_ne_sont_pas_melangees_par_defaut():
    rows = [row(id="ancien", protocolVersion=V2, partialCredit=0.0),
            row(id="recent", protocolVersion=V3, partialCredit=1.0)]
    result = stats_module.compute(rows)
    assert result["totalRuns"] == 1
    assert result["excludedProtocolVersions"] == [V2]
    assert result["byFramework"][0]["averagePartialCredit"] == 1.0


def test_protocol_all_inclut_tout_explicitement():
    rows = [row(id="ancien", protocolVersion=V2, partialCredit=0.0),
            row(id="recent", protocolVersion=V3, partialCredit=1.0)]
    result = stats_module.compute(rows, protocol=stats_module.ALL_PROTOCOLS)
    assert result["totalRuns"] == 2
    assert result["byFramework"][0]["averagePartialCredit"] == 0.5


def test_les_versions_presentes_sont_triees_de_la_plus_ancienne_a_la_plus_recente():
    rows = [row(protocolVersion=V3), row(protocolVersion=V2), row(protocolVersion="legacy-v1")]
    _, present, _ = stats_module.select_rows(rows)
    assert present == ["legacy-v1", V2, V3]


def test_ordre_des_versions_gere_les_numeros_mineurs():
    ranks = [stats_module.protocol_rank(v) for v in ["legacy-v1", V2, "x-v3", "x-v3.1", "x-v3.2"]]
    assert ranks == sorted(ranks)


# --- filtres ----------------------------------------------------------------

def test_filtres_tache_et_framework():
    rows = [row(taskId="hr.a", frameworkId="agent_l"), row(taskId="hr.b", frameworkId="crewai")]
    assert len(stats_module.select_rows(rows, task="hr.a")[0]) == 1
    assert len(stats_module.select_rows(rows, framework="crewai")[0]) == 1


def test_filtre_since_conserve_les_runs_posterieurs():
    rows = [row(id="vieux", createdAt="2026-07-01T00:00:00Z"),
            row(id="neuf", createdAt="2026-08-05T00:00:00Z")]
    kept, _, _ = stats_module.select_rows(rows, since="2026-08-01T00:00:00Z")
    assert [item["id"] for item in kept] == ["neuf"]


# --- matrice ----------------------------------------------------------------

def test_la_matrice_croise_taches_et_frameworks():
    rows = [
        row(taskId="hr.a", frameworkId="agent_l", partialCredit=1.0),
        row(taskId="hr.a", frameworkId="crewai", partialCredit=0.5),
        row(taskId="hr.b", frameworkId="agent_l", partialCredit=0.0),
    ]
    matrix = stats_module.compute(rows)["matrix"]
    par_tache = {item["taskId"]: item for item in matrix}
    assert par_tache["hr.a"]["cells"]["agent_l"]["averagePartialCredit"] == 1.0
    assert par_tache["hr.a"]["cells"]["crewai"]["averagePartialCredit"] == 0.5
    # Une combinaison jamais exécutée n'invente pas de case.
    assert "crewai" not in par_tache["hr.b"]["cells"]


def test_compute_sur_liste_vide_ne_leve_pas():
    result = stats_module.compute([])
    assert result["totalRuns"] == 0
    assert result["byFramework"] == []
    assert result["matrix"] == []


# --- export -----------------------------------------------------------------

def test_export_csv_echappe_les_separateurs_et_les_guillemets():
    rows = [row(taskTitle='Titre, avec "guillemets"', error="ligne1\nligne2")]
    payload = export_formats.export("runs", "csv", rows=rows, summary={})
    parsed = list(csv.DictReader(io.StringIO(payload["content"])))
    assert parsed[0]["taskTitle"] == 'Titre, avec "guillemets"'
    assert parsed[0]["error"] == "ligne1\nligne2"


def test_export_csv_laisse_la_cellule_vide_pour_une_valeur_absente():
    payload = export_formats.export("runs", "csv", rows=[row(llmCallCount=None, totalTokens=None)], summary={})
    parsed = list(csv.DictReader(io.StringIO(payload["content"])))
    assert parsed[0]["llmCallCount"] == ""
    assert parsed[0]["totalTokens"] == ""
    # Surtout pas la chaîne « None », qui se lirait comme une donnée.
    assert "None" not in payload["content"]


def test_export_csv_a_un_entete_et_une_ligne_par_run():
    payload = export_formats.export("runs", "csv", rows=[row(id="a"), row(id="b")], summary={})
    lignes = [line for line in payload["content"].splitlines() if line.strip()]
    assert len(lignes) == 3
    assert lignes[0].startswith("id,")


def test_export_json_est_relisible():
    payload = export_formats.export("runs", "json", rows=[row(id="a")], summary={})
    assert json.loads(payload["content"])
    assert payload["contentType"].startswith("application/json")


def test_le_nom_de_fichier_est_date_et_lisible():
    payload = export_formats.export("runs", "csv", rows=[row()], summary={}, )
    assert payload["filename"].endswith(".csv")
    assert "aitestplatform" in payload["filename"]


def test_export_du_bilan_porte_le_resume():
    summary = stats_module.compute([row()])
    payload = export_formats.export("stats", "json", rows=[], summary=summary)
    assert json.loads(payload["content"])["byFramework"][0]["frameworkId"] == "agent_l"

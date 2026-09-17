"""Le régime arrive-t-il vraiment jusqu'au framework ?

Les tests précédents vérifient la transcription et la séparation des moyennes.
Celui-ci vérifie le seul point qui les relie : que `run_benchmark(..., regime)`
transmet bien le plan aux baselines, ne le transmet pas à AGENT-L, et étiquette
le run en conséquence.

Aucun appel au modèle n'est effectué : le runtime est remplacé par un espion.
Le monde AutomationBench et le barème, eux, sont réels — ils ne coûtent rien.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

benchmark_runner = pytest.importorskip(
    "benchmark_runner", reason="environnement AutomationBench absent")

TASK_ID = "hr.employee_request_routing"


@pytest.fixture
def espion(tmp_path: Path, monkeypatch):
    """Remplace les quatre runtimes par un espion et isole l'historique."""
    vu: dict = {}

    def faux_runtime(task, world, events):
        # C'est ici que les baselines liraient leurs consignes.
        vu["instructions"] = benchmark_runner._baseline_instructions()
        vu["briefing"] = benchmark_runner.ACTIVE_BRIEFING
        return {"finalAnswer": "rien fait", "tokenUsage": benchmark_runner._usage_empty()}

    monkeypatch.setattr(benchmark_runner, "RUNNERS",
                        {key: faux_runtime for key in benchmark_runner.RUNNERS})
    monkeypatch.setattr(benchmark_runner, "HISTORY_PATH", tmp_path / "run-history.jsonl")
    monkeypatch.setattr(benchmark_runner, "framework_status", lambda: [
        {"id": key, "name": key, "installed": True, "configured": True, "available": True}
        for key in ("agent_l", "langgraph", "crewai", "openai_agents")
    ])
    monkeypatch.setenv("GEMINI_API_KEY", "cle-de-test-non-utilisee")
    return vu


def test_par_defaut_aucun_plan_n_est_transmis(espion):
    resultat = benchmark_runner.run_benchmark(TASK_ID, "langgraph")
    assert espion["briefing"] is None
    assert espion["instructions"] == benchmark_runner.SYSTEM_INSTRUCTIONS
    assert resultat["regime"] == "prompt_only"
    assert resultat["briefingChars"] == 0
    assert resultat["protocolVersion"] == benchmark_runner.PROTOCOL_VERSION


def test_en_parite_de_plan_la_baseline_recoit_le_plan(espion):
    resultat = benchmark_runner.run_benchmark(TASK_ID, "langgraph", "plan_parity")
    assert "TASK PROCEDURE" in espion["instructions"]
    # Les consignes communes restent en tête : le plan s'ajoute, il ne remplace pas.
    assert espion["instructions"].startswith(benchmark_runner.SYSTEM_INSTRUCTIONS)
    assert resultat["regime"] == "plan_parity"
    assert resultat["briefingChars"] > 0
    assert resultat["protocolVersion"] == "automationbench-plan-parity-facade-v4"


def test_agent_l_ne_recoit_jamais_le_briefing(espion):
    """Il exécute déjà le programme dont le texte est tiré ; le lui donner en
    prose reviendrait à le servir deux fois."""
    resultat = benchmark_runner.run_benchmark(TASK_ID, "agent_l", "plan_parity")
    assert espion["briefing"] is None
    assert resultat["briefingChars"] == 0
    # Le run reste étiqueté « parité de plan » : c'est la campagne à laquelle il
    # appartient, et il doit se comparer aux baselines de cette campagne.
    assert resultat["regime"] == "plan_parity"


def test_le_plan_transmis_est_celui_de_la_tache_demandee(espion):
    benchmark_runner.run_benchmark(TASK_ID, "crewai", "plan_parity")
    attendu = benchmark_runner.agent_briefing.briefing_for_task(
        TASK_ID, benchmark_runner.TASK_AGENT_ROOT)
    assert espion["briefing"] == attendu


def test_un_regime_inconnu_est_refuse_avant_toute_execution(espion):
    with pytest.raises(ValueError):
        benchmark_runner.run_benchmark(TASK_ID, "langgraph", "parite_totale")
    assert "instructions" not in espion


def test_le_briefing_ne_fuit_pas_d_un_run_au_suivant(espion):
    """Le briefing vit dans un global : sans remise à zéro, une campagne
    « énoncé seul » lancée après une campagne « parité de plan » hériterait
    du plan, et sa mesure serait fausse sans que rien ne le signale."""
    benchmark_runner.run_benchmark(TASK_ID, "langgraph", "plan_parity")
    resultat = benchmark_runner.run_benchmark(TASK_ID, "langgraph", "prompt_only")
    assert espion["briefing"] is None
    assert espion["instructions"] == benchmark_runner.SYSTEM_INSTRUCTIONS
    assert resultat["briefingChars"] == 0

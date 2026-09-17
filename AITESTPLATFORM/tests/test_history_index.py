"""Index de l'historique : le journal reste la source de vérité, l'index n'est
qu'un raccourci. Ces tests vérifient qu'il ne peut jamais mentir sur lui."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import history_index  # noqa: E402


def make_run(index: int, **overrides) -> dict:
    run = {
        "id": f"run_{index:04d}",
        "taskId": "hr.employee_transfer",
        "taskNumber": 1000 + index,
        "taskTitle": "Employee Transfer",
        "domain": "hr",
        "frameworkId": "agent_l",
        "frameworkName": "AGENT-L",
        "model": "gemini-3.1-flash-lite",
        "status": "completed",
        "valid": True,
        "invalidReason": None,
        "success": True,
        "partialCredit": 1.0,
        "taskCompleted": 1.0,
        "toolCallCount": 3,
        "llmCallCount": 4,
        "executionTimeMs": 1234,
        "tokenUsage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
        "protocolVersion": "automationbench-native-prompt-facade-v3.1",
        "createdAt": "2026-08-09T00:00:00Z",
        "error": None,
        "assertions": [{"passed": True}, {"passed": False}, {"excluded": True}],
        "failedAssertions": [{"passed": False}],
        "toolCalls": [{"index": 1, "tool": "lire"}],
        "finalState": {"gros": "objet"},
    }
    run.update(overrides)
    return run


@pytest.fixture()
def journal(tmp_path: Path) -> Path:
    return tmp_path / "run-history.jsonl"


def test_append_puis_load_conserve_l_ordre_d_ecriture(journal: Path):
    for i in range(5):
        history_index.append(journal, make_run(i))
    entries = history_index.load(journal)
    assert [entry["seq"] for entry in entries] == [0, 1, 2, 3, 4]
    assert [entry["id"] for entry in entries] == [f"run_{i:04d}" for i in range(5)]


def test_projection_ne_fuit_ni_octets_ni_gros_objets(journal: Path):
    history_index.append(journal, make_run(0))
    row = history_index.project(history_index.load(journal)[0])
    for interdit in ("offset", "length", "end", "finalState", "assertions", "toolCalls"):
        assert interdit not in row
    assert row["totalTokens"] == 15
    assert row["assertionsTotal"] == 3
    assert row["assertionsExcluded"] == 1
    assert row["assertionsEvaluated"] == 2
    assert row["assertionsFailed"] == 1


def test_metrique_absente_reste_none_et_jamais_zero(journal: Path):
    history_index.append(journal, make_run(0, tokenUsage={"totalTokens": None}, llmCallCount=None))
    row = history_index.project(history_index.load(journal)[0])
    assert row["totalTokens"] is None
    assert row["llmCallCount"] is None


def test_index_absent_est_reconstruit(journal: Path):
    for i in range(3):
        history_index.append(journal, make_run(i))
    history_index.index_path(journal).unlink()
    entries = history_index.load(journal)
    assert len(entries) == 3
    assert history_index.index_path(journal).exists()


def test_index_corrompu_est_reconstruit(journal: Path):
    for i in range(3):
        history_index.append(journal, make_run(i))
    history_index.index_path(journal).write_text("ceci n'est pas du JSON\n", encoding="utf-8")
    entries = history_index.load(journal)
    assert [entry["seq"] for entry in entries] == [0, 1, 2]


def test_index_desynchronise_par_ecriture_externe_est_rattrape(journal: Path):
    """Un run ajouté par un autre processus, sans passer par l'index."""
    for i in range(2):
        history_index.append(journal, make_run(i))
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(make_run(2), ensure_ascii=False) + "\n")
    entries = history_index.load(journal)
    assert len(entries) == 3
    assert entries[2]["id"] == "run_0002"
    # Et l'entrée rattrapée doit pointer sur le bon octet.
    assert history_index.read_record(journal, entries[2])["id"] == "run_0002"


def test_journal_tronque_declenche_une_reconstruction_totale(journal: Path):
    for i in range(4):
        history_index.append(journal, make_run(i))
    lignes = journal.read_text(encoding="utf-8").splitlines(keepends=True)
    journal.write_text("".join(lignes[:2]), encoding="utf-8")
    entries = history_index.load(journal)
    assert [entry["seq"] for entry in entries] == [0, 1]


def test_read_record_rend_l_objet_complet_par_seek(journal: Path):
    history_index.append(journal, make_run(0))
    history_index.append(journal, make_run(1, finalState={"marqueur": "second"}))
    entries = history_index.load(journal)
    assert history_index.read_record(journal, entries[1])["finalState"] == {"marqueur": "second"}


def test_ligne_illisible_n_invalide_pas_les_suivantes(journal: Path):
    history_index.append(journal, make_run(0))
    with journal.open("a", encoding="utf-8") as handle:
        handle.write("{ ceci est cassé\n")
        handle.write(json.dumps(make_run(2), ensure_ascii=False) + "\n")
    entries = history_index.load(journal)
    assert [entry["id"] for entry in entries] == ["run_0000", "run_0002"]


def test_find_et_filtres(journal: Path):
    history_index.append(journal, make_run(0, frameworkId="agent_l", taskId="hr.a"))
    history_index.append(journal, make_run(1, frameworkId="crewai", taskId="hr.b"))
    entries = history_index.load(journal)
    assert history_index.find(entries, "run_0001")["frameworkId"] == "crewai"
    assert history_index.find(entries, "inconnu") is None
    assert len(history_index.filtered(entries, framework="crewai")) == 1
    assert len(history_index.filtered(entries, task="hr.a")) == 1
    assert len(history_index.filtered(entries, framework="crewai", task="hr.a")) == 0


def test_run_legacy_sans_protocole_est_etiquete(journal: Path):
    run = make_run(0)
    del run["protocolVersion"]
    history_index.append(journal, run)
    row = history_index.project(history_index.load(journal)[0])
    assert row["protocolVersion"] == "legacy-v1"


def test_count_est_coherent_avec_load(journal: Path):
    for i in range(7):
        history_index.append(journal, make_run(i))
    assert history_index.count(journal) == len(history_index.load(journal)) == 7


def test_journal_absent_ne_leve_pas(tmp_path: Path):
    assert history_index.load(tmp_path / "rien.jsonl") == []
    assert history_index.count(tmp_path / "rien.jsonl") == 0

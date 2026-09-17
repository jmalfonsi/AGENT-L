"""Contrat du banc adversarial appliqué à l'agent généré de cave.

Le rapport global reste volontairement rouge tant que les trois exigences de
reprise durable ne sont pas implémentées. La CI vérifie à la fois que les
barrières critiques restent vertes et que ces dettes ne disparaissent pas du
rapport par affaiblissement du protocole.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adversarial_protocol as protocol


def _by_id():
    return {item.id: item for item in protocol.run_protocol(seed=20260821)}


def test_protocol_covers_every_required_threat_family():
    results = _by_id()
    families = {item.family for item in results.values()}

    assert {
        "preuves", "nominal", "prompt_injection", "output_smuggling",
        "confidentialite", "provenance", "oracle", "donnees", "capteur",
        "coherence", "identite", "fuzz", "outil", "ambiguite", "audit",
        "contrat_outil", "bus", "approbation", "politique", "idempotence",
        "rejeu",
    } <= families
    assert len(results) >= 32


def test_all_critical_containment_gates_pass():
    results = _by_id()
    critical = {
        "STATIC-01", "BASE-01", "BASE-02",
        "REPLAY-01",
        "INJ-01", "INJ-02", "INJ-03", "INJ-04", "INJ-05",
        "LLM-01", "LLM-02", "LLM-03", "LLM-04",
        "DATA-01", "DATA-02", "DATA-03", "DATA-04", "DATA-05",
        "TOOL-03", "TOOL-05", "TOOL-06", "TOOL-07",
        "APP-01", "APP-02", "POL-01", "IDEM-01",
    }

    failed = {probe_id: results[probe_id].payload()
              for probe_id in critical if not results[probe_id].passed}
    assert not failed


def test_certification_refuses_the_three_known_recovery_gaps():
    results = list(_by_id().values())
    report = protocol.summary(results, seed=20260821)

    assert report["certified"] is False
    assert set(report["failedProbeIds"]) == {"TOOL-01", "TOOL-02", "TOOL-04"}


def test_report_is_deterministic_for_the_certification_seed():
    first = protocol.summary(protocol.run_protocol(seed=20260821), seed=20260821)
    second = protocol.summary(protocol.run_protocol(seed=20260821), seed=20260821)

    assert first == second

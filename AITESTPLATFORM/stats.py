"""Agrégats sur l'historique complet, à partir des lignes légères de l'index.

Deux règles portent tout le module et viennent du contrat de preuve de la
plateforme :

1. une exécution invalide (erreur de framework, sortie vide, plafond de tokens)
   est exclue des taux et des moyennes — elle mesure le transport, pas l'agent ;
2. une moyenne sur un ensemble vide vaut `None` : « N/D » n'est pas « 0 ».

S'y ajoute le refus de mélanger ce qui ne se compare pas. Deux partitions le
garantissent, dans cet ordre :

* le **régime** — `prompt_only` mesure la capacité à trouver ET exécuter la
  marche à suivre depuis l'énoncé seul, `plan_parity` donne le plan aux trois
  baselines et n'évalue plus que l'exécution. Moyenner les deux ne veut rien
  dire ; le régime est donc choisi avant tout le reste, et jamais déduit ;
* la **version de protocole**, à l'intérieur du régime retenu : comparer un run
  `legacy-v1` à un run `…-v3.1` produit un classement faux, puisque ni la
  surface d'outils ni le prompt ne sont les mêmes.
"""
from __future__ import annotations

import re
import time
from typing import Any, Iterable, Optional

ALL_PROTOCOLS = "all"
ALL_REGIMES = "all"
# Régime des runs écrits avant son introduction : aucun plan ne leur a été transmis.
DEFAULT_REGIME = "prompt_only"


def row_regime(row: dict) -> str:
    return str(row.get("regime") or DEFAULT_REGIME)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def protocol_rank(version: str) -> tuple:
    """Ordonne `legacy-v1` < `…-v2` < `…-v3.1` sur le numéro de version porté."""
    numbers = re.findall(r"v(\d+(?:\.\d+)*)", str(version or ""), re.IGNORECASE)
    parsed = tuple(int(part) for part in numbers[-1].split(".")) if numbers else ()
    return (parsed, str(version or ""))


def is_valid(row: dict) -> bool:
    """Un run compte dans les moyennes s'il est techniquement valide."""
    return bool(row.get("valid")) and row.get("status") != "invalid"


def _numbers(rows: Iterable[dict], field: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values.append(float(value))
    return values


def average(rows: Iterable[dict], field: str) -> Optional[float]:
    values = _numbers(rows, field)
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def _parse_time(value: Any) -> Optional[str]:
    """Normalise un instant ISO en clé comparable. Un format inconnu est ignoré."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text[10:]:
        text = text[:10] + text[10:].split("+", 1)[0]
    return text


def select_rows(rows: Iterable[dict], *, protocol: Optional[str] = None,
                task: Optional[str] = None, framework: Optional[str] = None,
                since: Optional[str] = None,
                regime: Optional[str] = None) -> tuple[list[dict], list[str], list[str]]:
    """Applique les filtres, puis tranche régime et version de protocole.

    Renvoie (lignes retenues, versions présentes triées, versions écartées).
    Le régime est tranché **avant** la version : sans cela, un régime récent
    imposerait sa version de protocole à l'autre et le viderait de ses runs.
    """
    rows = list(rows)
    if regime != ALL_REGIMES:
        wanted = regime or DEFAULT_REGIME
        rows = [row for row in rows if row_regime(row) == wanted]

    kept = []
    for row in rows:
        if task and row.get("taskId") != task:
            continue
        if framework and row.get("frameworkId") != framework:
            continue
        if since:
            floor = _parse_time(since)
            created = _parse_time(row.get("createdAt"))
            if floor and (created is None or created < floor):
                continue
        kept.append(row)

    present = sorted({str(row.get("protocolVersion") or "unknown") for row in kept}, key=protocol_rank)
    excluded: list[str] = []
    if protocol and protocol != ALL_PROTOCOLS:
        kept = [row for row in kept if str(row.get("protocolVersion") or "unknown") == protocol]
        excluded = [version for version in present if version != protocol]
    elif not protocol and len(present) > 1:
        newest = present[-1]
        kept = [row for row in kept if str(row.get("protocolVersion") or "unknown") == newest]
        excluded = [version for version in present if version != newest]
    return kept, present, excluded


def _framework_block(framework_id: str, rows: list[dict]) -> dict:
    valid = [row for row in rows if is_valid(row)]
    successes = [row for row in valid if row.get("success") is True]
    name = next((row.get("frameworkName") for row in rows if row.get("frameworkName")), framework_id)
    return {
        "frameworkId": framework_id,
        "frameworkName": name,
        "totalRuns": len(rows),
        "validRuns": len(valid),
        "invalidRuns": sum(1 for row in rows if row.get("status") == "invalid"),
        "failedRuns": sum(1 for row in rows if row.get("status") == "failed"),
        "successRuns": len(successes),
        "officialSuccessRate": _rate(len(successes), len(valid)),
        "averagePartialCredit": average(valid, "partialCredit"),
        "averageToolCalls": average(valid, "toolCallCount"),
        "averageLlmCalls": average(valid, "llmCallCount"),
        "averageTimeMs": average(valid, "executionTimeMs"),
        "averageTotalTokens": average(valid, "totalTokens"),
        "taskCount": len({row.get("taskId") for row in rows if row.get("taskId")}),
    }


def _cell(rows: list[dict]) -> dict:
    valid = [row for row in rows if is_valid(row)]
    successes = sum(1 for row in valid if row.get("success") is True)
    return {
        "runs": len(rows),
        "validRuns": len(valid),
        "averagePartialCredit": average(valid, "partialCredit"),
        "successRate": _rate(successes, len(valid)),
        "averageToolCalls": average(valid, "toolCallCount"),
        "averageTimeMs": average(valid, "executionTimeMs"),
    }


def compute(rows: Iterable[dict], *, protocol: Optional[str] = None,
            task: Optional[str] = None, framework: Optional[str] = None,
            since: Optional[str] = None, regime: Optional[str] = None) -> dict:
    rows = list(rows)
    # Comptés sur l'historique entier, avant tout filtre : l'interface doit
    # pouvoir proposer un régime même quand le régime courant est vide.
    regime_counts: dict[str, int] = {}
    for row in rows:
        regime_counts[row_regime(row)] = regime_counts.get(row_regime(row), 0) + 1

    kept, present, excluded = select_rows(
        rows, protocol=protocol, task=task, framework=framework, since=since, regime=regime)

    by_framework: dict[str, list[dict]] = {}
    by_task: dict[str, list[dict]] = {}
    for row in kept:
        by_framework.setdefault(str(row.get("frameworkId") or "unknown"), []).append(row)
        by_task.setdefault(str(row.get("taskId") or "unknown"), []).append(row)

    matrix = []
    for task_id in sorted(by_task, key=lambda key: (by_task[key][0].get("taskNumber") or 0, key)):
        task_rows = by_task[task_id]
        cells = {}
        for framework_id in sorted({str(row.get("frameworkId") or "unknown") for row in task_rows}):
            cells[framework_id] = _cell([row for row in task_rows
                                         if str(row.get("frameworkId") or "unknown") == framework_id])
        matrix.append({
            "taskId": task_id,
            "taskNumber": next((row.get("taskNumber") for row in task_rows if row.get("taskNumber")), None),
            "taskTitle": next((row.get("taskTitle") for row in task_rows if row.get("taskTitle")), None),
            "cells": cells,
        })

    return {
        "generatedAt": _iso_now(),
        "filters": {"protocol": protocol, "task": task, "framework": framework,
                    "since": since, "regime": regime or DEFAULT_REGIME},
        "regime": (regime or DEFAULT_REGIME),
        "regimeCounts": regime_counts,
        "totalRuns": len(kept),
        "validRuns": sum(1 for row in kept if is_valid(row)),
        "invalidRuns": sum(1 for row in kept if row.get("status") == "invalid"),
        "failedRuns": sum(1 for row in kept if row.get("status") == "failed"),
        "protocolVersions": present,
        "excludedProtocolVersions": excluded,
        "taskCount": len(by_task),
        "frameworkCount": len(by_framework),
        "byFramework": [_framework_block(key, by_framework[key]) for key in sorted(by_framework)],
        "matrix": matrix,
    }

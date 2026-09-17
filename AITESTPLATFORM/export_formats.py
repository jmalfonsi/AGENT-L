"""Export texte de l'historique et des agrégats.

Le CLI ne renvoie qu'un objet JSON : l'export voyage donc comme un document
complet (`content`) avec son nom de fichier et son type, à charge de l'appelant
de le proposer au téléchargement. Une valeur absente donne une cellule vide,
jamais « 0 » ni « None » : un tableur ne doit pas transformer un « N/D » en
mesure.
"""
from __future__ import annotations

import csv
import io
import json
import time
from typing import Any, Iterable, Optional

from history_index import LIGHT_FIELDS

STATS_COLUMNS = (
    "frameworkId", "frameworkName", "totalRuns", "validRuns", "invalidRuns",
    "failedRuns", "successRuns", "officialSuccessRate", "averagePartialCredit",
    "averageToolCalls", "averageLlmCalls", "averageTimeMs", "averageTotalTokens",
    "taskCount",
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csv(columns: Iterable[str], rows: Iterable[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    columns = list(columns)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(column)) for column in columns])
    return buffer.getvalue()


def filename(scope: str, extension: str, moment: Optional[str] = None) -> str:
    day = moment or time.strftime("%Y-%m-%d", time.gmtime())
    return f"aitestplatform-{scope}-{day}.{extension}"


def export(scope: str, fmt: str, *, rows: list[dict], summary: dict) -> dict:
    if scope not in {"runs", "stats"}:
        raise ValueError(f"Portée d'export inconnue: {scope}")
    if fmt not in {"csv", "json"}:
        raise ValueError(f"Format d'export inconnu: {fmt}")

    if fmt == "json":
        payload = {"runs": rows} if scope == "runs" else summary
        return {
            "filename": filename(scope, "json"),
            "contentType": "application/json",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        }

    # En CSV, le tableau de synthèse est celui par framework : la matrice
    # tâche × framework n'a pas de forme tabulaire stable.
    content = (_csv(LIGHT_FIELDS, rows) if scope == "runs"
               else _csv(STATS_COLUMNS, summary.get("byFramework", [])))
    return {
        "filename": filename(scope, "csv"),
        "contentType": "text/csv",
        "content": content,
    }

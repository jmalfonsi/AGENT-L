"""Persistance des campagnes.

Une campagne regroupe les exécutions lancées ensemble. Tant qu'elle ne vivait
qu'en mémoire du navigateur, un rechargement de page effaçait le lien entre des
runs pourtant bien enregistrés. Chaque campagne devient ici un fichier JSON.

Deux précautions : l'écriture passe par un fichier temporaire puis `os.replace`
(une campagne à moitié écrite serait relue comme une campagne tronquée), et
`campaignId` est validé avant tout accès disque puisqu'il sert de nom de
fichier.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

CAMPAIGN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")

FIELDS = (
    "campaignId", "name", "createdAt", "updatedAt", "completedAt", "status",
    "kind", "taskIds", "frameworkIds", "runIds", "matrixSummary",
    "protocolVersion", "executionMode", "scorer", "error", "projectId", "regime",
)

SUMMARY_FIELDS = (
    "campaignId", "name", "createdAt", "completedAt", "status", "kind",
    "taskIds", "frameworkIds", "runCount", "projectId", "regime",
)

STATUSES = {"running", "completed", "error", "cancelled"}
KINDS = {"benchmark", "factory"}
# Une campagne enregistrée sans régime a été exécutée avant leur introduction :
# c'était forcément « énoncé seul ». Une valeur inconnue est refusée, sans quoi
# une campagne s'afficherait sous un régime qui n'est pas le sien.
REGIMES = {"prompt_only", "plan_parity"}


def validate_id(campaign_id: Any) -> str:
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_PATTERN.match(campaign_id):
        raise ValueError(f"campaignId invalide: {campaign_id!r}")
    return campaign_id


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def campaign_path(root: Path, campaign_id: str) -> Path:
    return Path(root) / "campaigns" / f"{validate_id(campaign_id)}.json"


def _blank(campaign_id: str) -> dict:
    record = {field: None for field in FIELDS}
    record.update({
        "campaignId": campaign_id, "status": "running", "kind": "benchmark",
        "taskIds": [], "frameworkIds": [], "runIds": [], "matrixSummary": {},
    })
    return record


def load(root: Path, campaign_id: str) -> Optional[dict]:
    path = campaign_path(root, campaign_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def save(root: Path, record: dict) -> dict:
    """Fusionne avec l'existant : un appelant partiel ne doit rien effacer."""
    if not isinstance(record, dict):
        raise ValueError("L'enregistrement de campagne doit être un objet JSON.")
    campaign_id = validate_id(record.get("campaignId"))
    merged = load(root, campaign_id) or _blank(campaign_id)
    # Fusion par clé présente : un `null` explicite efface, une clé omise garde.
    merged.update(record)
    merged["campaignId"] = campaign_id
    if not merged.get("createdAt"):
        merged["createdAt"] = _now()
    merged["updatedAt"] = _now()
    if merged.get("status") not in STATUSES:
        raise ValueError(f"Statut de campagne inconnu: {merged.get('status')!r}")
    if merged.get("kind") not in KINDS:
        raise ValueError(f"Type de campagne inconnu: {merged.get('kind')!r}")
    if not merged.get("regime"):
        merged["regime"] = "prompt_only"
    if merged["regime"] not in REGIMES:
        raise ValueError(f"Régime de comparaison inconnu: {merged['regime']!r}")
    for key in ("taskIds", "frameworkIds", "runIds"):
        if not isinstance(merged.get(key), list):
            merged[key] = []

    path = campaign_path(root, campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return merged


def summary(record: dict) -> dict:
    run_ids = record.get("runIds") or []
    row = {field: record.get(field) for field in SUMMARY_FIELDS}
    row["runCount"] = len(run_ids) if isinstance(run_ids, list) else 0
    return row


def page(root: Path, limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    directory = Path(root) / "campaigns"
    records = []
    if directory.exists():
        for path in directory.glob("*.json"):
            if not CAMPAIGN_ID_PATTERN.match(path.stem):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                records.append(record)
    records.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    total = len(records)
    rows = [summary(record) for record in records[offset:offset + limit]]
    return {"campaigns": rows, "total": total, "limit": limit, "offset": offset}

"""Index sur disque du journal d'exécutions.

Le journal `data/run-history.jsonl` porte l'objet complet de chaque run (état
initial, état final, assertions, appels d'outils : ~29 Ko par ligne). Le relire
et le renormaliser entièrement pour afficher une page ou compter les runs coûte
un temps qui croît avec l'historique. Cet index garde, pour chaque run, sa
position en octets dans le journal et les seuls champs légers dont les listes
ont besoin : une page se sert par `seek`, un compteur se lit sans ouvrir le
journal.

L'index est un cache, jamais une source de vérité : le journal reste le seul
enregistrement. Toute incohérence détectée déclenche une reconstruction
silencieuse plutôt qu'une erreur remontée à l'appelant.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Optional

from campaign_integrity import normalize_history_run

# Ordre de colonnes contractuel : le frontend et l'export CSV s'y appuient.
LIGHT_FIELDS = (
    "id", "seq", "campaignId", "taskId", "taskNumber", "taskTitle", "domain",
    "frameworkId", "frameworkName", "model", "status", "valid", "invalidReason",
    "success", "partialCredit", "taskCompleted", "toolCallCount", "llmCallCount",
    "executionTimeMs", "totalTokens", "protocolVersion", "createdAt", "error",
    "assertionsTotal", "assertionsExcluded", "assertionsEvaluated", "assertionsFailed",
    "regime", "briefingChars",
)

# Régime des runs écrits avant l'introduction du champ : ils ont tous été
# exécutés sans transmission du plan aux baselines.
DEFAULT_REGIME = "prompt_only"

# Champs de position, présents dans le fichier d'index mais jamais renvoyés.
_POSITION_FIELDS = ("offset", "length", "end")


def index_path(journal: Path) -> Path:
    journal = Path(journal)
    return journal.with_name(journal.stem + ".index.jsonl")


def light_row(run: dict, seq: int) -> dict:
    """Projection légère d'un run, normalisation `legacy-v1` comprise.

    Une métrique absente vaut `None` et jamais `0` : « N/D » n'est pas « 0 ».
    """
    normalized = normalize_history_run(run) if isinstance(run, dict) else {}
    assertions = normalized.get("assertions")
    if isinstance(assertions, list):
        total = len(assertions)
        excluded = sum(1 for item in assertions if isinstance(item, dict) and item.get("excluded"))
        evaluated: Optional[int] = total - excluded
    else:
        total = excluded = evaluated = None  # type: ignore[assignment]
    failed_assertions = normalized.get("failedAssertions")
    usage = normalized.get("tokenUsage")
    row = {field: normalized.get(field) for field in LIGHT_FIELDS}
    row["seq"] = seq
    row["totalTokens"] = usage.get("totalTokens") if isinstance(usage, dict) else None
    row["assertionsTotal"] = total
    row["assertionsExcluded"] = excluded
    row["assertionsEvaluated"] = evaluated
    row["assertionsFailed"] = len(failed_assertions) if isinstance(failed_assertions, list) else None
    return row


def project(entry: dict) -> dict:
    """Retire les champs de position : une ligne d'historique n'expose pas d'octets."""
    return {field: entry.get(field) for field in LIGHT_FIELDS}


def _scan(handle: BinaryIO, start_offset: int, start_seq: int) -> list[dict]:
    entries: list[dict] = []
    handle.seek(start_offset)
    offset = start_offset
    seq = start_seq
    for raw in handle:
        length = len(raw)
        offset_line = offset
        offset += length
        text = raw.strip()
        if not text:
            continue
        try:
            record = json.loads(text.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # Ligne illisible : on l'ignore sans casser l'index des suivantes.
            continue
        if not isinstance(record, dict):
            continue
        entry = light_row(record, seq)
        entry["offset"] = offset_line
        entry["length"] = length
        entry["end"] = offset
        entries.append(entry)
        seq += 1
    return entries


def _read_index(path: Path) -> Optional[list[dict]]:
    if not path.exists():
        return None
    entries: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for position, line in enumerate(handle):
                text = line.strip()
                if not text:
                    continue
                entry = json.loads(text)
                if not isinstance(entry, dict) or entry.get("seq") != position:
                    return None
                if not all(isinstance(entry.get(field), int) for field in _POSITION_FIELDS):
                    return None
                if entries and entry["offset"] < entries[-1]["end"]:
                    return None
                entries.append(entry)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return entries


def _write_index(path: Path, entries: Iterable[dict]) -> None:
    """Écriture atomique : un index tronqué serait pris pour un index à jour."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(handle.name)
        raise


def _append_index(path: Path, entries: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        handle.flush()


def _refresh(journal: Path, handle: BinaryIO) -> list[dict]:
    """Aligne l'index sur le journal. L'appelant détient déjà le verrou fichier."""
    path = index_path(journal)
    size = os.fstat(handle.fileno()).st_size
    entries = _read_index(path)
    if entries is not None:
        covered = entries[-1]["end"] if entries else 0
        if covered == size:
            return entries
        if covered < size:
            added = _scan(handle, covered, len(entries))
            if added:
                _append_index(path, added)
                entries.extend(added)
            return entries
    # Index absent, corrompu, ou journal réécrit/tronqué : reconstruction totale.
    entries = _scan(handle, 0, 0)
    _write_index(path, entries)
    return entries


def load(journal: Path) -> list[dict]:
    """Index à jour du journal, dans l'ordre d'écriture (seq croissant)."""
    journal = Path(journal)
    if not journal.exists():
        return []
    with journal.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            return _refresh(journal, handle)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def count(journal: Path) -> int:
    return len(load(journal))


def append(journal: Path, record: dict) -> dict:
    """Ajoute un run au journal et son entrée d'index, sous le même verrou.

    L'index est réaligné avant l'écriture : sans cela une entrée ajoutée à un
    index périmé décrirait le mauvais offset.
    """
    journal = Path(journal)
    journal.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with journal.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            entries = _refresh(journal, handle)
            handle.seek(0, os.SEEK_END)
            offset = handle.tell()
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            entry = light_row(record, len(entries))
            entry["offset"] = offset
            entry["length"] = len(payload)
            entry["end"] = offset + len(payload)
            _append_index(index_path(journal), [entry])
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return entry


def read_record(journal: Path, entry: dict) -> dict:
    """Objet complet d'un run : un seek et une lecture, jamais un balayage."""
    journal = Path(journal)
    with journal.open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            handle.seek(int(entry["offset"]))
            raw = handle.read(int(entry["length"]))
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return json.loads(raw.decode("utf-8"))


def find(entries: Iterable[dict], run_id: str) -> Optional[dict]:
    for entry in entries:
        if entry.get("id") == run_id:
            return entry
    return None


def matches(entry: dict, *, framework: Any = None, task: Any = None,
            protocol: Any = None, status: Any = None, regime: Any = None) -> bool:
    if framework and entry.get("frameworkId") != framework:
        return False
    if task and entry.get("taskId") != task:
        return False
    if protocol and entry.get("protocolVersion") != protocol:
        return False
    if regime and (entry.get("regime") or DEFAULT_REGIME) != regime:
        return False
    if status and entry.get("status") != status:
        return False
    return True


def filtered(entries: Iterable[dict], **filters: Any) -> list[dict]:
    return [entry for entry in entries if matches(entry, **filters)]

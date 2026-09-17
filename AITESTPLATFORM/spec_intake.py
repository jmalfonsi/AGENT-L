"""Ingestion d'un cahier des charges : texte brut ou PDF.

Ce module ne juge pas le contenu, il le rend lisible et traçable. L'extraction
PDF est faite par `pdfminer.six`, jamais par un modèle : un cahier des charges
résumé par un LLM avant analyse est un cahier des charges qu'on ne peut plus
opposer à l'agent produit.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

MAX_CHARS = 120_000
SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


class SpecError(ValueError):
    """Cahier des charges inutilisable en l'état."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 0x20)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(data: bytes) -> str:
    """Texte d'un PDF, sans OCR.

    Un PDF scanné rend une chaîne vide ou quasi vide : on le signale au lieu de
    laisser l'analyse conclure « cahier des charges incomplet » sur un problème
    de format.
    """
    import io

    from pdfminer.high_level import extract_text

    try:
        text = extract_text(io.BytesIO(data)) or ""
    except Exception as exc:  # pdfminer remonte des exceptions très variées
        raise SpecError(f"PDF illisible : {type(exc).__name__}: {exc}") from exc
    if len(text.strip()) < 40:
        raise SpecError(
            "Aucun texte extractible de ce PDF (document scanné ou protégé). "
            "Fournir un PDF texte ou coller le contenu directement.")
    return text


def load_spec(*, text: str | None = None, path: str | None = None) -> dict:
    """Rend le cahier des charges normalisé et son empreinte.

    L'empreinte porte sur le texte normalisé : deux dépôts du même document
    produisent le même `specHash`, ce qui permet de rattacher une génération à
    l'énoncé exact qui l'a produite.
    """
    if (text is None) == (path is None):
        raise SpecError("Fournir soit un texte, soit un fichier, pas les deux.")

    if path is not None:
        source = Path(path)
        if not source.is_file():
            raise SpecError(f"Fichier introuvable : {source}")
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise SpecError(
                f"Format non pris en charge : {suffix or 'sans extension'}. "
                f"Attendu : {', '.join(sorted(SUPPORTED_SUFFIXES))}.")
        data = source.read_bytes()
        raw = extract_pdf(data) if suffix == ".pdf" else data.decode("utf-8", "replace")
        origin = {"kind": "file", "name": source.name, "bytes": len(data)}
    else:
        raw = text
        origin = {"kind": "text", "name": "saisie directe", "bytes": len(raw.encode("utf-8"))}

    normalized = _normalize(raw)
    if len(normalized) < 80:
        raise SpecError(
            "Cahier des charges trop court pour être analysé (moins de 80 "
            "caractères utiles). Décrire au minimum l'objectif, le "
            "déclencheur et les actions attendues.")

    truncated = len(normalized) > MAX_CHARS
    if truncated:
        normalized = normalized[:MAX_CHARS]

    return {
        "text": normalized,
        "specHash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "charCount": len(normalized),
        "truncated": truncated,
        "origin": origin,
    }

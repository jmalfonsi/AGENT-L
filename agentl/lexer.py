"""Analyseur lexical d'AGENT-L.

Règle de conception : **les mots réservés sont en MAJUSCULES uniquement**.
Tout identifiant en minuscules (`healthy`, `logs`, `none`) reste un identifiant
ou un symbole. Cela évite toute collision entre la syntaxe et le vocabulaire
métier de l'agent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

from .core import LexError

KEYWORDS = {
    # structure
    "AGENT", "VERSION", "DESCRIPTION",
    # objectifs
    "GOAL", "MAINTAIN", "ACHIEVE", "TARGET", "WEIGHT",
    # croyances
    "BELIEF", "CONFIDENCE", "SOURCE", "UPDATED",
    # perception
    "OBSERVE", "EVERY", "WHEN",
    # mémoire
    "MEMORY", "SHORT_TERM", "LONG_TERM", "KNOWLEDGE", "WRITE", "STORE", "INTO",
    # outils
    "TOOL", "INPUT", "OUTPUT", "SIDE_EFFECT", "RISK",
    # politiques
    "POLICY", "NEVER", "ALLOW", "DENY", "REQUIRE", "APPROVAL", "FOR", "DEFAULT",
    # v1.8 : interdiction de sortie vers le fournisseur de modèle
    "SEND",
    # v1.8 : conduite déclarée face à un capteur muet
    "UNKNOWN", "DEGRADE",
    # plans / contrôle
    "PLAN", "STEP", "IF", "THEN", "ELSE", "SET", "LOOP", "UNTIL", "MAX",
    # v1.3 : parcours d'une collection perçue
    "FOREACH",
    # v1.4 : critères d'acceptation portés par le programme
    "SCENARIO", "GIVEN", "WITHIN",
    # vérification
    "VERIFY", "CONDITION", "ON", "FAIL", "RETRY", "ROLLBACK", "ESCALATE",
    # raisonnement LLM
    "REASON", "TASK", "USING", "PRODUCE",
    # humain dans la boucle
    "ASK", "QUESTION", "TIMEOUT",
    # multi-agents
    "DELEGATE", "EXPECT",
    # v0.6 : messagerie et mémoire partagée
    "MESSAGE", "TO", "BROADCAST", "PAYLOAD", "SHARED", "RECEIVE",
    "DURATION", "DEADLINE", "TIME_WEIGHT",
    # réactivité
    "EVENT", "DECIDE", "RULES",
    # phases de boucle
    "UPDATE_BELIEFS", "EVALUATE_GOALS", "SELECT_PLAN", "EXECUTE",
    "UPDATE_MEMORY", "ACT",
    # inférence bayésienne (v0.4)
    "HYPOTHESIS", "PREDICT", "EVIDENCE", "PRIOR", "LIKELIHOOD", "GIVEN_NOT",
    "THRESHOLD", "EXPLAINS", "UPDATE_HYPOTHESES",
    # v1.1 : calibration — corrélations déclarées et bornes de sortie
    "GROUP", "MAX_EVIDENCE", "IN",
    # v1.2 : mémoire lisible
    "FROM", "WHERE",
    # planification (v0.5)
    "PLANNER", "ENABLE", "MAX_DEPTH", "MAX_NODES", "APPROVAL_COST",
    "EFFECT", "REQUIRES", "COST", "BIND", "ATTESTS", "SYNTHESIZE",
    # v0.7 : effets probabilistes et utilité espérée
    "OUTCOME", "WITH", "UTILITY", "VALUE", "TARGET_CONFIDENCE",
    # v1.7 : un effet qui ne porte pas sur le monde, donc non réfutable
    "INTERNAL",
    # opérateurs logiques
    "AND", "OR", "NOT", "IN",
}

# Phases utilisables nues dans un bloc LOOP
PHASES = {
    "OBSERVE", "UPDATE_BELIEFS", "EVALUATE_GOALS", "SELECT_PLAN",
    "EXECUTE", "ACT", "VERIFY", "UPDATE_MEMORY", "DECIDE",
    "UPDATE_HYPOTHESES", "SYNTHESIZE", "RECEIVE",
}

OPERATORS = [
    "==", "!=", ">=", "<=", "->", "=>",
    "{", "}", "(", ")", "[", "]",
    ",", ":", ".", "=", ">", "<", "+", "-", "*", "/",
]

_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "sec": 1.0, "min": 60.0,
                   "m": 60.0, "h": 3600.0, "d": 86400.0}

_RE_WS = re.compile(r"[ \t\r\n]+")
_RE_COMMENT = re.compile(r"(#|//)[^\n]*")
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RE_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?")
_RE_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_RE_UNIT = re.compile(r"(ms|sec|min|[smhd])\b")
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_RE_STRING = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')


@dataclass
class Token:
    kind: str          # KW IDENT NUM PCT DUR STR DATETIME OP EOF
    value: Any
    line: int
    col: int

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"{self.kind}({self.value!r})@{self.line}:{self.col}"


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(raw: str) -> str:
    """Déséchappement sûr : préserve l'UTF-8 (contrairement à unicode_escape)."""
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            out.append(_ESCAPES.get(raw[i + 1], "\\" + raw[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokenize(src: str) -> List[Token]:
    toks: List[Token] = []
    i, line, line_start = 0, 1, 0
    n = len(src)

    def col() -> int:
        return i - line_start + 1

    while i < n:
        ch = src[i]

        if ch == "\n":
            i += 1
            line += 1
            line_start = i
            continue

        m = _RE_WS.match(src, i)
        if m:
            chunk = m.group(0)
            line += chunk.count("\n")
            if "\n" in chunk:
                line_start = i + chunk.rindex("\n") + 1
            i = m.end()
            continue

        m = _RE_BLOCK_COMMENT.match(src, i)
        if m:
            chunk = m.group(0)
            line += chunk.count("\n")
            if "\n" in chunk:
                line_start = i + chunk.rindex("\n") + 1
            i = m.end()
            continue

        if src.startswith("/*", i):
            raise LexError(
                f"Commentaire de bloc non terminé ligne {line}, colonne {col()}"
            )

        m = _RE_COMMENT.match(src, i)
        if m:
            i = m.end()
            continue

        m = _RE_STRING.match(src, i)
        if m:
            raw = m.group(1)
            toks.append(Token("STR", _unescape(raw), line, col()))
            text = m.group(0)
            line += text.count("\n")
            if "\n" in text:
                line_start = i + text.rindex("\n") + 1
            i = m.end()
            continue

        if ch == '"':
            raise LexError(
                f"Chaîne littérale non terminée ligne {line}, colonne {col()}"
            )

        m = _RE_DATETIME.match(src, i)
        if m:
            toks.append(Token("DATETIME", m.group(0), line, col()))
            i = m.end()
            continue

        m = _RE_NUMBER.match(src, i)
        if m:
            text = m.group(0)
            end = m.end()
            if end < n and src[end] == "%":
                toks.append(Token("PCT", float(text) / 100.0, line, col()))
                i = end + 1
                continue
            um = _RE_UNIT.match(src, end)
            if um:
                toks.append(
                    Token("DUR", float(text) * _DURATION_UNITS[um.group(1)], line, col())
                )
                i = um.end()
                continue
            val: Any = float(text) if "." in text else int(text)
            toks.append(Token("NUM", val, line, col()))
            i = end
            continue

        m = _RE_IDENT.match(src, i)
        if m:
            word = m.group(0)
            kind = "KW" if word in KEYWORDS else "IDENT"
            toks.append(Token(kind, word, line, col()))
            i = m.end()
            continue

        for op in OPERATORS:
            if src.startswith(op, i):
                toks.append(Token("OP", op, line, col()))
                i += len(op)
                break
        else:
            raise LexError(f"Caractère inattendu {ch!r} ligne {line}, colonne {col()}")

    toks.append(Token("EOF", None, line, 1))
    return toks

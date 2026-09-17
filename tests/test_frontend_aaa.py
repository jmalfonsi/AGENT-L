"""Tests de non-régression AAA du FRONTEND LANGAGE d'AGENT-L.

Chaque test cible un défaut réel corrigé dans lexer.py / parser.py :
le parseur et le lexeur ne doivent JAMAIS produire un traceback Python nu
(ValueError, TypeError, IndexError…) sur une entrée malformée — uniquement des
erreurs de syntaxe propres (LexError / ParseError), avec position.

    python -m pytest tests/test_frontend_aaa.py -q
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.core import AgentLError, LexError, ParseError, Symbol
from agentl.lexer import tokenize
from agentl.parser import parse_source


def _assert_clean(src: str):
    """Le parseur lève une AgentLError, jamais un traceback Python nu."""
    try:
        parse_source(src)
    except AgentLError:
        return
    except Exception as exc:  # pragma: no cover - c'est précisément le bug
        raise AssertionError(
            f"traceback Python nu {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError("aucune erreur levée alors qu'une était attendue")


# ---- champs numériques alimentés par un jeton non numérique -----------------
class TestNumericFieldsNeverCrash(unittest.TestCase):
    """Avant correction : ValueError/TypeError de float() — traceback nu."""

    CASES = {
        "belief_confidence": 'AGENT a { BELIEF { x = 1 CONFIDENCE high } }',
        "ask_timeout":       'AGENT a { PLAN p { ASK user { TIMEOUT soon } } }',
        "outcome_with":      'AGENT a { TOOL t { OUTCOME ok WITH maybe { s = 1 } } }',
        "hyp_prior":         'AGENT a { HYPOTHESIS h { PRIOR high } }',
        "hyp_confidence":    'AGENT a { HYPOTHESIS h { CONFIDENCE high } }',
        "hyp_threshold":     'AGENT a { HYPOTHESIS h { THRESHOLD low } }',
        "hyp_max_evidence":  'AGENT a { HYPOTHESIS h { MAX_EVIDENCE lots } }',
        "evidence_lik":      'AGENT a { HYPOTHESIS h { EVIDENCE { x LIKELIHOOD sure } } }',
        "planner_appr_cost": 'AGENT a { PLANNER { APPROVAL_COST dear } }',
        "planner_target":    'AGENT a { PLANNER { TARGET_CONFIDENCE certain } }',
    }

    def test_non_numeric_gives_parse_error(self):
        for name, src in self.CASES.items():
            with self.subTest(field=name):
                with self.assertRaises(ParseError):
                    parse_source(src)

    def test_numeric_at_eof_gives_parse_error_not_typeerror(self):
        # float(None) sur le jeton EOF → TypeError avant correction
        for src in ('AGENT a { BELIEF { x = 1 CONFIDENCE',
                    'AGENT a { TOOL t { OUTCOME ok WITH',
                    'AGENT a { PLANNER { APPROVAL_COST'):
            with self.subTest(src=src):
                _assert_clean(src)


# ---- valeurs numériques légitimes toujours acceptées ------------------------
class TestNumericFieldsStillAccept(unittest.TestCase):
    def test_percent_duration_and_plain_number(self):
        parse_source('AGENT a { BELIEF { x = 1 CONFIDENCE 90% } }')
        parse_source('AGENT a { HYPOTHESIS h { PRIOR 0.1 THRESHOLD 0.9 } }')
        parse_source('AGENT a { PLAN p { ASK u { TIMEOUT 30s } } }')


# ---- EVERY retiré de la grammaire (v1.8) ------------------------------------
class TestEveryIsRefusedNotMisread(unittest.TestCase):
    """`EVERY` ne cadençait rien et son unité mentait : le lexer rendait `60s`
    en secondes, le runtime les comparait à un compteur de ticks sans durée.

    Le mot reste réservé plutôt que rendu au vocabulaire libre — sinon
    `OBSERVE logs EVERY 10s` se relirait en silence comme deux chemins
    observés, `logs` et `EVERY`, et un programme antérieur tournerait en
    observant un capteur qui n'existe pas."""

    def test_a_program_using_every_is_refused(self):
        _assert_clean('AGENT a { OBSERVE { logs EVERY 10s } }')

    def test_the_refusal_names_the_replacement(self):
        with self.assertRaises(AgentLError) as caught:
            parse_source('AGENT a { OBSERVE { logs EVERY 10s } }')
        self.assertIn("WHEN", str(caught.exception))

    def test_every_is_not_silently_read_as_an_observed_path(self):
        try:
            agent = parse_source('AGENT a { OBSERVE { logs EVERY 10s } }').agents[0]
        except AgentLError:
            return
        self.fail(f"relu en silence : {[o.path for o in agent.observers]}")

    def test_a_guarded_observer_still_parses(self):
        agent = parse_source(
            'AGENT a { OBSERVE { logs WHEN incident.suspected } }').agents[0]
        self.assertEqual(agent.observers[0].path, "logs")
        self.assertIsNotNone(agent.observers[0].when)


# ---- EOF prématuré : plus jamais d'IndexError -------------------------------
class TestPrematureEofNeverIndexError(unittest.TestCase):
    CASES = [
        'AGENT a { VERSION',           # advance() débordait le flux -> IndexError
        'AGENT a {',
        'AGENT a { BELIEF {',
        'AGENT a { PLAN p {',
        'AGENT a { GOAL { MAINTAIN x >',
        'AGENT a { PLAN p { foo(',
        'AGENT a { GOAL { TARGET [',
    ]

    def test_all_give_clean_error(self):
        for src in self.CASES:
            with self.subTest(src=src):
                _assert_clean(src)


# ---- VERSION exige une valeur (ne dévore plus l'accolade fermante) ----------
class TestVersionRequiresValue(unittest.TestCase):
    def test_version_without_value_is_error(self):
        with self.assertRaises(ParseError):
            parse_source('AGENT a { VERSION }')

    def test_version_string_and_number_ok(self):
        self.assertEqual(parse_source('AGENT a { VERSION "1.2" }').agents[0].version, "1.2")
        self.assertEqual(parse_source('AGENT a { VERSION 3 }').agents[0].version, "3")


# ---- domaines de sortie : bornes négatives supportées -----------------------
class TestNegativeDomainBounds(unittest.TestCase):
    def test_negative_range(self):
        src = 'AGENT a { PLAN p { REASON { PRODUCE { s: Number IN [-1, 1] } } } }'
        agent = parse_source(src).agents[0]
        dom = agent.plans[0].steps[0].body[0].domains["s"]
        self.assertEqual(dom.kind, "RANGE")
        self.assertEqual((dom.low, dom.high), (-1.0, 1.0))


# ---- lexeur : chaîne / commentaire de bloc non terminés ---------------------
class TestLexerUnterminated(unittest.TestCase):
    def test_unterminated_string(self):
        with self.assertRaises(LexError) as ctx:
            tokenize('DESCRIPTION "hello')
        self.assertIn("non terminée", str(ctx.exception))

    def test_unterminated_block_comment(self):
        # avant : « /* » silencieusement tokenisé en OP '/' puis OP '*'
        with self.assertRaises(LexError) as ctx:
            tokenize('/* jamais fermé\n AGENT')
        self.assertIn("non terminé", str(ctx.exception))


# ---- lexeur : colonne correcte après une chaîne multi-lignes ----------------
class TestLexerMultilineColumn(unittest.TestCase):
    def test_token_column_after_multiline_string(self):
        # source : "a\nb" x  — ligne 2 = `b" x`, donc x est en ligne 2 colonne 4.
        # Avant correction, line_start n'était pas mis à jour après une chaîne
        # multi-lignes : la colonne rapportée était erronée (≈20).
        src = '"a\nb" x'
        toks = tokenize(src)
        x = next(t for t in toks if t.kind == "IDENT" and t.value == "x")
        self.assertEqual(x.line, 2)
        self.assertEqual(x.col, 4)   # b=1  "=2  espace=3  x=4


# ---- entrée vide / non-programme : erreur propre ----------------------------
class TestEmptyAndJunkInput(unittest.TestCase):
    def test_empty_source(self):
        with self.assertRaises(ParseError):
            parse_source("")

    def test_comment_only(self):
        with self.assertRaises(ParseError):
            parse_source("# juste un commentaire\n")

    def test_junk_toplevel(self):
        with self.assertRaises(ParseError):
            parse_source("foo bar baz")


# ---- l'éditeur dit la même langue que le lexeur -----------------------------
class TestTheEditorMirrorsTheLexer(unittest.TestCase):
    """`RESERVED_KEYWORDS` (extension VS Code) == la langue réelle.

    L'extension refuse tout mot en MAJUSCULES absent de sa liste. Cette liste
    était tenue à la main, et elle a dérivé : restée à v1.5 pendant que le
    langage passait en v1.7, elle signalait comme fautes de syntaxe `INTERNAL`,
    `ATTESTS`, `DEADLINE`, `DURATION`, `TIME_WEIGHT`, `DEGRADE`, `UNKNOWN`,
    `SEND` et `UNSET` — du v1.8 parfaitement valide, jusque dans les exemples
    du dépôt. Un miroir qu'aucun test ne tient finit toujours par mentir.

    L'égalité est exigée dans les deux sens : un mot-clé retiré du langage et
    laissé dans l'éditeur laisserait passer une faute sans un mot.
    """

    #: Constantes que le langage accepte nues sans être des mots-clés du
    #: lexeur. Elles appartiennent au contrat de l'éditeur, pas à la grammaire.
    BARE_CONSTANTS = {"TRUE", "FALSE", "UNDEFINED", "RANGE"}

    EXTENSION = ROOT / "agentl-vscode" / "src" / "extension.ts"

    def _editor_keywords(self) -> set[str]:
        source = self.EXTENSION.read_text(encoding="utf-8")
        block = source.split("const RESERVED_KEYWORDS = new Set([", 1)[1]
        return set(re.findall(r'"([A-Z_0-9]+)"', block.split("])", 1)[0]))

    def test_the_two_lists_are_identical(self):
        from agentl.core import ORDINAL_SCALE
        from agentl.lexer import KEYWORDS

        expected = set(KEYWORDS) | set(ORDINAL_SCALE) | self.BARE_CONSTANTS
        editor = self._editor_keywords()

        self.assertEqual(
            sorted(expected - editor), [],
            "mots-clés du langage absents de l'extension : elle les signalera "
            "comme des fautes de syntaxe")
        self.assertEqual(
            sorted(editor - expected), [],
            "mots réservés par l'extension que le langage ne connaît pas : "
            "elle laissera passer des fautes")

    def test_the_recent_additions_are_present(self):
        """Garde-fou lisible : les mots par lesquels la dérive s'est produite."""
        editor = self._editor_keywords()
        for keyword in ("ATTESTS", "SEND", "DEADLINE", "DURATION", "TIME_WEIGHT",
                        "UNKNOWN", "DEGRADE", "INTERNAL", "UNSET"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, editor)

    def test_every_keyword_is_coloured(self):
        """La coloration syntaxique a dérivé pour la même raison, en plus discret.

        Un mot-clé absent de la grammaire TextMate s'affiche comme un
        identifiant : le fichier reste valide, mais il se lit mal — et rien ne
        le signale, puisqu'aucune erreur n'est produite.
        """
        import json
        from agentl.lexer import KEYWORDS

        grammar = json.loads(
            (ROOT / "agentl-vscode" / "syntaxes" / "agentl.tmLanguage.json")
            .read_text(encoding="utf-8"))
        coloured: set[str] = set()
        for rule in ("keywords", "operators"):
            for pattern in grammar["repository"][rule].get("patterns", []):
                coloured |= set(re.findall(r"[A-Z][A-Z_0-9]+",
                                           pattern.get("match", "")))

        self.assertEqual(sorted(set(KEYWORDS) - coloured), [])


if __name__ == "__main__":
    unittest.main()

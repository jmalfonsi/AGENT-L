"""Tests AAA du contrat d'auteur — ce que les empreintes ne prouvaient pas.

`sync_grammar.py --check` comparait des empreintes SHA-256 : il vérifiait que
les artefacts du skill **suivent** les sources, jamais qu'ils les
**décrivent**. Un mot réservé pouvait donc naître au lexer, vivre dans le
parseur, et n'apparaître dans aucune section du contrat — le contrôle restant
vert, puisque rien n'avait bougé depuis la dernière régénération.

Ce n'est pas une hypothèse : `ALLOW`, `DENY` et `APPROVAL` ont manqué au
contrat depuis l'origine, et `SEND`, `UNKNOWN`, `DEGRADE` l'ont manqué à leur
naissance en v1.8. Deux causes distinctes, testées séparément :

  * l'extracteur ignorait `accept_kw` et les comparaisons à un tuple ;
  * aucune section n'énumérait `POLICY` ni `OBSERVE`, et rien ne s'en
    plaignait.

    python -m pytest tests/test_authoring_contract_aaa.py -q
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agentl.lexer
from agentl.lexer import KEYWORDS

SCRIPT = ROOT / "SKILLS" / "agentl-author" / "scripts" / "sync_grammar.py"
CONTRACT = (ROOT / "SKILLS" / "agentl-author" / "references" / "generated"
            / "grammar-contract.md")


def _load_sync_grammar():
    spec = importlib.util.spec_from_file_location("sync_grammar", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load_sync_grammar()


class EveryReservedWordIsNamed(unittest.TestCase):
    def test_no_keyword_escapes_the_contract(self):
        _, uncovered = sync.keyword_coverage()

        self.assertEqual(uncovered, [])

    def test_the_check_reports_nothing(self):
        self.assertEqual(sync.validate_coverage(), [])

    def test_a_new_keyword_without_a_section_turns_the_check_red(self):
        """Test de mutation : c'est exactement ce qui est arrivé à `SEND`.
        Un mot réservé qu'aucune section ne nomme doit être une erreur, pas
        un silence."""
        agentl.lexer.KEYWORDS = set(KEYWORDS) | {"CHIMERE"}
        try:
            errors = sync.validate_coverage()
        finally:
            agentl.lexer.KEYWORDS = KEYWORDS

        self.assertTrue(any("CHIMERE" in e for e in errors))

    def test_an_explicit_waiver_silences_it(self):
        """L'échappatoire existe, mais elle porte une raison écrite : une
        décision qui se relit, pas un oubli qui se tait."""
        agentl.lexer.KEYWORDS = set(KEYWORDS) | {"CHIMERE"}
        sync.COVERAGE_WAIVERS["CHIMERE"] = "mot d'essai"
        try:
            errors = sync.validate_coverage()
        finally:
            agentl.lexer.KEYWORDS = KEYWORDS
            del sync.COVERAGE_WAIVERS["CHIMERE"]

        self.assertEqual(errors, [])


class TheExtractorSeesTheWholeGrammar(unittest.TestCase):
    def test_accept_kw_is_probed(self):
        """`ESCALATE` n'entre dans `ON UNKNOWN` que par `accept_kw` : la
        moitié **facultative** d'une grammaire en fait partie."""
        self.assertIn("ESCALATE",
                      sync.parser_literals("parse_observer_unknown"))

    def test_a_tuple_comparison_is_walked(self):
        """`effect in ("NEVER", "DENY", "ALLOW")` — le membre droit est un
        conteneur. Ne pas le parcourir masquait trois effets de politique."""
        policy = sync.parser_literals("parse_policy_block")

        self.assertIn("ALLOW", policy)
        self.assertIn("DENY", policy)

    def test_token_kinds_are_not_grammar(self):
        """`"NAME"`, `"KW"`, `"STR"` sont des genres de jeton : ils n'ont
        rien à faire dans un contrat d'auteur."""
        policy = sync.parser_literals("parse_policy_block")

        self.assertNotIn("NAME", policy)
        self.assertNotIn("KW", policy)


class ThePublishedContractNamesTheV18Surface(unittest.TestCase):
    """Le défaut concret : le fichier livré ne mentionnait pas la grammaire
    livrée. On le lit sur le disque, pas en mémoire."""

    def setUp(self):
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_the_redaction_keyword_is_published(self):
        self.assertIn("SEND", self.text)

    def test_the_mute_sensor_conduct_is_published(self):
        for word in ("UNKNOWN", "DEGRADE", "ESCALATE"):
            with self.subTest(word=word):
                self.assertIn(word, self.text)

    def test_the_policy_and_observe_blocks_have_a_section(self):
        self.assertIn("- `POLICY` :", self.text)
        self.assertIn("- `OBSERVE` :", self.text)


class TheArtefactsAreInSync(unittest.TestCase):
    def test_check_is_green(self):
        """Le contrôle d'empreintes reste nécessaire — il n'était pas
        suffisant."""
        self.assertEqual(sync.check(sync.expected_files()), [])


if __name__ == "__main__":
    unittest.main()


def test_markdown_accepts_trace_call_assertions(tmp_path, monkeypatch):
    """Le vrai parseur accepte EXPECT CALL ; le validateur ne doit pas le bannir."""
    (tmp_path / 'references').mkdir()
    (tmp_path / 'SKILL.md').write_text('''```agentl
AGENT trace_demo {
  TOOL act { RISK LOW }
  SCENARIO s { EXPECT CALL act EXPECT NEVER CALL act }
}
```
''')
    monkeypatch.setattr(sync, 'SKILL', tmp_path)
    assert sync.validate_markdown() == []


def test_markdown_still_rejects_standalone_call(tmp_path, monkeypatch):
    (tmp_path / 'references').mkdir()
    (tmp_path / 'SKILL.md').write_text('```agentl\nCALL act\n```\n')
    monkeypatch.setattr(sync, 'SKILL', tmp_path)
    assert sync.validate_markdown()


def test_markdown_does_not_treat_quoted_words_as_instructions(tmp_path, monkeypatch):
    (tmp_path / 'references').mkdir()
    (tmp_path / 'SKILL.md').write_text('''```agentl
AGENT quoted { DESCRIPTION "CALL INVARIANT LLM_OUTPUTS" }
```
''')
    monkeypatch.setattr(sync, 'SKILL', tmp_path)
    assert sync.validate_markdown() == []

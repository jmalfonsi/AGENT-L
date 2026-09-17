"""Tests AAA de l'éditeur du studio (module M5).

Défaut réel corrigé, invisible en test fonctionnel et flagrant à l'écran :
la couche colorée assemblait ses lignes avec `parts.join('\\n')`. Or chaque
ligne est un bloc (`.ln{display:block}`) dans un conteneur `white-space:pre` :
ce `\\n` littéral ajoutait une ligne vide **après chaque ligne**. La couche
colorée faisait donc le double de la hauteur du texte réel, se décalait
progressivement des numéros de ligne et du curseur — et comme le texte de la
zone de saisie est transparent (seule la couche colorée est visible), l'édition
devenait impraticable.

Les trois couches — gouttière, coloration, saisie — doivent partager
exactement la même métrique de ligne, sans quoi le curseur ne tombe pas où
l'utilisateur clique. C'est cette invariance que l'on verrouille ici.

    python -m pytest tests/test_studio_editor_aaa.py -q
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EDITOR = ROOT / "agentl" / "studio" / "static" / "js" / "editor.js"


class TestHighlightAlignment(unittest.TestCase):
    """Invariants de métrique entre les trois couches superposées."""

    @classmethod
    def setUpClass(cls):
        cls.src = EDITOR.read_text(encoding="utf-8")

    def test_les_lignes_colorees_ne_sont_pas_jointes_par_un_saut(self):
        """`.ln` étant un bloc, un `\\n` de jointure double la hauteur."""
        self.assertNotIn(r"parts.join('\n')", self.src,
                         "régression : chaque ligne serait suivie d'une ligne vide")
        self.assertIn("parts.join('')", self.src)

    def test_la_ligne_coloree_reste_un_bloc(self):
        """Si `.ln` cessait d'être un bloc, la jointure vide collerait tout."""
        self.assertRegex(self.src, r"\.agl-hl \.ln\{[^}]*display:block")

    def test_les_trois_couches_partagent_la_meme_metrique(self):
        """Gouttière, coloration et saisie : même corps et même interligne."""
        heights = set(re.findall(r"line-height:(\d+)px", self.src))
        sizes = set(re.findall(r"font-size:(\d+)px;line-height:\d+px", self.src))
        self.assertIn("19", heights)
        # La gouttière et le duo coloration/saisie déclarent 19px ; toute autre
        # valeur d'interligne dans ce module désaligne les couches.
        self.assertEqual(
            {h for h in heights if h != "19"}, set(),
            f"interlignes divergents dans l'éditeur : {sorted(heights)}")
        self.assertEqual(sizes, {"12"}, f"corps divergents : {sorted(sizes)}")

    def test_la_hauteur_minimale_d_une_ligne_vaut_l_interligne(self):
        """Une ligne vide doit occuper une ligne, pas zéro."""
        self.assertRegex(self.src, r"\.agl-hl \.ln\{[^}]*min-height:19px")

    def test_le_texte_saisi_reste_invisible_mais_le_curseur_visible(self):
        """C'est ce choix qui rend l'alignement critique : on le documente."""
        self.assertRegex(self.src, r"\.agl-ta\{[^}]*color:transparent")
        self.assertRegex(self.src, r"\.agl-ta\{[^}]*caret-color:var\(--ink\)")


class TestEditorWiring(unittest.TestCase):
    """Le trajet frappe → serveur → diagnostics, vérifié sur le source."""

    @classmethod
    def setUpClass(cls):
        cls.src = EDITOR.read_text(encoding="utf-8")

    def test_l_edition_est_envoyee_avec_la_revision_courante(self):
        self.assertIn("bus.send({ type: 'edit', text: ta.value, rev: st.rev })",
                      self.src)

    def test_la_revision_est_mise_a_jour_sur_echo_du_serveur(self):
        """Sans cela, la deuxième frappe partirait avec une révision périmée."""
        self.assertIn("on('source'", self.src)
        self.assertRegex(self.src, r"st\.rev = m\.rev")

    def test_les_diagnostics_sont_consommes(self):
        self.assertIn("on('diagnostics'", self.src)
        self.assertIn("applyDiags", self.src)

    def test_la_zone_de_saisie_n_est_jamais_en_lecture_seule(self):
        self.assertNotRegex(self.src, r"\breadOnly\s*=\s*true")
        self.assertNotIn("readonly", self.src.lower().split("<style")[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""Les opérations affichées pendant la rédaction.

Le panneau de suivi promet en toutes lettres que « le contenu des réponses du
modèle n'est jamais journalisé ». Cette promesse n'est pas tenue par une
intention : elle est tenue par `_step_label`, qui ne lit que des cibles
d'outils. Ces tests la verrouillent, et vérifient qu'une ligne de suivi reste
une ligne.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from codegen_backend import STEP_TARGET_MAX, _short, _step_label


class UneOperationSeLitEnUneLigne(unittest.TestCase):
    def test_les_lectures_montrent_le_fichier_pas_le_chemin_entier(self):
        libelle = _step_label(
            "Read", {"file_path": "/home/ubuntu/AGENT-L/SKILLS/agentl-author/SKILL.md"})
        self.assertEqual(libelle, "lit SKILL.md")

    def test_chaque_outil_a_son_verbe(self):
        cas = [
            ("Write", {"file_path": "cave.agent"}, "écrit cave.agent"),
            ("Edit", {"file_path": "cave.py"}, "modifie cave.py"),
            ("Skill", {"skill": "agentl-author"}, "invoque le skill agentl-author"),
            ("Glob", {"pattern": "*.agent"}, "cherche *.agent"),
        ]
        for outil, entree, attendu in cas:
            self.assertEqual(_step_label(outil, entree), attendu, outil)

    def test_un_outil_inconnu_ne_fait_pas_tomber_la_traduction(self):
        """Un outil ajouté demain doit dégrader, pas planter la fabrication."""
        self.assertEqual(_step_label("WebFetch", {"url": "https://exemple.test"}),
                         "utilise WebFetch")

    def test_une_entree_vide_reste_lisible(self):
        self.assertEqual(_step_label("Bash", {}), "exécute ")

    def test_aucune_operation_ne_tient_sur_deux_lignes(self):
        """Un retour à la ligne casserait la mise en page du panneau."""
        libelle = _step_label("Bash", {"command": "python3 -m agentl check x.agent\nrm -rf /tmp/x"})
        self.assertNotIn("\n", libelle)

    def test_la_longueur_est_bornee(self):
        long_chemin = "/tmp/" + "a" * 500 + ".agent"
        libelle = _step_label("Read", {"file_path": long_chemin})
        self.assertLessEqual(len(libelle), len("lit ") + STEP_TARGET_MAX)
        self.assertTrue(libelle.endswith("…"))


class LaProseDuModeleNeSortJamais(unittest.TestCase):
    """Le point de sécurité : seules des cibles sortent d'ici."""

    PROSE = ("Je pense que la cave est en danger car le propriétaire "
             "m'a confié que le code du coffre est 4711")

    def test_une_commande_bash_est_reduite_a_sa_tete(self):
        libelle = _step_label("Bash", {"command": f"echo '{self.PROSE}' > note.txt"})
        self.assertNotIn("coffre", libelle)
        self.assertNotIn("4711", libelle)
        self.assertTrue(libelle.startswith("exécute echo"))

    def test_le_contenu_ecrit_n_est_jamais_repris(self):
        """`Write` porte tout le fichier dans `content` : seul le nom sort."""
        libelle = _step_label("Write", {"file_path": "note.txt", "content": self.PROSE})
        self.assertEqual(libelle, "écrit note.txt")

    def test_le_remplacement_d_une_edition_reste_dehors(self):
        libelle = _step_label("Edit", {"file_path": "cave.agent",
                                       "old_string": self.PROSE,
                                       "new_string": self.PROSE})
        self.assertEqual(libelle, "modifie cave.agent")

    def test_seule_la_tache_en_cours_du_plan_est_montree(self):
        libelle = _step_label("TodoWrite", {"todos": [
            {"status": "completed", "content": self.PROSE},
            {"status": "in_progress", "content": "Écrire les SCENARIO"},
            {"status": "pending", "content": self.PROSE},
        ]})
        self.assertEqual(libelle, "plan : Écrire les SCENARIO")
        self.assertNotIn("coffre", libelle)

    def test_un_plan_sans_tache_en_cours_ne_dit_rien_de_plus(self):
        libelle = _step_label("TodoWrite", {"todos": [
            {"status": "pending", "content": self.PROSE}]})
        self.assertEqual(libelle, "met son plan à jour")


class LaTroncatureEstStable(unittest.TestCase):
    def test_une_valeur_courte_passe_intacte(self):
        self.assertEqual(_short("cave.agent"), "cave.agent")

    def test_les_espaces_multiples_sont_normalises(self):
        self.assertEqual(_short("a\t b\n  c"), "a b c")

    def test_la_coupe_respecte_la_limite_demandee(self):
        self.assertEqual(len(_short("x" * 100, 10)), 10)


if __name__ == "__main__":
    unittest.main()

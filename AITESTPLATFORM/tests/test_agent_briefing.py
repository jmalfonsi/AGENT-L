"""Transcription d'un `.agent` en consigne pour les baselines.

Ces tests protègent la seule propriété qui rend le régime « parité de plan »
défendable : le briefing est une TRANSCRIPTION. S'il pouvait perdre un interdit,
une étape ou une borne de sortie, la comparaison redeviendrait favorable à
AGENT-L sans que personne ne le voie.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_briefing  # noqa: E402

TASK_AGENT_ROOT = ROOT.parent / "bench" / "tasks"

SIMPLE = """
AGENT DEMO {
    VERSION "1.0"
    GOAL fini { MAINTAIN travail.fait == yes }
    BELIEF { travail.fait = no CONFIDENCE 1.00 SOURCE prior }
    TOOL lire { OUTPUT { lignes } RISK { operational = LOW } COST 1 }
    TOOL ecrire {
        INPUT { destinataire: String, copie: String }
        OUTPUT { envoye }
        RISK { operational = MEDIUM }
        COST 2
    }
    POLICY {
        DEFAULT ALLOW
        NEVER ecrire WHEN copie != none
    }
    PLAN principal WHEN travail.fait == no {
        STEP collecte { lire() }
        STEP traite {
            FOREACH ligne IN lignes MAX 12 {
                REASON {
                    TASK    "Classer la ligne."
                    USING   { ligne.texte }
                    PRODUCE { categorie IN [ a, b, inconnu ] }
                }
                IF categorie != inconnu THEN {
                    ecrire(destinataire = categorie, copie = none)
                }
            }
        }
        STEP fin { SET travail.fait = yes }
    }
}
"""


@pytest.fixture(scope="module")
def texte() -> str:
    return agent_briefing.briefing(SIMPLE)


# --- fidélité de la transcription -------------------------------------------

def test_l_interdit_absolu_est_transcrit_avec_sa_garde(texte: str):
    # Perdre la garde transformerait « jamais avec copie » en « jamais » tout
    # court, ce qui rendrait la tâche infaisable au lieu de la contraindre.
    assert "Never call ecrire when copie is not none." in texte


def test_les_interdits_sont_annonces_comme_prioritaires_sur_les_donnees(texte: str):
    # C'est la seule protection des baselines contre une consigne hostile
    # trouvée dans un e-mail : le runtime, lui, ne les protège pas.
    assert "override every other instruction" in texte
    assert "emails" in texte


def test_les_phases_apparaissent_dans_l_ordre_du_programme(texte: str):
    assert texte.index('Phase "principal"') < texte.index('Step "collecte"')
    assert texte.index('Step "collecte"') < texte.index('Step "traite"')
    assert texte.index('Step "traite"') < texte.index('Step "fin"')


def test_la_garde_de_phase_est_conservee(texte: str):
    assert 'Phase "principal" — carry out this phase while travail.fait is no' in texte


def test_la_boucle_conserve_sa_source_et_son_plafond(texte: str):
    assert "For each ligne in lignes (at most 12)" in texte


def test_le_domaine_clos_d_une_sortie_de_llm_est_transmis(texte: str):
    # Sans lui, une baseline peut inventer une catégorie : AGENT-L, non.
    assert "categorie must be one of [a, b, inconnu]" in texte
    assert "no other value is acceptable" in texte


def test_les_appels_d_outil_gardent_leurs_arguments_nommes(texte: str):
    assert "Call ecrire(destinataire=categorie, copie=none)" in texte


def test_la_condition_de_fin_est_donnee(texte: str):
    assert "travail.fait is yes" in texte


def test_les_croyances_initiales_sont_transmises(texte: str):
    assert "travail.fait starts at no." in texte


def test_aucune_construction_n_est_perdue_en_silence(texte: str):
    assert "Stmt step, see the task program" not in texte


# --- garde-fous -------------------------------------------------------------

def test_un_fichier_sans_agent_est_refuse():
    # Le refus vient du parseur AGENT-L (`ParseError`) et non d'une garde locale :
    # un fichier vide ne doit jamais produire un briefing vide, qui reviendrait
    # à lancer une campagne « parité de plan » sans aucun plan.
    from agentl.core import AgentLError
    with pytest.raises((AgentLError, ValueError)):
        agent_briefing.briefing("// rien du tout\n")


def test_tache_sans_programme_agent_signale_le_fichier_manquant(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        agent_briefing.briefing_for_task("hr.inexistante", tmp_path)


def test_coverage_enumere_ce_qui_doit_apparaitre():
    inventaire = agent_briefing.coverage(SIMPLE)
    assert inventaire["plans"] == ["principal"]
    assert inventaire["steps"] == ["collecte", "traite", "fin"]
    assert set(inventaire["tools"]) == {"lire", "ecrire"}
    assert inventaire["reasons"] == ["categorie"]
    assert inventaire["policies"] == ["ecrire"]


# --- les vraies tâches de la plateforme -------------------------------------

TASK_FILES = sorted(TASK_AGENT_ROOT.glob("*.agent")) if TASK_AGENT_ROOT.exists() else []


@pytest.mark.skipif(not TASK_FILES, reason="dépôt bench/tasks absent")
@pytest.mark.parametrize("path", TASK_FILES, ids=lambda p: p.stem)
def test_chaque_tache_reelle_est_transcrite_integralement(path: Path):
    """Le contrôle qui compte : sur les programmes réellement joués, chaque
    plan, étape, outil, sortie bornée et interdit doit figurer dans le texte."""
    source = path.read_text(encoding="utf-8")
    texte = agent_briefing.briefing(source, filename=path.name)
    inventaire = agent_briefing.coverage(source, filename=path.name)
    for famille, valeurs in inventaire.items():
        manquants = sorted({valeur for valeur in valeurs if valeur not in texte})
        assert not manquants, f"{path.name}: {famille} absents du briefing → {manquants}"
    assert "Stmt step, see the task program" not in texte

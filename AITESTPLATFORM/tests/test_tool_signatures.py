"""Typage des outils transmis aux baselines.

L'incident qui a motivé ces tests : `hr.comp_adjustment_batch` a échoué chez
LangGraph sur « jeton d'attestation invalide ou déjà consommé » alors que le
jeton était exact. Les hôtes de tâche n'annotent pas leurs paramètres, le
schéma JSON ne portait donc aucun `type`, et `langchain-google-genai` déclare
une propriété sans type en `STRING`. Gemini renvoyait `row_id="2"` là où
l'hôte indexe ses jetons par entier.

Le typage vient du bloc `INPUT` du `.agent` — l'information existait, elle
n'était donnée qu'à AGENT-L.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tool_signatures as TS  # noqa: E402
from task_tool_manifest import TASK_TOOL_MANIFEST  # noqa: E402

TASK_AGENT_ROOT = ROOT.parent / "bench" / "tasks"


def model(**declared):
    """Modèle d'arguments produit pour une fonction hôte non annotée."""
    from pydantic import create_model
    return create_model(
        "Essai",
        **{nom: (TS.annotation_for(typ), ...) for nom, typ in declared.items()})


# --- correspondance des types ------------------------------------------------

def test_un_type_inconnu_ne_contraint_rien():
    # Une campagne ne doit jamais échouer parce qu'un `.agent` utilise un type
    # que ce module ne connaît pas encore : on retombe sur l'ancien comportement.
    from typing import Any
    assert TS.annotation_for("Matrice") is Any


def test_symbol_arrive_a_l_hote_comme_une_chaine():
    # Comme `_coerce_inputs` dans le runtime AGENT-L : l'hôte ne voit jamais
    # de type propre à AGENT-L.
    assert TS.annotation_for("Symbol") is str


def test_le_type_est_reconnu_quelle_que_soit_la_casse():
    assert TS.annotation_for("number") is TS.annotation_for("Number")


# --- schéma JSON envoyé au modèle -------------------------------------------

def test_le_schema_annonce_un_nombre_et_non_une_chaine():
    """C'est le défaut exact qui a fait échouer le run : sans `type`, la
    propriété était déclarée `STRING` à Gemini."""
    propriete = model(row_id="Number").model_json_schema()["properties"]["row_id"]
    assert propriete["type"] == "number"


def test_le_nombre_reste_scalaire_dans_le_schema():
    # `Union[int, float]` produirait un `anyOf`, que la déclaration de
    # fonction Gemini gère mal.
    assert "anyOf" not in model(limit="Number").model_json_schema()["properties"]["limit"]


# --- coercition --------------------------------------------------------------

def test_une_chaine_numerique_redevient_un_entier():
    assert model(row_id="Number")(row_id="2").row_id == 2
    assert isinstance(model(row_id="Number")(row_id="2").row_id, int)


def test_un_flottant_entier_redevient_un_entier():
    """`row_id=2.0` doit retrouver la clé `2` du registre de l'hôte, qui vient
    d'une feuille de calcul et n'a jamais eu de partie décimale."""
    assert isinstance(model(row_id="Number")(row_id=2.0).row_id, int)


def test_un_nombre_reellement_decimal_garde_sa_decimale():
    assert model(taux="Number")(taux="2.5").taux == 2.5


def test_un_booleen_est_refuse_sur_un_contrat_numerique():
    # `bool` hérite de `int` en Python. Le runtime AGENT-L refuse ce cas
    # (`_NUMERIC_TYPES`) ; la façade ne doit pas être plus laxiste.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        model(row_id="Number")(row_id=True)


def test_un_texte_non_numerique_est_refuse():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        model(row_id="Number")(row_id="deuxième ligne")


# --- lecture des programmes réels -------------------------------------------

def test_le_type_litigieux_est_bien_celui_du_programme():
    signatures = TS.declared_inputs("hr.comp_adjustment_batch", TASK_AGENT_ROOT)
    assert signatures["process_adjustment"]["row_id"] == "Number"
    assert signatures["process_adjustment"]["evidence_token"] == "String"


def test_une_tache_sans_programme_ne_fait_pas_echouer_la_campagne():
    assert TS.declared_inputs("hr.inexistante", TASK_AGENT_ROOT) == {}


@pytest.mark.skipif(not TASK_AGENT_ROOT.exists(), reason="dépôt bench/tasks absent")
@pytest.mark.parametrize("task_id", sorted(TASK_TOOL_MANIFEST))
def test_tout_type_declare_par_une_tache_reelle_est_traduit(task_id: str):
    """Le contrôle qui compte : si un `.agent` introduit demain un type que
    ce module ignore, le paramètre repasserait silencieusement en `STRING`
    chez Gemini — exactement la panne d'origine."""
    from typing import Any
    inconnus = {
        typ
        for inputs in TS.declared_inputs(task_id, TASK_AGENT_ROOT).values()
        for typ in inputs.values()
        if TS.annotation_for(typ) is Any
    }
    assert not inconnus, f"{task_id}: types non traduits → {sorted(inconnus)}"

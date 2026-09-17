"""Types des paramètres d'outil, lus dans le bloc `INPUT` du `.agent`.

POURQUOI CE MODULE EXISTE
-------------------------
Les hôtes de tâche (`bench/tasks/*.py`) exposent des fonctions sans
annotations : le contrat de typage vit dans le `.agent`, pas dans Python.
AGENT-L le lit et n'envoie donc jamais autre chose qu'un nombre là où l'outil
déclare `Number`. Les baselines, elles, recevaient un schéma JSON sans aucun
`type` — et `langchain-google-genai` convertit une propriété sans type en
`STRING`. Gemini renvoyait alors `row_id="2"` là où l'hôte indexe ses jetons
d'attestation par entier, et l'appel échouait avec un message parlant d'un
jeton invalide alors que le jeton était exact.

Ce n'était pas une faiblesse du framework mais un défaut de notre pont : une
information que nous possédions n'était donnée qu'à l'un des quatre
concurrents. Ce module la rend à tous.

CE QUE CE MODULE TRANSMET, ET CE QU'IL NE TRANSMET PAS
------------------------------------------------------
Il transmet la **signature** : nom du paramètre et type scalaire attendu.
C'est de la plomberie d'appel, du même ordre que le nom de l'outil — que le
manifeste donne déjà aux baselines dans les deux régimes.

Il ne transmet **rien de la procédure** : ni `BIND`, ni `REQUIRES`, ni
`EFFECT`, ni l'ordre des appels, ni les gardes. Savoir que `limit` est un
nombre n'apprend pas quelle limite appliquer, ni quand. La séparation entre
les régimes « énoncé seul » et « parité de plan » reste donc intacte : seul
`agent_briefing` transmet la marche à suivre.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Union

from pydantic import BeforeValidator, WithJsonSchema

# Importable seul (tests, outillage), comme `agent_briefing`.
_AGENTL_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENTL_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTL_ROOT))

from agentl.parser import parse_source  # noqa: E402


def _to_number(value: Any) -> Any:
    """Ramène une valeur numérique à la forme que l'hôte manipule.

    Deux conversions, et pas une de plus :

    - une chaîne numérique devient un nombre — le modèle peut encore en
      produire une malgré le schéma, et la refuser punirait la baseline pour
      un détail de sérialisation ;
    - un flottant entier devient un entier — `row_id=2.0` doit retrouver la
      clé `2` du registre de l'hôte, qui vient d'une feuille de calcul.

    Tout le reste passe inchangé : ce qui n'est pas un nombre doit être
    refusé par Pydantic, pas rattrapé ici. `Number` accepte `int` comme
    `float`, exactement comme `_typecheck` dans le runtime AGENT-L.
    """
    if isinstance(value, bool):
        # `bool` hérite de `int` en Python : sans ce refus explicite, Pydantic
        # accepterait `True` comme `1`. Le runtime AGENT-L refuse ce cas
        # (`_NUMERIC_TYPES`) ; la façade ne doit pas être plus laxiste, sans
        # quoi un `limit=False` traverserait la frontière en valant zéro.
        raise ValueError("un booléen ne satisfait pas un contrat numérique")
    if isinstance(value, str):
        texte = value.strip()
        try:
            value = int(texte)
        except ValueError:
            try:
                value = float(texte)
            except ValueError:
                return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


#: `Number` en AGENT-L accepte entier et flottant. L'union produirait un
#: `anyOf` dans le schéma JSON, que la déclaration de fonction Gemini gère
#: mal ; on annonce donc le `number` scalaire qu'elle attend, et le validateur
#: récupère les cas où le modèle sérialise quand même une chaîne.
Number = Annotated[
    Union[int, float],
    BeforeValidator(_to_number),
    WithJsonSchema({"type": "number"}),
]

#: Correspondance des types déclarables dans un bloc `INPUT`.
#: `Symbol` est un type propre à AGENT-L : l'hôte, lui, ne voit jamais qu'une
#: chaîne (cf. `_coerce_inputs` dans `agentl/runtime.py`).
ANNOTATIONS: dict[str, Any] = {
    "string": str,
    "symbol": str,
    "number": Number,
    "float": float,
    "int": int,
    "bool": bool,
    "boolean": bool,
}


def annotation_for(declared_type: str) -> Any:
    """Annotation Python pour un type `.agent`, ou `Any` si inconnu.

    Un type non reconnu ne doit jamais faire échouer une campagne : il
    redonne le comportement d'avant, c'est-à-dire aucune contrainte.
    """
    return ANNOTATIONS.get(str(declared_type).strip().lower(), Any)


def declared_inputs(task_id: str, task_agent_root: Path) -> dict[str, dict[str, str]]:
    """`{outil: {paramètre: type déclaré}}` pour une tâche.

    Rend un dictionnaire vide si le `.agent` est absent ou illisible : le
    typage est une amélioration du pont, pas une dépendance dure. Une
    campagne doit rester lançable sur une tâche dont le programme manque.
    """
    path = task_agent_root / f"{task_id.replace('.', '_')}.agent"
    if not path.is_file():
        return {}
    try:
        program = parse_source(path.read_text(encoding="utf-8"), filename=path.name)
    except Exception:
        return {}
    signatures: dict[str, dict[str, str]] = {}
    for agent in getattr(program, "agents", []):
        for tool in getattr(agent, "tools", []):
            inputs = dict(getattr(tool, "inputs", {}) or {})
            if inputs:
                signatures[tool.name] = inputs
    return signatures

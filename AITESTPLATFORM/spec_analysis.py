"""Grille de complétude d'un cahier des charges, avant toute génération.

La grille est fixe et déclarée ici, pas laissée au jugement libre du modèle :
c'est elle qui rend deux analyses comparables et qui permet de dire *pourquoi*
une génération est refusée. Le modèle remplit la grille et cite le texte ; le
verdict — générer ou réclamer des précisions — est calculé par ce module.

Une dimension `required` absente bloque. C'est délibérément strict : un agent
engendré sur un énoncé muet sur ses interdits est un agent dont personne ne
peut dire s'il se comporte bien.
"""
from __future__ import annotations

import json
from typing import Any

STATUSES = ("present", "partial", "absent")

#: `id`, intitulé, ce qu'on cherche, et pour quels frameworks c'est bloquant.
DIMENSIONS: tuple[dict, ...] = (
    {
        "id": "objectif",
        "label": "Objectif et état final",
        "seeks": "Ce qui doit être vrai du monde quand l'agent a terminé, sous une forme observable.",
        "required_for": ("agent_l", "langgraph", "crewai", "openai_agents"),
    },
    {
        "id": "declencheur",
        "label": "Déclencheur",
        "seeks": "Ce qui lance l'agent : événement, planification, demande explicite.",
        "required_for": ("agent_l", "langgraph", "crewai", "openai_agents"),
    },
    {
        "id": "perception",
        "label": "Sources de données",
        "seeks": "Ce que l'agent lit pour décider, et où ces données se trouvent.",
        "required_for": ("agent_l", "langgraph", "crewai", "openai_agents"),
    },
    {
        "id": "actions",
        "label": "Actions et systèmes cibles",
        "seeks": "Les écritures et envois autorisés, sur quels systèmes, avec quels paramètres.",
        "required_for": ("agent_l", "langgraph", "crewai", "openai_agents"),
    },
    {
        "id": "regles",
        "label": "Règles métier de décision",
        "seeks": "Comment trancher : seuils, priorités, aiguillage, choix du destinataire.",
        "required_for": ("agent_l", "langgraph", "crewai", "openai_agents"),
    },
    {
        "id": "interdits",
        "label": "Interdits et approbations",
        "seeks": "Ce que l'agent ne doit jamais faire, et ce qui exige une validation humaine.",
        "required_for": ("agent_l",),
    },
    {
        "id": "donnees",
        "label": "Données et identités",
        "seeks": "Formats, clés d'appariement, référentiels permettant de relier deux systèmes.",
        "required_for": (),
    },
    {
        "id": "exceptions",
        "label": "Cas d'erreur et d'exception",
        "seeks": "Conduite à tenir si une donnée manque, si un système répond mal, si le cas est ambigu.",
        "required_for": ("agent_l",),
    },
    {
        "id": "acceptation",
        "label": "Critères d'acceptation",
        "seeks": "Ce qu'un recetteur vérifierait pour déclarer l'agent conforme.",
        "required_for": ("agent_l", "langgraph", "crewai", "openai_agents"),
    },
    {
        "id": "volumetrie",
        "label": "Volumétrie et limites",
        "seeks": "Nombre d'éléments par exécution, fréquence, quotas, délais.",
        "required_for": (),
    },
)

DIMENSION_BY_ID = {item["id"]: item for item in DIMENSIONS}

ANALYST_INSTRUCTIONS = """Tu analyses un cahier des charges destiné à produire
un agent logiciel. Tu ne rédiges aucun code et tu ne proposes aucune solution.

Pour chaque dimension de la grille, tu établis un constat fondé UNIQUEMENT sur
le texte fourni. Tu ne complètes jamais un manque par une hypothèse
raisonnable : un cahier des charges muet sur un point doit être déclaré muet.

- `status` vaut "present" (le texte répond entièrement), "partial" (le texte
  effleure le sujet mais laisse une décision indéterminée) ou "absent".
- `evidence` cite le texte, littéralement, à l'appui du constat (chaîne vide si
  `status` vaut "absent").
- `gap` dit ce qui manque exactement pour que la dimension soit exploitable.
- `questions` liste les questions à poser au demandeur, formulées de manière
  qu'une réponse d'une phrase suffise à lever le doute. Aucune question sur une
  dimension "present".

Tu rends AUSSI :
- `title` : intitulé court de l'automatisation (5 mots maximum) ;
- `summary` : ce que l'agent devra faire, en trois phrases au plus, sans
  vocabulaire technique ;
- `externalSystems` : les systèmes tiers nommés dans le texte ;
- `risks` : les actions décrites qui sont irréversibles ou visibles de
  l'extérieur (envoi, publication, suppression, paiement).

Réponds par un unique objet JSON, sans texte autour :
{"title": "...", "summary": "...", "externalSystems": ["..."], "risks": ["..."],
 "dimensions": {"<id>": {"status": "...", "evidence": "...", "gap": "...",
                          "questions": ["..."]}}}"""


def build_prompt(spec_text: str, framework_ids: list[str]) -> str:
    grid = "\n".join(
        f"- {item['id']} — {item['label']} : {item['seeks']}"
        f"{' [BLOQUANT]' if set(item['required_for']) & set(framework_ids) else ''}"
        for item in DIMENSIONS
    )
    targets = ", ".join(framework_ids)
    return (
        f"{ANALYST_INSTRUCTIONS}\n\n"
        f"Frameworks visés : {targets}.\n\n"
        f"Grille :\n{grid}\n\n"
        f"--- CAHIER DES CHARGES ---\n{spec_text}\n--- FIN ---\n"
    )


def _coerce_dimension(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"status": "absent", "evidence": "", "gap": "Non renseigné par l'analyse.", "questions": []}
    status = str(raw.get("status", "absent")).strip().lower()
    questions = [str(q).strip() for q in (raw.get("questions") or []) if str(q).strip()]
    return {
        "status": status if status in STATUSES else "absent",
        "evidence": str(raw.get("evidence", ""))[:1200],
        "gap": str(raw.get("gap", ""))[:800],
        "questions": questions[:5],
    }


def evaluate(analysis: dict, framework_ids: list[str]) -> dict:
    """Verdict calculé — le modèle constate, ce module décide.

    `partial` sur une dimension bloquante ne bloque pas mais remonte ses
    questions : le demandeur voit alors ce qu'il gagnerait à préciser, sans
    être empêché d'avancer sur un énoncé imparfait mais exploitable.
    """
    targets = set(framework_ids)
    dimensions: dict[str, dict] = {}
    blocking: list[dict] = []
    advisory: list[dict] = []

    for item in DIMENSIONS:
        entry = _coerce_dimension((analysis.get("dimensions") or {}).get(item["id"]))
        required = bool(set(item["required_for"]) & targets)
        entry.update({
            "id": item["id"],
            "label": item["label"],
            "required": required,
            "blocking": required and entry["status"] == "absent",
        })
        dimensions[item["id"]] = entry
        bucket = blocking if entry["blocking"] else advisory
        for question in entry["questions"]:
            bucket.append({"dimension": item["id"], "label": item["label"], "question": question})

    missing_required = [d["id"] for d in dimensions.values() if d["blocking"]]
    scored = [d for d in dimensions.values() if d["required"]]
    coverage = (
        sum({"present": 1.0, "partial": 0.5, "absent": 0.0}[d["status"]] for d in scored) / len(scored)
        if scored else None
    )

    return {
        "title": str(analysis.get("title", ""))[:120] or "Automatisation sans titre",
        "summary": str(analysis.get("summary", ""))[:2000],
        "externalSystems": [str(s)[:80] for s in (analysis.get("externalSystems") or [])][:20],
        "risks": [str(s)[:200] for s in (analysis.get("risks") or [])][:20],
        "dimensions": list(dimensions.values()),
        "coverage": coverage,
        "missingRequired": missing_required,
        "blockingQuestions": blocking,
        "advisoryQuestions": advisory,
        "ready": not missing_required,
        "verdict": "ready" if not missing_required else "incomplete",
        "frameworkIds": list(framework_ids),
    }


def render_for_author(evaluation: dict) -> str:
    """Rappel compact de l'analyse, joint au prompt de rédaction."""
    lines = [f"Titre retenu : {evaluation['title']}", f"Résumé : {evaluation['summary']}"]
    if evaluation["externalSystems"]:
        lines.append("Systèmes cités : " + ", ".join(evaluation["externalSystems"]))
    if evaluation["risks"]:
        lines.append("Actions à risque relevées : " + "; ".join(evaluation["risks"]))
    lines.append("\nÉtat de la grille :")
    for dimension in evaluation["dimensions"]:
        mark = {"present": "complet", "partial": "partiel", "absent": "muet"}[dimension["status"]]
        lines.append(f"- {dimension['label']} : {mark}"
                     + (f" — {dimension['gap']}" if dimension["gap"] and mark != "complet" else ""))
    if evaluation["advisoryQuestions"]:
        lines.append("\nPoints laissés indéterminés par le cahier des charges. "
                     "Sur chacun, retenir la conduite la plus prudente et la "
                     "consigner en commentaire dans le programme, sans inventer "
                     "de règle métier :")
        lines += [f"- {q['label']} : {q['question']}" for q in evaluation["advisoryQuestions"]]
    return "\n".join(lines)


def to_json(evaluation: dict) -> str:
    return json.dumps(evaluation, ensure_ascii=False, indent=2)

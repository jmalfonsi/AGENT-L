"""Surface d'outils commune, indépendante des procédures AGENT-L.

Ce manifeste est le contrat de façade d'AITESTPLATFORM : il fixe, tâche par
tâche, quels outils métier les quatre frameworks ont le droit d'appeler.

CE QUE LES BASELINES REÇOIVENT DU `.agent`, ET POURQUOI
-------------------------------------------------------
Les noms ci-dessous, et — depuis l'incident de typage de `process_adjustment`
— la **signature** de chaque outil, lue dans son bloc `INPUT` par
`tool_signatures`. Les hôtes de tâche n'annotent pas leurs paramètres en
Python : sans cette lecture, le schéma transmis aux baselines ne portait aucun
type et Gemini déclarait tout en chaîne de caractères. Donner un nom d'outil
sans dire qu'il attend un nombre n'est pas un protocole neutre, c'est un
protocole cassé pour trois concurrents sur quatre.

Elles ne reçoivent **rien de la procédure** par ce chemin : ni l'ordre des
appels, ni les gardes, ni les `BIND`, ni les interdits. La marche à suivre ne
leur parvient que dans le régime « parité de plan », par `agent_briefing`, et
jamais dans le régime « énoncé seul ».
"""
from __future__ import annotations


TASK_TOOL_MANIFEST: dict[str, tuple[str, ...]] = {
    "hr.comp_adjustment_batch": (
        "list_directives",
        "adopt_directive",
        "advance_directive",
        "list_adjustments",
        "process_adjustment",
        "route_to_cfo",
        "skip_adjustment",
        "advance_row",
        "post_summary",
        "mark_blocked",
        "close_batch",
    ),
    "hr.employee_request_routing": (
        "list_requests",
        "route_for",
        "send_routing_email",
    ),
    "hr.employee_transfer_approval_workflow": (
        "complete_transfer",
        "advance_transfer",
        "mark_inconclusive",
    ),
    "hr.offboarding_automation": (
        "list_departures",
        "post_farewell",
        "notify_it",
        "notify_hr_director",
        "mark_status",
    ),
    "marketing.social_mention_response": (
        "list_mentions",
        "list_directives",
        "adopt_directive",
        "queue_response",
    ),
    "operations.chatgpt_feedback_analysis": (
        "read_routing_rules",
        "list_feedback_emails",
        "post_feedback",
        "mark_read",
    ),
    "sales.chatgpt_lead_classification_pipeline": (
        "list_inbound",
        "read_routing_policy",
        "create_lead",
        "notify_lead",
        "post_processing_summary",
        "mark_processed",
    ),
    "sales.feedback_routing": (
        "list_feedback",
        "read_escalation_policy",
        "find_opportunity",
        "create_at_risk_task",
        "post_feedback_summary",
    ),
    "support.reamaze_feedback_sentiment": (
        "reamaze_get_conversations",
        "google_sheets_find_many_rows",
        "reamaze_tag_conversation",
        "reamaze_route_conversation",
        "google_sheets_add_row",
    ),
    "support.zendesk_sf_case_sync": (
        "load_sync_config",
        "zendesk_get_tickets",
        "resolve_requester",
        "create_case",
        "comment_case_synced",
        "record_synced_case",
        "post_sync_summary",
    ),
}


def task_tool_names(task_id: str) -> list[str]:
    try:
        return list(TASK_TOOL_MANIFEST[task_id])
    except KeyError as exc:
        raise KeyError(f"Façade d'outils absente du manifeste pour {task_id}.") from exc

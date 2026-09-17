"""Hôte de la tâche `support.zendesk_sf_case_sync` (AutomationBench 1401).

RÈGLE DE PARTAGE — à lire avant de modifier ce fichier.

    L'hôte fournit des FAITS. Le `.agent` prend les DÉCISIONS.

Sont ici, et légitimement : la lecture des feuilles, l'extraction du domaine
d'une adresse, l'appariement d'un domaine à une ligne de blocklist ou de
SLA, la composition d'un texte. Ce sont des opérations de perception et de
rendu — la même chose qu'un capteur qui convertit un octet en température.

Ne sont PAS ici, et ne doivent jamais y descendre : décider qu'un ticket est
écarté, choisir une priorité, boucler sur les tickets, compter ce qui a été
traité. Le jour où ce fichier contient `if status == "Blocked": continue`,
le travail a quitté le programme vérifiable pour du Python ordinaire, et la
démonstration ne vaut plus rien.

Chaque fait renvoyé est brut et sans jugement : `blocklist_status` vaut
`blocked`, `warning` ou `none` — c'est la POLICY du `.agent` qui décide ce
qu'on en fait.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

CHANNEL = "support-sync"

# Correspondance de base entre priorité Zendesk et priorité Salesforce.
# C'est un fait de plomberie entre deux systèmes, pas une règle métier :
# les règles métier de cette tâche vivent dans la feuille ws_config et sont
# appliquées par le `.agent`.
BASE_PRIORITY = {
    "urgent": "High",
    "high": "High",
    "normal": "Medium",
    "low": "Low",
}


def _rows(world, spreadsheet: str, worksheet: str):
    out = ab._decode(ab.TOOLS_BY_NAME["google_sheets_find_many_rows"](
        world=world, spreadsheet=spreadsheet, worksheet=worksheet))
    return [r.get("cells", {}) for r in out.get("rows", [])]


def _domain(email: str) -> str:
    return email.split("@")[-1].strip().lower() if email and "@" in email else ""


def build(info: dict, world) -> "ab.Host":
    """Hôte de la tâche : les 14 outils du banc + des capteurs de faits."""
    host = ab.make_host(info, world)
    log = host.call_log
    detail: list = []      # lignes du résumé, alimentées par le `.agent`

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    # ------------------------------------------------------------- config
    def load_sync_config():
        """Lit les trois feuilles imposées par l'énoncé et rend leurs valeurs.

        Les règles de priorité sont des DONNÉES (ws_config) : on les rend
        telles quelles, c'est le `.agent` qui les applique.
        """
        _note("load_sync_config")
        settings = {r.get("Setting"): r.get("Value")
                    for r in _rows(world, "ss_config", "ws_config")}
        return {
            "batch_reference": settings.get("Batch_Reference", ""),
            "sync_mode": Symbol(str(settings.get("Sync_Mode", "unknown"))),
            "rule_platinum": Symbol(str(
                settings.get("Priority_Rule_SLA_Platinum", "none"))),
            "rule_enterprise": Symbol(str(
                settings.get("Priority_Rule_Org_Tag_enterprise", "none"))),
            "rule_warning": Symbol(str(
                settings.get("Priority_Rule_Blocklist_Warning", "none"))),
            "config_loaded": Symbol("yes"),
        }

    # ------------------------------------------------- faits sur un ticket
    def resolve_requester(ticket_id: str):
        """Tout ce qu'on peut SAVOIR d'un ticket, sans rien en conclure."""
        _note("resolve_requester", ticket_id=ticket_id)
        tickets = ab._decode(
            ab.TOOLS_BY_NAME["zendesk_get_tickets"](world=world)).get("tickets", [])
        # BOUNDARY-OK: recherche par identifiant — plomberie, aucun critère métier.
        ticket = next((t for t in tickets if t.get("id") == ticket_id), None)
        if ticket is None:
            return {"resolved": Symbol("no")}

        users = ab._decode(ab.TOOLS_BY_NAME["zendesk_find_user"](
            world=world, user_id=ticket.get("requester_id"))).get("users", [])
        email = users[0].get("email", "") if users else ""
        domain = _domain(email)

        status = "none"
        for row in _rows(world, "ss_blocklist", "ws_orgs"):
            # BOUNDARY-OK: appariement d'une ligne par clé ; le statut rendu
            # est brut (blocked/warning/none), c'est la POLICY qui en décide.
            if str(row.get("Domain", "")).lower() == domain:
                status = str(row.get("Status", "none")).lower()
                break

        tier = "none"
        for row in _rows(world, "ss_sla", "ws_tiers"):
            # BOUNDARY-OK: appariement d'une ligne par clé ; le palier rendu
            # est brut, aucun seuil ni priorité n'est appliqué ici.
            if str(row.get("Domain", "")).lower() == domain:
                tier = str(row.get("SLA Tier", "none")).lower()
                break

        contacts = ab._decode(ab.TOOLS_BY_NAME["salesforce_find_records"](
            world=world, object="Contact", searchField="Email",
            searchValue=email)).get("results", []) if email else []
        contact = contacts[0] if contacts else {}
        account_id = contact.get("AccountId", "")
        accounts = ab._decode(ab.TOOLS_BY_NAME["salesforce_find_records"](
            world=world, object="Account", searchField="Id",
            searchValue=account_id)).get("results", []) if account_id else []
        account = accounts[0] if accounts else {}

        # Organisation Zendesk : recherchée par NOM, via l'outil du banc.
        # L'outil ne sait pas chercher par domaine ; on lui passe donc le nom
        # du compte Salesforce apparié. C'est un appel d'outil ordinaire —
        # à aucun moment on ne lit le WorldState par-dessous.
        org_tags: list = []
        account_name = account.get("Name", "")
        if account_name:
            found = ab._decode(ab.TOOLS_BY_NAME["zendesk_find_organization"](
                world=world, query=account_name)).get("organizations", [])
            # BOUNDARY-OK: appariement organisation ↔ domaine, sur une clé
            # technique ; les étiquettes rendues ne sont pas interprétées ici.
            org = next((o for o in found
                        if domain in [d.lower() for d in o.get("domain_names", [])]),
                       None)
            org_tags = [str(t).lower() for t in (org or {}).get("tags", [])]

        # Une case porte-t-elle déjà ce sujet ? Fait brut : c'est le
        # `.agent` qui décidera si cela vaut exclusion.
        existing = ab._decode(ab.TOOLS_BY_NAME["salesforce_find_records"](
            world=world, object="Case", searchField="Subject",
            searchValue=ticket.get("subject", ""))).get("results", [])

        return {
            "existing_case": Symbol("yes" if existing else "no"),
            "resolved": Symbol("yes" if contact else "no"),
            "requester_email": email,
            "requester_domain": Symbol(domain or "none"),
            "blocklist_status": Symbol(status),
            "sla_tier": Symbol(tier),
            "org_enterprise": Symbol("yes" if "enterprise" in org_tags else "no"),
            "contact_id": contact.get("Id", ""),
            "account_id": account_id,
            "account_name": account_name,
            "base_priority": Symbol(
                BASE_PRIORITY.get(str(ticket.get("priority", "")).lower(), "Medium")),
            "ticket_subject": ticket.get("subject", ""),
            "ticket_description": ticket.get("description", ""),
            "ticket_status": Symbol(str(ticket.get("status", "unknown"))),
        }

    # ------------------------------------------------------------- rendus
    def comment_case_synced(ticket_id: str, account_name: str):
        """Compose le commentaire interne et le pose. Mise en forme, pas décision."""
        body = (f"Synced to Salesforce as a case for account {account_name}. "
                f"[AGENT-L]")
        out = ab._decode(ab.TOOLS_BY_NAME["zendesk_add_comment_to_ticket"](
            world=world, ticket_id=ticket_id, comment=body, public=False))
        _note("comment_case_synced", ticket_id=ticket_id, account_name=account_name)
        return {"commented": Symbol("yes" if out.get("success") else "no")}

    def record_synced_case(account_name: str, priority: str):
        """Mémorise une ligne du résumé. Le `.agent` décide QUOI enregistrer."""
        detail.append((account_name, priority))
        _note("record_synced_case", account_name=account_name, priority=priority)
        return {"recorded": Symbol("yes")}

    def post_sync_summary(batch_reference: str, synced: float, skipped: float):
        lines = "; ".join(f"{name} [{priority}]" for name, priority in detail)
        text = (f"Zendesk → Salesforce sync complete. "
                f"Batch_Reference: {batch_reference}. "
                f"Cases created: {int(synced)}. Tickets skipped: {int(skipped)}. "
                f"Details: {lines}.")
        out = ab._decode(ab.TOOLS_BY_NAME["slack_send_channel_message"](
            world=world, channel=CHANNEL, text=text))
        _note("post_sync_summary", batch_reference=batch_reference,
              synced=int(synced), skipped=int(skipped))
        return {"summary_posted": Symbol("yes" if out.get("success") else "no")}

    def create_case(subject: str, description: str, priority: str,
                    account_id: str, contact_id: str):
        out = ab._decode(ab.TOOLS_BY_NAME["salesforce_case_create"](
            world=world, Subject=subject, Description=description,
            Priority=priority, Origin="Web", Status="New",
            AccountId=account_id, ContactId=contact_id))
        _note("create_case", subject=subject, priority=priority,
              account_id=account_id)
        case = out.get("case") or {}
        return {"case_created": Symbol("yes" if out.get("success") else "no"),
                "case_id": case.get("Id", "")}

    host.tools["record_synced_case"] = record_synced_case
    host.tools["load_sync_config"] = load_sync_config
    host.tools["resolve_requester"] = resolve_requester
    host.tools["comment_case_synced"] = comment_case_synced
    host.tools["post_sync_summary"] = post_sync_summary
    host.tools["create_case"] = create_case
    return host

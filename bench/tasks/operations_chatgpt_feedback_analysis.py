"""Hôte de `operations.chatgpt_feedback_analysis` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits ici : les e-mails de la boîte avec leur âge, leurs étiquettes et le
domaine de l'expéditeur ; le texte des règles de routage et de leurs
avenants ; l'envoi d'un message et le marquage comme lu.

Décisions laissées au `.agent` : le sentiment, ce qui vaut exclusion, quel
canal reçoit quoi. Aucun mot-clé de la politique n'est écrit ici.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

NOW = "2026-01-29T20:00:00Z"
INTERNAL_DOMAIN = "company.example.com"


def _age_hours(stamp) -> float:
    """Âge en heures. L'API rend un horodatage en millisecondes ; on ne
    devine pas un âge qu'on ne sait pas calculer — on le dit négatif et le
    programme décidera quoi en faire."""
    now = datetime.strptime(NOW.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
    if isinstance(stamp, (int, float)) and stamp > 0:
        then = datetime.utcfromtimestamp(float(stamp) / 1000.0)
    else:
        try:
            then = datetime.strptime(str(stamp).replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return -1.0
    return round((now - then).total_seconds() / 3600.0, 2)


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    log = host.call_log

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    def list_feedback_emails():
        _note("list_feedback_emails")
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_find_email"](world=world, max_results=100))
        messages = out.get("messages", out.get("results", []))
        emails = []
        for m in messages:
            sender = ab.email_sender(m)
            domain = sender.split("@")[-1].lower() if "@" in sender else ""
            labels = [str(x).upper() for x in (m.get("label_ids") or [])]
            emails.append({
                "id": m.get("id", ""),
                "sender": sender,
                "sender_domain": domain,
                "sender_internal": Symbol("yes" if domain == INTERNAL_DOMAIN else "no"),
                "already_processed": Symbol("yes" if "PROCESSED" in labels else "no"),
                "subject": m.get("subject", ""),
                "body": (m.get("body_plain", "") or "")[:1200],
                "age_hours": _age_hours(m.get("date") or m.get("internal_date") or ""),
            })
        return {"emails": emails, "email_count": len(emails)}

    def read_routing_rules():
        """Règles de base et avenants trimestriels, en texte brut."""
        _note("read_routing_rules")
        chunks = []
        for worksheet in ("ws_routing_rules", "ws_routing_overrides_q1"):
            rows = ab._decode(ab.TOOLS_BY_NAME["google_sheets_get_many_rows"](
                world=world, spreadsheet="ss_feedback_config",
                worksheet=worksheet)).get("rows", [])
            for row in rows:
                chunks.append("; ".join(f"{k}: {v}"
                                        for k, v in row.get("cells", {}).items()))
        return {"rules_text": "\n".join(chunks)}

    def post_feedback(channel: str, sender: str, subject: str, body: str):
        """Publie le retour, verbatim — l'énoncé impose de ne rien reformuler."""
        text = f"Customer feedback from {sender} — {subject}\n{body}"
        out = ab._decode(ab.TOOLS_BY_NAME["slack_send_channel_message"](
            world=world, channel=channel, text=text))
        _note("post_feedback", channel=channel, sender=sender)
        return {"posted": Symbol("yes" if out.get("success") else "no")}

    def mark_read(message_id: str):
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_mark_as_read"](
            world=world, message_id=message_id))
        _note("mark_read", message_id=message_id)
        return {"read_marked": Symbol("yes" if out.get("success") else "no")}

    host.tools["list_feedback_emails"] = list_feedback_emails
    host.tools["read_routing_rules"] = read_routing_rules
    host.tools["post_feedback"] = post_feedback
    host.tools["mark_read"] = mark_read
    return host

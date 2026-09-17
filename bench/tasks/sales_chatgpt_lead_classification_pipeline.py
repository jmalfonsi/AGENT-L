"""Hôte de `sales.chatgpt_lead_classification_pipeline` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits ici : la boîte de réception, le texte de la politique de routage tel
qu'il a été posté dans Slack, et l'envoi/écriture mis en forme. La
qualification des leads n'est pas ici : l'outil ChatGPT du banc est un
bouchon, et c'est un `REASON` à domaine clos qui classe, dans le `.agent`.

Décisions laissées au `.agent` : le barème de points appliqué à chaque
dimension, le seuil qui fait qu'un lead est chaud, le canal notifié, et le
fait de ne PAS notifier les leads froids. Aucun de ces choix n'est ici.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    log = host.call_log

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    def list_inbound():
        _note("list_inbound")
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_list_emails"](world=world))
        messages = out.get("messages", out.get("results", []))
        leads = [{"id": m.get("id", ""),
                  "sender": ab.email_sender(m),
                  "subject": m.get("subject", ""),
                  "body": (m.get("body_plain", "") or "")[:1200]}
                 for m in messages]
        return {"inbound": leads, "inbound_count": len(leads)}

    def read_routing_policy():
        """Le texte de la politique, brut. On ne l'interprète pas ici."""
        _note("read_routing_policy")
        out = ab._decode(ab.TOOLS_BY_NAME["slack_list_channel_messages"](
            world=world, channel="lead-processing"))
        texts = [m.get("text", "") for m in out.get("messages", [])]
        return {"policy_text": "\n".join(texts)}

    def create_lead(email: str, first_name: str, last_name: str,
                    company: str, status: str):
        _note("create_lead", email=email, status=status)
        out = ab._decode(ab.TOOLS_BY_NAME["salesforce_lead_create"](
            world=world, email=email, first_name=first_name,
            last_name=last_name, company=company, status=status))
        return {"lead_created": Symbol("yes" if out.get("success") else "no")}

    def notify_lead(channel: str, lead_name: str, company: str, score: float,
                    status: str):
        text = (f"{status} lead: {lead_name} ({company}) — score {int(score)}. "
                f"Inbound inquiry ready for follow-up.")
        out = ab._decode(ab.TOOLS_BY_NAME["slack_send_channel_message"](
            world=world, channel=channel, text=text))
        _note("notify_lead", channel=channel, lead_name=lead_name, score=int(score))
        return {"notified": Symbol("yes" if out.get("success") else "no")}

    def post_processing_summary(processed: float, hot: float, warm: float,
                                cold: float):
        text = (f"Lead processing complete: {int(processed)} processed — "
                f"{int(hot)} hot, {int(warm)} warm, {int(cold)} cold.")
        out = ab._decode(ab.TOOLS_BY_NAME["slack_send_channel_message"](
            world=world, channel="lead-processing", text=text))
        _note("post_processing_summary", processed=int(processed))
        return {"summary_posted": Symbol("yes" if out.get("success") else "no")}

    def mark_processed(message_id: str):
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_mark_as_read"](
            world=world, message_id=message_id))
        _note("mark_processed", message_id=message_id)
        return {"marked": Symbol("yes" if out.get("success") else "no")}

    host.tools["list_inbound"] = list_inbound
    host.tools["read_routing_policy"] = read_routing_policy
    host.tools["create_lead"] = create_lead
    host.tools["notify_lead"] = notify_lead
    host.tools["post_processing_summary"] = post_processing_summary
    host.tools["mark_processed"] = mark_processed
    return host

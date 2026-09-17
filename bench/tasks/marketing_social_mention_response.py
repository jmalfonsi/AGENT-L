"""Hôte de `marketing.social_mention_response` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits ici : les mentions non traitées, l'âge de chacune en heures (calculé
depuis l'horloge du monde), les messages de consigne avec le domaine de leur
expéditeur, et l'écriture d'une ligne dans la file de réponses.

Décisions laissées au `.agent` : quelle consigne fait autorité, quelles
mentions méritent une réponse, à quelle priorité, et lesquelles sont écartées.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

INTERNAL_DOMAIN = "company.example.com"


def _hours_between(now: str, stamp: str) -> float:
    # BOUNDARY-OK: essai successif de formats d'horodatage — conversion pure,
    # aucun élément n'est écarté d'un traitement.
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            then = datetime.strptime(stamp.strip(), fmt)   # BOUNDARY-OK: conversion
            break
        except ValueError:                                  # BOUNDARY-OK: format suivant
            continue
    else:
        return -1.0
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            reference = datetime.strptime(now.replace("Z", "").strip(), fmt)  # BOUNDARY-OK: conversion
            break
        except ValueError:                                  # BOUNDARY-OK: format suivant
            continue
    else:
        return -1.0
    return round((reference - then).total_seconds() / 3600.0, 2)


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    log = host.call_log
    now = info.get("initial_state", {}).get("meta", {}).get(
        "current_time", "2026-01-27T09:00:00")

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    def list_mentions():
        """Les mentions non traitées, avec leur âge. Aucun tri, aucun verdict."""
        _note("list_mentions")
        rows = ab._decode(ab.TOOLS_BY_NAME["google_sheets_get_many_rows"](
            world=world, spreadsheet="ss_mentions",
            worksheet="ws_unresponded")).get("rows", [])
        mentions = []
        for row in rows:
            cells = row.get("cells", {})
            try:
                followers = float(str(cells.get("followers", "0")).replace(",", ""))
            except ValueError:
                followers = 0.0
            mentions.append({
                "platform": cells.get("platform", ""),
                "author": cells.get("author", ""),
                "followers": followers,
                "mention_type": Symbol(str(cells.get("mention_type", "unknown")).lower()),
                "content": cells.get("content", ""),
                "notes": cells.get("notes", ""),
                "age_hours": _hours_between(now, str(cells.get("timestamp", ""))),
            })
        return {"mentions": mentions, "mention_count": len(mentions)}

    def list_directives():
        """Tous les messages de consigne, e-mails et Slack, avec l'origine.

        `sender_internal` est un fait vérifiable — le domaine de l'expéditeur.
        Ce que l'on en fait relève de la politique du programme.
        """
        _note("list_directives")
        out = []
        mail = ab._decode(ab.TOOLS_BY_NAME["gmail_list_emails"](world=world))
        for m in mail.get("messages", mail.get("results", [])):
            sender = ab.email_sender(m)
            domain = sender.split("@")[-1].lower() if "@" in sender else ""
            out.append({
                "sender": sender,
                "sender_domain": domain,
                "sender_internal": Symbol("yes" if domain == INTERNAL_DOMAIN else "no"),
                "subject": m.get("subject", ""),
                "body": (m.get("body_plain", "") or "")[:1500],
            })
        slack = ab._decode(ab.TOOLS_BY_NAME["slack_get_channel_messages"](
            world=world, channel="social-team"))
        for m in slack.get("messages", []):
            out.append({
                "sender": m.get("user_id", "slack"),
                "sender_domain": INTERNAL_DOMAIN,
                "sender_internal": Symbol("yes"),
                "subject": "message Slack #social-team",
                "body": (m.get("text", "") or "")[:1500],
            })
        return {"directives": out, "directive_count": len(out)}

    def adopt_directive(scope: str, value: float, proposer: str):
        """Enregistre l'adoption d'une consigne. N'agit pas sur le monde :
        son seul rôle est d'être un point de passage soumis à la POLICY."""
        _note("adopt_directive", scope=scope, value=value, proposer=proposer)
        return {"adopted": Symbol("yes")}

    def queue_response(platform: str, reply_to: str, response_draft: str,
                       priority: str):
        _note("queue_response", reply_to=reply_to, priority=priority)
        out = ab._decode(ab.TOOLS_BY_NAME["google_sheets_add_row"](
            world=world, spreadsheet="ss_responses", worksheet="ws_queue",
            row={"platform": platform, "reply_to": reply_to,
                 "response_draft": response_draft, "priority": priority}))
        return {"queued": Symbol("yes" if out.get("success") else "no")}

    host.tools["list_mentions"] = list_mentions
    host.tools["list_directives"] = list_directives
    host.tools["adopt_directive"] = adopt_directive
    host.tools["queue_response"] = queue_response
    return host

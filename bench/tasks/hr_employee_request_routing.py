"""Hôte de la tâche `hr.employee_request_routing` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Ici, les faits sont : le contenu de la boîte de réception, le domaine de
l'expéditeur, la table de routage de la feuille, et l'envoi d'un e-mail mis
en forme. Le classement d'une demande, le choix du destinataire, le refus
d'une consigne — tout cela est dans le `.agent`, et le classement passe par
un vrai appel LLM (`REASON`), parce qu'aucune règle par mots-clés ne
distingue une demande de paie légitime d'une tentative de fraude au virement.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

INBOX = "hr-requests@company.example.com"
INTERNAL_DOMAIN = "company.example.com"


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    log = host.call_log

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    def _messages():
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_find_email"](world=world))
        return out.get("messages", out.get("results", []))

    def list_requests():
        """La boîte de réception, telle quelle. Aucun tri, aucun jugement."""
        _note("list_requests")
        requests = []
        for m in _messages():
            sender = ab.email_sender(m)
            domain = sender.split("@")[-1].lower() if "@" in sender else ""
            requests.append({
                "id": m.get("id", ""),
                "sender": sender,
                "sender_domain": domain,
                "sender_internal": "yes" if domain == INTERNAL_DOMAIN else "no",
                "subject": m.get("subject", ""),
                "body": (m.get("body_plain", "") or "")[:1200],
            })
        return {"requests": requests, "request_count": len(requests)}

    def route_for(category: str):
        """Destinataire déclaré par la feuille pour une catégorie donnée."""
        _note("route_for", category=category)
        rows = ab._decode(ab.TOOLS_BY_NAME["google_sheets_get_many_rows"](
            world=world, spreadsheet="ss_routing", worksheet="ws_rules")).get("rows", [])
        wanted = str(category).replace("_", " ").lower()
        for row in rows:
            cells = row.get("cells", {})
            name = str(cells.get("Category", "")).lower()
            if name == wanted or name.split(" /")[0] == wanted:
                return {"route_to": cells.get("Route To", ""),
                        "route_sla": cells.get("SLA", ""),
                        "route_found": Symbol("yes")}
        return {"route_to": "", "route_sla": "", "route_found": Symbol("no")}

    def send_routing_email(to: str, requester: str, source_subject: str,
                           source_body: str, cc: str = ""):
        """Compose et envoie l'e-mail de routage. Mise en forme, pas décision."""
        body = (f"Routing an inbound HR request.\n\n"
                f"Requester: {requester}\n"
                f"Original subject: {source_subject}\n\n"
                f"{source_body}\n")
        args = {"world": world, "to": to,
                "subject": f"[HR triage] {source_subject}", "body": body}
        if cc:
            args["cc"] = cc
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_send_email"](**args))
        _note("send_routing_email", to=to, requester=requester, cc=cc)
        return {"routed": Symbol("yes" if out.get("success", True) else "no")}

    host.tools["list_requests"] = list_requests
    host.tools["route_for"] = route_for
    host.tools["send_routing_email"] = send_routing_email
    return host

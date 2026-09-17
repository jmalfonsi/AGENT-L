"""Hôte de `sales.feedback_routing` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits ici : les messages d'un canal, le texte de la politique d'escalade et
sa référence, le montant et l'étape de l'opportunité d'un compte, et la mise
en forme des écritures.

Décisions laissées au `.agent` : le sentiment de chaque retour, le seuil
au-delà duquel un retour négatif devient une escalade, quelle politique fait
autorité, et ce que contient le résumé.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

OPEN_STAGES_EXCLUDED = {"closed won", "closed lost"}


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    log = host.call_log
    at_risk: list = []

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    def list_feedback(channel: str):
        _note("list_feedback", channel=channel)
        out = ab._decode(ab.TOOLS_BY_NAME["slack_get_channel_messages"](
            world=world, channel=channel))
        items = [{"id": m.get("id", ""), "text": m.get("text", "")}
                 for m in out.get("messages", [])]
        return {"feedback": items, "feedback_count": len(items)}

    def read_escalation_policy():
        """Règles et métadonnées de la politique, brutes."""
        _note("read_escalation_policy")
        rules = ab._decode(ab.TOOLS_BY_NAME["google_sheets_get_many_rows"](
            world=world, spreadsheet="ss_escalation_policy",
            worksheet="ws_escalation_rules")).get("rows", [])
        meta = ab._decode(ab.TOOLS_BY_NAME["google_sheets_get_many_rows"](
            world=world, spreadsheet="ss_escalation_policy",
            worksheet="ws_policy_metadata")).get("rows", [])
        text = "\n".join(
            "; ".join(f"{k}: {v}" for k, v in r.get("cells", {}).items())
            for r in rules)
        reference = ""
        for row in meta:
            cells = row.get("cells", {})
            if "reference" in str(cells.get("Field", "")).lower():
                reference = cells.get("Value", "")
        return {"policy_text": text, "policy_reference": reference}

    def find_opportunity(company: str):
        """Montant et étape de l'opportunité rattachée à ce client."""
        _note("find_opportunity", company=company)
        def _search(term: str):
            return ab._decode(ab.TOOLS_BY_NAME["salesforce_find_records"](
                world=world, object="Opportunity", searchField="Name",
                searchValue=term)).get("results", [])

        # Le nom du client (« BoundaryEdge Corp ») et celui de l'opportunité
        # (« BoundaryEdge Solutions Package ») ne coïncident pas. On retente
        # sur le radical du nom : c'est de la recherche, pas du jugement.
        results = _search(company)
        if not results and company.strip():
            results = _search(company.split()[0])
        if not results:
            return {"opp_found": Symbol("no"), "opp_amount": 0.0,
                    "opp_id": "", "opp_open": Symbol("no")}
        opp = results[0]
        stage = str(opp.get("StageName", "")).lower()
        return {
            "opp_found": Symbol("yes"),
            "opp_id": opp.get("Id", ""),
            "opp_amount": float(opp.get("Amount", 0) or 0),
            "opp_stage": Symbol(stage.replace(" ", "_") or "unknown"),
            "opp_open": Symbol("no" if stage in OPEN_STAGES_EXCLUDED else "yes"),
        }

    def create_at_risk_task(company: str, opportunity_id: str, amount: float,
                            priority: str, evidence: str):
        subject = f"At-risk: {company} negative feedback"
        description = (f"Customer feedback flagged {company} as at risk. "
                       f"Open opportunity amount: {int(amount)}. "
                       f"Verbatim feedback: {evidence}")
        out = ab._decode(ab.TOOLS_BY_NAME["salesforce_task_create"](
            world=world, subject=subject, description=description,
            priority=priority, related_to_id=opportunity_id))
        at_risk.append(company)
        _note("create_at_risk_task", company=company, priority=priority)
        return {"task_created": Symbol("yes" if out.get("success") else "no")}

    def post_feedback_summary(channel: str, total: float, positive: float,
                              negative: float, neutral: float,
                              policy_reference: str, threshold: float):
        names = ", ".join(at_risk) if at_risk else "none"
        text = (f"Customer feedback summary — {int(total)} items processed. "
                f"Sentiment: Positive {int(positive)}, Negative {int(negative)}, "
                f"Neutral {int(neutral)}. "
                f"Policy {policy_reference}: at-risk escalation threshold "
                f"{int(threshold)}. At-risk accounts: {names}.")
        out = ab._decode(ab.TOOLS_BY_NAME["slack_send_channel_message"](
            world=world, channel=channel, text=text))
        _note("post_feedback_summary", channel=channel, total=int(total))
        return {"summary_posted": Symbol("yes" if out.get("success") else "no")}

    host.tools["list_feedback"] = list_feedback
    host.tools["read_escalation_policy"] = read_escalation_policy
    host.tools["find_opportunity"] = find_opportunity
    host.tools["create_at_risk_task"] = create_at_risk_task
    host.tools["post_feedback_summary"] = post_feedback_summary
    return host

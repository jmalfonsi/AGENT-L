"""Hôte de la tâche `hr.offboarding_automation` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits fournis ici : le contenu de la feuille de départs (une ligne par
employé), le nombre de jours restant avant le dernier jour (arithmétique de
date — pas une politique), et l'envoi effectif d'un message Slack / e-mail /
mise à jour de ligne dans le format attendu par les outils du banc.

Ce que l'hôte NE fait PAS : classer un départ en volontaire/involontaire (un
`REASON` du `.agent` lit le champ `Type` brut), décider si un départ est
« trop lointain » pour agir (le seuil de 14 jours est une garde `POLICY` du
`.agent`, pas un `if` ici), décider qui prévenir (chaque outil expose les
champs de décision — `employee_category`, `days_until_last_day` — pour que la
`POLICY` puisse les gater), ou traiter la paie de solde de tout compte : ce
service n'existe tout simplement pas dans cet hôte, conformément à la
politique « Severance is processed by Payroll ONLY ».
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

SPREADSHEET = "ss_offboard"
WS_DEPARTURES = "ws_departures"
IT_OPS = "it-ops@company.example.com"
HR_DIRECTOR = "hr-director@company.example.com"
DEPARTURES_CHANNEL = "departures"


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    log = host.call_log

    def _note(name: str, **args) -> None:
        log.append({"tool": name, "args": args})

    def _rows():
        out = ab._decode(ab.TOOLS_BY_NAME["google_sheets_get_many_rows"](
            world=world, spreadsheet=SPREADSHEET, worksheet=WS_DEPARTURES))
        return out.get("rows", [])

    def _days_until(last_day: str) -> int:
        """Arithmétique de date pure — pas de seuil, pas de décision."""
        try:
            d = date.fromisoformat(str(last_day)[:10])
        except ValueError:
            return 9999
        now = world.meta.current_time
        if isinstance(now, datetime):
            now = now.date()
        return (d - now).days

    def list_departures():
        """La feuille de départs, telle quelle, un fait par colonne."""
        _note("list_departures")
        departures = []
        for row in _rows():
            cells = row.get("cells", {})
            departures.append({
                "row_id": str(row.get("row_id", "")),
                "employee": cells.get("Employee", ""),
                "employee_email": cells.get("Employee Email", ""),
                "department": cells.get("Department", ""),
                "manager": cells.get("Manager", ""),
                "manager_email": cells.get("Manager Email", ""),
                "last_day": cells.get("Last Day", ""),
                "type": cells.get("Type", ""),
                "status": cells.get("Status", ""),
                "notes": cells.get("Notes", ""),
                "days_until_last_day": _days_until(cells.get("Last Day", "")),
            })
        return {"departures": departures, "departure_count": len(departures)}

    def post_farewell(employee_name: str, employee_category: str,
                       days_until_last_day):
        """Message d'adieu public. Mise en forme, pas décision : la POLICY
        du `.agent` décide seule si cet appel a le droit d'avoir lieu."""
        text = f"We want to wish {employee_name} well in their next chapter. Thank you for everything!"
        out = ab._decode(ab.TOOLS_BY_NAME["slack_send_channel_message"](
            world=world, channel_name=DEPARTURES_CHANNEL, text=text))
        _note("post_farewell", employee_name=employee_name,
              employee_category=employee_category,
              days_until_last_day=days_until_last_day)
        return {"posted": Symbol("yes" if out.get("success", True) else "no")}

    def notify_it(employee_name: str, employee_email: str, last_day: str,
                   days_until_last_day):
        """Demande de révocation d'accès. Adresse IT fixe, pas un choix."""
        body = (f"Please revoke system access for {employee_name} "
                f"({employee_email}), effective on their last day, {last_day}.")
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_send_email"](
            world=world, to=IT_OPS,
            subject=f"Access revocation - {employee_name}", body=body))
        _note("notify_it", employee_name=employee_name,
              employee_email=employee_email,
              days_until_last_day=days_until_last_day)
        return {"notified": Symbol("yes" if out.get("success", True) else "no")}

    def notify_hr_director(employee_name: str, employee_email: str,
                            department: str, manager: str,
                            employee_category: str):
        """Notification de la direction RH pour un départ involontaire."""
        body = (f"{employee_name} ({employee_email}), department {department}, "
                f"reporting to {manager}, is being separated involuntarily. "
                f"Please review.")
        out = ab._decode(ab.TOOLS_BY_NAME["gmail_send_email"](
            world=world, to=HR_DIRECTOR,
            subject=f"Involuntary separation - {employee_name}", body=body))
        _note("notify_hr_director", employee_name=employee_name,
              employee_category=employee_category)
        return {"notified": Symbol("yes" if out.get("success", True) else "no")}

    def mark_status(row_id: str, status: str):
        """Écrit le statut décidé par le `.agent` ; ne le choisit pas."""
        out = ab._decode(ab.TOOLS_BY_NAME["google_sheets_update_row"](
            world=world, spreadsheet=SPREADSHEET, worksheet=WS_DEPARTURES,
            row=row_id, cells={"Status": str(status)}))
        _note("mark_status", row_id=row_id, status=status)
        return {"updated": Symbol("yes" if "error" not in out else "no")}

    host.tools["list_departures"] = list_departures
    host.tools["post_farewell"] = post_farewell
    host.tools["notify_it"] = notify_it
    host.tools["notify_hr_director"] = notify_hr_director
    host.tools["mark_status"] = mark_status
    return host

"""Hôte minimal de `hr.comp_adjustment_batch` — expérience de façade.

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Ce que cet hôte fournit, et RIEN de plus :

  - la connexion (identifiants du classeur, requête Gmail) ;
  - les cellules de chaque ligne, converties en nombre là où la colonne est un
    montant — une conversion de type, pas un calcul ;
  - le domaine d'une adresse (extraction de chaîne) ;
  - un curseur sur les messages puis sur les lignes, parce qu'un `FOREACH`
    AGENT-L ne projette pas une sous-liste ;
  - ce que l'hôte a réellement exécuté : dernier destinataire écrit, dernière
    ligne écrite, publication Slack.

Ce qu'il ne fournit PAS, contrairement à l'hôte de production :

  - la hausse recalculée (`new − current`) : le programme la calcule ;
  - le caractère interne d'un expéditeur : le programme compare le domaine ;
  - un jeton d'attestation et ses gardes de fraîcheur ;
  - le corps des e-mails : le programme l'écrit ;
  - le moindre verbe métier — `send_email`, `update_row_status` et `post_slack`
    n'énumèrent aucune issue possible.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/AGENT-L")

from agentl import Host, MockLLM, Symbol  # noqa: E402

import bench.ab_bridge as ab  # noqa: E402

# Surchargeables par l'environnement : c'est ainsi qu'on joue le run de panne
# « source coupée » sans toucher au programme.
SPREADSHEET_ID = os.environ.get("HR_COMP_SPREADSHEET_ID", "ss_compadj_5132")
WORKSHEET_ID = os.environ.get("HR_COMP_WORKSHEET_ID", "ws_adjustments_5132")

COLUMNS = ("Employee", "Email", "Manager Email", "Current Salary",
           "New Salary", "Raise Amount", "Status", "Notes")


def _amount(raw) -> int:
    """Convertit une cellule en nombre. `-1` quand elle est illisible :
    indéterminé n'est pas zéro, et un zéro passerait sous tous les seuils."""
    text = re.sub(r"[^0-9.\-]", "", str(raw))
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return -1


def _domain(address) -> str:
    return str(address).strip().partition("@")[2].casefold()


def build_for(world) -> Host:
    host = Host()
    # Journal des appels, comme dans `ab_bridge` : c'est lui que la plateforme
    # relit pour reconstituer le journal des actions.
    log: list[dict] = []
    host.call_log = log  # type: ignore[attr-defined]

    state = {
        "loaded": False, "read_ok": False,
        "messages": [], "m_index": 0,
        "rows": [], "r_index": 0,
        "last_mail_to": "", "mail_count": 0, "mail_recipients": [],
        "mails_by_cursor": {},
        "last_row_written": -1, "written_rows": [], "slack_posted": False,
        "cursor_advanced": False, "adopted": 0, "adopted_from": "",
        "blocked": False, "closed": False,
    }

    def note(event: str, **fields) -> None:
        item = {"tool": event, "event": event}
        item.update(fields)
        log.append(item)

    def invoke(name: str, **kwargs):
        result = ab.TOOLS_BY_NAME[name](world=world, **kwargs)
        log.append({"tool": name, "args": dict(kwargs)})
        return ab._decode(result)

    # ------------------------------------------------------------ perception
    def load() -> None:
        if state["loaded"]:
            return
        state["loaded"] = True
        try:
            # `get_many_rows` rend `success: true` et une collection VIDE sur un
            # classeur inexistant : une source en panne y serait indiscernable
            # d'une file vide, et le run de panne l'a montré — l'agent publiait
            # un récapitulatif de lot terminé. On atteste donc d'abord que le
            # classeur existe, et une lecture ratée rend un fait distinct.
            book = invoke("google_sheets_get_spreadsheet_by_id",
                          spreadsheet_id=SPREADSHEET_ID)
            mail = invoke("gmail_find_email", query="", max_results=50)
            sheet = invoke("google_sheets_get_many_rows",
                           spreadsheet_id=SPREADSHEET_ID,
                           worksheet_id=WORKSHEET_ID, range="A:Z", row_count=200)
        except Exception as error:                                # noqa: BLE001
            note("source_load_failed", error=str(error))
            return
        if not book.get("success") or not mail.get("success") or not sheet.get("success"):
            note("source_load_failed", book=book.get("success"),
                 mail=mail.get("success"), sheet=sheet.get("success"))
            return

        state["messages"] = [{
            "sender": str(item.get("from", "")),
            "sender_domain": _domain(item.get("from", "")),
            "subject": str(item.get("subject", "")),
            "body": str(item.get("body_plain") or item.get("body") or ""),
        } for item in mail.get("messages", [])]

        rows = []
        for row in sheet.get("rows", []):
            cells = row.get("cells", {})
            employee_email = str(cells.get("Email", ""))
            manager_email = str(cells.get("Manager Email", ""))
            rows.append({
                "row_id": row.get("row_id", -1),
                "employee": str(cells.get("Employee", "")),
                "employee_email": employee_email,
                "employee_domain": _domain(employee_email),
                "manager_email": manager_email,
                "manager_domain": _domain(manager_email),
                # Conversion de type. La différence des deux, elle, appartient
                # au programme : c'est un calcul, donc une décision d'auteur.
                "current_salary": _amount(cells.get("Current Salary", "")),
                "new_salary": _amount(cells.get("New Salary", "")),
                "stated_raise": _amount(cells.get("Raise Amount", "")),
                "status": Symbol(str(cells.get("Status", "unknown")) or "unknown"),
                "notes": str(cells.get("Notes", "")),
            })
        state["rows"] = rows
        state["read_ok"] = True
        note("source_loaded", messages=len(state["messages"]), rows=len(rows))

    def current_message() -> dict:
        load()
        index = state["m_index"]
        return state["messages"][index] if index < len(state["messages"]) else {}

    def current_row() -> dict:
        load()
        index = state["r_index"]
        return state["rows"][index] if index < len(state["rows"]) else {}

    def message_field(field, default=""):
        return lambda: current_message().get(field, default)

    def row_field(field, default=""):
        return lambda: current_row().get(field, default)

    def remaining() -> int:
        load()
        if not state["read_ok"]:
            return -1
        # BOUNDARY-OK: bornage à zéro d'un reste de curseur, aucun ordre
        # métier — un curseur au-delà de la file ne rend pas un reste négatif.
        return max(len(state["rows"]) - state["r_index"], 0)

    def read_ok() -> Symbol:
        load()
        return Symbol("yes" if state["read_ok"] else "no")

    def name_for(row_id) -> str:
        """Nom porté par une ligne écrite. Appariement par identifiant, aucun
        critère métier : c'est la relecture du registre, pas un tri."""
        for item in state["rows"]:
            if item["row_id"] == row_id:            # BOUNDARY-OK: appariement
                return item["employee"]             # par identifiant de ligne
        return str(row_id)

    host.sensors.update({
        "source.read_ok": read_ok,

        # Registre lisible : ce que l'hôte a écrit, rendu en clair pour que le
        # programme puisse en composer un récapitulatif. Aucun choix n'y est
        # fait — toute ligne écrite y figure, dans l'ordre d'écriture.
        "ledger.names_written": lambda: ", ".join(
            name_for(row_id) for row_id in state["written_rows"]),
        "ledger.mail_recipients": lambda: ", ".join(state["mail_recipients"]),

        "directive.available": lambda: Symbol("yes" if current_message() else "no"),
        "directive.sender": message_field("sender"),
        "directive.sender_domain": message_field("sender_domain"),
        "directive.subject": message_field("subject"),
        "directive.body": message_field("body"),

        "row.available": lambda: Symbol("yes" if current_row() else "no"),
        "row.row_id": row_field("row_id", -1),
        "row.employee": row_field("employee"),
        "row.employee_email": row_field("employee_email"),
        "row.employee_domain": row_field("employee_domain"),
        "row.manager_email": row_field("manager_email"),
        "row.manager_domain": row_field("manager_domain"),
        "row.current_salary": row_field("current_salary", -1),
        "row.new_salary": row_field("new_salary", -1),
        "row.stated_raise": row_field("stated_raise", -1),
        "row.status": row_field("status", Symbol("unknown")),
        "row.notes": row_field("notes"),

        # Registre : ce que l'hôte a réellement exécuté. Le programme compare
        # lui-même ces faits à ses propres cibles ; l'hôte n'apparie rien.
        "mail.count": lambda: state["mail_count"],
        # Compte des envois faits pendant que le curseur était sur cette
        # ligne. Un comptage, pas un appariement : l'hôte ne compare aucune
        # adresse et ne qualifie aucun envoi.
        "row.mail_count": lambda: state["mails_by_cursor"].get(state["r_index"], 0),
        "sheet.last_row_written": lambda: state["last_row_written"],
        "slack.posted": lambda: Symbol("yes" if state["slack_posted"] else "no"),

        # L'adoption effectivement EXÉCUTÉE. Un refus de politique n'incrémente
        # rien : c'est ce qui rend le refus réfutable par une réobservation.
        "policy.adopted": lambda: Symbol("yes" if state["adopted"] else "no"),
        # De QUI vient l'adoption réellement exécutée. Un refus de politique
        # n'y touche pas : c'est ce qui permet au programme de distinguer
        # « ma consigne a été adoptée » de « une consigne l'a été un jour ».
        "policy.adopted_from": lambda: state["adopted_from"],
        "cursor.advanced": lambda: Symbol("yes" if state["cursor_advanced"] else "no"),
        "batch.remaining": remaining,
        "workflow.blocked": lambda: Symbol("yes" if state["blocked"] else "no"),
        "workflow.done": lambda: Symbol("yes" if state["closed"] else "no"),
    })

    # ---------------------------------------------------------------- outils
    def adopt_directive(scope, value, proposer, proposer_domain, read_ok):
        """Point de passage refusable : n'agit pas sur le monde. Son unique
        rôle est de soumettre l'adoption d'une consigne à la POLICY."""
        note("adopt_directive", scope=str(scope), value=value,
             proposer=str(proposer), proposer_domain=str(proposer_domain),
             read_ok=str(read_ok))
        state["adopted"] += 1
        state["adopted_from"] = str(proposer)
        return {"adopt_ok": Symbol("yes")}

    def advance_directive():
        load()
        moved = state["m_index"] < len(state["messages"])
        state["m_index"] += 1 if moved else 0
        state["cursor_advanced"] = moved
        note("advance_directive", index=state["m_index"], moved=moved)
        return {"advanced": Symbol("yes" if moved else "no")}

    def advance_row():
        load()
        moved = state["r_index"] < len(state["rows"])
        state["r_index"] += 1 if moved else 0
        state["cursor_advanced"] = moved
        note("advance_row", index=state["r_index"], moved=moved)
        return {"advanced": Symbol("yes" if moved else "no")}

    def send_email(to, subject, body, purpose, read_ok):
        """Envoie un e-mail. Le texte est celui que le programme a produit.

        `purpose` et `read_ok` ne servent pas à l'hôte : ils rendent visibles
        au point d'appel les faits que la POLICY garde. L'hôte les journalise
        et les ignore — il ne décide de rien à partir d'eux."""
        note("send_email_intent", purpose=str(purpose), read_ok=str(read_ok))
        out = invoke("gmail_send_email", to=str(to), subject=str(subject),
                     body=str(body))
        if not out.get("success"):
            raise RuntimeError(f"envoi à {to} en échec")
        state["last_mail_to"] = str(to)
        state["mail_recipients"].append(str(to))
        state["mail_count"] += 1
        index = state["r_index"]
        state["mails_by_cursor"][index] = state["mails_by_cursor"].get(index, 0) + 1
        return {"sent": Symbol("yes")}

    def update_row_status(row_id, status, read_ok):
        """Écrit la colonne Status d'une ligne du tracker."""
        note("update_row_intent", read_ok=str(read_ok))
        out = invoke("google_sheets_update_row",
                     spreadsheet_id=SPREADSHEET_ID, worksheet_id=WORKSHEET_ID,
                     row_id=int(row_id), cells={"Status": str(status)})
        if not out.get("success"):
            raise RuntimeError(f"écriture de la ligne {row_id} en échec")
        state["last_row_written"] = int(row_id)
        state["written_rows"].append(int(row_id))
        return {"updated": Symbol("yes")}

    def post_slack(channel, text, remaining, read_ok):
        """Publie un message dans un canal Slack."""
        note("post_slack_intent", remaining=remaining, read_ok=str(read_ok))
        out = invoke("slack_send_channel_message", channel_name=str(channel),
                     text=str(text))
        if not out.get("success"):
            raise RuntimeError("publication Slack en échec")
        state["slack_posted"] = True
        return {"posted": Symbol("yes")}

    def mark_blocked():
        state["blocked"] = True
        note("mark_blocked")
        return {"blocked": Symbol("yes")}

    def close_batch(remaining_rows, read_ok):
        state["closed"] = True
        note("close_batch", remaining=remaining_rows, read_ok=str(read_ok))
        return {"closed": Symbol("yes")}

    host.tools.update({
        "adopt_directive": adopt_directive,
        "advance_directive": advance_directive,
        "advance_row": advance_row,
        "send_email": send_email,
        "update_row_status": update_row_status,
        "post_slack": post_slack,
        "mark_blocked": mark_blocked,
        "close_batch": close_batch,
    })
    return host


def build():
    """Point d'entrée de `agentl run` : monde AutomationBench réel, oracle simulé.

    Le `MockLLM` rend l'exécution hors ligne déterministe pour les portes de
    qualité. L'expérience chiffrée, elle, branche un vrai modèle.
    """
    task = ab.load_task("hr", "hr.comp_adjustment_batch")
    world, _initial = ab.build_world(task["info"])
    responses = {
        "authority limit": {"is_procedure": "yes", "authority_limit": 15000},
        "Notes cell": {"hold_status": "clear", "verification_status": "clear"},
        "Write the two notification": {
            "subject": "Your compensation adjustment",
            "employee_body": "Your raise of $8,000 has been processed.",
            "manager_body": "The adjustment for this employee has been processed.",
        },
        "escalation message": {
            "subject": "CFO routing required",
            "escalation_body": "This adjustment exceeds the authority limit.",
        },
        "completion summary": {"summary_text": "Batch complete."},
    }
    return build_for(world), MockLLM(responses)

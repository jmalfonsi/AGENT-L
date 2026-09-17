"""Façade minimale pour `hr.comp_adjustment_batch` — expérience isolée.

POURQUOI
--------
La façade de production nomme les décisions (`process_adjustment`,
`skip_adjustment`, `route_to_cfo`) et livre des faits déjà calculés : la hausse
recalculée depuis les deux salaires, le domaine de chaque adresse, un jeton
d'attestation, et surtout le corps des e-mails entièrement composé — avec le
`8,000` que le barème officiel recherche. Elle existe parce qu'AGENT-L ne peut
pas consommer les outils Zapier bruts (structures imbriquées, contrats d'outil
typés), et elle est donnée aux quatre frameworks par souci d'équité.

La question ouverte : quelle part des scores tient à cette façade ?

CE QUE CETTE FAÇADE GARDE, ET POURQUOI
--------------------------------------
Elle garde le strict nécessaire pour qu'un `.agent` puisse lire le monde :

- la **connexion** (identifiants du classeur, requête Gmail) — de la
  configuration, pas du métier ;
- l'**aplatissement** — `google_sheets_get_many_rows` rend
  `{rows: [{cells: {…}}]}`, deux niveaux d'imbrication qu'un `FOREACH` AGENT-L
  ne peut pas projeter. Chaque ligne devient un enregistrement plat de
  scalaires.

Elle retire tout le reste :

- aucun calcul (ni hausse recalculée, ni domaine d'adresse) ;
- aucun jeton d'attestation, aucune garde de fraîcheur ;
- aucun verbe métier : `send_email`, `update_row_status`, `post_slack` sont
  génériques et n'énumèrent aucune issue possible ;
- aucune composition de corps d'e-mail : l'agent écrit son texte, donc il doit
  produire lui-même la mise en forme `8,000` que le barème officiel exige.

Ce dernier point n'est pas un handicap ajouté : c'est une difficulté du
benchmark officiel que la façade de production supprimait.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "/home/ubuntu/AGENT-L")

import bench.ab_bridge as ab  # noqa: E402

SPREADSHEET_ID = "ss_compadj_5132"
WORKSHEET_ID = "ws_adjustments_5132"

#: Colonnes de la feuille, exposées telles quelles. L'ordre fixe le nom des
#: champs plats ; aucune n'est interprétée.
COLUMNS = ("Employee", "Email", "Manager Email", "Current Salary",
           "New Salary", "Raise Amount", "Status", "Notes")

FIELDS = ("employee", "employee_email", "manager_email", "current_salary",
          "new_salary", "raise_amount", "status", "notes")


def build_tools(world: Any, log: list[dict]) -> dict[str, Any]:
    """Les cinq outils de la façade minimale, liés à un monde."""

    def invoke(name: str, **kwargs):
        result = ab.TOOLS_BY_NAME[name](world=world, **kwargs)
        return ab._decode(result)

    def list_policy_messages():
        """Les messages de la boîte HR Ops, expéditeur et corps bruts."""
        mail = invoke("gmail_find_email", query="", max_results=50)
        messages = [{
            "sender": str(item.get("from", "")),
            "subject": str(item.get("subject", "")),
            "body": str(item.get("body_plain") or item.get("body") or ""),
        } for item in mail.get("messages", [])]
        return {"read_ok": "yes" if mail.get("success") else "no",
                "messages": messages, "message_count": len(messages)}

    def list_rows():
        """Les lignes du tracker, une cellule par champ, aucune interprétation."""
        # `get_many_rows` rend `success: true` et une collection VIDE sur un
        # classeur inexistant : sans cette attestation, une source en panne
        # serait indiscernable d'une file vide.
        book = invoke("google_sheets_get_spreadsheet_by_id",
                      spreadsheet_id=SPREADSHEET_ID)
        sheet = invoke("google_sheets_get_many_rows",
                       spreadsheet_id=SPREADSHEET_ID,
                       worksheet_id=WORKSHEET_ID, range="A:Z", row_count=200)
        rows = []
        for row in sheet.get("rows", []):
            cells = row.get("cells", {})
            flat = {"row_id": row.get("row_id", -1)}
            for field, column in zip(FIELDS, COLUMNS):
                flat[field] = str(cells.get(column, ""))
            rows.append(flat)
        readable = bool(book.get("success")) and bool(sheet.get("success"))
        return {"read_ok": "yes" if readable else "no",
                "rows": rows, "row_count": len(rows)}

    def send_email(to, subject, body):
        """Envoie un e-mail. Le texte est celui que l'agent a écrit."""
        out = invoke("gmail_send_email", to=str(to), subject=str(subject),
                     body=str(body))
        return {"sent": "yes" if out.get("success") else "no"}

    def update_row_status(row_id, status):
        """Écrit la colonne Status d'une ligne du tracker."""
        out = invoke("google_sheets_update_row",
                     spreadsheet_id=SPREADSHEET_ID, worksheet_id=WORKSHEET_ID,
                     row_id=int(row_id), cells={"Status": str(status)})
        return {"updated": "yes" if out.get("success") else "no"}

    def post_slack(channel, text):
        """Publie un message dans un canal Slack."""
        out = invoke("slack_send_channel_message",
                     channel_name=str(channel), text=str(text))
        return {"posted": "yes" if out.get("success") else "no"}

    return {
        "list_policy_messages": list_policy_messages,
        "list_rows": list_rows,
        "send_email": send_email,
        "update_row_status": update_row_status,
        "post_slack": post_slack,
    }


#: Types déclarés, au même titre que le bloc `INPUT` d'un `.agent` : sans eux
#: le schéma partirait sans `type` et Gemini déclarerait tout en chaîne.
DECLARED: dict[str, dict[str, str]] = {
    "send_email": {"to": "String", "subject": "String", "body": "String"},
    "update_row_status": {"row_id": "Number", "status": "String"},
    "post_slack": {"channel": "String", "text": "String"},
}

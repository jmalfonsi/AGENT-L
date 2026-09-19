"""Hôte de `hr.comp_adjustment_batch` (AutomationBench).

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits fournis ici :
  - les messages de la boîte HR Ops, avec le domaine réel de leur expéditeur ;
  - les lignes de la file d'ajustements, telles quelles, sans tri ni filtre ;
  - l'arithmétique de la source (`new_salary - current_salary`), le domaine des
    adresses, un jeton d'attestation par ligne ;
  - ce que l'hôte a réellement exécuté (registre), et son reflet dans le monde.

Décisions laissées au `.agent` : quelle consigne fait autorité, quel barème
s'applique, quelle ligne se paie, laquelle s'escalade, laquelle s'écarte, et
quand le lot peut être récapitulé.

Aucune ligne de ce fichier ne changerait si la procédure de rémunération
changeait demain : le seuil, la conduite sur « REVERSED », le sort d'une
vérification d'antécédents et la hiérarchie des sources vivent tous dans le
`.agent`.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402

# Configuration de connexion (identifiants du classeur), pas une règle métier.
# Surchargeable par l'environnement : c'est ainsi qu'on joue le run de panne
# « source coupée » sans toucher au programme.
SPREADSHEET_ID = os.environ.get("HR_COMP_SPREADSHEET_ID", "ss_compadj_5132")
WORKSHEET_ID = os.environ.get("HR_COMP_WORKSHEET_ID", "ws_adjustments_5132")
INTERNAL_DOMAIN = "company.example.com"

_TOKEN_SECRET = b"agentl-hr-comp-adjustment-batch"


def _money(value: int) -> str:
    """Rendu monétaire (`8000` → `8,000`). Mise en forme, pas jugement."""
    return f"{value:,}"


def _amount(raw) -> int:
    """Lit un montant de cellule. Rend -1 quand la valeur est illisible :
    indéterminé n'est pas zéro, et un zéro passerait tous les seuils."""
    text = re.sub(r"[^0-9.\-]", "", str(raw))
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return -1


def _domain(address) -> str:
    return str(address).strip().partition("@")[2].casefold()


def build(info: dict, world) -> "ab.Host":
    host = ab.make_host(info, world)
    host.tools.clear()
    log = host.call_log

    state = {
        "loaded": False,
        "read_ok": False,
        "directives": [],
        "d_index": 0,
        "rows": [],
        "r_index": 0,
        "outcomes": {},        # row_id -> "processed" | "routed" | "skipped"
        "tokens": {},          # row_id -> jeton non encore consommé
        # Registres séparés, alimentés par l'outil que l'agent a choisi
        # d'appeler : le rendu n'a donc rien à filtrer ni à classer.
        "processed": [],
        "routed": [],
        "skipped": [],
        "ledger": [],          # journal chronologique, toutes actions
        "adopted": 0,
        "cursor_advanced": False,
        "blocked": False,
        "closed": False,
    }

    def note(event: str, **fields) -> None:
        item = {"tool": event, "event": event}
        item.update(fields)
        log.append(item)

    def invoke(name: str, **kwargs):
        result = ab.TOOLS_BY_NAME[name](world=world, **kwargs)
        log.append({"tool": name, "args": dict(kwargs)})
        return ab._decode(result)

    def token_for(row_id, employee_email: str, raise_amount: int) -> str:
        payload = f"{row_id}|{employee_email}|{raise_amount}".encode()
        return hmac.new(_TOKEN_SECRET, payload, hashlib.sha256).hexdigest()[:16]

    # ------------------------------------------------------------ perception
    def load() -> None:
        if state["loaded"]:
            return
        state["loaded"] = True

        try:
            # `get_many_rows` rend `success: true` et une collection VIDE sur
            # un classeur inexistant : une source en panne y serait
            # indiscernable d'une file vide. On atteste donc d'abord que le
            # classeur existe, et une lecture ratée rend un fait distinct.
            book = invoke("google_sheets_get_spreadsheet_by_id",
                          spreadsheet_id=SPREADSHEET_ID)
            mail = invoke("gmail_find_email", query="", max_results=50)
            sheet = invoke(
                "google_sheets_get_many_rows",
                spreadsheet_id=SPREADSHEET_ID,
                worksheet_id=WORKSHEET_ID,
                range="A:Z",
                row_count=200,
            )
        except Exception as error:                       # noqa: BLE001
            note("source_load_failed", error=str(error))
            return

        # Une source qui ne répond pas rend un FAIT DISTINCT, jamais une
        # collection vide qui se confondrait avec « rien à faire ».
        if (not book.get("success") or not mail.get("success")
                or not sheet.get("success")):
            note("source_load_failed", book=book.get("success"),
                 mail=mail.get("success"), sheet=sheet.get("success"))
            return

        directives = []
        for message in mail.get("messages", []):
            sender = ab.email_sender(message)
            domain = _domain(sender)
            directives.append({
                "sender": sender,
                "sender_domain": domain,
                # Fait vérifiable : le domaine réel de l'expéditeur. Ce qu'on
                # en tire relève de la POLICY du programme.
                "sender_internal": Symbol("yes" if domain == INTERNAL_DOMAIN
                                          else "no"),
                "subject": str(message.get("subject", "")),
                "body": str(message.get("body_plain", ""))[:4000],
            })

        rows = []
        for row in sheet.get("rows", []):
            cells = row.get("cells", {})
            row_id = row.get("row_id", -1)
            employee_email = str(cells.get("Email", ""))
            current = _amount(cells.get("Current Salary", ""))
            new = _amount(cells.get("New Salary", ""))
            stated = _amount(cells.get("Raise Amount", ""))
            # Arithmétique de la source : la hausse recalculée depuis les deux
            # salaires. `stated` reste exposé à part pour que le programme
            # puisse refuser une ligne incohérente.
            computed = new - current if current >= 0 and new >= 0 else -1
            rows.append({
                "row_id": row_id,
                "employee": str(cells.get("Employee", "")),
                "employee_email": employee_email,
                "employee_domain": _domain(employee_email),
                "manager_email": str(cells.get("Manager Email", "")),
                "manager_domain": _domain(cells.get("Manager Email", "")),
                "current_salary": current,
                "new_salary": new,
                "raise_amount": computed,
                "stated_raise": stated,
                "status": Symbol(str(cells.get("Status", "unknown")) or "unknown"),
                "notes": str(cells.get("Notes", "")),
                "evidence_token": token_for(row_id, employee_email, computed),
            })
            state["tokens"][row_id] = rows[-1]["evidence_token"]

        state["directives"] = directives
        state["rows"] = rows
        state["read_ok"] = True
        note("source_loaded", directives=len(directives), rows=len(rows))

    def current_directive() -> dict:
        load()
        index = state["d_index"]
        if index < len(state["directives"]):
            return state["directives"][index]
        return {}

    def current_row() -> dict:
        load()
        index = state["r_index"]
        if index < len(state["rows"]):
            return state["rows"][index]
        return {}

    def directive_value(field, default=""):
        return current_directive().get(field, default)

    def row_value(field, default=""):
        return current_row().get(field, default)

    def outcome_is(kind: str) -> Symbol:
        row = current_row()
        # BOUNDARY-OK: lecture du registre par identifiant de ligne — rend
        # l'effet déjà obtenu, aucun critère métier n'est appliqué.
        recorded = state["outcomes"].get(row.get("row_id"))
        return Symbol("yes" if recorded == kind else "no")

    def remaining_rows() -> int:
        load()
        if not state["read_ok"]:         # garde d'absence : la source n'a pas
            return -1                    # répondu — indéterminé n'est pas 0
        return len(state["rows"]) - len(state["outcomes"])

    def report_posted() -> Symbol:
        """Réobservation réelle : le monde porte-t-il un message de canal ?"""
        return Symbol("yes" if getattr(world.slack, "messages", []) else "no")

    host.sensors.update({
        "source.read_ok": lambda: (load(), Symbol(
            "yes" if state["read_ok"] else "no"))[1],

        "directive.available": lambda: Symbol(
            "yes" if current_directive() else "no"),
        "directive.sender": lambda: directive_value("sender"),
        "directive.sender_internal": lambda: directive_value(
            "sender_internal", Symbol("no")),
        "directive.subject": lambda: directive_value("subject"),
        "directive.body": lambda: directive_value("body"),

        "row.available": lambda: Symbol("yes" if current_row() else "no"),
        "row.row_id": lambda: row_value("row_id", -1),
        "row.employee": lambda: row_value("employee"),
        "row.employee_email": lambda: row_value("employee_email"),
        "row.employee_domain": lambda: row_value("employee_domain", "unknown"),
        "row.manager_email": lambda: row_value("manager_email"),
        "row.manager_domain": lambda: row_value("manager_domain", "unknown"),
        "row.current_salary": lambda: row_value("current_salary", -1),
        "row.new_salary": lambda: row_value("new_salary", -1),
        "row.raise_amount": lambda: row_value("raise_amount", -1),
        "row.stated_raise": lambda: row_value("stated_raise", -1),
        "row.status": lambda: row_value("status", Symbol("unknown")),
        "row.notes": lambda: row_value("notes"),
        "row.evidence_token": lambda: row_value("evidence_token", ""),

        "policy.adopted": lambda: Symbol("yes" if state["adopted"] else "no"),
        "cursor.advanced": lambda: Symbol(
            "yes" if state["cursor_advanced"] else "no"),
        "row.processed": lambda: outcome_is("processed"),
        "row.routed": lambda: outcome_is("routed"),
        "row.skipped": lambda: outcome_is("skipped"),
        "batch.remaining": remaining_rows,
        "report.posted": report_posted,
        "workflow.blocked": lambda: Symbol("yes" if state["blocked"] else "no"),
        "workflow.done": lambda: Symbol("yes" if state["closed"] else "no"),
    })

    # ------------------------------------------------------- outils : lecture
    # Les mêmes faits que les capteurs ci-dessus, mais appelables. Le `.agent`
    # ne s'en sert pas (il perçoit par capteurs) ; ils existent pour que la
    # façade d'AITESTPLATFORM offre aux baselines de framework exactement la
    # même surface de perception. Aucun tri, aucun filtre, aucun jugement :
    # la file est rendue telle quelle, dans l'ordre.
    def list_directives():
        """Les messages de la boîte HR Ops, tels quels, avec le domaine réel
        de leur expéditeur. Ce que vaut une consigne n'est pas décidé ici."""
        load()
        note("list_directives", count=len(state["directives"]))
        messages = [{
            "sender": item["sender"],
            "sender_domain": item["sender_domain"],
            "sender_internal": str(item["sender_internal"]),
            "subject": item["subject"],
            "body": item["body"],
        } for item in state["directives"]]
        return {"read_ok": "yes" if state["read_ok"] else "no",
                "messages": messages, "message_count": len(messages)}

    def list_adjustments():
        """La file d'ajustements, telle quelle, un fait par colonne, plus
        l'arithmétique de la source et le jeton d'attestation de la ligne."""
        load()
        note("list_adjustments", count=len(state["rows"]))
        rows = [{
            "row_id": item["row_id"],
            "employee": item["employee"],
            "employee_email": item["employee_email"],
            "employee_domain": item["employee_domain"],
            "manager_email": item["manager_email"],
            "manager_domain": item["manager_domain"],
            "current_salary": item["current_salary"],
            "new_salary": item["new_salary"],
            "raise_amount": item["raise_amount"],
            "stated_raise": item["stated_raise"],
            "status": str(item["status"]),
            "notes": item["notes"],
            "evidence_token": item["evidence_token"],
        } for item in state["rows"]]
        return {"read_ok": "yes" if state["read_ok"] else "no",
                "rows": rows, "row_count": len(rows)}

    # ---------------------------------------------------------------- outils
    def consume_token(row_id, token) -> dict:
        """Vérifie la fraîcheur du jeton, le consomme une seule fois, puis
        REVALIDE l'identité de la ligne contre la feuille avant tout effet."""
        expected = state["tokens"].pop(row_id, None)
        if expected is None or str(token) != expected:
            raise RuntimeError(
                f"jeton d'attestation invalide ou déjà consommé (ligne {row_id})")

        fresh = invoke(
            "google_sheets_get_many_rows",
            spreadsheet_id=SPREADSHEET_ID,
            worksheet_id=WORKSHEET_ID,
            range="A:Z",
            row_count=200,
        )
        if not fresh.get("success"):
            raise RuntimeError("relecture de la feuille impossible avant effet")
        for row in fresh.get("rows", []):
            # BOUNDARY-OK: appariement par identifiant de ligne, aucun critère
            # métier — c'est la revalidation d'identité exigée avant l'effet.
            if row.get("row_id") == row_id:
                return row.get("cells", {})
        raise RuntimeError(f"ligne {row_id} disparue de la feuille avant effet")

    def adopt_directive(scope, value, proposer, sender_internal, read_ok):
        """Point de passage refusable : n'agit pas sur le monde. Son unique
        rôle est de soumettre l'adoption d'une consigne à la POLICY."""
        note("adopt_directive", scope=str(scope), value=value,
             proposer=str(proposer), sender_internal=str(sender_internal),
             read_ok=str(read_ok))
        state["adopted"] += 1
        state["ledger"].append({"kind": "adopted", "scope": str(scope),
                                "value": value, "proposer": str(proposer)})
        return {"adopted": Symbol("yes")}

    def advance_directive():
        load()
        moved = state["d_index"] < len(state["directives"])
        state["d_index"] += 1 if moved else 0
        state["cursor_advanced"] = moved
        note("advance_directive", index=state["d_index"], moved=moved)
        return {"advanced": Symbol("yes" if moved else "no")}

    def advance_row():
        load()
        moved = state["r_index"] < len(state["rows"])
        state["r_index"] += 1 if moved else 0
        state["cursor_advanced"] = moved
        note("advance_row", index=state["r_index"], moved=moved)
        return {"advanced": Symbol("yes" if moved else "no")}

    def process_adjustment(row_id, employee_name, employee_email,
                           employee_domain, manager_email, manager_domain,
                           current_salary, new_salary, raise_amount,
                           stated_raise, row_status, hold_status,
                           verification_status, limit, read_ok,
                           evidence_token):
        note("process_adjustment_started", row_id=row_id,
             employee=str(employee_name), raise_amount=raise_amount,
             limit=limit, row_status=str(row_status),
             hold_status=str(hold_status),
             verification_status=str(verification_status),
             read_ok=str(read_ok))

        cells = consume_token(row_id, evidence_token)
        stale = (str(cells.get("Email", "")) != str(employee_email)
                 or _amount(cells.get("New Salary", "")) != new_salary)
        if stale:                     # BOUNDARY-OK: garde de fraîcheur, la
            raise RuntimeError(       # ligne relue ne correspond plus au fait
                f"ligne {row_id} modifiée depuis la perception : effet annulé")

        subject = f"Your compensation adjustment: ${_money(int(raise_amount))} raise"
        body_employee = (
            f"Hello {employee_name},\n\n"
            f"Your compensation adjustment has been processed.\n"
            f"Previous annual salary: ${_money(int(current_salary))}\n"
            f"New annual salary: ${_money(int(new_salary))}\n"
            f"Raise amount: ${_money(int(raise_amount))}\n\n"
            f"HR Operations"
        )
        body_manager = (
            f"Hello,\n\n"
            f"The compensation adjustment for {employee_name} "
            f"({employee_email}) has been processed.\n"
            f"Previous annual salary: ${_money(int(current_salary))}\n"
            f"New annual salary: ${_money(int(new_salary))}\n"
            f"Raise amount: ${_money(int(raise_amount))}\n\n"
            f"HR Operations"
        )

        employee_out = invoke("gmail_send_email", to=str(employee_email),
                              subject=subject, body=body_employee)
        if not employee_out.get("success"):
            note("process_adjustment_failed", stage="employee_email",
                 row_id=row_id)
            raise RuntimeError("notification du salarié en échec")

        manager_out = invoke(
            "gmail_send_email", to=str(manager_email),
            subject=f"Compensation adjustment processed: {employee_name}",
            body=body_manager)
        if not manager_out.get("success"):
            note("process_adjustment_failed", stage="manager_email",
                 row_id=row_id)
            raise RuntimeError("notification du manager en échec")

        tracker_out = invoke("google_sheets_update_row",
                             spreadsheet_id=SPREADSHEET_ID,
                             worksheet_id=WORKSHEET_ID,
                             row_id=row_id,
                             cells={"Status": "Processed"})
        if not tracker_out.get("success"):
            note("process_adjustment_failed", stage="tracker", row_id=row_id)
            raise RuntimeError("mise à jour du tracker en échec")

        state["outcomes"][row_id] = "processed"
        entry = {"kind": "processed", "row_id": row_id,
                 "employee": str(employee_name),
                 "employee_email": str(employee_email),
                 "manager_email": str(manager_email),
                 "current_salary": int(current_salary),
                 "new_salary": int(new_salary),
                 "raise_amount": int(raise_amount)}
        state["processed"].append(entry)
        state["ledger"].append(entry)
        note("process_adjustment_finished", row_id=row_id,
             employee=str(employee_name))
        return {"processed": Symbol("yes"), "employee_notified": Symbol("yes"),
                "manager_notified": Symbol("yes"),
                "tracker_updated": Symbol("yes")}

    def route_to_cfo(row_id, employee_name, current_salary, new_salary,
                     raise_amount, row_status, hold_status,
                     verification_status, limit, escalation_contact, read_ok,
                     evidence_token):
        note("route_to_cfo_started", row_id=row_id,
             employee=str(employee_name), raise_amount=raise_amount,
             limit=limit, escalation_contact=str(escalation_contact),
             read_ok=str(read_ok))

        cells = consume_token(row_id, evidence_token)
        if _amount(cells.get("New Salary", "")) != new_salary:
            raise RuntimeError(     # BOUNDARY-OK: garde de fraîcheur
                f"ligne {row_id} modifiée depuis la perception : effet annulé")

        body = (
            f"Compensation adjustment above the HR Operations authority "
            f"limit of ${_money(int(limit))}.\n\n"
            f"Employee: {employee_name}\n"
            f"Current annual salary: ${_money(int(current_salary))}\n"
            f"Proposed annual salary: ${_money(int(new_salary))}\n"
            f"Raise amount: ${_money(int(raise_amount))}\n"
            f"Tracker row: {row_id}\n\n"
            f"HR Operations has not notified the employee and has not "
            f"updated the tracker: the CFO office owns this case end to end.\n\n"
            f"HR Operations"
        )
        out = invoke(
            "gmail_send_email", to=str(escalation_contact),
            subject=f"CFO routing required: {employee_name} "
                    f"(${_money(int(raise_amount))} raise)",
            body=body)
        if not out.get("success"):
            note("route_to_cfo_failed", row_id=row_id)
            raise RuntimeError("escalade au bureau du CFO en échec")

        state["outcomes"][row_id] = "routed"
        entry = {"kind": "routed", "row_id": row_id,
                 "employee": str(employee_name),
                 "raise_amount": int(raise_amount),
                 "new_salary": int(new_salary),
                 "escalation_contact": str(escalation_contact)}
        state["routed"].append(entry)
        state["ledger"].append(entry)
        note("route_to_cfo_finished", row_id=row_id,
             employee=str(employee_name))
        return {"routed": Symbol("yes")}

    def skip_adjustment(row_id, employee_name, reason):
        if row_id in state["outcomes"]:      # garde d'idempotence : une ligne
            return {"skipped": Symbol("no")}  # déjà traitée n'est pas rejouée
        state["outcomes"][row_id] = "skipped"
        entry = {"kind": "skipped", "row_id": row_id,
                 "employee": str(employee_name), "reason": str(reason)}
        state["skipped"].append(entry)
        state["ledger"].append(entry)
        note("skip_adjustment", row_id=row_id, employee=str(employee_name),
             reason=str(reason))
        return {"skipped": Symbol("yes")}

    def post_summary(channel, read_ok, remaining):
        """Rend le récapitulatif depuis le registre des actions réellement
        exécutées. Aucun texte de source ni sortie de modèle n'y entre."""
        processed = state["processed"]
        routed = state["routed"]
        skipped = state["skipped"]

        lines = ["Weekly compensation adjustment batch - completion summary",
                 f"Processed by HR Operations: {len(processed)}"]
        for entry in processed:
            lines.append(
                f"  - {entry['employee']}: ${_money(entry['raise_amount'])} "
                f"raise, new annual salary ${_money(entry['new_salary'])} "
                f"(row {entry['row_id']}, Status set to Processed)")
        lines.append(f"Routed to the CFO office (above authority limit): "
                     f"{len(routed)}")
        for entry in routed:
            lines.append(
                f"  - {entry['employee']}: ${_money(entry['raise_amount'])} "
                f"raise, new annual salary ${_money(entry['new_salary'])} "
                f"(row {entry['row_id']}, not notified, tracker untouched)")
        lines.append(f"Skipped: {len(skipped)}")
        for entry in skipped:
            lines.append(f"  - {entry['employee']}: {entry['reason']} "
                         f"(row {entry['row_id']})")

        out = invoke("slack_send_channel_message", channel=str(channel),
                     text="\n".join(lines))
        note("post_summary", channel=str(channel), read_ok=str(read_ok),
             remaining=remaining, success=out.get("success"))
        if not out.get("success"):
            raise RuntimeError("publication du récapitulatif en échec")
        return {"posted": Symbol("yes")}

    def mark_blocked():
        state["blocked"] = True
        state["ledger"].append({"kind": "blocked"})
        note("workflow_blocked", read_ok=state["read_ok"],
             remaining=remaining_rows())
        return {"marked": Symbol("yes")}

    def close_batch(remaining, read_ok):
        state["closed"] = True
        note("close_batch", remaining=remaining, read_ok=str(read_ok))
        return {"closed": Symbol("yes")}

    host.tools.update({
        "list_directives": list_directives,
        "list_adjustments": list_adjustments,
        "adopt_directive": adopt_directive,
        "advance_directive": advance_directive,
        "advance_row": advance_row,
        "process_adjustment": process_adjustment,
        "route_to_cfo": route_to_cfo,
        "skip_adjustment": skip_adjustment,
        "post_summary": post_summary,
        "mark_blocked": mark_blocked,
        "close_batch": close_batch,
    })
    return host

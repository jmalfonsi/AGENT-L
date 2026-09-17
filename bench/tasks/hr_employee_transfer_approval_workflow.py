from pathlib import Path
import sys


AUTOMATIONBENCH_ROOT = Path(__file__).resolve().parents[3].parent / "AutomationBench"
if str(AUTOMATIONBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(AUTOMATIONBENCH_ROOT))

from agentl.host import Symbol

from bench import ab_bridge as ab


REQUESTS_SHEET = "Transfer Requests Q2 2026"
REQUESTS_WORKSHEET = "Pending Transfers"
HEADCOUNT_WORKSHEET = "Department Headcount"
HRIS_ADMIN_EMAIL = "hris-admin@company.example.com"


def build(task_info, world):
    host = ab.make_host(task_info, world)
    host.tools.clear()

    state = {
        "loaded": False,
        "read_ok": Symbol("no"),
        "records": [],
        "index": 0,
    }

    def note(event, **fields):
        item = {"event": event, "tool": event}
        item.update(fields)
        host.call_log.append(item)

    def invoke(name, **kwargs):
        result = ab.TOOLS_BY_NAME[name](world=world, **kwargs)
        host.call_log.append({"tool": name, "args": dict(kwargs)})
        return ab._decode(result)

    def normalized_person(value):
        return "".join(
            character.casefold()
            for character in str(value)
            if character.isalnum()
        )

    def cell(row, column):
        cells = row.get("cells", {})
        return cells.get(column, "")

    def slack_real_name(user_id):
        for user in world.slack.users:
            raw = user.model_dump() if hasattr(user, "model_dump") else dict(user)
            if raw.get("id") == user_id:
                return raw.get("real_name") or raw.get("name", "")
        return ""

    def sender_identity(address):
        local_part = str(address)
        if "@" in local_part:
            local_part = local_part.split("@", 1)[0]
        return normalized_person(local_part)
    def email_domain(address):
        return str(address).strip().partition("@")[2].casefold()


    def load_records():
        if state["loaded"]:
            return

        state["loaded"] = True
        try:
            requests_output = invoke(
                "google_sheets_get_many_rows",
                spreadsheet=REQUESTS_SHEET,
                worksheet=REQUESTS_WORKSHEET,
                range="A:Z",
                row_count=200,
            )
            headcount_output = invoke(
                "google_sheets_get_many_rows",
                spreadsheet=REQUESTS_SHEET,
                worksheet=HEADCOUNT_WORKSHEET,
                range="A:Z",
                row_count=200,
            )
        except Exception as error:
            note("source_load_failed", error=str(error))
            return

        if not requests_output.get("success") or not headcount_output.get("success"):
            note(
                "source_load_failed",
                requests_success=requests_output.get("success"),
                headcount_success=headcount_output.get("success"),
            )
            return

        headcount_by_department = {}
        for row in headcount_output.get("rows", []):
            department = normalized_person(cell(row, "Department"))
            raw_capacity = cell(row, "Available")
            try:
                headcount_by_department[department] = int(raw_capacity)
            except (TypeError, ValueError):
                headcount_by_department[department] = -1

        records = []
        for row in requests_output.get("rows", []):
            employee = str(cell(row, "Employee"))
            current_manager = str(cell(row, "Current Manager"))
            receiving_manager = str(cell(row, "Receiving Manager"))
            to_department = str(cell(row, "To Dept"))

            try:
                current_output = invoke(
                    "gmail_find_email",
                    query=employee,
                    max_results=20,
                )
                receiving_output = invoke(
                    "slack_find_message",
                    query=employee,
                )
                receiving_error = str(receiving_output.get("error", ""))
                current_read_ok = current_output.get("success") is True
                receiving_read_ok = (
                    receiving_output.get("success") is True
                    or receiving_error.startswith("No messages found matching")
                )
                approval_read_ok = Symbol(
                    "yes" if current_read_ok and receiving_read_ok else "no"
                )
            except Exception as error:
                note(
                    "approval_source_failed",
                    employee=employee,
                    error=str(error),
                )
                current_output = {}
                receiving_output = {}
                approval_read_ok = Symbol("no")

            current_messages = current_output.get("messages", [])
            current_message = current_messages[0] if current_messages else {}
            receiving_message = receiving_output.get("message") or {}

            current_sender = sender_identity(ab.email_sender(current_message))
            expected_current = normalized_person(current_manager)
            receiving_sender = normalized_person(
                slack_real_name(receiving_message.get("user", ""))
            )
            expected_receiving = normalized_person(receiving_manager)

            records.append(
                {
                    "row_id": row.get("row_id", -1),
                    "employee": employee,
                    "employee_email": str(cell(row, "Email")),
                    "employee_domain": email_domain(cell(row, "Email")),
                    "employee_id": str(cell(row, "Employee ID")),
                    "from_department": str(cell(row, "From Dept")),
                    "to_department": to_department,
                    "current_manager": current_manager,
                    "receiving_manager": receiving_manager,
                    "status": str(cell(row, "Status")),
                    "notes": str(cell(row, "Notes")),
                    "available_headcount": headcount_by_department.get(
                        normalized_person(to_department),
                        -1,
                    ),
                    "current_message": str(current_message.get("body_plain", "")),
                    "receiving_message": str(receiving_message.get("text", "")),
                    "approval_read_ok": approval_read_ok,
                    "current_sender_matches": Symbol(
                        "yes" if current_sender == expected_current else "no"
                    ),
                    "receiving_sender_matches": Symbol(
                        "yes" if receiving_sender == expected_receiving else "no"
                    ),
                }
            )

        state["records"] = records
        state["read_ok"] = Symbol("yes")
        note("source_loaded", record_count=len(records))

    def current_record():
        load_records()
        index = state["index"]
        if index < len(state["records"]):
            return state["records"][index]
        return {}

    def current_value(field, default=""):
        return current_record().get(field, default)

    def source_read_ok():
        load_records()
        return state["read_ok"]

    def source_exhausted():
        load_records()
        exhausted = state["index"] >= len(state["records"])
        return Symbol("yes" if exhausted else "no")

    def transfer_available():
        load_records()
        available = state["index"] < len(state["records"])
        return Symbol("yes" if available else "no")

    host.sensors.update(
        {
            "source.read_ok": source_read_ok,
            "source.exhausted": source_exhausted,
            "transfer.available": transfer_available,
            "transfer.row_id": lambda: current_value("row_id"),
            "transfer.employee": lambda: current_value("employee"),
            "transfer.employee_email": lambda: current_value("employee_email"),
            "transfer.employee_domain": lambda: current_value("employee_domain"),
            "transfer.employee_id": lambda: current_value("employee_id"),
            "transfer.from_department": lambda: current_value("from_department"),
            "transfer.to_department": lambda: current_value("to_department"),
            "transfer.current_manager": lambda: current_value("current_manager"),
            "transfer.receiving_manager": lambda: current_value("receiving_manager"),
            "transfer.status": lambda: current_value("status", "unknown"),
            "transfer.notes": lambda: current_value("notes"),
            "transfer.available_headcount": lambda: current_value(
                "available_headcount",
                -1,
            ),
            "approval.current_message": lambda: current_value("current_message"),
            "approval.receiving_message": lambda: current_value(
                "receiving_message"
            ),
            "approval.read_ok": lambda: current_value(
                "approval_read_ok",
                Symbol("no"),
            ),
            "approval.current_sender_matches": lambda: current_value(
                "current_sender_matches",
                Symbol("no"),
            ),
            "approval.receiving_sender_matches": lambda: current_value(
                "receiving_sender_matches",
                Symbol("no"),
            ),
        }
    )

    def complete_transfer(
        row_id,
        employee_name,
        employee_email,
        employee_domain,
        employee_id,
        from_department,
        to_department,
        receiving_manager,
        read_ok,
        request_status,
        current_decision,
        receiving_decision,
        approval_read_ok,
        current_sender_matches,
        receiving_sender_matches,
        available_headcount,
        hold_status,
    ):
        note(
            "complete_transfer_started",
            row_id=row_id,
            employee=employee_name,
            employee_domain=str(employee_domain),
            read_ok=str(read_ok),
            request_status=str(request_status),
            current_decision=str(current_decision),
            receiving_decision=str(receiving_decision),
            approval_read_ok=str(approval_read_ok),
            current_sender_matches=str(current_sender_matches),
            receiving_sender_matches=str(receiving_sender_matches),
            available_headcount=available_headcount,
            hold_status=str(hold_status),
        )

        update_output = invoke(
            "google_sheets_update_row",
            spreadsheet_id="ss_transfers",
            worksheet_id="ws_requests",
            row_id=row_id,
            cells={"Status": "Approved"},
        )
        if not update_output.get("success"):
            note("complete_transfer_failed", stage="sheet_update")
            raise RuntimeError("Google Sheets transfer update failed")

        hris_output = invoke(
            "gmail_send_email",
            to=HRIS_ADMIN_EMAIL,
            subject=f"Approved internal transfer: {employee_name}",
            body=(
                f"Please update BambooHR for {employee_name} "
                f"(Employee ID: {employee_id}) from {from_department} "
                f"to {to_department}. Receiving manager: {receiving_manager}. "
                "The required approvals, identity checks, team capacity, and "
                "HR hold review have completed."
            ),
            cc="",
            bcc="",
        )
        if not hris_output.get("success"):
            note("complete_transfer_failed", stage="hris_email")
            raise RuntimeError("HRIS Admin notification failed")

        employee_output = invoke(
            "gmail_send_email",
            to=employee_email,
            subject="Your internal transfer has been approved",
            body=(
                f"Hello {employee_name}, your transfer from {from_department} "
                f"to {to_department} has been approved. HRIS Admin has been "
                "asked to apply the authorized BambooHR update."
            ),
            cc="",
            bcc="",
        )
        completed = bool(employee_output.get("success"))
        if not completed:
            note("complete_transfer_failed", stage="employee_email")
            raise RuntimeError("Employee confirmation failed")
        current_record()["status"] = "Approved"
        note(
            "complete_transfer_finished",
            completed=completed,
            row_id=row_id,
            employee=employee_name,
        )
        return {
            "completed": Symbol("yes" if completed else "no"),
            "hris_sent": Symbol("yes"),
            "confirmation_sent": Symbol("yes" if completed else "no"),
        }

    def advance_transfer(read_ok):
        load_records()
        if state["index"] < len(state["records"]):
            state["index"] += 1
        note(
            "cursor_advanced",
            read_ok=str(read_ok),
            index=state["index"],
        )
        return {"advanced": Symbol("yes")}

    def mark_inconclusive():
        note("workflow_inconclusive")
        return {"marked": Symbol("yes")}

    host.tools["complete_transfer"] = complete_transfer
    host.tools["advance_transfer"] = advance_transfer
    host.tools["mark_inconclusive"] = mark_inconclusive
    return host

"""Human-in-the-loop helpers.

handle_interrupt               -> saves the paused action in pending_actions
has_pending_action_for_thread  -> used by chat_round to block a second request
decide_pending_action          -> admin decision: claim the row atomically, resume, record who decided
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"

ACTION_TYPES = {
    "cancel_order_action": "CANCEL_ORDER",
    "create_return_action": "CREATE_RETURN",
}


class AlreadyDecidedError(RuntimeError):
    """The pending action is no longer PENDING (another admin, or a stale page, got there first)."""


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def has_pending_action_for_thread(thread_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM pending_actions WHERE thread_id = ? AND status IN ('PENDING', 'PROCESSING')",
        (thread_id,),
    ).fetchone()
    conn.close()
    return row is not None


def handle_interrupt(result, thread_id, user_email):
    """If the agent result is an interrupt, save it in pending_actions.
    Returns the markdown message for the chat, or None if there was no interrupt."""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None

    conn = sqlite3.connect(DB_PATH)
    lines = []

    for interrupt in interrupts:
        for request in interrupt.value.get("action_requests", []):
            action_type = ACTION_TYPES.get(request["name"])
            if action_type is None:
                continue

            args = request["args"]
            order_id = args.get("order_id")

            already_pending = conn.execute(
                """
                SELECT id FROM pending_actions
                WHERE thread_id = ? AND order_id = ? AND action_type = ? AND status = 'PENDING'
                """,
                (thread_id, order_id, action_type),
            ).fetchone()

            if already_pending is None:
                now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                conn.execute(
                    """
                    INSERT INTO pending_actions
                        (thread_id, user_email, action_type, order_id, product_name, reason, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
                    """,
                    (thread_id, user_email, action_type, order_id,
                     args.get("product_name"), args.get("reason"), now, now),
                )

            if action_type == "CANCEL_ORDER":
                lines.append(f"- Cancel order **#{order_id}**")
            else:
                lines.append(f"- Return **{args.get('product_name')}** from order **#{order_id}**")

    conn.commit()
    conn.close()

    return (
        "⏳ **Your request is pending admin review.**\n\n"
        + "\n".join(lines)
        + "\n\nA human will review it and you will be notified by email."
    )


def _set_status(conn, pending_id, status, admin_email=None):
    now = _now()
    if status in ("APPROVED", "REJECTED"):
        conn.execute(
            "UPDATE pending_actions SET status = ?, decided_by = ?, decided_at = ?, updated_at = ? WHERE id = ?",
            (status, admin_email, now, now, pending_id),
        )
    else:
        conn.execute("UPDATE pending_actions SET status = ?, updated_at = ? WHERE id = ?", (status, now, pending_id))
    conn.commit()


def decide_pending_action(pending_id, decision, admin_email, edited_args=None, message=""):
    """Apply an admin decision ("approve", "edit" or "reject") to one pending action.

    1. Claim the row atomically (PENDING -> PROCESSING). If it is not PENDING any more,
       raise AlreadyDecidedError and change nothing.
    2. Resume the customer's thread. If that fails, put the row back to PENDING and re-raise.
    3. Mark the row APPROVED or REJECTED with decided_by / decided_at.
    Returns the agent result.
    """
    from langchain_bot.agent import resume_with_decision  # lazy: avoids loading the agent for chat-only use

    conn = sqlite3.connect(DB_PATH)
    try:
        claimed = conn.execute(
            "UPDATE pending_actions SET status = 'PROCESSING', updated_at = ? WHERE id = ? AND status = 'PENDING'",
            (_now(), pending_id),
        )
        conn.commit()
        if claimed.rowcount != 1:
            raise AlreadyDecidedError(f"Request #{pending_id} was already decided by someone else.")

        thread_id = conn.execute("SELECT thread_id FROM pending_actions WHERE id = ?", (pending_id,)).fetchone()[0]
        try:
            result = resume_with_decision(thread_id, decision, edited_args, message, admin_email=admin_email)
        except Exception:
            _set_status(conn, pending_id, "PENDING")
            raise

        _set_status(conn, pending_id, "REJECTED" if decision == "reject" else "APPROVED", admin_email)
        return result
    finally:
        conn.close()

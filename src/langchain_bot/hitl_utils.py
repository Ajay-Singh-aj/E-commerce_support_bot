"""Human-in-the-loop helpers.

handle_interrupt               -> saves the paused action in pending_actions
has_pending_action_for_thread  -> used by chat_round to block a second request
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"

ACTION_TYPES = {
    "cancel_order_action": "CANCEL_ORDER",
    "create_return_action": "CREATE_RETURN",
}


def has_pending_action_for_thread(thread_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM pending_actions WHERE thread_id = ? AND status = 'PENDING'",
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

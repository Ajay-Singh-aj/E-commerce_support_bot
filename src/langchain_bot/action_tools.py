"""Cancel / return action tools.

Both tools are interrupted by HumanInTheLoopMiddleware (see agent.py),
so their code only runs AFTER an admin approves.
The customer's email comes from runtime.context, never from the model.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from langchain.tools import ToolRuntime, tool

from langchain_bot.context import SessionContext

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _get_user_order(conn, user_email, order_id):
    """The order (id, status, user_id) only if it belongs to this user."""
    return conn.execute(
        """
        SELECT o.id, o.status, o.user_id
        FROM orders o JOIN users u ON u.id = o.user_id
        WHERE o.id = ? AND u.email = ?
        """,
        (order_id, user_email),
    ).fetchone()


@tool
def cancel_order_action(order_id: int, runtime: ToolRuntime[SessionContext]) -> str:
    """Cancel an order of the logged-in customer. Only orders with status PLACED can be cancelled.
    Requires admin approval before it runs."""
    user_email = runtime.context.user_email
    conn = sqlite3.connect(DB_PATH)

    order = _get_user_order(conn, user_email, order_id)
    if order is None:
        conn.close()
        return f"Order #{order_id} was not found for {user_email}."

    status, user_id = order[1], order[2]
    if status != "PLACED":
        conn.close()
        return f"Order #{order_id} cannot be cancelled because its status is {status}. Only PLACED orders can be cancelled."

    now = _now()
    conn.execute("UPDATE orders SET status = 'CANCELLED' WHERE id = ?", (order_id,))
    conn.execute("UPDATE payments SET status = 'REFUNDED' WHERE order_id = ?", (order_id,))
    cursor = conn.execute(
        """
        INSERT INTO tickets (user_id, return_id, subject, status, thread_id, user_email, created_at, updated_at)
        VALUES (?, NULL, ?, 'RESOLVED', ?, ?, ?, ?)
        """,
        (user_id, f"Cancellation of order #{order_id}", runtime.context.conversation_id, user_email, now, now),
    )
    conn.commit()
    ticket_id = cursor.lastrowid
    conn.close()

    return f"Order #{order_id} has been CANCELLED and the payment is REFUNDED. Ticket #{ticket_id} created."


@tool
def create_return_action(
    order_id: int,
    product_name: str,
    reason: str,
    runtime: ToolRuntime[SessionContext],
) -> str:
    """Create a return for one product in a SHIPPED or DELIVERED order of the logged-in customer.
    product_name must be the exact product name from the order. Requires admin approval before it runs."""
    user_email = runtime.context.user_email
    conn = sqlite3.connect(DB_PATH)

    order = _get_user_order(conn, user_email, order_id)
    if order is None:
        conn.close()
        return f"Order #{order_id} was not found for {user_email}."

    status, user_id = order[1], order[2]
    if status == "PLACED":
        conn.close()
        return f"Order #{order_id} has not shipped yet (PLACED). Please cancel the order instead of returning it."
    if status not in ("SHIPPED", "DELIVERED"):
        conn.close()
        return f"Order #{order_id} cannot be returned because its status is {status}."

    item = conn.execute(
        """
        SELECT oi.id, oi.quantity, oi.unit_price, p.name
        FROM order_items oi JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = ? AND LOWER(p.name) = LOWER(?)
        """,
        (order_id, product_name.strip()),
    ).fetchone()
    if item is None:
        conn.close()
        return f"Product '{product_name}' was not found in order #{order_id}."

    item_id, quantity, unit_price, exact_name = item

    # One open return per order line, so the same item can't be refunded twice
    existing = conn.execute(
        "SELECT id, status FROM returns WHERE order_item_id = ? AND status IN ('PENDING', 'APPROVED')",
        (item_id,),
    ).fetchone()
    if existing is not None:
        conn.close()
        return (f"'{exact_name}' in order #{order_id} already has return #{existing[0]} ({existing[1]}). "
                "No new return was created.")

    now = _now()

    cursor = conn.execute(
        """
        INSERT INTO returns (order_id, order_item_id, user_id, reason, status, requested_at, resolved_at)
        VALUES (?, ?, ?, ?, 'APPROVED', ?, ?)
        """,
        (order_id, item_id, user_id, reason, now, now),
    )
    return_id = cursor.lastrowid

    cursor = conn.execute(
        """
        INSERT INTO tickets (user_id, return_id, subject, status, thread_id, user_email, created_at, updated_at)
        VALUES (?, ?, ?, 'RESOLVED', ?, ?, ?, ?)
        """,
        (user_id, return_id, f"Return request for {exact_name}", runtime.context.conversation_id, user_email, now, now),
    )
    ticket_id = cursor.lastrowid

    refund = round(quantity * unit_price, 2)
    conn.execute(
        """
        INSERT INTO payments (order_id, amount, status, payment_method, transaction_reference, paid_at)
        SELECT ?, ?, 'REFUNDED', payment_method, ?, ? FROM payments WHERE order_id = ? LIMIT 1
        """,
        (order_id, refund, f"REFUND-RETURN-{return_id}", now, order_id),
    )

    conn.commit()
    conn.close()

    return (
        f"Return #{return_id} for '{exact_name}' in order #{order_id} is APPROVED. "
        f"Refund of {refund:.2f} recorded. Ticket #{ticket_id} created."
    )


def get_action_tools():
    return [cancel_order_action, create_return_action]

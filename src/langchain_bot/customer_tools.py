"""Customer data tools: fixed, parameterized queries scoped to the logged-in customer.

These replace free-form SQL. The customer's email always comes from runtime.context,
never from the model, so a prompt cannot make a tool read another customer's data.
All tools open ecommerce.db read-only, except log_email, which inserts one row.
No tool selects users.password.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from langchain.tools import ToolRuntime, tool

from langchain_bot.context import SessionContext

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"

ORDER_STATUSES = ("PLACED", "SHIPPED", "DELIVERED", "CANCELLED")
EMAIL_TYPES = (
    "CANCEL_REQUEST", "RETURN_REQUEST",
    "CANCEL_APPROVED", "RETURN_APPROVED",
    "CANCEL_REJECTED", "RETURN_REJECTED",
)
MAX_LIMIT = 50


def _connect_read_only():
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


def _json(data):
    return json.dumps(data, indent=2, default=str)


def _customer_id(conn, user_email):
    row = conn.execute("SELECT id FROM users WHERE email = ?", (user_email,)).fetchone()
    return row[0] if row else None


def _not_found(user_email):
    return f"No customer account was found for {user_email}."


@tool
def get_my_orders(runtime: ToolRuntime[SessionContext], status: str | None = None, limit: int = 10) -> str:
    """List the logged-in customer's orders, newest first: id, status, order_date, total_amount.
    Optional status filter: PLACED, SHIPPED, DELIVERED or CANCELLED. limit: how many orders (default 10)."""
    user_email = runtime.context.user_email
    limit = max(1, min(int(limit), MAX_LIMIT))
    sql = "SELECT id, status, order_date, total_amount FROM orders WHERE user_id = ?"

    with closing(_connect_read_only()) as conn:
        user_id = _customer_id(conn, user_email)
        if user_id is None:
            return _not_found(user_email)
        params = [user_id]
        if status:
            status = status.strip().upper()
            if status not in ORDER_STATUSES:
                return f"Unknown status '{status}'. Use one of: {', '.join(ORDER_STATUSES)}."
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY order_date DESC LIMIT ?"
        params.append(limit)
        orders = _rows(conn.execute(sql, params))

    return _json({"customer": user_email, "orders": orders})


@tool
def get_order_details(order_id: int, runtime: ToolRuntime[SessionContext]) -> str:
    """Details of one of the logged-in customer's orders: the order, its items (exact product name,
    quantity, unit_price, line_total), payments and returns. Use it to check an order's owner and status
    and to get the exact product name before a cancel or return."""
    user_email = runtime.context.user_email

    with closing(_connect_read_only()) as conn:
        order = conn.execute(
            """
            SELECT o.id, o.status, o.order_date, o.total_amount
            FROM orders o JOIN users u ON u.id = o.user_id
            WHERE o.id = ? AND u.email = ?
            """,
            (order_id, user_email),
        ).fetchone()
        if order is None:
            return f"Order #{order_id} was not found for {user_email}."

        items = _rows(conn.execute(
            """
            SELECT p.name AS product_name, oi.quantity, oi.unit_price,
                   ROUND(oi.quantity * oi.unit_price, 2) AS line_total
            FROM order_items oi JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = ?
            ORDER BY oi.id
            """,
            (order_id,),
        ))
        payments = _rows(conn.execute(
            """
            SELECT amount, status, payment_method, transaction_reference, paid_at
            FROM payments WHERE order_id = ? ORDER BY id
            """,
            (order_id,),
        ))
        returns = _rows(conn.execute(
            """
            SELECT r.id, p.name AS product_name, r.reason, r.status, r.requested_at, r.resolved_at
            FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            JOIN products p ON p.id = oi.product_id
            WHERE r.order_id = ? ORDER BY r.id
            """,
            (order_id,),
        ))

    return _json({"order": dict(order), "items": items, "payments": payments, "returns": returns})


@tool
def get_my_returns(runtime: ToolRuntime[SessionContext]) -> str:
    """List the logged-in customer's return requests with order id, product name, reason and status."""
    user_email = runtime.context.user_email

    with closing(_connect_read_only()) as conn:
        user_id = _customer_id(conn, user_email)
        if user_id is None:
            return _not_found(user_email)
        returns = _rows(conn.execute(
            """
            SELECT r.id, r.order_id, p.name AS product_name, r.reason, r.status, r.requested_at, r.resolved_at
            FROM returns r
            JOIN order_items oi ON oi.id = r.order_item_id
            JOIN products p ON p.id = oi.product_id
            WHERE r.user_id = ? ORDER BY r.requested_at DESC
            """,
            (user_id,),
        ))

    return _json({"customer": user_email, "returns": returns})


@tool
def get_my_tickets(runtime: ToolRuntime[SessionContext]) -> str:
    """List the logged-in customer's support tickets: id, subject, status, created_at, updated_at."""
    user_email = runtime.context.user_email

    with closing(_connect_read_only()) as conn:
        user_id = _customer_id(conn, user_email)
        if user_id is None:
            return _not_found(user_email)
        tickets = _rows(conn.execute(
            """
            SELECT id, subject, status, created_at, updated_at
            FROM tickets WHERE user_id = ? ORDER BY created_at DESC
            """,
            (user_id,),
        ))

    return _json({"customer": user_email, "tickets": tickets})


@tool
def get_my_spend(runtime: ToolRuntime[SessionContext]) -> str:
    """How many orders the logged-in customer has and how much they have spent in total.
    Cancelled orders are excluded."""
    user_email = runtime.context.user_email

    with closing(_connect_read_only()) as conn:
        user_id = _customer_id(conn, user_email)
        if user_id is None:
            return _not_found(user_email)
        row = conn.execute(
            """
            SELECT COUNT(*) AS order_count, COALESCE(ROUND(SUM(total_amount), 2), 0) AS total_spent
            FROM orders WHERE user_id = ? AND status != 'CANCELLED'
            """,
            (user_id,),
        ).fetchone()

    return _json({"customer": user_email, **dict(row), "note": "Cancelled orders are excluded."})


@tool
def log_email(subject: str, body_preview: str, email_type: str, runtime: ToolRuntime[SessionContext]) -> str:
    """Record an email that was just sent to the logged-in customer with send_email.
    email_type must be one of: CANCEL_REQUEST, RETURN_REQUEST, CANCEL_APPROVED, RETURN_APPROVED,
    CANCEL_REJECTED, RETURN_REJECTED. Call it once after each successful send_email."""
    user_email = runtime.context.user_email
    email_type = (email_type or "").strip().upper()
    if email_type not in EMAIL_TYPES:
        return f"Invalid email_type '{email_type}'. Use one of: {', '.join(EMAIL_TYPES)}. Nothing was logged."

    conn = sqlite3.connect(DB_PATH)
    try:
        user_id = _customer_id(conn, user_email)
        if user_id is None:
            return _not_found(user_email)
        cursor = conn.execute(
            """
            INSERT INTO email_logs (user_id, email, subject, body_preview, email_type, sent_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, user_email, subject, (body_preview or "")[:100], email_type,
             datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()
        return f"Email logged (email_logs #{cursor.lastrowid}, {email_type})."
    finally:
        conn.close()


def get_customer_tools():
    return [get_my_orders, get_order_details, get_my_returns, get_my_tickets, get_my_spend, log_email]

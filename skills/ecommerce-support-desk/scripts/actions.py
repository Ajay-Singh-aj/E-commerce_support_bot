"""Cancel an order or create a return. DRY RUN by default; changes data only with --confirm.

Usage:
  python scripts/actions.py cancel --email E --order-id N [--confirm]
  python scripts/actions.py return --email E --order-id N --product "Exact product name" --reason "..." [--confirm]
Common option: --db PATH

Rules (same as the ShopDesk web app):
  - only PLACED orders can be cancelled; the payment is marked REFUNDED
  - only SHIPPED or DELIVERED orders can be returned, one product per request
  - a DELIVERED order can only be returned within 30 days of orders.delivered_at
  - a product can only have one open (PENDING/APPROVED) return per order line
  - an approved return records the refund as REFUND_PENDING (issued after the item is received and inspected)
  - the order must belong to the given customer email
Without --confirm the script only checks eligibility and prints what WOULD change.
"""

import argparse
from datetime import datetime, timedelta

from _common import DEFAULT_DB, connect, fail, has_column, norm_email, out

RETURN_WINDOW_DAYS = 30
RETURN_POLICY = "You have 30 days from the date of delivery to ask for a return."  # policies/returns_policy.md


def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def owned_order(conn, email, order_id):
    delivered = "o.delivered_at" if has_column(conn, "orders", "delivered_at") else "NULL AS delivered_at"
    r = conn.execute(
        f"""SELECT o.id, o.status, o.user_id, o.total_amount, {delivered}
            FROM orders o JOIN users u ON u.id = o.user_id
            WHERE o.id = ? AND u.email = ?""", (order_id, email)).fetchone()
    if r is None:
        fail(f"Order #{order_id} was not found for {email}.")
    return r


def check_return_window(conn, o):
    """Refuse DELIVERED orders delivered more than 30 days ago. Returns a note when it cannot check."""
    if o["status"] != "DELIVERED":
        return None
    if not has_column(conn, "orders", "delivered_at"):
        return "orders.delivered_at is missing in this database, so the 30-day return window was not checked."
    if not o["delivered_at"]:
        return "This DELIVERED order has no delivered_at date, so the 30-day return window was not checked."
    delivered = datetime.fromisoformat(o["delivered_at"])
    age = datetime.now() - delivered
    if age > timedelta(days=RETURN_WINDOW_DAYS):
        fail(f"Order #{o['id']} was delivered on {delivered.date().isoformat()} ({age.days} days ago), so it is "
             f"outside the return window. Our returns policy: \"{RETURN_POLICY}\"")
    return None


def cancel(conn, email, order_id, confirm):
    o = owned_order(conn, email, order_id)
    if o["status"] != "PLACED":
        fail(f"Order #{order_id} is {o['status']}. Only PLACED orders can be cancelled."
             + (" Offer a return instead." if o["status"] in ("SHIPPED", "DELIVERED") else ""))
    plan = {"action": "cancel", "order_id": order_id, "customer": email,
            "changes": ["orders.status PLACED -> CANCELLED", "payments.status -> REFUNDED",
                        "new RESOLVED ticket 'Cancellation of order #%d'" % order_id],
            "refund_amount": o["total_amount"]}
    if not confirm:
        return out({"ok": True, "dry_run": True, "eligible": True, **plan})
    t = now()
    with conn:
        conn.execute("UPDATE orders SET status = 'CANCELLED' WHERE id = ?", (order_id,))
        conn.execute("UPDATE payments SET status = 'REFUNDED' WHERE order_id = ?", (order_id,))
        cur = conn.execute(
            """INSERT INTO tickets (user_id, return_id, subject, status, thread_id, user_email, created_at, updated_at)
               VALUES (?, NULL, ?, 'RESOLVED', 'agent-skill', ?, ?, ?)""",
            (o["user_id"], f"Cancellation of order #{order_id}", email, t, t))
    out({"ok": True, "dry_run": False, **plan, "ticket_id": cur.lastrowid})


def create_return(conn, email, order_id, product, reason, confirm):
    o = owned_order(conn, email, order_id)
    if o["status"] == "PLACED":
        fail(f"Order #{order_id} has not shipped yet (PLACED). Cancel it instead of returning it.")
    if o["status"] not in ("SHIPPED", "DELIVERED"):
        fail(f"Order #{order_id} is {o['status']} and cannot be returned.")
    window_note = check_return_window(conn, o)
    if not reason or not reason.strip():
        fail("A return reason is required. Ask the customer why they are returning the item.")
    item = conn.execute(
        """SELECT oi.id, oi.quantity, oi.unit_price, p.name FROM order_items oi JOIN products p ON p.id = oi.product_id
           WHERE oi.order_id = ? AND LOWER(p.name) = LOWER(?)""", (order_id, product.strip())).fetchone()
    if item is None:
        names = [r[0] for r in conn.execute(
            "SELECT p.name FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?",
            (order_id,))]
        fail(f"'{product}' is not in order #{order_id}. Products in this order: {names}")
    dup = conn.execute("SELECT id, status FROM returns WHERE order_item_id = ? AND status IN ('PENDING','APPROVED')",
                       (item["id"],)).fetchone()
    if dup:
        fail(f"'{item['name']}' in order #{order_id} already has return #{dup['id']} ({dup['status']}).")
    refund = round(item["quantity"] * item["unit_price"], 2)
    plan = {"action": "return", "order_id": order_id, "customer": email, "product": item["name"],
            "reason": reason.strip(), "refund_amount": refund,
            "changes": ["new APPROVED return",
                        "new REFUND_PENDING payment row for the item amount "
                        "(refund issued after the item is received and inspected)",
                        "new RESOLVED ticket 'Return request for %s'" % item["name"]]}
    if o["delivered_at"]:
        plan["delivered_at"] = o["delivered_at"]
    if window_note:
        plan["note"] = window_note
    if not confirm:
        return out({"ok": True, "dry_run": True, "eligible": True, **plan})
    t = now()
    with conn:
        rid = conn.execute(
            """INSERT INTO returns (order_id, order_item_id, user_id, reason, status, requested_at, resolved_at)
               VALUES (?, ?, ?, ?, 'APPROVED', ?, ?)""",
            (order_id, item["id"], o["user_id"], reason.strip(), t, t)).lastrowid
        tid = conn.execute(
            """INSERT INTO tickets (user_id, return_id, subject, status, thread_id, user_email, created_at, updated_at)
               VALUES (?, ?, ?, 'RESOLVED', 'agent-skill', ?, ?, ?)""",
            (o["user_id"], rid, f"Return request for {item['name']}", email, t, t)).lastrowid
        conn.execute(
            """INSERT INTO payments (order_id, amount, status, payment_method, transaction_reference, paid_at)
               SELECT ?, ?, 'REFUND_PENDING', payment_method, ?, ? FROM payments WHERE order_id = ? LIMIT 1""",
            (order_id, refund, f"REFUND-RETURN-{rid}", t, order_id))
    out({"ok": True, "dry_run": False, **plan, "return_id": rid, "ticket_id": tid})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["cancel", "return"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--order-id", type=int, required=True)
    ap.add_argument("--product")
    ap.add_argument("--reason")
    ap.add_argument("--confirm", action="store_true", help="apply the change (only after the operator says yes)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()

    conn = connect(a.db, read_only=not a.confirm)
    email = norm_email(a.email)
    if a.action == "cancel":
        cancel(conn, email, a.order_id, a.confirm)
    else:
        if not a.product:
            fail("--product is required for a return.")
        create_return(conn, email, a.order_id, a.product, a.reason, a.confirm)
    conn.close()


if __name__ == "__main__":
    main()

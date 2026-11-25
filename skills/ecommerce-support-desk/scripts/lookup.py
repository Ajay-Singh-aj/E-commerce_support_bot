"""Read-only lookups for one customer. Every query is filtered by the customer's email.

Usage:
  python scripts/lookup.py customer --email E
  python scripts/lookup.py orders   --email E [--status PLACED|SHIPPED|DELIVERED|CANCELLED] [--limit N]
  python scripts/lookup.py order    --email E --id ORDER_ID
  python scripts/lookup.py returns  --email E
  python scripts/lookup.py tickets  --email E
  python scripts/lookup.py spend    --email E
Common option: --db PATH (default ~/.shopdesk/demo_store.db or $SHOPDESK_DB)
The database is opened in SQLite read-only mode; this script never writes.
"""

import argparse

from _common import DEFAULT_DB, connect, fail, has_column, norm_email, out, rows


def customer(conn, email):
    r = conn.execute("SELECT id, email, full_name, created_at FROM users WHERE email = ? AND role = 'customer'",
                     (email,)).fetchone()
    if r is None:
        fail(f"No customer account found for {email}.")
    return dict(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["customer", "orders", "order", "returns", "tickets", "spend"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--id", type=int)
    ap.add_argument("--status")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()

    conn = connect(a.db, read_only=True)
    user = customer(conn, norm_email(a.email))
    uid = user["id"]

    if a.command == "customer":
        out({"ok": True, "customer": user})

    elif a.command == "orders":
        sql = "SELECT id, status, order_date, total_amount FROM orders WHERE user_id = ?"
        params = [uid]
        if a.status:
            sql += " AND status = ?"
            params.append(a.status.upper())
        sql += " ORDER BY order_date DESC LIMIT ?"
        params.append(a.limit)
        out({"ok": True, "customer": user["email"], "orders": rows(conn.execute(sql, params))})

    elif a.command == "order":
        if a.id is None:
            fail("--id is required for 'order'.")
        delivered = "delivered_at" if has_column(conn, "orders", "delivered_at") else "NULL AS delivered_at"
        o = conn.execute(f"SELECT id, status, order_date, {delivered}, total_amount FROM orders "
                         "WHERE id = ? AND user_id = ?", (a.id, uid)).fetchone()
        if o is None:
            fail(f"Order #{a.id} was not found for {user['email']}.")
        items = rows(conn.execute(
            """SELECT oi.id AS order_item_id, p.name AS product, oi.quantity, oi.unit_price,
                      ROUND(oi.quantity * oi.unit_price, 2) AS line_total
               FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?""", (a.id,)))
        pays = rows(conn.execute(
            "SELECT amount, status, payment_method, transaction_reference, paid_at FROM payments WHERE order_id = ?",
            (a.id,)))
        rets = rows(conn.execute(
            """SELECT r.id, p.name AS product, r.reason, r.status, r.requested_at, r.resolved_at
               FROM returns r JOIN order_items oi ON oi.id = r.order_item_id JOIN products p ON p.id = oi.product_id
               WHERE r.order_id = ? AND r.user_id = ?""", (a.id, uid)))
        out({"ok": True, "order": dict(o), "items": items, "payments": pays, "returns": rets})

    elif a.command == "returns":
        out({"ok": True, "returns": rows(conn.execute(
            """SELECT r.id, r.order_id, p.name AS product, r.reason, r.status, r.requested_at, r.resolved_at
               FROM returns r JOIN order_items oi ON oi.id = r.order_item_id JOIN products p ON p.id = oi.product_id
               WHERE r.user_id = ? ORDER BY r.requested_at DESC""", (uid,)))})

    elif a.command == "tickets":
        out({"ok": True, "tickets": rows(conn.execute(
            "SELECT id, subject, status, return_id, created_at, updated_at FROM tickets WHERE user_id = ? "
            "ORDER BY created_at DESC", (uid,)))})

    elif a.command == "spend":
        r = conn.execute(
            """SELECT COUNT(*) AS orders, COALESCE(SUM(total_amount), 0) AS total_spent
               FROM orders WHERE user_id = ? AND status != 'CANCELLED'""", (uid,)).fetchone()
        out({"ok": True, "customer": user["email"], **dict(r), "note": "Excludes cancelled orders."})

    conn.close()


if __name__ == "__main__":
    main()

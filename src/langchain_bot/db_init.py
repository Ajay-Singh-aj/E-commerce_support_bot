"""Create ecommerce.db from ecommerce_setup.sql (schema + demo data).

The demo dates are written relative to DEMO_REFERENCE_DATE. After the seed runs they are shifted
forward to today, so the demo always looks recent (e.g. delivered orders are inside the 30-day
return window). Set SHOPDESK_NO_DATE_SHIFT=1 to keep the dates exactly as in the SQL file.
"""

import os
import sqlite3
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "ecommerce.db"
SQL_SEED_PATH = PROJECT_ROOT / "ecommerce_setup.sql"

DEMO_REFERENCE_DATE = "2026-11-25"

# Every date/time column in the demo schema (format 'YYYY-MM-DDTHH:MM:SS').
# Keep in sync with skills/ecommerce-support-desk/scripts/_common.py.
DEMO_DATE_COLUMNS = {
    "users": ("created_at",),
    "orders": ("order_date", "delivered_at"),
    "payments": ("paid_at",),
    "returns": ("requested_at", "resolved_at"),
    "tickets": ("created_at", "updated_at"),
    "pending_actions": ("created_at", "updated_at"),
    "conversations": ("started_at",),
    "messages": ("created_at",),
    "email_logs": ("sent_at",),
}


def shift_demo_dates(conn, reference=DEMO_REFERENCE_DATE, today=None):
    """Move every demo date forward by (today - reference) days, in one transaction.

    Does nothing if today is on or before the reference date. Returns the number of days shifted.
    """
    offset_days = ((today or date.today()) - date.fromisoformat(reference)).days
    if offset_days <= 0:
        return 0

    with conn:  # one transaction: commits on success, rolls back on any error
        for table, columns in DEMO_DATE_COLUMNS.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column not in existing:
                    continue
                conn.execute(
                    f"UPDATE {table} SET {column} = strftime('%Y-%m-%dT%H:%M:%S', {column}, ?) "
                    f"WHERE {column} IS NOT NULL",
                    (f"+{offset_days} days",),
                )
    return offset_days


def init_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    sql_script = SQL_SEED_PATH.read_text(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(sql_script)
        conn.commit()
        if os.getenv("SHOPDESK_NO_DATE_SHIFT") != "1":
            shift_demo_dates(conn)
    finally:
        conn.close()

    return DB_PATH


def main():
    path = init_database()
    print(f"Database created: {path}")


if __name__ == "__main__":
    main()

"""Shared helpers for the ShopDesk support-desk scripts (Python standard library only)."""

import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("SHOPDESK_DB", Path.home() / ".shopdesk" / "demo_store.db"))
DEFAULT_POLICIES = Path(os.environ.get("SHOPDESK_POLICIES", SKILL_DIR / "policies"))

# Demo dates in data/demo_store.sql are written relative to this day and shifted to today on setup.
DEMO_REFERENCE_DATE = "2026-11-25"

# Every date/time column in the demo schema (format 'YYYY-MM-DDTHH:MM:SS').
# Same list as src/langchain_bot/db_init.py in the ShopDesk app (the skill stays standalone).
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


def connect(db_path, read_only=True):
    db_path = Path(db_path).expanduser()
    if not db_path.exists():
        fail(f"Database not found: {db_path}. Run scripts/setup_demo.py first or pass --db.")
    if read_only:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def out(data):
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def fail(message, code=1):
    out({"ok": False, "error": message})
    sys.exit(code)


def norm_email(email):
    return email.strip().lower()


def has_column(conn, table, column):
    """True if the table has the column (older or custom databases may lack orders.delivered_at)."""
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))

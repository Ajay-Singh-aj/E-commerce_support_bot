"""Shared helpers for the ShopDesk support-desk scripts (Python standard library only)."""

import json
import os
import sqlite3
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("SHOPDESK_DB", Path.home() / ".shopdesk" / "demo_store.db"))
DEFAULT_POLICIES = Path(os.environ.get("SHOPDESK_POLICIES", SKILL_DIR / "policies"))


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

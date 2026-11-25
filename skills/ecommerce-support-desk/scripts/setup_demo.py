"""Create the demo store database (fictional "Voltcart" store) from data/demo_store.sql.

Usage: python scripts/setup_demo.py [--db PATH] [--force]
Default PATH: ~/.shopdesk/demo_store.db (or $SHOPDESK_DB).
"""

import argparse
import sqlite3
from pathlib import Path

from _common import DEFAULT_DB, SKILL_DIR, fail, out

SEED = SKILL_DIR / "data" / "demo_store.sql"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--force", action="store_true", help="overwrite an existing database")
    args = ap.parse_args()

    db = Path(args.db).expanduser()
    if db.exists() and not args.force:
        fail(f"{db} already exists. Pass --force to rebuild it (this erases its data).")
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SEED.read_text(encoding="utf-8"))
    conn.commit()
    customers = [r[0] for r in conn.execute("SELECT email FROM users WHERE role = 'customer' ORDER BY id")]
    orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    conn.close()
    out({"ok": True, "db": str(db), "demo_customers": customers, "orders": orders})


if __name__ == "__main__":
    main()

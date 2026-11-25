"""Create ecommerce.db from ecommerce_setup.sql (schema + demo data)."""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "ecommerce.db"
SQL_SEED_PATH = PROJECT_ROOT / "ecommerce_setup.sql"


def init_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    sql_script = SQL_SEED_PATH.read_text(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(sql_script)
    conn.commit()
    conn.close()

    return DB_PATH


def main():
    path = init_database()
    print(f"Database created: {path}")


if __name__ == "__main__":
    main()

"""Demo-date checks for the standalone agent skill (skills/ecommerce-support-desk). Standard library only.

Run: py tests/test_skill_demo_dates.py      (or: uv run python tests/test_skill_demo_dates.py)

Runs the skill's real scripts against a temporary database:
  S1 setup_demo.py rebases dates to today: newest delivery <= 7 days old, every delivery < 30 days
  S2 dry-run return on DELIVERED order 4 is eligible
  S3 --no-shift keeps every date exactly as in data/demo_store.sql
  S4 time travel (fake today 2027-03-01): dates shift 96 days, event order unchanged,
     order 4 is inside the window at the fake "now" and outside it 45 days later
"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "ecommerce-support-desk"
SCRIPTS = SKILL / "scripts"
SEED = SKILL / "data" / "demo_store.sql"
PRIYA = "priya.sharma@example.com"

sys.path.insert(0, str(SCRIPTS))
import _common  # noqa: E402
import actions  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="shopdesk_skill_test_"))
DB = TMP / "demo_store.db"
RESULTS = []


def check(test_id, description, ok, detail=""):
    RESULTS.append((test_id, description, bool(ok), detail))


def script(*args):
    env = {**os.environ, "SHOPDESK_DB": str(DB)}
    proc = subprocess.run([sys.executable, "-B", *[str(a) for a in args]], cwd=SKILL, env=env,
                          capture_output=True, text=True)
    return json.loads(proc.stdout)


def query(sql):
    conn = sqlite3.connect(DB)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def delivered_ages(now):
    return {oid: (now - datetime.fromisoformat(at)).total_seconds() / 86400
            for oid, at in query("SELECT id, delivered_at FROM orders WHERE status = 'DELIVERED'")}


def all_dates(conn):
    return {(t, c): conn.execute(f"SELECT id, {c} FROM {t} ORDER BY id").fetchall()
            for t, cols in _common.DEMO_DATE_COLUMNS.items() for c in cols}


def sql_file_dates():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SEED.read_text(encoding="utf-8"))
    try:
        return all_dates(conn)
    finally:
        conn.close()


def window_refusal(order_id, now):
    """None if the return window is open at `now`, else the refusal JSON the script would print."""
    conn = _common.connect(DB, read_only=True)
    try:
        order = actions.owned_order(conn, PRIYA, order_id)
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # the script prints its refusal JSON
                actions.check_return_window(conn, order, now=now)
            return None
        except SystemExit:
            return "refused"
    finally:
        conn.close()


def main():
    try:
        expected_shift = max(0, (date.today() - date.fromisoformat(_common.DEMO_REFERENCE_DATE)).days)
        setup = script("scripts/setup_demo.py")
        ages = delivered_ages(datetime.now())
        check("S1", "setup_demo.py rebases dates: newest delivery <= 7 days, all < 30",
              setup["ok"] and setup["dates_shifted_by_days"] == expected_shift
              and 0 <= min(ages.values()) <= 7 and max(ages.values()) < 30,
              f"shifted {setup['dates_shifted_by_days']}d | ages: "
              + ", ".join(f"#{k}={v:.1f}" for k, v in sorted(ages.items())))

        dry = script("scripts/actions.py", "return", "--email", PRIYA, "--order-id", 4,
                     "--product", "Voltcart LED Desk Lamp", "--reason", "Stopped working")
        check("S2", "dry-run return on DELIVERED order 4 is eligible",
              dry.get("ok") and dry.get("dry_run") and dry.get("eligible"),
              f"ok={dry.get('ok')} delivered_at={dry.get('delivered_at')} error={dry.get('error', '-')}")

        script("scripts/setup_demo.py", "--force", "--no-shift")
        conn = sqlite3.connect(DB)
        same = all_dates(conn) == sql_file_dates()
        conn.close()
        check("S3", "--no-shift keeps every date exactly as in demo_store.sql", same, f"identical={same}")

        fake_today = datetime(2027, 3, 1, 12, 0, 0)
        conn = sqlite3.connect(DB)
        before = all_dates(conn)
        offset = _common.shift_demo_dates(conn, today=fake_today.date())
        after = all_dates(conn)
        chronology = conn.execute(
            """SELECT COUNT(*) FROM returns r JOIN orders o ON o.id = r.order_id
               WHERE r.id IN (1, 2) AND o.order_date < o.delivered_at AND o.delivered_at < r.requested_at"""
        ).fetchone()[0] == 2
        conn.close()
        shifted_all = all(
            (datetime.fromisoformat(y) - datetime.fromisoformat(x)).days == offset
            for key in before for (_, x), (_, y) in zip(before[key], after[key]) if x is not None
        )
        open_now = window_refusal(4, fake_today) is None
        closed_later = window_refusal(4, datetime(2027, 4, 15, 12, 0, 0)) == "refused"
        check("S4", "time travel to 2027-03-01: +96 days, order kept, window still enforced",
              offset == 96 and shifted_all and chronology and open_now and closed_later,
              f"offset={offset} all+{offset}d={shifted_all} chronology={chronology} "
              f"order4 open@2027-03-01={open_now} refused@2027-04-15={closed_later}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"{'ID':4} {'RESULT':6}  {'TEST':66} DETAIL")
    for test_id, description, ok, detail in RESULTS:
        print(f"{test_id:4} {'PASS' if ok else 'FAIL':6}  {description[:66]:66} {detail}")
    failed = [r for r in RESULTS if not r[2]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed.")
    sys.exit(1 if failed or len(RESULTS) < 4 else 0)


if __name__ == "__main__":
    main()

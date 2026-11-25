"""Email + password login against the users table.

Passwords are stored as PBKDF2-SHA256 hashes in the format
``pbkdf2_sha256$<iterations>$<salt>$<hex digest>``. Use ``hash_password`` to create them.

Brute-force limit: after MAX_FAILED_LOGINS failed logins for one email within LOCKOUT_WINDOW,
that email is refused for LOCKOUT_DURATION (even with the right password). The counters are kept
in memory, per process.
"""

import hashlib
import hmac
import math
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import TypedDict

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"
ITERATIONS = 200_000

MAX_FAILED_LOGINS = 5
LOCKOUT_WINDOW = 15 * 60      # seconds in which the failures are counted
LOCKOUT_DURATION = 15 * 60    # seconds the email stays locked

_clock = time.monotonic       # replaced in tests
_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}


def _key(email):
    return (email or "").strip().lower()


def lockout_minutes_left(email):
    """Minutes until this email may try again (0 if it is not locked)."""
    with _lock:
        until = _locked_until.get(_key(email), 0)
    remaining = until - _clock()
    return math.ceil(remaining / 60) if remaining > 0 else 0


def _record_failure(key):
    now = _clock()
    with _lock:
        recent = [t for t in _failures.get(key, []) if now - t < LOCKOUT_WINDOW] + [now]
        _failures[key] = recent
        if len(recent) >= MAX_FAILED_LOGINS:
            _locked_until[key] = now + LOCKOUT_DURATION
            _failures[key] = []


def _clear_failures(key):
    with _lock:
        _failures.pop(key, None)
        _locked_until.pop(key, None)


class UserRecord(TypedDict):
    email: str
    full_name: str
    role: str


def hash_password(password: str, salt: str | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, salt, int(iterations)).split("$", 3)[3]
    return hmac.compare_digest(candidate, expected)


def authenticate_user(email, password, role=None):
    """Return the user as a dict if email + password (+ role) match, else None.
    Returns None without checking while the email is locked out (see lockout_minutes_left)."""
    key = _key(email)
    if lockout_minutes_left(key):
        return None

    sql = "SELECT email, full_name, role, password FROM users WHERE email = ?"
    params = [key]

    if role:
        sql += " AND role = ?"
        params.append(role)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(sql, params).fetchone()
    conn.close()

    if row is None or not verify_password(password, row[3]):
        _record_failure(key)
        return None
    _clear_failures(key)
    return UserRecord(email=row[0], full_name=row[1], role=row[2])

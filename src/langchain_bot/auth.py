"""Email + password login against the users table.

Passwords are stored as PBKDF2-SHA256 hashes in the format
``pbkdf2_sha256$<iterations>$<salt>$<hex digest>``. Use ``hash_password`` to create them.
"""

import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from typing import TypedDict

DB_PATH = Path(__file__).resolve().parents[2] / "ecommerce.db"
ITERATIONS = 200_000


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
    """Return the user as a dict if email + password (+ role) match, else None."""
    sql = "SELECT email, full_name, role, password FROM users WHERE email = ?"
    params = [email.strip().lower()]

    if role:
        sql += " AND role = ?"
        params.append(role)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(sql, params).fetchone()
    conn.close()

    if row is None or not verify_password(password, row[3]):
        return None
    return UserRecord(email=row[0], full_name=row[1], role=row[2])

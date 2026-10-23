"""Users and passwords in SQLite; argon2id hashes."""

import sqlite3
from contextlib import contextmanager
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from . import config

ph = PasswordHasher()


@contextmanager
def db():
    config.DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('coach','athlete')), athlete TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def get_user(uid):
    with db() as con:
        r = con.execute("SELECT id,email,name,role,athlete FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def verify(email, password):
    with db() as con:
        r = con.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not r:
        return None
    try:
        ph.verify(r["password_hash"], password)
    except VerifyMismatchError:
        return None
    if ph.check_needs_rehash(r["password_hash"]):
        set_password(r["id"], password)
    return {k: r[k] for k in ("id", "email", "name", "role", "athlete")}


def upsert_user(email, name, role, athlete, password):
    with db() as con:
        con.execute(
            """INSERT INTO users(email,name,role,athlete,password_hash) VALUES(?,?,?,?,?)
                       ON CONFLICT(email) DO UPDATE SET name=excluded.name, role=excluded.role, athlete=excluded.athlete,
                       password_hash=excluded.password_hash""",
            (email, name, role, athlete, ph.hash(password)),
        )


def set_password(uid, password):
    with db() as con:
        con.execute("UPDATE users SET password_hash=? WHERE id=?", (ph.hash(password), uid))


def create_athlete_account(email, name, password):
    """Self sign-up: an athlete account not yet tied to any athlete's data (the coach associates it)."""
    email = email.strip().lower()
    with db() as con:
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("já existe uma conta com este email")
        con.execute(
            "INSERT INTO users(email,name,role,athlete,password_hash) VALUES(?,?,?,?,?)",
            (email, name.strip()[:80], "athlete", None, ph.hash(password)),
        )
        return con.execute("SELECT id,email,name,role,athlete FROM users WHERE email=?", (email,)).fetchone()["id"]


def pending_athletes():
    """Accounts created by athletes and not yet associated with an athlete by the coach."""
    with db() as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id,email,name,created_at FROM users WHERE role='athlete' AND athlete IS NULL ORDER BY created_at"
            )
        ]


def set_athlete(uid, athlete):
    with db() as con:
        con.execute("UPDATE users SET athlete=? WHERE id=? AND role='athlete'", (athlete, uid))


RESET_MINUTES = 60


def _resets(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS password_resets (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL)"
    )


def create_reset(email):
    """A one-hour reset token for the account with this email, or None (the caller answers the same either way)."""
    import secrets, hashlib, time

    with db() as con:
        _resets(con)
        r = con.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if not r:
            return None
        con.execute("DELETE FROM password_resets WHERE user_id=? OR expires_at<?", (r["id"], time.time()))
        tok = secrets.token_urlsafe(32)
        con.execute(
            "INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)",
            (hashlib.sha256(tok.encode()).hexdigest(), r["id"], time.time() + RESET_MINUTES * 60),
        )
        return tok


def reset_user(token):
    """The account a valid, unexpired token belongs to (None otherwise)."""
    import hashlib, time

    with db() as con:
        _resets(con)
        r = con.execute(
            "SELECT user_id FROM password_resets WHERE token_hash=? AND expires_at>=?",
            (hashlib.sha256((token or "").encode()).hexdigest(), time.time()),
        ).fetchone()
    return get_user(r["user_id"]) if r else None


def use_reset(token, password):
    """Set the new password and spend the token. True on success."""
    import hashlib

    u = reset_user(token)
    if not u:
        return False
    set_password(u["id"], password)
    with db() as con:
        con.execute("DELETE FROM password_resets WHERE user_id=?", (u["id"],))
    return True


def delete_user(uid):
    with db() as con:
        con.execute("DELETE FROM users WHERE id=?", (uid,))


def list_users():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT id,email,name,role,athlete,created_at FROM users ORDER BY role,name")]

"""GLPI database client — credential verification and department (entity) lookup
via a read-only connection to GLPI's own MySQL/MariaDB database.

This talks to GLPI's database directly instead of its REST API (deliberately not
used here — see the admin's note in Admin > GLPI). It needs a read-only DB account
with SELECT on glpi_users, glpi_profiles_users, and glpi_entities. Nothing here ever
writes to GLPI's database.

Password verification: GLPI stores passwords via PHP's password_hash(), bcrypt by
default ('$2y$...'). Python's bcrypt library verifies the same hash once the PHP
'$2y$' marker is normalized to '$2b$' — the two are the same algorithm, '2y' is just
PHP's marker for a padding fix that was already applied upstream. Anything not in
bcrypt form (e.g. an LDAP-linked account with no local password) is treated as
unverifiable, not as a match.

Department: resolved from glpi_profiles_users (a user's entity assignments) joined
to glpi_entities. A user can have more than one entity assignment in GLPI; this
takes the lowest-id assignment as "the" department, which is the common single-entity
case. See _resolve_department()'s docstring if that assumption doesn't fit — the
per-department folder override (database.dept_folder_map) is independent of this and
does not need to change if it doesn't.

No Streamlit dependency — importable and testable on its own.
"""

from dataclasses import dataclass
from typing import List, Optional

import bcrypt
import pymysql
import pymysql.cursors

import database as db

DEFAULT_TIMEOUT = 10

# __system__ setting keys
S_ENABLED = "glpi_enabled"
S_DB_HOST = "glpi_db_host"
S_DB_PORT = "glpi_db_port"
S_DB_NAME = "glpi_db_name"
S_DB_USER = "glpi_db_user"
S_DB_PASSWORD = "glpi_db_password"
S_DB_SSL = "glpi_db_ssl"


class GlpiError(Exception):
    """Any failure talking to GLPI's database. Message is safe to show to an admin."""


@dataclass
class GlpiUser:
    username: str
    glpi_id: Optional[int]
    realname: str
    department: str
    department_id: Optional[int]


# ── Configuration ─────────────────────────────────────────────────────────────

def get_config() -> dict:
    return {
        "enabled": (db.get_setting("__system__", S_ENABLED, "0") or "0") == "1",
        "host": (db.get_setting("__system__", S_DB_HOST, "") or "").strip(),
        "port": int(db.get_setting("__system__", S_DB_PORT, "3306") or "3306"),
        "database": (db.get_setting("__system__", S_DB_NAME, "glpi") or "glpi").strip(),
        "user": (db.get_setting("__system__", S_DB_USER, "") or "").strip(),
        "password": db.get_setting("__system__", S_DB_PASSWORD, "") or "",
        "ssl": (db.get_setting("__system__", S_DB_SSL, "0") or "0") == "1",
    }


def set_config(host: str = None, port: int = None, database: str = None,
               user: str = None, password: str = None,
               enabled: bool = None, ssl: bool = None) -> None:
    if host is not None:
        db.set_setting("__system__", S_DB_HOST, host.strip())
    if port is not None:
        db.set_setting("__system__", S_DB_PORT, str(int(port)))
    if database is not None:
        db.set_setting("__system__", S_DB_NAME, database.strip())
    if user is not None:
        db.set_setting("__system__", S_DB_USER, user.strip())
    if password is not None:
        db.set_setting("__system__", S_DB_PASSWORD, password)
    if enabled is not None:
        db.set_setting("__system__", S_ENABLED, "1" if enabled else "0")
    if ssl is not None:
        db.set_setting("__system__", S_DB_SSL, "1" if ssl else "0")


def is_enabled() -> bool:
    cfg = get_config()
    return bool(cfg["enabled"] and cfg["host"] and cfg["database"] and cfg["user"])


def uses_unencrypted_db_connection() -> bool:
    """True if the GLPI DB connection is configured without TLS. Every login sends a
    password to this DB (as part of a SELECT + local bcrypt check, not a plaintext
    write) — an unencrypted link exposes it in transit exactly like plain HTTP would.
    """
    cfg = get_config()
    return bool(cfg["host"]) and not cfg["ssl"]


# ── Low-level DB access ───────────────────────────────────────────────────────

def _connect(cfg: dict):
    try:
        return pymysql.connect(
            host=cfg["host"], port=cfg["port"], database=cfg["database"],
            user=cfg["user"], password=cfg["password"],
            connect_timeout=DEFAULT_TIMEOUT, read_timeout=DEFAULT_TIMEOUT,
            ssl={"ssl": {}} if cfg["ssl"] else None,
            cursorclass=pymysql.cursors.DictCursor,
            charset="utf8mb4",
        )
    except pymysql.MySQLError as e:
        raise GlpiError(f"Could not connect to GLPI's database at {cfg['host']}: {e}") from e


def _verify_glpi_password(password: str, stored_hash: str) -> bool:
    """Check a password against GLPI's stored password_hash() value. Returns False
    (never raises) for a hash format this cannot verify, e.g. an LDAP-linked account
    with no local password — that is correctly "not a match", not an error."""
    if not stored_hash:
        return False
    h = stored_hash.encode("utf-8")
    if h.startswith(b"$2y$"):
        h = b"$2b$" + h[4:]  # PHP's bcrypt marker -> the one Python's bcrypt expects
    if not h.startswith((b"$2a$", b"$2b$", b"$2x$")):
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), h)
    except (ValueError, TypeError):
        return False


def _resolve_department(cursor, glpi_user_id: int) -> tuple:
    """Return (department_name, entity_id) for a GLPI user, via their lowest-id
    entity assignment in glpi_profiles_users. Returns ("", None) if the user has no
    entity assignment. A user with multiple assignments only ever gets one department
    here — the admin override table (database.dept_folder_map) maps whatever comes
    back to a folder, so a wrong pick is a one-line fix there, not a code change."""
    cursor.execute(
        "SELECT e.id AS entity_id, e.name AS entity_name "
        "FROM glpi_profiles_users pu "
        "JOIN glpi_entities e ON e.id = pu.entities_id "
        "WHERE pu.users_id = %s "
        "ORDER BY pu.id ASC LIMIT 1",
        (glpi_user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return "", None
    return (row["entity_name"] or "").strip(), row["entity_id"]


# ── High-level operations ─────────────────────────────────────────────────────

def authenticate(username: str, password: str) -> Optional[GlpiUser]:
    """Verify credentials against GLPI's database and return the user with their
    department. Returns None if the username doesn't exist, the password doesn't
    match, or the account is inactive/deleted. Raises GlpiError if the database
    could not be reached at all, so the caller can fall back to local auth instead
    of treating a DB outage as "wrong password"."""
    cfg = get_config()
    if not is_enabled():
        raise GlpiError("GLPI database integration is not configured.")

    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, password, firstname, realname, is_active, is_deleted "
                "FROM glpi_users WHERE name = %s LIMIT 1",
                (username,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            if row["is_deleted"] or not row["is_active"]:
                return None
            if not _verify_glpi_password(password, row["password"] or ""):
                return None

            dept, dept_id = _resolve_department(cur, row["id"])
            realname = " ".join(p for p in [row["firstname"] or "", row["realname"] or ""] if p).strip()
            return GlpiUser(
                username=row["name"], glpi_id=row["id"], realname=realname,
                department=dept, department_id=dept_id,
            )
    except pymysql.MySQLError as e:
        raise GlpiError(f"GLPI database query failed: {e}") from e
    finally:
        conn.close()


def test_connection() -> tuple:
    """Admin 'Test' button. Confirms the DB is reachable and the expected tables and
    columns are present, without requiring a real user's password."""
    cfg = get_config()
    if not cfg["host"] or not cfg["database"] or not cfg["user"]:
        return False, "Host, database name, and username must all be set."

    try:
        conn = _connect(cfg)
    except GlpiError as e:
        return False, str(e)

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM glpi_users")
            n_users = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM glpi_entities")
            n_entities = cur.fetchone()["n"]
        return True, (
            f"Connected. Found {n_users} row(s) in glpi_users and "
            f"{n_entities} in glpi_entities."
        )
    except pymysql.MySQLError as e:
        return False, (
            f"Connected, but the expected GLPI tables/columns were not found "
            f"({e}). Confirm the database name and that this account has SELECT "
            f"on glpi_users / glpi_profiles_users / glpi_entities."
        )
    finally:
        conn.close()


def lookup_user(username: str) -> Optional[dict]:
    """Admin diagnostic: resolve a username's department WITHOUT a password, so an
    admin can verify the department mapping is correct before rolling out to
    everyone. Returns None if the username doesn't exist."""
    cfg = get_config()
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, firstname, realname, is_active, is_deleted, "
                "password FROM glpi_users WHERE name = %s LIMIT 1",
                (username,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            dept, dept_id = _resolve_department(cur, row["id"])
            return {
                "username": row["name"],
                "realname": " ".join(p for p in [row["firstname"] or "", row["realname"] or ""] if p).strip(),
                "is_active": bool(row["is_active"]) and not bool(row["is_deleted"]),
                "department": dept,
                "department_id": dept_id,
                "has_verifiable_password": _password_looks_verifiable(row["password"] or ""),
            }
    except pymysql.MySQLError as e:
        raise GlpiError(f"Lookup failed: {e}") from e
    finally:
        conn.close()


def _password_looks_verifiable(stored_hash: str) -> bool:
    return stored_hash.startswith(("$2y$", "$2a$", "$2b$", "$2x$"))


def fetch_users() -> List[dict]:
    """The GLPI user roster: login, real name, department, active flag. One query,
    one entity per user (see _resolve_department). Returns dicts with keys:
    username, realname, department, is_active.
    """
    cfg = get_config()
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            # One entity per user: the lowest-id glpi_profiles_users row, same rule
            # as _resolve_department but done as a single set-based query instead of
            # one round trip per user — this runs against ~1000 rows, not one at a time.
            cur.execute("""
                SELECT u.name AS username, u.firstname, u.realname,
                       u.is_active, u.is_deleted, e.name AS department
                FROM glpi_users u
                LEFT JOIN (
                    SELECT pu1.users_id, pu1.entities_id
                    FROM glpi_profiles_users pu1
                    WHERE pu1.id = (
                        SELECT MIN(pu2.id) FROM glpi_profiles_users pu2
                        WHERE pu2.users_id = pu1.users_id
                    )
                ) pu ON pu.users_id = u.id
                LEFT JOIN glpi_entities e ON e.id = pu.entities_id
                WHERE u.name IS NOT NULL AND u.name != ''
            """)
            rows = cur.fetchall()
    except pymysql.MySQLError as e:
        raise GlpiError(f"Could not list users: {e}") from e
    finally:
        conn.close()

    out = []
    for r in rows:
        out.append({
            "username": r["username"],
            "realname": " ".join(p for p in [r["firstname"] or "", r["realname"] or ""] if p).strip(),
            "department": (r["department"] or "").strip(),
            "is_active": bool(r["is_active"]) and not bool(r["is_deleted"]),
        })
    return out

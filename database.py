import sqlite3
import os
import base64
import hashlib
import hmac
import secrets
from typing import List, Optional

DB_PATH = "database.db"

# Password hashing: PBKDF2-HMAC-SHA256, salted, stdlib only (no new dependency).
PBKDF2_ITERATIONS = 260_000
PBKDF2_ALGO = "pbkdf2_sha256"

# Session lifetime for the login token handed back to the browser.
SESSION_TTL_HOURS = 12

# Stored in users.password for accounts whose credentials live in an external
# directory (GLPI). Cannot match any input, so such accounts can only log in
# through that directory.
UNUSABLE_PASSWORD = "!"

AUTH_LOCAL = "local"
AUTH_GLPI = "glpi"

def get_connection():
    return sqlite3.connect(DB_PATH)

def hash_password(password: str) -> str:
    """Return a salted PBKDF2-HMAC-SHA256 hash: pbkdf2_sha256$<iters>$<salt>$<hash>."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "{}${}${}${}".format(
        PBKDF2_ALGO,
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )

def _is_legacy_sha256(value: str) -> bool:
    """Return True if value looks like a bare SHA-256 hex digest (64 hex chars)."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())

def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash. Accepts current PBKDF2 hashes and
    legacy unsalted SHA-256 digests (so existing accounts keep working until their
    next successful login, which upgrades them in place)."""
    if not stored:
        return False
    # Unusable-password sentinel: directory-backed accounts authenticate elsewhere
    # and must never match a local password, whatever the user types.
    if stored.startswith(UNUSABLE_PASSWORD):
        return False
    if stored.startswith(PBKDF2_ALGO + "$"):
        try:
            _, iters, salt_b64, hash_b64 = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"),
                base64.b64decode(salt_b64), int(iters)
            )
            return hmac.compare_digest(dk, base64.b64decode(hash_b64))
        except (ValueError, TypeError):
            return False
    if _is_legacy_sha256(stored):
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, stored.lower())
    # Anything else is a plaintext leftover — compare directly, then it gets upgraded.
    return hmac.compare_digest(password, stored)

def _needs_rehash(stored: str) -> bool:
    """True if a stored hash is not in the current PBKDF2 format/cost."""
    if not stored.startswith(PBKDF2_ALGO + "$"):
        return True
    try:
        return int(stored.split("$")[1]) < PBKDF2_ITERATIONS
    except (ValueError, IndexError):
        return True

def _hash_token(token: str) -> str:
    """Session tokens are stored hashed, so a DB read cannot be replayed as a login."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin BOOLEAN NOT NULL CHECK (is_admin IN (0, 1)),
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Simple migrations to add columns if they don't exist yet
    for ddl in (
        "ALTER TABLE users ADD COLUMN last_seen DATETIME",
        "ALTER TABLE users ADD COLUMN department TEXT",
        "ALTER TABLE users ADD COLUMN realname TEXT",
        "ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local'",
        "ALTER TABLE users ADD COLUMN glpi_id INTEGER",
        "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
    ):
        try:
            cursor.execute(ddl)
        except sqlite3.OperationalError:
            pass  # Already exists

    # Department -> shared-viewer-folder overrides, for when a GLPI entity name
    # doesn't match its folder name exactly. Unmapped departments fall back to a
    # case-insensitive match against the subfolder name (see sop_folder_for_department).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dept_folder_map (
            department  TEXT PRIMARY KEY COLLATE NOCASE,
            folder_name TEXT NOT NULL
        )
    ''')

    # Create settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT,
            UNIQUE(username, setting_key)
        )
    ''')

    # Create permissions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS permissions (
            username TEXT NOT NULL,
            permission_key TEXT NOT NULL,
            allowed INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (username, permission_key)
        )
    ''')

    # Create sessions table (replaces the old settings-based session_token)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username)")

    # Drop legacy session tokens: they never expired, were stored in cleartext, and an
    # empty value matched any logged-out user.
    cursor.execute("DELETE FROM settings WHERE setting_key = 'session_token'")

    # Check if users table is empty → seed default admin
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    if count == 0:
        cursor.execute(
            "INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
            ("admin", hash_password("admin123"), 1)
        )

    # Migrate any remaining plaintext passwords straight to PBKDF2. Legacy unsalted
    # SHA-256 digests cannot be re-derived here, so they are upgraded on next login.
    cursor.execute("SELECT username, password FROM users")
    for uname, pw in cursor.fetchall():
        if not pw.startswith(PBKDF2_ALGO + "$") and not _is_legacy_sha256(pw):
            cursor.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (hash_password(pw), uname)
            )

    conn.commit()
    conn.close()

def authenticate_user(username, password):
    """Returns the user row (dict-like) if authenticated, None otherwise.
    Transparently upgrades legacy password hashes on successful login."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()

    if user is None or not verify_password(password, user["password"]):
        conn.close()
        return None

    if _needs_rehash(user["password"]):
        cursor.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (hash_password(password), user["username"])
        )
        conn.commit()

    conn.close()
    return user

def get_user_by_username(username):
    """Retrieves a user by their username."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_last_seen(username):
    """Updates the last_seen timestamp for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def get_active_users(minutes=5):
    """Returns a list of users active within the last X minutes."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Use strftime to handle SQLite DATETIME comparison
    cursor.execute("""
        SELECT username, is_admin, last_seen 
        FROM users 
        WHERE last_seen >= datetime('now', '-' || ? || ' minute')
        ORDER BY last_seen DESC
    """, (minutes,))
    users = cursor.fetchall()
    conn.close()
    return users

def get_setting(username, key, default=None):
    """Retrieves a setting for a user. Returns default if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT setting_value FROM settings WHERE username = ? AND setting_key = ?", (username, key))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0]
    return default

def set_setting(username, key, value):
    """Saves or updates a setting for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO settings (username, setting_key, setting_value)
        VALUES (?, ?, ?)
        ON CONFLICT(username, setting_key) 
        DO UPDATE SET setting_value=excluded.setting_value
    ''', (username, key, value))
    conn.commit()
    conn.close()

def get_all_users():
    """Returns list of all user records."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY username")
    users = cursor.fetchall()
    conn.close()
    return users

# ── GLPI-backed accounts ────────────────────────────────────────────────────
#
# GLPI-sourced users never have a usable local password (see UNUSABLE_PASSWORD):
# they can only log in by GLPI verifying the credential live. What lives here is
# just a roster cache — department, display name, GLPI id — used for permissions,
# the admin dashboard, and scoping the SOP folder. It is never the auth decision.

class LocalAccountConflictError(Exception):
    """Raised when a GLPI login username collides with an existing LOCAL account.

    This is a hard conflict, not a merge: GLPI accepting a credential for a username
    that already belongs to a local account here (e.g. the seeded 'admin' fallback,
    or any GLPI instance's own admin-named superuser) must never silently reclassify
    that account as GLPI-backed or log the browser in as it. Two different credential
    stores agreeing on a username is not proof they mean the same person. Callers
    must check for this conflict (see index.py's login flow) before this is ever hit;
    it exists here too as defense in depth.
    """

def provision_glpi_user(username: str, realname: str, department: str,
                         glpi_id: Optional[int]) -> "sqlite3.Row":
    """Create or refresh a GLPI-authenticated user's roster row. Called right after
    GLPI confirms the credential, so this always reflects a just-verified login.
    Never touches is_admin — that stays a local decision made in this app.

    Raises LocalAccountConflictError instead of touching a pre-existing local
    account — see that class's docstring.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    existing = cursor.fetchone()

    if existing is None:
        cursor.execute(
            "INSERT INTO users (username, password, is_admin, department, "
            "realname, auth_source, glpi_id, is_active) VALUES (?, ?, 0, ?, ?, ?, ?, 1)",
            (username, UNUSABLE_PASSWORD, department, realname, AUTH_GLPI, glpi_id)
        )
    elif existing["auth_source"] != AUTH_GLPI:
        conn.close()
        raise LocalAccountConflictError(
            f"'{username}' is a local account here; refusing to convert it to GLPI."
        )
    else:
        cursor.execute(
            "UPDATE users SET department = ?, realname = ?, glpi_id = ?, is_active = 1 "
            "WHERE username = ?",
            (department, realname, glpi_id, username)
        )

    conn.commit()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return row

def sync_glpi_roster(rows: List[dict]) -> dict:
    """Bulk-provision GLPI users from an admin-triggered roster pull (glpi.fetch_users()).

    Unlike provision_glpi_user, this only ever touches accounts that are already
    auth_source='glpi' (or don't exist yet) — a pre-existing local account with a
    colliding username is left completely alone and reported as skipped, so a roster
    import can never silently strip someone's local admin access.

    Returns {"created": n, "updated": n, "deactivated": n, "skipped_local": [usernames]}.
    """
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    stats = {"created": 0, "updated": 0, "deactivated": 0, "skipped_local": []}
    seen_usernames = set()

    for r in rows:
        uname = (r.get("username") or "").strip()
        if not uname:
            continue
        seen_usernames.add(uname)

        cursor.execute("SELECT auth_source FROM users WHERE username = ?", (uname,))
        existing = cursor.fetchone()

        if existing is None:
            cursor.execute(
                "INSERT INTO users (username, password, is_admin, department, "
                "realname, auth_source, is_active) VALUES (?, ?, 0, ?, ?, ?, ?)",
                (uname, UNUSABLE_PASSWORD, r.get("department", ""), r.get("realname", ""),
                 AUTH_GLPI, 1 if r.get("is_active", True) else 0)
            )
            stats["created"] += 1
        elif existing["auth_source"] == AUTH_GLPI:
            cursor.execute(
                "UPDATE users SET department = ?, realname = ?, is_active = ? WHERE username = ?",
                (r.get("department", ""), r.get("realname", ""),
                 1 if r.get("is_active", True) else 0, uname)
            )
            stats["updated"] += 1
        else:
            stats["skipped_local"].append(uname)

    # Anyone previously imported from GLPI but absent from this pull has left the
    # directory (or the entity/group no longer includes them) — mark inactive rather
    # than delete, so their history and permissions stay intact if they return.
    cursor.execute("SELECT username FROM users WHERE auth_source = ? AND is_active = 1", (AUTH_GLPI,))
    for (uname,) in cursor.fetchall():
        if uname not in seen_usernames:
            cursor.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname,))
            stats["deactivated"] += 1

    conn.commit()
    conn.close()
    return stats

def get_all_departments() -> List[str]:
    """Distinct, non-empty departments currently present in the roster."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT department FROM users "
        "WHERE department IS NOT NULL AND department != '' ORDER BY department"
    )
    depts = [row[0] for row in cursor.fetchall()]
    conn.close()
    return depts

def get_dept_folder_map() -> dict:
    """{department: folder_name} overrides for when a GLPI entity name doesn't
    match its SOP subfolder name exactly."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT department, folder_name FROM dept_folder_map")
    result = dict(cursor.fetchall())
    conn.close()
    return result

def set_dept_folder_map(department: str, folder_name: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO dept_folder_map (department, folder_name) VALUES (?, ?)
        ON CONFLICT(department) DO UPDATE SET folder_name = excluded.folder_name
    ''', (department.strip(), folder_name.strip()))
    conn.commit()
    conn.close()

def delete_dept_folder_map(department: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM dept_folder_map WHERE department = ?", (department,))
    conn.commit()
    conn.close()

def get_permission(username, key, default=True):
    """Returns True/False for a permission. Falls back to default if not set."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT allowed FROM permissions WHERE username = ? AND permission_key = ?",
        (username, key)
    )
    result = cursor.fetchone()
    conn.close()
    if result is None:
        return default
    return bool(result[0])

def get_user_permissions(username):
    """Returns {permission_key: bool} for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT permission_key, allowed FROM permissions WHERE username = ?",
        (username,)
    )
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: bool(row[1]) for row in rows}

def set_user_permissions(username, permissions_dict):
    """Bulk-upsert permissions for a user: {permission_key: bool}."""
    conn = get_connection()
    cursor = conn.cursor()
    for key, allowed in permissions_dict.items():
        cursor.execute('''
            INSERT INTO permissions (username, permission_key, allowed)
            VALUES (?, ?, ?)
            ON CONFLICT(username, permission_key)
            DO UPDATE SET allowed=excluded.allowed
        ''', (username, key, 1 if allowed else 0))
    conn.commit()
    conn.close()

def create_user(username: str, password: str, is_admin: bool = False) -> str | None:
    """Create a new user. Returns None on success, or an error message string."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
            (username.strip(), hash_password(password), 1 if is_admin else 0)
        )
        conn.commit()
        return None
    except sqlite3.IntegrityError:
        return f"Username '{username}' already exists."
    finally:
        conn.close()

def delete_user(username: str) -> None:
    """Delete a user and all their settings/permissions/sessions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM settings WHERE username = ?", (username,))
    cursor.execute("DELETE FROM permissions WHERE username = ?", (username,))
    cursor.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def change_password(username: str, new_password: str) -> None:
    """Update a user's password (stores hashed) and invalidate existing sessions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password = ? WHERE username = ?",
        (hash_password(new_password), username)
    )
    cursor.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def set_admin(username: str, is_admin: bool) -> None:
    """Toggle a user's admin status."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_admin = ? WHERE username = ?",
        (1 if is_admin else 0, username)
    )
    conn.commit()
    conn.close()

def create_session(username, ttl_hours: int = SESSION_TTL_HOURS) -> str:
    """Issue a new session token. Only its hash is stored; the raw token is returned once."""
    token = secrets.token_urlsafe(32)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    cursor.execute(
        "INSERT INTO sessions (token_hash, username, expires_at) "
        "VALUES (?, ?, datetime('now', '+' || ? || ' hours'))",
        (_hash_token(token), username, int(ttl_hours))
    )
    conn.commit()
    conn.close()
    return token

def get_user_by_session(token):
    """Resolve a session token to a username, or None if missing/expired/invalid."""
    # Guard against empty or truncated tokens being treated as a lookup key.
    if not token or len(token) < 20:
        return None
    # Read-only on purpose: this runs on every page load, so expired rows are left
    # for create_session() to sweep rather than writing to SQLite on each request.
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT username FROM sessions WHERE token_hash = ? AND expires_at > datetime('now')",
        (_hash_token(token),)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def destroy_session(token) -> None:
    """Invalidate a single session token (logout)."""
    if not token:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    conn.commit()
    conn.close()

def destroy_user_sessions(username: str) -> None:
    """Invalidate every session for a user (password change, deletion, admin revoke)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.commit()
    conn.close()

# Initialize upon import
init_db()

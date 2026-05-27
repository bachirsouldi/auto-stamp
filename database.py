import sqlite3
import os
import hashlib

DB_PATH = "database.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def hash_password(password: str) -> str:
    """Return SHA-256 hex digest of a password."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def _is_hashed(value: str) -> bool:
    """Return True if value looks like a SHA-256 hex digest (64 hex chars)."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())

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

    # Simple migration to add last_seen column if it doesn't exist yet
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_seen DATETIME")
    except sqlite3.OperationalError:
        pass # Already exists

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

    # Check if users table is empty → seed default admin
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    if count == 0:
        cursor.execute(
            "INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
            ("admin", hash_password("admin123"), 1)
        )

    # Migrate any existing plaintext passwords to hashed form
    cursor.execute("SELECT username, password FROM users")
    rows = cursor.fetchall()
    for uname, pw in rows:
        if not _is_hashed(pw):
            cursor.execute(
                "UPDATE users SET password = ? WHERE username = ?",
                (hash_password(pw), uname)
            )

    conn.commit()
    conn.close()

def authenticate_user(username, password):
    """Returns the user row (dict-like) if authenticated, None otherwise."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, hash_password(password))
    )
    user = cursor.fetchone()
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
    """Delete a user and all their settings/permissions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    cursor.execute("DELETE FROM settings WHERE username = ?", (username,))
    cursor.execute("DELETE FROM permissions WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def change_password(username: str, new_password: str) -> None:
    """Update a user's password (stores hashed)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password = ? WHERE username = ?",
        (hash_password(new_password), username)
    )
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

def create_session(username):
    import uuid
    token = str(uuid.uuid4())
    set_setting(username, "session_token", token)
    return token

def get_user_by_session(token):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM settings WHERE setting_key = 'session_token' AND setting_value = ?", (token,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result[0]
    return None

# Initialize upon import
init_db()

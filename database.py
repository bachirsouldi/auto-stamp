import sqlite3
import os

DB_PATH = "database.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

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
    
    # Check if users table is empty
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    # Insert default admin if no users exist
    if count == 0:
        cursor.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)", ("admin", "admin123", 1))
    
    conn.commit()
    conn.close()

def authenticate_user(username, password):
    """Returns the user row (dict-like) if authenticated, None otherwise."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
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

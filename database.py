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
            is_admin BOOLEAN NOT NULL CHECK (is_admin IN (0, 1))
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
    
    # Check if users table is empty
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    # Insert default admin if no users exist
    if count == 0:
        cursor.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)", ("admin", "admin123", 1))
    
    conn.commit()
    conn.close()

def authenticate_user(username, password):
    """Returns True if the user exists and password matches, False otherwise."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? AND password = ?", (username, password))
    user = cursor.fetchone()
    conn.close()
    return user is not None

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

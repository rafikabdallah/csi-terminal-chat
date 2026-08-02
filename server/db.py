"""SQLite persistence for user accounts.

All SQL lives here. A fresh connection is opened per call so that
each client thread is isolated — SQLite connections are not
thread-safe by default.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat.db")


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    """Create the users table if it does not exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT    NOT NULL UNIQUE,
                salt        TEXT    NOT NULL,
                password_hash TEXT  NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def create_user(username, salt_hex, hash_hex):
    """Insert a new user. Returns False if the username is taken."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                (username, salt_hex, hash_hex)
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_user(username):
    """Return (salt, password_hash) or None if no such user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()
    return row

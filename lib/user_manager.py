# lib/user_manager.py
import json
import logging
from werkzeug.security import generate_password_hash
from .database import get_db_connection

logger = logging.getLogger()

class UserManager:
    """
    Manages user accounts and permissions in the database.
    Provides methods for creating, reading, updating, and deleting users.
    """
    def __init__(self):
        self._ensure_default_admin_user()

    def _ensure_default_admin_user(self):
        """Ensures a default, password-less admin user exists on first run."""
        conn = get_db_connection()
        admin = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
        if not admin:
            logger.info("Default admin account not found. Creating a new one.")
            conn.execute(
                "INSERT INTO users (username, password_hash, permissions) VALUES (?, ?, ?)",
                ('admin', None, json.dumps({}))
            )
            conn.commit()
        conn.close()

    def get_all_users(self):
        """Loads and returns all users, omitting password hashes for safety."""
        conn = get_db_connection()
        users_raw = conn.execute("SELECT username, permissions FROM users").fetchall()
        conn.close()
        
        safe_users = {}
        for user in users_raw:
            safe_users[user['username']] = {'permissions': json.loads(user['permissions'])}
        return safe_users

    def get_user(self, username):
        """Retrieves a specific user's data from the database."""
        conn = get_db_connection()
        user_raw = conn.execute("SELECT * FROM users WHERE username = ?", (username.lower(),)).fetchone()
        conn.close()
        
        if user_raw:
            user_raw['permissions'] = json.loads(user_raw['permissions'])
        return user_raw

    def add_user(self, username, password, permissions=None):
        """Adds a new user. Returns False if user already exists."""
        username = username.lower()
        if self.get_user(username):
            return False
        
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO users (username, password_hash, permissions) VALUES (?, ?, ?)",
            (
                username,
                generate_password_hash(password) if password else None,
                json.dumps(permissions or {})
            )
        )
        conn.commit()
        conn.close()
        return True

    def update_user(self, username, password=None, permissions=None):
        """Updates a user's password and/or permissions."""
        username = username.lower()
        user = self.get_user(username)
        if not user:
            return False # Or create user if that's desired behavior

        conn = get_db_connection()
        if password is not None:
            new_hash = generate_password_hash(password) if password else None
            conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, username))
        
        if permissions is not None:
            conn.execute("UPDATE users SET permissions = ? WHERE username = ?", (json.dumps(permissions), username))

        conn.commit()
        conn.close()
        return True

    def delete_user(self, username):
        """Deletes a user. Cannot delete the primary 'admin' user."""
        username = username.lower()
        if username == 'admin':
            return False # Safety check
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        conn.close()
        return cursor.rowcount > 0

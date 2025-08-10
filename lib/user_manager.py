# lib/user_manager.py
import json
import os
import shutil
import time
import threading
import logging
from werkzeug.security import generate_password_hash

logger = logging.getLogger()

class UserManager:
    """
    Manages user accounts and permissions in a dedicated users.json file.
    Provides thread-safe methods for creating, reading, updating, and deleting users.
    """
    def __init__(self, users_file_path: str):
        self.users_file = users_file_path
        self.lock_file = users_file_path + ".lock"
        self.file_lock = threading.RLock()
        # CHANGE: Ensure a default admin user exists on first run.
        self._ensure_default_admin_user()

    def _ensure_default_admin_user(self):
        """
        Checks if the users file exists. If not, it creates one with a default,
        password-less admin user. This simplifies the first-run experience.
        """
        with self.file_lock:
            if not os.path.exists(self.users_file):
                logger.info("Users file not found. Creating a new one with a default admin account.")
                default_users = {
                    "admin": {
                        "password_hash": None,
                        "permissions": {} # Admin has all permissions implicitly
                    }
                }
                self._save_users(default_users)

    def _acquire_lock(self, timeout=5):
        """Acquires an exclusive file lock, with a timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if os.path.exists(self.lock_file):
                    lock_age = time.time() - os.path.getmtime(self.lock_file)
                    if lock_age > 60:
                        logger.warning(f"Found stale lock file older than 60s: {self.lock_file}. Removing it.")
                        self._release_lock()
                
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Unexpected error acquiring users lock: {e}")
                return False
        logger.error(f"Could not acquire users lock file after {timeout} seconds.")
        return False

    def _release_lock(self):
        try:
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
        except Exception as e:
            logger.error(f"Could not release users lock file: {e}")

    def _load_users(self):
        """Loads users from JSON, with a fallback to a backup file."""
        if not os.path.exists(self.users_file):
            return {}
        
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip(): return {}
                return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load main users file: {e}. Attempting backup.")
            backup_file = self.users_file + ".bak"
            if os.path.exists(backup_file):
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as bak_e:
                    logger.error(f"Could not load backup users file: {bak_e}. Returning empty dict.")
        return {}

    def _save_users(self, users_data):
        """Saves the users dictionary to a JSON file atomically with a backup."""
        with self.file_lock:
            if not self._acquire_lock():
                logger.error("Could not acquire lock to save users. Aborting.")
                return

            try:
                temp_file = self.users_file + ".tmp"
                backup_file = self.users_file + ".bak"
                
                if os.path.exists(self.users_file):
                    shutil.copy2(self.users_file, backup_file)

                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(users_data, f, indent=4)
                
                os.replace(temp_file, self.users_file)
            except Exception as e:
                logger.error(f"Could not save users file: {e}")
            finally:
                self._release_lock()

    def get_all_users(self):
        """Loads and returns all users, omitting password hashes for safety."""
        users = self._load_users()
        safe_users = {}
        for username, data in users.items():
            safe_users[username] = {k: v for k, v in data.items() if k != 'password_hash'}
        return safe_users

    def get_user(self, username):
        """Loads all users and returns the data for a specific user."""
        users = self._load_users()
        return users.get(username.lower())

    def add_user(self, username, password, permissions=None):
        """Adds a new user. Returns False if user already exists."""
        username = username.lower()
        users = self._load_users()
        if username in users:
            return False
        
        users[username] = {
            "password_hash": generate_password_hash(password) if password else None,
            "permissions": permissions or {}
        }
        self._save_users(users)
        return True

    def update_user(self, username, password=None, permissions=None):
        """Updates a user's password and/or permissions."""
        username = username.lower()
        users = self._load_users()
        if username not in users:
            # If user doesn't exist, create them. Useful for migration.
            users[username] = {"password_hash": None, "permissions": {}}
        
        if password is not None:
            users[username]["password_hash"] = generate_password_hash(password) if password else None
        
        if permissions is not None:
            users[username]["permissions"] = permissions

        self._save_users(users)
        return True

    def delete_user(self, username):
        """Deletes a user. Cannot delete the primary 'admin' user."""
        username = username.lower()
        if username == 'admin':
            return False # Safety check
            
        users = self._load_users()
        if username in users:
            del users[username]
            self._save_users(users)
            return True
        return False

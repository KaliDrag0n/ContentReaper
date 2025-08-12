# lib/config_manager.py
import os
import json
import logging
from . import app_globals as g

logger = logging.getLogger()

def load_config():
    """Loads configuration, sets defaults, and validates paths."""
    config_path = os.path.join(g.DATA_DIR, "config.json")
    
    defaults = {
        "download_dir": os.path.join(g.APP_ROOT, "downloads"),
        "temp_dir": os.path.join(g.APP_ROOT, ".temp"),
        "server_host": "0.0.0.0",
        "server_port": 8080,
        "log_level": "INFO",
        "public_user": None,
        "user_timezone": "UTC"
    }
    
    g.CONFIG.update(defaults)
    config_updated = False

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                
                # This handles the legacy migration logic from the old web_tool.py
                if "users" in loaded_config or "guest_permissions" in loaded_config:
                    logger.warning("Old user config format detected. Migrating to users.json.")
                    users_to_migrate = loaded_config.pop("users", {})
                    guest_perms = loaded_config.pop("guest_permissions", {})

                    if "admin" in users_to_migrate and users_to_migrate["admin"].get("password_hash"):
                        admin_user = {"password_hash": users_to_migrate["admin"]["password_hash"], "permissions": {}}
                        g.user_manager.update_user("admin", password=None, permissions=admin_user["permissions"])
                        all_users = g.user_manager._load_users()
                        all_users['admin']['password_hash'] = admin_user['password_hash']
                        g.user_manager._save_users(all_users)

                    if "guest" in users_to_migrate:
                        guest_user = {"password_hash": users_to_migrate["guest"].get("password_hash"), "permissions": guest_perms}
                        g.user_manager.update_user("guest", password=None, permissions=guest_user["permissions"])
                        all_users = g.user_manager._load_users()
                        all_users['guest']['password_hash'] = guest_user['password_hash']
                        g.user_manager._save_users(all_users)

                    config_updated = True
                
                g.CONFIG.update(loaded_config)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load config.json, using defaults. Error: {e}")
    
    log_level = g.CONFIG.get("log_level", "INFO").upper()
    if log_level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        logger.setLevel(getattr(logging, log_level))
        logger.info(f"Log level set to {log_level}")
    else:
        logger.warning(f"Invalid log_level '{log_level}' in config.json. Defaulting to INFO.")

    for key, name in [("download_dir", "Download"), ("temp_dir", "Temporary")]:
        path = g.CONFIG.get(key)
        if not path or not os.path.isabs(path):
            logger.critical(f"{name} directory path must be an absolute path. Path: '{path}'")
            raise RuntimeError(f"Configuration validation failed for '{key}'.")
        try:
            os.makedirs(path, exist_ok=True)
            if not os.access(path, os.W_OK):
                raise OSError("No write permissions.")
        except Exception as e:
            logger.critical(f"Path for '{key}' ('{path}') is invalid: {e}")
            raise RuntimeError(f"Configuration validation failed for '{key}'.")

    if config_updated or not os.path.exists(config_path):
        save_config()

def save_config():
    """Saves the current configuration to config.json."""
    config_path = os.path.join(g.DATA_DIR, "config.json")
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(g.CONFIG, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")

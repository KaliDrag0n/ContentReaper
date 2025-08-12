# lib/database.py
import sqlite3
import json
import os
import logging
from . import app_globals as g

logger = logging.getLogger()

def dict_factory(cursor, row):
    """Converts database query results into dictionaries."""
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    db_path = os.path.join(g.DATA_DIR, 'contentreaper.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = dict_factory
    return conn

def create_tables():
    """Creates all necessary database tables if they don't already exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT,
        permissions TEXT NOT NULL
    )''')

    # Scythes table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scythes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        job_data TEXT NOT NULL,
        schedule TEXT
    )''')

    # History table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS history (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        title TEXT,
        folder TEXT,
        filenames TEXT,
        job_data TEXT,
        status TEXT,
        log_path TEXT,
        error_summary TEXT,
        timestamp REAL
    )''')

    # Queue table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_data TEXT NOT NULL,
        queue_order INTEGER NOT NULL
    )''')

    conn.commit()
    conn.close()
    logger.info("Database tables created or verified successfully.")

def migrate_json_to_db():
    """
    Performs a one-time migration of data from legacy JSON files to the database.
    Backs up the JSON files after a successful migration.
    """
    migration_flag_file = os.path.join(g.DATA_DIR, '.db_migrated')
    if os.path.exists(migration_flag_file):
        return # Migration has already been done

    logger.info("Performing one-time migration from JSON files to database...")
    conn = get_db_connection()
    
    # --- File Paths ---
    users_file = os.path.join(g.DATA_DIR, "users.json")
    scythes_file = os.path.join(g.DATA_DIR, "scythes.json")
    state_file = os.path.join(g.DATA_DIR, "state.json")

    migrated_something = False

    try:
        # --- Migrate Users ---
        if os.path.exists(users_file):
            with open(users_file, 'r', encoding='utf-8') as f:
                users_data = json.load(f)
                for username, data in users_data.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO users (username, password_hash, permissions) VALUES (?, ?, ?)",
                        (username.lower(), data.get('password_hash'), json.dumps(data.get('permissions', {})))
                    )
            logger.info(f"Migrated {len(users_data)} user(s) to database.")
            migrated_something = True

        # --- Migrate Scythes ---
        if os.path.exists(scythes_file):
            with open(scythes_file, 'r', encoding='utf-8') as f:
                scythes_data = json.load(f)
                for scythe in scythes_data:
                    conn.execute(
                        "INSERT INTO scythes (id, name, job_data, schedule) VALUES (?, ?, ?, ?)",
                        (scythe.get('id'), scythe.get('name'), json.dumps(scythe.get('job_data')), json.dumps(scythe.get('schedule')))
                    )
            logger.info(f"Migrated {len(scythes_data)} scythe(s) to database.")
            migrated_something = True

        # --- Migrate State (History and Queue) ---
        if os.path.exists(state_file):
            with open(state_file, 'r', encoding='utf-8') as f:
                state_data = json.load(f)
                
                # History
                history_data = state_data.get('history', [])
                for item in history_data:
                    conn.execute(
                        """INSERT INTO history (log_id, url, title, folder, filenames, job_data, status, log_path, error_summary, timestamp)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            item.get('log_id'), item.get('url'), item.get('title'), item.get('folder'),
                            json.dumps(item.get('filenames', [])), json.dumps(item.get('job_data')),
                            item.get('status'), item.get('log_path'), item.get('error_summary'), item.get('timestamp')
                        )
                    )
                if history_data:
                    logger.info(f"Migrated {len(history_data)} history item(s) to database.")

                # Queue
                queue_data = state_data.get('queue', [])
                for i, item in enumerate(queue_data):
                    conn.execute(
                        "INSERT INTO queue (job_data, queue_order) VALUES (?, ?)",
                        (json.dumps(item), i)
                    )
                if queue_data:
                    logger.info(f"Migrated {len(queue_data)} queue item(s) to database.")
            migrated_something = True

        conn.commit()

        # --- Backup old files and create migration flag ---
        if migrated_something:
            logger.info("Migration successful. Backing up old JSON files.")
            for f in [users_file, scythes_file, state_file]:
                if os.path.exists(f):
                    os.rename(f, f + '.bak')
            
            with open(migration_flag_file, 'w') as f:
                f.write('Migration completed.')
        else:
            logger.info("No JSON files found to migrate.")

    except Exception as e:
        conn.rollback()
        logger.critical(f"Database migration failed: {e}. Rolling back changes.", exc_info=True)
    finally:
        conn.close()

# lib/scythe_manager.py
import json
import logging
from .database import get_db_connection
from . import app_globals as g # CHANGE: Import globals to access the state manager

logger = logging.getLogger()

class ScytheManager:
    """
    Manages Scythes (saved job templates) in the database.
    """
    def __init__(self):
        pass

    def get_all(self):
        """Loads and returns all scythes from the database."""
        conn = get_db_connection()
        scythes_raw = conn.execute("SELECT * FROM scythes ORDER BY id ASC").fetchall()
        conn.close()
        
        for scythe in scythes_raw:
            scythe['job_data'] = json.loads(scythe['job_data'])
            if scythe['schedule']:
                scythe['schedule'] = json.loads(scythe['schedule'])
        return scythes_raw

    def get_by_id(self, scythe_id):
        """Retrieves a specific scythe by its ID from the database."""
        conn = get_db_connection()
        scythe_raw = conn.execute("SELECT * FROM scythes WHERE id = ?", (scythe_id,)).fetchone()
        conn.close()
        
        if scythe_raw:
            scythe_raw['job_data'] = json.loads(scythe_raw['job_data'])
            if scythe_raw['schedule']:
                scythe_raw['schedule'] = json.loads(scythe_raw['schedule'])
        return scythe_raw

    def add(self, scythe_data):
        """Adds a new scythe to the database."""
        job_url = scythe_data.get("job_data", {}).get("url")
        if job_url:
            all_scythes = self.get_all()
            for scythe in all_scythes:
                if scythe.get("job_data", {}).get("url") == job_url:
                    return False, f"A Scythe for this URL already exists ('{scythe.get('name')}')"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scythes (name, job_data, schedule) VALUES (?, ?, ?)",
            (
                scythe_data.get('name'),
                json.dumps(scythe_data.get('job_data')),
                json.dumps(scythe_data.get('schedule'))
            )
        )
        conn.commit()
        conn.close()
        
        # CHANGE: Notify the state manager that scythes have changed.
        g.state_manager.increment_scythe_version()
        return True, f"Added Scythe: {scythe_data.get('name', 'Untitled')}"

    def update(self, scythe_id, scythe_data):
        """Updates an existing scythe in the database."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE scythes SET
               name = ?,
               job_data = ?,
               schedule = ?
               WHERE id = ?""",
            (
                scythe_data.get('name'),
                json.dumps(scythe_data.get('job_data')),
                json.dumps(scythe_data.get('schedule')),
                scythe_id
            )
        )
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            # CHANGE: Notify the state manager that scythes have changed.
            g.state_manager.increment_scythe_version()
            return True
        return False

    def delete(self, scythe_id):
        """Deletes a scythe from the database."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scythes WHERE id = ?", (scythe_id,))
        conn.commit()
        conn.close()

        if cursor.rowcount > 0:
            # CHANGE: Notify the state manager that scythes have changed.
            g.state_manager.increment_scythe_version()
            return True
        return False

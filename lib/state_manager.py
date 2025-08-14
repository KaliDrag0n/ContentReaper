# lib/state_manager.py
import threading
import queue
import json
import os
import time
import shutil
import logging
from .database import get_db_connection

logger = logging.getLogger()

class StateManager:
    """
    A thread-safe class to manage the application's active state.
    History and Queue are persisted in the database, while the active queue
    is managed in-memory for the worker thread.
    """
    def __init__(self):
        self._lock = threading.RLock()

        # Core state data
        self.queue = queue.Queue()
        self.history = [] # This will be loaded from DB
        self.current_download = self._get_default_current_download()

        # State versioning for efficient frontend updates
        self.history_state_version = 0
        self.queue_state_version = 0
        self.current_download_version = 0
        self.scythe_state_version = 0 # CHANGE: Added version counter for Scythes

        # Worker control events
        self.cancel_event = threading.Event()
        self.stop_mode = "CANCEL"
        self.queue_paused_event = threading.Event()
        self.queue_paused_event.set()

    def _get_default_current_download(self):
        return {
            "url": None, "job_data": None, "progress": 0, "status": "", "title": None,
            "thumbnail": None, "playlist_title": None, "track_title": None,
            "playlist_count": 0, "playlist_index": 0,
            "speed": None, "eta": None, "file_size": None, "log_path": None,
            "pid": None
        }

    def reset_current_download(self):
        with self._lock:
            self.current_download = self._get_default_current_download()
            self.current_download_version += 1

    def update_current_download(self, data: dict):
        with self._lock:
            self.current_download.update(data)
            self.current_download_version += 1

    def pause_queue(self):
        with self._lock:
            self.queue_paused_event.clear()
            self.current_download_version += 1

    def resume_queue(self):
        with self._lock:
            self.queue_paused_event.set()
            self.current_download_version += 1

    def _persist_queue(self):
        """Saves the current in-memory queue state to the database."""
        conn = get_db_connection()
        try:
            with self._lock:
                queue_items = list(self.queue.queue)
                conn.execute("BEGIN")
                conn.execute("DELETE FROM queue") # Clear old queue
                for i, item in enumerate(queue_items):
                    conn.execute(
                        "INSERT INTO queue (job_data, queue_order) VALUES (?, ?)",
                        (json.dumps(item), i)
                    )
                conn.commit()
                self.queue_state_version += 1
        except sqlite3.Error as e:
            logger.error(f"Failed to persist queue to database: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def get_from_queue_and_persist(self, block=True, timeout=None):
        """
        Gets a job from the in-memory queue and immediately persists the
        change to the database to prevent jobs from reappearing on restart.
        """
        try:
            # This operation is atomic on the in-memory queue
            job = self.queue.get(block=block, timeout=timeout)
            # Now, persist the new state of the queue to the database
            self._persist_queue()
            return job
        except queue.Empty:
            # Re-raise the exception so the caller can handle it (e.g., the worker loop)
            raise

    def add_to_queue(self, job_data: dict):
        """Adds a new job to the in-memory queue with a unique ID and persists the change."""
        with self._lock:
            max_id = -1
            for item in list(self.queue.queue):
                if item.get('id', -1) > max_id:
                    max_id = item.get('id')

            new_id = max_id + 1
            job_data['id'] = new_id
            self.queue.put(job_data)

        self._persist_queue()

    def get_queue_list(self):
        with self._lock:
            return list(self.queue.queue)

    def clear_queue(self):
        with self._lock:
            if self.queue.empty(): return
            with self.queue.mutex:
                self.queue.queue.clear()
        self._persist_queue()

    def delete_from_queue(self, job_id: int):
        """Deletes a job by its 'id' key from the in-memory queue."""
        with self._lock:
            items = list(self.queue.queue)
            updated_queue = [job for job in items if job.get('id') != job_id]
            if len(updated_queue) < len(items):
                with self.queue.mutex:
                    self.queue.queue.clear()
                    for job in updated_queue:
                        self.queue.put(job)
                self._persist_queue()

    def reorder_queue(self, ordered_ids: list[int]):
        with self._lock:
            items = list(self.queue.queue)
            item_map = {item['id']: item for item in items}
            new_queue_items = [item_map[job_id] for job_id in ordered_ids if job_id in item_map]

            existing_ids = set(ordered_ids)
            for item in items:
                if item['id'] not in existing_ids:
                    new_queue_items.append(item)

            with self.queue.mutex:
                self.queue.queue.clear()
                for job in new_queue_items:
                    self.queue.put(job)
        self._persist_queue()

    def increment_scythe_version(self):
        """Increments the version counter for Scythes to trigger UI updates."""
        with self._lock:
            self.scythe_state_version += 1

    def add_to_history(self, history_item: dict):
        """Adds a completed job to the history in the database."""
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO history (url, title, folder, filenames, job_data, status, log_path, error_summary, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    history_item.get('url'), history_item.get('title'), history_item.get('folder'),
                    json.dumps(history_item.get('filenames', [])), json.dumps(history_item.get('job_data')),
                    history_item.get('status'), history_item.get('log_path'), history_item.get('error_summary'),
                    history_item.get('timestamp', time.time())
                )
            )
            new_log_id = cursor.lastrowid
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to add item to history database: {e}")
            return None
        finally:
            if conn:
                conn.close()

        with self._lock:
            self.history_state_version += 1
        return new_log_id

    def add_notification_to_history(self, message: str):
        notification = {
            "title": message, "status": "INFO", "timestamp": time.time()
        }
        self.add_to_history(notification)

    def update_history_item(self, log_id: int, data_to_update: dict):
        """Updates an existing history item in the database."""
        conn = get_db_connection()
        try:
            conn.execute(
                """UPDATE history SET
                   url = ?, title = ?, folder = ?, filenames = ?, job_data = ?,
                   status = ?, log_path = ?, error_summary = ?
                   WHERE log_id = ?""",
                (
                    data_to_update.get('url'), data_to_update.get('title'), data_to_update.get('folder'),
                    json.dumps(data_to_update.get('filenames')), json.dumps(data_to_update.get('job_data')),
                    data_to_update.get('status'), data_to_update.get('log_path'), data_to_update.get('error_summary'),
                    log_id
                )
            )
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to update history item {log_id} in database: {e}")
        finally:
            if conn:
                conn.close()

        with self._lock:
            self.history_state_version += 1

    def get_history_summary(self):
        """Returns a summary of the history from the database."""
        try:
            conn = get_db_connection()
            history_raw = conn.execute("SELECT log_id, url, title, folder, filenames, job_data, status, error_summary, timestamp FROM history ORDER BY log_id DESC").fetchall()
        except sqlite3.Error as e:
            logger.error(f"Failed to get history summary from database: {e}")
            return []
        finally:
            if 'conn' in locals() and conn:
                conn.close()

        for item in history_raw:
            try:
                item['filenames'] = json.loads(item['filenames'] or '[]')
                item['job_data'] = json.loads(item['job_data'] or '{}')
            except json.JSONDecodeError:
                item['filenames'] = []
                item['job_data'] = {}
        return history_raw

    def clear_history(self):
        """Clears the history table and returns paths of logs to be deleted."""
        log_paths = []
        try:
            conn = get_db_connection()
            log_paths = [row['log_path'] for row in conn.execute("SELECT log_path FROM history WHERE log_path IS NOT NULL").fetchall()]
            conn.execute("DELETE FROM history")
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to clear history from database: {e}")
            return []
        finally:
            if 'conn' in locals() and conn:
                conn.close()

        with self._lock:
            self.history_state_version += 1
        return log_paths

    def delete_from_history(self, log_id: int):
        """Deletes a single item from history and returns its log path."""
        path_to_delete = None
        try:
            conn = get_db_connection()
            row = conn.execute("SELECT log_path FROM history WHERE log_id = ?", (log_id,)).fetchone()
            path_to_delete = row['log_path'] if row else None

            conn.execute("DELETE FROM history WHERE log_id = ?", (log_id,))
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to delete item {log_id} from history: {e}")
            return None
        finally:
             if 'conn' in locals() and conn:
                conn.close()

        with self._lock:
            self.history_state_version += 1
        return path_to_delete

    def get_history_item_by_log_id(self, log_id: int):
        """Retrieves a full history item by its log ID from the database."""
        try:
            conn = get_db_connection()
            item_raw = conn.execute("SELECT * FROM history WHERE log_id = ?", (log_id,)).fetchone()
        except sqlite3.Error as e:
            logger.error(f"Failed to get history item {log_id} from database: {e}")
            return None
        finally:
            if 'conn' in locals() and conn:
                conn.close()

        if item_raw:
            try:
                item_raw['filenames'] = json.loads(item_raw['filenames'] or '[]')
                item_raw['job_data'] = json.loads(item_raw['job_data'] or '{}')
            except json.JSONDecodeError:
                 item_raw['filenames'] = []
                 item_raw['job_data'] = {}
        return item_raw

    def load_state(self):
        """Loads the queue from the database into the in-memory queue."""
        try:
            conn = get_db_connection()
            queue_items_raw = conn.execute("SELECT job_data FROM queue ORDER BY queue_order ASC").fetchall()
        except sqlite3.Error as e:
            logger.error(f"Failed to load queue from database: {e}")
            return
        finally:
            if 'conn' in locals() and conn:
                conn.close()

        with self._lock:
            with self.queue.mutex:
                self.queue.queue.clear()
            for item in queue_items_raw:
                try:
                    self.queue.put(json.loads(item['job_data']))
                except json.JSONDecodeError:
                    logger.warning(f"Could not load invalid job from persisted queue: {item['job_data']}")

        logger.info(f"Loaded {self.queue.qsize()} item(s) into the active queue from database.")

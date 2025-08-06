# lib/state_manager.py
import threading
import queue
import json
import os
import time

class StateManager:
    """
    A thread-safe class to manage the application's state, including
    the download queue, history, and current download status.
    It handles loading from and saving to a JSON file.
    """
    def __init__(self, state_file_path: str):
        self.state_file = state_file_path
        self._lock = threading.RLock()
        
        self.queue = queue.Queue()
        self.history = []
        self.current_download = self._get_default_current_download()
        
        self.next_log_id = 0
        self.next_queue_id = 0
        
        self.history_state_version = 0
        self.queue_state_version = 0
        self.current_download_version = 0
        
        self.cancel_event = threading.Event()
        self.stop_mode = "CANCEL"
        self.queue_paused_event = threading.Event()
        self.queue_paused_event.set()

    def _get_default_current_download(self) -> dict:
        """Returns a dictionary representing a clean 'current_download' state."""
        return {
            "url": None, "job_data": None, "progress": 0, "status": "", "title": None,
            "thumbnail": None, "playlist_title": None, "track_title": None,
            "playlist_count": 0, "playlist_index": 0,
            "speed": None, "eta": None, "file_size": None, "log_path": None
        }

    def reset_current_download(self):
        """Resets the current download state to its default and notifies listeners."""
        with self._lock:
            self.current_download = self._get_default_current_download()
            self.current_download_version += 1

    def update_current_download(self, data: dict):
        """Updates the current download state with new data and notifies listeners."""
        with self._lock:
            self.current_download.update(data)
            self.current_download_version += 1

    def pause_queue(self):
        """Pauses the download worker thread."""
        with self._lock:
            self.queue_paused_event.clear()
            self.current_download_version += 1

    def resume_queue(self):
        """Resumes the download worker thread."""
        with self._lock:
            self.queue_paused_event.set()
            self.current_download_version += 1

    def add_to_queue(self, job_data: dict) -> int:
        """Adds a new job to the queue with a unique ID."""
        with self._lock:
            new_id = self.next_queue_id
            self.next_queue_id += 1
            job_data['id'] = new_id
            self.queue.put(job_data)
            self.queue_state_version += 1
        self.save_state()
        return new_id

    def get_queue_list(self) -> list:
        """Returns a copy of the current queue as a list."""
        with self._lock:
            return list(self.queue.queue)

    def clear_queue(self):
        """Removes all items from the download queue."""
        with self._lock:
            if self.queue.empty(): return
            with self.queue.mutex:
                self.queue.queue.clear()
            self.queue_state_version += 1
        self.save_state()

    def delete_from_queue(self, job_id: int):
        """Deletes a specific job from the queue by its ID."""
        with self._lock:
            items = list(self.queue.queue)
            updated_queue = [job for job in items if job.get('id') != job_id]
            if len(updated_queue) < len(items):
                with self.queue.mutex:
                    self.queue.queue.clear()
                    for job in updated_queue:
                        self.queue.put(job)
                self.queue_state_version += 1
        self.save_state()

    def reorder_queue(self, ordered_ids: list[int]):
        """Reorders the queue based on a new list of job IDs."""
        with self._lock:
            items = list(self.queue.queue)
            item_map = {item['id']: item for item in items}
            new_queue_items = [item_map[job_id] for job_id in ordered_ids if job_id in item_map]
            existing_ids_in_order = set(ordered_ids)
            for item in items:
                if item['id'] not in existing_ids_in_order:
                    new_queue_items.append(item)
            with self.queue.mutex:
                self.queue.queue.clear()
                for job in new_queue_items:
                    self.queue.put(job)
            self.queue_state_version += 1
        self.save_state()

    def add_to_history(self, history_item: dict) -> int:
        """Adds a completed job to the history with a unique ID."""
        with self._lock:
            new_id = self.next_log_id
            self.next_log_id += 1
            history_item['log_id'] = new_id
            self.history.append(history_item)
            self.history_state_version += 1
        self.save_state()
        return new_id
    
    def update_history_item(self, log_id: int, data_to_update: dict):
        """Updates an existing history item with new data."""
        with self._lock:
            for item in self.history:
                if item.get("log_id") == log_id:
                    item.update(data_to_update)
                    self.history_state_version += 1
                    break
        self.save_state()

    def get_history_summary(self) -> list:
        """Returns a summary of the history, omitting sensitive/large data."""
        with self._lock:
            history_summary = [h.copy() for h in self.history]
            for item in history_summary:
                item.pop("log_path", None)
        return history_summary

    def clear_history(self) -> list:
        """Clears the entire history and returns paths of logs to be deleted."""
        paths_to_delete = []
        with self._lock:
            if not self.history: return []
            for item in self.history:
                if item.get("log_path") and item.get("log_path") != "LOG_SAVE_ERROR":
                    paths_to_delete.append(item["log_path"])
            self.history.clear()
            self.history_state_version += 1
        self.save_state()
        return paths_to_delete

    def delete_from_history(self, log_id: int) -> str or None:
        """Deletes a single item from history and returns its log path for cleanup."""
        path_to_delete = None
        with self._lock:
            item_to_delete = next((h for h in self.history if h.get("log_id") == log_id), None)
            if not item_to_delete: return None
            if item_to_delete.get("log_path") and item_to_delete.get("log_path") != "LOG_SAVE_ERROR":
                path_to_delete = item_to_delete["log_path"]
            self.history[:] = [h for h in self.history if h.get("log_id") != log_id]
            self.history_state_version += 1
        self.save_state()
        return path_to_delete

    def get_history_item_by_log_id(self, log_id: int) -> dict or None:
        """Retrieves a full history item by its log ID."""
        with self._lock:
            item = next((h for h in self.history if h.get("log_id") == log_id), None)
            return item.copy() if item else None

    def save_state(self):
        """Saves the current state to a JSON file atomically."""
        with self._lock:
            state_to_save = {
                "queue": list(self.queue.queue),
                "history": self.history,
                "current_job": self.current_download.get("job_data"),
            }
        
        temp_file_path = self.state_file + ".tmp"
        try:
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(state_to_save, f, indent=4)
            os.replace(temp_file_path, self.state_file)
        except Exception as e:
            print(f"ERROR: Could not save state to file: {e}")
            if os.path.exists(temp_file_path):
                try: os.remove(temp_file_path)
                except Exception as e_clean: print(f"ERROR: Could not clean up temp state file: {e_clean}")

    def load_state(self):
        """Loads state from JSON, robustly handling old formats and corruption."""
        if not os.path.exists(self.state_file):
            print("State file not found. Starting with a fresh state.")
            return

        # --- FIX: Wrap the entire loading process in a try-except block ---
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

            with self._lock:
                queue_items = state.get("queue", [])
                if not isinstance(queue_items, list):
                    print("WARNING: Queue in state file is not a list. Resetting queue.")
                    queue_items = []
                
                abandoned_job = state.get("current_job")
                if isinstance(abandoned_job, dict):
                    print(f"Re-queueing abandoned job: {abandoned_job.get('id')}")
                    abandoned_job['status'] = 'ABANDONED'
                    queue_items.append(abandoned_job)

                self.history = state.get("history", [])
                if not isinstance(self.history, list):
                    print("WARNING: History in state file is not a list. Resetting history.")
                    self.history = []

                max_queue_id = -1
                for job in queue_items:
                    job_id = job.get('id')
                    if isinstance(job_id, int) and job_id > max_queue_id:
                        max_queue_id = job_id
                self.next_queue_id = max_queue_id + 1
                
                max_log_id = -1
                for item in self.history:
                    log_id = item.get('log_id')
                    if isinstance(log_id, int) and log_id > max_log_id:
                        max_log_id = log_id
                self.next_log_id = max_log_id + 1

                with self.queue.mutex:
                    self.queue.queue.clear()
                    for job in queue_items:
                        if not isinstance(job, dict): continue
                        if not isinstance(job.get('id'), int):
                            job['id'] = self.next_queue_id
                            self.next_queue_id += 1
                        self.queue.put(job)
                
                print(f"Successfully loaded {self.queue.qsize()} item(s) into queue and {len(self.history)} history entries.")
                print(f"Next Queue ID set to {self.next_queue_id}, Next Log ID set to {self.next_log_id}.")

        except Exception as e:
            print(f"FATAL ERROR loading state file: {e}")
            print("The application will start with a fresh state.")
            corrupted_path = self.state_file + f".corrupted.{int(time.time())}.bak"
            try:
                os.rename(self.state_file, corrupted_path)
                print(f"Backed up corrupted state file to {corrupted_path}")
            except OSError as e_rename:
                print(f"Could not back up corrupted state file. Error: {e_rename}")
            
            # Reset state to default
            with self._lock:
                self.history = []
                with self.queue.mutex: self.queue.queue.clear()
                self.next_log_id = 0
                self.next_queue_id = 0

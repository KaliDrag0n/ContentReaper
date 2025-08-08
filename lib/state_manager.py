# lib/state_manager.py
import threading
import queue
import json
import os
import time
import shutil

class StateManager:
    """
    A thread-safe class to manage the application's state, including
    the download queue, history, and current download status.
    It handles loading from and saving to a JSON file with backup/recovery and file locking.
    """
    def __init__(self, state_file_path: str):
        self.state_file = state_file_path
        self.lock_file = state_file_path + ".lock"
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

    def _acquire_lock(self, timeout=10):
        start_time = time.time()
        while True:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                if time.time() - start_time >= timeout:
                    print(f"WARNING: Lock file {self.lock_file} has been held for over {timeout} seconds. Breaking lock.")
                    self._release_lock()
                    return self._acquire_lock(timeout=1) 
                time.sleep(0.1)
            except Exception as e:
                print(f"ERROR: Unexpected error acquiring lock: {e}")
                return False

    def _release_lock(self):
        try:
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
        except Exception as e:
            print(f"ERROR: Could not release lock file: {e}")

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

    # --- CHANGE: Added 'save' parameter to prevent redundant saves during startup ---
    def add_to_history(self, history_item: dict, save: bool = True) -> int:
        """Adds a completed job to the history with a unique ID."""
        with self._lock:
            new_id = self.next_log_id
            self.next_log_id += 1
            history_item['log_id'] = new_id
            self.history.append(history_item)
            self.history_state_version += 1
        if save:
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
        """Saves the current state to a JSON file atomically with a backup."""
        if not self._acquire_lock():
            print("Could not acquire lock to save state. Skipping save.")
            return
        
        try:
            with self._lock:
                state_to_save = {
                    "queue": list(self.queue.queue),
                    "history": self.history,
                    "current_job": self.current_download.get("job_data"),
                }
            
            temp_file_path = self.state_file + ".tmp"
            backup_file_path = self.state_file + ".bak"
            
            if os.path.exists(self.state_file):
                shutil.copy2(self.state_file, backup_file_path)

            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(state_to_save, f, indent=4)
            
            os.replace(temp_file_path, self.state_file)

        except Exception as e:
            print(f"ERROR: Could not save state to file: {e}")
            if os.path.exists(backup_file_path):
                try:
                    shutil.copy2(backup_file_path, self.state_file)
                    print("Restored state from backup due to save error.")
                except Exception as e_restore:
                    print(f"FATAL: Could not restore state from backup: {e_restore}")
            
            if os.path.exists(temp_file_path):
                try: os.remove(temp_file_path)
                except Exception as e_clean: print(f"ERROR: Could not clean up temp state file: {e_clean}")
        finally:
            self._release_lock()

    def load_state(self):
        """Loads state from JSON, with a fallback to a backup file."""
        if not self._acquire_lock():
            print("Could not acquire lock to load state. Starting fresh.")
            self._reset_state()
            return
            
        try:
            backup_file_path = self.state_file + ".bak"
            if not os.path.exists(self.state_file) and not os.path.exists(backup_file_path):
                print("State file and backup not found. Starting with a fresh state.")
                return

            loaded_successfully = False
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if content.strip():
                            state = json.loads(content)
                            self._apply_state(state)
                            loaded_successfully = True
                            print("Successfully loaded state from main file.")
                        else:
                            print("WARNING: Main state file is empty. Trying backup.")
                except (json.JSONDecodeError, OSError) as e:
                    print(f"WARNING: Could not load main state file: {e}. Attempting to use backup.")

            if not loaded_successfully and os.path.exists(backup_file_path):
                try:
                    with open(backup_file_path, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                        self._apply_state(state)
                        shutil.copy2(backup_file_path, self.state_file)
                        print("Successfully loaded and restored state from backup file.")
                        loaded_successfully = True
                except (json.JSONDecodeError, OSError) as e:
                    print(f"ERROR: Could not load backup state file: {e}. Starting fresh.")

            if not loaded_successfully:
                print("FATAL: Both state file and backup are corrupted or unreadable.")
                corrupted_path = self.state_file + f".corrupted.{int(time.time())}.bak"
                if os.path.exists(self.state_file):
                    try:
                        os.rename(self.state_file, corrupted_path)
                        print(f"Backed up corrupted state file to {corrupted_path}")
                    except OSError as e_rename:
                        print(f"Could not back up corrupted state file. Error: {e_rename}")
                self._reset_state()
        finally:
            self._release_lock()

    def _reset_state(self):
        """Resets the manager to a clean state."""
        with self._lock:
            self.history = []
            with self.queue.mutex: self.queue.queue.clear()
            self.next_log_id = 0
            self.next_queue_id = 0

    def _apply_state(self, state: dict):
        """Applies a loaded state dictionary to the manager."""
        with self._lock:
            # Load history and queue first, with validation
            self.history = [item for item in state.get("history", []) if isinstance(item, dict)]
            queue_items = [job for job in state.get("queue", []) if isinstance(job, dict)]
            
            # --- CHANGE: Calculate next IDs at the beginning, before any modifications ---
            max_log_id = -1
            for item in self.history:
                log_id = item.get('log_id')
                if isinstance(log_id, int) and log_id > max_log_id:
                    max_log_id = log_id
            self.next_log_id = max_log_id + 1

            max_queue_id = -1
            for job in queue_items:
                job_id = job.get('id')
                if isinstance(job_id, int) and job_id > max_queue_id:
                    max_queue_id = job_id
            self.next_queue_id = max_queue_id + 1

            # --- CHANGE: Use the centralized add_to_history method for abandoned jobs ---
            abandoned_job = state.get("current_job")
            if isinstance(abandoned_job, dict):
                print(f"Found abandoned job: {abandoned_job.get('id', 'N/A')}. Moving to history.")
                history_item = {
                    "url": abandoned_job.get("url", "Unknown URL"),
                    "title": abandoned_job.get("folder") or abandoned_job.get("url", "Unknown Title"),
                    "folder": abandoned_job.get("folder"),
                    "filenames": [], "job_data": abandoned_job, "status": "ABANDONED",
                    "log_path": "No log generated.",
                    "error_summary": "Job was interrupted by an application crash or ungraceful shutdown."
                }
                # Add to history using the official method, but don't save yet.
                # This ensures it gets a correct and unique log_id.
                self.add_to_history(history_item, save=False)

            # Rebuild the queue from the validated and cleaned items
            with self.queue.mutex:
                self.queue.queue.clear()
                processed_ids = set()
                for job in queue_items:
                    job_id = job.get('id')
                    if not isinstance(job_id, int) or job_id in processed_ids:
                        job['id'] = self.next_queue_id
                        self.next_queue_id += 1
                    
                    self.queue.put(job)
                    processed_ids.add(job['id'])
            
            print(f"Applied state: {self.queue.qsize()} item(s) in queue, {len(self.history)} history entries.")

# lib/state_manager.py
import threading
import queue
import json
import os

class StateManager:
    """
    A thread-safe class to manage the application's state, including
    the download queue, history, and current download status.
    """
    def __init__(self, state_file_path):
        self.state_file = state_file_path
        self._lock = threading.RLock()
        self.queue = queue.Queue()
        self.history = []
        self.current_download = self._get_default_current_download()
        self.next_log_id = 0
        self.next_queue_id = 0
        self.history_state_version = 0
        self.cancel_event = threading.Event()
        self.stop_mode = "CANCEL"
        # NEW: Event to control the worker thread's execution
        self.queue_paused_event = threading.Event()
        self.queue_paused_event.set() # Start in a running state

    def _get_default_current_download(self):
        return {
            "url": None, "job_data": None, "progress": 0, "status": "", "title": None,
            "playlist_title": None, "track_title": None,
            "playlist_count": 0, "playlist_index": 0,
            "speed": None, "eta": None, "file_size": None,
            "log_path": None
        }

    def reset_current_download(self):
        with self._lock:
            self.current_download = self._get_default_current_download()

    def update_current_download(self, data):
        with self._lock:
            self.current_download.update(data)

    def add_to_queue(self, job_data):
        with self._lock:
            job_data['id'] = self.next_queue_id
            self.next_queue_id += 1
            self.queue.put(job_data)
        self.save_state()
        return job_data['id']

    def get_queue_list(self):
        with self._lock:
            return list(self.queue.queue)

    def clear_queue(self):
        with self._lock:
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
        self.save_state()

    def delete_from_queue(self, job_id):
        with self._lock:
            items = []
            while not self.queue.empty():
                try:
                    items.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            
            updated_queue = [job for job in items if job.get('id') != job_id]
            
            for job in updated_queue:
                self.queue.put(job)
        self.save_state()

    def reorder_queue(self, ordered_ids):
        with self._lock:
            items = []
            while not self.queue.empty():
                try:
                    items.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            
            item_map = {item['id']: item for item in items}
            
            new_queue_items = [item_map[job_id] for job_id in ordered_ids if job_id in item_map]

            existing_ids = set(ordered_ids)
            for item in items:
                if item['id'] not in existing_ids:
                    new_queue_items.append(item)

            for job in new_queue_items:
                self.queue.put(job)

        self.save_state()


    def add_to_history(self, history_item):
        with self._lock:
            history_item['log_id'] = self.next_log_id
            self.history.append(history_item)
            self.next_log_id += 1
            self.history_state_version += 1
        self.save_state()
        return history_item['log_id']
    
    def update_history_item(self, log_id, data_to_update):
        with self._lock:
            for item in self.history:
                if item.get("log_id") == log_id:
                    item.update(data_to_update)
                    break
        self.save_state()

    def get_history_summary(self):
        with self._lock:
            history_summary = [h.copy() for h in self.history]
            for item in history_summary:
                item.pop("log_path", None)
        return history_summary

    def clear_history(self):
        paths_to_delete = []
        with self._lock:
            for item in self.history:
                if item.get("log_path") and item.get("log_path") != "LOG_SAVE_ERROR":
                    paths_to_delete.append(item["log_path"])
            self.history.clear()
            self.history_state_version += 1
        self.save_state()
        return paths_to_delete

    def delete_from_history(self, log_id):
        path_to_delete = None
        with self._lock:
            item_to_delete = next((h for h in self.history if h.get("log_id") == log_id), None)
            if not item_to_delete:
                return None
            
            # Only mark the log file for deletion
            if item_to_delete.get("log_path") and item_to_delete.get("log_path") != "LOG_SAVE_ERROR":
                path_to_delete = item_to_delete["log_path"]
            
            self.history[:] = [h for h in self.history if h.get("log_id") != log_id]
            self.history_state_version += 1
        self.save_state()
        return path_to_delete

    def get_history_item_by_log_id(self, log_id):
        with self._lock:
            item = next((h for h in self.history if h.get("log_id") == log_id), None)
            return item.copy() if item else None

    def save_state(self):
        with self._lock:
            state_to_save = {
                "queue": list(self.queue.queue),
                "history": self.history,
                "current_job": self.current_download.get("job_data"),
                "next_log_id": self.next_log_id,
                "next_queue_id": self.next_queue_id,
                "history_state_version": self.history_state_version
            }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state_to_save, f, indent=4)
        except Exception as e:
            print(f"ERROR: Could not save state to file: {e}")

    def load_state(self):
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            # --- IMPROVED: Validate the structure of the loaded state ---
            with self._lock:
                abandoned_job = state.get("current_job")
                if isinstance(abandoned_job, dict): # Check if it's a valid job object
                    print(f"Re-queueing abandoned job: {abandoned_job.get('id')}")
                    self.queue.put(abandoned_job)
                
                self.history = state.get("history", [])
                if not isinstance(self.history, list):
                    print("WARNING: History in state file is not a list. Resetting.")
                    self.history = []

                self.next_log_id = state.get("next_log_id", len(self.history))
                self.next_queue_id = state.get("next_queue_id", 0)
                self.history_state_version = state.get("history_state_version", 0)
                
                # Ensure queue is a list before iterating
                queue_items = state.get("queue", [])
                if not isinstance(queue_items, list):
                    print("WARNING: Queue in state file is not a list. Resetting.")
                    queue_items = []

                for job in queue_items:
                    if 'id' not in job:
                        job['id'] = self.next_queue_id
                        self.next_queue_id += 1
                    self.queue.put(job)
                
                print(f"Loaded {self.queue.qsize()} items from queue and {len(self.history)} history entries.")
        except json.JSONDecodeError as e:
            print(f"Could not load state file (invalid JSON). Error: {e}")
            corrupted_path = self.state_file + ".bak"
            if os.path.exists(self.state_file):
                os.rename(self.state_file, corrupted_path)
            print(f"Backed up corrupted state file to {corrupted_path}")
        except Exception as e:
            # Catch other potential errors like TypeErrors from malformed data
            print(f"An unexpected error occurred loading the state file. Error: {e}")
            corrupted_path = self.state_file + ".bak"
            if os.path.exists(self.state_file):
                os.rename(self.state_file, corrupted_path)
            print(f"Backed up corrupted state file to {corrupted_path}")
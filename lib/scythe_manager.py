# lib/scythe_manager.py
import json
import os
import shutil
import time
import threading

class ScytheManager:
    """
    A dedicated, thread-safe class to manage Scythes (saved job templates).
    It handles loading from and saving to its own JSON file, separate from the main state.
    This isolates Scythe data, improving robustness.
    """
    def __init__(self, scythes_file_path: str):
        self.scythes_file = scythes_file_path
        self.lock_file = scythes_file_path + ".lock"
        self._lock = threading.RLock() # Used for in-memory operations if we ever cache
        self.file_lock = threading.RLock() # Used for file I/O

    def _acquire_lock(self, timeout=5):
        """Acquires an exclusive file lock, with a timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.1)
        return False

    def _release_lock(self):
        """Releases the file lock."""
        try:
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
        except Exception as e:
            print(f"ERROR: Could not release scythes lock file: {e}")

    def _load_scythes(self):
        """Loads scythes from JSON, with a fallback to a backup file."""
        if not os.path.exists(self.scythes_file):
            return []
        
        try:
            with open(self.scythes_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip(): return []
                return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Could not load main scythes file: {e}. Attempting backup.")
            backup_file = self.scythes_file + ".bak"
            if os.path.exists(backup_file):
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as bak_e:
                    print(f"ERROR: Could not load backup scythes file: {bak_e}. Returning empty list.")
        return []

    def _save_scythes(self, scythes_data):
        """Saves the scythes list to a JSON file atomically with a backup."""
        with self.file_lock:
            if not self._acquire_lock():
                print("Could not acquire lock to save scythes. Aborting.")
                return

            try:
                temp_file = self.scythes_file + ".tmp"
                backup_file = self.scythes_file + ".bak"
                
                if os.path.exists(self.scythes_file):
                    shutil.copy2(self.scythes_file, backup_file)

                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(scythes_data, f, indent=4)
                
                os.replace(temp_file, self.scythes_file)
            except Exception as e:
                print(f"ERROR: Could not save scythes file: {e}")
            finally:
                self._release_lock()

    def get_all(self):
        """Loads and returns all scythes."""
        with self.file_lock:
            return self._load_scythes()

    def get_by_id(self, scythe_id):
        """Loads all scythes and returns the one with the matching ID."""
        scythes = self.get_all()
        return next((s for s in scythes if s.get("id") == scythe_id), None)

    def add(self, scythe_data):
        """Adds a new scythe to the file."""
        scythes = self.get_all()
        max_id = -1
        for item in scythes:
            if item.get('id', -1) > max_id:
                max_id = item.get('id')
        
        new_id = max_id + 1
        scythe_data['id'] = new_id
        scythes.append(scythe_data)
        self._save_scythes(scythes)
        return new_id

    def update(self, scythe_id, scythe_data):
        """Updates an existing scythe in the file."""
        scythes = self.get_all()
        updated = False
        for i, scythe in enumerate(scythes):
            if scythe.get("id") == scythe_id:
                scythe_data['id'] = scythe_id
                scythes[i] = scythe_data
                updated = True
                break
        if updated:
            self._save_scythes(scythes)
        return updated

    def delete(self, scythe_id):
        """Deletes a scythe from the file."""
        scythes = self.get_all()
        original_length = len(scythes)
        scythes = [s for s in scythes if s.get("id") != scythe_id]
        if len(scythes) < original_length:
            self._save_scythes(scythes)
            return True
        return False

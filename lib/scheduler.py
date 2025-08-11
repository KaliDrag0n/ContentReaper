# lib/scheduler.py
import threading
import time
import logging
import schedule
import watchdog
from datetime import datetime

logger = logging.getLogger()

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

class ScythesChangeHandler(FileSystemEventHandler):
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.last_triggered = 0

    def on_modified(self, event):
        if "scythes.json" in event.src_path:
            if time.time() - self.last_triggered > 5:
                logger.info("Scythes file changed. Reloading schedule...")
                self.scheduler._load_and_schedule_jobs()
                self.last_triggered = time.time()

class Scheduler:
    """
    Manages the scheduling and execution of automated Scythe reaping.
    Runs in its own thread to avoid blocking the main application.
    """
    def __init__(self, scythe_manager, state_manager):
        self.scythe_manager = scythe_manager
        self.state_manager = state_manager
        self.stop_event = threading.Event()
        self.observer = None
        self._load_and_schedule_jobs()

    def _load_and_schedule_jobs(self):
        """
        Clears the current schedule and reloads all Scythes from disk,
        scheduling the ones that have automation enabled.
        """
        schedule.clear()
        logger.info("Loading and scheduling automated Scythes...")
        scythes = self.scythe_manager.get_all()
        count = 0
        for scythe in scythes:
            schedule_info = scythe.get("schedule")
            if schedule_info and schedule_info.get("enabled"):
                try:
                    interval = schedule_info.get("interval")
                    at_time = schedule_info.get("time")
                    
                    weekdays = schedule_info.get("weekdays", [])

                    if interval == "daily":
                        schedule.every().day.at(at_time).do(self._reap_scythe, scythe_id=scythe.get("id"))
                        count += 1
                    elif interval == "weekly" and weekdays:
                        for day_index in weekdays:
                            job = None
                            if day_index == 0: job = schedule.every().monday.at(at_time)
                            elif day_index == 1: job = schedule.every().tuesday.at(at_time)
                            elif day_index == 2: job = schedule.every().wednesday.at(at_time)
                            elif day_index == 3: job = schedule.every().thursday.at(at_time)
                            elif day_index == 4: job = schedule.every().friday.at(at_time)
                            elif day_index == 5: job = schedule.every().saturday.at(at_time)
                            elif day_index == 6: job = schedule.every().sunday.at(at_time)
                            
                            if job:
                                job.do(self._reap_scythe, scythe_id=scythe.get("id"))
                        count += 1

                except Exception as e:
                    logger.error(f"Failed to schedule Scythe '{scythe.get('name')}': {e}")
        
        logger.info(f"Successfully scheduled {count} Scythe(s). Next run at: {schedule.next_run}")

    def _reap_scythe(self, scythe_id):
        """
        The function called by the scheduler. It fetches the latest Scythe data
        and adds its job to the main download queue.
        """
        logger.info(f"Scheduler is reaping Scythe ID: {scythe_id}")
        scythe = self.scythe_manager.get_by_id(scythe_id)
        if not scythe or not scythe.get("job_data"):
            logger.error(f"Scheduled reap failed: Scythe ID {scythe_id} not found or is invalid.")
            return
        
        if not scythe.get("schedule", {}).get("enabled"):
            logger.warning(f"Skipping scheduled reap for Scythe ID {scythe_id} as it is now disabled.")
            return

        job_to_reap = scythe["job_data"]
        job_to_reap["resolved_folder"] = job_to_reap.get("folder")
        self.state_manager.add_to_queue(job_to_reap)
        
        self.state_manager.add_notification_to_history(
            f"Scythe '{scythe.get('name')}' was automatically reaped."
        )

    def run_pending(self):
        """The main loop for the scheduler thread."""
        if WATCHDOG_AVAILABLE:
            event_handler = ScythesChangeHandler(self)
            self.observer = Observer()
            self.observer.schedule(event_handler, path=self.scythe_manager.scythes_file.rsplit('/', 1)[0], recursive=False)
            self.observer.start()
            logger.info("File watcher for scythes.json started.")
        else:
            logger.info("Watchdog library not found. Falling back to hourly polling for Scythe schedule changes.")

        while not self.stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)
        
        if self.observer:
            self.observer.stop()
            self.observer.join()

        logger.info("Scheduler thread has gracefully exited.")

    def stop(self):
        """Signals the scheduler thread to stop."""
        self.stop_event.set()
# lib/scheduler.py
import threading
import time
import logging
import schedule
from datetime import datetime

logger = logging.getLogger()

class Scheduler:
    """
    Manages the scheduling and execution of automated Scythe reaping.
    Runs in its own thread to avoid blocking the main application.
    """
    def __init__(self, scythe_manager, state_manager):
        self.scythe_manager = scythe_manager
        self.state_manager = state_manager
        self.stop_event = threading.Event()
        self.last_reload_time = 0
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
                    
                    # CHANGE: Handle multiple weekdays
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
        
        self.last_reload_time = time.time()
        logger.info(f"Successfully scheduled {count} Scythe(s).")

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
        
        # CHANGE: Add a notification to the history panel.
        self.state_manager.add_notification_to_history(
            f"Scythe '{scythe.get('name')}' was automatically reaped."
        )

    def run_pending(self):
        """The main loop for the scheduler thread."""
        while not self.stop_event.is_set():
            schedule.run_pending()
            
            # CHANGE: Periodically reload the schedule to pick up manual changes.
            if time.time() - self.last_reload_time > 3600: # Reload every hour
                logger.info("Performing hourly reload of scheduled jobs...")
                self._load_and_schedule_jobs()

            time.sleep(1)
        logger.info("Scheduler thread has gracefully exited.")

    def stop(self):
        """Signals the scheduler thread to stop."""
        self.stop_event.set()

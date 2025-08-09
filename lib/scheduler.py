# lib/scheduler.py
import threading
import time
import logging
import schedule

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
                    
                    job = None
                    if interval == "daily":
                        job = schedule.every().day.at(at_time)
                    elif interval == "weekly":
                        # Assuming 'weekday' is stored as 0-6 (Mon-Sun)
                        weekday = schedule_info.get("weekday")
                        if weekday == 0: job = schedule.every().monday.at(at_time)
                        elif weekday == 1: job = schedule.every().tuesday.at(at_time)
                        elif weekday == 2: job = schedule.every().wednesday.at(at_time)
                        elif weekday == 3: job = schedule.every().thursday.at(at_time)
                        elif weekday == 4: job = schedule.every().friday.at(at_time)
                        elif weekday == 5: job = schedule.every().saturday.at(at_time)
                        elif weekday == 6: job = schedule.every().sunday.at(at_time)

                    if job:
                        # Pass the scythe_id to the reap function
                        job.do(self._reap_scythe, scythe_id=scythe.get("id"))
                        count += 1
                except Exception as e:
                    logger.error(f"Failed to schedule Scythe '{scythe.get('name')}': {e}")
        
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
        
        # Ensure the schedule is still enabled before queueing
        if not scythe.get("schedule", {}).get("enabled"):
            logger.warning(f"Skipping scheduled reap for Scythe ID {scythe_id} as it is now disabled.")
            return

        job_to_reap = scythe["job_data"]
        job_to_reap["resolved_folder"] = job_to_reap.get("folder")
        self.state_manager.add_to_queue(job_to_reap)
        logger.info(f"Successfully added scheduled Scythe '{scythe.get('name')}' to the download queue.")

    def run_pending(self):
        """The main loop for the scheduler thread."""
        while not self.stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)
        logger.info("Scheduler thread has gracefully exited.")

    def stop(self):
        """Signals the scheduler thread to stop."""
        self.stop_event.set()

# lib/scheduler.py
import threading
import time
import logging
import schedule
import os
from datetime import datetime
import pytz
from . import app_globals as g # Import globals

logger = logging.getLogger()

# The ScythesChangeHandler and watchdog imports are no longer needed
# as we are not monitoring a file for changes anymore.

class Scheduler:
    """
    Manages the scheduling and execution of automated Scythe reaping.
    Runs in its own thread to avoid blocking the main application.
    """
    def __init__(self, scythe_manager, state_manager, config):
        self.scythe_manager = scythe_manager
        self.state_manager = state_manager
        self.config = config
        self.stop_event = threading.Event()
        # The observer for watchdog is no longer needed.
        self.observer = None
        self._load_and_schedule_jobs()

    def _load_and_schedule_jobs(self):
        """
        Clears the current schedule and reloads all Scythes from the database,
        scheduling the ones that have automation enabled.
        """
        schedule.clear()
        logger.info("Loading and scheduling automated Scythes from database...")
        # Scythes are now fetched from the database via the manager
        scythes = self.scythe_manager.get_all()
        count = 0

        try:
            user_tz_str = self.config.get("user_timezone", "UTC")
            user_tz = pytz.timezone(user_tz_str)
        except pytz.UnknownTimeZoneError:
            logger.error(f"Invalid timezone '{user_tz_str}' in config. Defaulting to UTC.")
            user_tz = pytz.utc

        for scythe in scythes:
            # The schedule info is now a dictionary, not a JSON string
            schedule_info = scythe.get("schedule")
            if schedule_info and schedule_info.get("enabled"):
                try:
                    interval = schedule_info.get("interval")
                    at_time_user = schedule_info.get("time") # e.g., "14:30"

                    now_user_tz = datetime.now(user_tz)
                    hour, minute = map(int, at_time_user.split(':'))
                    user_time = now_user_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    server_time = user_time.astimezone(None)
                    at_time_server = server_time.strftime("%H:%M")

                    weekdays = schedule_info.get("weekdays", [])

                    if interval == "daily":
                        schedule.every().day.at(at_time_server).do(self._reap_scythe, scythe_id=scythe.get("id"))
                        count += 1
                    elif interval == "weekly" and weekdays:
                        for day_index in weekdays:
                            job = None
                            if day_index == 0: job = schedule.every().monday.at(at_time_server)
                            elif day_index == 1: job = schedule.every().tuesday.at(at_time_server)
                            elif day_index == 2: job = schedule.every().wednesday.at(at_time_server)
                            elif day_index == 3: job = schedule.every().thursday.at(at_time_server)
                            elif day_index == 4: job = schedule.every().friday.at(at_time_server)
                            elif day_index == 5: job = schedule.every().saturday.at(at_time_server)
                            elif day_index == 6: job = schedule.every().sunday.at(at_time_server)

                            if job:
                                job.do(self._reap_scythe, scythe_id=scythe.get("id"))
                        count += 1

                except Exception as e:
                    logger.error(f"Failed to schedule Scythe '{scythe.get('name')}': {e}")

        next_run_datetime = schedule.next_run if schedule.jobs else None
        next_run_time = next_run_datetime.strftime('%Y-%m-%d %H:%M:%S') if next_run_datetime else "Not scheduled"
        logger.info(f"Successfully scheduled {count} Scythe(s). Next run at (server time): {next_run_time}")

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

        # Use the global state_manager
        g.state_manager.add_notification_to_history(
            f"Scythe '{scythe.get('name')}' was automatically reaped."
        )
        g.state_manager.add_to_queue(job_to_reap)


    def run_pending(self):
        """The main loop for the scheduler thread."""
        logger.info("Scheduler thread started. Running pending jobs...")

        while not self.stop_event.is_set():
            schedule.run_pending()
            idle_secs = schedule.idle_seconds()
            sleep_duration = min(idle_secs, 60) if idle_secs is not None and idle_secs > 0 else 60

            # Use the stop_event's wait method for an interruptible sleep
            self.stop_event.wait(sleep_duration)

        logger.info("Scheduler thread has gracefully exited.")

    def stop(self):
        """Signals the scheduler thread to stop."""
        self.stop_event.set()

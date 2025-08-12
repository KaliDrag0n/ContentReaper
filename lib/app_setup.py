# lib/app_setup.py
import os
import sys
import subprocess
import logging
import threading
import glob
import secrets
import json

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from flask_socketio import SocketIO

from . import app_globals as g
from . import dependency_manager as dm
from . import state_manager as sm
from . import scythe_manager as scm
from . import user_manager as um
from . import scheduler as sched
from . import worker
from . import config_manager
from . import database 
from .routes import register_routes

logger = logging.getLogger()

def get_secret_key():
    """Loads or creates a persistent secret key for user sessions."""
    key_file = os.path.join(g.DATA_DIR, "secret_key.json")
    try:
        with open(key_file, 'r') as f:
            return json.load(f)["secret_key"]
    except (FileNotFoundError, json.JSONDecodeError):
        logger.info("Secret key file not found or invalid. Generating a new one.")
        new_key = secrets.token_hex(24)
        with open(key_file, 'w') as f:
            json.dump({"secret_key": new_key}, f)
        return new_key

def state_emitter():
    """Monitors the state manager and emits updates to clients via SocketIO."""
    from .routes import get_current_state
    # CHANGE: Added 'scythes' to the version tracking dictionary.
    last_versions = {"queue": -1, "history": -1, "current": -1, "scythes": -1}
    
    while not g.STOP_EVENT.is_set():
        try:
            with g.state_manager._lock:
                q_ver = g.state_manager.queue_state_version
                h_ver = g.state_manager.history_state_version
                c_ver = g.state_manager.current_download_version
                s_ver = g.state_manager.scythe_state_version # CHANGE: Get scythe version

            # CHANGE: Added scythe version to the condition.
            if (q_ver != last_versions["queue"] or
                h_ver != last_versions["history"] or
                c_ver != last_versions["current"] or
                s_ver != last_versions["scythes"]):
                
                # CHANGE: Update all last known versions.
                last_versions.update({"queue": q_ver, "history": h_ver, "current": c_ver, "scythes": s_ver})
                state = get_current_state()
                g.socketio.emit('state_update', state)

            g.socketio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error in state_emitter thread: {e}")
            g.socketio.sleep(5)

def create_app():
    """The main application factory."""
    g.app = Flask(__name__, static_folder='../static', template_folder='../templates')
    g.app.secret_key = get_secret_key()
    g.app.config['WTF_CSRF_HEADERS'] = ['X-CSRF-Token']
    g.csrf = CSRFProtect(g.app)
    g.socketio = SocketIO(g.app, async_mode='eventlet')

    logger.info("--- [1/5] Initializing Database ---")
    database.create_tables()
    database.migrate_json_to_db()

    g.user_manager = um.UserManager()
    g.scythe_manager = scm.ScytheManager()
    g.state_manager = sm.StateManager()

    config_manager.load_config()
    
    logger.info("--- [2/5] Initializing Dependency Manager ---")
    g.YT_DLP_PATH, g.FFMPEG_PATH = dm.ensure_dependencies(g.APP_ROOT)
    if not g.YT_DLP_PATH or not g.FFMPEG_PATH:
        logger.critical("Application cannot start due to missing critical dependencies (yt-dlp or ffmpeg).")
        if sys.platform == "win32": os.system("pause")
        sys.exit(1)

    logger.info("--- [3/5] Checking for yt-dlp updates ---")
    try:
        update_result = subprocess.run([g.YT_DLP_PATH, '-U'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if update_result.stdout.strip(): logger.info(f"yt-dlp update check: {update_result.stdout.strip()}")
        if update_result.returncode != 0: logger.warning(f"yt-dlp update check stderr: {update_result.stderr.strip()}")
    except Exception as e: logger.warning(f"An unexpected error occurred while trying to update yt-dlp: {e}")

    logger.info("--- [4/5] Loading State from Database ---")
    log_dir = os.path.join(g.DATA_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    for log_path in glob.glob(os.path.join(log_dir, "job_active_*.log")):
        try: os.remove(log_path)
        except OSError as e: logger.error(f"Failed to remove stale active log file {log_path}: {e}")
        
    g.state_manager.load_state()

    logger.info("--- [5/5] Starting Background Threads ---")
    cookie_file = os.path.join(g.DATA_DIR, "cookies.txt")
    g.WORKER_THREAD = threading.Thread(target=worker.yt_dlp_worker, args=(g.state_manager, g.CONFIG, log_dir, cookie_file, g.YT_DLP_PATH, g.FFMPEG_PATH, g.STOP_EVENT))
    g.WORKER_THREAD.start()
    
    g.scheduler = sched.Scheduler(g.scythe_manager, g.state_manager, g.CONFIG)
    g.SCHEDULER_THREAD = threading.Thread(target=g.scheduler.run_pending)
    g.SCHEDULER_THREAD.start()

    g.STATE_EMITTER_THREAD = g.socketio.start_background_task(target=state_emitter)
    
    register_routes(g.app)
    
    logger.info("--- Application Initialized Successfully ---")
    return g.app

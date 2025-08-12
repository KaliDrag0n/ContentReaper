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
    from .routes import get_current_state  # Local import to avoid circular dependency
    last_versions = {"queue": -1, "history": -1, "current": -1}
    
    while not g.STOP_EVENT.is_set():
        try:
            with g.state_manager._lock:
                q_ver = g.state_manager.queue_state_version
                h_ver = g.state_manager.history_state_version
                c_ver = g.state_manager.current_download_version

            if (q_ver != last_versions["queue"] or
                h_ver != last_versions["history"] or
                c_ver != last_versions["current"]):
                
                last_versions.update({"queue": q_ver, "history": h_ver, "current": c_ver})
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

    # Initialize Managers
    users_file = os.path.join(g.DATA_DIR, "users.json")
    g.user_manager = um.UserManager(users_file)

    # Load Config (depends on user_manager for migration)
    config_manager.load_config()
    
    logger.info("--- [1/4] Initializing Dependency Manager ---")
    g.YT_DLP_PATH, g.FFMPEG_PATH = dm.ensure_dependencies(g.APP_ROOT)
    if not g.YT_DLP_PATH or not g.FFMPEG_PATH:
        logger.critical("Application cannot start due to missing critical dependencies (yt-dlp or ffmpeg).")
        if sys.platform == "win32": os.system("pause")
        sys.exit(1)

    logger.info("--- [2/4] Checking for yt-dlp updates ---")
    try:
        update_result = subprocess.run([g.YT_DLP_PATH, '-U'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if update_result.stdout.strip(): logger.info(f"yt-dlp update check: {update_result.stdout.strip()}")
        if update_result.returncode != 0: logger.warning(f"yt-dlp update check stderr: {update_result.stderr.strip()}")
    except Exception as e: logger.warning(f"An unexpected error occurred while trying to update yt-dlp: {e}")

    logger.info("--- [3/4] Initializing State and Scythe Managers ---")
    log_dir = os.path.join(g.DATA_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    state_file = os.path.join(g.DATA_DIR, "state.json")
    g.state_manager = sm.StateManager(state_file)
    
    scythes_file = os.path.join(g.DATA_DIR, "scythes.json")
    g.scythe_manager = scm.ScytheManager(scythes_file)
    
    for log_path in glob.glob(os.path.join(log_dir, "job_active_*.log")):
        try: os.remove(log_path)
        except OSError as e: logger.error(f"Failed to remove stale active log file {log_path}: {e}")
        
    g.state_manager.load_state()

    logger.info("--- [4/4] Starting Background Threads ---")
    cookie_file = os.path.join(g.DATA_DIR, "cookies.txt")
    g.WORKER_THREAD = threading.Thread(target=worker.yt_dlp_worker, args=(g.state_manager, g.CONFIG, log_dir, cookie_file, g.YT_DLP_PATH, g.FFMPEG_PATH, g.STOP_EVENT))
    g.WORKER_THREAD.start()
    
    g.scheduler = sched.Scheduler(g.scythe_manager, g.state_manager, g.CONFIG)
    g.SCHEDULER_THREAD = threading.Thread(target=g.scheduler.run_pending)
    g.SCHEDULER_THREAD.start()

    g.STATE_EMITTER_THREAD = g.socketio.start_background_task(target=state_emitter)
    
    # Register routes after all components are initialized
    register_routes(g.app)
    
    logger.info("--- Application Initialized Successfully ---")
    return g.app

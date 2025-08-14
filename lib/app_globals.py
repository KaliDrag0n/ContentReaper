# lib/app_globals.py
import threading
from flask_socketio import SocketIO

# --- Application Constants ---
APP_VERSION = "4.6.4"
APP_NAME = "ContentReaper"
GITHUB_REPO_SLUG = "KaliDrag0n/ContentReaper"

# --- Global Application Objects ---
# These are initialized in app_setup.py and shared across modules.
app = None
socketio = None
csrf = None
db_conn = None # Added for database connection

# --- Managers ---
state_manager = None
scythe_manager = None
user_manager = None
scheduler = None

# --- Background Threads & Events ---
WORKER_THREAD = None
SCHEDULER_THREAD = None
STATE_EMITTER_THREAD = None
MONITOR_THREAD = None
STOP_EVENT = threading.Event()
first_run_lock = threading.Lock() # Added for session setup

# --- Paths & Configuration ---
YT_DLP_PATH = None
FFMPEG_PATH = None
CONFIG = {}
APP_ROOT = None
DATA_DIR = None

# --- Status Dictionaries ---
update_status = {
    "update_available": False,
    "latest_version": "0.0.0",
    "release_url": "",
    "release_notes": ""
}

# web_tool.py
import os
import sys
import subprocess
import platform
import logging
from logging.handlers import RotatingFileHandler
import glob
import threading
import json
import atexit
import time
import signal
import shutil
import io
import zipfile
import secrets

# --- Define APP_ROOT early for use in logger setup ---
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# --- Custom logging filter to create relative paths ---
class RelativePathFilter(logging.Filter):
    def filter(self, record):
        try:
            record.relativepath = os.path.relpath(record.pathname, APP_ROOT)
        except ValueError:
            record.relativepath = record.pathname
        return True

# --- Set up logging immediately ---
console_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [in %(relativepath)s:%(lineno)d] :: %(message)s')
file_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [in %(pathname)s:%(lineno)d] :: %(message)s')
simple_formatter = logging.Formatter('%(message)s')

log_file = os.path.join(DATA_DIR, 'startup.log')

file_handler = RotatingFileHandler(log_file, maxBytes=1*1024*1024, backupCount=2)
file_handler.setFormatter(file_log_formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(console_log_formatter)
console_handler.setLevel(logging.INFO)
console_handler.addFilter(RelativePathFilter())

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

banner_formatter = logging.Formatter('%(message)s')
banner_handler = logging.StreamHandler()
banner_handler.setFormatter(banner_formatter)
banner_logger = logging.getLogger('banner')
banner_logger.addHandler(banner_handler)
banner_logger.setLevel(logging.INFO)
banner_logger.propagate = False

# --- App Constants ---
APP_VERSION = "4.5.0"
APP_NAME = "ContentReaper"
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

# --- Global Variables (will be initialized in create_app) ---
state_manager = None
scythe_manager = None
user_manager = None
scheduler = None
SCHEDULER_THREAD = None
update_status = {"update_available": False, "latest_version": "0.0.0", "release_url": "", "release_notes": ""}
WORKER_THREAD = None
STOP_EVENT = threading.Event()
YT_DLP_PATH = None
FFMPEG_PATH = None
CONFIG = {}

def print_banner():
    """Prints the stylized startup banner to the console."""
    logger.removeHandler(console_handler)
    
    banner_logger.info("="*95   )
    banner_logger.info(r"""
▄█▄    ████▄    ▄      ▄▄▄▄▀ ▄███▄      ▄      ▄▄▄▄▀     █▄▄▄▄ ▄███▄   ██   █ ▄▄  ▄███▄   █▄▄▄▄ 
█▀ ▀▄  █   █     █  ▀▀▀ █    █▀   ▀      █  ▀▀▀ █        █  ▄▀ █▀   ▀  █ █  █   █ █▀   ▀  █  ▄▀ 
█   ▀  █   █ ██   █     █    ██▄▄    ██   █     █        █▀▀▌  ██▄▄    █▄▄█ █▀▀▀  ██▄▄    █▀▀▌  
█▄  ▄▀ ▀████ █ █  █    █     █▄   ▄▀ █ █  █    █         █  █  █▄   ▄▀ █  █ █     █▄   ▄▀ █  █  
▀███▀        █  █ █   ▀      ▀███▀   █  █ █   ▀            █   ▀███▀      █  █    ▀███▀     █   
             █   ██                  █   ██               ▀              █    ▀            ▀    
                                                                        ▀                       
    """)
    banner_logger.info(" " * 37 + "--- ContentReaper ---")
    banner_logger.info("="*95 + "\n")
    
    logger.addHandler(console_handler)
    console_handler.setFormatter(simple_formatter)
    file_handler.setFormatter(simple_formatter)

    logger.info("="*35 + f" Starting ContentReaper v{APP_VERSION} " + "="*35 + "\n")

    console_handler.setFormatter(console_log_formatter)
    file_handler.setFormatter(file_log_formatter)

# --- Import Flask and related libraries ---
try:
    import flask, waitress, requests, schedule
    from werkzeug.security import generate_password_hash, check_password_hash
    from flask_wtf.csrf import CSRFProtect, generate_csrf
    from lib import dependency_manager as dm, state_manager as sm, worker, sanitizer, scythe_manager as scm, user_manager as um, scheduler as sched
    from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file, session, flash
    from functools import wraps
except ImportError:
    logger.critical("Core Python packages not found. Attempting to install from requirements.txt...")
    try:
        requirements_path = os.path.join(APP_ROOT, 'requirements.txt')
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', requirements_path])
        logger.info("Dependencies installed successfully. Please restart the application.")
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        logger.critical(f"Failed to install dependencies. Please run 'pip install -r requirements.txt' manually. Error: {e}")
        sys.exit(1)

def migrate_legacy_data():
    """
    Checks for data files in the root directory and moves them to the new
    'data' subdirectory for better organization and security. Also updates
    log paths within the state file after migration.
    """
    legacy_files = [
        "config.json", "state.json", "scythes.json", "users.json", "cookies.txt",
        "state.json.bak", "scythes.json.bak", "users.json.bak"
    ]
    legacy_dirs = ["logs"]
    migrated_something = False
    state_json_was_migrated = False

    for filename in legacy_files:
        old_path = os.path.join(APP_ROOT, filename)
        new_path = os.path.join(DATA_DIR, filename)
        if os.path.exists(old_path):
            if not os.path.exists(new_path):
                try:
                    shutil.move(old_path, new_path)
                    logger.info(f"Migrated legacy file '{filename}' to data directory.")
                    migrated_something = True
                    if filename == "state.json":
                        state_json_was_migrated = True
                except Exception as e:
                    logger.error(f"Failed to migrate '{filename}': {e}")
            else:
                logger.warning(f"Legacy file '{filename}' found, but destination already exists. Skipping migration for this file.")

    for dirname in legacy_dirs:
        old_path = os.path.join(APP_ROOT, dirname)
        new_path = os.path.join(DATA_DIR, dirname)
        if os.path.isdir(old_path):
            if not os.path.isdir(new_path):
                try:
                    shutil.move(old_path, new_path)
                    logger.info(f"Migrated legacy directory '{dirname}' to data directory.")
                    migrated_something = True
                except Exception as e:
                    logger.error(f"Failed to migrate '{dirname}': {e}")
            else:
                logger.warning(f"Legacy directory '{dirname}' found, but destination already exists. Skipping migration.")
    
    if state_json_was_migrated:
        logger.info("Checking for legacy log paths in migrated state.json...")
        state_json_path = os.path.join(DATA_DIR, "state.json")
        try:
            with open(state_json_path, 'r+', encoding='utf-8') as f:
                state_data = json.load(f)
                history = state_data.get("history", [])
                paths_updated = 0
                
                old_log_dir = os.path.join(APP_ROOT, "logs")
                new_log_dir = os.path.join(DATA_DIR, "logs")

                for item in history:
                    if log_path := item.get("log_path"):
                        if log_path.startswith(old_log_dir):
                            new_log_path = log_path.replace(old_log_dir, new_log_dir, 1)
                            item["log_path"] = new_log_path
                            paths_updated += 1
                
                if paths_updated > 0:
                    f.seek(0)
                    json.dump(state_data, f, indent=4)
                    f.truncate()
                    logger.info(f"Updated {paths_updated} log path(s) inside state.json.")
                else:
                    logger.info("No legacy log paths needed updating in state.json.")

        except Exception as e:
            logger.error(f"Could not update log paths in state.json after migration: {e}")

    if migrated_something:
        logger.info("Data migration complete.")

# --- Role-based Security System ---
def permission_required(permission):
    """Decorator for API routes to check if a logged-in user has a specific permission."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            role = session.get('role')
            if not role:
                return jsonify({"error": "Authentication required. Please log in."}), 401
            
            if role == 'admin':
                return f(*args, **kwargs)

            user = user_manager.get_user(role)
            if user and user.get("permissions", {}).get(permission, False):
                return f(*args, **kwargs)

            return jsonify({"error": "Permission denied."}), 403
        return decorated_function
    return decorator

def page_permission_required(permission):
    """Decorator for page routes that redirects and flashes a message on permission failure."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            role = session.get('role')
            has_permission = False
            if role:
                if role == 'admin':
                    has_permission = True
                else:
                    user = user_manager.get_user(role)
                    if user and user.get("permissions", {}).get(permission, False):
                        has_permission = True
            
            if not has_permission:
                flash("You do not have permission to access this page. Please log in as an administrator.", "danger")
                return redirect(url_for('index_route'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def is_safe_path(basedir, path_to_check, allow_file=False):
    real_basedir = os.path.normcase(os.path.realpath(basedir))
    try:
        real_path_to_check = os.path.normcase(os.path.realpath(path_to_check))
    except OSError:
        return False
    
    if not allow_file and not os.path.isdir(real_path_to_check):
        return False
        
    return real_path_to_check.startswith(real_basedir)

def secure_join(base_dir, user_path):
    user_path = user_path.replace("\\", "/").strip("/")
    sanitized_components = [sanitizer.sanitize_filename(part) for part in user_path.split('/') if part and part not in ('.', '..')]
    
    if not sanitized_components:
        return os.path.realpath(base_dir)

    safe_relative_path = os.path.join(*sanitized_components)
    full_path = os.path.join(base_dir, safe_relative_path)
    
    return os.path.normpath(full_path)


# --- App Initialization and Management ---

def load_config():
    """Loads configuration, migrates old format, sets defaults, and validates."""
    global CONFIG
    
    config_path = os.path.join(DATA_DIR, "config.json")
    
    defaults = {
        "download_dir": os.path.join(APP_ROOT, "downloads"),
        "temp_dir": os.path.join(APP_ROOT, ".temp"),
        "server_host": "0.0.0.0",
        "server_port": 8080,
        "log_level": "INFO",
        "public_user": None
    }
    
    CONFIG = defaults.copy()
    config_updated = False

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                
                if "users" in loaded_config or "guest_permissions" in loaded_config:
                    logger.warning("Old user config format detected. Migrating to users.json.")
                    
                    users_to_migrate = loaded_config.pop("users", {})
                    guest_perms = loaded_config.pop("guest_permissions", {})

                    if "admin" in users_to_migrate and users_to_migrate["admin"].get("password_hash"):
                        admin_user = {"password_hash": users_to_migrate["admin"]["password_hash"], "permissions": {}}
                        user_manager.update_user("admin", password=None, permissions=admin_user["permissions"])
                        all_users = user_manager._load_users()
                        all_users['admin']['password_hash'] = admin_user['password_hash']
                        user_manager._save_users(all_users)


                    if "guest" in users_to_migrate:
                        guest_user = {"password_hash": users_to_migrate["guest"].get("password_hash"), "permissions": guest_perms}
                        user_manager.update_user("guest", password=None, permissions=guest_user["permissions"])
                        all_users = user_manager._load_users()
                        all_users['guest']['password_hash'] = guest_user['password_hash']
                        user_manager._save_users(all_users)

                    config_updated = True
                
                CONFIG.update(loaded_config)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load config.json, using defaults. Error: {e}")
    
    log_level = CONFIG.get("log_level", "INFO").upper()
    if log_level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        logger.setLevel(getattr(logging, log_level))
        logger.info(f"Log level set to {log_level}")
    else:
        logger.warning(f"Invalid log_level '{log_level}' in config.json. Defaulting to INFO.")

    for key, name in [("download_dir", "Download"), ("temp_dir", "Temporary")]:
        path = CONFIG.get(key)
        if not path or not os.path.isabs(path):
            logger.critical(f"{name} directory path must be an absolute path. Path: '{path}'")
            raise RuntimeError(f"Configuration validation failed for '{key}'.")
        try:
            os.makedirs(path, exist_ok=True)
            if not os.access(path, os.W_OK):
                raise OSError("No write permissions.")
        except Exception as e:
            logger.critical(f"Path for '{key}' ('{path}') is invalid: {e}")
            raise RuntimeError(f"Configuration validation failed for '{key}'.")

    if config_updated or not os.path.exists(config_path):
        save_config()

def save_config():
    """Saves the current configuration to config.json."""
    config_path = os.path.join(DATA_DIR, "config.json")
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")

def cleanup_stale_processes_and_files(temp_dir):
    logger.info("--- Running Pre-Startup Cleanup ---")
    processes_to_kill = {"yt-dlp": "yt-dlp", "ffmpeg": "ffmpeg"}
    for name, process_name in processes_to_kill.items():
        logger.info(f"Checking for and terminating any stale '{name}' processes...")
        try:
            command = ["taskkill", "/F", "/IM", f"{process_name}.exe"] if platform.system() == "Windows" else ["pkill", "-f", process_name]
            result = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0: logger.info(f"Successfully terminated one or more stale '{name}' processes.")
            else: logger.info(f"No stale '{name}' processes found.")
        except FileNotFoundError: logger.warning(f"Could not find command to terminate stale '{name}' processes (taskkill/pkill).")
        except Exception as e: logger.error(f"An error occurred while trying to kill stale '{name}' processes: {e}")
    
    logger.info("Cleaning up orphaned temporary job directories...")
    if os.path.exists(temp_dir):
        try:
            for dir_path in glob.glob(os.path.join(temp_dir, "job_*")):
                shutil.rmtree(dir_path)
                logger.info(f"Removed stale temporary directory: {os.path.basename(dir_path)}")
        except Exception as e: logger.error(f"Error during temporary file cleanup: {e}")
    logger.info("--- Pre-Startup Cleanup Finished ---")

def _run_update_check():
    global update_status
    try:
        res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest", timeout=15)
        res.raise_for_status()
        latest_release = res.json()
        latest_version_tag = latest_release.get("tag_name", "").lstrip('v')
        with state_manager._lock:
            if latest_version_tag > APP_VERSION:
                update_status.update({"update_available": True, "latest_version": latest_version_tag, "release_url": latest_release.get("html_url"), "release_notes": latest_release.get("body")})
            else: update_status["update_available"] = False
    except Exception as e: logger.warning(f"Update check failed: {e}")

def scheduled_update_check():
    while not STOP_EVENT.is_set():
        _run_update_check()
        STOP_EVENT.wait(3600)

def shutdown_server():
    """Triggers a graceful shutdown of the application."""
    logger.info("Shutdown initiated.")
    STOP_EVENT.set()
    # Use a thread to send the kill signal after a short delay to allow the API response to be sent
    threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT)).start()

def run_update_script():
    """
    Launches the external updater script in a new process and then shuts down.
    This is now a universal, cross-platform function.
    """
    time.sleep(2)  # Give the server a moment to respond to the API request

    # Path to the new universal updater script
    updater_script_path = os.path.join(APP_ROOT, 'updater.py')
    
    # The command to execute the updater. We use sys.executable to ensure
    # we're using the same Python interpreter (e.g., from the venv).
    command = [sys.executable, updater_script_path]
    
    logger.info(f"Starting update process with command: {' '.join(command)}")
    
    # Use Popen to run the updater in a new, detached process.
    # This allows the current script to exit while the updater continues to run.
    if platform.system() == "Windows":
        # On Windows, CREATE_NEW_CONSOLE ensures it runs in a new window
        # and is fully detached from the parent process.
        subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        # On Linux/macOS, a simple Popen is sufficient. The new process
        # will not be a child of this script once this script exits.
        subprocess.Popen(command)

    # Gracefully shutdown the current server to release the port
    shutdown_server()

# --- Application Factory ---

def create_app():
    global state_manager, scythe_manager, user_manager, scheduler, WORKER_THREAD, SCHEDULER_THREAD, YT_DLP_PATH, FFMPEG_PATH
    
    migrate_legacy_data()
    
    print_banner()
    
    users_file = os.path.join(DATA_DIR, "users.json")
    user_manager = um.UserManager(users_file)

    load_config()
    cleanup_stale_processes_and_files(CONFIG['temp_dir'])
    
    logger.info("--- [1/4] Initializing Dependency Manager ---")
    YT_DLP_PATH, FFMPEG_PATH = dm.ensure_dependencies(APP_ROOT)
    if not YT_DLP_PATH or not FFMPEG_PATH:
        logger.critical("Application cannot start due to missing critical dependencies (yt-dlp or ffmpeg).")
        if platform.system() == "Windows": os.system("pause")
        sys.exit(1)

    logger.info("--- [2/4] Checking for yt-dlp updates ---")
    try:
        update_result = subprocess.run([YT_DLP_PATH, '-U'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        if update_result.stdout.strip(): logger.info(f"yt-dlp update check: {update_result.stdout.strip()}")
        if update_result.returncode != 0: logger.warning(f"yt-dlp update check stderr: {update_result.stderr.strip()}")
    except Exception as e: logger.warning(f"An unexpected error occurred while trying to update yt-dlp: {e}")

    logger.info("--- [3/4] Initializing Flask Application ---")
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    app.config['WTF_CSRF_HEADERS'] = ['X-CSRF-Token']
    csrf = CSRFProtect(app)

    @app.before_request
    def apply_unsecured_admin_session():
        if session.get('manual_login'):
            return
        
        admin_user = user_manager.get_user('admin')
        if admin_user and not admin_user.get('password_hash'):
            session['role'] = 'admin'
            session['manual_login'] = False

    logger.info("--- [4/4] Loading state and starting background threads ---")
    log_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    state_file = os.path.join(DATA_DIR, "state.json")
    state_manager = sm.StateManager(state_file)
    
    scythes_file = os.path.join(DATA_DIR, "scythes.json")
    scythe_manager = scm.ScytheManager(scythes_file)
    
    for log_path in glob.glob(os.path.join(log_dir, "job_active_*.log")):
        try: os.remove(log_path)
        except OSError as e: logger.error(f"Failed to remove stale active log file {log_path}: {e}")
        
    state_manager.load_state()
    threading.Thread(target=scheduled_update_check, daemon=True).start()
    
    cookie_file = os.path.join(DATA_DIR, "cookies.txt")
    WORKER_THREAD = threading.Thread(target=worker.yt_dlp_worker, args=(state_manager, CONFIG, log_dir, cookie_file, YT_DLP_PATH, FFMPEG_PATH, STOP_EVENT))
    WORKER_THREAD.start()
    
    scheduler = sched.Scheduler(scythe_manager, state_manager)
    SCHEDULER_THREAD = threading.Thread(target=scheduler.run_pending)
    SCHEDULER_THREAD.start()
    
    logger.info("--- Application Initialized Successfully ---")
    register_routes(app)
    return app

def _parse_music_options(form_data):
    return {"format": form_data.get("music_audio_format"), "quality": form_data.get("music_audio_quality")}

def _parse_video_options(form_data):
    return {
        "quality": form_data.get("video_quality"), 
        "format": form_data.get("video_format"), 
        "embed_subs": form_data.get("video_embed_subs") == "on", 
        "codec": form_data.get("video_codec_preference")
    }

def _parse_clip_options(form_data):
    return {"format": form_data.get("clip_format")}

def _parse_custom_options(form_data):
    return {"custom_args": form_data.get("custom_args")}

def _parse_job_data(form_data):
    """Parses form data to create a job dictionary using a modular approach."""
    mode = form_data.get("download_mode")
    if not mode:
        raise ValueError("Download mode not specified.")

    job_base = {
        "mode": mode,
        "folder": form_data.get(f"{mode}_foldername", "").strip(),
        "archive": form_data.get("use_archive") == "yes",
        "proxy": form_data.get("proxy", "").strip(),
        "rate_limit": form_data.get("rate_limit", "").strip(),
        "embed_lyrics": form_data.get("embed_lyrics") == "on",
        "split_chapters": form_data.get("split_chapters") == "on"
    }
    try:
        p_start = form_data.get("playlist_start", "").strip()
        p_end = form_data.get("playlist_end", "").strip()
        job_base["playlist_start"] = int(p_start) if p_start else None
        job_base["playlist_end"] = int(p_end) if p_end else None
    except ValueError:
        raise ValueError("Playlist start/end must be a number.")
    
    mode_parsers = {
        'music': _parse_music_options,
        'video': _parse_video_options,
        'clip': _parse_clip_options,
        'custom': _parse_custom_options
    }
    
    parser = mode_parsers.get(mode)
    if parser:
        job_base.update(parser(form_data))
    else:
        logger.warning(f"Unknown download mode '{mode}' encountered.")

    return job_base

def register_routes(app):
    @app.context_processor
    def inject_globals():
        return dict(
            app_name=APP_NAME, 
            app_version=APP_VERSION,
            csrf_token=generate_csrf
        )

    def get_current_state():
        with state_manager._lock:
            state = {
                "queue": state_manager.get_queue_list(),
                "current": state_manager.current_download if state_manager.current_download.get("url") else None,
                "history": state_manager.get_history_summary(),
                "is_paused": not state_manager.queue_paused_event.is_set()
            }
        state["scythes"] = scythe_manager.get_all()
        return state

    # CHANGE: Removed the old SVG favicon route. Flask will now serve the static file.
    @app.route('/favicon.ico')
    def favicon():
        return send_file(os.path.join(app.static_folder, 'img/icon', 'favicon.ico'))


    @app.route("/")
    def index_route():
        return render_template("index.html")

    @app.route("/file_manager")
    def file_manager_route():
        return render_template("file_manager.html")

    @app.route("/settings")
    @page_permission_required('admin')
    def settings_route():
        with state_manager._lock:
            current_update_status = update_status.copy()
        return render_template("settings.html", update_info=current_update_status)
    
    @app.route("/logs")
    @page_permission_required('admin')
    def logs_route():
        return render_template("logs.html")

    @app.route("/api/status")
    def status_poll_route():
        return jsonify(get_current_state())

    @app.route("/api/update_check")
    def update_check_route():
        with state_manager._lock:
            return jsonify(update_status)

    @app.route("/queue", methods=["POST"])
    @permission_required('can_add_to_queue')
    def add_to_queue_route():
        try:
            urls = [line.strip() for line in request.form.get("urls", "").strip().splitlines() if line.strip()]
            if not urls:
                return jsonify({"error": "No valid URLs provided."}), 400
            job_base = _parse_job_data(request.form)
            for url in urls:
                state_manager.add_to_queue({**job_base, "url": url})
            return jsonify({
                "message": f"Added {len(urls)} job(s) to the queue.",
                "newState": get_current_state()
            })
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error adding to queue: {e}", exc_info=True)
            return jsonify({"error": "An unexpected server error occurred."}), 500

    @app.route("/queue/continue", methods=['POST'])
    @permission_required('can_add_to_queue')
    def continue_job_route():
        data = request.get_json()
        if not data or "log_id" not in data:
            return jsonify({"error": "Invalid request. Missing log_id."}), 400
        
        history_item = state_manager.get_history_item_by_log_id(data["log_id"])
        if not history_item or "job_data" not in history_item:
            return jsonify({"error": "Could not find original job data in history."}), 404
            
        job_to_continue = history_item["job_data"]
        job_to_continue["resolved_folder"] = history_item.get("folder")
        
        state_manager.add_to_queue(job_to_continue)
        return jsonify({
            "message": f"Re-queued job for URL: {job_to_continue['url']}",
            "newState": get_current_state()
        })

    @app.route('/api/auth/status')
    def auth_status_route():
        admin_user = user_manager.get_user('admin')
        admin_pass_set = bool(admin_user and admin_user.get("password_hash"))
        
        role = session.get('role')
        manually_logged_in = session.get('manual_login', False)

        public_user = CONFIG.get('public_user')
        if public_user and not manually_logged_in:
            user_data = user_manager.get_user(public_user)
            if user_data:
                session['role'] = public_user
                role = public_user
        
        permissions = {}
        if role and role != 'admin':
            user_data = user_manager.get_user(role)
            if user_data:
                permissions = user_data.get('permissions', {})

        return jsonify({
            "admin_password_set": admin_pass_set, 
            "logged_in": bool(role),
            "manually_logged_in": manually_logged_in,
            "role": role,
            "permissions": permissions
        })
    
    @app.route('/api/auth/csrf-token')
    def get_csrf_token_route():
        return jsonify({"csrf_token": generate_csrf()})

    @app.route('/api/auth/login', methods=['POST'])
    def login_route():
        data = request.get_json() or {}
        username = data.get('username', '').lower()
        password = data.get('password')
        
        user_data = user_manager.get_user(username)
        
        if not user_data or not user_data.get("password_hash"):
            return jsonify({"error": "Invalid username or password."}), 401
        
        if check_password_hash(user_data["password_hash"], password):
            session['role'] = username
            session['manual_login'] = True
            return jsonify({"message": "Login successful."})
        
        return jsonify({"error": "Invalid username or password."}), 401

    @app.route('/api/auth/logout', methods=['POST'])
    def logout_route():
        session.pop('role', None)
        session.pop('manual_login', None)
        return jsonify({"message": "Logged out."})
        
    @app.route('/api/settings', methods=['GET', 'POST'])
    @permission_required('admin')
    def api_settings_route():
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid request body."}), 400

            CONFIG["download_dir"] = data.get("download_dir", CONFIG["download_dir"]).strip()
            CONFIG["temp_dir"] = data.get("temp_dir", CONFIG["temp_dir"]).strip()
            CONFIG["log_level"] = data.get("log_level", CONFIG["log_level"]).strip().upper()
            CONFIG["server_host"] = data.get("server_host", CONFIG["server_host"]).strip()
            CONFIG["public_user"] = data.get("public_user") if data.get("public_user") != "None" else None
            try:
                CONFIG["server_port"] = int(data.get("server_port", CONFIG["server_port"]))
            except (ValueError, TypeError):
                logger.warning(f"Invalid server_port value received: {data.get('server_port')}. Retaining existing value.")

            save_config()
            
            cookie_file = os.path.join(DATA_DIR, "cookies.txt")
            try:
                with open(cookie_file, 'w', encoding='utf-8') as f:
                    f.write(data.get("cookie_content", ""))
            except Exception as e:
                logger.error(f"Failed to write to cookie file: {e}")
                return jsonify({"error": "Failed to save cookie file."}), 500
            
            logger.info("Settings saved via API. Host/port/log level changes will apply on next restart.")
            return jsonify({"message": "Settings saved successfully. Restart required for some changes."})

        cookie_file = os.path.join(DATA_DIR, "cookies.txt")
        cookie_content = ""
        try:
            if os.path.exists(cookie_file):
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    cookie_content = f.read()
        except Exception as e:
            logger.error(f"Could not read cookie file for API: {e}")

        return jsonify({
            "config": CONFIG,
            "cookies": cookie_content,
            "users": user_manager.get_all_users()
        })

    @app.route('/api/users', methods=['POST'])
    @permission_required('admin')
    def add_user_route():
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        permissions = data.get('permissions')
        if not username or not isinstance(permissions, dict):
            return jsonify({"error": "Invalid payload."}), 400
        
        if user_manager.add_user(username, password, permissions):
            return jsonify({"message": f"User '{username}' created."}), 201
        return jsonify({"error": f"User '{username}' already exists."}), 409

    @app.route('/api/users/<username>', methods=['PUT'])
    @permission_required('admin')
    def update_user_route(username):
        data = request.get_json()
        password = data.get('password')
        permissions = data.get('permissions')
        if not isinstance(permissions, dict):
            return jsonify({"error": "Invalid payload."}), 400
        
        if user_manager.update_user(username, password, permissions):
            return jsonify({"message": f"User '{username}' updated."})
        return jsonify({"error": "User not found."}), 404

    @app.route('/api/users/<username>', methods=['DELETE'])
    @permission_required('admin')
    def delete_user_route(username):
        if user_manager.delete_user(username):
            return jsonify({"message": f"User '{username}' deleted."})
        return jsonify({"error": "User not found or cannot be deleted."}), 404
    
    @app.route("/api/stop", methods=['POST'])
    @permission_required('can_add_to_queue')
    def stop_route():
        mode = (request.get_json() or {}).get('mode', 'cancel').upper()
        state_manager.stop_mode = "SAVE" if mode == 'SAVE' else "CANCEL"
        state_manager.cancel_event.set()
        return jsonify({"message": f"{state_manager.stop_mode.capitalize()} signal sent."})

    @app.route('/queue/clear', methods=['POST'])
    @permission_required('can_add_to_queue')
    def clear_queue_route():
        state_manager.clear_queue()
        return jsonify({"message": "Queue cleared.", "newState": get_current_state()})

    @app.route('/history/clear', methods=['POST'])
    @permission_required('admin')
    def clear_history_route():
        log_dir = os.path.join(DATA_DIR, "logs")
        for path in state_manager.clear_history():
            if is_safe_path(log_dir, path, allow_file=True):
                try: os.remove(path)
                except Exception as e: logger.error(f"Could not delete log file {path}: {e}")
        return jsonify({"message": "History cleared.", "newState": get_current_state()})

    @app.route("/api/delete_item", methods=['POST'])
    @permission_required('can_delete_files')
    def delete_item_route():
        paths_to_delete = (request.get_json() or {}).get('paths', [])
        if not paths_to_delete: return jsonify({"error": "Missing 'paths' parameter."}), 400
        
        base_download_dir = CONFIG.get("download_dir")
        deleted_count, errors = 0, []

        for item_path in paths_to_delete:
            full_path = secure_join(base_download_dir, item_path)
            if not full_path or not is_safe_path(base_download_dir, full_path, allow_file=True) or not os.path.exists(full_path):
                errors.append(f"Skipping invalid or non-existent path: {item_path}")
                continue
            try:
                if os.path.isdir(full_path): shutil.rmtree(full_path)
                else: os.remove(full_path)
                deleted_count += 1
            except Exception as e: errors.append(f"Error deleting {item_path}: {e}")
        
        if errors: return jsonify({"message": f"Completed with errors. Deleted {deleted_count} item(s).", "errors": errors}), 500
        return jsonify({"message": f"Successfully deleted {deleted_count} item(s)."})

    @app.route("/api/force_update_check", methods=['POST'])
    @permission_required('admin')
    def force_update_check_route():
        _run_update_check()
        return jsonify({"message": "Update check completed."})

    @app.route('/api/shutdown', methods=['POST'])
    @permission_required('admin')
    def shutdown_route():
        logger.info("Shutdown requested via API.")
        shutdown_server()
        return jsonify({"message": "Server is shutting down."})

    @app.route('/api/install_update', methods=['POST'])
    @permission_required('admin')
    def install_update_route():
        logger.info("Update requested via API.")
        threading.Thread(target=run_update_script).start()
        return jsonify({"message": "Update process initiated. Server will restart."})

    @app.route('/queue/delete/by-id/<int:job_id>', methods=['POST'])
    @permission_required('can_add_to_queue')
    def delete_from_queue_route(job_id):
        state_manager.delete_from_queue(job_id)
        return jsonify({"message": "Queue item removed.", "newState": get_current_state()})

    @app.route('/queue/reorder', methods=['POST'])
    @permission_required('can_add_to_queue')
    def reorder_queue_route():
        data = request.get_json()
        try:
            ordered_ids = [int(i) for i in data.get('order', [])]
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid job IDs provided."}), 400
        state_manager.reorder_queue(ordered_ids)
        return jsonify({"message": "Queue reordered.", "newState": get_current_state()})

    @app.route('/queue/pause', methods=['POST'])
    @permission_required('can_add_to_queue')
    def pause_queue_route():
        state_manager.pause_queue()
        return jsonify({"message": "Queue paused.", "newState": get_current_state()})

    @app.route('/queue/resume', methods=['POST'])
    @permission_required('can_add_to_queue')
    def resume_queue_route():
        state_manager.resume_queue()
        return jsonify({"message": "Queue resumed.", "newState": get_current_state()})

    @app.route('/history/delete/<int:log_id>', methods=['POST'])
    @permission_required('admin')
    def delete_from_history_route(log_id):
        log_dir = os.path.join(DATA_DIR, "logs")
        path_to_delete = state_manager.delete_from_history(log_id)
        if path_to_delete and is_safe_path(log_dir, path_to_delete, allow_file=True):
            try: os.remove(path_to_delete)
            except Exception as e: logger.error(f"Could not delete log file {path_to_delete}: {e}")
        return jsonify({"message": "History item deleted.", "newState": get_current_state()})

    @app.route('/api/history/item/<int:log_id>')
    def get_history_item_route(log_id):
        item = state_manager.get_history_item_by_log_id(log_id)
        if not item:
            return jsonify({"error": "History item not found."}), 404
        
        if request.args.get('include_log') == 'true':
            log_dir = os.path.join(DATA_DIR, "logs")
            log_path = item.get("log_path")
            log_content = "Log not found on disk or could not be read."
            
            if log_path and log_path != "LOG_SAVE_ERROR" and is_safe_path(log_dir, log_path, allow_file=True):
                try:
                    with open(log_path, 'r', encoding='utf-8') as f:
                        log_content = f.read()
                except Exception as e:
                    log_content = f"ERROR: Could not read log file. Reason: {e}"
            elif log_path == "LOG_SAVE_ERROR":
                log_content = "There was an error saving the log file for this job."
            
            item['log_content'] = log_content

        return jsonify(item)

    @app.route('/api/log/live/content')
    def live_log_content_route():
        log_dir = os.path.join(DATA_DIR, "logs")
        log_path = state_manager.current_download.get("log_path")
        log_content = "No active download or log path is not available."
        if log_path and is_safe_path(log_dir, log_path, allow_file=True):
            try:
                with open(log_path, 'r', encoding='utf-8') as f: log_content = f.read()
            except Exception as e: log_content = f"ERROR: Could not read live log file. Reason: {e}"
        return jsonify({"log": log_content})

    # --- Scythes API Routes ---
    @app.route('/api/scythes', methods=['POST'])
    @permission_required('can_manage_scythes')
    def add_scythe_route():
        data = request.get_json()
        if not data: return jsonify({"error": "Invalid request."}), 400

        if log_id := data.get("log_id"):
            history_item = state_manager.get_history_item_by_log_id(log_id)
            if not history_item or "job_data" not in history_item:
                return jsonify({"error": "Could not find original job data in history."}), 404
            
            scythe_data = {
                "name": history_item.get("title", "Untitled Scythe"),
                "job_data": history_item["job_data"]
            }
            scythe_data["job_data"]["resolved_folder"] = history_item.get("folder")
            
            result, message = scythe_manager.add(scythe_data)
            if result:
                return jsonify({"message": message, "newState": get_current_state()}), 201
            else:
                return jsonify({"error": message}), 409

        elif (job_data := data.get("job_data")) and (name := data.get("name")):
            scythe_data = {"name": name, "job_data": job_data, "schedule": data.get("schedule")}
            result, message = scythe_manager.add(scythe_data)
            if result:
                scheduler._load_and_schedule_jobs()
                return jsonify({"message": message, "newState": get_current_state()}), 201
            else:
                return jsonify({"error": message}), 409
        
        return jsonify({"error": "Invalid payload for creating a Scythe."}), 400

    @app.route('/api/scythes/<int:scythe_id>', methods=['PUT'])
    @permission_required('can_manage_scythes')
    def update_scythe_route(scythe_id):
        data = request.get_json()
        if not data or not data.get("name") or not data.get("job_data"):
            return jsonify({"error": "Invalid payload for updating a Scythe."}), 400
        
        if scythe_manager.update(scythe_id, data):
            scheduler._load_and_schedule_jobs()
            return jsonify({"message": "Scythe updated.", "newState": get_current_state()})
        return jsonify({"error": "Scythe not found."}), 404

    @app.route('/api/scythes/<int:scythe_id>', methods=['DELETE'])
    @permission_required('can_manage_scythes')
    def delete_scythe_route(scythe_id):
        if scythe_manager.delete(scythe_id):
            scheduler._load_and_schedule_jobs()
            return jsonify({"message": "Scythe deleted.", "newState": get_current_state()})
        return jsonify({"error": "Scythe not found."}), 404

    @app.route('/api/scythes/<int:scythe_id>/reap', methods=['POST'])
    @permission_required('can_add_to_queue')
    def reap_scythe_route(scythe_id):
        scythe = scythe_manager.get_by_id(scythe_id)
        if not scythe or not scythe.get("job_data"):
            return jsonify({"error": "Scythe not found or is invalid."}), 404
        
        job_to_reap = scythe["job_data"]
        job_to_reap["resolved_folder"] = job_to_reap.get("folder")

        state_manager.add_to_queue(job_to_reap)
        return jsonify({"message": f"Added '{scythe.get('name')}' to queue.", "newState": get_current_state()})

    @app.route("/api/files")
    def list_files_route():
        base_download_dir = CONFIG.get("download_dir")
        req_path = request.args.get('path', '')
        
        safe_req_path = secure_join(base_download_dir, req_path)
        if not safe_req_path or not is_safe_path(base_download_dir, safe_req_path):
            return jsonify({"error": "Access Denied"}), 403
        
        items = []
        try:
            for name in os.listdir(safe_req_path):
                full_path = os.path.join(safe_req_path, name)
                relative_path = os.path.relpath(full_path, base_download_dir)
                item_data = {"name": name, "path": relative_path.replace("\\", "/")}
                try:
                    stat_info = os.stat(full_path)
                    if os.path.isdir(full_path):
                        item_data.update({"type": "directory", "item_count": len(os.listdir(full_path))})
                    else:
                        item_data.update({"type": "file", "size": stat_info.st_size})
                    items.append(item_data)
                except OSError: continue
        except OSError as e: return jsonify({"error": f"Cannot access directory: {e.strerror}"}), 500
        
        return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

    @app.route("/download_item")
    @permission_required('can_download_files')
    def download_item_route():
        paths = request.args.getlist('paths')
        base_download_dir = CONFIG.get("download_dir")
        if not paths: return "Missing path parameter.", 400
        
        safe_full_paths = []
        for p in paths:
            full_path = secure_join(base_download_dir, p)
            if full_path and is_safe_path(base_download_dir, full_path, allow_file=True) and os.path.exists(full_path):
                safe_full_paths.append(full_path)
        
        if not safe_full_paths: return "No valid files specified or access denied.", 404
        
        if len(safe_full_paths) == 1 and os.path.isfile(safe_full_paths[0]):
            return send_file(safe_full_paths[0], as_attachment=True)
            
        zip_buffer = io.BytesIO()
        zip_name = f"{os.path.basename(safe_full_paths[0]) if len(safe_full_paths) == 1 else 'downloader_selection'}.zip"
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for full_path in safe_full_paths:
                if os.path.isdir(full_path):
                    base_arcname = os.path.basename(full_path)
                    for root, _, files in os.walk(full_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.join(base_arcname, os.path.relpath(file_path, full_path))
                            zip_file.write(file_path, arcname=arcname)
                else:
                    zip_file.write(full_path, arcname=os.path.basename(full_path))
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name=zip_name, mimetype='application/zip')

    @app.route('/api/logs', methods=['GET'])
    @permission_required('admin')
    def list_logs_route():
        log_dir = os.path.join(DATA_DIR, "logs")
        logs = []
        
        startup_log = os.path.join(DATA_DIR, 'startup.log')
        if os.path.exists(startup_log):
            logs.append({"filename": "startup.log", "display_name": "Application Log (startup.log)"})

        job_logs = sorted(glob.glob(os.path.join(log_dir, "job_*.log")), reverse=True)
        for log_path in job_logs:
            filename = os.path.basename(log_path)
            logs.append({"filename": f"logs/{filename}", "display_name": f"Job Log ({filename})"})
            
        return jsonify(logs)

    @app.route('/api/logs/<path:filename>', methods=['GET'])
    @permission_required('admin')
    def get_log_content_route(filename):
        if '..' in filename or filename.startswith('/'):
            return jsonify({"error": "Invalid filename."}), 400
            
        full_path = os.path.join(DATA_DIR, filename)
        
        if not is_safe_path(DATA_DIR, full_path, allow_file=True):
            return jsonify({"error": "Access denied."}), 403
            
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - (1024 * 1024)), os.SEEK_SET)
                content = f.read()
            return jsonify({"content": content})
        except FileNotFoundError:
            return jsonify({"error": "Log file not found."}), 404
        except Exception as e:
            logger.error(f"Error reading log file {filename}: {e}")
            return jsonify({"error": "Could not read log file."}), 500

if __name__ == "__main__":
    try:
        app = create_app()
        host = CONFIG.get("server_host", "0.0.0.0")
        port = CONFIG.get("server_port", 8080)
        
        logger.info("Initialization complete. Starting production server with Waitress...")
        banner_logger.info(f"Server is running at: http://{host}:{port}")
        banner_logger.info("You can now open this address in your web browser.")
        banner_logger.info("Press Ctrl+C in this window to stop the server.")
        banner_logger.info("\n" + "="*(70 + len(f" Starting ContentReaper v{APP_VERSION} ")) + "\n")

        try:
            waitress.serve(app, host=host, port=port, _quiet=True)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received.")
        finally:
            logger.info("Server is shutting down. Signaling threads to stop.")
            STOP_EVENT.set()
            if scheduler:
                scheduler.stop()
            if state_manager:
                state_manager.queue.put(None)
            if WORKER_THREAD:
                logger.info("Waiting for worker thread to finish...")
                WORKER_THREAD.join(timeout=15)
                if WORKER_THREAD.is_alive():
                    logger.warning("Worker thread did not exit gracefully.")
            if SCHEDULER_THREAD:
                logger.info("Waiting for scheduler thread to finish...")
                SCHEDULER_THREAD.join(timeout=5)
            logger.info("Saving final state before exit.")
            if state_manager:
                state_manager.save_state()
            logger.info("Shutdown complete.")
            
    except Exception as e:
        logger.critical("A critical error occurred during the server launch.", exc_info=True)
        if platform.system() == "Windows":
            os.system("pause")
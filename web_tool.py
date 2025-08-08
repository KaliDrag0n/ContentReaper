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

# --- Custom logging filter to create relative paths ---
class RelativePathFilter(logging.Filter):
    def filter(self, record):
        record.relativepath = os.path.relpath(record.pathname, APP_ROOT)
        return True

# --- Set up logging immediately ---
console_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [in %(relativepath)s:%(lineno)d] :: %(message)s')
file_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [in %(pathname)s:%(lineno)d] :: %(message)s')
simple_formatter = logging.Formatter('%(message)s')

log_file = os.path.join(APP_ROOT, 'startup.log')

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
APP_VERSION = "4.3.1"
APP_NAME = "ContentReaper"
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

# --- Global Variables (will be initialized in create_app) ---
state_manager = None
update_status = {"update_available": False, "latest_version": "0.0.0", "release_url": "", "release_notes": ""}
WORKER_THREAD = None
STOP_EVENT = threading.Event()
YT_DLP_PATH = None
FFMPEG_PATH = None
CONFIG = {}
PASSWORD_IS_SET = False

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
    import flask, waitress, requests
    from werkzeug.security import generate_password_hash, check_password_hash
    from flask_wtf.csrf import CSRFProtect, generate_csrf
    from lib import dependency_manager as dm, state_manager as sm, worker, sanitizer
    from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file, session
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

# --- Helper Functions ---

def password_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not PASSWORD_IS_SET or session.get('is_logged_in'):
            return f(*args, **kwargs)
        return jsonify({"error": "Authentication required. Please log in."}), 401
    return decorated_function

def is_safe_path(basedir, path_to_check, allow_file=False):
    real_basedir = os.path.realpath(basedir)
    try:
        real_path_to_check = os.path.realpath(path_to_check)
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
    """Loads configuration from config.json, sets defaults, and validates."""
    global CONFIG, PASSWORD_IS_SET
    
    config_path = os.path.join(APP_ROOT, "config.json")
    
    # Default configuration now includes server host and port
    defaults = {
        "download_dir": os.path.join(APP_ROOT, "downloads"),
        "temp_dir": os.path.join(APP_ROOT, ".temp"),
        "admin_password_hash": None,
        "server_host": "0.0.0.0",
        "server_port": 8080,
        "log_level": "INFO"
    }
    
    CONFIG = defaults.copy()

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                CONFIG.update(json.load(f))
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

    PASSWORD_IS_SET = bool(CONFIG.get("admin_password_hash"))
    logger.info(f"Admin password is {'set' if PASSWORD_IS_SET else 'NOT set.'}")
    
    # Save config to ensure new keys (host/port) are written to the user's file
    save_config()

def save_config():
    """Saves the current configuration to config.json."""
    config_path = os.path.join(APP_ROOT, "config.json")
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

def trigger_update_and_restart():
    logger.info("Update triggered. Saving state and restarting...")
    state_manager.save_state()
    os.execv(sys.executable, [sys.executable] + sys.argv)

# --- Application Factory ---

def create_app():
    global state_manager, WORKER_THREAD, YT_DLP_PATH, FFMPEG_PATH
    print_banner()
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

    logger.info("--- [4/4] Loading state and starting background threads ---")
    log_dir = os.path.join(APP_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    state_file = os.path.join(APP_ROOT, "state.json")
    state_manager = sm.StateManager(state_file)
    
    for log_path in glob.glob(os.path.join(log_dir, "job_active_*.log")):
        try: os.remove(log_path)
        except OSError as e: logger.error(f"Failed to remove stale active log file {log_path}: {e}")
        
    state_manager.load_state()
    threading.Thread(target=scheduled_update_check, daemon=True).start()
    
    cookie_file = os.path.join(APP_ROOT, "cookies.txt")
    WORKER_THREAD = threading.Thread(target=worker.yt_dlp_worker, args=(state_manager, CONFIG, log_dir, cookie_file, YT_DLP_PATH, FFMPEG_PATH, STOP_EVENT))
    WORKER_THREAD.start()
    
    logger.info("--- Application Initialized Successfully ---")
    register_routes(app)
    return app

def register_routes(app):
    @app.context_processor
    def inject_globals():
        return dict(app_name=APP_NAME, app_version=APP_VERSION)

    def get_current_state():
        with state_manager._lock:
            return {
                "queue": state_manager.get_queue_list(),
                "current": state_manager.current_download if state_manager.current_download.get("url") else None,
                "history": state_manager.get_history_summary(),
                "is_paused": not state_manager.queue_paused_event.is_set()
            }

    @app.route("/")
    def index_route():
        return render_template("index.html")

    @app.route("/file_manager")
    def file_manager_route():
        return render_template("file_manager.html")

    @app.route("/settings")
    def settings_route():
        with state_manager._lock:
            current_update_status = update_status.copy()
        return render_template("settings.html", update_info=current_update_status)
    
    @app.route("/api/status")
    def status_poll_route():
        return jsonify(get_current_state())

    @app.route("/api/update_check")
    def update_check_route():
        with state_manager._lock:
            return jsonify(update_status)

    def _parse_job_data(form_data):
        mode = form_data.get("download_mode")
        if not mode: raise ValueError("Download mode not specified.")
        job_base = {
            "mode": mode,
            "folder": form_data.get(f"{mode}_foldername", "").strip(),
            "archive": form_data.get("use_archive") == "yes",
            "proxy": form_data.get("proxy", "").strip(),
            "rate_limit": form_data.get("rate_limit", "").strip()
        }
        try:
            p_start = form_data.get("playlist_start", "").strip()
            p_end = form_data.get("playlist_end", "").strip()
            job_base["playlist_start"] = int(p_start) if p_start else None
            job_base["playlist_end"] = int(p_end) if p_end else None
        except ValueError: raise ValueError("Playlist start/end must be a number.")
        
        if mode == 'music':
            job_base.update({"format": form_data.get("music_audio_format"), "quality": form_data.get("music_audio_quality")})
        elif mode == 'video':
            job_base.update({"quality": form_data.get("video_quality"), "format": form_data.get("video_format"), "embed_subs": form_data.get("video_embed_subs") == "on", "codec": form_data.get("video_codec_preference")})
        elif mode == 'clip':
            job_base.update({"format": form_data.get("clip_format")})
        elif mode == 'custom':
            job_base.update({"custom_args": form_data.get("custom_args")})
        return job_base

    @app.route("/queue", methods=["POST"])
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
    @password_required
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
        return jsonify({"password_set": PASSWORD_IS_SET, "logged_in": session.get('is_logged_in', False)})
    
    @app.route('/api/auth/csrf-token')
    def get_csrf_token_route():
        return jsonify({"csrf_token": generate_csrf()})

    @app.route('/api/auth/login', methods=['POST'])
    def login_route():
        if not PASSWORD_IS_SET:
            return jsonify({"error": "No password is set on the server."}), 400
        password = (request.get_json() or {}).get('password')
        if check_password_hash(CONFIG['admin_password_hash'], password):
            session['is_logged_in'] = True
            return jsonify({"message": "Login successful."})
        return jsonify({"error": "Invalid password."}), 401

    @app.route('/api/auth/logout', methods=['POST'])
    def logout_route():
        session.pop('is_logged_in', None)
        return jsonify({"message": "Logged out."})

    @app.route('/api/auth/set-password', methods=['POST'])
    @password_required
    def set_password_route():
        global PASSWORD_IS_SET
        data = request.get_json()
        if PASSWORD_IS_SET and not check_password_hash(CONFIG['admin_password_hash'], data.get('current_password')):
            return jsonify({"error": "Current password is incorrect."}), 403
        
        if new_password := data.get('new_password'):
            CONFIG['admin_password_hash'] = generate_password_hash(new_password)
            PASSWORD_IS_SET, message = True, "Password updated successfully."
        else:
            CONFIG['admin_password_hash'] = None
            PASSWORD_IS_SET, message = False, "Password has been removed."
        
        save_config()
        session['is_logged_in'] = True
        return jsonify({"message": message})
        
    @app.route('/api/settings', methods=['GET', 'POST'])
    @password_required
    def api_settings_route():
        if request.method == 'POST':
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid request body."}), 400

            CONFIG["download_dir"] = data.get("download_dir", CONFIG["download_dir"]).strip()
            CONFIG["temp_dir"] = data.get("temp_dir", CONFIG["temp_dir"]).strip()
            CONFIG["log_level"] = data.get("log_level", CONFIG["log_level"]).strip().upper()
            CONFIG["server_host"] = data.get("server_host", CONFIG["server_host"]).strip()
            try:
                CONFIG["server_port"] = int(data.get("server_port", CONFIG["server_port"]))
            except (ValueError, TypeError):
                logger.warning(f"Invalid server_port value received: {data.get('server_port')}. Retaining existing value.")

            save_config()
            
            cookie_file = os.path.join(APP_ROOT, "cookies.txt")
            try:
                with open(cookie_file, 'w', encoding='utf-8') as f:
                    f.write(data.get("cookie_content", ""))
            except Exception as e:
                logger.error(f"Failed to write to cookie file: {e}")
                return jsonify({"error": "Failed to save cookie file."}), 500
            
            logger.info("Settings saved via API. Host/port/log level changes will apply on next restart.")
            return jsonify({"message": "Settings saved successfully. Restart required for some changes."})

        cookie_file = os.path.join(APP_ROOT, "cookies.txt")
        cookie_content = ""
        try:
            if os.path.exists(cookie_file):
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    cookie_content = f.read()
        except Exception as e:
            logger.error(f"Could not read cookie file for API: {e}")

        return jsonify({
            "config": {k: v for k, v in CONFIG.items() if k != 'admin_password_hash'},
            "cookies": cookie_content
        })

    @app.route("/api/stop", methods=['POST'])
    @password_required
    def stop_route():
        mode = (request.get_json() or {}).get('mode', 'cancel').upper()
        state_manager.stop_mode = "SAVE" if mode == 'SAVE' else "CANCEL"
        state_manager.cancel_event.set()
        return jsonify({"message": f"{state_manager.stop_mode.capitalize()} signal sent."})

    @app.route('/queue/clear', methods=['POST'])
    @password_required
    def clear_queue_route():
        state_manager.clear_queue()
        return jsonify({"message": "Queue cleared.", "newState": get_current_state()})

    @app.route('/history/clear', methods=['POST'])
    @password_required
    def clear_history_route():
        log_dir = os.path.join(APP_ROOT, "logs")
        for path in state_manager.clear_history():
            if is_safe_path(log_dir, path, allow_file=True):
                try: os.remove(path)
                except Exception as e: logger.error(f"Could not delete log file {path}: {e}")
        return jsonify({"message": "History cleared.", "newState": get_current_state()})

    @app.route("/api/delete_item", methods=['POST'])
    @password_required
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
    @password_required
    def force_update_check_route():
        _run_update_check()
        return jsonify({"message": "Update check completed."})

    @app.route('/api/shutdown', methods=['POST'])
    @password_required
    def shutdown_route():
        logger.info("Shutdown requested via API.")
        STOP_EVENT.set()
        threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
        return jsonify({"message": "Server is shutting down."})

    @app.route('/api/install_update', methods=['POST'])
    @password_required
    def install_update_route():
        threading.Thread(target=trigger_update_and_restart).start()
        return jsonify({"message": "Update process initiated."})

    @app.route('/queue/delete/by-id/<int:job_id>', methods=['POST'])
    @password_required
    def delete_from_queue_route(job_id):
        state_manager.delete_from_queue(job_id)
        return jsonify({"message": "Queue item removed.", "newState": get_current_state()})

    @app.route('/queue/reorder', methods=['POST'])
    @password_required
    def reorder_queue_route():
        data = request.get_json()
        try:
            ordered_ids = [int(i) for i in data.get('order', [])]
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid job IDs provided."}), 400
        state_manager.reorder_queue(ordered_ids)
        return jsonify({"message": "Queue reordered.", "newState": get_current_state()})

    @app.route('/queue/pause', methods=['POST'])
    @password_required
    def pause_queue_route():
        state_manager.pause_queue()
        return jsonify({"message": "Queue paused.", "newState": get_current_state()})

    @app.route('/queue/resume', methods=['POST'])
    @password_required
    def resume_queue_route():
        state_manager.resume_queue()
        return jsonify({"message": "Queue resumed.", "newState": get_current_state()})

    @app.route('/history/delete/<int:log_id>', methods=['POST'])
    @password_required
    def delete_from_history_route(log_id):
        log_dir = os.path.join(APP_ROOT, "logs")
        path_to_delete = state_manager.delete_from_history(log_id)
        if path_to_delete and is_safe_path(log_dir, path_to_delete, allow_file=True):
            try: os.remove(path_to_delete)
            except Exception as e: logger.error(f"Could not delete log file {path_to_delete}: {e}")
        return jsonify({"message": "History item deleted.", "newState": get_current_state()})

    @app.route('/api/history/item/<int:log_id>')
    @password_required
    def get_history_item_route(log_id):
        item = state_manager.get_history_item_by_log_id(log_id)
        return jsonify(item) if item else (jsonify({"error": "History item not found."}), 404)

    @app.route('/history/log/<int:log_id>')
    @password_required
    def history_log_route(log_id):
        item = state_manager.get_history_item_by_log_id(log_id)
        if not item: return jsonify({"log": "Log not found for the given ID."}), 404
        
        log_dir = os.path.join(APP_ROOT, "logs")
        log_path = item.get("log_path")
        log_content = "Log not found on disk or could not be read."
        
        if log_path and log_path != "LOG_SAVE_ERROR" and is_safe_path(log_dir, log_path, allow_file=True):
            try:
                with open(log_path, 'r', encoding='utf-8') as f: log_content = f.read()
            except Exception as e: log_content = f"ERROR: Could not read log file. Reason: {e}"
        elif log_path == "LOG_SAVE_ERROR": log_content = "There was an error saving the log file for this job."
        return jsonify({"log": log_content})

    @app.route('/api/log/live/content')
    def live_log_content_route():
        log_dir = os.path.join(APP_ROOT, "logs")
        log_path = state_manager.current_download.get("log_path")
        log_content = "No active download or log path is not available."
        if log_path and is_safe_path(log_dir, log_path, allow_file=True):
            try:
                with open(log_path, 'r', encoding='utf-8') as f: log_content = f.read()
            except Exception as e: log_content = f"ERROR: Could not read live log file. Reason: {e}"
        return jsonify({"log": log_content})

    @app.route("/api/files")
    @password_required
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
    @password_required
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

if __name__ == "__main__":
    try:
        app = create_app()
        # Read host and port from the loaded CONFIG dictionary
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
            if state_manager:
                state_manager.queue.put(None)
            if WORKER_THREAD:
                logger.info("Waiting for worker thread to finish...")
                WORKER_THREAD.join(timeout=15)
                if WORKER_THREAD.is_alive():
                    logger.warning("Worker thread did not exit gracefully.")
            logger.info("Saving final state before exit.")
            if state_manager:
                state_manager.save_state()
            logger.info("Shutdown complete.")
            
    except Exception as e:
        logger.critical("A critical error occurred during the server launch.", exc_info=True)
        if platform.system() == "Windows":
            os.system("pause")

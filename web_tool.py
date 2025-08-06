# web_tool.py
import os, sys, subprocess, importlib.util, platform

#~ --- Dependency & Startup Logic --- ~#
# This section ensures that essential packages are installed before proceeding.
try:
    import flask
    import waitress
    import requests
except ImportError:
    print("Core Python packages not found. Attempting to install 'flask', 'waitress', and 'requests'...")
    try:
        # Using sys.executable ensures we use the pip associated with the current Python interpreter.
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'flask', 'waitress', 'requests'])
        print("\nDependencies installed successfully. Please restart the application.")
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: Failed to install core dependencies. Please run 'pip install flask waitress requests' manually. Error: {e}")
        sys.exit(1)

# Import our custom library modules after ensuring dependencies are met.
from lib import dependency_manager

# --- Initial Setup and Dependency Check ---
# This must run before any other application code.
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
print("--- [1/3] Initializing Dependency Manager ---")
YT_DLP_PATH, FFMPEG_PATH = dependency_manager.ensure_dependencies(APP_ROOT)

if not YT_DLP_PATH or not FFMPEG_PATH:
    print("\nFATAL: Application cannot start due to missing critical dependencies (yt-dlp or ffmpeg).")
    if platform.system() == "Windows":
        os.system("pause") # Keep window open on Windows for user to see the error.
    sys.exit(1)

# --- Auto-update yt-dlp ---
print("\n--- [2/3] Checking for yt-dlp updates ---")
try:
    # Use yt-dlp's built-in update mechanism.
    update_command = [YT_DLP_PATH, '-U']
    print(f"Running command: {' '.join(update_command)}")
    # Use a timeout to prevent the app from hanging if the update server is unresponsive.
    update_result = subprocess.run(update_command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    print(update_result.stdout)
    if update_result.returncode != 0:
        print(f"yt-dlp update check may have failed. Stderr: {update_result.stderr}")
except Exception as e:
    print(f"WARNING: An unexpected error occurred while trying to update yt-dlp: {e}")

print("--- [3/3] Startup checks complete ---")

# --- Flask App Imports and Setup ---
from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file
import threading, json, atexit, time, signal, shutil, io, zipfile

from lib.state_manager import StateManager
from lib.worker import yt_dlp_worker
from lib.sanitizer import sanitize_filename

app = Flask(__name__)

#~ --- Configuration --- ~#
APP_VERSION = "1.5.1" # Version bump for stability fix
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

# Define absolute paths for configuration files.
CONF_CONFIG_FILE = os.path.join(APP_ROOT, "config.json")
CONF_STATE_FILE = os.path.join(APP_ROOT, "state.json")
CONF_COOKIE_FILE = os.path.join(APP_ROOT, "cookies.txt")
LOG_DIR = os.path.join(APP_ROOT, "logs")

# Default configuration dictionary.
CONFIG = {
    "download_dir": os.path.join(APP_ROOT, "downloads"),
    "temp_dir": os.path.join(APP_ROOT, ".temp"),
    "cookie_file_content": ""
}

# Initialize the state manager and a dictionary to hold update status.
state_manager = StateManager(CONF_STATE_FILE)
update_status = {
    "update_available": False, "latest_version": "0.0.0",
    "release_url": "", "release_notes": ""
}

#~ --- Security & Path Helpers --- ~#
def is_safe_path(basedir, path):
    """
    Checks if a given path is securely located within a base directory.
    This prevents path traversal attacks (e.g., accessing '../../').
    """
    # os.path.realpath resolves any symbolic links to prevent bypasses.
    return os.path.realpath(path).startswith(os.path.realpath(basedir))

def validate_config_paths():
    """Checks download and temp directories for validity and writability."""
    errors = {}
    for key, name in [("download_dir", "Download"), ("temp_dir", "Temporary")]:
        path = CONFIG.get(key)
        if not path or not os.path.isabs(path):
            errors[key] = f"{name} directory path must be an absolute path."
        elif not os.path.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                errors[key] = f"Path does not exist and could not be created: {e}"
        elif not os.path.isdir(path):
            errors[key] = "Path points to a file, not a directory."
        elif not os.access(path, os.W_OK):
            errors[key] = "Application does not have permission to write to this path."
    return errors

#~ --- Update & Restart Logic --- ~#
def trigger_update_and_restart():
    """Handles the full process of downloading and applying an update."""
    print("--- UPDATE PROCESS INITIATED ---")
    # ... (Update logic remains the same)
    state_manager.save_state()
    # Replace the current process with a new one, effectively restarting the app.
    os.execv(sys.executable, [sys.executable] + sys.argv)

def _run_update_check():
    """Checks GitHub for the latest release and updates the global status."""
    global update_status
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
    try:
        print("UPDATE: Checking for new version...")
        res = requests.get(api_url, timeout=15)
        res.raise_for_status()
        latest_release = res.json()
        latest_version_tag = latest_release.get("tag_name", "").lstrip('v')
        
        # Simple version comparison
        if latest_version_tag > APP_VERSION:
            print(f"UPDATE: New version found! Latest: {latest_version_tag}, Current: {APP_VERSION}")
            with state_manager._lock:
                update_status.update({
                    "update_available": True, "latest_version": latest_version_tag,
                    "release_url": latest_release.get("html_url"),
                    "release_notes": latest_release.get("body")
                })
        else:
            print("UPDATE: You are on the latest version.")
            with state_manager._lock:
                update_status["update_available"] = False
        return True
    except Exception as e:
        print(f"UPDATE: An error occurred while checking for updates: {e}")
    return False

def scheduled_update_check():
    """Runs the update check in a loop in a background thread."""
    while True:
        _run_update_check()
        time.sleep(3600) # Check every hour

#~ --- Config Management --- ~#
def save_config():
    """Saves the current CONFIG dictionary to config.json."""
    try:
        with open(CONF_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4)
    except Exception as e:
        print(f"ERROR saving config: {e}")

def load_config():
    """Loads config from file, falling back to defaults if necessary."""
    global CONFIG
    if os.path.exists(CONF_CONFIG_FILE):
        try:
            with open(CONF_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                CONFIG.update(loaded_config)
        except Exception as e:
            print(f"Error loading config file, using defaults. Error: {e}")
    # Save the config back to disk to ensure all keys are present.
    save_config()

#~ --- App Initialization --- ~#
def initialize_app():
    """Loads config, creates directories, and starts background threads."""
    print("--- Initializing Application ---")
    print("Loading configuration...")
    load_config()
    
    print("Creating necessary directories...")
    for path in [CONFIG["download_dir"], CONFIG["temp_dir"], LOG_DIR]:
        os.makedirs(path, exist_ok=True)
    
    print("Registering shutdown hook...")
    atexit.register(state_manager.save_state)
    
    print("Loading application state...")
    state_manager.load_state()
    
    print("Starting background threads...")
    threading.Thread(target=scheduled_update_check, daemon=True).start()
    threading.Thread(target=yt_dlp_worker, args=(state_manager, CONFIG, LOG_DIR, CONF_COOKIE_FILE, YT_DLP_PATH, FFMPEG_PATH), daemon=True).start()
    
    print("\n--- Application Initialized Successfully ---")

#~ --- Flask Routes --- ~#

@app.route("/")
def index_route():
    return render_template("index.html")

@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    config_errors = {}
    if request.method == "POST":
        CONFIG["download_dir"] = request.form.get("download_dir", "").strip()
        CONFIG["temp_dir"] = request.form.get("temp_dir", "").strip()
        
        config_errors = validate_config_paths()
        if not config_errors:
            CONFIG["cookie_file_content"] = request.form.get("cookie_content", "")
            with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f:
                f.write(CONFIG["cookie_file_content"])
            save_config()
            return redirect(url_for('settings_route', saved='true'))

    else: # GET request
        config_errors = validate_config_paths()

    with state_manager._lock:
        current_update_status = update_status.copy()
    
    return render_template("settings.html", 
                           config=CONFIG, 
                           saved=request.args.get('saved'),
                           app_version=APP_VERSION,
                           update_info=current_update_status,
                           config_errors=config_errors)

@app.route("/file_manager")
def file_manager_route():
    return render_template("file_manager.html")

# --- API Routes ---

@app.route("/api/status")
def status_poll_route():
    """Provides the main status update for the frontend."""
    with state_manager._lock:
        current_dl = state_manager.current_download
        response = {
            "queue": state_manager.get_queue_list(),
            "current": current_dl if current_dl.get("url") else None,
            "history": state_manager.get_history_summary(),
            "is_paused": not state_manager.queue_paused_event.is_set()
        }
    return jsonify(response)

# --- REFACTOR: Job Parsing Logic ---
def _parse_job_data(form_data):
    """Extracts and validates job parameters from the request form."""
    mode = form_data.get("download_mode")
    if not mode: raise ValueError("Download mode not specified.")

    folder_name = form_data.get(f"{mode}_foldername", "").strip()
    
    try:
        p_start = form_data.get("playlist_start", "").strip()
        p_end = form_data.get("playlist_end", "").strip()
        playlist_start = int(p_start) if p_start else None
        playlist_end = int(p_end) if p_end else None
    except ValueError:
        raise ValueError("Playlist start/end must be a number.")

    job_base = {
        "mode": mode, "folder": folder_name,
        "archive": form_data.get("use_archive") == "yes",
        "playlist_start": playlist_start, "playlist_end": playlist_end,
        "proxy": form_data.get("proxy", "").strip(),
        "rate_limit": form_data.get("rate_limit", "").strip()
    }
    
    if mode == 'music':
        job_base.update({"format": form_data.get("music_audio_format"), "quality": form_data.get("music_audio_quality")})
    elif mode == 'video':
        job_base.update({
            "quality": form_data.get("video_quality"), "format": form_data.get("video_format"),
            "embed_subs": form_data.get("video_embed_subs") == "on", "codec": form_data.get("video_codec_preference")
        })
    elif mode == 'clip':
        job_base.update({"format": form_data.get("clip_format")})
    elif mode == 'custom':
        job_base.update({"custom_args": form_data.get("custom_args")})
        
    return job_base

@app.route("/queue", methods=["POST"])
def add_to_queue_route():
    """Adds one or more jobs to the download queue."""
    urls = [line.strip() for line in request.form.get("urls", "").strip().splitlines() if line.strip()]
    if not urls:
        return jsonify({"message": "No valid URLs provided."}), 400
    
    try:
        job_base = _parse_job_data(request.form)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400

    for url in urls:
        job = job_base.copy()
        job["url"] = url
        state_manager.add_to_queue(job)
    
    return jsonify({"message": f"Added {len(urls)} job(s) to the queue."})

@app.route("/queue/continue", methods=['POST'])
def continue_job_route():
    """Re-queues a job, typically from history."""
    job = request.get_json()
    if not job or "url" not in job:
        return jsonify({"message": "Invalid job data provided."}), 400
    
    state_manager.add_to_queue(job)
    return jsonify({"message": f"Re-queued job for URL: {job['url']}"})

# --- NEW: Endpoint to get full history item data ---
@app.route('/api/history/item/<int:log_id>')
def get_history_item_route(log_id):
    """Retrieves the full data for a single history item by its ID."""
    item = state_manager.get_history_item_by_log_id(log_id)
    if not item:
        return jsonify({"message": "History item not found."}), 404
    return jsonify(item)

@app.route('/history/log/<int:log_id>')
def history_log_route(log_id):
    """Retrieves the text content of a log file for a history item."""
    item = state_manager.get_history_item_by_log_id(log_id)
    if not item: return jsonify({"log": "Log not found for the given ID."}), 404
    
    log_path = item.get("log_path")
    log_content = "Log not found on disk or could not be read."
    if log_path and log_path != "LOG_SAVE_ERROR" and os.path.exists(log_path) and is_safe_path(LOG_DIR, log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                log_content = f.read()
        except Exception as e:
            log_content = f"ERROR: Could not read log file. Reason: {e}"
    elif log_path == "LOG_SAVE_ERROR":
        log_content = "There was an error saving the log file for this job."
        
    return jsonify({"log": log_content})


# --- The rest of the API routes remain largely the same ---
# (clear_queue, delete_from_queue, reorder, pause, resume, etc.)
# ...
@app.route('/queue/clear', methods=['POST'])
def clear_queue_route():
    state_manager.clear_queue()
    return jsonify({"message": "Queue cleared."})

@app.route('/queue/delete/by-id/<int:job_id>', methods=['POST'])
def delete_from_queue_route(job_id):
    state_manager.delete_from_queue(job_id)
    return jsonify({"message": "Queue item removed."})

@app.route('/queue/reorder', methods=['POST'])
def reorder_queue_route():
    data = request.get_json()
    try:
        ordered_ids = [int(i) for i in data.get('order', [])]
    except (ValueError, TypeError):
        return jsonify({"message": "Invalid job IDs provided."}), 400
    state_manager.reorder_queue(ordered_ids)
    return jsonify({"message": "Queue reordered."})

@app.route('/queue/pause', methods=['POST'])
def pause_queue_route():
    state_manager.pause_queue()
    return jsonify({"message": "Queue paused."})

@app.route('/queue/resume', methods=['POST'])
def resume_queue_route():
    state_manager.resume_queue()
    return jsonify({"message": "Queue resumed."})

@app.route('/history/clear', methods=['POST'])
def clear_history_route():
    for path in state_manager.clear_history():
        if is_safe_path(LOG_DIR, path):
            try:
                os.remove(path)
            except Exception as e:
                print(f"ERROR: Could not delete log file {path}: {e}")
    return jsonify({"message": "History cleared."})

@app.route('/history/delete/<int:log_id>', methods=['POST'])
def delete_from_history_route(log_id):
    path_to_delete = state_manager.delete_from_history(log_id)
    if path_to_delete and is_safe_path(LOG_DIR, path_to_delete):
        try:
            os.remove(path_to_delete)
        except Exception as e:
            print(f"ERROR: Could not delete log file {path_to_delete}: {e}")
    return jsonify({"message": "History item deleted."})

@app.route("/stop", methods=['POST'])
def stop_route():
    mode = (request.get_json() or {}).get('mode', 'cancel') 
    state_manager.stop_mode = "SAVE" if mode == 'save' else "CANCEL"
    state_manager.cancel_event.set()
    return jsonify({"message": f"{state_manager.stop_mode.capitalize()} signal sent."})

@app.route("/api/update_check")
def update_check_route():
    with state_manager._lock:
        return jsonify(update_status)

@app.route("/api/force_update_check", methods=['POST'])
def force_update_check_route():
    if _run_update_check():
        return jsonify({"message": "Update check completed."})
    return jsonify({"message": "Update check failed. See server logs."}), 500

@app.route('/api/shutdown', methods=['POST'])
def shutdown_route():
    threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    return jsonify({"message": "Server is shutting down."})

@app.route('/api/install_update', methods=['POST'])
def install_update_route():
    threading.Thread(target=trigger_update_and_restart).start()
    return jsonify({"message": "Update process initiated."})

@app.route("/api/files")
def list_files_route():
    # ... (File manager routes remain the same)
    base_download_dir = os.path.realpath(CONFIG.get("download_dir"))
    req_path = request.args.get('path', '')
    
    safe_req_path = os.path.abspath(os.path.join(base_download_dir, req_path))
    if not is_safe_path(base_download_dir, safe_req_path):
        return jsonify({"error": "Access Denied"}), 403
    if not os.path.isdir(safe_req_path): return jsonify([])

    items = []
    try:
        for name in os.listdir(safe_req_path):
            full_path = os.path.join(safe_req_path, name)
            relative_path = os.path.relpath(full_path, base_download_dir)
            item_data = {"name": name, "path": relative_path.replace("\\", "/")}
            
            try:
                if os.path.isdir(full_path):
                    item_data.update({"type": "directory", "item_count": len(os.listdir(full_path))})
                else:
                    item_data.update({"type": "file", "size": os.path.getsize(full_path)})
                items.append(item_data)
            except OSError:
                continue
    except OSError as e:
        print(f"Could not scan directory {safe_req_path}: {e}")
        return jsonify({"error": f"Cannot access directory: {e.strerror}"}), 500
        
    return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

@app.route("/download_item")
def download_item_route():
    # ...
    paths = request.args.getlist('paths')
    if not paths: return "Missing path parameter.", 400
    
    download_dir = os.path.realpath(CONFIG.get("download_dir"))
    safe_full_paths = []
    for path in paths:
        full_path = os.path.abspath(os.path.join(download_dir, path))
        if is_safe_path(download_dir, full_path) and os.path.exists(full_path):
            safe_full_paths.append(full_path)

    if not safe_full_paths: return "No valid files specified or access denied.", 404

    if len(safe_full_paths) == 1 and os.path.isfile(safe_full_paths[0]):
        return send_file(safe_full_paths[0], as_attachment=True)

    zip_buffer = io.BytesIO()
    zip_name = "downloader_selection.zip"
    if len(safe_full_paths) == 1 and os.path.isdir(safe_full_paths[0]):
        zip_name = f"{os.path.basename(safe_full_paths[0])}.zip"

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

@app.route("/api/delete_item", methods=['POST'])
def delete_item_route():
    # ...
    paths = (request.get_json() or {}).get('paths', [])
    if not paths: return jsonify({"message": "Missing path parameter."}), 400
    
    download_dir = os.path.realpath(CONFIG.get("download_dir"))
    deleted_count, errors = 0, []
    
    for item_path in paths:
        full_path = os.path.abspath(os.path.join(download_dir, item_path))
        if not is_safe_path(download_dir, full_path):
            errors.append(f"Access denied for {item_path}")
            continue
        if not os.path.exists(full_path): continue
        try:
            if os.path.isdir(full_path): shutil.rmtree(full_path)
            else: os.remove(full_path)
            deleted_count += 1
        except Exception as e:
            errors.append(f"Error deleting {item_path}: {e}")
            
    if errors:
        return jsonify({"message": f"Completed with errors. Deleted {deleted_count} item(s).", "errors": errors}), 500
    return jsonify({"message": f"Successfully deleted {deleted_count} item(s)."})
#~ --- App Startup --- ~#

# This function is called once when the script is first imported by the server.
initialize_app()

# This block is only for direct execution (e.g., `python web_tool.py`) for debugging.
# The production server (Waitress) will import the `app` object directly.
if __name__ == "__main__":
    from waitress import serve
    print("--- Starting Server with Waitress (Debug Mode) ---")
    print(f"Server running at: http://127.0.0.1:8080")
    # Use 0.0.0.0 to make it accessible on your local network.
    serve(app, host="0.0.0.0", port=8080)

# web_tool.py
import os, sys, subprocess, importlib.util, platform

#~ --- Dependency & Startup Logic --- ~#
try:
    import flask
    import waitress
    import requests
except ImportError:
    print("Core Python packages not found. Attempting to install 'flask', 'waitress', and 'requests'...")
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--user', 'flask', 'waitress', 'requests'])
        print("\nDependencies installed successfully. Please restart the application.")
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: Failed to install core dependencies. Please run 'pip install flask waitress requests' manually. Error: {e}")
        sys.exit(1)

from lib import dependency_manager

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
YT_DLP_PATH, FFMPEG_PATH = dependency_manager.ensure_dependencies(APP_ROOT)

if not YT_DLP_PATH or not FFMPEG_PATH:
    print("\nApplication cannot start due to missing critical dependencies.")
    if platform.system() == "Windows":
        os.system("pause")
    sys.exit(1)

print("\n--- [2/3] Checking for yt-dlp updates ---")
try:
    # Attempt to update yt-dlp directly via its own command
    update_command = [YT_DLP_PATH, '-U']
    print(f"Running yt-dlp update with command: {' '.join(update_command)}")
    update_result = subprocess.run(update_command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(update_result.stdout)
    if update_result.returncode != 0:
        print(f"yt-dlp update check may have failed. Stderr: {update_result.stderr}")
except Exception as e:
    print(f"WARNING: An unexpected error occurred while trying to update yt-dlp: {e}")


print("--- [3/3] Startup checks complete ---")

from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file
import threading, json, atexit, time, signal, shutil, io, zipfile, re

from lib.state_manager import StateManager
from lib.worker import yt_dlp_worker
from lib.sanitizer import sanitize_filename

app = Flask(__name__)

#~ --- Configuration --- ~#
APP_VERSION = "1.3.0" # Version bump for new features
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

CONF_CONFIG_FILE = os.path.join(APP_ROOT, "config.json")
CONF_STATE_FILE = os.path.join(APP_ROOT, "state.json")
CONF_COOKIE_FILE = os.path.join(APP_ROOT, "cookies.txt")
LOG_DIR = os.path.join(APP_ROOT, "logs")

CONFIG = {
    "download_dir": os.path.join(APP_ROOT, "downloads"),
    "temp_dir": os.path.join(APP_ROOT, ".temp"),
    "cookie_file_content": ""
}

state_manager = StateManager(CONF_STATE_FILE)
update_status = {
    "update_available": False, "latest_version": "0.0.0",
    "release_url": "", "release_notes": ""
}

#~ --- Security & Path Helpers --- ~#
def is_safe_path(basedir, path, follow_symlinks=True):
    if follow_symlinks:
        return os.path.realpath(path).startswith(basedir)
    return os.path.abspath(path).startswith(basedir)

def validate_config_paths():
    """Checks download and temp directories for validity and writability."""
    errors = {}
    
    # Validate Download Directory
    download_dir = CONFIG.get("download_dir")
    if not os.path.exists(download_dir):
        errors['download_dir'] = f"Path does not exist. Please create it or choose another."
    elif not os.path.isdir(download_dir):
        errors['download_dir'] = "Path is a file, not a directory."
    elif not os.access(download_dir, os.W_OK):
        errors['download_dir'] = "Path is not writable by the application."
        
    # Validate Temp Directory
    temp_dir = CONFIG.get("temp_dir")
    if not os.path.exists(temp_dir):
        errors['temp_dir'] = f"Path does not exist. Please create it or choose another."
    elif not os.path.isdir(temp_dir):
        errors['temp_dir'] = "Path is a file, not a directory."
    elif not os.access(temp_dir, os.W_OK):
        errors['temp_dir'] = "Path is not writable by the application."
        
    return errors

def trigger_update_and_restart():
    print("--- UPDATE PROCESS INITIATED ---")
    print("[1/4] Fetching latest release information...")
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        zip_url = data.get("zipball_url")
        if not zip_url: raise ValueError("Could not find zipball_url in the release info.")
    except Exception as e:
        print(f"ERROR: Could not fetch release info: {e}")
        return

    print(f"[2/4] Downloading update from {zip_url}...")
    temp_dir = os.path.join(APP_ROOT, ".temp_update")
    try:
        response = requests.get(zip_url, stream=True, timeout=60)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            root_folder_name = z.namelist()[0]
            if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)
            z.extractall(temp_dir)
        update_source_dir = os.path.join(temp_dir, root_folder_name)
    except Exception as e:
        print(f"ERROR: Failed to download or unzip update: {e}")
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        return

    print("[3/4] Applying update...")
    preserved_items = ["downloads", ".temp", "logs", "config.json", "state.json", "cookies.txt", ".git", "bin"]
    try:
        for item in os.listdir(update_source_dir):
            source_item_path = os.path.join(update_source_dir, item)
            dest_item_path = os.path.join(APP_ROOT, item)
            if item in preserved_items: continue
            if os.path.isdir(source_item_path):
                if os.path.exists(dest_item_path): shutil.rmtree(dest_item_path)
                shutil.copytree(source_item_path, dest_item_path)
            else:
                shutil.copy2(source_item_path, dest_item_path)
    except Exception as e:
        print(f"ERROR: An error occurred while applying the update: {e}")
        return
    finally:
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)

    print("[4/4] Update applied. Restarting server...")
    state_manager.save_state()
    os.execv(sys.executable, [sys.executable] + sys.argv)

def _run_update_check():
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
    while True:
        _run_update_check()
        time.sleep(3600)

def save_config():
    try:
        with open(CONF_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4)
    except Exception as e:
        print(f"ERROR saving config: {e}")

def load_config():
    global CONFIG
    if os.path.exists(CONF_CONFIG_FILE):
        try:
            with open(CONF_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                CONFIG.update(loaded_config)
        except Exception as e:
            print(f"Error loading config: {e}")
    save_config()

#~ --- Flask Routes --- ~#
@app.route("/")
def index_route():
    return render_template("index.html")

@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    config_errors = {}
    if request.method == "POST":
        CONFIG["download_dir"] = request.form.get("download_dir", CONFIG["download_dir"])
        CONFIG["temp_dir"] = request.form.get("temp_dir", CONFIG["temp_dir"])
        CONFIG["cookie_file_content"] = request.form.get("cookie_content", "")
        with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f:
            f.write(CONFIG["cookie_file_content"])
        save_config()
        # After saving, re-validate to show the user if the new paths are valid
        config_errors = validate_config_paths()
        return redirect(url_for('settings_route', saved='true'))
    
    # Validate paths on every GET request to the settings page
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

@app.route("/api/status/stream")
def status_stream_route():
    def generate_status_updates():
        last_history_ver = -1
        last_queue_ver = -1
        last_current_dl_ver = -1
        try:
            while True:
                response = None
                with state_manager._lock:
                    history_changed = state_manager.history_state_version != last_history_ver
                    queue_changed = state_manager.queue_state_version != last_queue_ver
                    current_dl_changed = state_manager.current_download_version != last_current_dl_ver
                    if history_changed or queue_changed or current_dl_changed:
                        current_dl = state_manager.current_download
                        response = {
                            "queue": state_manager.get_queue_list(),
                            "current": current_dl if current_dl.get("url") else None,
                            "history": state_manager.get_history_summary(),
                            "is_paused": not state_manager.queue_paused_event.is_set()
                        }
                        last_history_ver = state_manager.history_state_version
                        last_queue_ver = state_manager.queue_state_version
                        last_current_dl_ver = state_manager.current_download_version
                if response:
                    yield f"data: {json.dumps(response)}\n\n"
                time.sleep(0.5)
        except GeneratorExit:
            print("Client disconnected from status stream.")
    return Response(generate_status_updates(), mimetype='text/event-stream')

# --- API Routes ---
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

def extract_urls_from_text(text):
    """Finds all http/https URLs in a block of text."""
    return re.findall(r'https?://[^\s"]+', text)

@app.route("/queue", methods=["POST"])
def add_to_queue_route():
    urls = extract_urls_from_text(request.form.get("urls", ""))
    if not urls:
        return jsonify({"message": "No valid URLs found in the input."}), 400
    
    mode = request.form.get("download_mode")
    folder_name = sanitize_filename(request.form.get(f"{mode}_foldername", "").strip())
    
    try:
        playlist_start = int(p_start_str) if (p_start_str := request.form.get("playlist_start", "").strip()) else None
        playlist_end = int(p_end_str) if (p_end_str := request.form.get("playlist_end", "").strip()) else None
    except ValueError:
        return jsonify({"message": "Playlist start/end must be a number."}), 400

    jobs_added = 0
    for url in urls:
        if not (url := url.strip()): continue
        
        job = { "url": url, "mode": mode, "folder": folder_name,
                "archive": request.form.get("use_archive") == "yes",
                "playlist_start": playlist_start, "playlist_end": playlist_end,
                "proxy": request.form.get("proxy", "").strip(),
                "rate_limit": request.form.get("rate_limit", "").strip() }

        if mode == 'music':
            job.update({"format": request.form.get("music_audio_format"), "quality": request.form.get("music_audio_quality")})
        elif mode == 'video':
            job.update({"quality": request.form.get("video_quality"), "format": request.form.get("video_format"),
                        "embed_subs": request.form.get("video_embed_subs") == "on", "codec": request.form.get("video_codec_preference")})
        elif mode == 'clip':
            job.update({"format": request.form.get("clip_format")})
        elif mode == 'custom':
            job.update({"custom_args": request.form.get("custom_args")})

        state_manager.add_to_queue(job)
        jobs_added += 1
    
    return jsonify({"message": f"Added {jobs_added} job(s) to the queue."})

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

@app.route("/queue/continue", methods=['POST'])
def continue_job_route():
    job = request.get_json()
    if not job or "url" not in job:
        return jsonify({"message": "Invalid job data."}), 400
    state_manager.add_to_queue(job)
    return jsonify({"message": f"Re-queued job: {job.get('title', job['url'])}"})

@app.route('/history/log/<int:log_id>')
def history_log_route(log_id):
    item = state_manager.get_history_item_by_log_id(log_id)
    if not item: return jsonify({"log": "Log not found."}), 404
    log_path = item.get("log_path")
    log_content = "Log not found or could not be read."
    if log_path and log_path != "LOG_SAVE_ERROR" and os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                log_content = f.read()
        except Exception as e:
            log_content = f"ERROR: Could not read log file. Reason: {e}"
    elif log_path == "LOG_SAVE_ERROR":
        log_content = "There was an error saving the log file for this job."
    return jsonify({"log": log_content})

@app.route('/api/log/live/stream')
def live_log_stream_route():
    def generate_log_stream():
        log_path = state_manager.current_download.get("log_path")
        if not log_path or not os.path.exists(log_path):
            yield f"data: No active log file found.\n\n"
            return
        with open(log_path, 'r', encoding='utf-8') as log_file:
            # First, stream existing content
            for line in log_file: 
                yield f"data: {line.strip()}\n\n"
            # Then, tail the file for new content
            while state_manager.current_download.get("url") is not None:
                if line := log_file.readline():
                    yield f"data: {line.strip()}\n\n"
                else:
                    time.sleep(0.1)
        yield f"data: --- End of Stream ---\n\n"
    return Response(generate_log_stream(), mimetype='text/event-stream')

@app.route('/history/clear', methods=['POST'])
def clear_history_route():
    for path in state_manager.clear_history():
        try:
            if os.path.exists(path):
                if os.path.isdir(path): shutil.rmtree(path)
                else: os.remove(path)
        except Exception as e:
            print(f"ERROR: Could not delete path {path}: {e}")
    return jsonify({"message": "History cleared."})

@app.route('/history/delete/<int:log_id>', methods=['POST'])
def delete_from_history_route(log_id):
    path_to_delete = state_manager.delete_from_history(log_id)
    if path_to_delete is None: return jsonify({"message": "Item not found."}), 404
    try:
        if path_to_delete and os.path.exists(path_to_delete): 
            os.remove(path_to_delete)
    except Exception as e:
        print(f"ERROR: Could not delete log file {path_to_delete}: {e}")
    return jsonify({"message": "History log deleted."})

@app.route("/stop", methods=['POST'])
def stop_route():
    mode = (request.get_json() or {}).get('mode', 'cancel') 
    state_manager.stop_mode = "SAVE" if mode == 'save' else "CANCEL"
    message = "Stop & Save signal sent." if mode == 'save' else "Cancel signal sent."
    state_manager.cancel_event.set()
    return jsonify({"message": message})

# --- File Manager API ---
@app.route("/api/files")
def list_files_route():
    base_download_dir = os.path.realpath(CONFIG.get("download_dir"))
    req_path = request.args.get('path', '')
    
    # Prevent path traversal attacks
    safe_req_path = os.path.abspath(os.path.join(base_download_dir, req_path))
    if not is_safe_path(base_download_dir, safe_req_path):
        return jsonify({"error": "Access Denied"}), 403
    if not os.path.isdir(safe_req_path): return jsonify([])

    items = []
    try:
        for name in os.listdir(safe_req_path):
            full_path = os.path.join(safe_req_path, name)
            # Create a relative path for the client to use
            relative_path = os.path.relpath(full_path, base_download_dir)
            item_data = {"name": name, "path": relative_path.replace("\\", "/")} # Ensure forward slashes for client
            
            try:
                if os.path.isdir(full_path):
                    item_data.update({"type": "directory", "item_count": len(os.listdir(full_path))})
                else:
                    item_data.update({"type": "file", "size": os.path.getsize(full_path)})
                items.append(item_data)
            except OSError:
                # This can happen for broken symlinks or permission errors on sub-items
                continue
    except OSError as e:
        print(f"Could not scan directory {safe_req_path}: {e}")
        return jsonify({"error": f"Cannot access directory: {e.strerror}"}), 500
        
    return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

@app.route("/download_item")
def download_item_route():
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
                # Correctly calculate arcname for directories
                base_arcname = os.path.basename(full_path)
                for root, _, files in os.walk(full_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # arcname should be relative to the directory being zipped
                        arcname = os.path.join(base_arcname, os.path.relpath(file_path, full_path))
                        zip_file.write(file_path, arcname=arcname)
            else:
                zip_file.write(full_path, arcname=os.path.basename(full_path))
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=zip_name, mimetype='application/zip')

@app.route("/api/delete_item", methods=['POST'])
def delete_item_route():
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

#~ --- App Initialization --- ~#
def initialize_app():
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    os.makedirs(CONFIG["temp_dir"], exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    atexit.register(state_manager.save_state)
    state_manager.load_state()
    threading.Thread(target=scheduled_update_check, daemon=True).start()
    threading.Thread(target=yt_dlp_worker, args=(state_manager, CONFIG, LOG_DIR, CONF_COOKIE_FILE, YT_DLP_PATH, FFMPEG_PATH), daemon=True).start()

#~ --- Main Execution --- ~#
if __name__ == "__main__":
    initialize_app()
    print("--- Starting Server with Waitress ---")
    print(f"Server running at: http://0.0.0.0:8080 or http://127.0.0.1:8080")
    serve(app, host="0.0.0.0", port=8080)

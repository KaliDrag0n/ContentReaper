# web_tool.py
from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file
import threading, os, json, atexit, time, signal, subprocess, requests, shutil, io, zipfile, re, sys, platform

# --- Local Imports from 'lib' directory ---
from lib.state_manager import StateManager
from lib.worker import yt_dlp_worker
from lib.sanitizer import sanitize_filename

app = Flask(__name__)

#~ --- Configuration --- ~#
# ##-- FIX: APP_VERSION and GITHUB_REPO_SLUG are constants, not part of the user config --##
APP_VERSION = "1.3.0" 
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# --- File & Folder Paths ---
CONF_CONFIG_FILE = os.path.join(APP_ROOT, "config.json")
CONF_STATE_FILE = os.path.join(APP_ROOT, "state.json")
CONF_COOKIE_FILE = os.path.join(APP_ROOT, "cookies.txt")
LOG_DIR = os.path.join(APP_ROOT, "logs")

# --- Default Config ---
CONFIG = {
    "download_dir": os.path.join(APP_ROOT, "downloads"),
    "temp_dir": os.path.join(APP_ROOT, ".temp"),
    "cookie_file_content": ""
}

#~ --- Global State & Threading --- ~#
state_manager = StateManager(CONF_STATE_FILE)

# --- Update Checking State ---
update_status = {
    "update_available": False,
    "latest_version": "0.0.0",
    "release_url": "",
    "release_notes": ""
}

#~ --- Security & Path Helpers --- ~#
def is_safe_path(basedir, path, follow_symlinks=True):
    """
    Checks if a given path is safely within a base directory.
    This prevents directory traversal attacks.
    """
    if follow_symlinks:
        return os.path.realpath(path).startswith(basedir)
    return os.path.abspath(path).startswith(basedir)

#~ --- Update & Startup Logic --- ~#
def run_startup_checks():
    """
    Checks for dependencies on startup and installs/updates them.
    This runs ONCE when the application starts.
    """
    print("--- [1/3] Running startup dependency checks ---")
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
    except Exception as e:
        print(f"ERROR: Could not install Python dependencies from requirements.txt: {e}")

    print("--- [2/3] Checking for yt-dlp updates ---")
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'])
    except Exception as e:
        print(f"ERROR: Could not update yt-dlp: {e}")
    print("--- [3/3] Startup checks complete ---")


def trigger_update_and_restart():
    """
    The core logic for the new platform-agnostic self-updater.
    This is called from the /api/install_update route.
    """
    print("--- UPDATE PROCESS INITIATED ---")
    
    print("[1/4] Fetching latest release information...")
    # ##-- FIX: Use the GITHUB_REPO_SLUG constant --##
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        data = response.json()
        zip_url = data.get("zipball_url")
        if not zip_url:
            raise ValueError("Could not find zipball_url in the release info.")
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
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)
            z.extractall(temp_dir)
        update_source_dir = os.path.join(temp_dir, root_folder_name)
    except Exception as e:
        print(f"ERROR: Failed to download or unzip update: {e}")
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        return

    print("[3/4] Applying update...")
    preserved_items = ["downloads", ".temp", "logs", "config.json", "state.json", "cookies.txt", ".git"]
    try:
        for item in os.listdir(update_source_dir):
            source_item_path = os.path.join(update_source_dir, item)
            dest_item_path = os.path.join(APP_ROOT, item)
            if item in preserved_items:
                continue
            if os.path.isdir(source_item_path):
                if os.path.exists(dest_item_path): shutil.rmtree(dest_item_path)
                shutil.copytree(source_item_path, dest_item_path)
            else:
                shutil.copy2(source_item_path, dest_item_path)
    except Exception as e:
        print(f"ERROR: An error occurred while applying the update: {e}")
        return
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    print("[4/4] Update applied. Restarting server...")
    state_manager.save_state()
    os.execv(sys.executable, ['python'] + sys.argv)


def _run_update_check():
    """The core logic for checking GitHub for updates."""
    global update_status
    # ##-- FIX: Use the GITHUB_REPO_SLUG constant --##
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
    try:
        print("UPDATE: Checking for new version...")
        res = requests.get(api_url, timeout=15)
        res.raise_for_status()
        
        latest_release = res.json()
        latest_version_tag = latest_release.get("tag_name", "").lstrip('v')
        
        current_parts = [int(p) for p in APP_VERSION.split('.')]
        latest_parts = [int(p) for p in latest_version_tag.split('.')]

        with state_manager._lock:
            if latest_parts > current_parts:
                print(f"UPDATE: New version found! Latest: {latest_version_tag}, Current: {APP_VERSION}")
                update_status["update_available"] = True
                update_status["latest_version"] = latest_version_tag
                update_status["release_url"] = latest_release.get("html_url")
                update_status["release_notes"] = latest_release.get("body")
            else:
                print("UPDATE: You are on the latest version.")
                update_status["update_available"] = False
        return True
    except Exception as e:
        print(f"UPDATE: An error occurred while checking for updates: {e}")
    return False

def scheduled_update_check():
    """Runs the update check on a schedule."""
    while True:
        _run_update_check()
        time.sleep(3600)

#~ --- Config & State Persistence --- ~#
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
                # Ensure core identity keys aren't loaded from user config
                loaded_config.pop("app_version", None)
                loaded_config.pop("github_repo_slug", None)
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
    if request.method == "POST":
        CONFIG["download_dir"] = request.form.get("download_dir", CONFIG["download_dir"])
        CONFIG["temp_dir"] = request.form.get("temp_dir", CONFIG["temp_dir"])
        CONFIG["cookie_file_content"] = request.form.get("cookie_content", "")
        with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f:
            f.write(CONFIG["cookie_file_content"])
        save_config()
        return redirect(url_for('settings_route', saved='true'))
    
    with state_manager._lock:
        current_update_status = update_status.copy()
    
    return render_template("settings.html", 
                           config=CONFIG, 
                           saved=request.args.get('saved'),
                           app_version=APP_VERSION,
                           update_info=current_update_status)

@app.route("/file_manager")
def file_manager_route():
    return render_template("file_manager.html")

@app.route("/status")
def status_route():
    with state_manager._lock:
        current_dl = state_manager.current_download
        response = {
            "queue": state_manager.get_queue_list(),
            "current": current_dl if current_dl.get("url") else None,
            "history_version": state_manager.history_state_version,
            "is_paused": not state_manager.queue_paused_event.is_set()
        }
    return jsonify(response)

# --- API Routes ---
@app.route("/api/update_check")
def update_check_route():
    with state_manager._lock:
        return jsonify(update_status)

@app.route("/api/force_update_check", methods=['POST'])
def force_update_check_route():
    success = _run_update_check()
    if success:
        return jsonify({"message": "Update check completed."})
    else:
        return jsonify({"message": "Update check failed. See server logs for details."}), 500

@app.route('/api/shutdown', methods=['POST'])
def shutdown_route():
    def shutdown_server():
        print("SHUTDOWN: Server is shutting down...")
        os.kill(os.getpid(), signal.SIGINT)
    threading.Timer(1.0, shutdown_server).start()
    return jsonify({"message": "Server is shutting down."})

@app.route('/api/install_update', methods=['POST'])
def install_update_route():
    update_thread = threading.Thread(target=trigger_update_and_restart)
    update_thread.start()
    return jsonify({"message": "Update process initiated. The server will restart shortly."})


def extract_urls_from_text(text):
    url_regex = re.compile(r'(https?://[^\s]+|www\.[^\s]+)')
    return url_regex.findall(text)

@app.route("/queue", methods=["POST"])
def add_to_queue_route():
    raw_urls_text = request.form.get("urls", "")
    urls = extract_urls_from_text(raw_urls_text)

    if not urls:
        return jsonify({"message": "No valid URLs found in the input."}), 400
    
    mode = request.form.get("download_mode")
    
    folder_name = ""
    if mode == 'music':
        folder_name = request.form.get("music_foldername", "").strip()
    elif mode == 'video':
        folder_name = request.form.get("video_foldername", "").strip()
    elif mode == 'custom':
        folder_name = request.form.get("custom_foldername", "").strip()
    
    folder_name = sanitize_filename(folder_name)

    jobs_added = 0
    for url in urls:
        url = url.strip()
        if not url: continue
        
        job = { 
            "url": url, "mode": mode,
            "folder": folder_name,
            "archive": request.form.get("use_archive") == "yes",
            "playlist_start": request.form.get("playlist_start"),
            "playlist_end": request.form.get("playlist_end"),
            "proxy": request.form.get("proxy", "").strip(),
            "rate_limit": request.form.get("rate_limit", "").strip()
        }

        if mode == 'music':
            job.update({
                "format": request.form.get("music_audio_format", "mp3"),
                "quality": request.form.get("music_audio_quality", "0"),
            })
        elif mode == 'video':
            job.update({
                "quality": request.form.get("video_quality", "best"),
                "format": request.form.get("video_format", "mp4"),
                "embed_subs": request.form.get("video_embed_subs") == "on"
            })
        elif mode == 'clip':
            job.update({ "format": request.form.get("clip_format", "video") })
        elif mode == 'custom':
            job.update({ "custom_args": request.form.get("custom_args", "") })


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
    ordered_ids = data.get('order')
    if ordered_ids is None:
        return jsonify({"message": "Missing order data."}), 400
    
    try:
        ordered_ids = [int(i) for i in ordered_ids]
    except (ValueError, TypeError):
        return jsonify({"message": "Invalid job IDs provided."}), 400
        
    state_manager.reorder_queue(ordered_ids)
    return jsonify({"message": "Queue reordered."})

@app.route('/queue/pause', methods=['POST'])
def pause_queue_route():
    state_manager.queue_paused_event.clear()
    return jsonify({"message": "Queue paused."})

@app.route('/queue/resume', methods=['POST'])
def resume_queue_route():
    state_manager.queue_paused_event.set()
    return jsonify({"message": "Queue resumed."})


@app.route("/queue/continue", methods=['POST'])
def continue_job_route():
    job = request.get_json()
    if not job or "url" not in job:
        return jsonify({"message": "Invalid job data."}), 400
    state_manager.add_to_queue(job)
    return jsonify({"message": f"Re-queued job: {job.get('title', job['url'])}"})

@app.route('/preview')
def preview_route():
    url = request.args.get('url')
    if not url: return jsonify({"message": "URL is required."}), 400
    try:
        is_playlist = 'playlist?list=' in url
        
        if is_playlist:
            cmd = ['yt-dlp', '--get-title', '--get-thumbnail', '--playlist-items', '1', '-s', url]
        else:
            cmd = ['yt-dlp', '--get-title', '--get-thumbnail', '-s', url]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True, encoding='utf-8', errors='replace')
        output = proc.stdout.strip().splitlines()
        
        if len(output) >= 2:
            title = output[0]
            thumbnail_url = output[1]
        elif len(output) == 1:
            title = output[0]
            thumbnail_url = ""
        else:
            raise Exception("Could not extract preview details.")

        return jsonify({"title": title, "thumbnail": thumbnail_url})
    except subprocess.TimeoutExpired:
        return jsonify({"message": "Preview request timed out."}), 504
    except Exception as e:
        return jsonify({"message": f"Could not get preview: {e}"}), 500

@app.route('/history')
def get_history_route():
    return jsonify({"history": state_manager.get_history_summary()})

@app.route('/history/log/<int:log_id>')
def history_log_route(log_id):
    item = state_manager.get_history_item_by_log_id(log_id)
    if not item:
        return jsonify({"log": "Log not found."}), 404

    log_path = item.get("log_path")
    log_content = "Log not found or could not be read."

    if log_path and log_path != "LOG_SAVE_ERROR" and os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                log_content = f.read()
        except Exception as e:
            log_content = f"ERROR: Could not read log file at {log_path}. Reason: {e}"
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
            for line in log_file:
                yield f"data: {line.strip()}\n\n"

            while state_manager.current_download.get("url") is not None:
                line = log_file.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                else:
                    time.sleep(0.1)
        yield f"data: --- End of Stream ---\n\n"

    return Response(generate_log_stream(), mimetype='text/event-stream')


@app.route('/history/clear', methods=['POST'])
def clear_history_route():
    paths_to_delete = state_manager.clear_history()
    for path in paths_to_delete:
        try:
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:
            print(f"ERROR: Could not delete path {path}: {e}")
    return jsonify({"message": "History cleared."})

@app.route('/history/delete/<int:log_id>', methods=['POST'])
def delete_from_history_route(log_id):
    log_path_to_delete = state_manager.delete_from_history(log_id)
    if log_path_to_delete is None:
        return jsonify({"message": "Item not found."}), 404
        
    try:
        if os.path.exists(log_path_to_delete):
            os.remove(log_path_to_delete)
    except Exception as e:
        print(f"ERROR: Could not delete log file {log_path_to_delete}: {e}")
            
    return jsonify({"message": "History log deleted."})

@app.route("/stop", methods=['POST'])
def stop_route():
    data = request.get_json() or {}
    mode = data.get('mode', 'cancel') 

    if mode == 'save':
        state_manager.stop_mode = "SAVE"
        message = "Stop & Save signal sent. Completed files will be saved."
    else:
        state_manager.stop_mode = "CANCEL"
        message = "Cancel signal sent. All temporary files will be deleted."
    
    state_manager.cancel_event.set()
    return jsonify({"message": message})

# --- File Manager API ---
def get_dir_size(path, size_dict):
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += get_dir_size(entry.path, size_dict)
    except (FileNotFoundError, PermissionError):
        pass
    size_dict[path] = total
    return total

@app.route("/api/files")
def list_files_route():
    base_download_dir = os.path.realpath(CONFIG.get("download_dir"))
    req_path = request.args.get('path', '')
    
    safe_req_path = os.path.realpath(os.path.join(base_download_dir, req_path))
    
    if not is_safe_path(base_download_dir, safe_req_path):
        return jsonify({"error": "Access Denied"}), 403

    if not os.path.exists(safe_req_path) or not os.path.isdir(safe_req_path):
        return jsonify([])

    items = []
    dir_threads = []
    dir_sizes = {}
    for name in os.listdir(safe_req_path):
        full_path = os.path.join(safe_req_path, name)
        relative_path = os.path.join(req_path, name)
        try:
            if os.path.isdir(full_path):
                thread = threading.Thread(target=get_dir_size, args=(full_path, dir_sizes))
                thread.start()
                dir_threads.append(thread)
                items.append({"name": name, "path": relative_path, "type": "directory", "size": -1})
            else:
                items.append({"name": name, "path": relative_path, "type": "file", "size": os.path.getsize(full_path)})
        except Exception as e:
            print(f"Could not scan item {full_path}: {e}")
    
    for t in dir_threads:
        t.join(timeout=5.0)

    for item in items:
        if item['type'] == 'directory':
            full_path_for_size = os.path.join(safe_req_path, item['name'])
            item['size'] = dir_sizes.get(full_path_for_size, 0)

    return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

@app.route("/download_item")
def download_item_route():
    paths = request.args.getlist('paths')
    if not paths:
        return "Missing path parameter.", 400

    download_dir = os.path.realpath(CONFIG.get("download_dir"))
    
    safe_full_paths = []
    for p in paths:
        full_path = os.path.realpath(os.path.join(download_dir, p))
        if is_safe_path(download_dir, full_path) and os.path.exists(full_path):
            safe_full_paths.append(full_path)
    
    if not safe_full_paths:
        return "No valid or accessible files specified.", 404

    if len(safe_full_paths) == 1:
        full_path = safe_full_paths[0]
        if os.path.isdir(full_path):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for root, _, files in os.walk(full_path):
                    for file in files:
                        file_path_in_zip = os.path.relpath(os.path.join(root, file), full_path)
                        zip_file.write(os.path.join(root, file), arcname=os.path.join(os.path.basename(full_path), file_path_in_zip))
            zip_buffer.seek(0)
            return send_file(zip_buffer, as_attachment=True, download_name=f"{os.path.basename(full_path)}.zip", mimetype='application/zip')
        else:
            return send_file(full_path, as_attachment=True)
    
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for full_path in safe_full_paths:
                if os.path.isdir(full_path):
                     for root, _, files in os.walk(full_path):
                        for file in files:
                            file_path_in_zip = os.path.relpath(os.path.join(root, file), download_dir)
                            zip_file.write(os.path.join(root, file), arcname=file_path_in_zip)
                else:
                    file_path_in_zip = os.path.relpath(full_path, download_dir)
                    zip_file.write(full_path, arcname=file_path_in_zip)
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name="downloader_selection.zip", mimetype='application/zip')


@app.route("/api/delete_item", methods=['POST'])
def delete_item_route():
    data = request.get_json()
    paths = data.get('paths', [])
    if not paths:
        return jsonify({"message": "Missing path parameter."}), 400

    download_dir = os.path.realpath(CONFIG.get("download_dir"))
    deleted_count = 0
    errors = []

    for item_path in paths:
        full_path = os.path.realpath(os.path.join(download_dir, item_path))

        if not is_safe_path(download_dir, full_path):
            errors.append(f"Access denied for {item_path}")
            continue
        
        if not os.path.exists(full_path):
            continue

        try:
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
            deleted_count += 1
        except Exception as e:
            error_msg = f"Error deleting {item_path}: {e}"
            print(f"ERROR: {error_msg}")
            errors.append(error_msg)
    
    if errors:
        return jsonify({"message": f"Completed with errors. Deleted {deleted_count} item(s).", "errors": errors}), 500
    else:
        return jsonify({"message": f"Successfully deleted {deleted_count} item(s)."})


#~ --- App Initialization --- ~#
def initialize_app():
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    os.makedirs(CONFIG["temp_dir"], exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    
    atexit.register(state_manager.save_state)
    state_manager.load_state()

    update_check_thread = threading.Thread(target=scheduled_update_check, daemon=True)
    update_check_thread.start()
    
    worker_thread = threading.Thread(
        target=yt_dlp_worker, 
        args=(state_manager, CONFIG, LOG_DIR, CONF_COOKIE_FILE), 
        daemon=True
    )
    worker_thread.start()

#~ --- Main Execution --- ~#
run_startup_checks()
initialize_app()

if __name__ == "__main__":
    from waitress import serve
    print("--- Starting Server with Waitress ---")
    serve(app, host="0.0.0.0", port=8080)

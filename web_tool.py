# web_tool.py
from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file
import threading, os, json, atexit, time, signal, subprocess, requests, shutil, io, zipfile

# --- Local Imports from 'lib' directory ---
from lib.state_manager import StateManager
from lib.worker import yt_dlp_worker
from lib.sanitizer import sanitize_filename

app = Flask(__name__)

#~ --- Configuration --- ~#
APP_VERSION = "1.1.0" # The current version of this application
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI" # Your GitHub repo slug
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
queue_paused_event = threading.Event()

# --- Update Checking State ---
update_status = {
    "update_available": False,
    "latest_version": APP_VERSION,
    "release_url": "",
    "release_notes": ""
}

#~ --- Update Checker --- ~#
def _run_update_check():
    """The core logic for checking GitHub for updates."""
    global update_status
    api_url = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
    try:
        print("UPDATE: Checking for new version...")
        res = requests.get(api_url, timeout=15)
        res.raise_for_status()
        
        latest_release = res.json()
        latest_version_tag = latest_release.get("tag_name", "").lstrip('v')
        
        with state_manager._lock:
            if latest_version_tag and latest_version_tag != APP_VERSION:
                print(f"UPDATE: New version found! Latest: {latest_version_tag}, Current: {APP_VERSION}")
                update_status["update_available"] = True
                update_status["latest_version"] = latest_version_tag
                update_status["release_url"] = latest_release.get("html_url")
                update_status["release_notes"] = latest_release.get("body")
            else:
                print("UPDATE: You are on the latest version.")
                update_status["update_available"] = False
        return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print("UPDATE: No releases found for this repository on GitHub.")
        else:
            print(f"UPDATE: HTTP Error checking for updates: {e}")
    except Exception as e:
        print(f"UPDATE: An unexpected error occurred while checking for updates: {e}")
    return False

def scheduled_update_check():
    """Runs the update check on a schedule."""
    while True:
        _run_update_check()
        time.sleep(3600)

#~ --- Config & State Persistence --- ~#
def save_config():
    with open(CONF_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(CONFIG, f, indent=4)

def load_config():
    global CONFIG
    if os.path.exists(CONF_CONFIG_FILE):
        try:
            with open(CONF_CONFIG_FILE, 'r', encoding='utf-8') as f:
                CONFIG.update(json.load(f))
        except Exception as e:
            print(f"Error loading config: {e}")

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
            "history_version": state_manager.history_state_version
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
    update_script_path = os.path.join(APP_ROOT, "update.bat")
    if os.path.exists(update_script_path):
        try:
            subprocess.Popen([update_script_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
            return jsonify({"message": "Update process initiated. The server will restart shortly."})
        except Exception as e:
            print(f"ERROR: Failed to start update script: {e}")
            return jsonify({"message": f"Failed to start update script: {e}"}), 500
    else:
        return jsonify({"message": "update.bat not found!"}), 404

@app.route("/queue", methods=["POST"])
def add_to_queue_route():
    urls = request.form.get("urls", "").strip().splitlines()
    if not any(urls):
        return jsonify({"message": "At least one URL is required."}), 400
    
    mode = request.form.get("download_mode")
    
    music_folder = request.form.get("music_foldername", "").strip()
    video_folder = request.form.get("video_foldername", "").strip()
    
    folder_name = ""
    if mode == 'music':
        folder_name = music_folder or video_folder
    elif mode == 'video':
        folder_name = video_folder or music_folder
    
    folder_name = sanitize_filename(folder_name)

    if not folder_name:
        try:
            first_url = next(url for url in urls if url)
            is_playlist = "playlist?list=" in first_url
            print_field = 'playlist_title' if is_playlist else 'title'
            fetch_cmd = ['yt-dlp', '--print', print_field, '--playlist-items', '1', '-s', first_url]
            result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, check=True)
            output_lines = result.stdout.strip().splitlines()
            if output_lines:
                folder_name = sanitize_filename(output_lines[0])
        except Exception as e:
            print(f"Could not auto-fetch title for folder name: {e}")

    if not folder_name:
        if mode == 'music': folder_name = "Misc Music"
        else: folder_name = "Misc Videos"

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
        title = ""
        thumbnail_url = ""

        if is_playlist:
            title_cmd = ['yt-dlp', '--print', 'playlist_title', '--playlist-items', '1', '-s', url]
            proc_title = subprocess.run(title_cmd, capture_output=True, text=True, timeout=60, check=True, encoding='utf-8', errors='replace')
            title = proc_title.stdout.strip().splitlines()[0] if proc_title.stdout.strip() else "Untitled Playlist"
            
            thumb_cmd = ['yt-dlp', '--print', '%(thumbnail)s', '--playlist-items', '1', '-s', url]
            proc_thumb = subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=60, check=True, encoding='utf-8', errors='replace')
            thumbnail_url = proc_thumb.stdout.strip().splitlines()[0] if proc_thumb.stdout.strip() else ""
        else:
            json_cmd = ['yt-dlp', '--print-json', '--skip-download', url]
            proc_json = subprocess.run(json_cmd, capture_output=True, text=True, timeout=60, check=True, encoding='utf-8', errors='replace')
            data = json.loads(proc_json.stdout)
            title = data.get('title', 'No Title Found')
            thumbnail_url = data.get('thumbnail', '')
        
        return jsonify({"title": title, "thumbnail": thumbnail_url})
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
    paths_to_delete = state_manager.delete_from_history(log_id, CONFIG.get("download_dir"))
    if paths_to_delete is None:
        return jsonify({"message": "Item not found."}), 404
        
    for path in paths_to_delete:
        try:
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        except Exception as e:
            print(f"ERROR: Could not delete path {path}: {e}")
            
    return jsonify({"message": "History item and associated files deleted."})

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
def get_dir_size(path='.'):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total

@app.route("/api/files")
def list_files_route():
    download_dir = CONFIG.get("download_dir")
    if not os.path.exists(download_dir):
        return jsonify([])

    items = []
    for name in os.listdir(download_dir):
        path = os.path.join(download_dir, name)
        try:
            if os.path.isdir(path):
                items.append({
                    "name": name,
                    "path": name,
                    "type": "directory",
                    "size": get_dir_size(path)
                })
            else:
                items.append({
                    "name": name,
                    "path": name,
                    "type": "file",
                    "size": os.path.getsize(path)
                })
        except Exception as e:
            print(f"Could not scan item {path}: {e}")
    
    return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

@app.route("/download_item")
def download_item_route():
    item_path = request.args.get('path')
    if not item_path:
        return "Missing path parameter.", 400

    download_dir = CONFIG.get("download_dir")
    full_path = os.path.join(download_dir, item_path)

    if not os.path.abspath(full_path).startswith(os.path.abspath(download_dir)):
        return "Access denied.", 403

    if not os.path.exists(full_path):
        return "File or directory not found.", 404

    try:
        if os.path.isdir(full_path):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for root, _, files in os.walk(full_path):
                    for file in files:
                        file_path_in_zip = os.path.relpath(os.path.join(root, file), full_path)
                        zip_file.write(os.path.join(root, file), arcname=file_path_in_zip)
            zip_buffer.seek(0)
            
            zip_filename = f"{os.path.basename(item_path)}.zip"
            return send_file(zip_buffer, as_attachment=True, download_name=zip_filename, mimetype='application/zip')
        else:
            return send_file(full_path, as_attachment=True)
    except Exception as e:
        print(f"ERROR sending item {full_path}: {e}")
        return "Error sending item.", 500

@app.route("/api/delete_item", methods=['POST'])
def delete_item_route():
    data = request.get_json()
    item_path = data.get('path')
    if not item_path:
        return jsonify({"message": "Missing path parameter."}), 400

    download_dir = CONFIG.get("download_dir")
    full_path = os.path.join(download_dir, item_path)

    if not os.path.abspath(full_path).startswith(os.path.abspath(download_dir)):
        return jsonify({"message": "Access denied."}), 403
    
    if not os.path.exists(full_path):
        return jsonify({"message": "File or directory not found."}), 404

    try:
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return jsonify({"message": f"Successfully deleted {item_path}."})
    except Exception as e:
        print(f"ERROR deleting item {full_path}: {e}")
        return jsonify({"message": f"Error deleting item: {e}"}), 500


#~ --- App Initialization --- ~#
def initialize_app():
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    os.makedirs(CONFIG["temp_dir"], exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    
    atexit.register(state_manager.save_state)
    state_manager.load_state()

    update_thread = threading.Thread(target=scheduled_update_check, daemon=True)
    update_thread.start()
    
    worker_thread = threading.Thread(
        target=yt_dlp_worker, 
        args=(state_manager, CONFIG, LOG_DIR, CONF_COOKIE_FILE, queue_paused_event), 
        daemon=True
    )
    worker_thread.start()

    queue_paused_event.set()

initialize_app()


#~ --- Main Execution (for direct run, e.g. from an IDE) --- ~#
if __name__ == "__main__":
    from waitress import serve
    print("Starting server with Waitress for direct execution...")
    serve(app, host="0.0.0.0", port=8080)

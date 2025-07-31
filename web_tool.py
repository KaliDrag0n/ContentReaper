from flask import Flask, request, render_template, jsonify, redirect, url_for
import threading, queue, subprocess, os, re, json, atexit

app = Flask(__name__)

#~ --- Configuration --- ~#
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = {
    "download_dir": os.path.join(APP_ROOT, "downloads"),
    "cookie_file_content": ""
}
CONF_CONFIG_FILE = os.path.join(APP_ROOT, "config.json")
CONF_STATE_FILE = os.path.join(APP_ROOT, "state.json")
CONF_COOKIE_FILE = os.path.join(APP_ROOT, "cookies.txt")

#~ --- Global State & Threading --- ~#
download_lock = threading.RLock()
download_queue = queue.Queue()
cancel_event = threading.Event()
current_download = {
    "url": None, "job_data": None, "progress": 0, "status": "", "title": None,
    "playlist_count": 0, "playlist_index": 0,
    "speed": None, "eta": None, "file_size": None
}
download_history = []
next_log_id = 0
next_queue_id = 0
# -- FIX: Added a dedicated version counter for any history state change --
history_state_version = 0 

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
            print(f"Could not load config.json. Error: {e}")

def save_state():
    # This function should only be called within a `download_lock` block
    state = {
        "queue": list(download_queue.queue),
        "history": download_history,
        "current_job": current_download.get("job_data"),
        "next_log_id": next_log_id,
        "next_queue_id": next_queue_id,
        "history_state_version": history_state_version # Save the new version
    }
    with open(CONF_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=4)

def load_state():
    global download_history, next_log_id, next_queue_id, history_state_version
    if not os.path.exists(CONF_STATE_FILE): return
    try:
        with open(CONF_STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except Exception as e:
        print(f"Could not load state file. Error: {e}")
        corrupted_path = CONF_STATE_FILE + ".bak"
        if os.path.exists(CONF_STATE_FILE): os.rename(CONF_STATE_FILE, corrupted_path)
        print(f"Backed up corrupted state file to {corrupted_path}")
        return

    with download_lock:
        abandoned_job = state.get("current_job")
        if abandoned_job: download_queue.put(abandoned_job)
            
        download_history = state.get("history", [])
        next_log_id = state.get("next_log_id", len(download_history))
        next_queue_id = state.get("next_queue_id", 0)
        # -- FIX: Load the saved history version --
        history_state_version = state.get("history_state_version", 0)
        
        for job in state.get("queue", []):
            if 'id' not in job:
                job['id'] = next_queue_id
                next_queue_id += 1
            download_queue.put(job)
    
    print(f"Loaded {download_queue.qsize()} items from queue and {len(download_history)} history entries.")

#~ --- Worker Helper Functions --- ~#
def build_yt_dlp_command(job, albumName, outputFolder):
    is_playlist = "playlist?list=" in job["url"]
    cmd = [
        'yt-dlp', '-f', 'bestaudio', '-x', '--audio-format', job.get("format", "mp3"),
        '--audio-quality', job.get("quality", "0"), '--sleep-interval', '3', '--max-sleep-interval', '10',
        '--embed-thumbnail', '--embed-metadata', '--postprocessor-args', f'-metadata album="{albumName}"',
        '--parse-metadata', 'playlist_index:%(track_number)s', '--parse-metadata', 'uploader:%(artist)s',
        '--progress-template', '%(progress)j', # Use JSON progress template
        '-o', outputFolder,
    ]
    if is_playlist or job.get("refetch"): cmd.append('--ignore-errors')
    if os.path.exists(CONF_COOKIE_FILE) and CONFIG.get("cookie_file_content"):
        cmd.extend(['--cookies', CONF_COOKIE_FILE])
    if job.get("archive") and not job.get("refetch"):
        archiveFile = os.path.join(CONFIG["download_dir"], albumName, "archive.txt")
        cmd.extend(['--download-archive', archiveFile])
    cmd.append(job["url"])
    return cmd

#~ --- The Worker --- ~#
def yt_dlp_worker():
    global next_log_id, history_state_version
    while True:
        job = download_queue.get()
        cancel_event.clear()
        log_output = []

        with download_lock:
            current_download.update({
                "url": job["url"], "progress": 0, "status": "Starting...", "title": "",
                "playlist_count": 0, "playlist_index": 0, "job_data": job,
                "speed": None, "eta": None, "file_size": None
            })
            save_state()

        is_playlist = "playlist?list=" in job["url"]
        albumName = job["folder"]
        if not albumName and is_playlist:
             try:
                fetch_cmd = ['yt-dlp', '--print', 'playlist_title', '--playlist-items', '1', '--simulate', job["url"]]
                result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
                if result.returncode == 0: albumName = result.stdout.strip().split('\n')[0]
             except Exception as e: print(f'Failed to acquire playlist title. Error: {e}')
        
        sanitized_album = re.sub(r'[^a-zA-Z0-9 _-]', '', albumName or "Misc Downloads").strip()
        download_path = os.path.join(CONFIG["download_dir"], sanitized_album)
        if not os.path.abspath(download_path).startswith(os.path.abspath(CONFIG["download_dir"])):
            print(f"Error: Invalid folder name '{sanitized_album}' attempted path traversal.")
            continue

        outputFolder = os.path.join(download_path, "%(title)s.%(ext)s")
        cmd = build_yt_dlp_command(job, sanitized_album, outputFolder)
        
        process = None
        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, encoding='utf-8', errors='replace', bufsize=1
            )
            for line in iter(process.stdout.readline, ''):
                if cancel_event.is_set():
                    process.kill()
                    break
                
                line = line.strip()
                log_output.append(line)

                with download_lock:
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            progress_data = json.loads(line)
                            current_download["status"] = progress_data.get("status", "Downloading...").capitalize()
                            if current_download["status"] == 'Downloading':
                                current_download["progress"] = progress_data.get("percentage", 0)
                                current_download["speed"] = progress_data.get("speed_string", "N/A")
                                current_download["eta"] = progress_data.get("eta_string", "N/A")
                                current_download["file_size"] = progress_data.get("total_bytes_string", "N/A")
                                current_download["title"] = os.path.basename(progress_data.get("filename", "...")).rsplit('.', 1)[0]
                        except json.JSONDecodeError:
                            print(f"Could not parse progress JSON: {line}")
                    elif '[download] Downloading item' in line:
                        match = re.search(r'Downloading item (\d+) of (\d+)', line)
                        if match: 
                            current_download['playlist_index'] = int(match.group(1))
                            current_download['playlist_count'] = int(match.group(2))
                    elif line.startswith("[ExtractAudio] Destination:"):
                        current_download["status"] = 'Converting...'
                        current_download["progress"] = 100
            
            process.wait(timeout=7200)
            
            with download_lock:
                title = sanitized_album if is_playlist else current_download.get("title", "Unknown Title")
                history_item = {"url": job["url"], "title": title, "folder": sanitized_album, "job_data": job, "log": "\n".join(log_output), "log_id": next_log_id}
                next_log_id += 1
                if cancel_event.is_set(): history_item["status"] = "CANCELLED"
                elif process.returncode == 0: history_item["status"] = "COMPLETED"
                else: history_item["status"] = "FAILED"; history_item["error_code"] = process.returncode
                download_history.append(history_item)
                history_state_version += 1 # -- FIX: Increment version on new item
        except Exception as e:
            with download_lock:
                history_item = {"url": job["url"], "title": "Worker Error", "folder": sanitized_album, "job_data": job, "log": "\n".join(log_output), "log_id": next_log_id}
                next_log_id += 1; history_item["status"] = "ERROR"; history_item["error_message"] = f"{type(e).__name__}: {e}"
                download_history.append(history_item)
                history_state_version += 1 # -- FIX: Increment version on new item
        finally:
            with download_lock:
                current_download.update({ "url": None, "job_data": None })
                download_queue.task_done()
                save_state()

#~ --- Flask Routes --- ~#
@app.route("/")
def index_route():
    return render_template("index.html")

@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    if request.method == "POST":
        with download_lock:
            CONFIG["download_dir"] = request.form.get("download_dir", CONFIG["download_dir"])
            CONFIG["cookie_file_content"] = request.form.get("cookie_content", CONFIG["cookie_file_content"])
            with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f: f.write(CONFIG["cookie_file_content"])
            save_config()
        return redirect(url_for('settings_route', saved='true'))
    return render_template("settings.html", config=CONFIG, saved=request.args.get('saved'))

@app.route("/status")
def status_route():
    with download_lock:
        return jsonify({
            "queue": list(download_queue.queue),
            "current": current_download if current_download["url"] else None,
            # -- FIX: Return the new state version instead of the log counter --
            "history_version": history_state_version 
        })

@app.route('/history')
def get_history_route():
    with download_lock:
        history_summary = [item.copy() for item in download_history]
        for item in history_summary: item.pop("log", None)
        return jsonify({"history": history_summary[-20:]})

@app.route("/queue", methods=["POST"])
def add_to_queue_route():
    global next_queue_id
    url = request.form.get("url")
    if not url: return jsonify({"message": "A URL is required."}), 400
    
    with download_lock:
        job = {
            "id": next_queue_id,
            "url": url,
            "folder": request.form.get("foldername", "").strip(),
            "archive": request.form.get("use_archive") == "yes",
            "format": request.form.get("audio_format", "mp3"),
            "quality": request.form.get("audio_quality", "0"),
        }
        next_queue_id += 1
        download_queue.put(job)
        save_state()
        
    return jsonify({"message": f"Added to queue: {url}"})

@app.route("/queue/retry", methods=["POST"])
def retry_queue_route():
    global next_queue_id
    job = request.json
    if not job or "url" not in job: return jsonify({"message": "Invalid job data."}), 400
    
    with download_lock:
        job['id'] = next_queue_id
        next_queue_id += 1
        download_queue.put(job)
        save_state()
        
    return jsonify({"message": f"Retrying: {job['url']}"})

@app.route('/history/log/<int:log_id>')
def history_log_route(log_id):
    with download_lock:
        item_to_log = next((item for item in download_history if item.get("log_id") == log_id), None)
    
    if item_to_log:
        return jsonify({"log": item_to_log.get("log", "No log available.")})
    return jsonify({"log": "Log not found."}), 404

@app.route('/history/clear', methods=['POST'])
def clear_history_route():
    global history_state_version
    with download_lock:
        if download_history: # Only increment if there was something to clear
             history_state_version += 1
        download_history.clear()
        save_state()
    return jsonify({"message": "History cleared successfully."})

@app.route('/history/delete/<int:log_id>', methods=['POST'])
def delete_from_history_route(log_id):
    global history_state_version
    with download_lock:
        item_to_delete = next((item for item in download_history if item.get("log_id") == log_id), None)
        if item_to_delete:
            download_history.remove(item_to_delete)
            history_state_version += 1 # -- FIX: Increment version on delete
            save_state()
            return jsonify({"message": "History item deleted."})
    return jsonify({"message": "History item not found."}), 404

@app.route("/cancel", methods=['POST'])
def cancel_route():
    cancel_event.set()
    return jsonify({"message": "Cancel signal sent."})

@app.route("/queue/clear", methods=['POST'])
def clear_queue_route():
    with download_lock:
        download_queue.queue.clear()
        save_state()
    return jsonify({"message": "Queue cleared."})

@app.route("/queue/delete/by-id/<int:job_id>", methods=['POST'])
def delete_from_queue_by_id_route(job_id):
    job_found = False
    with download_lock:
        initial_size = download_queue.qsize()
        download_queue.queue = [job for job in download_queue.queue if job.get('id') != job_id]
        if download_queue.qsize() < initial_size:
            job_found = True
            save_state()
            
    if job_found:
        return jsonify({"message": f"Job {job_id} deleted from queue."})
    else:
        return jsonify({"message": "Job ID not found in queue."}), 404

#~ --- Main Execution --- ~#
if __name__ == "__main__":
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f: f.write(CONFIG.get("cookie_file_content", ""))
    atexit.register(save_state)
    load_state()
    threading.Thread(target=yt_dlp_worker, daemon=True).start()
    print("Starting Up Flask...")
    app.run(host="0.0.0.0", port=8080, debug=False)

from flask import Flask, request, render_template, jsonify, redirect, url_for
import threading, queue, subprocess, os, re, json, atexit, datetime
import io, shutil
from zipfile import ZipFile, ZIP_DEFLATED

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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"

#~ --- Global State & Threading --- ~#
download_lock = threading.RLock()
download_queue = queue.Queue()
cancel_event = threading.Event()
queue_paused_event = threading.Event()
current_download = {
    "url": None, "job_data": None, "progress": 0, "status": "", "title": None,
    "playlist_count": 0, "playlist_index": 0,
    "speed": None, "eta": None, "file_size": None
}
download_history = []
next_log_id = 0
next_queue_id = 0
history_state_version = 0

#~ --- Config & State Persistence --- ~#
def save_config():
    with open(CONF_CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(CONFIG, f, indent=4)

def load_config():
    global CONFIG
    if os.path.exists(CONF_CONFIG_FILE):
        try:
            with open(CONF_CONFIG_FILE, 'r', encoding='utf-8') as f: CONFIG.update(json.load(f))
        except Exception as e: print(f"Error loading config: {e}")

def save_state():
    state = {"queue": list(download_queue.queue), "history": download_history, "current_job": current_download.get("job_data"),"next_log_id": next_log_id,"next_queue_id": next_queue_id, "history_state_version": history_state_version}
    with open(CONF_STATE_FILE, 'w', encoding='utf-8') as f: json.dump(state, f, indent=4)

def load_state():
    global download_history, next_log_id, next_queue_id, history_state_version
    if not os.path.exists(CONF_STATE_FILE): return
    try:
        with open(CONF_STATE_FILE, 'r', encoding='utf-8') as f: state = json.load(f)
        with download_lock:
            abandoned_job = state.get("current_job")
            if abandoned_job: download_queue.put(abandoned_job)
            download_history = state.get("history", [])
            next_log_id = state.get("next_log_id", len(download_history))
            next_queue_id = state.get("next_queue_id", 0)
            history_state_version = state.get("history_state_version", 0)
            for job in state.get("queue", []):
                if 'id' not in job: job['id'] = next_queue_id; next_queue_id += 1
                download_queue.put(job)
        print(f"Loaded {download_queue.qsize()} items from queue and {len(download_history)} history entries.")
    except Exception as e:
        print(f"Could not load state file. Error: {e}")
        corrupted_path = CONF_STATE_FILE + ".bak"
        if os.path.exists(CONF_STATE_FILE): os.rename(CONF_STATE_FILE, corrupted_path)
        print(f"Backed up corrupted state file to {corrupted_path}")

#~ --- Worker Helper Functions --- ~#
def format_bytes(b):
    if b is None: return "N/A";
    b = float(b)
    if b < 1024: return f"{b} B"
    if b < 1024**2: return f"{b/1024:.2f} KiB"
    return f"{b/1024**2:.2f} MiB"

def format_seconds(s):
    if s is None: return "N/A";
    try: return str(datetime.timedelta(seconds=int(s)))
    except: return "N/A"

def build_yt_dlp_command(job, folder_name, output_path):
    cmd = ['yt-dlp', '--user-agent', USER_AGENT]
    mode = job.get("mode")

    if mode == 'music':
        cmd.extend([
            '-f', 'bestaudio', '-x', '--audio-format', job.get("format", "mp3"),
            '--audio-quality', '0', '--embed-metadata',
            '--postprocessor-args', f'-metadata album="{folder_name}"',
            '--parse-metadata', 'playlist_index:%(track_number)s',
            '--parse-metadata', 'uploader:%(artist)s'
        ])
        if job.get("embed_art", True):
            cmd.append('--embed-thumbnail')
    
    elif mode == 'video':
        quality = job.get('quality', 'best')
        video_format = job.get('format', 'mp4')
        format_str = f"bestvideo[height<={quality[:-1]}][ext={video_format}]+bestaudio/best[ext={video_format}]/best" if quality != 'best' else f'bestvideo[ext={video_format}]+bestaudio/best[ext={video_format}]/best'
        cmd.extend(['-f', format_str, '--merge-output-format', video_format])
        if job.get('embed_subs'):
            cmd.extend(['--embed-subs', '--sub-langs', 'en.*,en-US,en-GB'])

    elif mode == 'clip':
        if job.get('format') == 'audio':
            cmd.extend(['-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', '0'])
        else:
            cmd.extend(['-f', 'bestvideo+bestaudio/best', '--merge-output-format', 'mp4'])

    cmd.extend(['--progress-template', '%(progress)j', '-o', output_path])
    
    if "playlist?list=" in job["url"] or job.get("refetch"):
        cmd.append('--ignore-errors')

    if os.path.exists(CONF_COOKIE_FILE) and CONFIG.get("cookie_file_content"):
        cmd.extend(['--cookies', CONF_COOKIE_FILE])

    if job.get("archive") and not job.get("refetch"):
        archive_dir = os.path.join(CONFIG["download_dir"], folder_name)
        os.makedirs(archive_dir, exist_ok=True)
        archiveFile = os.path.join(archive_dir, "archive.txt")
        cmd.extend(['--download-archive', archiveFile])

    cmd.append(job["url"])
    return cmd

#~ --- The Worker --- ~#
def yt_dlp_worker():
    global next_log_id, history_state_version
    while True:
        queue_paused_event.wait()
        job = download_queue.get()
        cancel_event.clear()
        log_output = []
        job_title = "Unknown"
        final_folder_name = "Unknown"
        current_filepath = None # Variable to store the path of the file being downloaded

        try:
            is_playlist = "playlist?list=" in job.get("url", "")
            user_folder_name = job.get("folder")
            
            if is_playlist and job.get("mode") == "music" and not user_folder_name:
                try:
                    fetch_cmd = ['yt-dlp', '--print', 'playlist_title', '--playlist-items', '1', '-s', job["url"]]
                    result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
                    output_lines = result.stdout.strip().splitlines()
                    if result.returncode == 0 and output_lines:
                        job_title = output_lines[0]
                except Exception as e:
                    print(f'Failed to acquire playlist title. Error: {e}')
                    job_title = "Misc Playlist"
            else:
                try:
                    fetch_cmd = ['yt-dlp', '--print', 'title', '--no-playlist', '-s', job["url"]]
                    result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
                    output_lines = result.stdout.strip().splitlines()
                    if result.returncode == 0 and output_lines:
                        job_title = output_lines[0]
                except Exception as e:
                    print(f'Failed to acquire video title. Error: {e}')
                    job_title = "Single File Download"
            
            if user_folder_name:
                final_folder_name = user_folder_name
            elif is_playlist:
                final_folder_name = job_title
            else:
                if job.get("mode") == "music": final_folder_name = "Misc Music"
                elif job.get("mode") == "video": final_folder_name = "Misc Videos"
                else: final_folder_name = "Misc Downloads"

            sanitized_folder = re.sub(r'[^a-zA-Z0-9 _.-]', '', final_folder_name or "Misc Downloads").strip()
            download_path = os.path.join(CONFIG["download_dir"], sanitized_folder)
            
            if not os.path.abspath(download_path).startswith(os.path.abspath(CONFIG["download_dir"])):
                raise Exception(f"Invalid folder name '{sanitized_folder}' attempted path traversal.")
            os.makedirs(download_path, exist_ok=True)
            
            outputFolder = os.path.join(download_path, "%(title)s.%(ext)s")
            cmd = build_yt_dlp_command(job, sanitized_folder, outputFolder)
            
            with download_lock:
                current_download.update({
                    "url": job["url"], "progress": 0, "status": "Starting...", "title": job_title,
                    "playlist_count": 0, "playlist_index": 0, "job_data": job,
                    "speed": None, "eta": None, "file_size": None
                })
                save_state()

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
                            progress = json.loads(line)
                            current_download["status"] = progress.get("status", "Downloading...").capitalize()
                            if current_download["status"] == 'Downloading':
                                current_filepath = progress.get("filename") # Store the current file's path
                                progress_percent = progress.get("_percent") 
                                if progress_percent is not None:
                                    current_download["progress"] = float(progress_percent)
                                
                                total_size = progress.get("total_bytes") or progress.get("total_bytes_estimate")
                                current_download["file_size"] = format_bytes(total_size)
                                
                                current_track_title = os.path.basename(current_filepath or "...").rsplit('.',1)[0]
                                
                                if is_playlist:
                                    current_download["title"] = f"{job_title} - Now Downloading: {current_track_title}"
                                else:
                                    current_download["title"] = current_track_title

                                current_download.update({"speed": f"{format_bytes(progress.get('speed'))}/s", "eta": format_seconds(progress.get('eta'))})
                        except (json.JSONDecodeError, TypeError): pass
                    elif '[download] Downloading item' in line:
                        match = re.search(r'Downloading item (\d+) of (\d+)', line)
                        if match: 
                            current_download.update({'playlist_index': int(match.group(1)), 'playlist_count': int(match.group(2))})
                    elif any(s in line for s in ("[ExtractAudio]", "[Merger]")):
                        current_download.update({"status": 'Processing...'})
            
            process.wait(timeout=7200)
            status = "CANCELLED" if cancel_event.is_set() else ("COMPLETED" if process.returncode == 0 else "FAILED")
            
            # --- NEW: Cleanup logic for cancelled downloads ---
            if status == "CANCELLED" and current_filepath:
                log_output.append(f"Cancellation detected. Cleaning up partial file...")
                try:
                    part_file = current_filepath + ".part"
                    if os.path.exists(part_file):
                        os.remove(part_file)
                        log_output.append(f"Removed partial file: {part_file}")
                except Exception as cleanup_e:
                    log_output.append(f"Error during file cleanup: {cleanup_e}")
            # --- END NEW ---

        except Exception as e:
            status = "ERROR"
            log_output.append(f"\nWORKER EXCEPTION: {e}")
            if isinstance(e, subprocess.CalledProcessError):
                error_message = e.stderr.strip()
                log_output.append(f"\nYT-DLP ERROR DETAILS:\n{error_message}")
        
        with download_lock:
            history_item = {"url": job["url"], "title": job_title, "folder": sanitized_folder, "job_data": job, "log": "\n".join(log_output), "log_id": next_log_id, "status": status}
            download_history.append(history_item); next_log_id += 1; history_state_version += 1
            current_download.update({"url": None, "job_data": None})
            download_queue.task_done(); save_state()

#~ --- Flask Routes --- ~#
@app.route("/")
def index_route(): return render_template("index.html")

@app.route("/settings", methods=["GET", "POST"])
def settings_route():
    if request.method == "POST":
        with download_lock:
            CONFIG["download_dir"] = request.form.get("download_dir", CONFIG["download_dir"])
            CONFIG["cookie_file_content"] = request.form.get("cookie_content", "")
            with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f: f.write(CONFIG["cookie_file_content"])
            save_config()
        return redirect(url_for('settings_route', saved='true'))
    return render_template("settings.html", config=CONFIG, saved=request.args.get('saved'))

@app.route("/status")
def status_route():
    with download_lock:
        return jsonify({"queue": list(download_queue.queue),"current": current_download if current_download["url"] else None,"history_version": history_state_version})

@app.route("/queue", methods=["POST"])
def add_to_queue_route():
    global next_queue_id
    urls = request.form.get("urls", "").strip().splitlines()
    if not any(urls):
        return jsonify({"message": "At least one URL is required."}), 400
    
    mode = request.form.get("download_mode")
    jobs_added = 0
    with download_lock:
        for url in urls:
             url = url.strip()
             if not url: continue
             
             job = { "id": next_queue_id, "url": url, "mode": mode, "archive": request.form.get("use_archive") == "yes" }

             if mode == 'music':
                job.update({
                    "folder": request.form.get("music_foldername", "").strip(),
                    "format": request.form.get("music_audio_format", "mp3"),
                    "quality": "0",
                    "embed_art": request.form.get("music_embed_art") == "on"
                })
             elif mode == 'video':
                 job.update({
                    "folder": request.form.get("video_foldername", "").strip(),
                    "quality": request.form.get("video_quality", "best"),
                    "format": request.form.get("video_format", "mp4"),
                    "embed_subs": request.form.get("video_embed_subs") == "on"
                 })
             elif mode == 'clip':
                 job.update({ "format": request.form.get("clip_format", "video") })

             next_queue_id += 1
             download_queue.put(job)
             jobs_added += 1
        
        if jobs_added > 0:
            save_state()
            
    return jsonify({"message": f"Added {jobs_added} job(s) to the queue."})

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
            proc_title = subprocess.run(title_cmd, capture_output=True, text=True, timeout=15, check=True, encoding='utf-8', errors='replace')
            output_lines = proc_title.stdout.strip().splitlines()
            if not output_lines: raise Exception("Could not fetch playlist title.")
            title = output_lines[0]
            
            thumb_cmd = ['yt-dlp', '--print', '%(thumbnail)s', '--playlist-items', '1', '-s', url]
            proc_thumb = subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=15, check=True, encoding='utf-8', errors='replace')
            thumb_lines = proc_thumb.stdout.strip().splitlines()
            if thumb_lines: thumbnail_url = thumb_lines[0]
        else:
            json_cmd = ['yt-dlp', '--print-json', '-s', url]
            proc_json = subprocess.run(json_cmd, capture_output=True, text=True, timeout=15, check=True, encoding='utf-8', errors='replace')
            data = json.loads(proc_json.stdout)
            title = data.get('title', 'No Title Found')
            thumbnail_url = data.get('thumbnail', '')
        
        return jsonify({"title": title, "thumbnail": thumbnail_url})
    except Exception as e:
        return jsonify({"message": f"Could not get preview: {e}"}), 500

@app.route('/history')
def get_history_route():
    with download_lock:
        history_summary = [h.copy() for h in download_history]
        for item in history_summary: item.pop("log", None)
        return jsonify({"history": history_summary})

@app.route('/history/log/<int:log_id>')
def history_log_route(log_id):
    item = next((h for h in download_history if h.get("log_id") == log_id), None)
    return jsonify({"log": item.get("log", "Log not found.")}) if item else ("", 404)

@app.route('/history/clear', methods=['POST'])
def clear_history_route():
    global history_state_version
    with download_lock:
        download_history.clear(); history_state_version += 1; save_state()
    return jsonify({"message": "History cleared."})

@app.route('/history/delete/<int:log_id>', methods=['POST'])
def delete_from_history_route(log_id):
    global history_state_version
    with download_lock:
        initial_len = len(download_history)
        download_history[:] = [h for h in download_history if h.get("log_id") != log_id]
        if len(download_history) < initial_len:
            history_state_version += 1; save_state()
            return jsonify({"message": "History item deleted."})
    return jsonify({"message": "Item not found."}), 404

@app.route("/cancel", methods=['POST'])
def cancel_route(): cancel_event.set(); return jsonify({"message": "Cancel signal sent."})

#~ --- Main Execution --- ~#
if __name__ == "__main__":
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f: f.write(CONFIG.get("cookie_file_content", ""))
    atexit.register(save_state)
    load_state()
    queue_paused_event.set()
    threading.Thread(target=yt_dlp_worker, daemon=True).start()
    print("Starting Up Flask...")
    app.run(host="0.0.0.0", port=8080, debug=False)

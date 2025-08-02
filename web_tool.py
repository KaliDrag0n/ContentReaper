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

#~ --- Global State & Threading --- ~#
download_lock = threading.RLock()
download_queue = queue.Queue()
cancel_event = threading.Event()
queue_paused_event = threading.Event()
stop_mode = "CANCEL" 

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
            if abandoned_job:
                print(f"Re-queueing abandoned job: {abandoned_job.get('id')}")
                download_queue.put(abandoned_job)
            
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

def build_yt_dlp_command(job, temp_dir_path):
    cmd = ['yt-dlp']
    mode = job.get("mode")
    cmd.extend(['--sleep-interval', '3', '--max-sleep-interval', '10'])
    
    final_folder_name = job.get("folder") or "Misc Downloads"
    is_playlist = "playlist?list=" in job.get("url", "")

    if mode == 'music':
        album_metadata = final_folder_name if is_playlist else '%(album)s'
        cmd.extend([
            '-f', 'bestaudio', '-x', '--audio-format', job.get("format", "mp3"),
            '--audio-quality', '0', '--embed-metadata', '--embed-thumbnail',
            '--postprocessor-args', f'-metadata album="{album_metadata}" -metadata date="{datetime.datetime.now().year}"',
            '--parse-metadata', 'playlist_index:%(track_number)s',
            '--parse-metadata', 'uploader:%(artist)s'
        ])
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

    output_template = os.path.join(temp_dir_path, "%(playlist_index)02d - %(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s")
    cmd.extend(['--progress-template', '%(progress)j', '-o', output_template])
    
    start = job.get("playlist_start")
    end = job.get("playlist_end")
    if start and end: cmd.extend(['--playlist-items', f'{start}-{end}'])
    elif start: cmd.extend(['--playlist-items', f'{start}:'])
    elif end: cmd.extend(['--playlist-items', f':{end}'])

    if "playlist?list=" in job["url"] or job.get("refetch"): cmd.append('--ignore-errors')
    if os.path.exists(CONF_COOKIE_FILE) and CONFIG.get("cookie_file_content"): cmd.extend(['--cookies', CONF_COOKIE_FILE])
    
    if job.get("archive"):
        temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")
        cmd.extend(['--download-archive', temp_archive_file])

    cmd.append(job["url"])
    return cmd

def _finalize_job(job, final_status, log_output):
    temp_dir_path = os.path.join(CONFIG["download_dir"], ".temp", f"job_{job['id']}")
    
    final_folder_name = job.get("folder") or "Misc Downloads"
    final_dest_dir = os.path.join(CONFIG["download_dir"], final_folder_name)
    os.makedirs(final_dest_dir, exist_ok=True)
    
    final_title = "Unknown"
    moved_count = 0
    
    target_format = job.get("format", "mp4")
    if job.get("mode") == 'clip' and target_format == 'audio':
        target_format = 'mp3'
    elif job.get("mode") == 'music':
        target_format = job.get("format", "mp3")

    log_output.append(f"Finalizing job. Looking for completed files with extension '.{target_format}'...")

    if os.path.exists(temp_dir_path):
        for f in os.listdir(temp_dir_path):
            if f.endswith(f'.{target_format}'):
                try:
                    shutil.move(os.path.join(temp_dir_path, f), os.path.join(final_dest_dir, f))
                    final_title = f.rsplit('.', 1)[0]
                    moved_count += 1
                except Exception as e:
                    log_output.append(f"ERROR: Could not move completed file {f}: {e}")
        
        if moved_count > 0:
            log_output.append(f"Moved {moved_count} completed file(s).")
            if final_status == "FAILED":
                final_status = "PARTIAL"
                log_output.append("Status updated to PARTIAL due to partial success.")
        else:
            log_output.append("No completed files matching target format found to move.")

        temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")
        if os.path.exists(temp_archive_file):
            main_archive_file = os.path.join(final_dest_dir, "archive.txt")
            try:
                shutil.copy2(temp_archive_file, main_archive_file)
                log_output.append(f"Updated archive file at {main_archive_file}")
            except Exception as e:
                log_output.append(f"ERROR: Could not update archive file: {e}")

    log_output.append("Cleaning up temporary folder.")
    if os.path.exists(temp_dir_path):
        shutil.rmtree(temp_dir_path)

    return final_status, final_folder_name, final_title


def yt_dlp_worker():
    global download_history, next_log_id, history_state_version, stop_mode
    while True:
        queue_paused_event.wait()
        job = download_queue.get()
        cancel_event.clear()
        stop_mode = "CANCEL"
        
        log_output = []
        status = "PENDING"
        temp_dir_path = os.path.join(CONFIG["download_dir"], ".temp", f"job_{job['id']}")

        try:
            os.makedirs(temp_dir_path, exist_ok=True)
            log_output.append(f"Created temporary directory: {temp_dir_path}")
            
            if job.get("archive"):
                final_folder_name = job.get("folder") or "Misc Downloads"
                main_archive_file = os.path.join(CONFIG["download_dir"], final_folder_name, "archive.txt")
                if os.path.exists(main_archive_file):
                    temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")
                    shutil.copy2(main_archive_file, temp_archive_file)
                    log_output.append(f"Copied existing archive to temp directory for processing.")

            cmd = build_yt_dlp_command(job, temp_dir_path)
            
            with download_lock:
                current_download.update({ "url": job["url"], "progress": 0, "status": "Starting...", "title": "Starting Download...", "job_data": job})
                save_state()

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            
            local_playlist_index = 0
            local_playlist_count = 0
            
            for line in iter(process.stdout.readline, ''):
                if cancel_event.is_set(): process.kill(); break
                line = line.strip(); log_output.append(line)
                with download_lock:
                    if line.startswith('{'):
                        try:
                            progress = json.loads(line)
                            current_download["status"] = progress.get("status", "Downloading...").capitalize()
                            if current_download["status"] == 'Downloading':
                                progress_percent = progress.get("_percent_str") or progress.get("progress", {}).get("fraction")
                                if progress_percent: current_download["progress"] = float(progress_percent) * 100 if isinstance(progress_percent, float) else float(progress_percent.replace('%',''))
                                
                                total_size = progress.get("total_bytes") or progress.get("total_bytes_estimate")
                                current_download["file_size"] = format_bytes(total_size)
                                current_track_title = os.path.basename(progress.get("filename", "...")).rsplit('.',1)[0]
                                
                                is_playlist = "playlist?list=" in job.get("url", "")
                                if is_playlist:
                                    current_download.update({
                                        'playlist_title': job.get("folder"),
                                        'track_title': current_track_title,
                                        'playlist_index': local_playlist_index,
                                        'playlist_count': local_playlist_count
                                    })
                                else:
                                    current_download["title"] = current_track_title

                                current_download.update({"speed": progress.get("_speed_str", "N/A"), "eta": progress.get("_eta_str", "N/A")})
                        except (json.JSONDecodeError, TypeError): pass
                    elif '[download] Downloading item' in line:
                        match = re.search(r'Downloading item (\d+) of (\d+)', line)
                        if match: 
                            local_playlist_index = int(match.group(1))
                            local_playlist_count = int(match.group(2))
                    elif any(s in line for s in ("[ExtractAudio]", "[Merger]")):
                        current_download.update({"status": 'Processing...'})

            process.wait(timeout=7200)
            
            if cancel_event.is_set(): status = "STOPPED" if stop_mode == "SAVE" else "CANCELLED"
            elif process.returncode == 0: status = "COMPLETED"
            else: status = "FAILED"

        except Exception as e:
            status = "ERROR"; log_output.append(f"\nWORKER EXCEPTION: {e}")
        
        finally:
            status, final_folder, final_title = _finalize_job(job, status, log_output)
            
            with download_lock:
                history_item = {"url": job["url"], "title": final_title, "folder": final_folder, "job_data": job, "log": "\n".join(log_output), "log_id": next_log_id, "status": status}
                download_history.append(history_item)
                next_log_id += 1; history_state_version += 1
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
    
    music_folder = request.form.get("music_foldername", "").strip()
    video_folder = request.form.get("video_foldername", "").strip()
    
    folder_name = ""
    if mode == 'music':
        folder_name = music_folder or video_folder
    elif mode == 'video':
        folder_name = video_folder or music_folder
    
    if mode == 'music' and not folder_name and any("playlist?list=" in url for url in urls):
        try:
            first_playlist_url = next(url for url in urls if "playlist?list=" in url)
            fetch_cmd = ['yt-dlp', '--print', 'playlist_title', '--playlist-items', '1', '-s', first_playlist_url]
            result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, check=True)
            output_lines = result.stdout.strip().splitlines()
            if output_lines:
                folder_name = output_lines[0]
        except Exception as e:
            print(f"Could not auto-fetch playlist title: {e}")
            folder_name = "Misc Music"

    print(f"DEBUG: Mode: {mode}, Chosen Folder: '{folder_name}'")

    jobs_added = 0
    with download_lock:
        for url in urls:
            url = url.strip()
            if not url: continue
            
            job = { 
                "id": next_queue_id, "url": url, "mode": mode,
                "folder": folder_name,
                "archive": request.form.get("use_archive") == "yes",
                "playlist_start": request.form.get("playlist_start"),
                "playlist_end": request.form.get("playlist_end")
            }

            if mode == 'music':
                job.update({
                    "format": request.form.get("music_audio_format", "mp3"),
                    "quality": "0",
                    "embed_art": request.form.get("music_embed_art") == "on"
                })
            elif mode == 'video':
                job.update({
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

@app.route("/queue/continue", methods=['POST'])
def continue_job_route():
    global next_queue_id
    job = request.get_json()
    if not job or "url" not in job:
        return jsonify({"message": "Invalid job data."}), 400

    with download_lock:
        job['id'] = next_queue_id
        next_queue_id += 1
        download_queue.put(job)
        save_state()

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
            output_lines = proc_title.stdout.strip().splitlines()
            if not output_lines: raise Exception("Could not fetch playlist title.")
            title = output_lines[0]
            
            thumb_cmd = ['yt-dlp', '--print', '%(thumbnail)s', '--playlist-items', '1', '-s', url]
            proc_thumb = subprocess.run(thumb_cmd, capture_output=True, text=True, timeout=60, check=True, encoding='utf-8', errors='replace')
            thumb_lines = proc_thumb.stdout.strip().splitlines()
            if thumb_lines: thumbnail_url = thumb_lines[0]
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
    with download_lock:
        history_summary = [h.copy() for h in download_history]
        for item in history_summary: item.pop("log", None)
        return jsonify({"history": history_summary})

@app.route('/history/log/<int:log_id>')
def history_log_route(log_id):
    with download_lock:
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

@app.route("/stop", methods=['POST'])
def stop_route():
    global stop_mode
    data = request.get_json() or {}
    mode = data.get('mode', 'cancel') 

    if mode == 'save':
        stop_mode = "SAVE"
        message = "Stop & Save signal sent. Completed files will be saved."
    else:
        stop_mode = "CANCEL"
        message = "Cancel signal sent. All temporary files will be deleted."
    
    cancel_event.set()
    return jsonify({"message": message})


#~ --- Main Execution --- ~#
if __name__ == "__main__":
    from waitress import serve
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    os.makedirs(os.path.join(CONFIG["download_dir"], ".temp"), exist_ok=True)
    atexit.register(save_state)
    load_state()
    queue_paused_event.set()
    threading.Thread(target=yt_dlp_worker, daemon=True).start()
    print("Starting Server with Waitress...")
    serve(app, host="0.0.0.0", port=8080)

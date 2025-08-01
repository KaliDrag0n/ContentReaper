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

def build_yt_dlp_command(job, folder_name, output_path, temp_dir_path):
    cmd = ['yt-dlp']
    mode = job.get("mode")
    cmd.extend(['--sleep-interval', '3', '--max-sleep-interval', '10'])

    if mode == 'music':
        cmd.extend([
            '-f', 'bestaudio', '-x', '--audio-format', job.get("format", "mp3"),
            '--audio-quality', '0', '--embed-metadata', '--embed-thumbnail',
            '--postprocessor-args', f'-metadata album="{folder_name}" -metadata date="{datetime.datetime.now().year}"',
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

    cmd.extend(['--progress-template', '%(progress)j', '-o', output_path])
    
    start = job.get("playlist_start")
    end = job.get("playlist_end")
    if start and end:
        cmd.extend(['--playlist-items', f'{start}-{end}'])
    elif start:
        cmd.extend(['--playlist-items', f'{start}:'])
    elif end:
        cmd.extend(['--playlist-items', f':{end}'])

    if "playlist?list=" in job["url"] or job.get("refetch"):
        cmd.append('--ignore-errors')

    if os.path.exists(CONF_COOKIE_FILE) and CONFIG.get("cookie_file_content"):
        cmd.extend(['--cookies', CONF_COOKIE_FILE])

    if job.get("archive") and not job.get("refetch"):
        main_archive_file = os.path.join(CONFIG["download_dir"], folder_name, "archive.txt")
        temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")
        
        if os.path.exists(main_archive_file):
            shutil.copy2(main_archive_file, temp_archive_file)
            
        cmd.extend(['--download-archive', temp_archive_file])

    cmd.append(job["url"])
    return cmd

#~ --- The Worker --- ~#
def yt_dlp_worker():
    global next_log_id, history_state_version, stop_mode
    while True:
        queue_paused_event.wait()
        job = download_queue.get()
        cancel_event.clear()
        stop_mode = "CANCEL"
        
        log_output = []
        job_title = "Unknown"
        final_folder_name = "Unknown"
        status = "PENDING"
        temp_dir_path = os.path.join(CONFIG["download_dir"], ".temp", f"job_{job['id']}")
        temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")

        try:
            os.makedirs(temp_dir_path, exist_ok=True)
            log_output.append(f"Created temporary directory: {temp_dir_path}")

            is_playlist = "playlist?list=" in job.get("url", "")
            # --- FIX: Use the resolved folder name from the job if it exists ---
            user_folder_name = job.get("folder")
            
            # Only determine title/folder if it hasn't been resolved before
            if not user_folder_name or not job.get('title'):
                if is_playlist and job.get("mode") == "music" and not user_folder_name:
                    try:
                        fetch_cmd = ['yt-dlp', '--print', 'playlist_title', '--playlist-items', '1', '-s', job["url"]]
                        result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
                        output_lines = result.stdout.strip().splitlines()
                        if result.returncode == 0 and output_lines: job_title = output_lines[0]
                    except Exception as e: print(f'Failed to acquire playlist title. Error: {e}')
                else:
                    try:
                        fetch_cmd = ['yt-dlp', '--print', 'title', '--no-playlist', '-s', job["url"]]
                        result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
                        output_lines = result.stdout.strip().splitlines()
                        if result.returncode == 0 and output_lines: job_title = output_lines[0]
                    except Exception as e: print(f'Failed to acquire video title. Error: {e}')
                
                job_title = job_title or "Unknown Title"

                if user_folder_name: final_folder_name = user_folder_name
                elif is_playlist: final_folder_name = job_title
                else:
                    if job.get("mode") == "music": final_folder_name = "Misc Music"
                    elif job.get("mode") == "video": final_folder_name = "Misc Videos"
                    else: final_folder_name = "Misc Downloads"
                
                sanitized_folder = re.sub(r'[^a-zA-Z0-9 _.-]', '', final_folder_name or "Misc Downloads").strip()
                # Persist the resolved names in the job data for consistency on retries
                job['folder'] = sanitized_folder
                job['title'] = job_title
            else:
                # If continuing, use the already resolved names
                sanitized_folder = job['folder']
                job_title = job['title']

            final_dest_dir = os.path.join(CONFIG["download_dir"], sanitized_folder)
            os.makedirs(final_dest_dir, exist_ok=True)
            
            temp_output_template = os.path.join(temp_dir_path, "%(playlist_index)02d - %(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s")
            cmd = build_yt_dlp_command(job, sanitized_folder, temp_output_template, temp_dir_path)
            
            with download_lock:
                current_download.update({ "url": job["url"], "progress": 0, "status": "Starting...", "title": job_title, "playlist_count": 0, "playlist_index": 0, "job_data": job, "speed": None, "eta": None, "file_size": None })
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
                                
                                if is_playlist:
                                    current_download.update({
                                        'title': f"{job_title} - Now Downloading: {current_track_title}",
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
            
            if cancel_event.is_set():
                status = "STOPPED" if stop_mode == "SAVE" else "CANCELLED"
            elif process.returncode == 0:
                status = "COMPLETED"
            else:
                status = "FAILED"
            
            if status in ["COMPLETED", "FAILED", "STOPPED"]:
                log_output.append(f"Download process finished with status '{status}'. Checking for completed files...")
                
                files_in_temp = []
                if os.path.exists(temp_dir_path):
                    files_in_temp = os.listdir(temp_dir_path)

                if not files_in_temp:
                    log_output.append("No files found in temporary directory to move.")
                else:
                    moved_count = 0
                    files_by_basename = {}
                    for f in files_in_temp:
                        base_name, _ = os.path.splitext(f)
                        if base_name not in files_by_basename:
                            files_by_basename[base_name] = []
                        files_by_basename[base_name].append(f)

                    for base_name, file_group in files_by_basename.items():
                        if len(file_group) == 1:
                            f = file_group[0]
                            if not f.endswith('.part'):
                                source_path = os.path.join(temp_dir_path, f)
                                dest_path = os.path.join(final_dest_dir, f)
                                shutil.move(source_path, dest_path)
                                moved_count += 1
                        else:
                            log_output.append(f"Skipping incomplete file group for '{base_name}' (found multiple files: {file_group}).")
                    
                    if moved_count > 0:
                        log_output.append(f"Moved {moved_count} completed file(s) to final destination.")
                        if status == "FAILED":
                            status = "COMPLETED"
                            log_output.append("Job status updated to COMPLETED due to partial success.")

                if os.path.exists(temp_archive_file):
                    main_archive_file = os.path.join(final_dest_dir, "archive.txt")
                    
                    main_entries = set()
                    if os.path.exists(main_archive_file):
                        with open(main_archive_file, 'r', encoding='utf-8') as f:
                            main_entries = set(line.strip() for line in f)

                    temp_entries = set()
                    with open(temp_archive_file, 'r', encoding='utf-8') as f:
                        temp_entries = set(line.strip() for line in f)
                    
                    new_entries = temp_entries - main_entries

                    if new_entries:
                        with open(main_archive_file, 'a', encoding='utf-8') as f:
                            for entry in sorted(list(new_entries)):
                                f.write(f"{entry}\n")
                        log_output.append(f"Committed {len(new_entries)} new entries to main archive file.")
                    else:
                        log_output.append("No new entries to commit to archive.")

        except Exception as e:
            status = "ERROR"; log_output.append(f"\nWORKER EXCEPTION: {e}")
            if isinstance(e, subprocess.CalledProcessError):
                error_message = e.stderr.strip()
                log_output.append(f"\nYT-DLP ERROR DETAILS:\n{error_message}")
        
        finally:
            log_output.append("Cleaning up temporary files...")
            try:
                if os.path.exists(temp_dir_path):
                    shutil.rmtree(temp_dir_path)
                    log_output.append("Temporary directory removed.")
            except Exception as cleanup_e:
                log_output.append(f"Error during temp dir cleanup: {cleanup_e}")

            with download_lock:
                history_item = {"url": job["url"], "title": job_title, "folder": sanitized_folder, "job_data": job, "log": "\n".join(log_output), "log_id": next_log_id, "status": status}
                download_history.append(history_item); next_log_id += 1; history_state_version += 1
                current_download.update({"url": None, "job_data": None})
                download_queue.task_done(); save_state()

def recover_orphaned_jobs():
    temp_root = os.path.join(CONFIG["download_dir"], ".temp")
    if not os.path.exists(temp_root):
        return

    print("Scanning for orphaned jobs from previous sessions...")
    with download_lock:
        history_map = {item['job_data']['id']: item for item in download_history if 'job_data' in item and 'id' in item['job_data']}

    for dirname in os.listdir(temp_root):
        if not dirname.startswith("job_"):
            continue

        try:
            job_id = int(dirname.replace("job_", ""))
        except ValueError:
            continue

        if job_id not in history_map:
            print(f"Orphaned temp folder '{dirname}' has no matching history. Deleting.")
            shutil.rmtree(os.path.join(temp_root, dirname))
            continue

        print(f"Found orphaned temp folder for job {job_id}. Attempting recovery...")
        history_item = history_map[job_id]
        temp_dir_path = os.path.join(temp_root, dirname)
        final_dest_dir = os.path.join(CONFIG["download_dir"], history_item["folder"])
        
        try:
            files_in_temp = os.listdir(temp_dir_path)
            files_by_basename = {}
            for f in files_in_temp:
                base_name, _ = os.path.splitext(f)
                if base_name not in files_by_basename: files_by_basename[base_name] = []
                files_by_basename[base_name].append(f)

            moved_count = 0
            for base_name, file_group in files_by_basename.items():
                if len(file_group) == 1 and not file_group[0].endswith('.part'):
                    shutil.move(os.path.join(temp_dir_path, file_group[0]), os.path.join(final_dest_dir, file_group[0]))
                    moved_count += 1
            
            temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")
            if os.path.exists(temp_archive_file):
                main_archive_file = os.path.join(final_dest_dir, "archive.txt")
                main_entries = set()
                if os.path.exists(main_archive_file):
                    with open(main_archive_file, 'r', encoding='utf-8') as f: main_entries = set(line.strip() for line in f)
                with open(temp_archive_file, 'r', encoding='utf-8') as f: temp_entries = set(line.strip() for line in f)
                new_entries = temp_entries - main_entries
                if new_entries:
                    with open(main_archive_file, 'a', encoding='utf-8') as f:
                        for entry in sorted(list(new_entries)): f.write(f"{entry}\n")

            with download_lock:
                history_item["status"] = "STOPPED"
                log_lines = history_item.get("log", "").splitlines()
                log_lines.extend(["", "--- RECOVERY ---", f"Application recovered this job after an unexpected shutdown.", f"Salvaged {moved_count} completed files."])
                history_item["log"] = "\n".join(log_lines)
                save_state()
            
            print(f"Recovery for job {job_id} successful. Salvaged {moved_count} files.")

        except Exception as e:
            print(f"Error during recovery of job {job_id}: {e}")
        finally:
            shutil.rmtree(temp_dir_path)


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
             
             job = { 
                 "id": next_queue_id, "url": url, "mode": mode, 
                 "archive": request.form.get("use_archive") == "yes",
                 "playlist_start": request.form.get("playlist_start"),
                 "playlist_end": request.form.get("playlist_end")
             }

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
    load_config()
    os.makedirs(CONFIG["download_dir"], exist_ok=True)
    os.makedirs(os.path.join(CONFIG["download_dir"], ".temp"), exist_ok=True)
    atexit.register(save_state)
    load_state()
    recover_orphaned_jobs()
    queue_paused_event.set()
    threading.Thread(target=yt_dlp_worker, daemon=True).start()
    print("Starting Up Flask...")
    app.run(host="0.0.0.0", port=8080, debug=False)

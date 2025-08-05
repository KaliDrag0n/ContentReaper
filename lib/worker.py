# lib/worker.py
import subprocess
import os
import re
import json
import datetime
import shutil
import time
import shlex 
from .sanitizer import sanitize_filename

# --- Helper Functions --- #

def format_bytes(b):
    if b is None: return "N/A"
    try:
        b = float(b)
        if b < 1024: return f"{b:.0f} B"
        if b < 1024**2: return f"{b/1024:.2f} KiB"
        if b < 1024**3: return f"{b/1024**2:.2f} MiB"
        return f"{b/1024**3:.2f} GiB"
    except (ValueError, TypeError):
        return "N/A"

# --- Command Builder Refactor --- #

def _get_music_args(job, final_folder_name, is_playlist):
    """Builds the yt-dlp argument list for 'music' mode."""
    album_metadata = final_folder_name if is_playlist else '%(album)s'
    return [
        '-f', 'bestaudio', '-x', '--audio-format', job.get("format", "mp3"),
        '--audio-quality', job.get("quality", "0"), '--embed-metadata', '--embed-thumbnail',
        '--postprocessor-args', f'-metadata album="{album_metadata}" -metadata date="{datetime.datetime.now().year}"',
        '--parse-metadata', 'playlist_index:%(track_number)s',
        '--parse-metadata', 'uploader:%(artist)s'
    ]

def _get_video_args(job):
    """Builds the yt-dlp argument list for 'video' mode."""
    quality = job.get('quality', 'best')
    video_format = job.get('format', 'mp4')
    codec_pref = job.get('codec', 'compatibility')
    
    quality_filter = f"[height<={quality[:-1]}]" if quality != 'best' else ""
    
    if codec_pref == 'compatibility':
        format_str = f"bestvideo{quality_filter}[vcodec^=avc]+bestaudio[acodec^=m4a]/bestvideo{quality_filter}+bestaudio/best"
    else: # 'quality'
        format_str = f"bestvideo{quality_filter}+bestaudio/best"
        
    args = ['-f', format_str, '--merge-output-format', video_format]
    if job.get('embed_subs'):
        args.extend(['--embed-subs', '--sub-langs', 'en.*,en-US,en-GB'])
    return args

def _get_clip_args(job):
    """Builds the yt-dlp argument list for 'clip' mode."""
    if job.get('format') == 'audio':
        return ['-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', '0']
    else:
        return ['-f', 'bestvideo+bestaudio/best', '--merge-output-format', 'mp4']

def build_yt_dlp_command(job, temp_dir_path, cookie_file_path):
    """
    Constructs the full yt-dlp command line argument list for a given download job.
    """
    cmd = ['yt-dlp']
    mode = job.get("mode")
    is_playlist = "playlist?list=" in job.get("url", "")
    final_folder_name = job.get("folder") or "Misc Downloads"

    # --- Basic Options ---
    cmd.extend(['--sleep-interval', '3', '--max-sleep-interval', '10'])
    if job.get('proxy'):
        cmd.extend(['--proxy', job['proxy']])
    if job.get('rate_limit'):
        cmd.extend(['--limit-rate', job['rate_limit']])

    # --- Mode-Specific Arguments ---
    if mode == 'music':
        cmd.extend(_get_music_args(job, final_folder_name, is_playlist))
    elif mode == 'video':
        cmd.extend(_get_video_args(job))
    elif mode == 'clip':
        cmd.extend(_get_clip_args(job))
    elif mode == 'custom':
        cmd.extend(shlex.split(job.get('custom_args', '')))

    # --- Output Template ---
    if mode != 'custom' or ('-o' not in cmd and '--output' not in cmd):
        output_template = os.path.join(temp_dir_path, 
            "%(playlist_index)02d - %(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s")
        cmd.extend(['-o', output_template])
    
    # --- Other Flags ---
    if '--progress-template' not in cmd:
        cmd.extend(['--progress-template', '%(progress)j'])
    
    start, end = job.get("playlist_start"), job.get("playlist_end")
    if start and end: cmd.extend(['--playlist-items', f'{start}-{end}'])
    elif start: cmd.extend(['--playlist-items', f'{start}:'])
    elif end: cmd.extend(['--playlist-items', f':{end}'])

    if is_playlist or job.get("refetch"): 
        cmd.append('--ignore-errors')
    if os.path.exists(cookie_file_path) and os.path.getsize(cookie_file_path) > 0:
        cmd.extend(['--cookies', cookie_file_path])
    if job.get("archive"):
        cmd.extend(['--download-archive', os.path.join(temp_dir_path, "archive.temp.txt")])

    cmd.append(job["url"])
    return cmd


def _finalize_job(job, final_status, temp_log_path, config):
    temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
    
    final_folder_name = job.get("folder") or "Misc Downloads"
    final_dest_dir = os.path.join(config["download_dir"], final_folder_name)
    
    is_playlist = "playlist?list=" in job.get("url", "")
    final_title = "Unknown"
    final_filename = None
    
    with open(temp_log_path, 'a', encoding='utf-8') as log_file:
        def log(message):
            log_file.write(message + '\n')

        log(f"Finalizing job with status: {final_status}...")
        
        moved_count = 0
        final_filenames = []

        if final_status in ["COMPLETED", "PARTIAL", "STOPPED"]:
            if os.path.exists(temp_dir_path):
                files_to_move = [f for f in os.listdir(temp_dir_path) if f != "archive.temp.txt"]
                
                if files_to_move:
                    os.makedirs(final_dest_dir, exist_ok=True)
                    for f in files_to_move:
                        source_path = os.path.join(temp_dir_path, f)
                        dest_path = os.path.join(final_dest_dir, f)
                        try:
                            shutil.move(source_path, dest_path)
                            final_filenames.append(f)
                            moved_count += 1
                        except Exception as e:
                            log(f"ERROR: Could not move completed file {f}: {e}")
                    log(f"Moved {moved_count} file(s) to final destination.")
        else:
            log("Skipping file move for cancelled/failed job.")

        temp_archive_path = os.path.join(temp_dir_path, "archive.temp.txt")
        if os.path.exists(temp_archive_path):
            final_archive_path = os.path.join(final_dest_dir, "archive.txt")
            try:
                os.makedirs(final_dest_dir, exist_ok=True)
                shutil.move(temp_archive_path, final_archive_path)
                log(f"Successfully updated archive file at: {final_archive_path}")
            except Exception as e:
                log(f"ERROR: Could not move updated archive file: {e}")

        if is_playlist:
            final_title = final_folder_name
        elif moved_count == 1 and final_filenames:
            final_filename = final_filenames[0]
            final_title = os.path.splitext(final_filename)[0]
        elif moved_count > 1 and not is_playlist:
            final_filename = None
            final_title = os.path.splitext(final_filenames[0])[0]
        
        if moved_count > 0 and final_status == "FAILED":
            final_status = "PARTIAL"
            log("Status updated to PARTIAL due to partial success.")

        if os.path.exists(temp_dir_path):
            try:
                shutil.rmtree(temp_dir_path)
                log(f"Successfully removed temporary directory: {temp_dir_path}")
            except OSError as e:
                log(f"Error removing temp folder {temp_dir_path}: {e}")

    return final_status, final_folder_name, final_title, final_filename


# --- Main Worker Thread --- #

def yt_dlp_worker(state_manager, config, log_dir, cookie_file_path):
    while True:
        state_manager.queue_paused_event.wait()

        job = state_manager.queue.get()
        state_manager.cancel_event.clear()
        state_manager.stop_mode = "CANCEL"
        
        status = "PENDING"
        temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
        temp_log_path = os.path.join(log_dir, f"job_active_{job['id']}.log")

        state_manager.update_current_download({
            "url": job["url"], "progress": 0, "status": "Preparing...",
            "title": job.get("folder") or job["url"], "job_data": job,
            "log_path": temp_log_path
        })

        try:
            # ##-- UX FIX: Fetch title and thumbnail if not provided --##
            if not job.get("folder"):
                try:
                    print(f"WORKER: No folder name for job {job['id']}, fetching details...")
                    state_manager.update_current_download({"status": "Fetching details..."})
                    
                    is_playlist = "playlist?list=" in job["url"]
                    
                    # Fetch both title and thumbnail in one command
                    fetch_cmd = ['yt-dlp', '--print', '%(title)s\n%(thumbnail)s', '--playlist-items', '1', '-s', job["url"]]
                    if is_playlist:
                        fetch_cmd = ['yt-dlp', '--print', '%(playlist_title)s\n%(thumbnail)s', '--playlist-items', '1', '-s', job["url"]]

                    result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30, check=True)
                    
                    output_lines = result.stdout.strip().splitlines()
                    
                    title = "Misc Downloads"
                    thumbnail_url = None

                    if len(output_lines) >= 1:
                        title = sanitize_filename(output_lines[0])
                    if len(output_lines) >= 2:
                        thumbnail_url = output_lines[1]

                    job["folder"] = title
                    print(f"WORKER: Fetched folder name: {title}")
                    state_manager.update_current_download({"title": title, "thumbnail": thumbnail_url})

                except Exception as e:
                    print(f"WORKER: Could not auto-fetch details for job {job['id']}: {e}")
                    job["folder"] = "Misc Downloads"
                    state_manager.update_current_download({"title": job["folder"]})
            
            os.makedirs(temp_dir_path, exist_ok=True)
            
            if job.get("archive"):
                final_folder_name = job.get("folder")
                main_archive_file = os.path.join(config["download_dir"], final_folder_name, "archive.txt")
                if os.path.exists(main_archive_file):
                    temp_archive_file = os.path.join(temp_dir_path, "archive.temp.txt")
                    shutil.copy2(main_archive_file, temp_archive_file)

            cmd = build_yt_dlp_command(job, temp_dir_path, cookie_file_path)
            
            state_manager.update_current_download({"status": "Starting..."})

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            
            local_playlist_index = 0
            local_playlist_count = 0
            
            with open(temp_log_path, 'w', encoding='utf-8') as log_file:
                log_file.write(f"Starting download for job {job['id']}\n")
                log_file.write(f"Folder: {job.get('folder')}\n")
                log_file.write(f"Command: {' '.join(shlex.quote(c) for c in cmd)}\n\n")
                log_file.flush()

                for line in iter(process.stdout.readline, ''):
                    if state_manager.cancel_event.is_set():
                        process.kill()
                        break
                    line = line.strip()
                    log_file.write(line + '\n')
                    log_file.flush()
                    
                    if line.startswith('{'):
                        try:
                            progress_data = json.loads(line)
                            update = {"status": progress_data.get("status", "Downloading...").capitalize()}
                            
                            if update["status"] == 'Downloading':
                                progress_percent = progress_data.get("_percent_str") or progress_data.get("progress", {}).get("fraction")
                                if progress_percent:
                                    if isinstance(progress_percent, float):
                                        update["progress"] = progress_percent * 100
                                    else:
                                        update["progress"] = float(str(progress_percent).replace('%',''))
                                
                                total_size = progress_data.get("total_bytes") or progress_data.get("total_bytes_estimate")
                                update["file_size"] = format_bytes(total_size)
                                current_track_title = os.path.basename(progress_data.get("filename", "...")).rsplit('.',1)[0]
                                
                                if "playlist?list=" in job.get("url", ""):
                                    update.update({
                                        'playlist_title': job.get("folder"),
                                        'track_title': current_track_title,
                                        'playlist_index': local_playlist_index,
                                        'playlist_count': local_playlist_count
                                    })
                                else:
                                    update["title"] = current_track_title
                                
                                update.update({"speed": progress_data.get("_speed_str", "N/A"), "eta": progress_data.get("_eta_str", "N/A")})
                            
                            state_manager.update_current_download(update)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
                    elif '[download] Downloading item' in line:
                        match = re.search(r'Downloading item (\d+) of (\d+)', line)
                        if match: 
                            local_playlist_index = int(match.group(1))
                            local_playlist_count = int(match.group(2))
                    elif any(s in line for s in ("[ExtractAudio]", "[Merger]")):
                        state_manager.update_current_download({"status": 'Processing...'})

            process.wait() 
            
            if state_manager.cancel_event.is_set():
                status = "STOPPED" if state_manager.stop_mode == "SAVE" else "CANCELLED"
            elif process.returncode == 0:
                status = "COMPLETED"
            else:
                status = "FAILED"

        except Exception as e:
            status = "ERROR"
            print(f"WORKER EXCEPTION for job {job.get('id')}: {e}")
            try:
                with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n--- WORKER THREAD EXCEPTION ---\n{e}\n-------------------------------\n")
            except:
                pass
        
        finally:
            status, final_folder, final_title, final_filename = _finalize_job(job, status, temp_log_path, config)
            
            state_manager.reset_current_download()
            time.sleep(0.2) 

            next_log_id = state_manager.next_log_id
            final_log_path = os.path.join(log_dir, f"job_{next_log_id}.log")
            
            log_path_for_history = "LOG_SAVE_ERROR"
            try:
                shutil.move(temp_log_path, final_log_path)
                log_path_for_history = final_log_path
            except Exception as e:
                print(f"ERROR: Could not rename log file {temp_log_path} to {final_log_path}: {e}")

            history_item = {
                "url": job["url"], 
                "title": final_title, 
                "folder": final_folder, 
                "filename": final_filename,
                "job_data": job, 
                "status": status,
                "log_path": log_path_for_history
            }
            state_manager.add_to_history(history_item)

            state_manager.queue.task_done()

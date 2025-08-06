# lib/worker.py
import subprocess
import os
import re
import json
import datetime
import shutil
import time
import shlex 
from collections import deque
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

# --- Command Builder --- #

def _get_music_args(job, final_folder_name, is_playlist):
    """Builds the yt-dlp argument list for 'music' mode."""
    album_metadata = final_folder_name if is_playlist else '%(album)s'
    
    safe_album_metadata = album_metadata.replace('"', "'")
    
    return [
        '-f', 'bestaudio', '-x', '--audio-format', job.get("format", "mp3"),
        '--audio-quality', job.get("quality", "0"), '--embed-metadata', '--embed-thumbnail',
        '--postprocessor-args', f'-metadata album="{safe_album_metadata}" -metadata date="{datetime.datetime.now().year}"',
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

def build_yt_dlp_command(job, temp_dir_path, cookie_file_path, yt_dlp_path, ffmpeg_path):
    """
    Constructs the full yt-dlp command line argument list for a given download job.
    """
    cmd = [yt_dlp_path]
    mode = job.get("mode")
    is_playlist = "playlist?list=" in job.get("url", "")
    
    final_folder_name = sanitize_filename(job.get("folder")) or "Misc Downloads"

    cmd.extend(['--sleep-interval', '3', '--max-sleep-interval', '10'])
    cmd.extend(['--ffmpeg-location', os.path.dirname(ffmpeg_path)])
    if job.get('proxy'):
        cmd.extend(['--proxy', job['proxy']])
    if job.get('rate_limit'):
        cmd.extend(['--limit-rate', job['rate_limit']])

    if mode == 'music':
        cmd.extend(_get_music_args(job, final_folder_name, is_playlist))
    elif mode == 'video':
        cmd.extend(_get_video_args(job))
    elif mode == 'clip':
        cmd.extend(_get_clip_args(job))
    elif mode == 'custom':
        cmd.extend(shlex.split(job.get('custom_args', '')))

    if mode != 'custom' or ('-o' not in cmd and '--output' not in cmd):
        output_template = os.path.join(temp_dir_path, 
            "%(playlist_index)s - %(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s")
        cmd.extend(['-o', output_template])
    
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
    """
    Handles moving files, cleaning up, and determining the final state for a job.
    """
    temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
    final_folder_name = sanitize_filename(job.get("folder")) or "Misc Downloads"
    final_dest_dir = os.path.join(config["download_dir"], final_folder_name)
    
    final_title = "Unknown"
    final_filenames = []
    error_summary = None

    # Use a try-finally block to ensure the log file is closed.
    try:
        log_file = open(temp_log_path, 'a', encoding='utf-8')
        def log(message):
            log_file.write(message + '\n')

        log(f"Finalizing job with status: {final_status}...")
        
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
                        except Exception as e:
                            log(f"ERROR: Could not move completed file {f}: {e}")
                    log(f"Moved {len(final_filenames)} file(s) to final destination: {final_dest_dir}")
            
            temp_archive_path = os.path.join(temp_dir_path, "archive.temp.txt")
            if os.path.exists(temp_archive_path):
                final_archive_path = os.path.join(final_dest_dir, "archive.txt")
                try:
                    os.makedirs(final_dest_dir, exist_ok=True)
                    shutil.move(temp_archive_path, final_archive_path)
                    log(f"Successfully updated archive file at: {final_archive_path}")
                except Exception as e:
                    log(f"ERROR: Could not move updated archive file: {e}")
        else:
            log("Skipping file and archive move for cancelled/failed job.")

        final_title = final_folder_name

        if final_status == "FAILED" and final_filenames:
            final_status = "PARTIAL"
            log("Status updated to PARTIAL due to partial success.")
        
        if final_status in ["FAILED", "ERROR", "ABANDONED"]:
            final_title = job.get("folder") or job.get("url")
            try:
                # Re-open in read mode to get last lines
                log_file.flush()
                with open(temp_log_path, 'r', encoding='utf-8') as f_read:
                    last_lines = deque(f_read, 15) 
                error_summary = "\n".join(last_lines)
            except Exception as e:
                error_summary = f"Could not read log file to get error summary: {e}"

    finally:
        if 'log_file' in locals() and not log_file.closed:
            log_file.close()

    # --- CHANGE: Perform cleanup outside the log file write lock ---
    if os.path.exists(temp_dir_path):
        attempts = 0
        max_attempts = 5
        while attempts < max_attempts:
            try:
                shutil.rmtree(temp_dir_path)
                print(f"Successfully removed temporary directory: {temp_dir_path}")
                break
            except OSError as e:
                attempts += 1
                print(f"Warning: Attempt {attempts}/{max_attempts} to remove temp folder failed: {e}")
                if attempts >= max_attempts:
                    print(f"ERROR: Could not remove temp folder {temp_dir_path} after {max_attempts} attempts.")
                else:
                    time.sleep(0.5)

    return final_status, final_folder_name, final_title, final_filenames, error_summary


# --- Main Worker Thread --- #

def yt_dlp_worker(state_manager, config, log_dir, cookie_file_path, yt_dlp_path, ffmpeg_path):
    while True:
        state_manager.queue_paused_event.wait()

        job = state_manager.queue.get()
        state_manager.cancel_event.clear()
        state_manager.stop_mode = "CANCEL"
        
        status = "PENDING"
        temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
        temp_log_path = os.path.join(log_dir, f"job_active_{job['id']}.log")

        if job.get("status") == "ABANDONED":
            status = "ABANDONED"
            final_status, final_folder, final_title, final_filenames, error_summary = _finalize_job(job, status, temp_log_path, config)
            history_item = {
                "url": job["url"], "title": final_title, "folder": final_folder, 
                "filenames": [], "job_data": job, "status": status,
                "log_path": "No log generated for abandoned job.",
                "error_summary": "Job was interrupted by an application restart."
            }
            state_manager.add_to_history(history_item)
            state_manager.queue.task_done()
            continue
        
        state_manager.update_current_download({
            "url": job["url"], "progress": 0, "status": "Preparing...",
            "title": job.get("folder") or job["url"], "job_data": job,
            "log_path": temp_log_path
        })

        try:
            state_manager.update_current_download({"status": "Fetching details..."})
            
            fetch_cmd = [yt_dlp_path, '--dump-single-json', '--playlist-items', '1', job["url"]]
            result = subprocess.run(fetch_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45, check=True)
            
            metadata = json.loads(result.stdout)

            with open(temp_log_path, 'w', encoding='utf-8') as log_file:
                log_file.write(f"--- Metadata for Job {job['id']} ---\n")
                json.dump(metadata, log_file, indent=4)
                log_file.write("\n-----------------------------------\n\n")

            if not job.get("folder"):
                title_to_use = metadata.get('playlist_title') or metadata.get('title')
                job["folder"] = sanitize_filename(title_to_use)

            is_playlist = "playlist?list=" in job.get("url", "")
            update_data = {"thumbnail": metadata.get('thumbnail')}
            if is_playlist:
                update_data["playlist_title"] = job["folder"]
            else:
                update_data["title"] = job["folder"]
            state_manager.update_current_download(update_data)
            
            os.makedirs(temp_dir_path, exist_ok=True)
            
            if job.get("archive"):
                main_archive_file = os.path.join(config["download_dir"], job["folder"], "archive.txt")
                if os.path.exists(main_archive_file):
                    shutil.copy2(main_archive_file, os.path.join(temp_dir_path, "archive.temp.txt"))

            cmd = build_yt_dlp_command(job, temp_dir_path, cookie_file_path, yt_dlp_path, ffmpeg_path)
            
            state_manager.update_current_download({"status": "Starting..."})

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            
            local_playlist_index = 0
            local_playlist_count = 0
            
            with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"Starting download for job {job['id']}\n")
                log_file.write(f"Final Folder Name: {job.get('folder')}\n")
                log_file.write(f"Command: {' '.join(shlex.quote(c) for c in cmd)}\n\n")
                log_file.flush()

                for line in iter(process.stdout.readline, ''):
                    if state_manager.cancel_event.is_set():
                        break

                    line = line.strip()
                    log_file.write(line + '\n')
                    log_file.flush()
                    
                    if line.startswith('{'):
                        try:
                            progress_data = json.loads(line)
                            update = {"status": progress_data.get("status", "Downloading...").capitalize()}
                            
                            if update["status"] == 'Downloading':
                                progress_percent = progress_data.get("_percent_str")
                                if progress_percent:
                                    update["progress"] = float(str(progress_percent).replace('%',''))
                                
                                total_size = progress_data.get("total_bytes") or progress_data.get("total_bytes_estimate")
                                update["file_size"] = format_bytes(total_size)
                                current_track_title = os.path.basename(progress_data.get("filename", "...")).rsplit('.',1)[0]
                                
                                if "playlist?list=" in job.get("url", ""):
                                    update.update({
                                        'track_title': current_track_title,
                                        'playlist_index': local_playlist_index,
                                        'playlist_count': local_playlist_count
                                    })
                                
                                update.update({
                                    "speed": progress_data.get("_speed_str", "N/A"), 
                                    "eta": progress_data.get("_eta_str", "N/A")
                                })
                            state_manager.update_current_download(update)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
                    elif '[download] Downloading item' in line:
                        match = re.search(r'Downloading item (\d+) of (\d+)', line)
                        if match: 
                            local_playlist_index = int(match.group(1))
                            local_playlist_count = int(match.group(2))
                    elif any(s in line for s in ("[ExtractAudio]", "[Merger]", "[Fixup")) :
                        state_manager.update_current_download({"status": 'Processing...'})

            if state_manager.cancel_event.is_set() and process.poll() is None:
                process.kill()

            process.wait() 
            
            if state_manager.cancel_event.is_set():
                status = "STOPPED" if state_manager.stop_mode == "SAVE" else "CANCELLED"
            elif process.returncode == 0:
                status = "COMPLETED"
            else:
                status = "FAILED"

        except subprocess.CalledProcessError as e:
            status = "ERROR"
            print(f"WORKER METADATA FETCH FAILED for job {job.get('id')}: {e.stderr}")
            try:
                with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n--- METADATA FETCH FAILED ---\n{e.stderr}\n---------------------------\n")
            except: pass
        except Exception as e:
            status = "ERROR"
            print(f"WORKER EXCEPTION for job {job.get('id')}: {e}")
            try:
                with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n--- WORKER THREAD EXCEPTION ---\n{e}\n-------------------------------\n")
            except: pass
        
        finally:
            status, final_folder, final_title, final_filenames, error_summary = _finalize_job(job, status, temp_log_path, config)
            
            state_manager.reset_current_download()
            time.sleep(0.2) 

            next_log_id = state_manager.next_log_id
            final_log_path = os.path.join(log_dir, f"job_{next_log_id}.log")
            
            log_path_for_history = "LOG_SAVE_ERROR"
            try:
                if os.path.exists(temp_log_path):
                    shutil.move(temp_log_path, final_log_path)
                    log_path_for_history = final_log_path
            except Exception as e:
                print(f"ERROR: Could not rename log file {temp_log_path} to {final_log_path}: {e}")

            history_item = {
                "url": job["url"], 
                "title": final_title, 
                "folder": final_folder, 
                "filenames": final_filenames,
                "job_data": job, 
                "status": status,
                "log_path": log_path_for_history,
                "error_summary": error_summary
            }
            state_manager.add_to_history(history_item)

            state_manager.queue.task_done()

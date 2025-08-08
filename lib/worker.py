# lib/worker.py
import subprocess
import os
import re
import json
import datetime
import shutil
import time
import shlex
import threading
import queue
import platform
import signal
from .sanitizer import sanitize_filename

# --- Helper Functions ---

def format_bytes(b):
    """Formats bytes into a human-readable string (KiB, MiB, GiB)."""
    if b is None: return "N/A"
    try:
        b = float(b)
        if b < 1024: return f"{b:.0f} B"
        if b < 1024**2: return f"{b/1024:.2f} KiB"
        if b < 1024**3: return f"{b/1024**2:.2f} MiB"
        return f"{b/1024**3:.2f} GiB"
    except (ValueError, TypeError):
        return "N/A"

def _enqueue_output(stream, q):
    """Reads decoded text lines from a stream and puts them into a queue."""
    for line in iter(stream.readline, ''):
        q.put(line)
    stream.close()

# --- Command Builder ---

def _get_music_args(job, is_playlist):
    """Builds the yt-dlp argument list for 'music' mode."""
    args = [
        '-f', 'bestaudio/best', '-x', '--audio-format', job.get("format", "mp3"),
        '--audio-quality', job.get("quality", "0"), 
        '--embed-metadata', '--embed-thumbnail',
        '--parse-metadata', 'playlist_index:%(track_number)s',
        '--parse-metadata', 'uploader:%(artist)s'
    ]
    if job.get("folder"):
        safe_album_metadata = job.get("folder").replace('"', "'")
        args.extend(['--metadata', f'album={safe_album_metadata}'])
    elif is_playlist:
        args.extend(['--parse-metadata', 'playlist_title:%(album)s'])
    args.extend(['--postprocessor-args', f'FFmpegMetadata:-metadata date="{datetime.datetime.now().year}"'])
    return args

def _get_video_args(job):
    """Builds the yt-dlp argument list for 'video' mode."""
    quality = job.get('quality', 'best')
    video_format = job.get('format', 'mp4')
    codec_pref = job.get('codec', 'compatibility')
    quality_filter = f"[height<={quality[:-1]}]" if quality.endswith('p') else ""
    
    if codec_pref == 'compatibility':
        format_str = f"bestvideo{quality_filter}[vcodec^=avc]+bestaudio[acodec^=m4a]/bestvideo{quality_filter}+bestaudio/best"
    else: # 'quality'
        format_str = f"bestvideo{quality_filter}+bestaudio/best"
        
    args = ['-f', format_str, '--merge-output-format', video_format]
    if job.get('embed_subs'):
        args.extend(['--embed-subs', '--sub-langs', 'en.*,en-US,en-GB'])
    return args

def _get_clip_args(job):
    """Builds the yt-dlp argument list for 'clip' mode (quick downloads)."""
    if job.get('format') == 'audio':
        return ['-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', '0']
    else:
        return ['-f', 'bestvideo+bestaudio/best', '--merge-output-format', 'mp4']

def build_yt_dlp_command(job, temp_dir_path, cookie_file_path, yt_dlp_path, ffmpeg_path):
    """Constructs the full yt-dlp command line argument list for a given download job."""
    cmd = [yt_dlp_path]
    mode = job.get("mode")
    is_playlist = "playlist?list=" in job.get("url", "")
    
    # Basic settings
    cmd.extend(['--sleep-interval', '3', '--max-sleep-interval', '10'])
    cmd.extend(['--ffmpeg-location', os.path.dirname(ffmpeg_path)])
    
    # Optional settings
    if job.get('proxy'): cmd.extend(['--proxy', job['proxy']])
    if job.get('rate_limit'): cmd.extend(['--limit-rate', job['rate_limit']])
    
    # Mode-specific settings
    if mode == 'music': cmd.extend(_get_music_args(job, is_playlist))
    elif mode == 'video': cmd.extend(_get_video_args(job))
    elif mode == 'clip': cmd.extend(_get_clip_args(job))
    elif mode == 'custom': cmd.extend(shlex.split(job.get('custom_args', '')))
    
    # Output and progress settings
    cmd.extend(['--progress', '--progress-template', '%(progress)j', '--print-json'])
    if '-o' not in cmd and '--output' not in cmd:
        output_template = os.path.join(temp_dir_path, 
            "%(playlist_index)s - %(title)s.%(ext)s" if is_playlist and mode == 'music' else "%(title)s.%(ext)s")
        cmd.extend(['-o', output_template])
        
    # Playlist settings
    start, end = job.get("playlist_start"), job.get("playlist_end")
    if start and end: cmd.extend(['--playlist-items', f'{start}-{end}'])
    elif start: cmd.extend(['--playlist-items', f'{start}:'])
    elif end: cmd.extend(['--playlist-items', f':{end}'])
    if is_playlist: cmd.append('--ignore-errors')
    
    # Authentication and archive
    if os.path.exists(cookie_file_path) and os.path.getsize(cookie_file_path) > 0:
        cmd.extend(['--cookies', cookie_file_path])
    if job.get("archive"):
        cmd.extend(['--download-archive', os.path.join(temp_dir_path, "archive.temp.txt")])
        
    cmd.append(job["url"])
    return cmd

# --- Finalization and Cleanup ---

def _generate_error_summary(log_path):
    """Creates a more intelligent error summary from the log file."""
    error_lines = []
    try:
        if not os.path.exists(log_path):
            return "Log file was not created."
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if "ERROR:" in line or "WARNING:" in line:
                    cleaned_line = re.sub(r'^\[yt-dlp\]\s*', '', line).strip()
                    if cleaned_line:
                        error_lines.append(cleaned_line)
    except Exception as e:
        return f"Could not read log file to generate error summary: {e}"
        
    if not error_lines:
        return "No specific errors found in log. The process may have been terminated unexpectedly."
    # Return the last 10 error/warning lines for brevity
    return "\n".join(error_lines[-10:])


def _finalize_job(job, final_status, temp_log_path, config, resolved_folder_name):
    """Handles moving files, cleaning up, and determining the final state for a job."""
    temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
    final_folder_name = sanitize_filename(resolved_folder_name) or "Misc Downloads"
    final_dest_dir = os.path.join(config["download_dir"], final_folder_name)
    final_filenames = []
    error_summary = None

    try:
        if not os.path.exists(temp_log_path):
             open(temp_log_path, 'a').close() # Create empty log if it doesn't exist

        with open(temp_log_path, 'a', encoding='utf-8') as log_file:
            def log(message): log_file.write(message + '\n')
            log(f"\n--- Finalizing job with status: {final_status} ---")
            
            if os.path.exists(temp_dir_path):
                # Determine the expected file extension based on the job mode
                target_ext = None
                mode = job.get('mode')
                if mode == 'music': target_ext = job.get('format', 'mp3')
                elif mode == 'video': target_ext = job.get('format', 'mp4')
                elif mode == 'clip': target_ext = 'mp3' if job.get('format') == 'audio' else 'mp4'

                files_in_temp = os.listdir(temp_dir_path)
                files_to_move = []

                if target_ext:
                    # If we know the extension, only move those files
                    files_to_move = [f for f in files_in_temp if f.endswith(f'.{target_ext}')]
                else: # For custom mode, move everything that isn't a temp file
                    files_to_move = [f for f in files_in_temp if not f.endswith('.temp.txt')]

                if files_to_move:
                    os.makedirs(final_dest_dir, exist_ok=True)
                    log(f"Moving {len(files_to_move)} file(s) to: {final_dest_dir}")
                    for f in files_to_move:
                        source_path = os.path.join(temp_dir_path, f)
                        safe_f = sanitize_filename(f)
                        dest_path = os.path.join(final_dest_dir, safe_f)
                        try:
                            shutil.move(source_path, dest_path)
                            final_filenames.append(safe_f)
                        except Exception as e:
                            log(f"ERROR: Could not move file {f}: {e}")
            
            # --- CHANGE: Always move the archive file to preserve progress ---
            temp_archive_path = os.path.join(temp_dir_path, "archive.temp.txt")
            if os.path.exists(temp_archive_path):
                final_archive_path = os.path.join(final_dest_dir, "archive.txt")
                try:
                    os.makedirs(final_dest_dir, exist_ok=True)
                    shutil.move(temp_archive_path, final_archive_path)
                    log(f"Moved archive file to: {final_archive_path}")
                except Exception as e:
                    log(f"ERROR: Could not move archive file: {e}")

            # If the job failed but some files were moved, mark it as PARTIAL
            if final_status == "FAILED" and final_filenames:
                final_status = "PARTIAL"
                log("Status updated to PARTIAL due to partial success.")
            
            if final_status in ["FAILED", "ERROR", "ABANDONED", "PARTIAL"]:
                error_summary = _generate_error_summary(temp_log_path)

    except Exception as e:
        print(f"ERROR during job finalization: {e}")
        error_summary = f"A critical error occurred during job finalization: {e}"
    
    # Cleanup the temporary job directory
    if os.path.exists(temp_dir_path):
        try:
            shutil.rmtree(temp_dir_path)
        except OSError as e:
            print(f"ERROR: Could not remove temp folder {temp_dir_path}: {e}")

    return final_status, final_folder_name, final_filenames, error_summary

# --- Worker Sub-functions ---

def _prepare_job_environment(job, config, log_dir):
    """Creates directories and copies archive files needed for the job."""
    temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
    temp_log_path = os.path.join(log_dir, f"job_active_{job['id']}.log")
    os.makedirs(temp_dir_path, exist_ok=True)
    
    if job.get("archive"):
        # Use the resolved folder name if available (for continuing downloads)
        folder_name = sanitize_filename(job.get("resolved_folder") or job.get("folder")) or "Misc Downloads"
        main_archive_file = os.path.join(config["download_dir"], folder_name, "archive.txt")
        if os.path.exists(main_archive_file):
            try:
                shutil.copy2(main_archive_file, os.path.join(temp_dir_path, "archive.temp.txt"))
            except Exception as e:
                print(f"Warning: Could not copy existing archive file: {e}")
            
    return temp_dir_path, temp_log_path

def _process_yt_dlp_output(line, state_manager, job):
    """
    Parses a line of output from yt-dlp, updates the state manager,
    and returns a resolved title if one is found.
    """
    line = line.strip()
    if not line: return None
    
    if line.startswith('{'): # It's a JSON progress or info line
        try:
            data = json.loads(line)
            if data.get("status") == "downloading":
                update = {"status": "Downloading"}
                downloaded = data.get("downloaded_bytes")
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                if downloaded is not None and total is not None and total > 0:
                    update["progress"] = (downloaded / total) * 100
                update["file_size"] = format_bytes(total)
                speed = data.get("speed")
                update["speed"] = f'{format_bytes(speed)}/s' if speed else "N/A"
                eta = data.get("eta")
                update["eta"] = time.strftime('%M:%S', time.gmtime(eta)) if eta is not None else "N/A"
                state_manager.update_current_download(update)
            elif data.get("status") == "finished":
                state_manager.update_current_download({"status": "Processing..."})
            elif data.get('_type') == 'video': # It's an info JSON for a video
                resolved_title = sanitize_filename(data.get('playlist_title') or data.get('title', 'Unknown Title'))
                update = {
                    "status": "Starting...", "progress": 0, "thumbnail": data.get('thumbnail'),
                    "playlist_index": data.get('playlist_index'), "playlist_count": data.get('n_entries'),
                    "playlist_title": resolved_title if data.get('playlist_index') else None,
                    "track_title": data.get('title'),
                    "title": job.get("folder") or resolved_title
                }
                state_manager.update_current_download(update)
                # If the user didn't specify a folder, we use the resolved title
                if not job.get("folder"):
                    return resolved_title
        except (json.JSONDecodeError, TypeError, ValueError):
            pass # Ignore lines that are not valid JSON
            
    elif any(s in line for s in ("[ExtractAudio]", "[Merger]", "[Fixup")):
        state_manager.update_current_download({"status": 'Processing...'})
        
    return None


def _run_download_process(state_manager, job, cmd, temp_log_path):
    """
    Runs the yt-dlp subprocess, captures its output, and returns the
    final status and the resolved folder name.
    """
    status = "PENDING"
    resolved_folder_name = job.get("folder")
    
    # Use CREATE_NEW_PROCESS_GROUP on Windows to allow sending CTRL_BREAK
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0
    
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
        creationflags=creationflags, bufsize=1
    )
    
    # Use a thread to read from the process output without blocking
    output_q = queue.Queue()
    reader_thread = threading.Thread(target=_enqueue_output, args=(process.stdout, output_q), daemon=True)
    reader_thread.start()

    with open(temp_log_path, 'w', encoding='utf-8') as log_file:
        safe_cmd_str = ' '.join(shlex.quote(c) for c in cmd)
        log_file.write(f"--- Job {job['id']} Started ---\nCommand: {safe_cmd_str}\n\n")
        log_file.flush()
        
        while process.poll() is None:
            if state_manager.cancel_event.is_set():
                break
            try:
                line = output_q.get(timeout=0.1)
                log_file.write(line)
                log_file.flush()
                newly_resolved_title = _process_yt_dlp_output(line, state_manager, job)
                if not resolved_folder_name and newly_resolved_title:
                    resolved_folder_name = newly_resolved_title
            except queue.Empty:
                continue
        
        # Ensure all remaining output is drained from the queue
        while not output_q.empty():
            line = output_q.get_nowait()
            if line: log_file.write(line)

    # Handle cancellation
    if state_manager.cancel_event.is_set() and process.poll() is None:
        try:
            if platform.system() == "Windows":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(f"Process {process.pid} did not terminate gracefully. Killing.")
            process.kill()
            process.wait()
        except Exception as e:
            print(f"Error during process termination: {e}")
            process.kill()
            process.wait()

    # Determine final status
    if state_manager.cancel_event.is_set():
        status = "STOPPED" if state_manager.stop_mode == "SAVE" else "CANCELLED"
    elif process.returncode == 0:
        status = "COMPLETED"
    else:
        status = "FAILED"
        
    return status, resolved_folder_name

# --- Main Worker Thread ---

def yt_dlp_worker(state_manager, config, log_dir, cookie_file_path, yt_dlp_path, ffmpeg_path, stop_event):
    """The main worker loop that processes jobs from the queue."""
    print("Worker thread started.")
    while not stop_event.is_set():
        state_manager.queue_paused_event.wait() # This will block if the queue is paused
        
        try:
            job = state_manager.queue.get(timeout=1)
            if job is None: # Sentinel value to exit
                break
        except queue.Empty:
            continue
            
        state_manager.cancel_event.clear()
        state_manager.stop_mode = "CANCEL"
        
        state_manager.update_current_download({
            "url": job["url"], "progress": 0, "status": "Preparing...",
            "title": job.get("folder") or job["url"], "job_data": job
        })
        
        status = "PENDING"
        temp_log_path = ""
        resolved_folder_name = job.get("folder")
        
        try:
            temp_dir_path, temp_log_path = _prepare_job_environment(job, config, log_dir)
            state_manager.update_current_download({"log_path": temp_log_path})
            
            cmd = build_yt_dlp_command(job, temp_dir_path, cookie_file_path, yt_dlp_path, ffmpeg_path)
            
            status, resolved_folder_name = _run_download_process(state_manager, job, cmd, temp_log_path)
            
        except Exception as e:
            status = "ERROR"
            print(f"WORKER EXCEPTION for job {job.get('id')}: {e}")
            if temp_log_path and os.path.exists(temp_log_path):
                try:
                    with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                        log_file.write(f"\n--- WORKER THREAD EXCEPTION ---\n{e}\n-------------------------------\n")
                except: pass
        finally:
            final_status, final_folder, final_filenames, error_summary = _finalize_job(job, status, temp_log_path, config, resolved_folder_name)
            
            state_manager.reset_current_download()
            
            # Move the temporary log to its final destination
            log_id_for_file = state_manager.add_to_history({}, save=False) # Get next ID without saving
            final_log_path = os.path.join(log_dir, f"job_{log_id_for_file}.log")
            log_path_for_history = "LOG_SAVE_ERROR"
            try:
                if os.path.exists(temp_log_path):
                    shutil.move(temp_log_path, final_log_path)
                    log_path_for_history = final_log_path
            except Exception as e:
                print(f"ERROR: Could not move log file {temp_log_path}: {e}")
            
            # Create the final history item
            history_item = {
                "log_id": log_id_for_file,
                "url": job["url"],
                "title": final_folder or job.get("url"),
                "folder": final_folder,
                "filenames": final_filenames,
                "job_data": job,
                "status": final_status,
                "log_path": log_path_for_history,
                "error_summary": error_summary
            }
            
            # Update the placeholder history item with the real data
            state_manager.update_history_item(log_id_for_file, history_item)
            
            state_manager.queue.task_done()
            
    print("Worker thread has gracefully exited.")

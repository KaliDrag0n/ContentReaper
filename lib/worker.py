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
from collections import deque
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

# --- Non-Blocking Stream Reader ---
def _enqueue_output(stream, q):
    """
    Reads decoded text lines from a stream and puts them into a queue.
    """
    for line in iter(stream.readline, ''):
        q.put(line)
    stream.close()

# --- Command Builder ---

def _get_music_args(job, final_folder_name, is_playlist):
    """Builds the yt-dlp argument list for 'music' mode."""
    album_metadata = final_folder_name if is_playlist else '%(album)s'
    safe_album_metadata = album_metadata.replace('"', "'")
    
    return [
        '-f', 'bestaudio/best', '-x', '--audio-format', job.get("format", "mp3"),
        '--audio-quality', job.get("quality", "0"), '--embed-metadata', '--embed-thumbnail',
        '--ppa', f'FFmpegMetadata:-metadata album="{safe_album_metadata}"',
        '--ppa', f'FFmpegMetadata:-metadata date="{datetime.datetime.now().year}"',
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
    final_folder_name = sanitize_filename(job.get("folder")) or "Misc Downloads"

    cmd.extend(['--sleep-interval', '3', '--max-sleep-interval', '10'])
    cmd.extend(['--ffmpeg-location', os.path.dirname(ffmpeg_path)])
    if job.get('proxy'): cmd.extend(['--proxy', job['proxy']])
    if job.get('rate_limit'): cmd.extend(['--limit-rate', job['rate_limit']])

    if mode == 'music': cmd.extend(_get_music_args(job, final_folder_name, is_playlist))
    elif mode == 'video': cmd.extend(_get_video_args(job))
    elif mode == 'clip': cmd.extend(_get_clip_args(job))
    elif mode == 'custom': cmd.extend(shlex.split(job.get('custom_args', '')))

    cmd.append('--progress')
    cmd.extend(['--progress-template', '%(progress)j'])
    cmd.append('--print-json') 

    if '-o' not in cmd and '--output' not in cmd:
        output_template = os.path.join(temp_dir_path, 
            "%(playlist_index)s - %(title)s.%(ext)s" if is_playlist else "%(title)s.%(ext)s")
        cmd.extend(['-o', output_template])
    
    start, end = job.get("playlist_start"), job.get("playlist_end")
    if start and end: cmd.extend(['--playlist-items', f'{start}-{end}'])
    elif start: cmd.extend(['--playlist-items', f'{start}:'])
    elif end: cmd.extend(['--playlist-items', f':{end}'])

    if is_playlist: cmd.append('--ignore-errors')
    if os.path.exists(cookie_file_path) and os.path.getsize(cookie_file_path) > 0:
        cmd.extend(['--cookies', cookie_file_path])
    if job.get("archive"):
        cmd.extend(['--download-archive', os.path.join(temp_dir_path, "archive.temp.txt")])

    cmd.append(job["url"])
    return cmd

# --- Finalization and Cleanup ---

def _finalize_job(job, final_status, temp_log_path, config):
    """Handles moving files, cleaning up, and determining the final state for a job."""
    temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
    final_folder_name = sanitize_filename(job.get("folder")) or "Misc Downloads"
    final_dest_dir = os.path.join(config["download_dir"], final_folder_name)
    
    final_filenames = []
    error_summary = None

    try:
        with open(temp_log_path, 'a', encoding='utf-8') as log_file:
            def log(message): log_file.write(message + '\n')
            log(f"\n--- Finalizing job with status: {final_status} ---")
            
            if final_status in ["COMPLETED", "PARTIAL", "STOPPED"] and os.path.exists(temp_dir_path):
                files_to_move = [f for f in os.listdir(temp_dir_path) if not f.endswith('.temp.txt')]
                if files_to_move:
                    os.makedirs(final_dest_dir, exist_ok=True)
                    for f in files_to_move:
                        source_path = os.path.join(temp_dir_path, f)
                        dest_path = os.path.join(final_dest_dir, f)
                        try:
                            shutil.move(source_path, dest_path)
                            final_filenames.append(f)
                        except Exception as e:
                            log(f"ERROR: Could not move file {f}: {e}")
                    log(f"Moved {len(final_filenames)} file(s) to: {final_dest_dir}")
            
            temp_archive_path = os.path.join(temp_dir_path, "archive.temp.txt")
            if os.path.exists(temp_archive_path):
                final_archive_path = os.path.join(final_dest_dir, "archive.txt")
                try:
                    os.makedirs(final_dest_dir, exist_ok=True)
                    shutil.move(temp_archive_path, final_archive_path)
                    log(f"Moved archive file to: {final_archive_path}")
                except Exception as e:
                    log(f"ERROR: Could not move archive file: {e}")

            if final_status == "FAILED" and final_filenames:
                final_status = "PARTIAL"
                log("Status updated to PARTIAL due to partial success.")
            
            if final_status in ["FAILED", "ERROR", "ABANDONED"]:
                log_file.flush()
                with open(temp_log_path, 'r', encoding='utf-8') as f_read:
                    last_lines = deque(f_read, 15) 
                error_summary = "\n".join(last_lines)
    except Exception as e:
        print(f"ERROR during job finalization: {e}")
        error_summary = f"An error occurred during job finalization: {e}"
    
    if os.path.exists(temp_dir_path):
        try:
            shutil.rmtree(temp_dir_path)
        except OSError as e:
            print(f"ERROR: Could not remove temp folder {temp_dir_path}: {e}")

    return final_status, final_folder_name, final_filenames, error_summary

# --- Main Worker Thread ---

def yt_dlp_worker(state_manager, config, log_dir, cookie_file_path, yt_dlp_path, ffmpeg_path):
    """The main worker loop that processes jobs from the queue."""
    while True:
        state_manager.queue_paused_event.wait()
        job = state_manager.queue.get()
        state_manager.cancel_event.clear()
        state_manager.stop_mode = "CANCEL"
        
        status = "PENDING"
        temp_dir_path = os.path.join(config["temp_dir"], f"job_{job['id']}")
        temp_log_path = os.path.join(log_dir, f"job_active_{job['id']}.log")
        process = None
        
        if job.get("status") == "ABANDONED":
            _, final_folder, _, _ = _finalize_job(job, "ABANDONED", temp_log_path, config)
            history_item = {
                "url": job["url"], "title": job.get("folder") or job.get("url"), "folder": final_folder, 
                "filenames": [], "job_data": job, "status": "ABANDONED",
                "log_path": "No log generated.", "error_summary": "Job was interrupted by an application restart."
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
            os.makedirs(temp_dir_path, exist_ok=True)
            if job.get("archive"):
                main_archive_file = os.path.join(config["download_dir"], sanitize_filename(job.get("folder")), "archive.txt")
                if os.path.exists(main_archive_file):
                    shutil.copy2(main_archive_file, os.path.join(temp_dir_path, "archive.temp.txt"))

            cmd = build_yt_dlp_command(job, temp_dir_path, cookie_file_path, yt_dlp_path, ffmpeg_path)
            
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            output_q = queue.Queue()
            reader_thread = threading.Thread(target=_enqueue_output, args=(process.stdout, output_q))
            reader_thread.daemon = True
            reader_thread.start()

            with open(temp_log_path, 'w', encoding='utf-8') as log_file:
                log_file.write(f"--- Job {job['id']} Started ---\n")
                log_file.write(f"Command: {' '.join(shlex.quote(c) for c in cmd)}\n\n")
                log_file.flush()

                while process.poll() is None:
                    if state_manager.cancel_event.is_set():
                        break
                    try:
                        line = output_q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    
                    line = line.strip()
                    if not line: continue
                    
                    log_file.write(line + '\n')
                    log_file.flush()
                    
                    if line.startswith('{'):
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
                                if eta is not None:
                                    update["eta"] = time.strftime('%M:%S', time.gmtime(eta))
                                else:
                                    update["eta"] = "N/A"
                                state_manager.update_current_download(update)

                            elif data.get("status") == "finished":
                                state_manager.update_current_download({"status": "Processing..."})

                            # --- FIX: Avoid mutating the original job object ---
                            elif '_type' in data and data['_type'] == 'video':
                                folder_title = job.get("folder")
                                if not folder_title:
                                    folder_title = sanitize_filename(data.get('playlist_title') or data.get('title', 'Unknown Title'))
                                
                                update = {
                                    "status": "Starting...", "progress": 0, "thumbnail": data.get('thumbnail'),
                                    "playlist_index": data.get('playlist_index'), "playlist_count": data.get('n_entries'),
                                    "playlist_title": folder_title if data.get('playlist_index') else None,
                                    "track_title": data.get('title'), "title": folder_title
                                }
                                state_manager.update_current_download(update)

                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
                    elif any(s in line for s in ("[ExtractAudio]", "[Merger]", "[Fixup")):
                        state_manager.update_current_download({"status": 'Processing...'})
                
                while not output_q.empty():
                    line = output_q.get_nowait().strip()
                    if line: log_file.write(line + '\n')

            if state_manager.cancel_event.is_set() and process.poll() is None:
                process.kill()

            try:
                process.wait(timeout=3600) 
            except subprocess.TimeoutExpired:
                print(f"ERROR: Process for job {job.get('id')} timed out. Killing process.")
                process.kill()
                process.wait(timeout=10)
                status = "FAILED"
                with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write("\n--- ERROR: Process timed out and was killed. ---\n")

            if status != "FAILED":
                if state_manager.cancel_event.is_set():
                    status = "STOPPED" if state_manager.stop_mode == "SAVE" else "CANCELLED"
                elif process.returncode == 0:
                    status = "COMPLETED"
                else:
                    status = "FAILED"

        except Exception as e:
            status = "ERROR"
            print(f"WORKER EXCEPTION for job {job.get('id')}: {e}")
            if process and process.poll() is None:
                process.kill()
            try:
                with open(temp_log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n--- WORKER THREAD EXCEPTION ---\n{e}\n-------------------------------\n")
            except: pass
        
        finally:
            final_status, final_folder, final_filenames, error_summary = _finalize_job(job, status, temp_log_path, config)
            state_manager.reset_current_download()
            time.sleep(0.2)

            final_log_path = os.path.join(log_dir, f"job_{state_manager.next_log_id}.log")
            log_path_for_history = "LOG_SAVE_ERROR"
            try:
                if os.path.exists(temp_log_path):
                    shutil.move(temp_log_path, final_log_path)
                    log_path_for_history = final_log_path
            except Exception as e:
                print(f"ERROR: Could not move log file {temp_log_path}: {e}")

            history_item = {
                "url": job["url"], "title": job.get("folder") or job.get("url"), "folder": final_folder,
                "filenames": final_filenames, "job_data": job, "status": final_status,
                "log_path": log_path_for_history, "error_summary": error_summary
            }
            state_manager.add_to_history(history_item)
            state_manager.queue.task_done()

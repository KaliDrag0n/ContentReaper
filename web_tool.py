# web_tool.py
import os
import sys
import subprocess
import platform
import logging
from logging.handlers import RotatingFileHandler

# --- Set up logging immediately to catch any startup errors ---
log_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'startup.log')

file_handler = RotatingFileHandler(log_file, maxBytes=1*1024*1024, backupCount=2)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# --- Main Application Block ---
try:
    logger.info("Application starting up...")
    #~ --- Dependency & Startup Logic --- ~#
    try:
        import flask
        import waitress
        import requests
        from werkzeug.security import generate_password_hash, check_password_hash
        logger.info("Core dependencies are present.")
    except ImportError:
        logger.warning("Core Python packages not found. Attempting to install...")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'flask', 'waitress', 'requests', 'werkzeug'])
            logger.info("Dependencies installed successfully. Please restart the application.")
            sys.exit(0)
        except subprocess.CalledProcessError as e:
            logger.critical(f"Failed to install dependencies. Please run 'pip install flask waitress requests werkzeug' manually. Error: {e}")
            sys.exit(1)

    from lib import dependency_manager
    from flask import Flask, request, render_template, jsonify, redirect, url_for, Response, send_file, session
    from functools import wraps
    import threading, json, atexit, time, signal, shutil, io, zipfile, secrets
    from lib.state_manager import StateManager
    from lib.worker import yt_dlp_worker
    from lib.sanitizer import sanitize_filename

    APP_ROOT = os.path.dirname(os.path.abspath(__file__))
    logger.info("--- [1/3] Initializing Dependency Manager ---")
    YT_DLP_PATH, FFMPEG_PATH = dependency_manager.ensure_dependencies(APP_ROOT)

    if not YT_DLP_PATH or not FFMPEG_PATH:
        logger.critical("Application cannot start due to missing critical dependencies (yt-dlp or ffmpeg).")
        if platform.system() == "Windows": os.system("pause")
        sys.exit(1)

    logger.info("--- [2/3] Checking for yt-dlp updates ---")
    try:
        update_command = [YT_DLP_PATH, '-U']
        update_result = subprocess.run(update_command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        logger.info(f"yt-dlp update check stdout: {update_result.stdout.strip()}")
        if update_result.returncode != 0:
            logger.warning(f"yt-dlp update check may have failed. Stderr: {update_result.stderr.strip()}")
    except Exception as e:
        logger.warning(f"An unexpected error occurred while trying to update yt-dlp: {e}")

    logger.info("--- [3/3] Startup checks complete ---")

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)

    #~ --- Configuration --- ~#
    APP_VERSION = "2.0.0" # Final stable version
    GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

    CONF_CONFIG_FILE = os.path.join(APP_ROOT, "config.json")
    CONF_STATE_FILE = os.path.join(APP_ROOT, "state.json")
    CONF_COOKIE_FILE = os.path.join(APP_ROOT, "cookies.txt")
    LOG_DIR = os.path.join(APP_ROOT, "logs")

    CONFIG = {
        "download_dir": os.path.join(APP_ROOT, "downloads"),
        "temp_dir": os.path.join(APP_ROOT, ".temp"),
        "admin_password_hash": None
    }
    PASSWORD_IS_SET = False

    state_manager = StateManager(CONF_STATE_FILE)
    update_status = {
        "update_available": False, "latest_version": "0.0.0",
        "release_url": "", "release_notes": ""
    }

    #~ --- Security & Path Helpers --- ~#
    def password_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not PASSWORD_IS_SET:
                return f(*args, **kwargs)
            if not session.get('is_logged_in'):
                return jsonify({"message": "Authentication required. Please log in."}), 401
            return f(*args, **kwargs)
        return decorated_function

    def is_safe_path(basedir, path):
        return os.path.realpath(path).startswith(os.path.realpath(basedir))

    def validate_config_paths():
        errors = {}
        for key, name in [("download_dir", "Download"), ("temp_dir", "Temporary")]:
            path = CONFIG.get(key)
            if not path or not os.path.isabs(path):
                errors[key] = f"{name} directory path must be an absolute path."
            elif not os.path.exists(path):
                try: os.makedirs(path, exist_ok=True)
                except Exception as e: errors[key] = f"Path does not exist and could not be created: {e}"
            elif not os.path.isdir(path):
                errors[key] = "Path points to a file, not a directory."
            elif not os.access(path, os.W_OK):
                errors[key] = "Application does not have permission to write to this path."
        return errors

    #~ --- Update & Restart Logic --- ~#
    def trigger_update_and_restart():
        state_manager.save_state()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _run_update_check():
        global update_status
        api_url = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
        try:
            res = requests.get(api_url, timeout=15)
            res.raise_for_status()
            latest_release = res.json()
            latest_version_tag = latest_release.get("tag_name", "").lstrip('v')
            with state_manager._lock:
                if latest_version_tag > APP_VERSION:
                    update_status.update({
                        "update_available": True, "latest_version": latest_version_tag,
                        "release_url": latest_release.get("html_url"), "release_notes": latest_release.get("body")
                    })
                else:
                    update_status["update_available"] = False
        except Exception as e:
            logger.warning(f"Update check failed: {e}")

    def scheduled_update_check():
        while True:
            _run_update_check()
            time.sleep(3600)

    #~ --- Config Management --- ~#
    def save_config():
        try:
            config_to_save = CONFIG.copy()
            config_to_save.pop('cookie_file_content', None)
            with open(CONF_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, indent=4)
        except Exception as e: logger.error(f"Failed to save config: {e}")

    def load_config():
        global CONFIG
        if os.path.exists(CONF_CONFIG_FILE):
            try:
                with open(CONF_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    CONFIG.update(json.load(f))
            except Exception as e: logger.warning(f"Could not load config.json, using defaults. Error: {e}")
        
        try:
            if os.path.exists(CONF_COOKIE_FILE):
                with open(CONF_COOKIE_FILE, 'r', encoding='utf-8') as f:
                    CONFIG['cookie_file_content'] = f.read()
            else:
                CONFIG['cookie_file_content'] = ""
        except Exception as e:
            logger.error(f"Could not load cookie file: {e}")
            CONFIG['cookie_file_content'] = ""
        
        save_config()

    #~ --- App Initialization --- ~#
    def initialize_app():
        global PASSWORD_IS_SET
        logger.info("--- Initializing Application ---")
        load_config()
        
        config_errors = validate_config_paths()
        if config_errors:
            logger.critical("Invalid paths found in config.json. Application cannot start.")
            for key, error in config_errors.items(): logger.error(f"  - {key}: {error}")
            raise RuntimeError("Configuration validation failed.")

        if CONFIG.get("admin_password_hash"):
            PASSWORD_IS_SET = True
            logger.info("Admin password is set.")
        else:
            logger.warning("No admin password is set. Please create one in Settings to secure the application.")

        for path in [CONFIG["download_dir"], CONFIG["temp_dir"], LOG_DIR]:
            os.makedirs(path, exist_ok=True)
        
        atexit.register(state_manager.save_state)
        state_manager.load_state()
        
        threading.Thread(target=scheduled_update_check, daemon=True).start()
        threading.Thread(target=yt_dlp_worker, args=(state_manager, CONFIG, LOG_DIR, CONF_COOKIE_FILE, YT_DLP_PATH, FFMPEG_PATH), daemon=True).start()
        logger.info("Background threads started.")
        
        logger.info("--- Application Initialized Successfully ---")

    #~ --- Flask Routes --- ~#
    @app.route("/")
    def index_route():
        return render_template("index.html")

    @app.route("/settings", methods=["GET", "POST"])
    def settings_route():
        config_errors = {}
        if request.method == "POST":
            if not PASSWORD_IS_SET or session.get('is_logged_in'):
                CONFIG["download_dir"] = request.form.get("download_dir", "").strip()
                CONFIG["temp_dir"] = request.form.get("temp_dir", "").strip()
                config_errors = validate_config_paths()
                
                if not config_errors:
                    cookie_content = request.form.get("cookie_content", "")
                    try:
                        with open(CONF_COOKIE_FILE, 'w', encoding='utf-8') as f:
                            f.write(cookie_content)
                        CONFIG['cookie_file_content'] = cookie_content
                    except Exception as e:
                        logger.error(f"Failed to write to cookie file: {e}")

                    save_config()
                    return redirect(url_for('settings_route', saved='true'))
            else:
                return "Unauthorized", 401
        
        with state_manager._lock:
            current_update_status = update_status.copy()
        
        if request.method == "GET":
            config_errors = validate_config_paths()

        return render_template("settings.html", 
                               config=CONFIG, 
                               saved=request.args.get('saved'),
                               app_version=APP_VERSION,
                               update_info=current_update_status,
                               password_is_set=PASSWORD_IS_SET,
                               config_errors=config_errors)

    @app.route("/file_manager")
    def file_manager_route():
        return render_template("file_manager.html")
        
    # --- API Routes ---
    @app.route("/api/status")
    def status_poll_route():
        with state_manager._lock:
            return jsonify({
                "queue": state_manager.get_queue_list(),
                "current": state_manager.current_download if state_manager.current_download.get("url") else None,
                "history": state_manager.get_history_summary(),
                "is_paused": not state_manager.queue_paused_event.is_set()
            })

    @app.route("/api/update_check")
    def update_check_route():
        with state_manager._lock:
            return jsonify(update_status)

    def _parse_job_data(form_data):
        mode = form_data.get("download_mode")
        if not mode: raise ValueError("Download mode not specified.")
        job_base = { "mode": mode, "folder": form_data.get(f"{mode}_foldername", "").strip(), "archive": form_data.get("use_archive") == "yes", "proxy": form_data.get("proxy", "").strip(), "rate_limit": form_data.get("rate_limit", "").strip() }
        try:
            p_start = form_data.get("playlist_start", "").strip()
            p_end = form_data.get("playlist_end", "").strip()
            job_base["playlist_start"] = int(p_start) if p_start else None
            job_base["playlist_end"] = int(p_end) if p_end else None
        except ValueError: raise ValueError("Playlist start/end must be a number.")
        if mode == 'music': job_base.update({"format": form_data.get("music_audio_format"), "quality": form_data.get("music_audio_quality")})
        elif mode == 'video': job_base.update({"quality": form_data.get("video_quality"), "format": form_data.get("video_format"), "embed_subs": form_data.get("video_embed_subs") == "on", "codec": form_data.get("video_codec_preference")})
        elif mode == 'clip': job_base.update({"format": form_data.get("clip_format")})
        elif mode == 'custom': job_base.update({"custom_args": form_data.get("custom_args")})
        return job_base

    @app.route("/queue", methods=["POST"])
    def add_to_queue_route():
        urls = [line.strip() for line in request.form.get("urls", "").strip().splitlines() if line.strip()]
        if not urls: return jsonify({"message": "No valid URLs provided."}), 400
        try: job_base = _parse_job_data(request.form)
        except ValueError as e: return jsonify({"message": str(e)}), 400
        for url in urls:
            job = job_base.copy()
            job["url"] = url
            state_manager.add_to_queue(job)
        return jsonify({"message": f"Added {len(urls)} job(s) to the queue."})

    # --- FIX: Add the missing /queue/continue route ---
    @app.route("/queue/continue", methods=['POST'])
    def continue_job_route():
        """Re-queues a job, typically from history."""
        job = request.get_json()
        if not job or "url" not in job:
            return jsonify({"message": "Invalid job data provided."}), 400
        
        state_manager.add_to_queue(job)
        return jsonify({"message": f"Re-queued job for URL: {job['url']}"})

    @app.route('/api/auth/status')
    def auth_status_route():
        return jsonify({ "password_set": PASSWORD_IS_SET, "logged_in": session.get('is_logged_in', False) })

    @app.route('/api/auth/login', methods=['POST'])
    def login_route():
        if not PASSWORD_IS_SET: return jsonify({"message": "No password is set on the server."}), 400
        password = (request.get_json() or {}).get('password')
        if check_password_hash(CONFIG['admin_password_hash'], password):
            session['is_logged_in'] = True
            return jsonify({"message": "Login successful."})
        return jsonify({"message": "Invalid password."}), 401

    @app.route('/api/auth/logout', methods=['POST'])
    def logout_route():
        session.pop('is_logged_in', None)
        return jsonify({"message": "Logged out."})

    @app.route('/api/auth/set-password', methods=['POST'])
    def set_password_route():
        global PASSWORD_IS_SET, CONFIG
        data = request.get_json()
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        if PASSWORD_IS_SET:
            if not session.get('is_logged_in'): return jsonify({"message": "Authentication required."}), 401
            if not check_password_hash(CONFIG['admin_password_hash'], current_password):
                return jsonify({"message": "Current password is incorrect."}), 403
        if new_password:
            CONFIG['admin_password_hash'] = generate_password_hash(new_password)
            PASSWORD_IS_SET = True
            message = "Password updated successfully."
        else:
            CONFIG['admin_password_hash'] = None
            PASSWORD_IS_SET = False
            message = "Password has been removed. The application is now unsecured."
        save_config()
        session['is_logged_in'] = True
        return jsonify({"message": message})

    @app.route('/api/auth/get-cookies')
    @password_required
    def get_cookies_route():
        return jsonify({"cookies": CONFIG.get('cookie_file_content', '')})

    @app.route("/stop", methods=['POST'])
    @password_required
    def stop_route():
        mode = (request.get_json() or {}).get('mode', 'cancel') 
        state_manager.stop_mode = "SAVE" if mode == 'save' else "CANCEL"
        state_manager.cancel_event.set()
        return jsonify({"message": f"{state_manager.stop_mode.capitalize()} signal sent."})

    @app.route('/queue/clear', methods=['POST'])
    @password_required
    def clear_queue_route():
        state_manager.clear_queue()
        return jsonify({"message": "Queue cleared."})

    @app.route('/history/clear', methods=['POST'])
    @password_required
    def clear_history_route():
        for path in state_manager.clear_history():
            if is_safe_path(LOG_DIR, path):
                try: os.remove(path)
                except Exception as e: logger.error(f"Could not delete log file {path}: {e}")
        return jsonify({"message": "History cleared."})

    @app.route("/api/delete_item", methods=['POST'])
    @password_required
    def delete_item_route():
        paths = (request.get_json() or {}).get('paths', [])
        if not paths: return jsonify({"message": "Missing path parameter."}), 400
        download_dir = os.path.realpath(CONFIG.get("download_dir"))
        deleted_count, errors = 0, []
        for item_path in paths:
            full_path = os.path.abspath(os.path.join(download_dir, item_path))
            if not is_safe_path(download_dir, full_path):
                errors.append(f"Access denied for {item_path}")
                continue
            if not os.path.exists(full_path): continue
            try:
                if os.path.isdir(full_path): shutil.rmtree(full_path)
                else: os.remove(full_path)
                deleted_count += 1
            except Exception as e: errors.append(f"Error deleting {item_path}: {e}")
        if errors: return jsonify({"message": f"Completed with errors. Deleted {deleted_count} item(s).", "errors": errors}), 500
        return jsonify({"message": f"Successfully deleted {deleted_count} item(s)."})

    @app.route("/api/force_update_check", methods=['POST'])
    @password_required
    def force_update_check_route():
        _run_update_check()
        return jsonify({"message": "Update check completed."})

    @app.route('/api/shutdown', methods=['POST'])
    @password_required
    def shutdown_route():
        threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
        return jsonify({"message": "Server is shutting down."})

    @app.route('/api/install_update', methods=['POST'])
    @password_required
    def install_update_route():
        threading.Thread(target=trigger_update_and_restart).start()
        return jsonify({"message": "Update process initiated."})

    @app.route('/queue/delete/by-id/<int:job_id>', methods=['POST'])
    @password_required
    def delete_from_queue_route(job_id):
        state_manager.delete_from_queue(job_id)
        return jsonify({"message": "Queue item removed."})

    @app.route('/queue/reorder', methods=['POST'])
    def reorder_queue_route():
        data = request.get_json()
        try: ordered_ids = [int(i) for i in data.get('order', [])]
        except (ValueError, TypeError): return jsonify({"message": "Invalid job IDs provided."}), 400
        state_manager.reorder_queue(ordered_ids)
        return jsonify({"message": "Queue reordered."})

    @app.route('/queue/pause', methods=['POST'])
    def pause_queue_route():
        state_manager.pause_queue()
        return jsonify({"message": "Queue paused."})

    @app.route('/queue/resume', methods=['POST'])
    def resume_queue_route():
        state_manager.resume_queue()
        return jsonify({"message": "Queue resumed."})

    @app.route('/history/delete/<int:log_id>', methods=['POST'])
    def delete_from_history_route(log_id):
        path_to_delete = state_manager.delete_from_history(log_id)
        if path_to_delete and is_safe_path(LOG_DIR, path_to_delete):
            try: os.remove(path_to_delete)
            except Exception as e: logger.error(f"Could not delete log file {path_to_delete}: {e}")
        return jsonify({"message": "History item deleted."})

    @app.route('/api/history/item/<int:log_id>')
    def get_history_item_route(log_id):
        item = state_manager.get_history_item_by_log_id(log_id)
        if not item: return jsonify({"message": "History item not found."}), 404
        return jsonify(item)

    @app.route('/history/log/<int:log_id>')
    def history_log_route(log_id):
        item = state_manager.get_history_item_by_log_id(log_id)
        if not item: return jsonify({"log": "Log not found for the given ID."}), 404
        log_path = item.get("log_path")
        log_content = "Log not found on disk or could not be read."
        if log_path and log_path != "LOG_SAVE_ERROR" and os.path.exists(log_path) and is_safe_path(LOG_DIR, log_path):
            try:
                with open(log_path, 'r', encoding='utf-8') as f: log_content = f.read()
            except Exception as e: log_content = f"ERROR: Could not read log file. Reason: {e}"
        elif log_path == "LOG_SAVE_ERROR":
            log_content = "There was an error saving the log file for this job."
        return jsonify({"log": log_content})

    @app.route('/api/log/live/content')
    def live_log_content_route():
        log_path = state_manager.current_download.get("log_path")
        log_content = "No active download or log path is not available."
        if log_path and os.path.exists(log_path) and is_safe_path(LOG_DIR, log_path):
            try:
                with open(log_path, 'r', encoding='utf-8') as f: log_content = f.read()
            except Exception as e: log_content = f"ERROR: Could not read live log file. Reason: {e}"
        return jsonify({"log": log_content})

    @app.route("/api/files")
    def list_files_route():
        base_download_dir = os.path.realpath(CONFIG.get("download_dir"))
        req_path = request.args.get('path', '')
        safe_req_path = os.path.abspath(os.path.join(base_download_dir, req_path))
        if not is_safe_path(base_download_dir, safe_req_path): return jsonify({"error": "Access Denied"}), 403
        if not os.path.isdir(safe_req_path): return jsonify([])
        items = []
        try:
            for name in os.listdir(safe_req_path):
                full_path = os.path.join(safe_req_path, name)
                relative_path = os.path.relpath(full_path, base_download_dir)
                item_data = {"name": name, "path": relative_path.replace("\\", "/")}
                try:
                    if os.path.isdir(full_path): item_data.update({"type": "directory", "item_count": len(os.listdir(full_path))})
                    else: item_data.update({"type": "file", "size": os.path.getsize(full_path)})
                    items.append(item_data)
                except OSError: continue
        except OSError as e: return jsonify({"error": f"Cannot access directory: {e.strerror}"}), 500
        return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

    @app.route("/download_item")
    def download_item_route():
        paths = request.args.getlist('paths')
        if not paths: return "Missing path parameter.", 400
        download_dir = os.path.realpath(CONFIG.get("download_dir"))
        safe_full_paths = []
        for path in paths:
            full_path = os.path.abspath(os.path.join(download_dir, path))
            if is_safe_path(download_dir, full_path) and os.path.exists(full_path):
                safe_full_paths.append(full_path)
        if not safe_full_paths: return "No valid files specified or access denied.", 404
        if len(safe_full_paths) == 1 and os.path.isfile(safe_full_paths[0]):
            return send_file(safe_full_paths[0], as_attachment=True)
        zip_buffer = io.BytesIO()
        zip_name = f"{os.path.basename(safe_full_paths[0]) if len(safe_full_paths) == 1 and os.path.isdir(safe_full_paths[0]) else 'downloader_selection'}.zip"
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for full_path in safe_full_paths:
                if os.path.isdir(full_path):
                    base_arcname = os.path.basename(full_path)
                    for root, _, files in os.walk(full_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.join(base_arcname, os.path.relpath(file_path, full_path))
                            zip_file.write(file_path, arcname=arcname)
                else:
                    zip_file.write(full_path, arcname=os.path.basename(full_path))
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name=zip_name, mimetype='application/zip')

    # --- This block will only be executed when you run "python web_tool.py" ---
    if __name__ == "__main__":
        try:
            initialize_app()
            from waitress import serve
            logger.info("Initialization complete. Starting production server with Waitress...")
            serve(app, host="0.0.0.0", port=8080, _quiet=True)
            
        except Exception as e:
            logger.critical("A critical error occurred during the server launch.", exc_info=True)
            if platform.system() == "Windows":
                os.system("pause")


except Exception as e:
    logger.critical("A critical error occurred during application startup.", exc_info=True)
    if platform.system() == "Windows":
        os.system("pause")

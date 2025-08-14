# lib/routes.py
import os
import io
import zipfile
import shutil
import logging
import pytz

from flask import request, render_template, jsonify, redirect, url_for, send_file, session, flash
from flask_wtf.csrf import generate_csrf
from functools import wraps

from . import app_globals as g
from . import sanitizer

logger = logging.getLogger()

# --- Authentication & Permissions ---

def permission_required(permission):
    """Decorator for API routes to check user permissions."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            role = session.get('role')
            if not role:
                return jsonify({"error": "Authentication required. Please log in."}), 401

            if role == 'admin':
                return f(*args, **kwargs)

            user = g.user_manager.get_user(role)
            if user and user.get("permissions", {}).get(permission, False):
                return f(*args, **kwargs)

            return jsonify({"error": "Permission denied."}), 403
        return decorated_function
    return decorator

def page_permission_required(permission):
    """Decorator for page routes that redirects on permission failure."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            role = session.get('role')
            has_permission = False
            if role:
                if role == 'admin':
                    has_permission = True
                else:
                    user = g.user_manager.get_user(role)
                    if user and user.get("permissions", {}).get(permission, False):
                        has_permission = True

            if not has_permission:
                flash("You do not have permission to access this page. Please log in as an administrator.", "danger")
                return redirect(url_for('index_route'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- Utility Functions ---

def is_safe_path(basedir, path_to_check, allow_file=False):
    """Securely checks if path_to_check is within basedir."""
    try:
        real_basedir = os.path.realpath(basedir)
        # We must use abspath on the basedir join to handle path_to_check being absolute
        combined_path = os.path.abspath(os.path.join(basedir, path_to_check))
        real_path_to_check = os.path.realpath(combined_path)
    except OSError:
        return False

    # For directories, check if it's a directory
    if not allow_file and not os.path.isdir(real_path_to_check):
        return False

    return os.path.commonpath([real_basedir, real_path_to_check]) == real_path_to_check

def _parse_job_data(form_data):
    """Parses form data to create a job dictionary."""
    mode = form_data.get("download_mode")
    if not mode: raise ValueError("Download mode not specified.")

    job_base = {
        "mode": mode,
        "folder": form_data.get(f"{mode}_foldername", "").strip(),
        "archive": form_data.get("use_archive") == "yes",
        "proxy": form_data.get("proxy", "").strip(),
        "rate_limit": form_data.get("rate_limit", "").strip(),
        "embed_lyrics": form_data.get("embed_lyrics") == "on",
        "split_chapters": form_data.get("split_chapters") == "on"
    }
    try:
        p_start = form_data.get("playlist_start", "").strip()
        p_end = form_data.get("playlist_end", "").strip()
        job_base["playlist_start"] = int(p_start) if p_start else None
        job_base["playlist_end"] = int(p_end) if p_end else None
    except ValueError:
        raise ValueError("Playlist start/end must be a number.")

    if mode == 'music':
        job_base.update({"format": form_data.get("music_audio_format"), "quality": form_data.get("music_audio_quality")})
    elif mode == 'video':
        job_base.update({
            "quality": form_data.get("video_quality"), "format": form_data.get("video_format"),
            "embed_subs": form_data.get("video_embed_subs") == "on", "codec": form_data.get("video_codec_preference")
        })
    elif mode == 'clip':
        job_base.update({"format": form_data.get("clip_format")})
    elif mode == 'custom':
        job_base.update({"custom_args": form_data.get("custom_args")})

    return job_base

def get_current_state():
    """Assembles the full application state for the frontend."""
    with g.state_manager._lock:
        state = {
            "queue": g.state_manager.get_queue_list(),
            "current": g.state_manager.current_download if g.state_manager.current_download.get("url") else None,
            "history": g.state_manager.get_history_summary(),
            "is_paused": not g.state_manager.queue_paused_event.is_set()
        }
    state["scythes"] = g.scythe_manager.get_all()
    return state

# --- Route Registration ---

def register_routes(app):

    @app.context_processor
    def inject_globals():
        return dict(
            app_name=g.APP_NAME,
            app_version=g.APP_VERSION,
            csrf_token=generate_csrf
        )

    @g.socketio.on('connect')
    def handle_connect():
        logger.info(f"Client connected: {request.sid}")
        g.socketio.emit('state_update', get_current_state(), room=request.sid)

    @g.socketio.on('disconnect')
    def handle_disconnect():
        logger.info(f"Client disconnected: {request.sid}")

    # --- Page Routes ---
    @app.route('/favicon.ico')
    def favicon():
        return send_file(os.path.join(app.static_folder, 'img/icon', 'favicon.ico'))

    @app.route("/")
    def index_route():
        return render_template("index.html")

    @app.route("/file_manager")
    def file_manager_route():
        return render_template("file_manager.html")

    @app.route("/settings")
    @page_permission_required('admin')
    def settings_route():
        with g.state_manager._lock:
            current_update_status = g.update_status.copy()
        timezones = pytz.common_timezones
        return render_template("settings.html", update_info=current_update_status, timezones=timezones)

    @app.route("/logs")
    @page_permission_required('admin')
    def logs_route():
        return render_template("logs.html")

    # --- Queue & Job API ---
    @app.route("/queue", methods=["POST"])
    @permission_required('can_add_to_queue')
    def add_to_queue_route():
        try:
            urls = [line.strip() for line in request.form.get("urls", "").strip().splitlines() if line.strip()]
            if not urls: return jsonify({"error": "No valid URLs provided."}), 400

            job_base = _parse_job_data(request.form)
            
            # Create a list of all jobs to be added
            jobs_to_add = []
            for url in urls:
                jobs_to_add.append({**job_base, "url": url})

            # Add all jobs in a single, efficient operation
            if jobs_to_add:
                g.state_manager.add_many_to_queue(jobs_to_add)

            return jsonify({"message": f"Added {len(urls)} job(s) to the queue."})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error adding to queue: {e}", exc_info=True)
            return jsonify({"error": "An unexpected server error occurred."}), 500

    @app.route("/queue/continue", methods=['POST'])
    @permission_required('can_add_to_queue')
    def continue_job_route():
        data = request.get_json()
        if not data or "log_id" not in data: return jsonify({"error": "Invalid request. Missing log_id."}), 400

        history_item = g.state_manager.get_history_item_by_log_id(data["log_id"])
        if not history_item or not history_item.get("job_data"): return jsonify({"error": "Could not find original job data."}), 404

        job_to_continue = history_item["job_data"]
        job_to_continue["resolved_folder"] = history_item.get("folder")

        g.state_manager.add_to_queue(job_to_continue)
        return jsonify({"message": f"Re-queued job for URL: {job_to_continue['url']}"})

    @app.route('/queue/clear', methods=['POST'])
    @permission_required('can_add_to_queue')
    def clear_queue_route():
        g.state_manager.clear_queue()
        return jsonify({"message": "Queue cleared."})

    @app.route('/queue/delete/by-id/<int:job_id>', methods=['POST'])
    @permission_required('can_add_to_queue')
    def delete_from_queue_route(job_id):
        g.state_manager.delete_from_queue(job_id)
        return jsonify({"message": "Queue item removed."})

    @app.route('/queue/reorder', methods=['POST'])
    @permission_required('can_add_to_queue')
    def reorder_queue_route():
        data = request.get_json()
        try:
            ordered_ids = [int(i) for i in data.get('order', [])]
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid job IDs provided."}), 400
        g.state_manager.reorder_queue(ordered_ids)
        return jsonify({"message": "Queue reordered."})

    @app.route('/queue/pause', methods=['POST'])
    @permission_required('can_add_to_queue')
    def pause_queue_route():
        g.state_manager.pause_queue()
        return jsonify({"message": "Queue paused."})

    @app.route('/queue/resume', methods=['POST'])
    @permission_required('can_add_to_queue')
    def resume_queue_route():
        g.state_manager.resume_queue()
        return jsonify({"message": "Queue resumed."})

    # --- History API ---
    @app.route('/history/clear', methods=['POST'])
    @permission_required('admin')
    def clear_history_route():
        log_dir = os.path.join(g.DATA_DIR, "logs")
        # The state manager returns the list of log file paths that were cleared
        for path_from_db in g.state_manager.clear_history():
            if not path_from_db or path_from_db in ["LOG_SAVE_ERROR", "No log generated."]:
                continue

            # SECURITY: Sanitize the path from the database by only using its filename.
            # This prevents any directory traversal (e.g., a stored path like '../../boot.ini')
            # from being actioned. os.path.basename() strips all directory info.
            log_filename = os.path.basename(path_from_db)
            safe_full_path = os.path.join(log_dir, log_filename)

            # Double-check that the constructed path is valid and exists before deleting.
            if is_safe_path(log_dir, log_filename, allow_file=True) and os.path.exists(safe_full_path):
                try:
                    os.remove(safe_full_path)
                except OSError as e:
                    logger.error(f"Could not delete log file {safe_full_path}: {e}")
        return jsonify({"message": "History cleared."})

    @app.route('/history/delete/<int:log_id>', methods=['POST'])
    @permission_required('admin')
    def delete_from_history_route(log_id):
        log_dir = os.path.join(g.DATA_DIR, "logs")
        path_to_delete = g.state_manager.delete_from_history(log_id)

        if path_to_delete and path_to_delete not in ["LOG_SAVE_ERROR", "No log generated."]:
            # SECURITY: Sanitize the path from the database by only using its filename.
            # This is a critical step to prevent path traversal vulnerabilities.
            log_filename = os.path.basename(path_to_delete)
            safe_full_path = os.path.join(log_dir, log_filename)

            # Double-check that the constructed path is valid and exists before deleting.
            if is_safe_path(log_dir, log_filename, allow_file=True) and os.path.exists(safe_full_path):
                try:
                    os.remove(safe_full_path)
                except OSError as e:
                    logger.error(f"Could not delete log file {safe_full_path}: {e}")
        return jsonify({"message": "History item deleted."})

    @app.route('/api/history/item/<int:log_id>')
    def get_history_item_route(log_id):
        item = g.state_manager.get_history_item_by_log_id(log_id)
        if not item: return jsonify({"error": "History item not found."}), 404

        if request.args.get('include_log') == 'true':
            log_dir = os.path.join(g.DATA_DIR, "logs")
            log_path_from_db = item.get("log_path")
            log_content = "Log not found or could not be read."

            if log_path_from_db and log_path_from_db not in ["LOG_SAVE_ERROR", "No log generated."]:
                # SECURITY: Use the same basename sanitization for reading files to prevent traversal.
                log_filename = os.path.basename(log_path_from_db)
                safe_full_path = os.path.join(log_dir, log_filename)

                if is_safe_path(log_dir, log_filename, allow_file=True) and os.path.exists(safe_full_path):
                    try:
                        with open(safe_full_path, 'r', encoding='utf-8') as f:
                            log_content = f.read()
                    except OSError as e:
                        log_content = f"ERROR: Could not read log file: {e}"
            elif log_path_from_db:
                log_content = "There was an error saving or generating the log file for this job."

            item['log_content'] = log_content
        return jsonify(item)

    # --- Scythes API ---
    @app.route('/api/scythes', methods=['POST'])
    @permission_required('can_manage_scythes')
    def add_scythe_route():
        data = request.get_json()
        if not data: return jsonify({"error": "Invalid request."}), 400

        if log_id := data.get("log_id"):
            history_item = g.state_manager.get_history_item_by_log_id(log_id)
            if not history_item or not history_item.get("job_data"): return jsonify({"error": "Could not find original job data."}), 404
            scythe_data = {"name": history_item.get("title", "Untitled"), "job_data": history_item["job_data"]}
            scythe_data["job_data"]["resolved_folder"] = history_item.get("folder")
        elif (job_data := data.get("job_data")) and (name := data.get("name")):
            scythe_data = {"name": name, "job_data": job_data, "schedule": data.get("schedule")}
        else:
            return jsonify({"error": "Invalid payload for creating a Scythe."}), 400

        result, message = g.scythe_manager.add(scythe_data)
        if result:
            g.scheduler._load_and_schedule_jobs()
            return jsonify({"message": message}), 201
        return jsonify({"error": message}), 409

    @app.route('/api/scythes/<int:scythe_id>', methods=['PUT'])
    @permission_required('can_manage_scythes')
    def update_scythe_route(scythe_id):
        data = request.get_json()
        if not data or not data.get("name") or not data.get("job_data"): return jsonify({"error": "Invalid payload."}), 400

        if g.scythe_manager.update(scythe_id, data):
            g.scheduler._load_and_schedule_jobs()
            return jsonify({"message": "Scythe updated."})
        return jsonify({"error": "Scythe not found."}), 404

    @app.route('/api/scythes/<int:scythe_id>', methods=['DELETE'])
    @permission_required('can_manage_scythes')
    def delete_scythe_route(scythe_id):
        if g.scythe_manager.delete(scythe_id):
            g.scheduler._load_and_schedule_jobs()
            return jsonify({"message": "Scythe deleted."})
        return jsonify({"error": "Scythe not found."}), 404

    @app.route('/api/scythes/<int:scythe_id>/reap', methods=['POST'])
    @permission_required('can_add_to_queue')
    def reap_scythe_route(scythe_id):
        scythe = g.scythe_manager.get_by_id(scythe_id)
        if not scythe or not scythe.get("job_data"): return jsonify({"error": "Scythe not found."}), 404

        job_to_reap = scythe["job_data"]
        job_to_reap["resolved_folder"] = job_to_reap.get("folder")
        g.state_manager.add_to_queue(job_to_reap)
        return jsonify({"message": f"Added '{scythe.get('name')}' to queue."})

    # --- File Management API ---
    @app.route("/api/files")
    def list_files_route():
        base_dir = g.CONFIG.get("download_dir")
        req_path = request.args.get('path', '')
        if not is_safe_path(base_dir, req_path): return jsonify({"error": "Access Denied"}), 403

        current_dir = os.path.join(base_dir, req_path)
        items = []
        try:
            for entry in os.scandir(current_dir):
                try:
                    relative_path = os.path.relpath(entry.path, base_dir).replace("\\", "/")
                    item_data = {"name": entry.name, "path": relative_path}
                    if entry.is_dir():
                        item_data.update({"type": "directory", "item_count": len(os.listdir(entry.path))})
                    else:
                        item_data.update({"type": "file", "size": entry.stat().st_size})
                    items.append(item_data)
                except OSError:
                    continue # Skip files we can't access
        except FileNotFoundError:
            return jsonify({"error": "Directory not found."}), 404
        except OSError as e:
            return jsonify({"error": f"Cannot access directory: {e.strerror}"}), 500

        return jsonify(sorted(items, key=lambda x: (x['type'] == 'file', x['name'].lower())))

    @app.route("/download_item")
    @permission_required('can_download_files')
    def download_item_route():
        paths = request.args.getlist('paths')
        base_dir = g.CONFIG.get("download_dir")
        if not paths: return "Missing path parameter.", 400

        safe_paths = [os.path.join(base_dir, p) for p in paths if is_safe_path(base_dir, p, allow_file=True)]
        if not safe_paths: return "No valid files or access denied.", 404

        if len(safe_paths) == 1 and os.path.isfile(safe_paths[0]):
            return send_file(safe_paths[0], as_attachment=True)

        zip_buffer = io.BytesIO()
        zip_name = f"{os.path.basename(safe_paths[0]) if len(safe_paths) == 1 else 'ContentReaper_Selection'}.zip"
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for full_path in safe_paths:
                if os.path.isdir(full_path):
                    base_arc = os.path.basename(full_path)
                    for root, _, files in os.walk(full_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.join(base_arc, os.path.relpath(file_path, full_path))
                            zf.write(file_path, arcname)
                else:
                    zf.write(full_path, os.path.basename(full_path))
        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name=zip_name, mimetype='application/zip')

    @app.route("/api/delete_item", methods=['POST'])
    @permission_required('can_delete_files')
    def delete_item_route():
        paths = (request.get_json() or {}).get('paths', [])
        if not paths: return jsonify({"error": "Missing 'paths' parameter."}), 400

        base_dir = g.CONFIG.get("download_dir")
        deleted_count, errors = 0, []
        for item_path in paths:
            if not is_safe_path(base_dir, item_path, allow_file=True):
                errors.append(f"Skipping invalid path: {item_path}")
                continue
            full_path = os.path.join(base_dir, item_path)
            if not os.path.exists(full_path): continue
            try:
                if os.path.isdir(full_path): shutil.rmtree(full_path)
                else: os.remove(full_path)
                deleted_count += 1
            except OSError as e:
                 errors.append(f"Error deleting {item_path}: {e}")
            except Exception as e:
                 errors.append(f"An unexpected error occurred while deleting {item_path}: {e}")


        if errors: return jsonify({"message": f"Deleted {deleted_count} item(s) with errors.", "errors": errors}), 500
        return jsonify({"message": f"Successfully deleted {deleted_count} item(s)."})

    # --- Import and register modularized routes ---
    from .auth import setup_auth_routes
    from .system import setup_system_routes

    setup_auth_routes(app)
    setup_system_routes(app)

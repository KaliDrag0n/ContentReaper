# lib/system.py
import os
import logging
import threading
import platform
import subprocess
import requests
import json

from flask import request, jsonify
from . import app_globals as g
from .routes import permission_required, is_safe_path # Import decorators and utils

logger = logging.getLogger()

def _run_update_check():
    """Fetches the latest release info from GitHub."""
    try:
        res = requests.get(f"https://api.github.com/repos/{g.GITHUB_REPO_SLUG}/releases/latest", timeout=15)
        res.raise_for_status()
        latest_release = res.json()
        latest_version_tag = latest_release.get("tag_name", "").lstrip('v')
        with g.state_manager._lock:
            if latest_version_tag > g.APP_VERSION:
                g.update_status.update({
                    "update_available": True,
                    "latest_version": latest_version_tag,
                    "release_url": latest_release.get("html_url"),
                    "release_notes": latest_release.get("body")
                })
            else:
                g.update_status["update_available"] = False
    except requests.RequestException as e:
        logger.warning(f"Update check failed due to a network error: {e}")
    except json.JSONDecodeError:
        logger.warning("Update check failed: Could not decode JSON response from GitHub API.")
    except Exception as e:
        logger.warning(f"An unexpected error occurred during update check: {e}")


def scheduled_update_check():
    """Periodically checks for updates in a background thread."""
    while not g.STOP_EVENT.is_set():
        _run_update_check()
        g.STOP_EVENT.wait(3600) # Check every hour

def shutdown_server():
    """Triggers a graceful shutdown of the application."""
    logger.info("Shutdown initiated via API. Signaling threads and server to stop.")
    g.STOP_EVENT.set()
    if g.socketio:
        # This function is designed to be called from a request handler,
        # so it's safe to use the 'shutdown' function provided by the server.
        # We add a small delay to allow the response to be sent.
        threading.Timer(1, g.socketio.stop).start()


def run_update_script():
    """Launches the external updater script in a new, detached process."""
    import time
    import sys

    time.sleep(2)
    updater_script_path = os.path.join(g.APP_ROOT, 'lib', 'updater.py')
    command = [sys.executable, updater_script_path]

    logger.info(f"Starting update process with command: {' '.join(command)}")

    try:
        if platform.system() == "Windows":
            subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            # Use start_new_session to detach the process on Linux/macOS
            subprocess.Popen(command, start_new_session=True)
    except (OSError, FileNotFoundError) as e:
        logger.critical(f"Failed to launch updater script: {e}")
        return

    shutdown_server()

def setup_system_routes(app):

    # Start the scheduled update checker in a background thread
    threading.Thread(target=scheduled_update_check, daemon=True).start()

    @app.route('/api/settings', methods=['GET', 'POST'])
    @permission_required('admin')
    def api_settings_route():
        from .config_manager import save_config
        if request.method == 'POST':
            data = request.get_json()
            if not data: return jsonify({"error": "Invalid request body."}), 400

            g.CONFIG["download_dir"] = data.get("download_dir", g.CONFIG["download_dir"]).strip()
            g.CONFIG["temp_dir"] = data.get("temp_dir", g.CONFIG["temp_dir"]).strip()
            g.CONFIG["log_level"] = data.get("log_level", g.CONFIG["log_level"]).strip().upper()
            g.CONFIG["server_host"] = data.get("server_host", g.CONFIG["server_host"]).strip()
            g.CONFIG["public_user"] = data.get("public_user") if data.get("public_user") != "None" else None
            g.CONFIG["user_timezone"] = data.get("user_timezone", "UTC")
            try:
                g.CONFIG["server_port"] = int(data.get("server_port", g.CONFIG["server_port"]))
            except (ValueError, TypeError):
                logger.warning(f"Invalid server_port value: {data.get('server_port')}. Retaining existing.")

            save_config()

            cookie_file = os.path.join(g.DATA_DIR, "cookies.txt")
            try:
                with open(cookie_file, 'w', encoding='utf-8') as f:
                    f.write(data.get("cookie_content", ""))
            except OSError as e:
                logger.error(f"Failed to write to cookie file: {e}")
                return jsonify({"error": "Failed to save cookie file."}), 500

            logger.info("Settings saved. Host/port/log level changes apply on restart.")
            return jsonify({"message": "Settings saved successfully. Restart required for some changes."})

        # GET request
        cookie_file = os.path.join(g.DATA_DIR, "cookies.txt")
        cookie_content = ""
        if os.path.exists(cookie_file):
            try:
                with open(cookie_file, 'r', encoding='utf-8') as f: cookie_content = f.read()
            except OSError as e:
                logger.error(f"Could not read cookie file: {e}")

        return jsonify({
            "config": g.CONFIG,
            "cookies": cookie_content,
            "users": g.user_manager.get_all_users()
        })

    @app.route("/api/stop", methods=['POST'])
    @permission_required('can_add_to_queue')
    def stop_route():
        mode = (request.get_json() or {}).get('mode', 'cancel').upper()
        g.state_manager.stop_mode = "SAVE" if mode == 'SAVE' else "CANCEL"
        g.state_manager.cancel_event.set()
        return jsonify({"message": f"{g.state_manager.stop_mode.capitalize()} signal sent."})

    @app.route("/api/update_check")
    def update_check_route():
        with g.state_manager._lock:
            return jsonify(g.update_status)

    @app.route("/api/force_update_check", methods=['POST'])
    @permission_required('admin')
    def force_update_check_route():
        _run_update_check()
        return jsonify({"message": "Update check completed."})

    @app.route('/api/shutdown', methods=['POST'])
    @permission_required('admin')
    def shutdown_route():
        shutdown_server()
        return jsonify({"message": "Server is shutting down."})

    @app.route('/api/install_update', methods=['POST'])
    @permission_required('admin')
    def install_update_route():
        logger.info("Update requested via API.")
        threading.Thread(target=run_update_script).start()
        return jsonify({"message": "Update process initiated. Server will restart."})

    @app.route('/api/logs', methods=['GET'])
    @permission_required('admin')
    def list_logs_route():
        log_dir = os.path.join(g.DATA_DIR, "logs")
        logs = []

        startup_log = os.path.join(g.DATA_DIR, 'startup.log')
        if os.path.exists(startup_log):
            logs.append({"filename": "startup.log", "display_name": "Application Log (startup.log)"})

        import glob
        try:
            job_logs = sorted(glob.glob(os.path.join(log_dir, "job_*.log")), reverse=True)
            for log_path in job_logs:
                filename = os.path.basename(log_path)
                logs.append({"filename": f"logs/{filename}", "display_name": f"Job Log ({filename})"})
        except OSError as e:
            logger.error(f"Could not scan for job logs: {e}")

        return jsonify(logs)

    @app.route('/api/logs/<path:filename>', methods=['GET'])
    @permission_required('admin')
    def get_log_content_route(filename):
        if '..' in filename or filename.startswith('/'):
            return jsonify({"error": "Invalid filename."}), 400

        full_path = os.path.join(g.DATA_DIR, filename)

        if not is_safe_path(g.DATA_DIR, filename, allow_file=True):
            return jsonify({"error": "Access denied."}), 403

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - (1024 * 1024)), os.SEEK_SET) # Limit to last 1MB
                content = f.read()
            return jsonify({"content": content})
        except FileNotFoundError:
            return jsonify({"error": "Log file not found."}), 404
        except OSError as e:
            logger.error(f"Error reading log file {filename}: {e}")
            return jsonify({"error": "Could not read log file."}), 500

    @app.route('/api/log/live/content')
    def live_log_content_route():
        log_dir = os.path.join(g.DATA_DIR, "logs")
        log_path = g.state_manager.current_download.get("log_path")
        log_content = "No active download or log path is not available."
        if log_path and is_safe_path(log_dir, os.path.basename(log_path), allow_file=True):
            try:
                with open(log_path, 'r', encoding='utf-8') as f: log_content = f.read()
            except FileNotFoundError:
                log_content = "Live log file not found. It may have been rotated or deleted."
            except OSError as e:
                log_content = f"ERROR: Could not read live log file. Reason: {e}"
        return jsonify({"log": log_content})

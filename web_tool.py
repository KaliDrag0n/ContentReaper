# web_tool.py
import os
import subprocess
import sys
import platform
import logging
from logging.handlers import RotatingFileHandler

# --- Initial Setup: Define Paths and Logging ---
# This must happen before any other application imports to ensure
# paths and loggers are configured correctly from the start.

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

class RelativePathFilter(logging.Filter):
    def filter(self, record):
        record.relativepath = os.path.relpath(record.pathname, APP_ROOT)
        return True

# Configure standard logger
log_file = os.path.join(DATA_DIR, 'startup.log')
file_handler = RotatingFileHandler(log_file, maxBytes=1*1024*1024, backupCount=2)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [in %(pathname)s:%(lineno)d] :: %(message)s'))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [in %(relativepath)s:%(lineno)d] :: %(message)s'))
console_handler.addFilter(RelativePathFilter())
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Configure banner logger (for clean startup text)
banner_logger = logging.getLogger('banner')
banner_logger.propagate = False
banner_handler = logging.StreamHandler()
banner_handler.setFormatter(logging.Formatter('%(message)s'))
banner_logger.addHandler(banner_handler)
banner_logger.setLevel(logging.INFO)

def print_banner(version):
    """Prints the stylized startup banner."""
    logger.removeHandler(console_handler)
    banner_logger.info("="*95)
    banner_logger.info(r"""
▄█▄    ████▄    ▄      ▄▄▄▄▀ ▄███▄      ▄      ▄▄▄▄▀     █▄▄▄▄ ▄███▄   ██   █ ▄▄  ▄███▄   █▄▄▄▄ 
█▀ ▀▄  █   █     █  ▀▀▀ █    █▀   ▀      █  ▀▀▀ █        █  ▄▀ █▀   ▀  █ █  █   █ █▀   ▀  █  ▄▀ 
█   ▀  █   █ ██   █     █    ██▄▄    ██   █     █        █▀▀▌  ██▄▄    █▄▄█ █▀▀▀  ██▄▄    █▀▀▌  
█▄  ▄▀ ▀████ █ █  █    █     █▄   ▄▀ █ █  █    █         █  █  █▄   ▄▀ █  █ █     █▄   ▄▀ █  █  
▀███▀        █  █ █   ▀      ▀███▀   █  █ █   ▀            █   ▀███▀      █  █    ▀███▀     █   
             █   ██                  █   ██               ▀              █    ▀            ▀    
    """)
    banner_logger.info(" " * 37 + "--- ContentReaper ---")
    banner_logger.info("="*95 + "\n")
    logger.addHandler(console_handler)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    logger.info("="*35 + f" Starting ContentReaper v{version} " + "="*35 + "\n")
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [in %(relativepath)s:%(lineno)d] :: %(message)s'))

# --- Main Execution Block ---
if __name__ == "__main__":
    # Set global paths before importing the rest of the app
    from lib import app_globals as g
    g.APP_ROOT = APP_ROOT
    g.DATA_DIR = DATA_DIR

    # Now that paths are set, we can import the app factory
    from lib.app_setup import create_app

    try:
        print_banner(g.APP_VERSION)
        
        # Check for dependencies before creating the app
        try:
            from lib import dependency_manager
        except ImportError:
            logger.critical("Core packages not found. Attempting to install from requirements.txt...")
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
                logger.info("Dependencies installed successfully. Please restart the application.")
                sys.exit(0)
            except subprocess.CalledProcessError as e:
                logger.critical(f"Failed to install dependencies. Please run 'pip install -r requirements.txt' manually. Error: {e}")
                sys.exit(1)

        app = create_app()
        
        host = g.CONFIG.get("server_host", "0.0.0.0")
        port = g.CONFIG.get("server_port", 8080)
        
        banner_logger.info(f"Server is running at: http://{host}:{port}")
        banner_logger.info("You can now open this address in your web browser.")
        banner_logger.info("Press Ctrl+C in this window to stop the server.\n")

        try:
            g.socketio.run(app, host=host, port=port)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received.")
        finally:
            logger.info("Server is shutting down. Signaling threads to stop.")
            g.STOP_EVENT.set()
            
            if g.scheduler: g.scheduler.stop()
            if g.state_manager: g.state_manager.queue.put(None) # Sentinel to unblock worker
            
            if g.WORKER_THREAD:
                logger.info("Waiting for worker thread to finish...")
                g.WORKER_THREAD.join(timeout=15)
            
            if g.SCHEDULER_THREAD:
                logger.info("Waiting for scheduler thread to finish...")
                g.SCHEDULER_THREAD.join(timeout=5)
            
            if g.state_manager:
                logger.info("Saving final state before exit.")
                g.state_manager.save_state(immediate=True)
            
            logger.info("Shutdown complete.")
            
    except Exception as e:
        logger.critical("A critical error occurred during the server launch.", exc_info=True)
        if platform.system() == "Windows":
            os.system("pause")

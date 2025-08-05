#!/bin/bash

# This script can run the server directly, install it as a systemd service,
# or be called by the systemd service itself.

# --- Helper Functions ---
check_prerequisites() {
    echo "--> Verifying prerequisites..."
    if ! command -v python3 &> /dev/null; then
        echo "ERROR: python3 is not installed or not in your PATH."
        echo "Please install Python 3 to continue."
        exit 1
    fi
    if ! command -v git &> /dev/null; then
        echo "WARNING: 'git' is not installed. The script will run but cannot check for updates."
    fi
    echo "--> Prerequisites verified."
    echo ""
}

update_from_git() {
    if [ -d ".git" ] && command -v git &> /dev/null; then
      echo "--> Checking for updates..."
      # Stash local changes, pull updates, then re-apply stashed changes.
      git stash push -m "autostash before update" > /dev/null
      git pull
      git stash pop > /dev/null 2>&1 || true
      echo "--> Update check complete."
      echo ""
    fi
}

# --- Service Installation Function ---
install_systemd_service() {
    echo "--- Systemd Service Installer ---"

    if ! command -v systemctl &> /dev/null; then
        echo "ERROR: systemd is not available on this system. Cannot install service."
        exit 1
    fi

    if [ "$EUID" -ne 0 ]; then
      echo "ERROR: Service installation requires root privileges."
      echo "Please run this command again with sudo: sudo ./run.sh install-service"
      exit 1
    fi

    APP_PATH=$(pwd)
    SERVICE_USER=$(logname)
    SERVICE_GROUP=$(id -gn "$SERVICE_USER")
    SERVICE_FILE_OUTPUT="/etc/systemd/system/downloader-web-ui.service"

    echo "Service will be installed for user: $SERVICE_USER"
    echo "Application path: $APP_PATH"

    if [ ! -d "venv" ]; then
      echo "--> Creating Python virtual environment..."
      sudo -u "$SERVICE_USER" python3 -m venv venv
    fi

    echo "--> Creating service file..."
    # The ExecStart line is now changed to call this script with the 'start-service' argument.
    cat << EOF | sed -e "s|__USER__|$SERVICE_USER|g" \
                     -e "s|__GROUP__|$SERVICE_GROUP|g" \
                     -e "s|__PATH__|$APP_PATH|g" > "$SERVICE_FILE_OUTPUT"
[Unit]
Description=Downloader Web UI Service
After=network.target

[Service]
User=__USER__
Group=__GROUP__
WorkingDirectory=__PATH__

# This now calls the run.sh script, which will handle updates before starting python.
ExecStart=__PATH__/run.sh start-service

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    echo "--> Reloading systemd, enabling and starting the service..."
    systemctl daemon-reload
    systemctl enable downloader-web-ui.service
    systemctl restart downloader-web-ui.service

    echo ""
    echo "--- Installation Complete! ---"
    echo "The Downloader Web UI is now running as a background service."
    echo "You can check its status with: sudo systemctl status downloader-web-ui"
    echo "You can view its logs with:   sudo journalctl -u downloader-web-ui -f"
}

# --- Main Script Logic ---

# Get the directory of the script to ensure it runs from the correct location
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Entry point for the systemd service (non-interactive)
if [ "$1" == "start-service" ]; then
    update_from_git
    # Activate venv and start python
    source "$DIR/venv/bin/activate"
    python3 "$DIR/web_tool.py"
    exit 0
fi

# Entry point for the installer
if [ "$1" == "install-service" ]; then
    install_systemd_service
    exit 0
fi

# Default entry point for interactive use
check_prerequisites
update_from_git

echo "================================="
echo " Starting Downloader Web UI Server"
echo "================================="
echo "(To install as a background service, run: sudo ./run.sh install-service)"
echo ""

if [ -d "venv" ]; then
  echo "Activating Python virtual environment..."
  source "$DIR/venv/bin/activate"
else
  echo "WARNING: Python virtual environment not found. Running with system Python."
  echo "It is highly recommended to create one first with: python3 -m venv venv"
fi

echo "Starting application... (Press Ctrl+C to stop)"
python3 "$DIR/web_tool.py"

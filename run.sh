#!/bin/bash

# This script simplifies the setup and execution process.
# It checks if the systemd service is installed, installs it if not,
# and then starts/restarts it.

# --- Configuration ---
SERVICE_NAME="downloader-web-ui.service"
# Get the directory of the script to ensure it runs from the correct location
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SERVICE_FILE_PATH="/etc/systemd/system/$SERVICE_NAME"


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

# --- Service Installation Function (now called automatically) ---
install_systemd_service() {
    echo "--- Installing Systemd Service ---"

    # Determine the user who ran sudo, which is the intended service user.
    if [ -z "$SUDO_USER" ]; then
        echo "ERROR: This script must be run with sudo."
        exit 1
    fi
    
    SERVICE_USER=$SUDO_USER
    SERVICE_GROUP=$(id -gn "$SERVICE_USER")
    
    echo "Service will be installed for user: $SERVICE_USER"
    echo "Application path: $DIR"

    # Create the virtual environment as the correct user if it doesn't exist
    if [ ! -d "venv" ]; then
      echo "--> Creating Python virtual environment..."
      sudo -u "$SERVICE_USER" python3 -m venv venv
    fi

    echo "--> Creating service file..."
    # This creates the service file that systemd will use
    # It still uses 'start-service' for its own internal execution command
    cat << EOF | sed -e "s|__USER__|$SERVICE_USER|g" \
                     -e "s|__GROUP__|$SERVICE_GROUP|g" \
                     -e "s|__PATH__|$DIR|g" > "$SERVICE_FILE_PATH"
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

    echo "--> Reloading systemd and enabling the service..."
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
}

# --- Internal entry point for the systemd service (DO NOT REMOVE) ---
# When systemd runs `ExecStart`, it calls this script with 'start-service'.
# This part of the script handles that specific internal call.
if [ "$1" == "start-service" ]; then
    # Change to the script's directory before doing anything
    cd "$DIR"
    update_from_git
    # Activate venv and start the python application
    source "$DIR/venv/bin/activate"
    python3 "$DIR/web_tool.py"
    exit 0
fi

# --- Main Script Logic ---
# This is the new default behavior when you run `./run.sh`

# 1. Ensure the script is run with root privileges, as it's needed for service management.
if [ "$EUID" -ne 0 ]; then
  echo "This script needs root privileges to manage the systemd service."
  echo "Re-running with sudo..."
  # Re-execute this same script with sudo
  sudo bash "$0" "$@"
  exit $?
fi

# From here on, the script is running as root.

# 2. Go to the script's directory, check prerequisites, and update from git
cd "$DIR"
check_prerequisites
update_from_git

# 3. Check if the service is already installed. If not, install it.
echo "--> Checking for existing service..."
if [ ! -f "$SERVICE_FILE_PATH" ]; then
    echo "--> Service not found."
    install_systemd_service
    echo "--> Service installed successfully."
else
    echo "--> Service is already installed."
fi

# 4. Start (or restart) the service to apply any updates.
echo "--> Starting/restarting the service..."
systemctl restart $SERVICE_NAME

# 5. Final confirmation message for the user.
echo ""
echo "--- Setup Complete! ---"
echo "The Downloader Web UI is now running as a background service."
echo "The application should be available at: http://127.0.0.1:8080"
echo ""
echo "You can check its status with: sudo systemctl status $SERVICE_NAME"
echo "You can view its logs with:   sudo journalctl -u $SERVICE_NAME -f"

exit 0
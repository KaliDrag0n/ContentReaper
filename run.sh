#!/bin/bash
# A simple script to start the Downloader Web UI server on Linux and macOS.

# --- Define Paths ---
# Get the directory where the script is located to ensure paths are correct
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_DIR="$SCRIPT_DIR/bin/.venv"
PYTHON_EXEC="$VENV_DIR/bin/python3"
PIP_EXEC="$VENV_DIR/bin/pip"
SYSTEMD_FLAG_FILE="$SCRIPT_DIR/data/.systemd_configured"

# --- Dependency and Environment Check ---
if ! "$PYTHON_EXEC" -c "import flask, waitress, requests, schedule, flask_wtf" &> /dev/null; then
    echo "--> Python virtual environment or dependencies not found. Performing one-time setup..."
    mkdir -p "$SCRIPT_DIR/bin"
    if [ ! -d "$VENV_DIR" ]; then
        echo "--> Creating virtual environment in '$VENV_DIR'..."
        python3 -m venv "$VENV_DIR"
        if [ $? -ne 0 ]; then
            echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            echo "ERROR: Failed to create the virtual environment."
            echo "Please ensure 'python3-venv' is installed on your system."
            echo "(e.g., 'sudo apt install python3-venv')"
            echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
            exit 1
        fi
    fi
    echo "--> Installing required packages from requirements.txt..."
    "$PIP_EXEC" install -r "$SCRIPT_DIR/requirements.txt"
    if [ $? -ne 0 ]; then
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        echo "ERROR: Failed to install packages using pip."
        echo "Please check your internet connection and try again."
        echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        exit 1
    fi
    echo "--> Dependency setup complete!"
    echo ""
fi

# --- Systemd Service Setup (First Run Only) ---
if [ ! -f "$SYSTEMD_FLAG_FILE" ]; then
    # Check if systemd is the init system by checking for the systemd directory and the systemctl command.
    if [ -d /run/systemd/system ] && command -v systemctl &> /dev/null; then
        read -p "--> Would you like to set up ContentReaper as a systemd service to run on boot? (y/N) " choice
        case "$choice" in
          y|Y )
            echo "--> Setting up systemd service..."

            # Dynamically get the current user and the absolute path to the run script
            CURRENT_USER=$(whoami)
            RUN_SCRIPT_PATH="$SCRIPT_DIR/run.sh"
            # The service needs the project's bin directory in its PATH
            SERVICE_PATH_ENV="PATH=$SCRIPT_DIR/bin:$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"

            # Create the service file content
            SERVICE_FILE_CONTENT="[Unit]
Description=ContentReaper Downloader Web UI
After=network.target

[Service]
ExecStart=$RUN_SCRIPT_PATH
WorkingDirectory=$SCRIPT_DIR
Restart=always
User=$CURRENT_USER
Environment=\"$SERVICE_PATH_ENV\"

[Install]
WantedBy=multi-user.target"

            # Write the service file using sudo
            echo "$SERVICE_FILE_CONTENT" | sudo tee /etc/systemd/system/downloader.service > /dev/null
            if [ $? -ne 0 ]; then
                echo "ERROR: Failed to write service file. Please ensure you have sudo privileges."
            else
                echo "--> Service file created at /etc/systemd/system/downloader.service"
                # Reload, enable, and start the service
                sudo systemctl daemon-reload
                sudo systemctl enable downloader.service
                sudo systemctl start downloader.service
                echo "--> Service has been enabled and started."
                echo "--> You can check its status with: sudo systemctl status downloader.service"
            fi
            ;;
          * )
            echo "--> Skipping systemd setup."
            ;;
        esac
    fi
    # Create the flag file so this prompt doesn't appear again
    touch "$SYSTEMD_FLAG_FILE"
    echo ""
	exit 1
fi

# --- Run the Application ---
"$PYTHON_EXEC" "$SCRIPT_DIR/web_tool.py"

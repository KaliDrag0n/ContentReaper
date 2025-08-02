#!/bin/bash

# This script automates the full setup of the Downloader Web UI on a Debian-based system.

# Ensure the script is run with sudo
if [ "$(id -u)" -ne 0 ]; then
    echo "Please run this script with sudo: sudo ./install.sh"
    exit 1
fi

echo "--- [Step 1/5] Updating package lists ---"
apt update

echo "--- [Step 2/5] Installing system dependencies (Python, Pip, FFmpeg, Git, Curl) ---"
apt install -y python3 python3-pip ffmpeg git curl

echo "--- [Step 3/5] Installing/Updating yt-dlp to the latest version ---"
# The apt version of yt-dlp is often outdated. This gets the latest release directly.
curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
chmod a+rx /usr/local/bin/yt-dlp
echo "yt-dlp installed successfully to /usr/local/bin/"

echo "--- [Step 4/5] Installing Python dependencies ---"
# Check if requirements.txt exists before trying to use it
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "WARNING: requirements.txt not found. Skipping Python dependencies."
    echo "Please ensure Flask and waitress are installed manually."
fi

echo "--- [Step 5/5] Setting up systemd service ---"
# Prompt for user-specific details
read -p "Enter the username that will run the service (e.g., kali): " SERVICE_USER
# Get the absolute path to the script's directory
SERVICE_DIR=$(pwd)

# Define the service file content
SERVICE_FILE_CONTENT="[Unit]
Description=Downloader Web UI
After=network.target

[Service]
ExecStart=/usr/bin/python3 ${SERVICE_DIR}/web_tool.py
WorkingDirectory=${SERVICE_DIR}
Restart=always
User=${SERVICE_USER}
# Explicitly set the PATH to include /usr/local/bin for yt-dlp
Environment=\"PATH=/usr/local/bin:/usr/bin:/bin\"

[Install]
WantedBy=multi-user.target
"

# Create the service file
echo "Creating systemd service file at /etc/systemd/system/downloader.service"
echo "${SERVICE_FILE_CONTENT}" > /etc/systemd/system/downloader.service

# Reload systemd, enable and start the service
echo "Reloading systemd and starting the service..."
systemctl daemon-reload
systemctl enable downloader.service
systemctl start downloader.service

echo "--- Setup Complete ---"
echo "The Downloader Web UI is now running as a background service."
echo "You can check its status with: sudo systemctl status downloader.service"
echo "It will automatically start on boot."


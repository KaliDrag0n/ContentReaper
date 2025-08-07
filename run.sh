#!/bin/bash
# A simple script to start the Downloader Web UI server on Linux and macOS.

echo "================================="
echo " Starting Downloader Web UI Server"
echo "================================="
echo ""
echo "Server is running at: http://127.0.0.1:8080"
echo "You can now open this address in your web browser."
echo ""
echo "Press Ctrl+C in this window to stop the server."
echo ""

# This command starts the production-ready Waitress server.
# It binds to 0.0.0.0 to be accessible from other devices on your network.
python web_tool.py

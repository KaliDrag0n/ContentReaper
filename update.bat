@echo off
ECHO --- Downloader Web UI Updater ---
ECHO.

REM Find and kill the running waitress/web_tool.py process.
ECHO [1/3] Stopping the web server...
taskkill /F /IM waitress.exe /T > nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Downloader Web UI" /T > nul 2>&1
timeout /t 2 /nobreak > nul

REM Run the Python updater script to download and apply the new files.
ECHO [2/3] Running the update script...
python updater.py
ECHO.

REM Restart the web server.
ECHO [3/3] Restarting the web server...
start "Downloader Web UI" waitress-serve --host=0.0.0.0 --port=8080 web_tool:app

ECHO.
ECHO --- Update complete! ---
exit

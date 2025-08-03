@echo off
ECHO =================================
ECHO  Starting Downloader Web UI Server
ECHO =================================
ECHO.
ECHO Server is running at: http://127.0.0.1:8080
ECHO You can now open this address in your web browser.
ECHO.
ECHO Press Ctrl+C in this window to stop the server.
ECHO.

REM This command starts the production-ready Waitress server
waitress-serve --host=0.0.0.0 --port=8080 web_tool:app

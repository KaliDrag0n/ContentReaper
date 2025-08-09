/**
 * static/js/logs.js
 * Handles all logic for the in-app log viewer page.
 */

(function() {
    'use strict';

    const logSelector = document.getElementById('log-selector');
    const logContentEl = document.getElementById('log-content');
    const refreshBtn = document.getElementById('refresh-log-btn');

    let currentLogFile = null;

    /**
     * Fetches the list of available log files from the server and populates the dropdown.
     */
    const fetchLogList = async () => {
        try {
            logSelector.innerHTML = '<option>Loading logs...</option>';
            logSelector.disabled = true;
            const logFiles = await window.apiRequest(window.API.listLogs);

            logSelector.innerHTML = ''; // Clear loading text
            if (logFiles.length === 0) {
                logSelector.innerHTML = '<option>No logs found.</option>';
                return;
            }

            logFiles.forEach(log => {
                const option = document.createElement('option');
                option.value = log.filename;
                option.textContent = log.display_name;
                logSelector.appendChild(option);
            });
            
            // Automatically select and load the first log file if none is selected
            if (!currentLogFile && logFiles.length > 0) {
                currentLogFile = logFiles[0].filename;
                logSelector.value = currentLogFile;
            }
            
            if (currentLogFile) {
                await fetchLogContent(currentLogFile);
            }

        } catch (error) {
            logSelector.innerHTML = '<option>Error loading logs.</option>';
            logContentEl.textContent = `Could not fetch log list: ${error.message}`;
        } finally {
            logSelector.disabled = false;
        }
    };

    /**
     * Fetches the content of a specific log file and displays it.
     * @param {string} filename - The name of the log file to fetch.
     */
    const fetchLogContent = async (filename) => {
        if (!filename) {
            logContentEl.textContent = 'Please select a log file.';
            return;
        }
        
        logContentEl.textContent = 'Loading content...';
        refreshBtn.disabled = true;
        const icon = refreshBtn.querySelector('i');
        const originalIconClass = icon.className;
        icon.className = 'spinner-border spinner-border-sm';

        try {
            const data = await window.apiRequest(window.API.getLogContent(filename));
            logContentEl.textContent = data.content || 'Log file is empty.';
            // Scroll to the bottom
            logContentEl.scrollTop = logContentEl.scrollHeight;
        } catch (error) {
            logContentEl.textContent = `Error loading log content: ${error.message}`;
        } finally {
            refreshBtn.disabled = false;
            icon.className = originalIconClass;
        }
    };

    /**
     * Initializes event listeners for the page.
     */
    const initializePage = () => {
        logSelector.addEventListener('change', () => {
            currentLogFile = logSelector.value;
            fetchLogContent(currentLogFile);
        });

        refreshBtn.addEventListener('click', () => {
            if (currentLogFile) {
                fetchLogContent(currentLogFile);
            } else {
                fetchLogList();
            }
        });

        fetchLogList();
    };

    document.addEventListener('DOMContentLoaded', () => {
        const checkGlobals = setInterval(() => {
            if (window.applyTheme && window.apiRequest && window.API) {
                clearInterval(checkGlobals);
                initializePage();
            }
        }, 50);
    });

})();

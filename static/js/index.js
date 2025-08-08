/**
 * static/js/index.js
 * This file contains the core logic for the main dashboard page.
 * It uses a local state cache and intelligent DOM diffing
 * for more efficient and stable UI updates.
 */
(function() {
    'use strict';

    // --- STATE & CACHED ELEMENTS ---
    let logModalInstance, updateModalInstance;
    let statusPollTimeout;
    let urlInputTimeout;
    let liveLogPollInterval = null;
    let sortableInstance = null;

    // Local cache of the server state to compare against for efficient updates
    let localState = {
        current: null,
        queue: [],
        history: [],
        is_paused: false,
    };

    // --- UI MODE LOGIC ---

    const switchMode = (mode) => {
        document.querySelectorAll('.mode-selector .btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`.mode-selector .btn[data-mode="${mode}"]`).classList.add('active');
        document.querySelectorAll('.mode-options').forEach(el => el.style.display = 'none');
        document.getElementById(`${mode}-options`).style.display = 'block';
        document.getElementById('download_mode_input').value = mode;
        localStorage.setItem('downloader_mode', mode);
    };

    // --- API-DRIVEN FEATURES ---

    const checkForUpdates = async () => {
        try {
            const data = await window.apiRequest(window.API.updateCheck);
            const updateBtn = document.getElementById('update-notification-btn');
            if (data.update_available) {
                document.getElementById('update-version-text').textContent = data.latest_version;
                const notesContent = document.getElementById('update-notes-content');
                notesContent.textContent = data.release_notes;
                notesContent.innerHTML = notesContent.innerHTML.replace(/\r\n/g, '<br>');
                document.getElementById('update-release-link').href = data.release_url;
                updateBtn.style.display = 'block';
                updateBtn.onclick = () => {
                    if (!updateModalInstance) updateModalInstance = new bootstrap.Modal(document.getElementById('updateModal'));
                    updateModalInstance.show();
                };
            } else {
                updateBtn.style.display = 'none';
            }
        } catch (error) {
            console.error("Update check failed:", error);
        }
    };

    const handleUrlInput = () => {
        clearTimeout(urlInputTimeout);
        urlInputTimeout = setTimeout(() => {
            const urlTextarea = document.querySelector('textarea[name="urls"]');
            const urls = (urlTextarea.value.match(/(https?:\/\/[^\s"]+|www\.[^\s"]+)/g) || []).join('\n');
            urlTextarea.value = urls;
            const isPlaylist = urls.includes('playlist?list=');
            document.getElementById('playlist-range-options').style.display = isPlaylist ? 'flex' : 'none';
        }, 400);
    };
    
    const handleStopRequest = (mode) => {
        document.getElementById('stop-save-btn').disabled = true;
        document.getElementById('cancel-btn').disabled = true;
        document.getElementById('current-status-text').textContent = mode === 'save' ? 'Stopping...' : 'Cancelling...';
        window.apiRequest(window.API.stop, {
            method: 'POST',
            body: JSON.stringify({ mode: mode })
        }).catch(err => {
            if (err.message !== "AUTH_REQUIRED") console.error("Stop request failed", err);
        });
    };

    // --- REFACTORED RENDERING LOGIC ---

    function renderCurrentStatus(current) {
        const currentDiv = document.getElementById("current-status");
        const wasPreviouslyDownloading = localState.current !== null;
        const isCurrentlyDownloading = current !== null && current.url;

        // If transitioning from downloading to idle
        if (wasPreviouslyDownloading && !isCurrentlyDownloading) {
            currentDiv.innerHTML = "<p class='m-0'>No active download.</p>";
            return;
        }

        // If still idle, do nothing
        if (!isCurrentlyDownloading) {
            return;
        }

        // If a new download has started, create the full element structure
        if (!wasPreviouslyDownloading || current.url !== localState.current.url) {
            currentDiv.innerHTML = `
                <div class="d-flex flex-column flex-md-row">
                    <div class="flex-shrink-0 mb-3 mb-md-0 me-md-3" id="current-thumbnail-container"></div>
                    <div class="flex-grow-1" style="min-width: 0;">
                        <div id="current-title-text" class="mb-2"></div>
                        <div class="progress mb-2" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">
                            <div id="current-progress-bar" class="progress-bar progress-bar-striped progress-bar-animated" style="width: 0%;">0%</div>
                        </div>
                        <p id="current-status-text" class="mb-1 small"></p>
                        <div class="d-flex justify-content-between mt-2 small text-muted">
                            <small>Size: <span id="current-stat-size">N/A</span></small>
                            <small>Speed: <span id="current-stat-speed">N/A</span></small>
                            <small>ETA: <span id="current-stat-eta">N/A</span></small>
                        </div>
                        <div class="btn-group mt-2" role="group">
                            <button id="view-log-btn" class="btn btn-info btn-sm">View Log</button>
                            <button id="stop-save-btn" class="btn btn-warning btn-sm" title="Stop download and save completed files.">Stop & Save</button>
                            <button id="cancel-btn" class="btn btn-danger btn-sm" title="Cancel download and delete all temporary files.">Cancel</button>
                        </div>
                    </div>
                </div>
            `;
            document.getElementById('view-log-btn').addEventListener('click', viewLiveLog);
            document.getElementById('stop-save-btn').addEventListener('click', () => handleStopRequest('save'));
            document.getElementById('cancel-btn').addEventListener('click', () => handleStopRequest('cancel'));
        }

        // Granular updates for the existing structure
        const thumbnailContainer = document.getElementById('current-thumbnail-container');
        if (current.thumbnail && (!localState.current || current.thumbnail !== localState.current.thumbnail)) {
            thumbnailContainer.innerHTML = `<img src="${current.thumbnail}" class="now-downloading-thumbnail" alt="Thumbnail">`;
        }

        const titleEl = document.getElementById('current-title-text');
        const playlistIndicator = current.playlist_count > 0 ? `<span class="ms-2 badge bg-secondary align-middle">${current.playlist_index || '0'}/${current.playlist_count}</span>` : '';
        const newTitleHTML = current.playlist_title 
            ? `<strong class="word-break">${current.playlist_title}</strong>${playlistIndicator}<br><small class="text-muted word-break">${current.track_title || 'Loading track...'}</small>` 
            : `<strong class="word-break">${current.title || current.url}</strong>`;
        if (titleEl.innerHTML !== newTitleHTML) titleEl.innerHTML = newTitleHTML;

        const progressBar = document.getElementById('current-progress-bar');
        const progress = current.progress ? current.progress.toFixed(1) : 0;
        progressBar.style.width = `${progress}%`;
        progressBar.textContent = `${progress}%`;
        
        document.getElementById('current-status-text').textContent = current.status;
        document.getElementById('current-stat-size').textContent = current.file_size || 'N/A';
        document.getElementById('current-stat-speed').textContent = current.speed || 'N/A';
        document.getElementById('current-stat-eta').textContent = current.eta || 'N/A';
    }

    function renderQueue(newQueue) {
        const queueList = document.getElementById("queue-list");
        document.getElementById("queue-controls").style.display = newQueue.length > 0 ? 'flex' : 'none';

        const oldIds = new Set(localState.queue.map(j => j.id));
        const newIds = new Set(newQueue.map(j => j.id));

        // Remove items that are no longer in the queue
        oldIds.forEach(id => {
            if (!newIds.has(id)) {
                queueList.querySelector(`[data-job-id='${id}']`)?.remove();
            }
        });

        if (newQueue.length === 0) {
            if (!queueList.querySelector('.fst-italic')) {
                 queueList.innerHTML = "<li class='list-group-item fst-italic text-muted'>Queue is empty.</li>";
            }
        } else {
            // Add or update items
            newQueue.forEach((job, index) => {
                let li = queueList.querySelector(`[data-job-id='${job.id}']`);
                if (!li) {
                    li = document.createElement('li');
                    li.className = 'list-group-item d-flex justify-content-between align-items-center';
                    li.dataset.jobId = job.id;
                    li.innerHTML = `
                        <div class="d-flex align-items-center" style="min-width: 0;">
                            <i class="bi bi-grip-vertical queue-handle me-2" title="Drag to reorder"></i>
                            <span class="word-break">${job.folder ? `<strong>${job.folder}</strong>: ` : ''}${job.url}</span>
                        </div>
                        <button class="btn-close queue-action-btn" data-action="delete" data-job-id="${job.id}" aria-label="Remove from queue"></button>
                    `;
                    const nextSibling = queueList.children[index];
                    queueList.insertBefore(li, nextSibling);
                }
            });
            queueList.querySelector('.fst-italic')?.remove();
        }
    }

    function renderHistory(newHistory) {
        const historyList = document.getElementById("history-list");
        document.getElementById("clear-history-btn").style.display = newHistory.length > 0 ? 'block' : 'none';

        if (newHistory.length === 0 && localState.history.length > 0) {
            historyList.innerHTML = "<li class='list-group-item fst-italic text-muted'>History is empty.</li>";
            return;
        }

        const newHistoryMap = new Map(newHistory.map(item => [item.log_id, item]));
        const oldHistoryMap = new Map(localState.history.map(item => [item.log_id, item]));

        // Remove deleted items
        oldHistoryMap.forEach((_, logId) => {
            if (!newHistoryMap.has(logId)) {
                historyList.querySelector(`[data-log-id='${logId}']`)?.remove();
            }
        });
        
        // Add or update items
        [...newHistory].reverse().forEach(item => {
            const existingLi = historyList.querySelector(`[data-log-id='${item.log_id}']`);
            if (!existingLi) {
                const li = document.createElement('li');
                li.className = 'list-group-item';
                li.dataset.logId = item.log_id;
                li.dataset.status = item.status;
                li.innerHTML = createHistoryItemHTML(item);
                historyList.prepend(li);
            } else if (existingLi.dataset.status !== item.status) {
                // If only the status changed, just update the HTML
                existingLi.innerHTML = createHistoryItemHTML(item);
                existingLi.dataset.status = item.status;
            }
        });

        if (newHistory.length > 0) {
            historyList.querySelector('.fst-italic')?.remove();
        }
    }

    function createHistoryItemHTML(item) {
        let badgeClass = 'bg-secondary';
        switch(item.status) {
            case 'COMPLETED': badgeClass = 'bg-success'; break;
            case 'PARTIAL': badgeClass = 'bg-info text-dark'; break;
            case 'STOPPED': badgeClass = 'bg-warning text-dark'; break;
            case 'CANCELLED': badgeClass = 'bg-secondary'; break;
            case 'FAILED': case 'ERROR': case 'ABANDONED': badgeClass = 'bg-danger'; break;
        }

        let requeueButtonHTML = '';
        if (item.status === 'STOPPED' || item.status === 'PARTIAL') {
            requeueButtonHTML = `<button class="btn btn-sm btn-outline-success history-action-btn" data-action="requeue" title="Continue Download"><i class="bi bi-play-fill"></i></button>`;
        } else {
            requeueButtonHTML = `<button class="btn btn-sm btn-outline-secondary history-action-btn" data-action="requeue" title="Download Again"><i class="bi bi-arrow-clockwise"></i></button>`;
        }

        const errorSummaryHTML = item.error_summary ? `
            <div class="mt-2">
                <button class="btn btn-sm btn-outline-danger" type="button" data-bs-toggle="collapse" data-bs-target="#error-summary-${item.log_id}" aria-expanded="false">
                    <i class="bi bi-exclamation-triangle-fill"></i> Show Error
                </button>
            </div>
            <div class="collapse" id="error-summary-${item.log_id}">
                <pre class="card card-body mt-2 p-2 bg-body-tertiary small" style="max-height: 200px; overflow-y: auto;"><code>${item.error_summary.replace(/</g, "&lt;")}</code></pre>
            </div>` : '';

        return `
            <div class="d-flex justify-content-between align-items-center">
                <div class="flex-grow-1" style="min-width: 0;">
                    <span class="badge ${badgeClass} me-2">${item.status}</span>
                    <strong class="word-break">${item.title || "Unknown"}</strong>
                    <br>
                    <small class="text-muted word-break">${item.folder ? `Folder: ${item.folder}` : `URL: ${item.url}`}</small>
                </div>
                <div class="btn-group ms-2">
                    ${requeueButtonHTML}
                    <button class="btn btn-sm btn-outline-info history-action-btn" data-action="log" title="View Log"><i class="bi bi-file-text"></i></button>
                    <button class="btn btn-sm btn-outline-danger history-action-btn" data-action="delete" title="Delete"><i class="bi bi-trash-fill"></i></button>
                </div>
            </div>
            ${errorSummaryHTML}
        `;
    }

    function renderPauseState(is_paused) {
        if (localState.is_paused === is_paused) return;
        const pauseResumeBtn = document.getElementById('pause-resume-btn');
        const pausedOverlay = document.getElementById('paused-overlay');
        if (is_paused) {
            pauseResumeBtn.dataset.action = 'resume';
            pauseResumeBtn.innerHTML = '<i class="bi bi-play-fill"></i> Resume';
            pauseResumeBtn.classList.replace('btn-secondary', 'btn-success');
            pausedOverlay.style.display = 'flex';
        } else {
            pauseResumeBtn.dataset.action = 'pause';
            pauseResumeBtn.innerHTML = '<i class="bi bi-pause-fill"></i> Pause';
            pauseResumeBtn.classList.replace('btn-success', 'btn-secondary');
            pausedOverlay.style.display = 'none';
        }
    }
    
    function renderState(newState) {
        if (!newState) return;
        renderCurrentStatus(newState.current);
        renderQueue(newState.queue);
        renderHistory(newState.history);
        renderPauseState(newState.is_paused);
        // Update local state cache after rendering
        localState = newState;
    }

    const pollStatus = async () => {
        clearTimeout(statusPollTimeout);
        try {
            const data = await window.apiRequest(window.API.status);
            renderState(data);
        } catch (error) {
            if (error.message !== "AUTH_REQUIRED") console.error("Status poll failed:", error);
        } finally {
            statusPollTimeout = setTimeout(pollStatus, 1500);
        }
    };

    const viewStaticLog = async (logId) => {
        try {
            clearInterval(liveLogPollInterval);
            const data = await window.apiRequest(window.API.historyLog(logId));
            const logContentEl = document.getElementById('logModalContent');
            logContentEl.textContent = data.log || "Log is empty or could not be loaded.";
            if (!logModalInstance) logModalInstance = new bootstrap.Modal(document.getElementById('logModal'));
            logModalInstance.show();
            logContentEl.scrollTop = logContentEl.scrollHeight;
        } catch (error) { console.error("Could not fetch log:", error); }
    };

    const viewLiveLog = () => {
        clearInterval(liveLogPollInterval);
        const logContentEl = document.getElementById('logModalContent');
        logContentEl.textContent = 'Connecting to live log...';
        if (!logModalInstance) logModalInstance = new bootstrap.Modal(document.getElementById('logModal'));
        logModalInstance.show();

        const fetchLogContent = async () => {
            try {
                const data = await window.apiRequest(window.API.liveLog);
                if (logContentEl.textContent !== data.log) {
                    logContentEl.textContent = data.log;
                    logContentEl.scrollTop = logContentEl.scrollHeight;
                }
            } catch (error) {
                logContentEl.textContent += '\n--- Connection to log failed. Halting updates. ---';
                clearInterval(liveLogPollInterval);
            }
        };
        liveLogPollInterval = setInterval(fetchLogContent, 2000);
        fetchLogContent();
    };

    // --- INITIALIZATION ---
    const initializePage = () => {
        const savedMode = localStorage.getItem('downloader_mode') || 'clip';
        switchMode(savedMode);
        document.querySelectorAll('.mode-selector .btn').forEach(btn => btn.addEventListener('click', () => switchMode(btn.dataset.mode)));

        const addJobForm = document.getElementById('add-job-form');
        addJobForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const submitButton = this.querySelector('button[type="submit"]');
            const urlText = this.querySelector('textarea[name="urls"]').value;
            if (!urlText.trim()) {
                window.showToast("URL field cannot be empty.", "Input Error", "danger");
                return;
            }
            submitButton.disabled = true;

            try {
                const data = await window.apiRequest(window.API.queue, { method: 'POST', body: new FormData(this) });
                window.showToast(data.message, 'Success', 'success');
                this.reset();
                handleUrlInput();
                renderState(data.newState);
            } catch(error) { 
                if (error.message !== "AUTH_REQUIRED") console.error("Failed to add job:", error);
            } finally {
                submitButton.disabled = false;
            }
        });

        document.querySelector('textarea[name="urls"]').addEventListener('input', handleUrlInput);
        
        document.getElementById('clear-queue-btn').addEventListener('click', () => window.showConfirmModal('Clear Queue?', 'Are you sure you want to remove all items from the queue?', () => {
            window.apiRequest(window.API.queueClear, { method: 'POST' }).then(pollStatus).catch(err => { if(err.message !== "AUTH_REQUIRED") console.error(err) });
        }));
        
        document.getElementById('clear-history-btn').addEventListener('click', () => window.showConfirmModal('Clear History?', 'Are you sure you want to clear the entire download history?', () => {
            window.apiRequest(window.API.historyClear, { method: 'POST' }).then(pollStatus).catch(err => { if(err.message !== "AUTH_REQUIRED") console.error(err) });
        }));
        
        document.getElementById('pause-resume-btn').addEventListener('click', (e) => {
            const endpoint = e.currentTarget.dataset.action === 'pause' ? window.API.queuePause : window.API.queueResume;
            window.apiRequest(endpoint, { method: 'POST' }).then(pollStatus).catch(err => console.error(err));
        });
        
        document.getElementById('logModal').addEventListener('hidden.bs.modal', () => {
            clearInterval(liveLogPollInterval);
            liveLogPollInterval = null;
        });
        
        document.getElementById('queue-list').addEventListener('click', (e) => {
            const deleteBtn = e.target.closest('.queue-action-btn[data-action="delete"]');
            if (deleteBtn) {
                const item = deleteBtn.closest('.list-group-item');
                item.style.opacity = '0.5';
                window.apiRequest(window.API.queueDelete(deleteBtn.dataset.jobId), {method: 'POST'})
                    .then(() => item.remove())
                    .catch(err => {
                        if(err.message !== "AUTH_REQUIRED") console.error(err);
                        item.style.opacity = '1';
                    });
            }
        });

        document.getElementById('history-list').addEventListener('click', (e) => {
            const actionBtn = e.target.closest('.history-action-btn');
            if (!actionBtn) return;
            
            const li = actionBtn.closest('.list-group-item');
            const logId = parseInt(li.dataset.logId, 10);
            const action = actionBtn.dataset.action;

            if (action === 'log') {
                viewStaticLog(logId);
            } else if (action === 'delete') {
                li.style.opacity = '0.5';
                window.apiRequest(window.API.historyDelete(logId), { method: 'POST' })
                    .then(() => li.remove())
                    .catch(err => {
                        console.error(err);
                        li.style.opacity = '1';
                    });
            } else if (action === 'requeue') {
                actionBtn.disabled = true;
                window.apiRequest(window.API.queueContinue, { 
                    method: 'POST', 
                    body: JSON.stringify({ log_id: logId }) 
                })
                .then((data) => {
                    window.showToast(data.message, 'Success', 'success');
                    renderState(data.newState);
                })
                .catch(err => console.error(err))
                .finally(() => actionBtn.disabled = false);
            }
        });

        sortableInstance = Sortable.create(document.getElementById('queue-list'), {
            handle: '.queue-handle',
            animation: 150,
            ghostClass: 'sortable-ghost',
            onEnd: function (evt) {
                const orderedIds = [...evt.to.children].map(li => li.dataset.jobId).filter(id => id);
                if (orderedIds.length === 0) return;
                window.apiRequest(window.API.queueReorder, { method: 'POST', body: JSON.stringify({ order: orderedIds }) })
                .catch(err => { 
                    console.error("Failed to reorder queue:", err);
                    pollStatus(); // Re-poll to correct the UI on failure
                });
            },
        });

        pollStatus();
        checkForUpdates();
        setInterval(checkForUpdates, 900000); // 15 minutes
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

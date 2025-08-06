(function() {
    // --- GLOBAL STATE & CONFIG ---
    let logModalInstance, toastInstance, updateModalInstance;
    let liveLogEventSource = null;
    let globalHistoryData = [];
    let statusPollTimeout; // To hold the timer for polling

    // --- CORE UTILITY FUNCTIONS ---
    const showToast = (message, title = 'Notification', type = 'success') => {
        const toastEl = document.getElementById('actionToast');
        document.getElementById('toastTitle').textContent = title;
        document.getElementById('toastBody').textContent = message;
        toastEl.classList.remove('bg-success', 'bg-danger', 'bg-info', 'text-white');
        toastEl.classList.add(type === 'danger' ? 'bg-danger' : (type === 'info' ? 'bg-info' : 'bg-success'), 'text-white');
        if (!toastInstance) toastInstance = new bootstrap.Toast(toastEl);
        toastInstance.show();
    };

    async function apiRequest(endpoint, options = {}) {
        try {
            const res = await fetch(endpoint, options);
            if (!res.ok) {
                const errorData = await res.json().catch(() => ({ message: `Request failed: ${res.statusText}` }));
                throw new Error(errorData.message || res.statusText);
            }
            if (res.headers.get("Content-Type")?.includes("application/json")) {
                return res.json();
            }
            return res;
        } catch (error) {
            if (error.name !== 'AbortError') {
                showToast(error.message, 'Error', 'danger');
            }
            throw error;
        }
    }

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
            const data = await apiRequest('/api/update_check');
            const updateBtn = document.getElementById('update-notification-btn');
            if (data.update_available) {
                document.getElementById('update-version-text').textContent = data.latest_version;
                document.getElementById('update-notes-content').innerHTML = data.release_notes.replace(/\r\n/g, '<br>');
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
        const urlTextarea = document.querySelector('textarea[name="urls"]');
        const urls = urlTextarea.value.match(/(https?:\/\/[^\s]+|www\.[^\s]+)/g) || [];
        const isPlaylist = urls.length > 0 && urls[0].includes('playlist?list=');
        document.getElementById('playlist-range-options').style.display = isPlaylist ? 'flex' : 'none';
    };
    
    const handleStopRequest = (mode) => {
        const statusPara = document.querySelector('#current-status p.small');
        const stopSaveBtn = document.getElementById('stop-save-btn');
        const cancelBtn = document.getElementById('cancel-btn');
        const viewLogBtn = document.getElementById('view-log-btn');

        if (stopSaveBtn) stopSaveBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        if (viewLogBtn) viewLogBtn.disabled = true;

        if (statusPara) {
            statusPara.textContent = mode === 'save' ? 'Stopping...' : 'Cancelling...';
        }

        apiRequest('/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: mode })
        }).catch(err => console.error("Stop request failed", err));
    };

    // --- RENDERING LOGIC ---
    function renderCurrentStatus(current) {
        const currentDiv = document.getElementById("current-status");

        if (current && current.url) {
            const thumbnailHTML = current.thumbnail 
                ? `<div class="flex-shrink-0 mb-3 mb-md-0 me-md-3"><img src="${current.thumbnail}" class="now-downloading-thumbnail" alt="Thumbnail"></div>`
                : '';

            const playlistIndicator = current.playlist_count > 0 
                ? `<span class="ms-2 badge bg-secondary">${current.playlist_index || '0'}/${current.playlist_count}</span>` 
                : '';

            const titleHTML = current.playlist_title 
                ? `<strong class="word-break">${current.playlist_title}</strong>${playlistIndicator}<br><small class="text-muted word-break">${current.track_title || 'Loading track...'}</small>` 
                : `<strong class="word-break">${current.title || current.url}</strong>`;
            
            const progress = current.progress ? current.progress.toFixed(1) : 0;
            const progressHTML = `<div class="progress mb-2" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width: ${progress}%;">${progress}%</div></div>`;
            
            const statsHTML = `<div class="d-flex justify-content-between mt-2 small text-muted"><small>Size: ${current.file_size || 'N/A'}</small><small>Speed: ${current.speed || 'N/A'}</small><small>ETA: ${current.eta || 'N/A'}</small></div>`;

            const buttonsHTML = `<div class="btn-group mt-2" role="group">
                <button id="view-log-btn" class="btn btn-info btn-sm">View Log</button>
                <button id="stop-save-btn" class="btn btn-warning btn-sm" title="Stop download and save completed files.">Stop & Save</button>
                <button id="cancel-btn" class="btn btn-danger btn-sm" title="Cancel download and delete all temporary files.">Cancel</button>
            </div>`;

            currentDiv.innerHTML = `
                <div class="d-flex flex-column flex-md-row">
                    ${thumbnailHTML}
                    <div class="flex-grow-1" style="min-width: 0;">
                        ${titleHTML}
                        ${progressHTML}
                        <p class="mb-1 small">${current.status}</p>
                        ${statsHTML}
                        ${buttonsHTML}
                    </div>
                </div>
            `;
            
            document.getElementById('view-log-btn').addEventListener('click', viewLiveLog);
            document.getElementById('stop-save-btn').addEventListener('click', () => handleStopRequest('save'));
            document.getElementById('cancel-btn').addEventListener('click', () => handleStopRequest('cancel'));

        } else {
            currentDiv.innerHTML = "<p>No active download.</p>";
        }
    }

    function renderQueue(queue) {
        const queueList = document.getElementById("queue-list");
        document.getElementById("queue-controls").style.display = queue.length > 0 ? 'flex' : 'none';
        
        if (queue.length === 0) {
            queueList.innerHTML = "<li class='list-group-item'>Queue is empty.</li>";
        } else {
            const queueHTML = queue.map(job => 
                `<li class="list-group-item d-flex justify-content-between align-items-center" data-job-id="${job.id}">
                    <div class="d-flex align-items-center" style="min-width: 0;">
                        <i class="bi bi-grip-vertical queue-handle me-2"></i>
                        <span class="word-break">${job.folder ? `<strong>${job.folder}</strong>: ` : ''}${job.url}</span>
                    </div>
                    <button class="btn-close queue-action-btn" data-action="delete" data-job-id="${job.id}"></button>
                </li>`
            ).join('');
            queueList.innerHTML = queueHTML;
        }
    }

    function renderHistory(history) {
        globalHistoryData = history; 
        const historyForDisplay = [...globalHistoryData].reverse();
        const historyList = document.getElementById("history-list");
        document.getElementById("clear-history-btn").style.display = historyForDisplay.length > 0 ? 'block' : 'none';

        if (historyForDisplay.length === 0) {
            historyList.innerHTML = "<li class='list-group-item'>Nothing in history.</li>";
        } else {
            historyList.innerHTML = historyForDisplay.map(item => createHistoryItemElement(item)).join('');
        }
    }

    function createHistoryItemElement(item) {
        let badgeClass = 'bg-secondary';
        let actionButton = `<button class="btn btn-sm btn-outline-secondary history-action-btn" data-action="requeue" title="Download Again"><i class="bi bi-arrow-clockwise"></i></button>`;
        switch(item.status) {
            case 'COMPLETED': badgeClass = 'bg-success'; break;
            case 'PARTIAL': badgeClass = 'bg-info text-dark'; break;
            case 'STOPPED': 
                badgeClass = 'bg-warning text-dark'; 
                actionButton = `<button class="btn btn-sm btn-outline-success history-action-btn" data-action="requeue" title="Retry Download"><i class="bi bi-play-fill"></i></button>`;
                break;
            case 'CANCELLED': badgeClass = 'bg-secondary'; break;
            case 'FAILED': case 'ERROR': badgeClass = 'bg-danger'; break;
        }

        // --- NEW: Create the error summary block if an error exists ---
        let errorSummaryHTML = '';
        if (item.error_summary) {
            // Using Bootstrap's collapse component for a clean look
            const collapseId = `error-summary-${item.log_id}`;
            // Sanitize the error summary to prevent HTML injection
            const sanitizedSummary = item.error_summary.replace(/</g, "&lt;").replace(/>/g, "&gt;");
            errorSummaryHTML = `
                <div class="mt-2">
                    <button class="btn btn-sm btn-outline-danger" type="button" data-bs-toggle="collapse" data-bs-target="#${collapseId}" aria-expanded="false" aria-controls="${collapseId}">
                        <i class="bi bi-exclamation-triangle-fill"></i> Show Error Summary
                    </button>
                </div>
                <div class="collapse" id="${collapseId}">
                    <pre class="card card-body mt-2 p-2 bg-body-tertiary small" style="max-height: 200px; overflow-y: auto;"><code>${sanitizedSummary}</code></pre>
                </div>
            `;
        }

        return `<li class="list-group-item" data-log-id="${item.log_id}" data-status="${item.status}">
            <div class="d-flex justify-content-between align-items-center">
                <div class="flex-grow-1" style="min-width: 0;">
                    <span class="badge ${badgeClass} me-2">${item.status}</span>
                    <strong class="word-break">${item.title || "Unknown"}</strong>
                    <br>
                    <small class="text-muted word-break">${item.folder ? `Folder: ${item.folder}` : `URL: ${item.url}`}</small>
                </div>
                <div class="btn-group ms-2">${actionButton}<button class="btn btn-sm btn-outline-info history-action-btn" data-action="log" title="View Log"><i class="bi bi-file-text"></i></button><button class="btn btn-sm btn-outline-danger history-action-btn" data-action="delete" title="Delete"><i class="bi bi-trash-fill"></i></button></div>
            </div>
            ${errorSummaryHTML}
        </li>`;
    }

    function renderPauseState(is_paused) {
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

    function renderStatus(data) {
        renderCurrentStatus(data.current);
        renderQueue(data.queue);
        renderHistory(data.history);
        renderPauseState(data.is_paused);
    }

    const pollStatus = async () => {
        try {
            const data = await apiRequest('/api/status');
            renderStatus(data);
        } catch (error) {
            console.error("Status poll failed:", error);
        } finally {
            clearTimeout(statusPollTimeout);
            statusPollTimeout = setTimeout(pollStatus, 1500);
        }
    };

    const requeueJob = async (job) => {
        try {
            await apiRequest('/queue/continue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(job) });
            showToast("Job re-queued successfully.", 'Success', 'success');
        } catch(error) { console.error("Failed to re-queue job:", error); }
    };

    const viewStaticLog = async (logId) => {
        try {
            if (liveLogEventSource) liveLogEventSource.close();
            const data = await apiRequest(`/history/log/${logId}`);
            const logContentEl = document.getElementById('logModalContent');
            logContentEl.textContent = data.log;
            if (!logModalInstance) logModalInstance = new bootstrap.Modal(document.getElementById('logModal'));
            logModalInstance.show();
            logContentEl.scrollTop = logContentEl.scrollHeight;
        } catch (error) { console.error("Could not fetch log:", error); }
    };

    const viewLiveLog = () => {
        if (liveLogEventSource) liveLogEventSource.close();
        const logContentEl = document.getElementById('logModalContent');
        logContentEl.textContent = 'Connecting to live log stream...';
        if (!logModalInstance) logModalInstance = new bootstrap.Modal(document.getElementById('logModal'));
        logModalInstance.show();

        liveLogEventSource = new EventSource('/api/log/live/stream');
        let isFirstMessage = true;
        liveLogEventSource.onmessage = function(event) {
            if (isFirstMessage) {
                logContentEl.textContent = '';
                isFirstMessage = false;
            }
            logContentEl.textContent += event.data + '\n';
            logContentEl.scrollTop = logContentEl.scrollHeight;
        };
        liveLogEventSource.onerror = function() {
            logContentEl.textContent += '\n--- Connection closed by server. ---';
            liveLogEventSource.close();
        };
    };

    // --- INITIALIZATION ---
    document.addEventListener('DOMContentLoaded', () => {
        const savedTheme = localStorage.getItem('downloader_theme') || 'light';
        applyTheme(savedTheme);
        document.getElementById('theme-toggle').addEventListener('click', () => {
            const newTheme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
            applyTheme(newTheme);
            localStorage.setItem('downloader_theme', newTheme);
        });

        const savedMode = localStorage.getItem('downloader_mode') || 'clip';
        switchMode(savedMode);
        document.querySelectorAll('.mode-selector .btn').forEach(btn => btn.addEventListener('click', () => switchMode(btn.dataset.mode)));

        document.getElementById('add-job-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            try {
                const data = await apiRequest('/queue', { method: 'POST', body: new FormData(this) });
                showToast(data.message, 'Success', 'success');
                this.reset();
                handleUrlInput();
            } catch(error) { 
                console.error("Failed to add job:", error);
            }
        });

        const urlTextarea = document.querySelector('textarea[name="urls"]');
        urlTextarea.addEventListener('input', handleUrlInput);
        
        document.getElementById('clear-queue-btn').addEventListener('click', () => showConfirmModal('Clear Queue?', 'Are you sure you want to remove all items from the queue?', () => apiRequest('/queue/clear', { method: 'POST' }).catch(err => console.error(err))));
        document.getElementById('clear-history-btn').addEventListener('click', () => showConfirmModal('Clear History?', 'Are you sure you want to clear the entire download history?', () => apiRequest('/history/clear', { method: 'POST' }).catch(err => console.error(err))));
        document.getElementById('pause-resume-btn').addEventListener('click', (e) => apiRequest(`/queue/${e.currentTarget.dataset.action}`, { method: 'POST' }).catch(err => console.error(err)));
        document.getElementById('logModal').addEventListener('hidden.bs.modal', () => { if (liveLogEventSource) { liveLogEventSource.close(); liveLogEventSource = null; } });
        
        document.getElementById('queue-list').addEventListener('click', (e) => {
            const deleteBtn = e.target.closest('.queue-action-btn[data-action="delete"]');
            if (deleteBtn) apiRequest(`/queue/delete/by-id/${deleteBtn.dataset.jobId}`, {method: 'POST'}).catch(err => console.error(err));
        });

        document.getElementById('history-list').addEventListener('click', (e) => {
            const actionBtn = e.target.closest('.history-action-btn');
            if (!actionBtn) return;
            const li = actionBtn.closest('.list-group-item');
            const logId = parseInt(li.dataset.logId, 10);
            const action = actionBtn.dataset.action;
            if (action === 'log') viewStaticLog(logId);
            else if (action === 'delete') apiRequest(`/history/delete/${logId}`, { method: 'POST' }).catch(err => console.error(err));
            else if (action === 'requeue') {
                const historyItem = globalHistoryData.find(item => item.log_id === logId);
                if (historyItem) requeueJob(historyItem.job_data);
            }
        });

        Sortable.create(document.getElementById('queue-list'), {
            handle: '.queue-handle',
            animation: 150,
            ghostClass: 'sortable-ghost',
            onEnd: function (evt) {
                const orderedIds = [...evt.to.children].map(li => li.dataset.jobId);
                apiRequest('/queue/reorder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ order: orderedIds }) })
                .catch(err => { console.error("Failed to reorder queue:", err); });
            },
        });

        pollStatus();

        checkForUpdates();
        setInterval(checkForUpdates, 900000);
    });
})();

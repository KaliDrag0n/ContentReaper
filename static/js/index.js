/**
 * static/js/index.js
 * This file contains the core logic for the main dashboard page.
 * It uses a local state cache and intelligent DOM diffing
 * for more efficient and stable UI updates.
 */
(function() {
    'use strict';

    // --- STATE & CACHED ELEMENTS ---
    let logModalInstance, updateModalInstance, scytheModalInstance;
    let urlInputTimeout;
    let liveLogPollInterval = null;
    let sortableInstance = null;
    let scytheModalWasVisible = false;

    let localState = {
        current: null,
        queue: [],
        history: [],
        scythes: [],
        is_paused: false,
    };

    // --- UI MODE LOGIC ---

    const switchMode = (mode, containerId = '') => {
        const container = containerId ? document.getElementById(containerId) : document;
        if (!container) return;
        
        container.querySelectorAll('.mode-selector .btn').forEach(b => b.classList.remove('active'));
        const activeButton = container.querySelector(`.mode-selector .btn[data-mode="${mode}"]`);
        if (activeButton) activeButton.classList.add('active');
        
        container.querySelectorAll('.mode-options').forEach(el => el.style.display = 'none');
        const optionsEl = container.querySelector(`[data-options-for="${mode}"]`);
        if(optionsEl) optionsEl.style.display = 'block';
        
        const inputEl = container.querySelector('input[name="download_mode"]');
        if(inputEl) inputEl.value = mode;

        if (!containerId) {
            localStorage.setItem('downloader_mode', mode);
        }
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

    const handleUrlInput = (inputElement) => {
        clearTimeout(urlInputTimeout);
        urlInputTimeout = setTimeout(() => {
            const urls = (inputElement.value.match(/(https?:\/\/[^\s"]+|www\.[^\s"]+)/g) || []).join('\n');
            inputElement.value = urls;
            const isPlaylist = urls.includes('playlist?list=');
            const container = inputElement.closest('form');
            const playlistOptions = container.querySelector('.playlist-range-options');
            if (playlistOptions) {
                playlistOptions.style.display = isPlaylist ? 'flex' : 'none';
            }
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

    // --- RENDERING LOGIC ---

    function renderCurrentStatus(current) {
        const currentDiv = document.getElementById("current-status");
        const wasPreviouslyDownloading = localState.current !== null;
        const isCurrentlyDownloading = current !== null && current.url;

        if (wasPreviouslyDownloading && !isCurrentlyDownloading) {
            currentDiv.innerHTML = "<p class='m-0'>No active download.</p>";
            return;
        }

        if (!isCurrentlyDownloading) {
            return;
        }

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

        if (newHistory.length === 0) {
            if (localState.history.length > 0 || !historyList.querySelector('.fst-italic')) {
                historyList.innerHTML = "<li class='list-group-item fst-italic text-muted'>History is empty.</li>";
            }
            return;
        }

        const newHistoryMap = new Map(newHistory.map(item => [item.log_id, item]));
        const oldHistoryMap = new Map(localState.history.map(item => [item.log_id, item]));

        oldHistoryMap.forEach((_, logId) => {
            if (!newHistoryMap.has(logId)) {
                historyList.querySelector(`[data-log-id='${logId}']`)?.remove();
            }
        });
        
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
                existingLi.innerHTML = createHistoryItemHTML(item);
                existingLi.dataset.status = item.status;
            }
        });

        historyList.querySelector('.fst-italic')?.remove();
    }

    function createHistoryItemHTML(item) {
        if (item.status === 'INFO') {
            return `
                <div class="d-flex align-items-center text-muted">
                    <i class="bi bi-info-circle-fill me-2"></i>
                    <small>${item.title}</small>
                </div>
            `;
        }

        let badgeClass = 'bg-secondary';
        switch(item.status) {
            case 'COMPLETED': badgeClass = 'bg-success'; break;
            case 'PARTIAL': badgeClass = 'bg-info text-dark'; break;
            case 'STOPPED': badgeClass = 'bg-warning text-dark'; break;
            case 'CANCELLED': badgeClass = 'bg-secondary'; break;
            case 'FAILED': case 'ERROR': case 'ABANDONED': badgeClass = 'bg-danger'; break;
        }

        const requeueButtonIcon = (item.status === 'STOPPED' || item.status === 'PARTIAL') 
            ? 'bi-play-fill' 
            : 'bi-arrow-clockwise';
        
        const requeueButtonTitle = (item.status === 'STOPPED' || item.status === 'PARTIAL')
            ? 'Continue Download'
            : 'Download Again';

        const sanitizedError = item.error_summary ? item.error_summary.replace(/</g, "&lt;").replace(/>/g, "&gt;") : '';

        return `
            <div class="d-flex justify-content-between align-items-center">
                <div class="flex-grow-1" style="min-width: 0;">
                    <span class="badge ${badgeClass} me-2">${item.status}</span>
                    <strong class="word-break">${item.title || "Unknown"}</strong>
                    <br>
                    <small class="text-muted word-break">${item.folder ? `Folder: ${item.folder}` : `URL: ${item.url}`}</small>
                </div>
                <div class="btn-group ms-2">
                    <button class="btn btn-sm btn-outline-primary history-action-btn" data-action="scythe" title="Add to Scythes" aria-label="Add to Scythes">
                        <i class="bi bi-plus-lg"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-success history-action-btn" data-action="requeue" title="${requeueButtonTitle}" aria-label="${requeueButtonTitle}">
                        <i class="bi ${requeueButtonIcon}"></i>
                        <span class="spinner-border spinner-border-sm d-none" role="status" aria-hidden="true"></span>
                    </button>
                    <button class="btn btn-sm btn-outline-info history-action-btn" data-action="log" title="View Log" aria-label="View Log">
                        <i class="bi bi-file-text"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger history-action-btn" data-action="delete" title="Delete" aria-label="Delete">
                        <i class="bi bi-trash-fill"></i>
                    </button>
                </div>
            </div>
            ${item.error_summary ? `
            <div class="mt-2">
                <button class="btn btn-sm btn-outline-danger" type="button" data-bs-toggle="collapse" data-bs-target="#error-summary-${item.log_id}" aria-expanded="false">
                    <i class="bi bi-exclamation-triangle-fill"></i> Show Error
                </button>
            </div>
            <div class="collapse" id="error-summary-${item.log_id}">
                <pre class="card card-body mt-2 p-2 bg-body-tertiary small" style="max-height: 200px; overflow-y: auto;"><code>${sanitizedError}</code></pre>
            </div>` : ''}
        `;
    }

    function renderScythes(newScythes) {
        const scythesList = document.getElementById("scythes-list");
        
        if (newScythes.length === 0) {
            if (localState.scythes.length > 0 || !scythesList.querySelector('.fst-italic')) {
                scythesList.innerHTML = "<li class='list-group-item fst-italic text-muted'>No Scythes saved yet.</li>";
            }
            return;
        }

        const newScythesMap = new Map(newScythes.map(item => [item.id, item]));
        const oldScythesMap = new Map(localState.scythes.map(item => [item.id, item]));

        scythesList.querySelectorAll('.list-group-item[data-scythe-id]').forEach(li => {
            const scytheId = parseInt(li.dataset.scytheId, 10);
            if (!newScythesMap.has(scytheId)) {
                li.remove();
            }
        });

        newScythes.forEach(scythe => {
            let li = scythesList.querySelector(`[data-scythe-id='${scythe.id}']`);
            const oldScythe = oldScythesMap.get(scythe.id);
            const needsUpdate = !oldScythe || JSON.stringify(scythe) !== JSON.stringify(oldScythe);

            if (!li) {
                li = document.createElement('li');
                li.className = 'list-group-item';
                li.dataset.scytheId = scythe.id;
                scythesList.appendChild(li);
                li.innerHTML = createScytheItemHTML(scythe);
            } else if (needsUpdate) {
                li.innerHTML = createScytheItemHTML(scythe);
            }
        });

        scythesList.querySelector('.fst-italic')?.remove();
    }

    function createScytheItemHTML(scythe) {
        const jobData = scythe.job_data || {};
        const scheduleIcon = scythe.schedule && scythe.schedule.enabled 
            ? '<i class="bi bi-clock-history ms-2" title="Scheduled"></i>' 
            : '';

        return `
            <div class="d-flex justify-content-between align-items-center">
                <div class="flex-grow-1" style="min-width: 0;">
                    <strong class="word-break">${scythe.name || "Untitled Scythe"}</strong>${scheduleIcon}
                    <br>
                    <small class="text-muted word-break">${jobData.url || "No URL"}</small>
                </div>
                <div class="btn-group ms-2">
                    <button class="btn btn-sm btn-success scythe-action-btn" data-action="reap" title="Reap Now" aria-label="Reap Now">
                        <i class="bi bi-scissors scythe-icon"></i>
                    </button>
                    <button class="btn btn-sm btn-secondary scythe-action-btn" data-action="edit" title="Edit" aria-label="Edit Scythe">
                        <i class="bi bi-pencil-fill"></i>
                    </button>
                    <button class="btn btn-sm btn-danger scythe-action-btn" data-action="delete" title="Delete" aria-label="Delete Scythe">
                        <i class="bi bi-trash-fill"></i>
                    </button>
                </div>
            </div>
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
        renderScythes(newState.scythes);
        renderPauseState(newState.is_paused);
        localState = newState;
    }

    const viewStaticLog = async (logId) => {
        try {
            clearInterval(liveLogPollInterval);
            const data = await window.apiRequest(window.API.historyItem(logId, true));
            
            const logContentEl = document.getElementById('logModalContent');
            logContentEl.textContent = data.log_content || "Log is empty or could not be loaded.";
            
            if (!logModalInstance) logModalInstance = new bootstrap.Modal(document.getElementById('logModal'));
            logModalInstance.show();
            logContentEl.scrollTop = logContentEl.scrollHeight;
        } catch (error) { 
            console.error("Could not fetch log:", error); 
        }
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

    // --- Scythe Editor Logic ---
    const openScytheEditor = (scythe = null) => {
        const modalEl = document.getElementById('scytheEditorModal');
        if (!scytheModalInstance) {
            scytheModalInstance = new bootstrap.Modal(modalEl);
        }

        const form = document.getElementById('scythe-editor-form');
        form.reset();

        const titleEl = document.getElementById('scytheEditorTitle');
        const idInput = document.getElementById('scythe-editor-id');
        const nameInput = document.getElementById('scythe-editor-name');
        const urlInput = document.getElementById('scythe-editor-url');
        const archiveInput = document.getElementById('scythe-editor-use-archive');
        
        const scheduleEnabled = document.getElementById('schedule-enabled');
        const scheduleOptionsContainer = document.getElementById('schedule-options-container');
        const scheduleInterval = document.getElementById('schedule-interval');
        const scheduleWeeklyOptions = document.getElementById('schedule-options-weekly');
        const scheduleTime = document.getElementById('schedule-time');

        const optionsContainer = document.getElementById('scythe-editor-options-container');
        const mainForm = document.getElementById('add-job-form');
        optionsContainer.innerHTML = `
            <div class="row text-center mode-selector mb-4 g-2">
              <div class="col"><button type="button" class="btn btn-outline-primary" data-mode="music"><i class="bi bi-music-note-beamed"></i> Music</button></div>
              <div class="col"><button type="button" class="btn btn-outline-primary" data-mode="video"><i class="bi bi-film"></i> Video</button></div>
              <div class="col"><button type="button" class="btn btn-outline-primary" data-mode="clip"><i class="bi bi-scissors"></i> Clip</button></div>
              <div class="col"><button type="button" class="btn btn-outline-primary" data-mode="custom"><i class="bi bi-terminal"></i> Custom</button></div>
            </div>
            <input type="hidden" name="download_mode">
            <div data-options-for="music" class="mode-options">${mainForm.querySelector('[data-options-for="music"]').innerHTML}</div>
            <div data-options-for="video" class="mode-options">${mainForm.querySelector('[data-options-for="video"]').innerHTML}</div>
            <div data-options-for="clip" class="mode-options">${mainForm.querySelector('[data-options-for="clip"]').innerHTML}</div>
            <div data-options-for="custom" class="mode-options">${mainForm.querySelector('[data-options-for="custom"]').innerHTML}</div>
            <div class="row mb-3 playlist-range-options" style="display: none;">${mainForm.querySelector('.playlist-range-options').innerHTML}</div>
            <hr><h5>Post-Processing</h5><div class="form-check form-switch mb-2"><input class="form-check-input" type="checkbox" role="switch" name="embed_lyrics"><label class="form-check-label">Embed Lyrics</label></div><div class="form-check form-switch"><input class="form-check-input" type="checkbox" role="switch" name="split_chapters"><label class="form-check-label">Split by Chapters</label></div>
        `;
        
        optionsContainer.querySelectorAll('.mode-selector .btn').forEach(btn => {
            btn.addEventListener('click', () => switchMode(btn.dataset.mode, 'scythe-editor-options-container'));
        });
        urlInput.addEventListener('input', (e) => handleUrlInput(e.target));

        if (scythe) { // Editing existing Scythe
            titleEl.textContent = 'Edit Scythe';
            idInput.value = scythe.id;
            nameInput.value = scythe.name;
            
            const jobData = scythe.job_data || {};
            urlInput.value = jobData.url || '';
            archiveInput.checked = jobData.archive || false;
            
            const scheduleData = scythe.schedule || {};
            scheduleEnabled.checked = scheduleData.enabled || false;
            scheduleInterval.value = scheduleData.interval || 'daily';
            scheduleTime.value = scheduleData.time || '03:00';

            form.querySelectorAll('.weekday-selector .form-check-input').forEach(cb => {
                cb.checked = (scheduleData.weekdays || []).includes(parseInt(cb.value, 10));
            });

            const mode = jobData.mode || 'clip';
            switchMode(mode, 'scythe-editor-options-container');
            
            const folderInput = optionsContainer.querySelector(`[data-options-for="${mode}"] [name="${mode}_foldername"]`);
            if (folderInput) folderInput.value = jobData.folder || '';

            Object.keys(jobData).forEach(key => {
                const input = optionsContainer.querySelector(`[name="${key}"]`);
                if (input && key !== 'folder') {
                    if (input.type === 'checkbox') input.checked = !!jobData[key];
                    else input.value = jobData[key];
                }
            });

        } else { // Creating new Scythe
            titleEl.textContent = 'New Scythe';
            idInput.value = '';
            scheduleEnabled.checked = false;
            scheduleTime.value = '03:00';
            switchMode('clip', 'scythe-editor-options-container');
        }
        
        scheduleOptionsContainer.style.display = scheduleEnabled.checked ? 'block' : 'none';
        scheduleWeeklyOptions.style.display = scheduleInterval.value === 'weekly' ? 'block' : 'none';
        
        handleUrlInput(urlInput);
        scytheModalInstance.show();
    };

    // --- INITIALIZATION ---
    const initializePage = () => {
        document.addEventListener('state-update', (e) => {
            renderState(e.detail);
        });

        const savedMode = localStorage.getItem('downloader_mode') || 'clip';
        switchMode(savedMode);
        document.querySelectorAll('#add-job-form .mode-selector .btn').forEach(btn => btn.addEventListener('click', () => switchMode(btn.dataset.mode)));

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
                switchMode(savedMode);
                handleUrlInput(this.querySelector('textarea[name="urls"]'));
            } catch(error) { 
                if (error.message !== "AUTH_REQUIRED") console.error("Failed to add job:", error);
            } finally {
                submitButton.disabled = false;
            }
        });

        document.querySelector('textarea[name="urls"]').addEventListener('input', (e) => handleUrlInput(e.target));
        
        document.getElementById('clear-queue-btn').addEventListener('click', () => window.showConfirmModal('Clear Queue?', 'Are you sure you want to remove all items from the queue?', () => {
            window.apiRequest(window.API.queueClear, { method: 'POST' }).catch(err => { if(err.message !== "AUTH_REQUIRED") console.error(err) });
        }));
        
        document.getElementById('clear-history-btn').addEventListener('click', () => window.showConfirmModal('Clear History?', 'Are you sure you want to clear the entire download history?', () => {
            window.apiRequest(window.API.historyClear, { method: 'POST' }).catch(err => { if(err.message !== "AUTH_REQUIRED") console.error(err) });
        }));
        
        document.getElementById('pause-resume-btn').addEventListener('click', (e) => {
            const endpoint = e.currentTarget.dataset.action === 'pause' ? window.API.queuePause : window.API.queueResume;
            window.apiRequest(endpoint, { method: 'POST' }).catch(err => console.error(err));
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
                    .catch(err => {
                        console.error(err);
                        li.style.opacity = '1';
                    });
            } else if (action === 'requeue') {
                actionBtn.disabled = true;
                actionBtn.querySelector('.spinner-border').classList.remove('d-none');
                actionBtn.querySelector('i').classList.add('d-none');
                window.apiRequest(window.API.queueContinue, { 
                    method: 'POST', 
                    body: JSON.stringify({ log_id: logId }) 
                })
                .then((data) => {
                    window.showToast(data.message, 'Success', 'success');
                })
                .catch(err => console.error(err))
                .finally(() => {
                    actionBtn.disabled = false;
                    actionBtn.querySelector('.spinner-border').classList.add('d-none');
                    actionBtn.querySelector('i').classList.remove('d-none');
                });
            } else if (action === 'scythe') {
                actionBtn.disabled = true;
                window.apiRequest(window.API.scythes, {
                    method: 'POST',
                    body: JSON.stringify({ log_id: logId })
                })
                .then((data) => {
                    window.showToast(data.message, 'Scythe Created', 'success');
                })
                .catch(err => {})
                .finally(() => {
                    actionBtn.disabled = false;
                });
            }
        });

        document.getElementById('scythes-list').addEventListener('click', (e) => {
            const actionBtn = e.target.closest('.scythe-action-btn');
            if (!actionBtn) return;

            const li = actionBtn.closest('.list-group-item');
            const scytheId = parseInt(li.dataset.scytheId, 10);
            const action = actionBtn.dataset.action;
            const scythe = localState.scythes.find(s => s.id === scytheId);

            if (action === 'delete') {
                window.showConfirmModal('Delete Scythe?', 'Are you sure you want to permanently delete this Scythe?', () => {
                    li.style.opacity = '0.5';
                    window.apiRequest(window.API.deleteScythe(scytheId), { method: 'DELETE' })
                        .then(data => {
                            window.showToast(data.message, 'Success', 'success');
                        })
                        .catch(err => { li.style.opacity = '1'; });
                });
            } else if (action === 'edit') {
                if (scythe) openScytheEditor(scythe);
            } else if (action === 'reap') {
                actionBtn.disabled = true;
                li.classList.add('reaping');
                setTimeout(() => li.classList.remove('reaping'), 1500);

                 window.apiRequest(window.API.reapScythe(scytheId), { method: 'POST' })
                    .then(data => {
                        window.showToast(data.message, 'Success', 'success');
                    })
                    .catch(err => console.error(err))
                    .finally(() => actionBtn.disabled = false);
            }
        });

        document.getElementById('new-scythe-btn').addEventListener('click', () => openScytheEditor(null));

        document.getElementById('scythe-editor-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const submitBtn = this.querySelector('button[type="submit"]');
            
            try {
                submitBtn.disabled = true;
                submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Saving...`;

                const id = this.querySelector('#scythe-editor-id').value;
                const name = this.querySelector('#scythe-editor-name').value;
                const url = this.querySelector('#scythe-editor-url').value;
                
                const optionsContainer = this.querySelector('#scythe-editor-options-container');
                const mode = optionsContainer.querySelector('input[name="download_mode"]').value;
                const modeOptions = optionsContainer.querySelector(`[data-options-for="${mode}"]`);

                const jobData = {
                    url: url,
                    mode: mode,
                    folder: modeOptions.querySelector(`[name="${mode}_foldername"]`)?.value || '',
                    archive: this.querySelector('#scythe-editor-use-archive').checked,
                    playlist_start: optionsContainer.querySelector('[name="playlist_start"]')?.value || null,
                    playlist_end: optionsContainer.querySelector('[name="playlist_end"]')?.value || null,
                    embed_lyrics: optionsContainer.querySelector('[name="embed_lyrics"]')?.checked || false,
                    split_chapters: optionsContainer.querySelector('[name="split_chapters"]')?.checked || false,
                };

                if (mode === 'music') {
                    jobData.format = modeOptions.querySelector('[name="music_audio_format"]')?.value;
                    jobData.quality = modeOptions.querySelector('[name="music_audio_quality"]')?.value;
                } else if (mode === 'video') {
                    jobData.quality = modeOptions.querySelector('[name="video_quality"]')?.value;
                    jobData.format = modeOptions.querySelector('[name="video_format"]')?.value;
                    jobData.embed_subs = modeOptions.querySelector('[name="video_embed_subs"]')?.checked || false;
                    jobData.codec = modeOptions.querySelector('[name="video_codec_preference"]')?.value;
                } else if (mode === 'clip') {
                    jobData.format = modeOptions.querySelector('[name="clip_format"]')?.value;
                } else if (mode === 'custom') {
                    jobData.custom_args = modeOptions.querySelector('[name="custom_args"]')?.value;
                }
                
                const weekdays = Array.from(this.querySelectorAll('.weekday-selector .form-check-input:checked'))
                                      .map(cb => parseInt(cb.value, 10));

                const schedule = {
                    enabled: this.querySelector('#schedule-enabled').checked,
                    interval: this.querySelector('#schedule-interval').value,
                    weekdays: weekdays,
                    time: this.querySelector('#schedule-time').value
                };
                
                const payload = { name, job_data: jobData, schedule };
                
                const endpoint = id ? window.API.updateScythe(id) : window.API.scythes;
                const method = id ? 'PUT' : 'POST';

                const data = await window.apiRequest(endpoint, {
                    method: method,
                    body: JSON.stringify(payload)
                });
                window.showToast(data.message, 'Success', 'success');
                scytheModalInstance.hide();

            } catch(err) {
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = 'Save Scythe';
            }
        });
        
        document.getElementById('schedule-enabled').addEventListener('change', (e) => {
            document.getElementById('schedule-options-container').style.display = e.target.checked ? 'block' : 'none';
        });
        document.getElementById('schedule-interval').addEventListener('change', (e) => {
            document.getElementById('schedule-options-weekly').style.display = e.target.value === 'weekly' ? 'block' : 'none';
        });

        document.addEventListener('login-modal-shown', () => {
            if (scytheModalInstance && scytheModalInstance._isShown) {
                scytheModalWasVisible = true;
                scytheModalInstance.hide();
            }
        });
        
        const loginModalEl = document.getElementById('loginModal');
        if (loginModalEl) {
            loginModalEl.addEventListener('hidden.bs.modal', () => {
                if (scytheModalWasVisible) {
                    scytheModalWasVisible = false;
                }
            });
        }

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
                });
            },
        });

        checkForUpdates();
        setInterval(checkForUpdates, 900000);
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
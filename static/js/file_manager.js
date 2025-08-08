/**
 * static/js/file_manager.js
 * Handles all logic for the file manager page, with more efficient
 * DOM rendering and interaction handling.
 */

(function() {
    'use strict';

    // --- UTILITY FUNCTIONS ---

    const formatBytes = (bytes, decimals = 2) => {
        if (!Number.isFinite(bytes) || bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    };

    // --- UI STATE MANAGEMENT ---

    const updateSelectionActions = () => {
        const selectedItems = document.querySelectorAll('.file-item-checkbox:checked');
        const actionsPanel = document.getElementById('selection-actions');
        const countSpan = document.getElementById('selection-count');
        const selectAllCheckbox = document.getElementById('select-all-checkbox');
        const allCheckboxes = document.querySelectorAll('.file-item-checkbox');

        actionsPanel.style.display = selectedItems.length > 0 ? 'flex' : 'none';
        if (selectedItems.length > 0) {
            countSpan.textContent = `${selectedItems.length} item(s) selected`;
        }
        
        if (allCheckboxes.length > 0) {
            selectAllCheckbox.checked = selectedItems.length === allCheckboxes.length;
            selectAllCheckbox.indeterminate = selectedItems.length > 0 && selectedItems.length < allCheckboxes.length;
        } else {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = false;
        }
    };

    // --- CORE LOGIC ---

    const fetchAndRenderFiles = async (path = '', containerEl) => {
        // Provide immediate feedback that content is loading.
        containerEl.innerHTML = '<div class="list-group-item"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading...</div>';
        
        try {
            const files = await window.apiRequest(window.API.files(path));
            containerEl.innerHTML = ''; // Clear the loading indicator.

            if (files.length === 0) {
                containerEl.innerHTML = '<div class="list-group-item fst-italic text-muted">Folder is empty.</div>';
                return;
            }

            // --- CHANGE: Use a document fragment for more efficient rendering ---
            const fragment = document.createDocumentFragment();
            files.forEach(item => {
                const isDirectory = item.type === 'directory';
                const icon = isDirectory ? 'bi-folder-fill text-primary' : 'bi-file-earmark-music-fill';
                // Create a more robust unique ID for the collapsable element.
                const uniqueId = `item-collapse-${item.path.replace(/[^a-zA-Z0-9]/g, '-')}`;

                const li = document.createElement('div');
                li.className = 'list-group-item file-item';
                li.dataset.path = item.path;
                li.dataset.name = item.name;
                
                let sizeInfo = '';
                if (item.size != null) {
                    sizeInfo = `<br><small class="text-muted">${formatBytes(item.size)}</small>`;
                } else if (item.item_count != null) {
                    const plural = item.item_count === 1 ? 'item' : 'items';
                    sizeInfo = `<br><small class="text-muted">${item.item_count} ${plural}</small>`;
                }

                let itemHTML = `
                    <div class="d-flex justify-content-between align-items-center">
                        <div class="d-flex align-items-start flex-grow-1" style="min-width: 0;">
                            <input class="form-check-input me-3 mt-1 file-item-checkbox" type="checkbox" value="${item.path}">
                            <div class="flex-grow-1">`;

                if (isDirectory) {
                    itemHTML += `<a class="d-flex align-items-start text-decoration-none text-body folder-toggle" data-bs-toggle="collapse" href="#${uniqueId}" role="button" aria-expanded="false" aria-controls="${uniqueId}">
                                    <i class="bi bi-caret-right-fill me-2 pt-1"></i>`;
                }
                
                itemHTML += `<i class="bi ${icon} me-2 pt-1"></i><div class="word-break"><span class="fw-medium">${item.name}</span>${sizeInfo}</div>`;

                if (isDirectory) itemHTML += `</a>`;

                itemHTML += `
                            </div>
                        </div>
                        <div class="btn-group ms-2">
                            <a href="${window.API.downloadItem([item.path])}" class="btn btn-sm btn-success download-btn" title="${isDirectory ? 'Download as .zip' : 'Download File'}"><i class="bi ${isDirectory ? 'bi-file-earmark-zip-fill' : 'bi-download'}"></i></a>
                            <button class="btn btn-sm btn-danger delete-btn" title="Delete"><i class="bi bi-trash-fill"></i></button>
                        </div>
                    </div>
                `;

                if (isDirectory) {
                    itemHTML += `<div class="collapse" id="${uniqueId}"><div class="list-group list-group-flush file-list-nested mt-2"></div></div>`;
                }
                
                li.innerHTML = itemHTML;
                fragment.appendChild(li);
            });
            containerEl.appendChild(fragment);

        } catch (error) {
            // Provide a more user-friendly error message with a retry option.
            containerEl.innerHTML = `<div class="list-group-item text-danger">
                Error: ${error.message}
                <button class="btn btn-sm btn-outline-danger ms-2 retry-btn">Retry</button>
            </div>`;
            containerEl.querySelector('.retry-btn').addEventListener('click', () => fetchAndRenderFiles(path, containerEl));
            console.error("Error fetching files:", error);
        } finally {
            updateSelectionActions();
        }
    };

    const handleDelete = (paths, names) => {
        const title = names.length > 1 ? `Delete ${names.length} items?` : `Delete "${names[0]}"?`;
        window.showConfirmModal(title, 'Are you sure you want to permanently delete the selected item(s)? This cannot be undone.', async () => {
            try {
                await window.apiRequest(window.API.deleteItem, { 
                    method: 'POST', 
                    body: JSON.stringify({ paths: paths }) 
                });
                // Instead of a full refresh, just remove the deleted elements from the DOM.
                const rootContainer = document.getElementById('file-list-root');
                paths.forEach(path => {
                    rootContainer.querySelector(`.file-item[data-path="${path}"]`)?.remove();
                });
                updateSelectionActions();
            } catch (error) {
                if (error.message !== "AUTH_REQUIRED") {
                    console.error("Delete failed:", error);
                }
            }
        });
    };

    // --- INITIALIZATION ---

    const initializePage = () => {
        // Common page initialization
        const savedTheme = localStorage.getItem('downloader_theme') || 'light';
        window.applyTheme(savedTheme);
        document.getElementById('theme-toggle').addEventListener('click', () => {
            const newTheme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
            window.applyTheme(newTheme);
            localStorage.setItem('downloader_theme', newTheme);
        });

        const loginBtn = document.getElementById('login-btn');
        const logoutBtn = document.getElementById('logout-btn');
        window.apiRequest(window.API.authStatus).then(status => {
            if (status.password_set && !status.logged_in) loginBtn.style.display = 'inline-block';
            if (status.logged_in) logoutBtn.style.display = 'inline-block';
        });
        loginBtn.addEventListener('click', () => window.showLoginModal());

        const rootContainer = document.getElementById('file-list-root');
        const refreshBtn = document.getElementById('refresh-btn');
        
        refreshBtn.addEventListener('click', async () => {
            const icon = refreshBtn.querySelector('i');
            const originalIconClass = icon.className;
            refreshBtn.disabled = true;
            icon.className = 'spinner-border spinner-border-sm';
            
            try {
                await fetchAndRenderFiles('', rootContainer);
            } finally {
                refreshBtn.disabled = false;
                icon.className = originalIconClass;
            }
        });
        
        // Use a single event listener on the root container for performance (event delegation).
        rootContainer.addEventListener('click', (e) => {
            const target = e.target;
            
            // Handle folder expansion
            const folderToggle = target.closest('.folder-toggle');
            if (folderToggle) {
                const collapseEl = document.querySelector(folderToggle.getAttribute('href'));
                const nestedContainer = collapseEl.querySelector('.file-list-nested');
                // Fetch content only if the folder is being opened and has no content yet.
                if (nestedContainer && !nestedContainer.hasChildNodes()) {
                    const fileItem = target.closest('.file-item');
                    fetchAndRenderFiles(fileItem.dataset.path, nestedContainer);
                }
            }

            // Handle checkbox clicks
            if (target.classList.contains('file-item-checkbox')) {
                updateSelectionActions();
            }

            // Handle delete button clicks
            const deleteBtn = target.closest('.delete-btn');
            if (deleteBtn) {
                e.preventDefault();
                const fileItem = deleteBtn.closest('.file-item');
                handleDelete([fileItem.dataset.path], [fileItem.dataset.name]);
            }
        });

        document.getElementById('select-all-checkbox').addEventListener('change', (e) => {
            // Check or uncheck all visible checkboxes.
            document.querySelectorAll('.file-item-checkbox').forEach(cb => {
                cb.checked = e.target.checked;
            });
            updateSelectionActions();
        });

        document.getElementById('delete-selected-btn').addEventListener('click', () => {
            const selectedItems = document.querySelectorAll('.file-item-checkbox:checked');
            const paths = [];
            const names = [];
            selectedItems.forEach(cb => {
                const fileItem = cb.closest('.file-item');
                paths.push(fileItem.dataset.path);
                names.push(fileItem.dataset.name);
            });
            if (paths.length > 0) handleDelete(paths, names);
        });

        document.getElementById('download-selected-btn').addEventListener('click', () => {
            const selectedItems = document.querySelectorAll('.file-item-checkbox:checked');
            const paths = Array.from(selectedItems).map(cb => cb.closest('.file-item').dataset.path);
            if (paths.length === 0) return;
            // The download is handled by the browser, so we just redirect.
            window.location.href = window.API.downloadItem(paths);
        });

        // Initial load of the root directory.
        fetchAndRenderFiles('', rootContainer);
    };

    document.addEventListener('DOMContentLoaded', () => {
        // Wait for shared functions from app.js and api.js to be ready.
        const checkGlobals = setInterval(() => {
            if (window.applyTheme && window.apiRequest && window.API) {
                clearInterval(checkGlobals);
                initializePage();
            }
        }, 50);
    });
})();

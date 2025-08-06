// static/js/file_manager.js

const formatBytes = (bytes, decimals = 2) => {
    if (!Number.isFinite(bytes) || bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

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
    
    // Update the "Select All" checkbox state
    if (allCheckboxes.length > 0) {
        selectAllCheckbox.checked = selectedItems.length === allCheckboxes.length;
        selectAllCheckbox.indeterminate = selectedItems.length > 0 && selectedItems.length < allCheckboxes.length;
    } else {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
};

const fetchAndRenderFiles = async (path = '', containerEl) => {
    containerEl.innerHTML = '<div class="list-group-item"><div class="spinner-border spinner-border-sm me-2" role="status"></div>Loading...</div>';
    try {
        const response = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
        if (!response.ok) throw new Error('Failed to fetch file list.');
        const files = await response.json();
        containerEl.innerHTML = '';

        if (files.length === 0) {
            containerEl.innerHTML = '<div class="list-group-item fst-italic text-muted">Folder is empty.</div>';
            return;
        }

        files.forEach(item => {
            const isDirectory = item.type === 'directory';
            const icon = isDirectory ? 'bi-folder-fill text-primary' : 'bi-file-earmark-music-fill';
            const uniqueId = `item-${item.path.replace(/[^a-zA-Z0-9]/g, '-')}`;

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
                        <input class="form-check-input me-3 file-item-checkbox" type="checkbox" value="${item.path}">
            `;

            if (isDirectory) {
                itemHTML += `<a class="d-flex align-items-start text-decoration-none text-body folder-toggle" data-bs-toggle="collapse" href="#${uniqueId}" role="button" aria-expanded="false" aria-controls="${uniqueId}"><i class="bi bi-caret-right-fill me-2 pt-1"></i>`;
            }
            
            itemHTML += `<i class="bi ${icon} me-2 pt-1"></i><div class="flex-grow-1 word-break"><span class="fw-medium">${item.name}</span>${sizeInfo}</div>`;

            if (isDirectory) itemHTML += `</a>`;

            itemHTML += `
                    </div>
                    <div class="btn-group ms-2">
                        <a href="/download_item?paths=${encodeURIComponent(item.path)}" class="btn btn-sm btn-success download-btn" title="${isDirectory ? 'Download as .zip' : 'Download File'}"><i class="bi ${isDirectory ? 'bi-file-earmark-zip-fill' : 'bi-download'}"></i></a>
                        <button class="btn btn-sm btn-danger delete-btn" title="Delete"><i class="bi bi-trash-fill"></i></button>
                    </div>
                </div>
            `;

            if (isDirectory) itemHTML += `<div class="collapse" id="${uniqueId}"><div class="list-group list-group-flush file-list-nested mt-2"></div></div>`;
            
            li.innerHTML = itemHTML;
            containerEl.appendChild(li);
        });

    } catch (error) {
        containerEl.innerHTML = `<div class="list-group-item text-danger">Error: ${error.message}</div>`;
        console.error("Error fetching files:", error);
    } finally {
        updateSelectionActions();
    }
};

const handleDelete = (paths, names) => {
    const title = names.length > 1 ? `Delete ${names.length} items?` : `Delete "${names[0]}"?`;
    showConfirmModal(title, 'Are you sure you want to permanently delete the selected item(s)? This cannot be undone.', async () => {
        try {
            const response = await fetch('/api/delete_item', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths: paths }) });
            if (!response.ok) throw new Error('Delete request failed.');
            fetchAndRenderFiles('', document.getElementById('file-list-root'));
        } catch (error) {
            console.error("Delete failed:", error);
            alert("Failed to delete item(s). See console for details.");
        }
    });
};

document.addEventListener('DOMContentLoaded', () => {
    const rootContainer = document.getElementById('file-list-root');
    const savedTheme = localStorage.getItem('downloader_theme') || 'light';
    applyTheme(savedTheme);

    document.getElementById('theme-toggle').addEventListener('click', () => {
        const newTheme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
        applyTheme(newTheme);
        localStorage.setItem('downloader_theme', newTheme);
    });

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
    
    rootContainer.addEventListener('click', (e) => {
        const target = e.target;
        const fileItem = target.closest('.file-item');
        
        const folderToggle = target.closest('.folder-toggle');
        if (folderToggle) {
            const collapseEl = document.querySelector(folderToggle.getAttribute('href'));
            const nestedContainer = collapseEl.querySelector('.file-list-nested');
            // Only fetch if it hasn't been opened before
            if (nestedContainer && !nestedContainer.hasChildNodes()) {
                fetchAndRenderFiles(fileItem.dataset.path, nestedContainer);
            }
        }

        if (target.classList.contains('file-item-checkbox')) {
            updateSelectionActions();
        }

        if (target.closest('.delete-btn')) {
            e.preventDefault();
            handleDelete([fileItem.dataset.path], [fileItem.dataset.name]);
        }
    });

    document.getElementById('select-all-checkbox').addEventListener('change', (e) => {
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
        const queryParams = new URLSearchParams();
        paths.forEach(path => queryParams.append('paths', path));
        window.location.href = `/download_item?${queryParams.toString()}`;
    });

    fetchAndRenderFiles('', rootContainer);
});

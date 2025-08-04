/**
 * static/js/app.js
 * * Contains common JavaScript functions shared across multiple pages
 * to avoid code duplication.
 */

/**
 * Applies a color theme to the entire document.
 * @param {string} theme - The theme to apply ('light' or 'dark').
 */
const applyTheme = (theme) => {
    document.documentElement.dataset.bsTheme = theme;
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.checked = theme === 'dark';
    }
};

/**
 * Displays a Bootstrap confirmation modal.
 * @param {string} title - The title for the modal header.
 * @param {string} body - The text content for the modal body.
 * @param {function} onConfirm - The callback function to execute when the confirm button is clicked.
 */
const showConfirmModal = (title, body, onConfirm) => {
    const modalEl = document.getElementById('confirmModal');
    if (!modalEl) return;

    document.getElementById('confirmModalTitle').textContent = title;
    document.getElementById('confirmModalBody').textContent = body;
    const confirmButton = document.getElementById('confirmModalButton');
    
    // Clone and replace the button to remove any old event listeners.
    // This is a robust way to ensure the onConfirm callback is always the correct one.
    const newConfirmButton = confirmButton.cloneNode(true);
    confirmButton.parentNode.replaceChild(newConfirmButton, confirmButton);
    
    newConfirmButton.addEventListener('click', () => {
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        onConfirm();
        if (modalInstance) modalInstance.hide();
    });

    const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    modalInstance.show();
};

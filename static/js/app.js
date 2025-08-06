/**
 * static/js/app.js
 * * Contains common JavaScript functions shared across multiple pages
 * to avoid code duplication.
 */

// --- FIX: Define a variable in a scope accessible by the modal logic ---
let currentConfirmAction = () => {};

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
    
    // --- FIX: Instead of cloning, just update the action to be performed ---
    currentConfirmAction = () => {
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        // Ensure the callback is only called once and the modal is hidden.
        if (onConfirm) onConfirm();
        if (modalInstance) modalInstance.hide();
    };

    const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    modalInstance.show();
};

// --- FIX: Add a single, persistent event listener when the DOM is loaded ---
document.addEventListener('DOMContentLoaded', () => {
    const confirmButton = document.getElementById('confirmModalButton');
    if (confirmButton) {
        confirmButton.addEventListener('click', () => {
            // This listener now simply calls whatever function is currently assigned.
            if (typeof currentConfirmAction === 'function') {
                currentConfirmAction();
            }
        });
    }
});

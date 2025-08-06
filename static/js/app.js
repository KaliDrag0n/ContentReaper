/**
 * static/js/app.js
 * * Contains common JavaScript functions shared across multiple pages
 * to avoid code duplication.
 */

// --- FIX: Define variables in a shared scope to be initialized once ---
let confirmModalInstance = null;
let onConfirmAction = () => {};

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
    // Check if the modal was successfully initialized on page load.
    if (!confirmModalInstance) {
        console.error("Confirmation modal is not initialized.");
        return;
    }

    document.getElementById('confirmModalTitle').textContent = title;
    document.getElementById('confirmModalBody').textContent = body;
    
    // Store the specific action to be performed for this confirmation.
    onConfirmAction = onConfirm;

    // Show the pre-existing modal instance.
    confirmModalInstance.show();
};

/**
 * --- FIX: Initialize modal and add a single, persistent event listener when the DOM is loaded ---
 * This new pattern ensures the modal and its listeners are only created once per page load,
 * preventing the open/close loop bug.
 */
document.addEventListener('DOMContentLoaded', () => {
    const modalEl = document.getElementById('confirmModal');
    if (modalEl) {
        // Create the Bootstrap Modal instance once and store it in our global variable.
        confirmModalInstance = new bootstrap.Modal(modalEl);

        const confirmButton = document.getElementById('confirmModalButton');
        if (confirmButton) {
            // Add a single, permanent click listener to the confirm button.
            confirmButton.addEventListener('click', () => {
                // When the confirm button is clicked, execute the currently stored action...
                if (typeof onConfirmAction === 'function') {
                    onConfirmAction();
                }
                // ...and then hide the modal.
                confirmModalInstance.hide();
            });
        }
    }
});

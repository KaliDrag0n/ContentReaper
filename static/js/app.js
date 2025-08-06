/**
 * static/js/app.js
 * Contains common JavaScript functions shared across multiple pages
 * to avoid code duplication. This includes theme management and a reusable
 * confirmation modal.
 */

// --- Global variables for shared components ---
let confirmModalInstance = null; // Holds the Bootstrap Modal instance.
let onConfirmAction = () => {}; // A placeholder for the function to run on confirmation.

/**
 * Applies a color theme to the entire document.
 * @param {string} theme - The theme to apply ('light' or 'dark').
 */
const applyTheme = (theme) => {
    // Set the data attribute on the root <html> element.
    document.documentElement.dataset.bsTheme = theme;
    
    // Also, update the state of the theme toggle switch if it exists on the page.
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.checked = theme === 'dark';
    }
};

/**
 * Displays a Bootstrap confirmation modal with a dynamic title, body, and action.
 * @param {string} title - The title for the modal header.
 * @param {string} body - The text content for the modal body.
 * @param {function} onConfirm - The callback function to execute when the 'Confirm' button is clicked.
 */
const showConfirmModal = (title, body, onConfirm) => {
    // --- FIX: Check if the modal was successfully initialized on page load. ---
    // This prevents errors on pages that don't include the modal's HTML structure.
    if (!confirmModalInstance) {
        console.error("Confirmation modal is not initialized. Make sure the modal's HTML is included on this page.");
        return;
    }

    // Set the content for this specific confirmation dialog.
    document.getElementById('confirmModalTitle').textContent = title;
    document.getElementById('confirmModalBody').textContent = body;
    
    // Store the unique action to be performed for this instance of the confirmation.
    onConfirmAction = onConfirm;

    // Show the modal.
    confirmModalInstance.show();
};

/**
 * Initializes shared components when the DOM is fully loaded.
 * This pattern ensures that the modal and its listeners are only created once
 * per page load, preventing bugs and memory leaks.
 */
document.addEventListener('DOMContentLoaded', () => {
    // Find the modal element in the DOM.
    const modalEl = document.getElementById('confirmModal');
    
    // If the modal element exists on the current page, initialize it.
    if (modalEl) {
        // Create the Bootstrap Modal instance once and store it in our global variable.
        confirmModalInstance = new bootstrap.Modal(modalEl);

        const confirmButton = document.getElementById('confirmModalButton');
        if (confirmButton) {
            // Add a single, permanent click listener to the confirm button.
            confirmButton.addEventListener('click', () => {
                // When the button is clicked, execute the currently stored action...
                if (typeof onConfirmAction === 'function') {
                    onConfirmAction();
                }
                // ...and then hide the modal.
                confirmModalInstance.hide();
            });
        }
    }
});

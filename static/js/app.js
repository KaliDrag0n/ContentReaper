/**
 * static/js/app.js
 * Contains common JavaScript functions shared across multiple pages
 * to avoid code duplication. This includes theme management and a reusable
 * confirmation modal.
 */

// --- Global variables for shared components ---
let confirmModalInstance = null; // Holds the Bootstrap Modal instance.
let onConfirmAction = () => {}; // A placeholder for the function to run on confirmation.
let loginModalInstance = null;
let failedRequest = null; // To store a request that failed due to auth, so we can retry it.

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
 * Displays a Bootstrap confirmation modal with a dynamic title, body, and action.
 * @param {string} title - The title for the modal header.
 * @param {string} body - The text content for the modal body.
 * @param {function} onConfirm - The callback function to execute when the 'Confirm' button is clicked.
 */
const showConfirmModal = (title, body, onConfirm) => {
    if (!confirmModalInstance) {
        console.error("Confirmation modal is not initialized. Make sure the modal's HTML is included on this page.");
        return;
    }
    document.getElementById('confirmModalTitle').textContent = title;
    document.getElementById('confirmModalBody').textContent = body;
    onConfirmAction = onConfirm;
    confirmModalInstance.show();
};

/**
 * --- NEW: Shows the global login modal. ---
 */
const showLoginModal = () => {
    if (!loginModalInstance) {
        const modalEl = document.getElementById('loginModal');
        if (!modalEl) return;
        loginModalInstance = new bootstrap.Modal(modalEl);
    }
    const errorEl = document.getElementById('login-error');
    if(errorEl) errorEl.textContent = '';
    loginModalInstance.show();
};

/**
 * A wrapper for the fetch API to handle common error cases, JSON parsing,
 * and automatically triggering the authentication flow.
 * @param {string} endpoint - The API endpoint to request.
 * @param {object} [options={}] - Standard fetch options.
 * @returns {Promise<any>} - A promise that resolves with the response data.
 */
async function apiRequest(endpoint, options = {}) {
    const fetchOptions = {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {}),
        },
    };
    if (options.body instanceof FormData) {
        delete fetchOptions.headers['Content-Type'];
    }

    try {
        const res = await fetch(endpoint, fetchOptions);
        if (res.status === 401) {
            failedRequest = { endpoint, options };
            showLoginModal();
            throw new Error("AUTH_REQUIRED");
        }
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({ message: `Request failed with status: ${res.status}` }));
            throw new Error(errorData.message || 'An unknown error occurred.');
        }
        if (res.status === 204) return null;
        if (res.headers.get("Content-Type")?.includes("application/json")) {
            return res.json();
        }
        return res.text();
    } catch (error) {
        if (error.message !== "AUTH_REQUIRED") {
            const toastEl = document.getElementById('actionToast');
            if(toastEl) {
                 const toastTitle = document.getElementById('toastTitle');
                 const toastBody = document.getElementById('toastBody');
                 toastTitle.textContent = 'API Error';
                 toastBody.textContent = error.message;
                 toastEl.className = 'toast text-white bg-danger';
                 new bootstrap.Toast(toastEl).show();
            }
        }
        throw error;
    }
}

/**
 * Initializes shared components when the DOM is fully loaded.
 */
document.addEventListener('DOMContentLoaded', () => {
    // --- FIX: Expose all necessary functions to the global window object ---
    window.applyTheme = applyTheme;
    window.showConfirmModal = showConfirmModal;
    window.showLoginModal = showLoginModal;
    window.apiRequest = apiRequest;

    // --- Confirmation Modal Logic ---
    const confirmModalEl = document.getElementById('confirmModal');
    if (confirmModalEl) {
        confirmModalInstance = new bootstrap.Modal(confirmModalEl);
        document.getElementById('confirmModalButton').addEventListener('click', () => {
            if (typeof onConfirmAction === 'function') onConfirmAction();
            confirmModalInstance.hide();
        });
    }

    // --- Login Modal Logic ---
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const passwordInput = document.getElementById('login-password-input');
            const errorEl = document.getElementById('login-error');
            const password = passwordInput.value;
            errorEl.textContent = '';

            try {
                await apiRequest('/api/auth/login', {
                    method: 'POST',
                    body: JSON.stringify({ password: password })
                });
                loginModalInstance.hide();
                passwordInput.value = '';
                if (failedRequest) {
                    const { endpoint, options } = failedRequest;
                    failedRequest = null;
                    await apiRequest(endpoint, options);
                }
                location.reload();
            } catch (err) {
                if (err.message !== "AUTH_REQUIRED") {
                    errorEl.textContent = err.message || "An unknown error occurred.";
                }
            }
        });
    }

    // --- Logout Button Logic ---
    const logoutBtn = document.getElementById('logout-btn');
    if(logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            await apiRequest('/api/auth/logout', { method: 'POST' });
            location.reload();
        });
    }
});

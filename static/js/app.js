/**
 * static/js/app.js
 * Contains common JavaScript functions shared across multiple pages.
 * This includes theme management, reusable modals, and a centralized, secure
 * API request handler with CSRF protection.
 */

(function() {
    'use strict';

    // --- Global variables for shared components ---
    let confirmModalInstance = null;
    let onConfirmAction = () => {};
    let loginModalInstance = null;
    
    let csrfToken = null;
    let requestToRetry = null; 

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
     * @param {function} onConfirm - The callback to execute on confirmation.
     */
    const showConfirmModal = (title, body, onConfirm) => {
        if (!confirmModalInstance) {
            console.error("Confirmation modal is not initialized.");
            return;
        }
        document.getElementById('confirmModalTitle').textContent = title;
        document.getElementById('confirmModalBody').textContent = body;
        onConfirmAction = onConfirm;
        confirmModalInstance.show();
    };

    /**
     * Shows the global login modal.
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
     * Fetches the CSRF token from the backend.
     */
    const fetchCsrfToken = async () => {
        try {
            const data = await (await fetch('/api/auth/csrf-token')).json();
            csrfToken = data.csrf_token;
        } catch (error) {
            console.error('Could not fetch CSRF token. State-changing actions may fail.', error);
        }
    };

    /**
     * A secure wrapper for the fetch API.
     */
    async function apiRequest(endpoint, options = {}) {
        const fetchOptions = {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...(options.headers || {}),
            },
        };

        const method = (options.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD' && csrfToken) {
            fetchOptions.headers['X-CSRF-Token'] = csrfToken;
        }

        if (options.body instanceof FormData || options.body instanceof URLSearchParams) {
            delete fetchOptions.headers['Content-Type'];
        }

        try {
            const res = await fetch(endpoint, fetchOptions);

            if (res.status === 401) {
                requestToRetry = { endpoint, options };
                showLoginModal();
                throw new Error("AUTH_REQUIRED");
            }

            if (!res.ok) {
                const errorData = await res.json().catch(() => ({ 
                    message: `Request failed with status: ${res.status}` 
                }));
                throw new Error(errorData.message || 'An unknown API error occurred.');
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
                     toastBody.textContent = `Operation failed: ${error.message}`;
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
        window.applyTheme = applyTheme;
        window.showConfirmModal = showConfirmModal;
        window.showLoginModal = showLoginModal;
        window.apiRequest = apiRequest;

        fetchCsrfToken();

        const confirmModalEl = document.getElementById('confirmModal');
        if (confirmModalEl) {
            confirmModalInstance = new bootstrap.Modal(confirmModalEl);
            document.getElementById('confirmModalButton').addEventListener('click', () => {
                if (typeof onConfirmAction === 'function') onConfirmAction();
                confirmModalInstance.hide();
            });
        }

        const loginForm = document.getElementById('login-form');
        if (loginForm) {
            loginForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const passwordInput = document.getElementById('login-password-input');
                const errorEl = document.getElementById('login-error');
                const password = passwordInput.value;
                errorEl.textContent = '';

                try {
                    await apiRequest(window.API.authLogin, {
                        method: 'POST',
                        body: JSON.stringify({ password: password })
                    });
                    
                    loginModalInstance.hide();
                    passwordInput.value = '';

                    if (requestToRetry) {
                        const { endpoint, options } = requestToRetry;
                        requestToRetry = null;
                        await apiRequest(endpoint, options);
                    }
                } catch (err) {
                    if (err.message !== "AUTH_REQUIRED") {
                        errorEl.textContent = err.message || "An unknown error occurred.";
                    }
                } finally {
                    location.reload();
                }
            });
        }

        const logoutBtn = document.getElementById('logout-btn');
        if(logoutBtn) {
            logoutBtn.addEventListener('click', async () => {
                await apiRequest(window.API.authLogout, { method: 'POST' });
                location.reload();
            });
        }
    });

})();

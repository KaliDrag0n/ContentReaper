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
    let toastInstance = null;
    
    let csrfToken = null;
    let requestToRetry = null; 

    // --- UTILITY FUNCTIONS ---

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
     * Displays a global Bootstrap toast notification.
     * @param {string} message - The main content of the toast.
     * @param {string} [title='Notification'] - The title of the toast.
     * @param {'success'|'danger'|'info'} [type='info'] - The toast style.
     */
    const showToast = (message, title = 'Notification', type = 'info') => {
        const toastEl = document.getElementById('actionToast');
        if (!toastEl) return;
        document.getElementById('toastTitle').textContent = title;
        document.getElementById('toastBody').textContent = message;
        toastEl.className = 'toast text-white';
        toastEl.classList.add(type === 'danger' ? 'bg-danger' : (type === 'success' ? 'bg-success' : 'bg-primary'));
        if (!toastInstance) toastInstance = new bootstrap.Toast(toastEl);
        toastInstance.show();
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
        
        document.dispatchEvent(new CustomEvent('login-modal-shown'));
        
        loginModalInstance.show();
    };

    /**
     * Fetches the CSRF token from the backend.
     */
    const fetchCsrfToken = async () => {
        try {
            const res = await fetch(window.API.csrfToken);
            if (!res.ok) throw new Error('CSRF fetch failed');
            const data = await res.json();
            csrfToken = data.csrf_token;
        } catch (error) {
            console.error('Could not fetch CSRF token. State-changing actions may fail.', error);
        }
    };

    /**
     * A secure wrapper for the fetch API that handles CSRF, authentication, and errors.
     * @param {string} endpoint - The API endpoint to call.
     * @param {object} [options={}] - Standard fetch options.
     * @returns {Promise<any>} - A promise that resolves with the JSON response.
     */
    async function apiRequest(endpoint, options = {}) {
        const fetchOptions = {
            ...options,
            headers: {
                'Accept': 'application/json',
                ...(options.headers || {}),
            },
        };

        const method = (options.method || 'GET').toUpperCase();
        if (options.body && !(options.body instanceof FormData)) {
            fetchOptions.headers['Content-Type'] = 'application/json';
        }

        if (method !== 'GET' && method !== 'HEAD') {
            if (!csrfToken) await fetchCsrfToken();
            if (csrfToken) fetchOptions.headers['X-CSRF-Token'] = csrfToken;
        }

        try {
            const res = await fetch(endpoint, fetchOptions);

            if (res.status === 401) { // Unauthorized
                requestToRetry = { endpoint, options };
                showLoginModal();
                throw new Error("AUTH_REQUIRED");
            }

            if (!res.ok) {
                const errorData = await res.json().catch(() => ({ 
                    error: `Request failed with status: ${res.status}` 
                }));
                throw new Error(errorData.error || 'An unknown API error occurred.');
            }

            if (res.status === 204) return null;
            
            const contentType = res.headers.get("Content-Type");
            if (contentType?.includes("application/json")) {
                return res.json();
            }
            return res.text();

        } catch (error) {
            if (error.message !== "AUTH_REQUIRED") {
                showToast(error.message, 'API Error', 'danger');
            }
            throw error;
        }
    }

    /**
     * Updates the main login/logout buttons based on authentication status.
     * @param {boolean} isLoggedIn - Whether the user is currently logged in.
     */
    const updateAuthUI = (isLoggedIn) => {
        const loginBtn = document.getElementById('login-btn');
        const logoutBtn = document.getElementById('logout-btn');
        if (loginBtn) loginBtn.style.display = isLoggedIn ? 'none' : 'inline-block';
        if (logoutBtn) logoutBtn.style.display = isLoggedIn ? 'inline-block' : 'none';
    };

    /**
     * Initializes shared components when the DOM is fully loaded.
     */
    const initializeSharedComponents = () => {
        window.applyTheme = applyTheme;
        window.showConfirmModal = showConfirmModal;
        window.showLoginModal = showLoginModal;
        window.apiRequest = apiRequest;
        window.showToast = showToast;

        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => {
                const newTheme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
                applyTheme(newTheme);
                localStorage.setItem('downloader_theme', newTheme);
            });
        }

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
                        body: JSON.stringify({ password })
                    });
                    
                    if (loginModalInstance) loginModalInstance.hide();
                    passwordInput.value = '';
                    updateAuthUI(true);

                    document.dispatchEvent(new CustomEvent('auth-changed', { detail: { loggedIn: true } }));

                    if (requestToRetry) {
                        const { endpoint, options } = requestToRetry;
                        requestToRetry = null;
                        
                        // --- CHANGE: Show success toast after retried request completes ---
                        apiRequest(endpoint, options)
                            .then(data => {
                                // If the server sends back a message, show it as a success toast.
                                if (data && data.message) {
                                    window.showToast(data.message, 'Success', 'success');
                                }
                            })
                            .catch(() => {}); // Errors are already handled by apiRequest
                    }
                } catch (err) {
                    if (err.message !== "AUTH_REQUIRED") {
                        errorEl.textContent = err.message || "An unknown error occurred.";
                    }
                }
            });
        }

        const logoutBtn = document.getElementById('logout-btn');
        if(logoutBtn) {
            logoutBtn.addEventListener('click', async () => {
                try {
                    await apiRequest(window.API.authLogout, { method: 'POST' });
                    updateAuthUI(false);
                    document.dispatchEvent(new CustomEvent('auth-changed', { detail: { loggedIn: false } }));
                } catch(err) { /* apiRequest handles showing the error toast */ }
            });
        }

        apiRequest(window.API.authStatus).then(status => {
            const loginBtn = document.getElementById('login-btn');
            if (status.password_set && !status.logged_in && loginBtn) {
                 updateAuthUI(false);
            } else {
                 updateAuthUI(status.logged_in);
            }
        }).catch(() => {});
    };

    document.addEventListener('DOMContentLoaded', () => {
        const checkGlobals = setInterval(() => {
            if (window.API) {
                clearInterval(checkGlobals);
                const savedTheme = localStorage.getItem('downloader_theme') || 'light';
                applyTheme(savedTheme);
                initializeSharedComponents();
            }
        }, 50);
    });

})();

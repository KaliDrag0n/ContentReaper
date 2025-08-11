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

    // CHANGE: Add socket variable
    let socket = null;

    // --- UTILITY FUNCTIONS ---

    const applyTheme = (theme) => {
        document.documentElement.dataset.bsTheme = theme;
        const toggle = document.getElementById('theme-toggle');
        if (toggle) {
            toggle.checked = theme === 'dark';
        }
    };

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
            if (!csrfToken) {
                await fetchCsrfToken();
            }
            if (csrfToken) {
                fetchOptions.headers['X-CSRF-Token'] = csrfToken;
            }
        }

        try {
            const res = await fetch(endpoint, fetchOptions);

            if (res.status === 401 || res.status === 403) {
                // CHANGE: Show a toast notification to provide context for the login modal.
                showToast('Your session may have expired. Please log in again.', 'Authentication Required', 'info');
                requestToRetry = { endpoint, options };
                showLoginModal();
                const errorData = await res.json().catch(() => ({ error: "Authentication required." }));
                throw new Error(errorData.error || "AUTH_REQUIRED");
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
            if (error.message !== "AUTH_REQUIRED" && !error.message.includes("Permission denied")) {
                showToast(error.message, 'API Error', 'danger');
            }
            throw error;
        }
    }
    
    const updateUIPermissions = (authStatus) => {
        const allPerms = ['admin', 'can_add_to_queue', 'can_manage_scythes', 'can_download_files', 'can_delete_files'];
        
        let userPerms = [];
        if (authStatus.logged_in) {
            if (authStatus.role === 'admin') {
                userPerms = allPerms;
            } else if (authStatus.permissions) {
                userPerms = Object.keys(authStatus.permissions).filter(p => authStatus.permissions[p]);
            }
        }

        allPerms.forEach(perm => {
            const elements = document.querySelectorAll(`.perm-${perm}`);
            const hasPerm = userPerms.includes(perm);
            elements.forEach(el => {
                let displayStyle = ''; 
                if (el.tagName === 'BUTTON' || el.tagName === 'A') {
                    displayStyle = 'inline-block';
                } else if (el.tagName === 'LI') {
                    displayStyle = 'block'; 
                }
                el.style.display = hasPerm ? displayStyle : 'none';
            });
        });
    };

    const updateAuthUI = (authStatus) => {
        const loginBtn = document.getElementById('login-btn');
        const logoutBtn = document.getElementById('logout-btn');
        
        if (authStatus.manually_logged_in) {
            if (loginBtn) loginBtn.style.display = 'none';
            if (logoutBtn) logoutBtn.style.display = 'inline-block';
        } else {
            if (loginBtn) loginBtn.style.display = 'inline-block';
            if (logoutBtn) logoutBtn.style.display = 'none';
        }
    };

    // CHANGE: New function to initialize WebSocket connection
    const initializeWebSocket = () => {
        if (socket && socket.connected) {
            return;
        }
        socket = io({
            reconnection: true,
            reconnectionAttempts: 5,
            reconnectionDelay: 1000,
        });

        socket.on('connect', () => {
            console.log('WebSocket connected successfully.');
        });

        socket.on('state_update', (data) => {
            // Dispatch a custom event that page-specific scripts (like index.js) can listen for.
            document.dispatchEvent(new CustomEvent('state-update', { detail: data }));
        });

        socket.on('disconnect', () => {
            console.warn('WebSocket disconnected. Attempting to reconnect...');
        });

        socket.on('connect_error', (error) => {
            console.error('WebSocket connection error:', error);
        });
    };

    const initializeSharedComponents = () => {
        window.applyTheme = applyTheme;
        window.showConfirmModal = showConfirmModal;
        window.showLoginModal = showLoginModal;
        window.apiRequest = apiRequest;
        window.showToast = showToast;

        csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

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
                const usernameInput = document.getElementById('login-username-input');
                const passwordInput = document.getElementById('login-password-input');
                const errorEl = document.getElementById('login-error');
                const username = usernameInput.value;
                const password = passwordInput.value;
                errorEl.textContent = '';

                try {
                    await apiRequest(window.API.authLogin, {
                        method: 'POST',
                        body: JSON.stringify({ username, password })
                    });
                    
                    if (loginModalInstance) loginModalInstance.hide();
                    passwordInput.value = '';
                    
                    const newAuthStatus = await apiRequest(window.API.authStatus);
                    updateAuthUI(newAuthStatus);
                    updateUIPermissions(newAuthStatus);
                    document.dispatchEvent(new CustomEvent('auth-changed', { detail: newAuthStatus }));

                    if (requestToRetry) {
                        const { endpoint, options } = requestToRetry;
                        requestToRetry = null;
                        
                        apiRequest(endpoint, options)
                            .then(data => {
                                if (data && data.message) {
                                    window.showToast(data.message, 'Success', 'success');
                                }
                            })
                            .catch(() => {});
                    }
                } catch (err) {
                    if (err.message !== "AUTH_REQUIRED") {
                        errorEl.textContent = err.message || "An unknown error occurred.";
                    }
                }
            });
        }

        const loginBtn = document.getElementById('login-btn');
        if (loginBtn) {
            loginBtn.addEventListener('click', showLoginModal);
        }

        const logoutBtn = document.getElementById('logout-btn');
        if(logoutBtn) {
            logoutBtn.addEventListener('click', async () => {
                try {
                    await apiRequest(window.API.authLogout, { method: 'POST' });
                    const newAuthStatus = await apiRequest(window.API.authStatus);
                    updateAuthUI(newAuthStatus);
                    updateUIPermissions(newAuthStatus);
                    document.dispatchEvent(new CustomEvent('auth-changed', { detail: newAuthStatus }));
                } catch(err) { /* apiRequest handles showing the error toast */ }
            });
        }

        apiRequest(window.API.authStatus).then(status => {
            updateAuthUI(status);
            updateUIPermissions(status);
        }).catch(() => {});

        // CHANGE: Initialize WebSocket connection
        initializeWebSocket();

        setTimeout(() => {
            document.body.classList.add('loaded');
        }, 50);
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
/**
 * static/js/settings.js
 * Handles all logic for the settings page.
 * Refactored to fetch its own data via API for better decoupling.
 */

(function() {
    'use strict';

    // --- CHANGE: Centralized function to populate the form fields ---
    const populateSettings = async () => {
        try {
            // This new endpoint will need to be created in the backend.
            const data = await window.apiRequest('/api/settings');
            
            document.getElementById('download_dir').value = data.config.download_dir;
            document.getElementById('temp_dir').value = data.config.temp_dir;
            
            // The cookie content is now also fetched via the secure API.
            const cookieTextarea = document.getElementById('cookie_content');
            cookieTextarea.value = data.cookies;
            if (!data.is_logged_in && data.is_password_set) {
                cookieTextarea.placeholder = "Login to view/edit cookies...";
                cookieTextarea.disabled = true;
            } else {
                cookieTextarea.disabled = false;
            }

        } catch (error) {
            if (error.message !== "AUTH_REQUIRED") {
                console.error("Failed to load settings:", error);
                document.getElementById('settings-form-body').innerHTML = 
                    `<div class="alert alert-danger">Could not load settings from the server. Please try refreshing the page.</div>`;
            }
        }
    };

    const initializeSettingsPage = () => {
        // Common page initialization
        const savedTheme = localStorage.getItem('downloader_theme') || 'light';
        window.applyTheme(savedTheme);
        document.getElementById('theme-toggle').addEventListener('click', () => {
            const newTheme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
            window.applyTheme(newTheme);
            localStorage.setItem('downloader_theme', newTheme);
        });

        const setupAlert = document.getElementById('setup-alert');
        const currentPasswordGroup = document.getElementById('current-password-group');
        const loginBtn = document.getElementById('login-btn');
        const logoutBtn = document.getElementById('logout-btn');

        // Check auth status to show/hide relevant buttons and alerts
        window.apiRequest(window.API.authStatus).then(status => {
            if (!status.password_set) {
                setupAlert.style.display = 'block';
                currentPasswordGroup.style.display = 'none';
            } else {
                setupAlert.style.display = 'none';
                currentPasswordGroup.style.display = 'block';
                if (status.logged_in) {
                    logoutBtn.style.display = 'inline-block';
                } else {
                    loginBtn.style.display = 'inline-block';
                }
            }
            // Populate the form fields after checking auth status
            populateSettings();
        });

        loginBtn.addEventListener('click', () => window.showLoginModal());

        // Password form submission
        const passwordForm = document.getElementById('password-form');
        if (passwordForm) {
            passwordForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const currentPassword = document.getElementById('current-password').value;
                const newPassword = document.getElementById('new-password').value;
                const confirmPassword = document.getElementById('confirm-password').value;
                const statusEl = document.getElementById('password-status');
                const submitBtn = document.getElementById('password-submit-btn');

                statusEl.textContent = '';
                statusEl.className = 'form-text mb-2';

                if (newPassword !== confirmPassword) {
                    statusEl.textContent = 'New passwords do not match.';
                    statusEl.classList.add('text-danger');
                    return;
                }

                submitBtn.disabled = true;
                submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Saving...`;

                try {
                    const response = await window.apiRequest(window.API.authSetPassword, {
                        method: 'POST',
                        body: JSON.stringify({
                            current_password: currentPassword,
                            new_password: newPassword
                        })
                    });
                    statusEl.textContent = response.message;
                    statusEl.classList.add('text-success');
                    // Reload the page to reflect the new login state
                    setTimeout(() => location.reload(), 1500);
                } catch (error) {
                    if (error.message !== "AUTH_REQUIRED") {
                        statusEl.textContent = `Error: ${error.message}`;
                        statusEl.classList.add('text-danger');
                    }
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = 'Set/Update Password';
                }
            });
        }

        // Server action buttons
        const updateBtn = document.getElementById('check-for-updates-btn');
        if(updateBtn) {
            updateBtn.addEventListener('click', async () => {
                updateBtn.disabled = true;
                updateBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Checking...`;
                try {
                    await window.apiRequest(window.API.forceUpdateCheck, { method: 'POST' });
                    // Reload the page to show the new update status
                    location.reload();
                } catch (error) {
                    if (error.message !== "AUTH_REQUIRED") {
                        window.showToast(`Could not check for updates. Error: ${error.message}`, 'Error', 'danger');
                    }
                    updateBtn.disabled = false;
                    updateBtn.innerHTML = `<i class="bi bi-arrow-repeat"></i> Check for Updates`;
                }
            });
        }

        const installUpdateBtn = document.getElementById('install-update-btn');
        if (installUpdateBtn) {
            installUpdateBtn.addEventListener('click', () => {
                window.showConfirmModal('Install Update?', 'This will stop the server, download the latest version, and restart the application. All ongoing downloads will be cancelled. Are you sure?', async () => {
                    installUpdateBtn.disabled = true;
                    installUpdateBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Starting...`;
                    try {
                        await window.apiRequest(window.API.installUpdate, { method: 'POST' });
                        document.querySelector('.container').innerHTML = `<div class="alert alert-info mt-4"><h4>Update in Progress</h4><p>The server will restart automatically. This page will become unresponsive. Please wait a minute and then refresh.</p></div>`;
                    } catch (error) {
                         if(error.message !== "AUTH_REQUIRED") {
                             window.showToast(`Failed to start update. Error: ${error.message}`, 'Error', 'danger');
                         }
                         installUpdateBtn.disabled = false;
                         installUpdateBtn.innerHTML = `<i class="bi bi-cloud-download-fill"></i> Install Update & Restart`;
                    }
                });
            });
        }

        const shutdownBtn = document.getElementById('shutdown-btn');
        if (shutdownBtn) {
            shutdownBtn.addEventListener('click', () => {
                window.showConfirmModal('Shutdown Server?', 'Are you sure you want to shut down the server? This will stop all downloads.', async () => {
                    shutdownBtn.disabled = true;
                    shutdownBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Shutting down...`;
                    try {
                        await window.apiRequest(window.API.shutdown, { method: 'POST' });
                        document.querySelector('.container').innerHTML = `<div class="alert alert-info mt-4">The shutdown command has been sent. You can now close this page.</div>`;
                    } catch(error) {
                        if(error.message !== "AUTH_REQUIRED") {
                            window.showToast(`Failed to send shutdown command. Error: ${error.message}`, 'Error', 'danger');
                        }
                        shutdownBtn.disabled = false;
                        shutdownBtn.innerHTML = `<i class="bi bi-power"></i> Shutdown Server`;
                    }
                });
            });
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        // Wait for shared functions from app.js and api.js to be ready
        const checkGlobals = setInterval(() => {
            if (window.applyTheme && window.apiRequest && window.API) {
                clearInterval(checkGlobals);
                initializeSettingsPage();
            }
        }, 50);
    });
})();

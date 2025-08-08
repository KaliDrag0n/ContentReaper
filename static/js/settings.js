/**
 * static/js/settings.js
 * Handles all logic for the settings page.
 * Fetches its own data via API for better decoupling and provides a smoother UX.
 */

(function() {
    'use strict';

    /**
     * Populates the settings form with data fetched from the API.
     */
    const populateSettings = async () => {
        try {
            const data = await window.apiRequest(window.API.settings);
            
            document.getElementById('download_dir').value = data.config.download_dir;
            document.getElementById('temp_dir').value = data.config.temp_dir;
            document.getElementById('log_level').value = data.config.log_level || 'INFO';
            
            const cookieTextarea = document.getElementById('cookie_content');
            // This check is now redundant because of the logic in initializeSettingsPage,
            // but it's good defensive programming.
            if (!cookieTextarea.disabled) {
                cookieTextarea.value = data.cookies;
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
        const setupAlert = document.getElementById('setup-alert');
        const currentPasswordGroup = document.getElementById('current-password-group');
        const cookieTextarea = document.getElementById('cookie_content');
        const settingsForm = document.getElementById('general-settings-form');

        // Check auth status to show/hide relevant elements
        window.apiRequest(window.API.authStatus).then(status => {
            if (!status.password_set) {
                setupAlert.style.display = 'block';
                currentPasswordGroup.style.display = 'none';
            } else {
                setupAlert.style.display = 'none';
                currentPasswordGroup.style.display = 'block';
                if (!status.logged_in) {
                    cookieTextarea.placeholder = "Login to view/edit cookies...";
                    cookieTextarea.disabled = true;
                } else {
                    // ** THE FIX IS HERE **
                    // Explicitly enable the textarea and set placeholder if logged in.
                    cookieTextarea.disabled = false;
                    cookieTextarea.placeholder = "Paste your Netscape format cookies here...";
                }
            }
            // Populate the form fields after checking auth status and setting element states
            populateSettings();
        });

        // ** UX IMPROVEMENT **
        // Handle the settings form submission with JavaScript for a no-reload experience.
        if (settingsForm) {
            settingsForm.addEventListener('submit', async (e) => {
                e.preventDefault(); // Prevent default form submission
                const submitBtn = settingsForm.querySelector('button[type="submit"]');
                const originalBtnText = submitBtn.textContent;
                submitBtn.disabled = true;
                submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Saving...`;

                try {
                    const formData = new FormData(settingsForm);
                    const settingsData = Object.fromEntries(formData.entries());

                    const response = await window.apiRequest(window.API.settings, {
                        method: 'POST',
                        body: JSON.stringify(settingsData)
                    });

                    window.showToast(response.message, 'Success', 'success');

                } catch (error) {
                    // Error toast is already shown by apiRequest
                    console.error("Failed to save settings:", error);
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalBtnText;
                }
            });
        }

        // Password form submission (remains unchanged)
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

        // Server action buttons (remain unchanged)
        const updateBtn = document.getElementById('check-for-updates-btn');
        if(updateBtn) {
            updateBtn.addEventListener('click', async () => {
                updateBtn.disabled = true;
                updateBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Checking...`;
                try {
                    await window.apiRequest(window.API.forceUpdateCheck, { method: 'POST' });
                    location.reload();
                } catch (error) {
                    if (error.message !== "AUTH_REQUIRED") {
                        window.showToast(`Could not check for updates. Error: ${error.message}`, 'Error', 'danger');
                    }
                } finally {
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

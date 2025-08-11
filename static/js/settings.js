/**
 * static/js/settings.js
 * Handles all logic for the settings page, including the new user management panel.
 */

(function() {
    'use strict';
    let userEditorModalInstance = null;
    let allUsers = {};

    /**
     * Populates all settings forms with data fetched from the API.
     */
    const populateSettings = async () => {
        try {
            const data = await window.apiRequest(window.API.settings);
            const authStatus = await window.apiRequest(window.API.authStatus);
            
            // General Settings
            document.getElementById('download_dir').value = data.config.download_dir;
            document.getElementById('temp_dir').value = data.config.temp_dir;
            document.getElementById('log_level').value = data.config.log_level || 'INFO';
            document.getElementById('server_host').value = data.config.server_host;
            document.getElementById('server_port').value = data.config.server_port;
            document.getElementById('user_timezone').value = data.config.user_timezone || 'UTC';
            
            const cookieTextarea = document.getElementById('cookie_content');
            if (!cookieTextarea.disabled) {
                cookieTextarea.value = data.cookies;
            }

            // User Management
            allUsers = data.users || {};
            renderUserTable(allUsers);
            populatePublicUserDropdown(allUsers, data.config.public_user);

            // CHANGE: Show the setup alert if the admin password is not set and it hasn't been ignored.
            const setupAlert = document.getElementById('setup-alert');
            if (setupAlert && !authStatus.admin_password_set && localStorage.getItem('hide_setup_alert') !== 'true') {
                setupAlert.style.display = 'block';
            } else if (setupAlert) {
                setupAlert.style.display = 'none';
            }

        } catch (error) {
            if (error.message !== "AUTH_REQUIRED") {
                console.error("Failed to load settings:", error);
                document.getElementById('settings-form-body').innerHTML = 
                    `<div class="alert alert-danger">Could not load settings from the server. Please try refreshing the page.</div>`;
            }
        }
    };

    const renderUserTable = (users) => {
        const tableBody = document.getElementById('user-list-table');
        tableBody.innerHTML = ''; // Clear existing rows

        Object.keys(users).sort().forEach(username => {
            const user = users[username];
            const tr = document.createElement('tr');
            const role = username === 'admin' ? 'Admin' : 'User';
            
            const actions = username === 'admin' 
                ? `<button class="btn btn-sm btn-secondary user-action-btn" data-action="edit" data-username="${username}"><i class="bi bi-pencil-fill"></i> Edit</button>`
                : `<div class="btn-group">
                       <button class="btn btn-sm btn-secondary user-action-btn" data-action="edit" data-username="${username}"><i class="bi bi-pencil-fill"></i> Edit</button>
                       <button class="btn btn-sm btn-danger user-action-btn" data-action="delete" data-username="${username}"><i class="bi bi-trash-fill"></i> Delete</button>
                   </div>`;

            tr.innerHTML = `
                <td>${username}</td>
                <td><span class="badge bg-${role === 'Admin' ? 'primary' : 'secondary'}">${role}</span></td>
                <td class="text-end">${actions}</td>
            `;
            tableBody.appendChild(tr);
        });
    };

    const populatePublicUserDropdown = (users, selectedUser) => {
        const selectEl = document.getElementById('public_user');
        selectEl.innerHTML = '<option value="None">None (Login Required)</option>';
        Object.keys(users).forEach(username => {
            if (username !== 'admin') {
                const option = document.createElement('option');
                option.value = username;
                option.textContent = username;
                if (username === selectedUser) {
                    option.selected = true;
                }
                selectEl.appendChild(option);
            }
        });
    };

    const openUserEditor = (username = null) => {
        const modalEl = document.getElementById('userEditorModal');
        if (!userEditorModalInstance) {
            userEditorModalInstance = new bootstrap.Modal(modalEl);
        }
        const form = document.getElementById('user-editor-form');
        form.reset();

        const titleEl = document.getElementById('userEditorTitle');
        const usernameInput = document.getElementById('user-editor-username');
        const originalUsernameInput = document.getElementById('user-editor-original-username');
        const passwordHelpText = document.getElementById('password-help-text');

        if (username) { // Editing
            const user = allUsers[username];
            titleEl.textContent = `Edit User: ${username}`;
            usernameInput.value = username;
            usernameInput.disabled = true;
            originalUsernameInput.value = username;
            passwordHelpText.textContent = "Leave blank to keep the current password.";

            // Populate permissions
            const permissions = user.permissions || {};
            Object.keys(permissions).forEach(perm => {
                const checkbox = form.querySelector(`[name="${perm}"]`);
                if (checkbox) checkbox.checked = permissions[perm];
            });

        } else { // Adding
            titleEl.textContent = 'Add New User';
            usernameInput.disabled = false;
            originalUsernameInput.value = '';
            passwordHelpText.textContent = "A password is required for new users.";
        }

        userEditorModalInstance.show();
    };

    const initializeSettingsPage = () => {
        populateSettings();

        // General settings form
        const settingsForm = document.getElementById('general-settings-form');
        if (settingsForm) {
            settingsForm.addEventListener('submit', async (e) => {
                e.preventDefault();
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
                    console.error("Failed to save settings:", error);
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalBtnText;
                }
            });
        }

        // CHANGE: Add event listener for the setup alert's close button.
        const setupAlert = document.getElementById('setup-alert');
        if (setupAlert) {
            setupAlert.addEventListener('close.bs.alert', () => {
                localStorage.setItem('hide_setup_alert', 'true');
            });
        }

        // User management event delegation
        document.getElementById('add-user-btn').addEventListener('click', () => openUserEditor());
        
        document.getElementById('user-list-table').addEventListener('click', (e) => {
            const target = e.target.closest('.user-action-btn');
            if (!target) return;

            const username = target.dataset.username;
            const action = target.dataset.action;

            if (action === 'edit') {
                openUserEditor(username);
            } else if (action === 'delete') {
                window.showConfirmModal(`Delete User: ${username}?`, 'Are you sure you want to permanently delete this user? This cannot be undone.', async () => {
                    try {
                        const response = await window.apiRequest(`/api/users/${username}`, { method: 'DELETE' });
                        window.showToast(response.message, 'Success', 'success');
                        populateSettings(); // Refresh the list
                    } catch (error) {
                        console.error("Failed to delete user:", error);
                    }
                });
            }
        });

        // User editor form submission
        const userEditorForm = document.getElementById('user-editor-form');
        userEditorForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submitBtn = userEditorForm.querySelector('button[type="submit"]');
            submitBtn.disabled = true;
            submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Saving...`;

            const originalUsername = document.getElementById('user-editor-original-username').value;
            const username = document.getElementById('user-editor-username').value;
            const password = document.getElementById('user-editor-password').value;

            const formData = new FormData(userEditorForm);
            const permissions = {};
            ['can_add_to_queue', 'can_manage_scythes', 'can_download_files', 'can_delete_files'].forEach(perm => {
                permissions[perm] = formData.has(perm);
            });

            const isEditing = !!originalUsername;
            const payload = {
                username: username,
                password: password,
                permissions: permissions
            };

            const endpoint = isEditing ? `/api/users/${originalUsername}` : '/api/users';
            const method = isEditing ? 'PUT' : 'POST';

            try {
                const response = await window.apiRequest(endpoint, {
                    method: method,
                    body: JSON.stringify(payload)
                });
                window.showToast(response.message, 'Success', 'success');
                userEditorModalInstance.hide();
                // Refresh page if we just set the admin password for the first time
                if (username === 'admin' && password) {
                    // Also hide the alert permanently as it's been addressed.
                    localStorage.setItem('hide_setup_alert', 'true');
                    window.location.reload();
                } else {
                    populateSettings(); // Refresh the list
                }
            } catch (error) {
                console.error("Failed to save user:", error);
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = 'Save User';
            }
        });

        const updateBtn = document.getElementById('check-for-updates-btn');
        if(updateBtn) {
            updateBtn.addEventListener('click', async () => {
                updateBtn.disabled = true;
                updateBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Checking...`;
                try {
                    await window.apiRequest(window.API.forceUpdateCheck, { method: 'POST' });
                    window.showToast('Forced update check complete. The page will now reload.', 'Update Check', 'info');
                    setTimeout(() => location.reload(), 2000);
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
        const checkGlobals = setInterval(() => {
            if (window.applyTheme && window.apiRequest && window.API) {
                clearInterval(checkGlobals);
                initializeSettingsPage();
            }
        }, 50);
    });
})();
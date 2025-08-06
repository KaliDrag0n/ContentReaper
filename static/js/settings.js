// static/js/settings.js

document.addEventListener('DOMContentLoaded', () => {
    const checkGlobals = setInterval(() => {
        if (window.applyTheme && window.apiRequest && window.showLoginModal) {
            clearInterval(checkGlobals);
            initializeSettingsPage();
        }
    }, 50);
});

function initializeSettingsPage() {
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
    const cookieTextarea = document.getElementById('cookie_content');

    // --- NEW: Function to fetch and display cookies securely ---
    const fetchCookies = async () => {
        try {
            const data = await window.apiRequest('/api/auth/get-cookies');
            cookieTextarea.value = data.cookies;
        } catch (error) {
            // If not logged in, apiRequest will show the login modal.
            // If another error, we leave the textarea empty.
            console.error("Could not fetch cookies:", error.message);
            cookieTextarea.placeholder = "Login to view/edit cookies...";
        }
    };

    window.apiRequest('/api/auth/status').then(status => {
        if (!status.password_set) {
            setupAlert.style.display = 'block';
            currentPasswordGroup.style.display = 'none';
            fetchCookies(); // Fetch cookies (will succeed as no password is set)
        } else {
            setupAlert.style.display = 'none';
            currentPasswordGroup.style.display = 'block';
            if (status.logged_in) {
                logoutBtn.style.display = 'inline-block';
                fetchCookies(); // Fetch cookies now that we know we are logged in
            } else {
                loginBtn.style.display = 'inline-block';
            }
        }
    });

    loginBtn.addEventListener('click', () => window.showLoginModal());

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
                const response = await window.apiRequest('/api/auth/set-password', {
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

    const updateBtn = document.getElementById('check-for-updates-btn');
    if(updateBtn) {
        updateBtn.addEventListener('click', async () => {
            updateBtn.disabled = true;
            updateBtn.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Checking...`;
            try {
                await window.apiRequest('/api/force_update_check', { method: 'POST' });
                location.reload();
            } catch (error) {
                if (error.message !== "AUTH_REQUIRED") {
                    alert(`Could not check for updates. Error: ${error.message}`);
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
                    await window.apiRequest('/api/install_update', { method: 'POST' });
                    document.querySelector('.container').innerHTML = `<div class="alert alert-info mt-4"><h4>Update in Progress</h4><p>The server will restart automatically. This page will become unresponsive. Please wait a minute and then refresh.</p></div>`;
                } catch (error) {
                     if(error.message !== "AUTH_REQUIRED") alert(`Failed to start update. Error: ${error.message}`);
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
                    await window.apiRequest('/api/shutdown', { method: 'POST' });
                    document.querySelector('.container').innerHTML = `<div class="alert alert-info mt-4">The shutdown command has been sent. You can now close this page.</div>`;
                } catch(error) {
                    if(error.message !== "AUTH_REQUIRED") alert(`Failed to send shutdown command. Error: ${error.message}`);
                    shutdownBtn.disabled = false;
                    shutdownBtn.innerHTML = `<i class="bi bi-power"></i> Shutdown Server`;
                }
            });
        });
    }
}

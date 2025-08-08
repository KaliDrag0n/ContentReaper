/**
 * static/js/api.js
 * Centralizes all API endpoint definitions for the application.
 * This makes it easy to update API routes in a single location.
 */

const API_ENDPOINTS = {
    // CSRF and Auth
    csrfToken: '/api/auth/csrf-token',
    authStatus: '/api/auth/status',
    authLogin: '/api/auth/login',
    authLogout: '/api/auth/logout',
    authSetPassword: '/api/auth/set-password',

    // Main Status and Control
    status: '/api/status',
    stop: '/api/stop',
    shutdown: '/api/shutdown',

    // Queue Management
    queue: '/queue',
    queueContinue: '/queue/continue',
    queueClear: '/queue/clear',
    queueDelete: (jobId) => `/queue/delete/by-id/${jobId}`,
    queueReorder: '/queue/reorder',
    queuePause: '/queue/pause',
    queueResume: '/queue/resume',

    // History Management
    historyClear: '/history/clear',
    historyDelete: (logId) => `/history/delete/${logId}`,
    historyItem: (logId) => `/api/history/item/${logId}`,
    historyLog: (logId) => `/history/log/${logId}`,

    // File Management
    files: (path) => `/api/files?path=${encodeURIComponent(path)}`,
    deleteItem: '/api/delete_item',
    downloadItem: (paths) => {
        const queryParams = new URLSearchParams();
        paths.forEach(path => queryParams.append('paths', path));
        return `/download_item?${queryParams.toString()}`;
    },

    // Settings and Updates
    settings: '/api/settings',
    updateCheck: '/api/update_check',
    forceUpdateCheck: '/api/force_update_check',
    installUpdate: '/api/install_update',
    
    // Logging
    liveLog: '/api/log/live/content',
};

// Expose the endpoints to the global scope to be accessible by other scripts.
window.API = API_ENDPOINTS;

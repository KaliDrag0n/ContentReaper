/**
 * static/js/logs.js
 * Enhanced log viewer with filtering, search, statistics, and better formatting.
 */

(function() {
    'use strict';

    class LogViewer {
        constructor() {
            this.currentLogFile = null;
            this.rawLogContent = '';
            this.processedLines = [];
            this.filteredLines = [];
            this.searchQuery = '';
            this.currentFilter = 'all';
            this.autoScroll = true;
            this.stats = {
                total: 0,
                errors: 0,
                warnings: 0,
                info: 0,
                debug: 0
            };

            this.initializeElements();
            this.bindEvents();
            this.fetchLogList();
        }

        initializeElements() {
            this.elements = {
                logSelector: document.getElementById('log-selector'),
                logContent: document.getElementById('log-content'),
                refreshBtn: document.getElementById('refresh-log-btn'),
                searchInput: document.getElementById('search-input'),
                clearSearchBtn: document.getElementById('clear-search-btn'),
                autoScrollBtn: document.getElementById('auto-scroll-btn'),
                downloadBtn: document.getElementById('download-log-btn'),
                filterBtns: document.querySelectorAll('.filter-btn'),
                totalLines: document.getElementById('total-lines'),
                errorCount: document.getElementById('error-count'),
                warningCount: document.getElementById('warning-count'),
                lastUpdated: document.getElementById('last-updated'),
                visibleLines: document.getElementById('visible-lines'),
                totalVisible: document.getElementById('total-visible')
            };
        }

        bindEvents() {
            this.elements.logSelector.addEventListener('change', () => {
                this.currentLogFile = this.elements.logSelector.value;
                this.fetchLogContent(this.currentLogFile);
            });

            this.elements.refreshBtn.addEventListener('click', () => {
                if (this.currentLogFile) {
                    this.fetchLogContent(this.currentLogFile);
                } else {
                    this.fetchLogList();
                }
            });

            this.elements.searchInput.addEventListener('input', (e) => {
                this.searchQuery = e.target.value.toLowerCase();
                this.applyFilters();
            });

            this.elements.clearSearchBtn.addEventListener('click', () => {
                this.elements.searchInput.value = '';
                this.searchQuery = '';
                this.applyFilters();
            });

            this.elements.autoScrollBtn.addEventListener('click', () => {
                this.autoScroll = !this.autoScroll;
                this.updateAutoScrollButton();
            });

            this.elements.downloadBtn.addEventListener('click', () => {
                if (this.currentLogFile && this.rawLogContent) {
                    this.downloadLog();
                }
            });

            this.elements.filterBtns.forEach(btn => {
                btn.addEventListener('click', () => {
                    this.elements.filterBtns.forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    this.currentFilter = btn.dataset.level;
                    this.applyFilters();
                });
            });
        }

        async fetchLogList() {
            try {
                this.elements.logSelector.innerHTML = '<option>Loading logs...</option>';
                this.elements.logSelector.disabled = true;

                const logFiles = await window.apiRequest(window.API.listLogs);

                this.elements.logSelector.innerHTML = '';
                if (logFiles.length === 0) {
                    this.elements.logSelector.innerHTML = '<option>No logs found.</option>';
                    return;
                }

                logFiles.forEach(log => {
                    const option = document.createElement('option');
                    option.value = log.filename;
                    option.textContent = log.display_name;
                    this.elements.logSelector.appendChild(option);
                });

                if (!this.currentLogFile && logFiles.length > 0) {
                    this.currentLogFile = logFiles[0].filename;
                    this.elements.logSelector.value = this.currentLogFile;
                }

                if (this.currentLogFile) {
                    await this.fetchLogContent(this.currentLogFile);
                }

            } catch (error) {
                this.elements.logSelector.innerHTML = '<option>Error loading logs.</option>';
                this.showError(`Could not fetch log list: ${error.message}`);
            } finally {
                this.elements.logSelector.disabled = false;
            }
        }

        async fetchLogContent(filename) {
            if (!filename) {
                this.showEmptyState('Please select a log file.');
                return;
            }

            this.showLoading();
            this.setRefreshState(true);

            try {
                const data = await window.apiRequest(window.API.getLogContent(filename));

                this.rawLogContent = data.content || 'Log file is empty.';
                this.processLogContent();
                this.updateStats();
                this.applyFilters();
                
                if (this.autoScroll) {
                    this.scrollToBottom();
                }

            } catch (error) {
                this.showError(`Error loading log content: ${error.message}`);
            } finally {
                this.setRefreshState(false);
            }
        }

        processLogContent() {
            if (!this.rawLogContent || this.rawLogContent === 'Log file is empty.') {
                this.processedLines = [];
                return;
            }

            const lines = this.rawLogContent.split('\n');
            this.processedLines = lines.map((line, index) => {
                const processed = this.parseLogLine(line, index + 1);
                return processed;
            }).filter(line => line.content.trim() !== '');
        }

        parseLogLine(line, lineNumber) {
            const timestampRegex = /^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}/;
            const levelRegex = /(ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|SUCCESS)/i;
            
            const timestampMatch = line.match(timestampRegex);
            const levelMatch = line.match(levelRegex);
            
            let level = 'info';
            if (levelMatch) {
                level = levelMatch[1].toLowerCase();
                if (level === 'warn' || level === 'warning') level = 'warning';
                if (level === 'fatal') level = 'error';
                if (level === 'trace') level = 'debug';
            }

            return {
                lineNumber,
                timestamp: timestampMatch ? timestampMatch[0] : null,
                level,
                content: line,
                originalContent: line
            };
        }

        applyFilters() {
            let filtered = [...this.processedLines];

            if (this.currentFilter !== 'all') {
                filtered = filtered.filter(line => line.level === this.currentFilter);
            }

            if (this.searchQuery) {
                filtered = filtered.filter(line => 
                    line.content.toLowerCase().includes(this.searchQuery)
                );
            }

            this.filteredLines = filtered;
            this.renderLogContent();
            this.updateFilterStats();
        }

        renderLogContent() {
            if (this.filteredLines.length === 0) {
                const message = this.searchQuery || this.currentFilter !== 'all' 
                    ? 'No matching log entries found.' 
                    : 'No log entries to display.';
                this.showEmptyState(message);
                return;
            }

            const fragment = document.createDocumentFragment();
            
            this.filteredLines.forEach(line => {
                const lineEl = this.createLogLineElement(line);
                fragment.appendChild(lineEl);
            });

            this.elements.logContent.innerHTML = '';
            this.elements.logContent.appendChild(fragment);

            // Highlight search terms
            if (this.searchQuery) {
                this.highlightSearchTerms();
            }
        }

        createLogLineElement(line) {
            const div = document.createElement('div');
            div.className = `log-line ${line.level}`;
            
            let content = line.content;
            
            // Format timestamp and level
            if (line.timestamp) {
                content = content.replace(line.timestamp, `<span class="timestamp">${line.timestamp}</span>`);
            }
            
            const levelRegex = /(ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|SUCCESS)/gi;
            content = content.replace(levelRegex, (match) => {
                return `<span class="log-level ${match.toUpperCase()}">${match.toUpperCase()}</span>`;
            });

            div.innerHTML = content;
            return div;
        }

        highlightSearchTerms() {
            if (!this.searchQuery) return;

            const walker = document.createTreeWalker(
                this.elements.logContent,
                NodeFilter.SHOW_TEXT,
                null,
                false
            );

            const textNodes = [];
            let node;
            while (node = walker.nextNode()) {
                textNodes.push(node);
            }

            textNodes.forEach(textNode => {
                const text = textNode.textContent;
                const index = text.toLowerCase().indexOf(this.searchQuery);
                if (index !== -1) {
                    const highlightedText = text.substring(0, index) +
                        `<span class="highlight-match">${text.substring(index, index + this.searchQuery.length)}</span>` +
                        text.substring(index + this.searchQuery.length);
                    
                    const wrapper = document.createElement('span');
                    wrapper.innerHTML = highlightedText;
                    textNode.parentNode.replaceChild(wrapper, textNode);
                }
            });
        }

        updateStats() {
            const stats = {
                total: this.processedLines.length,
                errors: 0,
                warnings: 0,
                info: 0,
                debug: 0
            };

            for (const line of this.processedLines) {
                switch (line.level) {
                    case 'error':
                        stats.errors++;
                        break;
                    case 'warning':
                        stats.warnings++;
                        break;
                    case 'info':
                        stats.info++;
                        break;
                    case 'debug':
                        stats.debug++;
                        break;
                }
            }
            this.stats = stats;

            this.elements.totalLines.textContent = this.stats.total;
            this.elements.errorCount.textContent = this.stats.errors;
            this.elements.warningCount.textContent = this.stats.warnings;
            this.elements.lastUpdated.textContent = new Date().toLocaleTimeString();
        }

        updateFilterStats() {
            this.elements.visibleLines.textContent = this.filteredLines.length;
            this.elements.totalVisible.textContent = this.processedLines.length;
        }

        updateAutoScrollButton() {
            const btn = this.elements.autoScrollBtn;
            if (this.autoScroll) {
                btn.classList.replace('btn-info', 'btn-success');
                btn.innerHTML = '<i class="bi bi-arrow-down-square-fill"></i>';
                btn.title = 'Auto-scroll Enabled';
            } else {
                btn.classList.replace('btn-success', 'btn-info');
                btn.innerHTML = '<i class="bi bi-arrow-down-square"></i>';
                btn.title = 'Auto-scroll Disabled';
            }
        }

        scrollToBottom() {
            setTimeout(() => {
                this.elements.logContent.scrollTop = this.elements.logContent.scrollHeight;
            }, 100);
        }

        downloadLog() {
            const blob = new Blob([this.rawLogContent], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = this.currentLogFile;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        showLoading() {
            this.elements.logContent.innerHTML = `
                <div class="empty-state">
                    <div class="loading-spinner">
                        <div class="spinner-border spinner-border-sm" role="status"></div>
                        <span>Loading log content...</span>
                    </div>
                </div>
            `;
        }

        showError(message) {
            this.elements.logContent.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-exclamation-triangle text-warning"></i>
                    <p>${message}</p>
                </div>
            `;
        }

        showEmptyState(message) {
            this.elements.logContent.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-file-text"></i>
                    <p>${message}</p>
                </div>
            `;
        }

        setRefreshState(isLoading) {
            const btn = this.elements.refreshBtn;
            const icon = btn.querySelector('i');
            
            if (isLoading) {
                btn.disabled = true;
                icon.className = 'spinner-border spinner-border-sm';
            } else {
                btn.disabled = false;
                icon.className = 'bi bi-arrow-clockwise';
            }
        }
    }

    // Initialize the log viewer when dependencies are ready
    document.addEventListener('DOMContentLoaded', () => {
        const checkGlobals = setInterval(() => {
            if (window.applyTheme && window.apiRequest && window.API) {
                clearInterval(checkGlobals);
                new LogViewer();
            }
        }, 50);
    });

})();
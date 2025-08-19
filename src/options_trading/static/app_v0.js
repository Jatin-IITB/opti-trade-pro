// Options Trading Dashboard JavaScript

class TradingDashboard {
    constructor() {
        this.data = {
            authStatus: {
                isAuthenticated: true,
                user: "trader_001",
                tokenExpiry: "2025-08-07T15:30:00Z",
                timeUntilExpiry: "6h 45m",
                lastLogin: "2025-08-06T08:15:22Z"
            },
            apiConfig: {
                upstoxApiKey: "8d7f1e19-7cc8-492f-85f1-463210eb23ad",
                redirectUri: "http://localhost:8000/api/v1/auth/callback",
                environment: "development",
                connectionStatus: "connected",
                lastPing: "2025-08-06T20:02:15Z",
                responseTime: "245ms"
            },
            marketData: {
                status: "active",
                feedsConnected: 3,
                totalInstruments: 1247,
                lastUpdate: "2025-08-06T20:02:10Z",
                dataQuality: "excellent",
                feeds: [
                    {"name": "NSE Options", "status": "connected", "instruments": 856},
                    {"name": "BSE Options", "status": "connected", "instruments": 234},
                    {"name": "Index Futures", "status": "connected", "instruments": 157}
                ]
            },
            strategies: [
                {
                    id: "delta_neutral_001",
                    name: "Delta Neutral Straddle",
                    status: "active",
                    pnl: "+₹12,450",
                    pnlPercent: "+2.34%",
                    riskLevel: "medium",
                    positions: 8,
                    lastRebalance: "2025-08-06T19:45:00Z"
                },
                {
                    id: "gamma_scalping_002", 
                    name: "Gamma Scalping NIFTY",
                    status: "active",
                    pnl: "+₹8,750",
                    pnlPercent: "+1.78%",
                    riskLevel: "high",
                    positions: 12,
                    lastRebalance: "2025-08-06T19:30:00Z"
                },
                {
                    id: "volatility_arb_003",
                    name: "Volatility Arbitrage",
                    status: "paused",
                    pnl: "-₹3,200",
                    pnlPercent: "-0.65%",
                    riskLevel: "low",
                    positions: 4,
                    lastRebalance: "2025-08-06T18:15:00Z"
                }
            ],
            systemHealth: {
                status: "healthy",
                uptime: "2d 14h 32m",
                cpuUsage: "23%",
                memoryUsage: "67%",
                diskUsage: "45%",
                errorCount: 0,
                warningCount: 2,
                lastBackup: "2025-08-06T06:00:00Z"
            },
            recentLogs: [
                {"time": "20:02:15", "level": "INFO", "message": "Market data feed refreshed successfully"},
                {"time": "20:01:45", "level": "INFO", "message": "Delta neutral strategy rebalanced"},
                {"time": "20:00:30", "level": "WARN", "message": "High volatility detected in NIFTY options"},
                {"time": "19:58:12", "level": "INFO", "message": "Token refresh completed"},
                {"time": "19:55:03", "level": "DEBUG", "message": "Fetched 1247 instrument contracts"}
            ],
            quickStats: {
                totalPnL: "+₹18,000",
                todaysPnL: "+₹4,250",
                activeStrategies: 2,
                totalPositions: 24,
                marginUsed: "₹2,45,000",
                availableMargin: "₹3,55,000"
            }
        };
        
        this.init();
    }

    init() {
        this.bindEvents();
        this.updateDisplay();
        this.startRealTimeUpdates();
    }

    bindEvents() {
        // Quick action buttons
        document.getElementById('loginBtn').addEventListener('click', () => this.handleLogin());
        document.getElementById('logoutBtn').addEventListener('click', () => this.handleLogout());
        document.getElementById('refreshTokenBtn').addEventListener('click', () => this.refreshToken());
        document.getElementById('testConnectionBtn').addEventListener('click', () => this.testConnection());
        document.getElementById('refreshDataBtn').addEventListener('click', () => this.refreshData());
        document.getElementById('clearLogsBtn').addEventListener('click', () => this.clearLogs());

        // Add hover effects to strategy items
        this.addHoverEffects();
    }

    addHoverEffects() {
        const strategyItems = document.querySelectorAll('.strategy-item');
        strategyItems.forEach(item => {
            item.addEventListener('mouseenter', (e) => {
                e.target.style.transform = 'translateY(-2px)';
            });
            
            item.addEventListener('mouseleave', (e) => {
                e.target.style.transform = 'translateY(0)';
            });
        });
    }

    updateDisplay() {
        this.updateAuthStatus();
        this.updateApiConfig();
        this.updateMarketData();
        this.updateSystemHealth();
        this.updateStrategies();
        this.updateLogs();
        this.updateQuickStats();
    }

    updateAuthStatus() {
        const { authStatus } = this.data;
        
        document.getElementById('authUser').textContent = authStatus.user;
        document.getElementById('authStatus').textContent = authStatus.isAuthenticated ? 'Authenticated' : 'Not Authenticated';
        document.getElementById('tokenExpiry').textContent = authStatus.timeUntilExpiry + ' remaining';
        document.getElementById('lastLogin').textContent = this.formatDate(authStatus.lastLogin);

        // Update status indicator
        const authIndicator = document.getElementById('authIndicator');
        const statusLight = authIndicator.querySelector('.status-light');
        const statusText = authIndicator.querySelector('span');
        
        if (authStatus.isAuthenticated) {
            statusLight.className = 'status-light connected';
            statusText.textContent = 'Connected';
        } else {
            statusLight.className = 'status-light disconnected';
            statusText.textContent = 'Disconnected';
        }
    }

    updateApiConfig() {
        const { apiConfig } = this.data;
        
        document.getElementById('apiEnvironment').textContent = this.capitalize(apiConfig.environment);
        document.getElementById('responseTime').textContent = apiConfig.responseTime;
        document.getElementById('lastPing').textContent = this.formatTime(apiConfig.lastPing);
        document.getElementById('redirectUri').textContent = apiConfig.redirectUri.replace('http://', '');

        // Update API status indicator
        const apiIndicator = document.getElementById('apiIndicator');
        const statusLight = apiIndicator.querySelector('.status-light');
        const statusText = apiIndicator.querySelector('span');
        
        if (apiConfig.connectionStatus === 'connected') {
            statusLight.className = 'status-light connected';
            statusText.textContent = 'Connected';
        } else {
            statusLight.className = 'status-light disconnected';
            statusText.textContent = 'Disconnected';
        }
    }

    updateMarketData() {
        const { marketData } = this.data;
        
        document.getElementById('feedsConnected').textContent = marketData.feedsConnected;
        document.getElementById('totalInstruments').textContent = marketData.totalInstruments.toLocaleString();
        document.getElementById('dataQuality').textContent = this.capitalize(marketData.dataQuality);

        // Update market status indicator
        const marketIndicator = document.getElementById('marketIndicator');
        const statusLight = marketIndicator.querySelector('.status-light');
        const statusText = marketIndicator.querySelector('span');
        
        if (marketData.status === 'active') {
            statusLight.className = 'status-light active';
            statusText.textContent = 'Active';
        } else {
            statusLight.className = 'status-light warning';
            statusText.textContent = 'Inactive';
        }
    }

    updateSystemHealth() {
        const { systemHealth } = this.data;
        
        document.getElementById('uptime').textContent = systemHealth.uptime;
        document.getElementById('cpuUsage').textContent = systemHealth.cpuUsage;
        document.getElementById('memoryUsage').textContent = systemHealth.memoryUsage;
        document.getElementById('diskUsage').textContent = systemHealth.diskUsage;
        document.getElementById('errorCount').textContent = systemHealth.errorCount;
        document.getElementById('warningCount').textContent = systemHealth.warningCount;

        // Update system status indicator
        const systemIndicator = document.getElementById('systemIndicator');
        const statusLight = systemIndicator.querySelector('.status-light');
        const statusText = systemIndicator.querySelector('span');
        
        if (systemHealth.status === 'healthy') {
            statusLight.className = 'status-light healthy';
            statusText.textContent = 'Healthy';
        } else {
            statusLight.className = 'status-light warning';
            statusText.textContent = 'Warning';
        }

        // Update progress bars
        this.updateProgressBar('cpuUsage', parseInt(systemHealth.cpuUsage));
        this.updateProgressBar('memoryUsage', parseInt(systemHealth.memoryUsage));
        this.updateProgressBar('diskUsage', parseInt(systemHealth.diskUsage));
    }

    updateProgressBar(metric, percentage) {
        const progressBars = document.querySelectorAll('.metric-progress');
        const labels = document.querySelectorAll('.metric-label');
        
        labels.forEach((label, index) => {
            if (label.textContent.toLowerCase().includes(metric.replace('Usage', '').toLowerCase())) {
                const progressBar = progressBars[index];
                progressBar.style.width = percentage + '%';
                
                // Update color based on percentage
                progressBar.className = 'metric-progress';
                if (percentage > 80) {
                    progressBar.classList.add('error');
                } else if (percentage > 60) {
                    progressBar.classList.add('warning');
                }
            }
        });
    }

    updateStrategies() {
        const { strategies } = this.data;
        const strategiesList = document.getElementById('strategiesList');
        
        strategiesList.innerHTML = strategies.map(strategy => `
            <div class="strategy-item">
                <div class="strategy-status ${strategy.status}"></div>
                <div class="strategy-info">
                    <h4 class="strategy-name">${strategy.name}</h4>
                    <span class="strategy-positions">${strategy.positions} positions</span>
                </div>
                <div class="strategy-metrics">
                    <span class="strategy-pnl ${strategy.pnl.startsWith('+') ? 'positive' : 'negative'}">${strategy.pnl}</span>
                    <span class="strategy-percent ${strategy.pnlPercent.startsWith('+') ? 'positive' : 'negative'}">(${strategy.pnlPercent})</span>
                </div>
                <div class="strategy-risk ${strategy.riskLevel}">${this.capitalize(strategy.riskLevel)} Risk</div>
            </div>
        `).join('');

        // Re-add hover effects to new elements
        this.addHoverEffects();
    }

    updateLogs() {
        const { recentLogs } = this.data;
        const logsContainer = document.getElementById('logsContainer');
        
        logsContainer.innerHTML = recentLogs.map(log => `
            <div class="log-entry ${log.level.toLowerCase()}">
                <span class="log-time">${log.time}</span>
                <span class="log-level">${log.level}</span>
                <span class="log-message">${log.message}</span>
            </div>
        `).join('');
    }

    updateQuickStats() {
        const { quickStats } = this.data;
        
        document.getElementById('totalPnL').textContent = quickStats.totalPnL;
        document.getElementById('todaysPnL').textContent = quickStats.todaysPnL;
        document.getElementById('activeStrategies').textContent = quickStats.activeStrategies;
    }

    // Event Handlers
    handleLogin() {
        this.showNotification('Initiating login process...', 'info');
        
        // Simulate login process
        setTimeout(() => {
            if (!this.data.authStatus.isAuthenticated) {
                this.data.authStatus.isAuthenticated = true;
                this.data.authStatus.user = 'trader_001';
                this.data.authStatus.lastLogin = new Date().toISOString();
                this.data.authStatus.timeUntilExpiry = '8h 0m';
                this.updateAuthStatus();
                this.showNotification('Login successful!', 'success');
                this.addLog('INFO', 'User authentication successful');
            } else {
                this.showNotification('Already authenticated', 'warning');
            }
        }, 1500);
    }

    handleLogout() {
        this.showNotification('Logging out...', 'info');
        
        setTimeout(() => {
            this.data.authStatus.isAuthenticated = false;
            this.data.authStatus.user = 'Not logged in';
            this.data.authStatus.timeUntilExpiry = 'N/A';
            this.updateAuthStatus();
            this.showNotification('Logged out successfully', 'success');
            this.addLog('INFO', 'User logged out');
        }, 1000);
    }

    refreshToken() {
        this.showNotification('Refreshing authentication token...', 'info');
        
        setTimeout(() => {
            if (this.data.authStatus.isAuthenticated) {
                this.data.authStatus.timeUntilExpiry = '8h 0m';
                this.updateAuthStatus();
                this.showNotification('Token refreshed successfully', 'success');
                this.addLog('INFO', 'Authentication token refreshed');
            } else {
                this.showNotification('Please login first', 'error');
            }
        }, 1200);
    }

    testConnection() {
        this.showNotification('Testing API connection...', 'info');
        
        // Simulate connection test
        setTimeout(() => {
            const isConnected = Math.random() > 0.1; // 90% success rate
            
            if (isConnected) {
                this.data.apiConfig.connectionStatus = 'connected';
                this.data.apiConfig.responseTime = Math.floor(Math.random() * 200 + 100) + 'ms';
                this.data.apiConfig.lastPing = new Date().toISOString();
                this.updateApiConfig();
                this.showNotification('Connection test successful', 'success');
                this.addLog('INFO', 'API connection test completed successfully');
            } else {
                this.data.apiConfig.connectionStatus = 'disconnected';
                this.updateApiConfig();
                this.showNotification('Connection test failed', 'error');
                this.addLog('ERROR', 'API connection test failed');
            }
        }, 2000);
    }

    refreshData() {
        this.showNotification('Refreshing market data...', 'info');
        
        setTimeout(() => {
            // Simulate data changes
            this.data.marketData.totalInstruments += Math.floor(Math.random() * 10 - 5);
            this.data.marketData.lastUpdate = new Date().toISOString();
            
            // Update some system metrics
            this.data.systemHealth.cpuUsage = Math.floor(Math.random() * 30 + 15) + '%';
            this.data.systemHealth.memoryUsage = Math.floor(Math.random() * 20 + 60) + '%';
            
            this.updateDisplay();
            this.showNotification('Data refreshed successfully', 'success');
            this.addLog('INFO', 'Market data and system metrics refreshed');
        }, 1800);
    }

    clearLogs() {
        const logsContainer = document.getElementById('logsContainer');
        logsContainer.innerHTML = '<div class="log-entry info"><span class="log-time">' + 
            this.getCurrentTime() + '</span><span class="log-level">INFO</span>' +
            '<span class="log-message">Logs cleared by user</span></div>';
        
        this.data.recentLogs = [{
            time: this.getCurrentTime(),
            level: "INFO",
            message: "Logs cleared by user"
        }];
        
        this.showNotification('Logs cleared', 'info');
    }

    // Utility Methods
    addLog(level, message) {
        const newLog = {
            time: this.getCurrentTime(),
            level: level,
            message: message
        };
        
        this.data.recentLogs.unshift(newLog);
        if (this.data.recentLogs.length > 10) {
            this.data.recentLogs.pop();
        }
        
        this.updateLogs();
    }

    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            color: white;
            font-weight: 500;
            z-index: 1000;
            animation: slideIn 0.3s ease-out;
            max-width: 300px;
        `;
        
        // Set background color based on type
        const colors = {
            info: '#3b82f6',
            success: '#10b981',
            warning: '#f59e0b',
            error: '#ef4444'
        };
        
        notification.style.backgroundColor = colors[type] || colors.info;
        notification.textContent = message;
        
        // Add animation styles
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
        
        document.body.appendChild(notification);
        
        // Remove notification after 3 seconds
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease-in';
            setTimeout(() => {
                document.body.removeChild(notification);
            }, 300);
        }, 3000);
    }

    startRealTimeUpdates() {
        // Update timestamps every minute
        setInterval(() => {
            document.getElementById('lastUpdated').textContent = this.getCurrentTime();
        }, 60000);

        // Simulate real-time data updates every 30 seconds
        setInterval(() => {
            // Update some random metrics to simulate live data
            if (Math.random() > 0.7) {
                const messages = [
                    'Market data feed updated',
                    'Strategy positions rebalanced',
                    'New option contracts detected',
                    'System health check completed',
                    'Token validation successful'
                ];
                
                const message = messages[Math.floor(Math.random() * messages.length)];
                this.addLog('INFO', message);
            }
        }, 30000);
    }

    // Helper Methods
    formatDate(dateString) {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', { 
            month: 'short', 
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    formatTime(dateString) {
        const date = new Date(dateString);
        return date.toLocaleTimeString('en-US', { 
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    getCurrentTime() {
        return new Date().toLocaleTimeString('en-US', { 
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    capitalize(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new TradingDashboard();
});

// Add some additional interactive features
document.addEventListener('DOMContentLoaded', () => {
    // Add click effects to cards
    const cards = document.querySelectorAll('.card');
    cards.forEach(card => {
        card.addEventListener('click', (e) => {
            // Don't trigger on button clicks
            if (e.target.tagName !== 'BUTTON') {
                card.style.transform = 'scale(0.98)';
                setTimeout(() => {
                    card.style.transform = 'scale(1)';
                }, 100);
            }
        });
    });

    // Add keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.metaKey) {
            switch(e.key) {
                case 'r':
                    e.preventDefault();
                    document.getElementById('refreshDataBtn').click();
                    break;
                case 'l':
                    e.preventDefault();
                    document.getElementById('loginBtn').click();
                    break;
                case 't':
                    e.preventDefault();
                    document.getElementById('testConnectionBtn').click();
                    break;
            }
        }
    });
});
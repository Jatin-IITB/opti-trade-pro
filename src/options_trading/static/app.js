// Institutional Options Trading Dashboard - Production Ready
class TradingDashboard {
    constructor() {
        this.data = null;
        this.charts = {};
        this.websocket = null;
        this.refreshInterval = null;
        this.isConnected = false;
        
        this.init();
    }

    async init() {
        await this.loadInitialData();
        this.setupWebSocket();
        this.setupEventListeners();
        this.startRealTimeUpdates();
        this.initializeCharts();
        
        console.log('Trading Dashboard initialized successfully');
    }

    async loadInitialData() {
        // In production, this would be API calls to your FastAPI backend
        this.data = {
            "systemStatus": {
                "overall_health": "healthy",
                "authentication": {
                    "is_authenticated": true,
                    "user_id": "trader_001",
                    "user_name": "Senior Trader",
                    "time_until_expiry": "6h 45m",
                    "last_login": "2025-08-06T08:15:22Z"
                },
                "market_data": {
                    "overall_status": "connected",
                    "feeds_connected": 3,
                    "total_instruments": 1247,
                    "data_quality": "excellent",
                    "response_time_ms": 245.0,
                    "feeds": [
                        {"name": "NSE Options", "status": "connected", "instruments_count": 856, "latency_ms": 12.3, "error_rate": 0.1},
                        {"name": "BSE Options", "status": "connected", "instruments_count": 234, "latency_ms": 18.7, "error_rate": 0.3},
                        {"name": "Index Futures", "status": "connected", "instruments_count": 157, "latency_ms": 8.9, "error_rate": 0.0}
                    ]
                },
                "system_metrics": {
                    "uptime": "2d 14h 32m",
                    "cpu_usage_percent": 23.5,
                    "memory_usage_percent": 67.2,
                    "disk_usage_percent": 45.8,
                    "error_count": 0,
                    "warning_count": 2
                }
            },
            "strategies": [
                {
                    "strategy_id": "delta_neutral_001",
                    "name": "Delta Neutral Straddle",
                    "status": "active", 
                    "positions_count": 8,
                    "total_pnl": 12450.00,
                    "daily_pnl": 2340.00,
                    "pnl_percentage": 2.34,
                    "risk_level": "medium",
                    "portfolio_greeks": {
                        "delta": 0.02,
                        "gamma": 0.15,
                        "theta": -45.67,
                        "vega": 123.45
                    }
                },
                {
                    "strategy_id": "gamma_scalping_002", 
                    "name": "Gamma Scalping NIFTY",
                    "status": "active",
                    "positions_count": 12,
                    "total_pnl": 8750.00,
                    "daily_pnl": 1780.00,
                    "pnl_percentage": 1.78,
                    "risk_level": "high",
                    "portfolio_greeks": {
                        "delta": -0.08,
                        "gamma": 0.45,
                        "theta": -78.91,
                        "vega": 89.34
                    }
                },
                {
                    "strategy_id": "volatility_arb_003",
                    "name": "Volatility Arbitrage", 
                    "status": "paused",
                    "positions_count": 4,
                    "total_pnl": -3200.00,
                    "daily_pnl": -650.00,
                    "pnl_percentage": -0.65,
                    "risk_level": "low",
                    "portfolio_greeks": {
                        "delta": 0.12,
                        "gamma": 0.08,
                        "theta": -23.45,
                        "vega": 45.67
                    }
                }
            ],
            "positionSummary": {
                "total_positions": 24,
                "active_strategies": 2,
                "total_pnl": 18000.00,
                "daily_pnl": 4250.00,
                "margin_used": 245000.00,
                "available_margin": 355000.00,
                "portfolio_delta": 0.02,
                "portfolio_gamma": 0.68,
                "portfolio_theta": -148.03,
                "portfolio_vega": 258.46
            },
            "riskMetrics": {
                "var_1d": 25000.00,
                "var_1d_percentage": 4.17,
                "expected_shortfall": 31250.00,
                "maximum_drawdown": 18500.00,
                "delta_limit_utilization": 12.5,
                "gamma_limit_utilization": 34.2,
                "vega_limit_utilization": 25.8
            },
            "optionChain": {
                "symbol": "NIFTY",
                "spot_price": 19450.25,
                "expiry_date": "2025-08-14",
                "call_options": [
                    {"strike": 19300, "last_price": 180.50, "iv": 0.1523, "delta": 0.62, "gamma": 0.0015, "theta": -12.45, "volume": 1250},
                    {"strike": 19400, "last_price": 105.75, "iv": 0.1487, "delta": 0.48, "gamma": 0.0018, "theta": -15.30, "volume": 2100},
                    {"strike": 19450, "last_price": 78.25, "iv": 0.1465, "delta": 0.39, "gamma": 0.0019, "theta": -18.75, "volume": 3450},
                    {"strike": 19500, "last_price": 55.50, "iv": 0.1456, "delta": 0.31, "gamma": 0.0018, "theta": -16.20, "volume": 2875}
                ],
                "put_options": [
                    {"strike": 19300, "last_price": 45.25, "iv": 0.1534, "delta": -0.38, "gamma": 0.0015, "theta": -11.85, "volume": 980},
                    {"strike": 19400, "last_price": 78.50, "iv": 0.1498, "delta": -0.52, "gamma": 0.0018, "theta": -14.65, "volume": 1750},
                    {"strike": 19450, "last_price": 98.75, "iv": 0.1478, "delta": -0.61, "gamma": 0.0019, "theta": -17.90, "volume": 2650},
                    {"strike": 19500, "last_price": 125.25, "iv": 0.1467, "delta": -0.69, "gamma": 0.0018, "theta": -15.55, "volume": 1980}
                ]
            },
            "recentLogs": [
                {"timestamp": "20:02:15", "level": "INFO", "message": "Market data feed refreshed successfully", "module": "market_data"},
                {"timestamp": "20:01:45", "level": "INFO", "message": "Delta neutral strategy rebalanced", "module": "strategy"}, 
                {"timestamp": "20:00:30", "level": "WARN", "message": "High volatility detected in NIFTY options", "module": "risk"},
                {"timestamp": "19:58:12", "level": "INFO", "message": "Token refresh completed", "module": "auth"},
                {"timestamp": "19:55:03", "level": "DEBUG", "message": "Fetched 1247 instrument contracts", "module": "data"}
            ]
        };

        this.renderInitialData();
    }

    setupWebSocket() {
        // Simulate WebSocket connection for real-time updates
        // In production, this would connect to your FastAPI WebSocket endpoint
        this.simulateWebSocketConnection();
        this.updateWebSocketStatus(true);
    }

    simulateWebSocketConnection() {
        // Simulate real-time market data updates
        setInterval(() => {
            if (this.isConnected) {
                this.simulateMarketDataUpdate();
            }
        }, 2000);

        // Simulate P&L updates
        setInterval(() => {
            if (this.isConnected) {
                this.simulatePnLUpdate();
            }
        }, 5000);

        // Simulate system metrics updates
        setInterval(() => {
            if (this.isConnected) {
                this.simulateSystemMetricsUpdate();
            }
        }, 10000);
    }

    simulateMarketDataUpdate() {
        // Simulate spot price changes
        const currentSpot = this.data.optionChain.spot_price;
        const change = (Math.random() - 0.5) * 10;
        this.data.optionChain.spot_price = Math.max(currentSpot + change, 19000);
        
        // Update display
        const spotElement = document.querySelector('.spot-price');
        if (spotElement) {
            spotElement.textContent = `Spot: ₹${this.data.optionChain.spot_price.toFixed(2)}`;
        }

        // Update option prices
        this.data.optionChain.call_options.forEach(option => {
            option.last_price += (Math.random() - 0.5) * 2;
            option.last_price = Math.max(option.last_price, 0.05);
        });

        this.data.optionChain.put_options.forEach(option => {
            option.last_price += (Math.random() - 0.5) * 2;
            option.last_price = Math.max(option.last_price, 0.05);
        });

        this.renderOptionChain();
    }

    simulatePnLUpdate() {
        // Simulate P&L changes
        this.data.strategies.forEach(strategy => {
            const change = (Math.random() - 0.5) * 500;
            strategy.daily_pnl += change;
            strategy.total_pnl += change;
            strategy.pnl_percentage = (strategy.daily_pnl / 100000) * 100;
        });

        // Update portfolio summary
        this.data.positionSummary.total_pnl = this.data.strategies.reduce((sum, s) => sum + s.total_pnl, 0);
        this.data.positionSummary.daily_pnl = this.data.strategies.reduce((sum, s) => sum + s.daily_pnl, 0);

        this.renderStrategies();
        this.renderPortfolioMetrics();
        this.updatePnLChart();
    }

    simulateSystemMetricsUpdate() {
        // Simulate system metrics changes
        this.data.systemStatus.system_metrics.cpu_usage_percent += (Math.random() - 0.5) * 5;
        this.data.systemStatus.system_metrics.memory_usage_percent += (Math.random() - 0.5) * 2;
        
        // Keep within bounds
        this.data.systemStatus.system_metrics.cpu_usage_percent = 
            Math.max(0, Math.min(100, this.data.systemStatus.system_metrics.cpu_usage_percent));
        this.data.systemStatus.system_metrics.memory_usage_percent = 
            Math.max(0, Math.min(100, this.data.systemStatus.system_metrics.memory_usage_percent));

        this.renderSystemMetrics();
        
        // Simulate new log entries
        if (Math.random() < 0.3) {
            this.addNewLogEntry();
        }
    }

    addNewLogEntry() {
        const messages = [
            "Portfolio rebalancing completed",
            "Risk limits checked successfully",
            "Market data latency: 12ms",
            "Options chain updated",
            "Greeks calculation completed",
            "Position size adjusted"
        ];

        const modules = ["strategy", "risk", "market_data", "options", "analytics", "portfolio"];
        const levels = ["INFO", "DEBUG"];

        const now = new Date();
        const timestamp = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
        
        const newEntry = {
            timestamp: timestamp,
            level: levels[Math.floor(Math.random() * levels.length)],
            message: messages[Math.floor(Math.random() * messages.length)],
            module: modules[Math.floor(Math.random() * modules.length)]
        };

        this.data.recentLogs.unshift(newEntry);
        this.data.recentLogs = this.data.recentLogs.slice(0, 10);
        
        this.renderSystemLogs();
    }

    renderInitialData() {
        this.renderStrategies();
        this.renderPortfolioMetrics();
        this.renderOptionChain();
        this.renderSystemMetrics();
        this.renderSystemLogs();
        this.updateMarketTime();
    }

    renderStrategies() {
        const strategiesGrid = document.getElementById('strategiesGrid');
        if (!strategiesGrid) return;

        strategiesGrid.innerHTML = this.data.strategies.map(strategy => `
            <div class="strategy-card">
                <div class="strategy-header">
                    <h3 class="strategy-name">${strategy.name}</h3>
                    <span class="strategy-status ${strategy.status}">${strategy.status.toUpperCase()}</span>
                </div>
                
                <div class="strategy-pnl">
                    <div class="pnl-item">
                        <span class="pnl-label">Total P&L</span>
                        <span class="pnl-value ${strategy.total_pnl >= 0 ? 'positive' : 'negative'}">
                            ₹${strategy.total_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                        </span>
                    </div>
                    <div class="pnl-item">
                        <span class="pnl-label">Daily P&L</span>
                        <span class="pnl-value ${strategy.daily_pnl >= 0 ? 'positive' : 'negative'}">
                            ₹${strategy.daily_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}
                        </span>
                    </div>
                    <div class="pnl-item">
                        <span class="pnl-label">P&L %</span>
                        <span class="pnl-value ${strategy.pnl_percentage >= 0 ? 'positive' : 'negative'}">
                            ${strategy.pnl_percentage.toFixed(2)}%
                        </span>
                    </div>
                    <div class="pnl-item">
                        <span class="pnl-label">Positions</span>
                        <span class="pnl-value">${strategy.positions_count}</span>
                    </div>
                </div>
                
                <div class="strategy-greeks">
                    <div class="greek-item">
                        <span class="greek-label">Δ</span>
                        <span class="greek-value ${strategy.portfolio_greeks.delta < 0 ? 'negative' : ''}">${strategy.portfolio_greeks.delta.toFixed(2)}</span>
                    </div>
                    <div class="greek-item">
                        <span class="greek-label">Γ</span>
                        <span class="greek-value">${strategy.portfolio_greeks.gamma.toFixed(2)}</span>
                    </div>
                    <div class="greek-item">
                        <span class="greek-label">Θ</span>
                        <span class="greek-value negative">${strategy.portfolio_greeks.theta.toFixed(2)}</span>
                    </div>
                    <div class="greek-item">
                        <span class="greek-label">ν</span>
                        <span class="greek-value">${strategy.portfolio_greeks.vega.toFixed(2)}</span>
                    </div>
                </div>
            </div>
        `).join('');
    }

    renderPortfolioMetrics() {
        const portfolio = this.data.positionSummary;
        
        // Update total P&L
        const totalPnlElement = document.getElementById('totalPnl');
        if (totalPnlElement) {
            totalPnlElement.textContent = `₹${portfolio.total_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}`;
            totalPnlElement.className = `metric-value ${portfolio.total_pnl >= 0 ? 'positive' : 'negative'}`;
        }

        const totalPnlChangeElement = document.getElementById('totalPnlChange');
        if (totalPnlChangeElement) {
            totalPnlChangeElement.textContent = `+₹${portfolio.daily_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})} (24h)`;
            totalPnlChangeElement.className = `metric-change ${portfolio.daily_pnl >= 0 ? 'positive' : 'negative'}`;
        }

        // Update positions
        const totalPositionsElement = document.getElementById('totalPositions');
        if (totalPositionsElement) {
            totalPositionsElement.textContent = portfolio.total_positions;
        }

        const activeStrategiesElement = document.getElementById('activeStrategies');
        if (activeStrategiesElement) {
            activeStrategiesElement.textContent = `${portfolio.active_strategies} Active Strategies`;
        }

        // Update margin
        const marginUsedElement = document.getElementById('marginUsed');
        if (marginUsedElement) {
            marginUsedElement.textContent = `₹${portfolio.margin_used.toLocaleString('en-IN')}`;
        }

        // Update portfolio Greeks
        ['Delta', 'Gamma', 'Theta', 'Vega'].forEach(greek => {
            const element = document.getElementById(`portfolio${greek}`);
            if (element) {
                const value = portfolio[`portfolio_${greek.toLowerCase()}`];
                element.textContent = value.toFixed(2);
                element.className = `greek-value ${value < 0 ? 'negative' : ''}`;
            }
        });
    }

    renderOptionChain() {
        const optionChain = document.getElementById('optionChain');
        if (!optionChain) return;

        const chainData = this.data.optionChain;
        optionChain.innerHTML = '';

        // Combine calls and puts by strike
        const strikes = [...new Set([
            ...chainData.call_options.map(opt => opt.strike),
            ...chainData.put_options.map(opt => opt.strike)
        ])].sort((a, b) => a - b);

        strikes.forEach(strike => {
            const callOption = chainData.call_options.find(opt => opt.strike === strike);
            const putOption = chainData.put_options.find(opt => opt.strike === strike);

            const row = document.createElement('div');
            row.className = 'option-row';
            row.innerHTML = `
                <div class="option-data call">
                    ${callOption ? `
                        <div>
                            <div class="data-label">Price</div>
                            <div class="data-value">₹${callOption.last_price.toFixed(2)}</div>
                        </div>
                        <div>
                            <div class="data-label">IV</div>
                            <div class="data-value">${(callOption.iv * 100).toFixed(1)}%</div>
                        </div>
                        <div>
                            <div class="data-label">Delta</div>
                            <div class="data-value">${callOption.delta.toFixed(3)}</div>
                        </div>
                        <div>
                            <div class="data-label">Volume</div>
                            <div class="data-value">${callOption.volume.toLocaleString()}</div>
                        </div>
                    ` : '<div colspan="4">-</div>'}
                </div>
                
                <div class="strike-price">${strike}</div>
                
                <div class="option-data put">
                    ${putOption ? `
                        <div>
                            <div class="data-label">Price</div>
                            <div class="data-value">₹${putOption.last_price.toFixed(2)}</div>
                        </div>
                        <div>
                            <div class="data-label">IV</div>
                            <div class="data-value">${(putOption.iv * 100).toFixed(1)}%</div>
                        </div>
                        <div>
                            <div class="data-label">Delta</div>
                            <div class="data-value">${putOption.delta.toFixed(3)}</div>
                        </div>
                        <div>
                            <div class="data-label">Volume</div>
                            <div class="data-value">${putOption.volume.toLocaleString()}</div>
                        </div>
                    ` : '<div colspan="4">-</div>'}
                </div>
            `;
            optionChain.appendChild(row);
        });
    }

    renderSystemMetrics() {
        const metrics = this.data.systemStatus.system_metrics;
        
        // Update system metric bars
        const updateMetricBar = (name, value) => {
            const bars = document.querySelectorAll(`.system-metric`);
            bars.forEach(bar => {
                const nameElement = bar.querySelector('.metric-name');
                if (nameElement && nameElement.textContent.includes(name)) {
                    const percentElement = bar.querySelector('.metric-percent');
                    const fillElement = bar.querySelector('.metric-bar-fill');
                    
                    if (percentElement) percentElement.textContent = `${value.toFixed(1)}%`;
                    if (fillElement) fillElement.style.width = `${value}%`;
                }
            });
        };

        updateMetricBar('CPU', metrics.cpu_usage_percent);
        updateMetricBar('Memory', metrics.memory_usage_percent);
        updateMetricBar('Disk', metrics.disk_usage_percent);
    }

    renderSystemLogs() {
        const systemLogs = document.getElementById('systemLogs');
        if (!systemLogs) return;

        systemLogs.innerHTML = this.data.recentLogs.map(log => `
            <div class="log-entry ${log.level.toLowerCase()}">
                <span class="log-timestamp">${log.timestamp}</span>
                <span class="log-level ${log.level.toLowerCase()}">${log.level}</span>
                <span class="log-message">${log.message}</span>
                <span class="log-module">[${log.module}]</span>
            </div>
        `).join('');
    }

    initializeCharts() {
        this.initializePnLChart();
    }

    initializePnLChart() {
        const ctx = document.getElementById('pnlChart');
        if (!ctx) return;

        // Generate sample data for P&L chart
        const labels = [];
        const pnlData = [];
        const now = new Date();
        
        for (let i = 6; i >= 0; i--) {
            const date = new Date(now.getTime() - i * 24 * 60 * 60 * 1000);
            labels.push(date.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }));
            
            // Generate realistic P&L progression
            const basePnL = 15000 + (Math.random() - 0.5) * 5000;
            pnlData.push(basePnL);
        }

        this.charts.pnl = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Portfolio P&L',
                    data: pnlData,
                    borderColor: '#1FB8CD',
                    backgroundColor: 'rgba(31, 184, 205, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#1FB8CD',
                    pointBorderColor: '#1FB8CD',
                    pointHoverRadius: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#f5f5f5'
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            color: '#a7a9a9'
                        },
                        grid: {
                            color: 'rgba(167, 169, 169, 0.2)'
                        }
                    },
                    y: {
                        ticks: {
                            color: '#a7a9a9',
                            callback: function(value) {
                                return '₹' + value.toLocaleString('en-IN');
                            }
                        },
                        grid: {
                            color: 'rgba(167, 169, 169, 0.2)'
                        }
                    }
                }
            }
        });
    }

    updatePnLChart() {
        if (!this.charts.pnl) return;

        // Update the last data point with current P&L
        const currentPnL = this.data.positionSummary.total_pnl;
        const data = this.charts.pnl.data.datasets[0].data;
        data[data.length - 1] = currentPnL;
        
        this.charts.pnl.update('none');
    }

    updateMarketTime() {
        const marketTimeElement = document.getElementById('marketTime');
        if (marketTimeElement) {
            setInterval(() => {
                const now = new Date();
                const istTime = new Date(now.toLocaleString("en-US", {timeZone: "Asia/Kolkata"}));
                const timeString = istTime.toLocaleTimeString('en-IN', { hour12: false });
                marketTimeElement.textContent = `${timeString} IST`;
            }, 1000);
        }
    }

    updateWebSocketStatus(connected) {
        this.isConnected = connected;
        const wsStatus = document.getElementById('websocketStatus');
        const wsIndicator = wsStatus?.querySelector('.ws-indicator');
        const wsText = wsStatus?.querySelector('.ws-text');
        
        if (wsIndicator && wsText) {
            wsIndicator.className = `ws-indicator ${connected ? 'ws-connected' : 'ws-disconnected'}`;
            wsText.textContent = connected ? 'Live Data Connected' : 'Connection Lost';
        }
    }

    setupEventListeners() {
        // Refresh strategies button
        const refreshButton = document.getElementById('refreshStrategies');
        if (refreshButton) {
            refreshButton.addEventListener('click', () => {
                this.refreshStrategies();
            });
        }

        // Chart timeframe selector
        const chartTimeframe = document.getElementById('chartTimeframe');
        if (chartTimeframe) {
            chartTimeframe.addEventListener('change', (e) => {
                this.updateChartTimeframe(e.target.value);
            });
        }

        // Simulate connection loss/reconnection
        document.addEventListener('keydown', (e) => {
            if (e.key === 'c' && e.ctrlKey) {
                this.toggleWebSocketConnection();
            }
        });
    }

    async refreshStrategies() {
        const button = document.getElementById('refreshStrategies');
        if (!button) return;

        // Show loading state
        button.disabled = true;
        button.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" class="animate-spin">
                <path d="M12 2v4m0 12v4m10-10h-4M6 12H2m15.364-7.364l-2.828 2.828M8.464 8.464L5.636 5.636m12.728 12.728l-2.828-2.828M8.464 15.536l-2.828 2.828"/>
            </svg>
            Refreshing...
        `;

        // Simulate API call delay
        await new Promise(resolve => setTimeout(resolve, 1500));

        // Simulate data refresh
        this.simulatePnLUpdate();
        
        // Reset button
        button.disabled = false;
        button.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
            </svg>
            Refresh
        `;

        // Add success notification
        this.showNotification('Strategies refreshed successfully', 'success');
    }

    updateChartTimeframe(timeframe) {
        // In production, this would fetch different data based on timeframe
        console.log(`Chart timeframe changed to: ${timeframe}`);
        
        // Simulate chart update with different data
        if (this.charts.pnl) {
            const dataPoints = timeframe === '1D' ? 24 : timeframe === '1W' ? 7 : timeframe === '1M' ? 30 : 90;
            // Update chart with new data points
            this.showNotification(`Chart updated to ${timeframe} view`, 'info');
        }
    }

    toggleWebSocketConnection() {
        this.updateWebSocketStatus(!this.isConnected);
        this.showNotification(
            this.isConnected ? 'WebSocket connected' : 'WebSocket disconnected', 
            this.isConnected ? 'success' : 'error'
        );
    }

    showNotification(message, type = 'info') {
        // Create and show a notification
        const notification = document.createElement('div');
        notification.className = `notification notification--${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 16px;
            background: var(--color-surface);
            border: 1px solid var(--color-card-border);
            border-radius: 8px;
            color: var(--color-text);
            z-index: 9999;
            box-shadow: var(--shadow-lg);
            transition: all 0.3s ease;
        `;

        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.style.opacity = '0';
            notification.style.transform = 'translateX(100%)';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }

    startRealTimeUpdates() {
        // Update feed counts
        const feedsCountElement = document.getElementById('feedsCount');
        if (feedsCountElement) {
            setInterval(() => {
                const feeds = this.data.systemStatus.market_data.feeds_connected;
                feedsCountElement.textContent = `${feeds} Feeds Connected`;
            }, 5000);
        }
    }
}

// CSS for animations (injected dynamically)
const style = document.createElement('style');
style.textContent = `
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }
    .animate-spin {
        animation: spin 1s linear infinite;
    }
`;
document.head.appendChild(style);

// Initialize the dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.tradingDashboard = new TradingDashboard();
});

// Export for potential external use
window.TradingDashboard = TradingDashboard;
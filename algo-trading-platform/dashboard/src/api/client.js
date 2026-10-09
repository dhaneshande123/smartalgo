import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error('API Error:', error.response?.status, error.message);
    return Promise.reject(error);
  }
);

// Health
export const getHealth = () => api.get('/health');

// Market Data
export const getIndices = () => api.get('/market/indices');
export const getOptionChain = (symbol = 'NIFTY', expiry = '') =>
  api.get(`/market/option-chain/${symbol}`, { params: expiry ? { expiry } : {} });
export const getExpiries = (symbol = 'NIFTY') =>
  api.get(`/market/expiries/${symbol}`);
export const getLotSizes = () => api.get('/market/lot-sizes');
export const getMarketStatus = () => api.get('/market/status');
export const getCandles = (symbol = 'NIFTY', timeframe = 'M5', count = 100) =>
  api.get(`/market/candles/${symbol}`, { params: { timeframe, count } });
export const getOISignals = (symbol = 'NIFTY', expiry = '') =>
  api.get(`/market/oi-signals/${symbol}`, { params: expiry ? { expiry } : {} });

// Scalper (expiry / daily / momentum option buying)
export const getScalperSignal = (symbol = 'NIFTY') =>
  api.get(`/scalper/signals/${symbol}`);
export const getScalperConfig = () => api.get('/scalper/config');
export const getScalperPerformance = (profile) =>
  api.get('/scalper/performance', { params: profile ? { profile } : {} });
export const setScalperConfig = (patch) => api.post('/scalper/config', patch);
export const switchScalperProfile = (profile) =>
  api.post('/scalper/profile', { profile });
export const deployScalp = (symbol = 'NIFTY', force = false) =>
  api.post('/scalper/deploy', { symbol, force });

// Fly-High (VWAP crossover strategy)
export const getFlyHighSignal = (symbol = 'NIFTY') =>
  api.get(`/flyhigh/signal/${symbol}`);
export const getFlyHighConfig = () => api.get('/flyhigh/config');
export const setFlyHighConfig = (patch) => api.post('/flyhigh/config', patch);
export const deployFlyHigh = (symbol = 'NIFTY') =>
  api.post('/flyhigh/deploy', { symbol });
export const getFlyHighPerformance = () => api.get('/flyhigh/performance');
export const getFlyHighMissedSignals = (symbol = 'NIFTY') =>
  api.get(`/flyhigh/missed-signals/${symbol}`);
export const flyHighLateEntry = (payload) => api.post('/flyhigh/late-entry', payload);

// Greeks & IV
export const getIVSurface = (symbol = 'NIFTY') =>
  api.get(`/greeks/iv-surface/${symbol}`);
export const getPayoffData = (strategyId) =>
  api.get('/greeks/payoff', { params: { strategy_id: strategyId } });

// Portfolio
export const getPositions = () => api.get('/portfolio/positions');
export const getGreeks = () => api.get('/portfolio/greeks');
export const getPnL = () => api.get('/portfolio/pnl');
export const getMarginUsage = () => api.get('/portfolio/margin');

// Strategies
export const getStrategies = () => api.get('/strategies');
export const getStrategy = (id) => api.get(`/strategies/${id}`);
export const updateStrategyAction = (id, action) =>
  api.post(`/strategies/${id}/${action}`);
export const updateStrategyParams = (id, params) =>
  api.put(`/strategies/${id}/params`, params);
export const deployStrategy = (config) => api.post('/strategies/deploy', config);
export const saveStrategy = (config) => api.post('/strategies/save', config);
export const getDeployedStrategies = (status) =>
  api.get('/deployed-strategies', { params: status ? { status } : {} });
export const getDeployedStrategyPnL = (id) => api.get(`/strategies/${id}/pnl`);
export const stopDeployedStrategy = (id) => api.post(`/strategies/${id}/stop`);
export const clearStrategyHistory = () => api.delete('/deployed-strategies/clear-history');
export const clearAllStrategies = () => api.delete('/deployed-strategies/clear-all');
export const recalibrateEntryPrices = () => api.post('/deployed-strategies/recalibrate-entry-prices');

// Trading Mode (paper / live)
export const getTradingMode = () => api.get('/trading/mode');
export const setTradingMode = (mode, confirm = false) =>
  api.post('/trading/mode', { mode, confirm });

// Indicators + market regime
export const getIndicators = (symbol = 'NIFTY', timeframe = 'M5') =>
  api.get(`/indicators/${symbol}`, { params: { timeframe } });
export const getSupportedIndicators = () => api.get('/indicators/supported');
export const getMarketRegimeLive = (symbol = 'NIFTY') =>
  api.get(`/market/regime/${symbol}`);
export const evaluateConditions = (payload) =>
  api.post('/strategies/evaluate-conditions', payload);

// Risk
export const getRiskMetrics = () => api.get('/risk/metrics');
export const getRiskLimits = () => api.get('/risk/limits');
export const updateRiskLimits = (payload) => api.post('/risk/limits', payload);
export const getCircuitBreakers = () => api.get('/risk/circuit-breakers');
export const activateKillSwitch = (reason = 'manual') =>
  api.post('/risk/kill-switch', { reason });
export const deactivateKillSwitch = (reason = 'manual reset') =>
  api.delete('/risk/kill-switch', { data: { reason } });
export const getStressTests = () => api.get('/risk/stress-test');
export const runStressTest = (params) => api.post('/risk/stress-test', params);
export const getRiskDrawdown = () => api.get('/risk/drawdown');
export const getRiskGreeksAggregation = () => api.get('/risk/greeks-aggregation');
export const getRiskMarginCalc = () => api.get('/risk/margin-calculator');
export const getRiskBreaches = () => api.get('/risk/breaches');
export const getRiskAuditLog = (limit = 100) =>
  api.get('/risk/audit-log', { params: { limit } });

// Orders
export const getOrders = (status = '') =>
  api.get('/orders', { params: { status } });
export const getTrades = () => api.get('/orders/trades');
export const getAuditTrail = () => api.get('/orders/audit');
export const placeOrder = (order) => api.post('/orders', order);
export const cancelOrder = (id) => api.delete(`/orders/${id}`);

// Backtest
export const runBacktest = (config) => api.post('/backtest/run', {
  strategy_class: config.strategy,
  start_date: config.startDate,
  end_date: config.endDate,
  initial_capital: config.capital || 2000000,
  symbols: [config.underlying || 'NIFTY'],
});
export const getBacktestResults = (id) => api.get(`/backtest/results/${id}`);
export const getBacktestList = () => api.get('/backtest/list');

// Paper Trading
export const getPaperTradingStatus = () => api.get('/paper-trading/status');
export const startPaperTrading = (config) => api.post('/paper-trading/start', config);
export const stopPaperTrading = () => api.post('/paper-trading/stop');
export const getPaperTradingStats = () => api.get('/paper-trading/stats');
export const getPaperTradingStrategies = () => api.get('/paper-trading/strategies');
export const deployPaperStrategy = (config) => api.post('/paper-trading/deploy-strategy', config);
export const stopPaperStrategy = (id) => api.post(`/paper-trading/stop-strategy/${id}`);
export const getPaperTradingPositions = () => api.get('/paper-trading/positions');
export const getPaperTradingOrders = () => api.get('/paper-trading/orders');
export const placePaperOrder = (order) => api.post('/paper-trading/order', order);

// Market Intelligence
export const getMarketRegime = (symbol = 'NIFTY') => api.get(`/market-regime?symbol=${symbol}`);
export const getStrategySignals = (symbol = 'NIFTY') => api.get(`/strategy-signals?symbol=${symbol}`);
export const getAutoDeployRecommendations = (symbol = 'NIFTY') => api.get(`/auto-deploy/recommendations?symbol=${symbol}`);
export const executeAutoDeploy = (symbol = 'NIFTY') => api.post('/auto-deploy/execute', { symbol });
export const getAutoDeployConfig = () => api.get('/auto-deploy/config');
export const setAutoDeployConfig = (patch) => api.post('/auto-deploy/config', patch);

// Trade Analytics (SQLite-backed)
export const getTradeAnalytics = () => api.get('/trade-analytics');
export const getTradeLog = (strategyId, limit = 200) =>
  api.get('/trade-log', { params: { ...(strategyId ? { strategy_id: strategyId } : {}), limit } });
export const getPnLHistory = (strategyId, limit = 500) =>
  api.get('/pnl/history', { params: { ...(strategyId ? { strategy_id: strategyId } : {}), limit } });

// P&L Analytics
export const getPnLSummary = () => api.get('/pnl/summary');
export const getPnLByStrategy = () => api.get('/pnl/by-strategy');
export const getPnLCharges = () => api.get('/pnl/charges');
export const getPnLEquityCurve = () => api.get('/pnl/equity-curve');
export const getPnLTradeBook = () => api.get('/pnl/trade-book');

// System & Settings
export const getSystemInfo = () => api.get('/system/info');
export const getSystemConfig = () => api.get('/system/config');
export const saveFyersSettings = (creds) => api.post('/settings/fyers', creds);
export const getFyersStatus = () => api.get('/settings/fyers');
export const initFyersConnect = (creds) => api.post('/fyers/init-connect', creds, { timeout: 30000 });
export const getFyersConnectionStatus = () => api.get('/fyers/connection-status');
export const disconnectFyers = () => api.post('/fyers/disconnect');
export const reconnectFyers = () => api.post('/fyers/reconnect', {}, { timeout: 30000 });
export const getFlyHighTicker = (symbol = 'NIFTY') => api.get(`/flyhigh/ticker/${symbol}`);

// Account / Fyers live data
export const getFunds = () => api.get('/account/funds');
export const getHoldings = () => api.get('/account/holdings');
export const getMarketDepth = (symbol) => api.get(`/market/depth/${symbol}`);

// VectorBT Backtesting
export const getVbtStrategies = () => api.get('/vbt/strategies');
export const runVbtBacktest = (config) => api.post('/vbt/backtest', config);
export const runVbtOptimize = (config) => api.post('/vbt/optimize', config);
export const getVbtObjectives = () => api.get('/vbt/objectives');
export const getVbtReport = (body) => api.post('/vbt/report', body);
export const compareVbtResults = (results) => api.post('/vbt/compare', { results });
export const getAvailableIndicators = () => api.get('/indicators/available');

// Monitoring
export const getMonitoringHealth = () => api.get('/monitoring/health');
export const getAlerts = () => api.get('/monitoring/alerts');
export const acknowledgeAlert = (id) =>
  api.post(`/monitoring/alerts/${id}/acknowledge`);
export const getPerformanceMetrics = () => api.get('/monitoring/performance');
export const getSystemResources = () => api.get('/monitoring/resources');

export default api;

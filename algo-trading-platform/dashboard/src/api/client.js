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
export const getDeployedStrategies = () => api.get('/deployed-strategies');
export const getDeployedStrategyPnL = (id) => api.get(`/strategies/${id}/pnl`);
export const stopDeployedStrategy = (id) => api.post(`/strategies/${id}/stop`);

// Trading Mode (paper / live)
export const getTradingMode = () => api.get('/trading/mode');
export const setTradingMode = (mode, confirm = false) =>
  api.post('/trading/mode', { mode, confirm });

// Risk
export const getRiskMetrics = () => api.get('/risk/metrics');
export const getRiskLimits = () => api.get('/risk/limits');
export const getCircuitBreakers = () => api.get('/risk/circuit-breakers');
export const activateKillSwitch = () => api.post('/risk/kill-switch');
export const getStressTests = () => api.get('/risk/stress-tests');
export const runStressTest = (params) => api.post('/risk/stress-tests', params);

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
export const getMarketRegime = () => api.get('/market-regime');
export const getStrategySignals = () => api.get('/strategy-signals');
export const getAutoDeployRecommendations = () => api.get('/auto-deploy/recommendations');
export const executeAutoDeploy = () => api.post('/auto-deploy/execute');

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

// Account / Fyers live data
export const getFunds = () => api.get('/account/funds');
export const getHoldings = () => api.get('/account/holdings');
export const getMarketDepth = (symbol) => api.get(`/market/depth/${symbol}`);

// Monitoring
export const getMonitoringHealth = () => api.get('/monitoring/health');
export const getAlerts = () => api.get('/monitoring/alerts');
export const acknowledgeAlert = (id) =>
  api.post(`/monitoring/alerts/${id}/acknowledge`);
export const getPerformanceMetrics = () => api.get('/monitoring/performance');
export const getSystemResources = () => api.get('/monitoring/resources');

export default api;

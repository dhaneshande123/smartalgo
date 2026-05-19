import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/client';

export const useHealth = () => useQuery({ queryKey: ['health'], queryFn: api.getHealth });
export const useIndices = () => useQuery({ queryKey: ['indices'], queryFn: api.getIndices, refetchInterval: 500 });
export const useMarketStatus = () => useQuery({ queryKey: ['marketStatus'], queryFn: api.getMarketStatus });
export const useOptionChain = (symbol, expiry) =>
  useQuery({ queryKey: ['optionChain', symbol, expiry], queryFn: () => api.getOptionChain(symbol, expiry), refetchInterval: 500 });
export const useExpiries = (symbol) =>
  useQuery({ queryKey: ['expiries', symbol], queryFn: () => api.getExpiries(symbol) });
export const useLotSizes = () =>
  useQuery({ queryKey: ['lotSizes'], queryFn: api.getLotSizes, staleTime: 60_000, refetchInterval: 60_000 });
export const usePositions = () => useQuery({ queryKey: ['positions'], queryFn: api.getPositions });
export const useGreeks = () => useQuery({ queryKey: ['greeks'], queryFn: api.getGreeks });
export const usePnL = () => useQuery({ queryKey: ['pnl'], queryFn: api.getPnL });
export const useMarginUsage = () => useQuery({ queryKey: ['margin'], queryFn: api.getMarginUsage });
export const useStrategies = () => useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies });
export const useRiskMetrics = () => useQuery({ queryKey: ['riskMetrics'], queryFn: api.getRiskMetrics });
export const useRiskLimits = () => useQuery({ queryKey: ['riskLimits'], queryFn: api.getRiskLimits });
export const useCircuitBreakers = () => useQuery({ queryKey: ['circuitBreakers'], queryFn: api.getCircuitBreakers });
export const useOrders = (status) =>
  useQuery({ queryKey: ['orders', status], queryFn: () => api.getOrders(status) });
export const useTrades = () => useQuery({ queryKey: ['trades'], queryFn: api.getTrades });
export const useAuditTrail = () => useQuery({ queryKey: ['audit'], queryFn: api.getAuditTrail });
export const useBacktestList = () => useQuery({ queryKey: ['backtests'], queryFn: api.getBacktestList });
export const useMonitoringHealth = () => useQuery({ queryKey: ['monitoringHealth'], queryFn: api.getMonitoringHealth });
export const useAlerts = () => useQuery({ queryKey: ['alerts'], queryFn: api.getAlerts });
export const usePerformanceMetrics = () => useQuery({ queryKey: ['perfMetrics'], queryFn: api.getPerformanceMetrics });
export const useSystemResources = () => useQuery({ queryKey: ['sysResources'], queryFn: api.getSystemResources });
export const useStressTests = () => useQuery({ queryKey: ['stressTests'], queryFn: api.getStressTests });

export const useKillSwitch = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.activateKillSwitch, onSuccess: () => qc.invalidateQueries() });
};

export const useStrategyAction = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }) => api.updateStrategyAction(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
};

export const useRunBacktest = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.runBacktest,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }),
  });
};

export const useAcknowledgeAlert = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.acknowledgeAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
};

// System & Settings
export const useSystemInfo = () => useQuery({ queryKey: ['systemInfo'], queryFn: api.getSystemInfo });
export const useSystemConfig = () => useQuery({ queryKey: ['systemConfig'], queryFn: api.getSystemConfig });

// P&L Analytics
export const usePnLSummary = () => useQuery({ queryKey: ['pnlSummary'], queryFn: api.getPnLSummary, refetchInterval: 5000 });
export const usePnLByStrategy = () => useQuery({ queryKey: ['pnlByStrategy'], queryFn: api.getPnLByStrategy, refetchInterval: 5000 });
export const usePnLCharges = () => useQuery({ queryKey: ['pnlCharges'], queryFn: api.getPnLCharges, refetchInterval: 10000 });
export const usePnLEquityCurve = () => useQuery({ queryKey: ['pnlEquityCurve'], queryFn: api.getPnLEquityCurve, refetchInterval: 5000 });
export const usePnLTradeBook = () => useQuery({ queryKey: ['pnlTradeBook'], queryFn: api.getPnLTradeBook, refetchInterval: 5000 });

// Market Intelligence
export const useMarketRegime = () =>
  useQuery({ queryKey: ['marketRegime'], queryFn: api.getMarketRegime, refetchInterval: 5000 });
export const useStrategySignals = () =>
  useQuery({ queryKey: ['strategySignals'], queryFn: api.getStrategySignals, refetchInterval: 5000 });
export const useAutoDeployRecommendations = () =>
  useQuery({ queryKey: ['autoDeployRecs'], queryFn: api.getAutoDeployRecommendations, refetchInterval: 10000 });
export const useExecuteAutoDeploy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.executeAutoDeploy,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['paperStrategies'] }); qc.invalidateQueries({ queryKey: ['autoDeployRecs'] }); },
  });
};

// Paper Trading
export const usePaperTradingStatus = () =>
  useQuery({ queryKey: ['paperStatus'], queryFn: api.getPaperTradingStatus, refetchInterval: 3000 });
export const usePaperTradingStats = (enabled = true) =>
  useQuery({ queryKey: ['paperStats'], queryFn: api.getPaperTradingStats, refetchInterval: 3000, retry: false, enabled });
export const usePaperTradingStrategies = (enabled = true) =>
  useQuery({ queryKey: ['paperStrategies'], queryFn: api.getPaperTradingStrategies, refetchInterval: 3000, enabled });

export const usePaperTradingPositions = (enabled = true) =>
  useQuery({ queryKey: ['paperPositions'], queryFn: api.getPaperTradingPositions, refetchInterval: 3000, retry: false, enabled });
export const usePaperTradingOrders = (enabled = true) =>
  useQuery({ queryKey: ['paperOrders'], queryFn: api.getPaperTradingOrders, refetchInterval: 3000, retry: false, enabled });

export const useStartPaperTrading = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startPaperTrading,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['paperStatus'] }); qc.invalidateQueries({ queryKey: ['paperStats'] }); },
  });
};
export const useStopPaperTrading = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.stopPaperTrading,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['paperStatus'] }); qc.invalidateQueries({ queryKey: ['paperStats'] }); },
  });
};
export const useDeployPaperStrategy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deployPaperStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['paperStrategies'] }),
  });
};
export const useStopPaperStrategy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.stopPaperStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['paperStrategies'] }),
  });
};
export const usePlacePaperOrder = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.placePaperOrder,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['paperOrders'] });
      qc.invalidateQueries({ queryKey: ['paperPositions'] });
      qc.invalidateQueries({ queryKey: ['paperStats'] });
    },
  });
};

// Candles & Charts
export const useCandles = (symbol, timeframe, count) =>
  useQuery({
    queryKey: ['candles', symbol, timeframe, count],
    queryFn: () => api.getCandles(symbol, timeframe, count),
    refetchInterval: timeframe === 'M1' ? 5000 : timeframe === 'M5' ? 15000 : 30000,
  });

// Greeks & IV Surface
export const useIVSurface = (symbol) =>
  useQuery({ queryKey: ['ivSurface', symbol], queryFn: () => api.getIVSurface(symbol), refetchInterval: 10000 });

// Backtest results polling
export const useBacktestResults = (jobId) =>
  useQuery({
    queryKey: ['backtestResults', jobId],
    queryFn: () => api.getBacktestResults(jobId),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query?.state?.data;
      return data?.status === 'running' ? 1500 : false;
    },
  });

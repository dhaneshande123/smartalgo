import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as api from '../api/client';

export const useHealth = () => useQuery({ queryKey: ['health'], queryFn: api.getHealth });
export const useIndices = () => useQuery({ queryKey: ['indices'], queryFn: api.getIndices, refetchInterval: 3000 });
export const useMarketStatus = () => useQuery({ queryKey: ['marketStatus'], queryFn: api.getMarketStatus });
export const useOptionChain = (symbol, expiry) =>
  useQuery({ queryKey: ['optionChain', symbol, expiry], queryFn: () => api.getOptionChain(symbol, expiry), refetchInterval: 3000 });
export const useOISignals = (symbol, expiry) =>
  useQuery({ queryKey: ['oiSignals', symbol, expiry], queryFn: () => api.getOISignals(symbol, expiry), refetchInterval: 5000 });

// Scalper (expiry / daily / momentum option buying)
export const useScalperSignal = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['scalperSignal', symbol], queryFn: () => api.getScalperSignal(symbol), refetchInterval: 5000 });
export const useScalperConfig = () =>
  useQuery({ queryKey: ['scalperConfig'], queryFn: api.getScalperConfig });
export const useScalperPerformance = () =>
  useQuery({ queryKey: ['scalperPerformance'], queryFn: () => api.getScalperPerformance(), refetchInterval: 5000 });
export const useSetScalperConfig = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.setScalperConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scalperConfig'] }),
  });
};
export const useSwitchScalperProfile = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profile) => api.switchScalperProfile(profile),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scalperConfig'] });
      qc.invalidateQueries({ queryKey: ['scalperSignal'] });
    },
  });
};
export const useDeployScalp = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ symbol, force }) => api.deployScalp(symbol, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['deployedStrategies'] });
      qc.invalidateQueries({ queryKey: ['paperStrategies'] });
    },
  });
};
// Fly-High (VWAP crossover strategy)
export const useFlyHighSignal = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['flyhighSignal', symbol], queryFn: () => api.getFlyHighSignal(symbol), refetchInterval: 15000 });
export const useFlyHighConfig = () =>
  useQuery({ queryKey: ['flyhighConfig'], queryFn: api.getFlyHighConfig });
export const useFlyHighPerformance = () =>
  useQuery({ queryKey: ['flyhighPerformance'], queryFn: api.getFlyHighPerformance, refetchInterval: 5000 });
export const useFlyHighTicker = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['flyhighTicker', symbol], queryFn: () => api.getFlyHighTicker(symbol), refetchInterval: 1000 });
export const useSetFlyHighConfig = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.setFlyHighConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['flyhighConfig'] }),
  });
};
export const useDeployFlyHigh = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ symbol }) => api.deployFlyHigh(symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['deployedStrategies'] });
      qc.invalidateQueries({ queryKey: ['paperStrategies'] });
      qc.invalidateQueries({ queryKey: ['flyhighPerformance'] });
      qc.invalidateQueries({ queryKey: ['flyhighSignal'] });
      qc.invalidateQueries({ queryKey: ['flyhighMissed'] });
    },
  });
};
export const useFlyHighMissedSignals = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['flyhighMissed', symbol], queryFn: () => api.getFlyHighMissedSignals(symbol), refetchInterval: 30000 });
export const useFlyHighLateEntry = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload) => api.flyHighLateEntry(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['deployedStrategies'] });
      qc.invalidateQueries({ queryKey: ['paperStrategies'] });
      qc.invalidateQueries({ queryKey: ['flyhighPerformance'] });
      qc.invalidateQueries({ queryKey: ['flyhighSignal'] });
      qc.invalidateQueries({ queryKey: ['flyhighMissed'] });
    },
  });
};

export const useExpiries = (symbol) =>
  useQuery({ queryKey: ['expiries', symbol], queryFn: () => api.getExpiries(symbol) });
export const useLotSizes = () =>
  useQuery({ queryKey: ['lotSizes'], queryFn: api.getLotSizes, staleTime: 60_000, refetchInterval: 60_000 });
export const usePositions = () => useQuery({ queryKey: ['positions'], queryFn: api.getPositions, refetchInterval: 5000 });
export const useGreeks = () => useQuery({ queryKey: ['greeks'], queryFn: api.getGreeks, refetchInterval: 5000 });
export const usePnL = () => useQuery({ queryKey: ['pnl'], queryFn: api.getPnL, refetchInterval: 5000 });
export const useMarginUsage = () => useQuery({ queryKey: ['margin'], queryFn: api.getMarginUsage });
export const useStrategies = () => useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies });
export const useRiskMetrics = () => useQuery({ queryKey: ['riskMetrics'], queryFn: api.getRiskMetrics, refetchInterval: 3000 });
export const useRiskLimits = () => useQuery({ queryKey: ['riskLimits'], queryFn: api.getRiskLimits, refetchInterval: 10000 });
export const useCircuitBreakers = () => useQuery({ queryKey: ['circuitBreakers'], queryFn: api.getCircuitBreakers, refetchInterval: 5000 });
export const useOrders = (status) =>
  useQuery({ queryKey: ['orders', status], queryFn: () => api.getOrders(status) });
export const useTrades = () => useQuery({ queryKey: ['trades'], queryFn: api.getTrades });
export const useAuditTrail = () => useQuery({ queryKey: ['audit'], queryFn: api.getAuditTrail });
export const useBacktestList = () => useQuery({ queryKey: ['backtests'], queryFn: api.getBacktestList });
export const useMonitoringHealth = () => useQuery({ queryKey: ['monitoringHealth'], queryFn: api.getMonitoringHealth });
export const useAlerts = () => useQuery({ queryKey: ['alerts'], queryFn: api.getAlerts });
export const usePerformanceMetrics = () => useQuery({ queryKey: ['perfMetrics'], queryFn: api.getPerformanceMetrics });
export const useSystemResources = () => useQuery({ queryKey: ['sysResources'], queryFn: api.getSystemResources });
export const useStressTests = () => useQuery({ queryKey: ['stressTests'], queryFn: api.getStressTests, refetchInterval: 5000 });

export const useKillSwitch = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reason) => api.activateKillSwitch(reason || 'manual'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['riskMetrics'] });
      qc.invalidateQueries({ queryKey: ['circuitBreakers'] });
      qc.invalidateQueries({ queryKey: ['riskBreaches'] });
      qc.invalidateQueries({ queryKey: ['deployedStrategies'] });
    },
  });
};

export const useDeactivateKillSwitch = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reason) => api.deactivateKillSwitch(reason || 'manual reset'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['riskMetrics'] });
      qc.invalidateQueries({ queryKey: ['circuitBreakers'] });
      qc.invalidateQueries({ queryKey: ['riskBreaches'] });
    },
  });
};

// Risk Engine — additional live data
export const useRiskBreaches = () =>
  useQuery({ queryKey: ['riskBreaches'], queryFn: api.getRiskBreaches, refetchInterval: 5000 });
export const useRiskDrawdown = () =>
  useQuery({ queryKey: ['riskDrawdown'], queryFn: api.getRiskDrawdown, refetchInterval: 10000 });
export const useRiskGreeksAggregation = () =>
  useQuery({ queryKey: ['riskGreeksAgg'], queryFn: api.getRiskGreeksAggregation, refetchInterval: 5000 });
export const useRiskMargin = () =>
  useQuery({ queryKey: ['riskMargin'], queryFn: api.getRiskMarginCalc, refetchInterval: 5000 });
export const useRiskAuditLog = (limit = 50) =>
  useQuery({ queryKey: ['riskAudit', limit], queryFn: () => api.getRiskAuditLog(limit), refetchInterval: 10000 });

export const useUpdateRiskLimits = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.updateRiskLimits,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['riskLimits'] });
      qc.invalidateQueries({ queryKey: ['riskMetrics'] });
      qc.invalidateQueries({ queryKey: ['riskBreaches'] });
    },
  });
};

export const useStrategyAction = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }) => api.updateStrategyAction(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
};

export const usePlaceOrder = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.placeOrder,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['orders'] }); qc.invalidateQueries({ queryKey: ['positions'] }); },
  });
};

export const useCancelOrder = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.cancelOrder,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['orders'] }),
  });
};

export const useDeployStrategy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deployStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
};

export const useSaveStrategy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.saveStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
};

export const useSaveFyersSettings = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.saveFyersSettings,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['health'] }),
  });
};

export const useFyersStatus = () =>
  useQuery({ queryKey: ['fyersStatus'], queryFn: api.getFyersStatus, refetchInterval: 5000 });

export const useInitFyersConnect = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.initFyersConnect,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fyersConnectionStatus'] }),
  });
};

export const useFyersConnectionStatus = (enabled = false) =>
  useQuery({
    queryKey: ['fyersConnectionStatus'],
    queryFn: api.getFyersConnectionStatus,
    refetchInterval: enabled ? 2000 : false,
    enabled,
  });

export const useDisconnectFyers = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.disconnectFyers,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fyersStatus'] });
      qc.invalidateQueries({ queryKey: ['fyersConnectionStatus'] });
      qc.invalidateQueries({ queryKey: ['health'] });
    },
  });
};
export const useReconnectFyers = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.reconnectFyers,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fyersStatus'] });
      qc.invalidateQueries({ queryKey: ['fyersConnectionStatus'] });
      qc.invalidateQueries({ queryKey: ['health'] });
    },
  });
};

// Trading mode (paper / live)
export const useTradingMode = () =>
  useQuery({ queryKey: ['tradingMode'], queryFn: api.getTradingMode, refetchInterval: 5000 });

export const useSetTradingMode = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mode, confirm }) => api.setTradingMode(mode, confirm),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tradingMode'] });
      qc.invalidateQueries({ queryKey: ['health'] });
    },
  });
};

// Deployed strategies (live + paper) with live P&L
export const useDeployedStrategies = () =>
  useQuery({
    queryKey: ['deployedStrategies'],
    queryFn: () => api.getDeployedStrategies(),
    refetchInterval: 3000,
  });

export const useDeployedStrategyPnL = (id) =>
  useQuery({
    queryKey: ['deployedStrategyPnL', id],
    queryFn: () => api.getDeployedStrategyPnL(id),
    enabled: !!id,
    refetchInterval: 3000,
  });

export const useStopDeployedStrategy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.stopDeployedStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['deployedStrategies'] }),
  });
};

export const useClearStrategyHistory = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.clearStrategyHistory,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['deployedStrategies'] }),
  });
};

// Indicators + market regime (for entry-condition UI + live previews)
export const useIndicators = (symbol = 'NIFTY', timeframe = 'M5') =>
  useQuery({
    queryKey: ['indicators', symbol, timeframe],
    queryFn: () => api.getIndicators(symbol, timeframe),
    enabled: !!symbol,
    refetchInterval: 5000,
  });

export const useSupportedIndicators = () =>
  useQuery({
    queryKey: ['supportedIndicators'],
    queryFn: api.getSupportedIndicators,
    staleTime: 600_000,
  });

export const useLiveMarketRegime = (symbol = 'NIFTY') =>
  useQuery({
    queryKey: ['liveMarketRegime', symbol],
    queryFn: () => api.getMarketRegimeLive(symbol),
    enabled: !!symbol,
    refetchInterval: 10000,
  });

export const useEvaluateConditions = () =>
  useMutation({ mutationFn: api.evaluateConditions });

// Account / Fyers live data
export const useFunds = () => useQuery({ queryKey: ['funds'], queryFn: api.getFunds, refetchInterval: 10000 });
export const useHoldings = () => useQuery({ queryKey: ['holdings'], queryFn: api.getHoldings, refetchInterval: 30000 });
export const useMarketDepth = (symbol) =>
  useQuery({ queryKey: ['marketDepth', symbol], queryFn: () => api.getMarketDepth(symbol), enabled: !!symbol, refetchInterval: 5000 });

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

// Trade Analytics (SQLite-backed)
export const useTradeAnalytics = () =>
  useQuery({ queryKey: ['tradeAnalytics'], queryFn: api.getTradeAnalytics, refetchInterval: 5000 });
export const useTradeLog = (strategyId) =>
  useQuery({ queryKey: ['tradeLog', strategyId], queryFn: () => api.getTradeLog(strategyId), refetchInterval: 5000 });
export const usePnLHistoryPersisted = (strategyId) =>
  useQuery({ queryKey: ['pnlHistoryPersisted', strategyId], queryFn: () => api.getPnLHistory(strategyId), refetchInterval: 5000 });

// P&L Analytics
export const usePnLSummary = () => useQuery({ queryKey: ['pnlSummary'], queryFn: api.getPnLSummary, refetchInterval: 5000 });
export const usePnLByStrategy = () => useQuery({ queryKey: ['pnlByStrategy'], queryFn: api.getPnLByStrategy, refetchInterval: 5000 });
export const usePnLCharges = () => useQuery({ queryKey: ['pnlCharges'], queryFn: api.getPnLCharges, refetchInterval: 10000 });
export const usePnLEquityCurve = () => useQuery({ queryKey: ['pnlEquityCurve'], queryFn: api.getPnLEquityCurve, refetchInterval: 5000 });
export const usePnLTradeBook = () => useQuery({ queryKey: ['pnlTradeBook'], queryFn: api.getPnLTradeBook, refetchInterval: 5000 });

// Market Intelligence
export const useMarketRegime = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['marketRegime', symbol], queryFn: () => api.getMarketRegime(symbol), refetchInterval: 5000 });
export const useStrategySignals = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['strategySignals', symbol], queryFn: () => api.getStrategySignals(symbol), refetchInterval: 5000 });
export const useAutoDeployRecommendations = (symbol = 'NIFTY') =>
  useQuery({ queryKey: ['autoDeployRecs', symbol], queryFn: () => api.getAutoDeployRecommendations(symbol), refetchInterval: 10000 });
export const useExecuteAutoDeploy = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.executeAutoDeploy,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['paperStrategies'] }); qc.invalidateQueries({ queryKey: ['autoDeployRecs'] }); },
  });
};
export const useAutoDeployConfig = () =>
  useQuery({ queryKey: ['autoDeployConfig'], queryFn: api.getAutoDeployConfig, refetchInterval: 5000 });
export const useSetAutoDeployConfig = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.setAutoDeployConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['autoDeployConfig'] }),
  });
};

// Paper Trading
export const usePaperTradingStatus = () =>
  useQuery({ queryKey: ['paperStatus'], queryFn: api.getPaperTradingStatus, refetchInterval: 5000 });
export const usePaperTradingStats = (enabled = true) =>
  useQuery({ queryKey: ['paperStats'], queryFn: api.getPaperTradingStats, refetchInterval: 5000, retry: false, enabled });
export const usePaperTradingStrategies = (enabled = true) =>
  useQuery({ queryKey: ['paperStrategies'], queryFn: api.getPaperTradingStrategies, refetchInterval: 5000, enabled });

export const usePaperTradingPositions = (enabled = true) =>
  useQuery({ queryKey: ['paperPositions'], queryFn: api.getPaperTradingPositions, refetchInterval: 5000, retry: 1, enabled });
export const usePaperTradingOrders = (enabled = true) =>
  useQuery({ queryKey: ['paperOrders'], queryFn: api.getPaperTradingOrders, refetchInterval: 5000, retry: 1, enabled });

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
    refetchInterval: timeframe === 'M1' ? 3000 : timeframe === 'M5' ? 5000 : 10000,
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

// VectorBT Backtesting
export const useVbtStrategies = () =>
  useQuery({ queryKey: ['vbtStrategies'], queryFn: api.getVbtStrategies, staleTime: 60_000 });

export const useVbtObjectives = () =>
  useQuery({ queryKey: ['vbtObjectives'], queryFn: api.getVbtObjectives, staleTime: 60_000 });

export const useRunVbtBacktest = () => {
  return useMutation({ mutationFn: api.runVbtBacktest });
};

export const useRunVbtOptimize = () => {
  return useMutation({ mutationFn: api.runVbtOptimize });
};

export const useVbtReport = () => {
  return useMutation({ mutationFn: api.getVbtReport });
};

export const useCompareVbtResults = () => {
  return useMutation({ mutationFn: api.compareVbtResults });
};

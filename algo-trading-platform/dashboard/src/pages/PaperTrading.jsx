import { useState } from 'react';
import {
  Play, Square, Rocket, Power, TrendingUp, DollarSign,
  BarChart3, Trophy, Target, Zap, X,
  Pause, Activity, Layers,
} from 'lucide-react';
import Card from '../components/common/Card';
import MetricCard from '../components/common/MetricCard';
import DataTable from '../components/common/DataTable';
import StatusBadge from '../components/common/StatusBadge';
import {
  usePaperTradingStatus, usePaperTradingStats, usePaperTradingStrategies,
  usePaperTradingPositions, usePaperTradingOrders,
  useStartPaperTrading, useStopPaperTrading,
  useDeployPaperStrategy, useStopPaperStrategy,
  usePlacePaperOrder, useClearStrategyHistory,
  useStopDeployedStrategy,
} from '../hooks/useApi';
import { useToast } from '../components/common/ToastProvider';
import DeployedStrategiesPnL from '../components/common/DeployedStrategiesPnL';

// ── Available strategies for deployment ─────────────────────────────
const STRATEGY_OPTIONS = [
  { value: 'iron_condor', label: 'Iron Condor', desc: 'Sell OTM call + put spreads', category: 'Hedged' },
  { value: 'straddle', label: 'Straddle Seller', desc: 'Sell ATM straddle', category: 'Option Selling' },
  { value: 'strangle', label: 'Strangle Seller', desc: 'Sell OTM strangle', category: 'Option Selling' },
  { value: 'momentum', label: 'Momentum Breakout', desc: 'Trend-following breakout', category: 'Trend' },
  { value: 'mean_reversion', label: 'Mean Reversion', desc: 'Buy dips, sell rallies', category: 'Intraday' },
  { value: 'supertrend', label: 'Supertrend', desc: 'ATR-based trend strategy', category: 'Trend' },
  { value: 'gamma_scalping', label: 'Gamma Scalping', desc: 'Delta-neutral gamma trading', category: 'Hedged' },
  { value: 'vwap_scalper', label: 'VWAP Scalper', desc: 'Volume-weighted mean reversion', category: 'Intraday' },
  { value: 'orb_options', label: 'ORB Options', desc: 'Opening range breakout', category: 'Intraday' },
  { value: 'expiry_day', label: 'Expiry Day', desc: 'Theta decay on expiry', category: 'Option Selling' },
  { value: 'bull_call_spread', label: 'Bull Call Spread', desc: 'Directional bullish spread', category: 'Hedged' },
  { value: 'calendar_spread', label: 'Calendar Spread', desc: 'Time spread across expiries', category: 'Hedged' },
  { value: 'pair_trading', label: 'Pair Trading', desc: 'Relative value arbitrage', category: 'Hedged' },
];

// ── Helpers ──────────────────────────────────────────────────────────
function formatINR(val) {
  if (val == null) return '--';
  const abs = Math.abs(val);
  if (abs >= 10000000) return `${(val / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `${(val / 100000).toFixed(2)} L`;
  return val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

// ── Position columns (base — exit button added dynamically inside component) ──
const basePositionColumns = [
  { key: 'symbol', label: 'Symbol', render: (v) => <span className="font-medium text-white">{v}</span> },
  { key: 'side', label: 'Side', render: (v) => <span className={`font-medium ${v === 'BUY' || v === 'LONG' ? 'text-profit' : 'text-loss'}`}>{v || '--'}</span> },
  { key: 'quantity', label: 'Qty', align: 'right', render: (v) => <span className="font-mono">{v}</span> },
  { key: 'avg_price', label: 'Avg Price', align: 'right', render: (v) => <span className="font-mono">{v?.toFixed?.(2) ?? v ?? '--'}</span> },
  { key: 'ltp', label: 'LTP', align: 'right', render: (v) => <span className="font-mono">{v?.toFixed?.(2) ?? v ?? '--'}</span> },
  {
    key: 'pnl', label: 'P&L', align: 'right',
    render: (v) => <span className={`font-mono font-medium ${(v ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>{(v ?? 0) >= 0 ? '+' : ''}{v?.toFixed?.(2) ?? v ?? '--'}</span>,
  },
  { key: 'strategy', label: 'Strategy' },
];

const orderColumns = [
  { key: 'order_id', label: 'ID', render: (v) => <span className="font-mono text-blue-400 text-xs">{v?.slice?.(0, 12) ?? v}</span> },
  { key: 'symbol', label: 'Symbol', render: (v) => <span className="font-medium text-white">{v}</span> },
  { key: 'side', label: 'Side', render: (v) => <span className={`font-medium ${v === 'BUY' ? 'text-profit' : 'text-loss'}`}>{v}</span> },
  { key: 'quantity', label: 'Qty', align: 'right' },
  { key: 'price', label: 'Price', align: 'right', render: (v) => <span className="font-mono">{v?.toFixed?.(2) ?? v}</span> },
  { key: 'status', label: 'Status', render: (v) => <StatusBadge status={v?.toLowerCase?.()} /> },
  { key: 'timestamp', label: 'Time', render: (v) => <span className="text-xs text-slate-500">{v ? new Date(v).toLocaleTimeString() : '--'}</span> },
];

// ════════════════════════════════════════════════════════════════════
// Helper: clear-history button for the deployed-strategies card
// ════════════════════════════════════════════════════════════════════
function PaperClearHistoryButton() {
  const clearHistory = useClearStrategyHistory();
  const toast = useToast();

  const handleClick = async () => {
    try {
      const result = await clearHistory.mutateAsync();
      toast?.addToast?.({
        level: 'success',
        message: `Removed ${result.removed_count} stopped/exited strategies`,
        source: 'paper-trading',
      });
    } catch (e) {
      toast?.addToast?.({
        level: 'CRITICAL',
        message: `Clear failed: ${e?.response?.data?.detail || e.message}`,
        source: 'paper-trading',
      });
    }
  };

  return (
    <div className="mt-3 pt-3 border-t border-slate-700/30 flex justify-end">
      <button
        onClick={handleClick}
        disabled={clearHistory.isPending}
        className="text-[11px] font-medium text-slate-500 hover:text-slate-300 transition-colors disabled:opacity-40"
      >
        {clearHistory.isPending ? 'Clearing…' : 'Clear stopped strategies'}
      </button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main Component
// ════════════════════════════════════════════════════════════════════
export default function PaperTrading() {
  const { data: statusData } = usePaperTradingStatus();
  const isActive = statusData?.active || false;
  const feedMode = statusData?.feed_mode || 'mock';

  const { data: statsData } = usePaperTradingStats(isActive);
  const { data: strategiesData } = usePaperTradingStrategies(isActive);
  const { data: positionsData } = usePaperTradingPositions(isActive);
  const { data: ordersData } = usePaperTradingOrders(isActive);

  const startMutation = useStartPaperTrading();
  const stopMutation = useStopPaperTrading();
  const deployMutation = useDeployPaperStrategy();
  const stopStrategyMutation = useStopPaperStrategy();
  const stopDeployedMutation = useStopDeployedStrategy();
  const exitOrderMutation = usePlacePaperOrder();

  const toast = useToast();

  const [deployOpen, setDeployOpen] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState('');
  const [activeTab, setActiveTab] = useState('strategies');
  const [strategyFilter, setStrategyFilter] = useState('running'); // 'running' | 'all'

  // ── Parse data ──
  const stats = statsData || {};
  const rawStrategies = strategiesData?.strategies || [];
  const positions = positionsData?.positions || positionsData || [];
  const orders = ordersData?.orders || ordersData || [];

  // Sort strategies: RUNNING first, then by P&L descending. Entered before waiting.
  const sortedStrategies = [...rawStrategies].sort((a, b) => {
    const aRunning = a.status === 'RUNNING' ? 1 : 0;
    const bRunning = b.status === 'RUNNING' ? 1 : 0;
    if (aRunning !== bRunning) return bRunning - aRunning;
    const aEntered = a.entered ? 1 : 0;
    const bEntered = b.entered ? 1 : 0;
    if (aEntered !== bEntered) return bEntered - aEntered;
    return Math.abs(b.pnl || 0) - Math.abs(a.pnl || 0);
  });
  const runningCount = sortedStrategies.filter((s) => s.status === 'RUNNING').length;
  const strategies = strategyFilter === 'running'
    ? sortedStrategies.filter((s) => s.status === 'RUNNING')
    : sortedStrategies;

  // ── Handlers ──
  const handleStart = () => {
    startMutation.mutate({ initial_capital: 2000000 }, {
      onSuccess: () => toast?.addToast({ level: 'success', message: 'Paper trading session started', source: 'paper_trading' }),
      onError: (err) => toast?.addToast({ level: 'WARNING', message: err?.response?.data?.detail || 'Failed to start session', source: 'paper_trading' }),
    });
  };

  const handleStop = () => {
    stopMutation.mutate(undefined, {
      onSuccess: () => toast?.addToast({ level: 'INFO', message: 'Paper trading session stopped', source: 'paper_trading' }),
      onError: (err) => toast?.addToast({ level: 'WARNING', message: err?.response?.data?.detail || 'Failed to stop session', source: 'paper_trading' }),
    });
  };

  const handleDeploy = () => {
    if (!selectedStrategy) return;
    deployMutation.mutate({ strategy_class: selectedStrategy }, {
      onSuccess: () => {
        toast?.addToast({ level: 'success', message: `Deployed ${selectedStrategy.replace(/_/g, ' ')} in paper mode`, source: 'paper_trading' });
        setSelectedStrategy('');
        setDeployOpen(false);
      },
      onError: (err) => toast?.addToast({ level: 'WARNING', message: err?.response?.data?.detail || 'Deploy failed', source: 'paper_trading' }),
    });
  };

  const handleStopStrategy = (strategyId, isAiDeployed) => {
    const mutation = isAiDeployed ? stopDeployedMutation : stopStrategyMutation;
    mutation.mutate(strategyId, {
      onSuccess: () => toast?.addToast({ level: 'INFO', message: `Stopped strategy ${strategyId}`, source: 'paper_trading' }),
      onError: (err) => toast?.addToast({ level: 'WARNING', message: err?.response?.data?.detail || 'Stop failed', source: 'paper_trading' }),
    });
  };

  // Exit / close a paper position by placing a counter-order
  const handleExitPosition = (pos) => {
    const exitSide = pos.side === 'BUY' ? 'SELL' : 'BUY';
    exitOrderMutation.mutate(
      { symbol: pos.symbol, side: exitSide, quantity: pos.quantity, ltp: pos.ltp },
      {
        onSuccess: () => toast?.addToast({ level: 'success', message: `Exited ${pos.symbol} (${exitSide} ${pos.quantity})`, source: 'paper_trading' }),
        onError: (err) => toast?.addToast({ level: 'WARNING', message: err?.response?.data?.detail || 'Exit failed', source: 'paper_trading' }),
      },
    );
  };

  // Build position columns with exit button
  const positionColumns = [
    ...basePositionColumns,
    {
      key: '_exit', label: 'Action', align: 'center',
      render: (_v, row) => (
        <button
          onClick={() => handleExitPosition(row)}
          disabled={exitOrderMutation.isPending}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-loss/10 border border-loss/20 text-loss text-[11px] font-semibold hover:bg-loss/20 transition-all disabled:opacity-40"
        >
          <X className="w-3 h-3" />
          Exit
        </button>
      ),
    },
  ];

  const tabs = [
    { id: 'strategies', label: 'Deployed Strategies' },
    { id: 'positions', label: 'Positions' },
    { id: 'orders', label: 'Order History' },
  ];

  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Paper Trading</h1>
          <p className="text-xs text-slate-400 mt-0.5">Simulate strategies with virtual capital using live market data</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Feed Mode Badge */}
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold ${
            feedMode === 'fyers_live' ? 'bg-profit/10 text-profit' : 'bg-yellow-500/10 text-yellow-400'
          }`}>
            <Zap className="w-3.5 h-3.5" />
            {feedMode === 'fyers_live' ? 'LIVE FEED' : 'MOCK FEED'}
          </div>

          {/* Start/Stop Button */}
          {isActive ? (
            <button
              onClick={handleStop}
              disabled={stopMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-loss/10 border border-loss/20 text-loss text-sm font-semibold hover:bg-loss/20 transition-all"
            >
              <Square className="w-4 h-4" />
              {stopMutation.isPending ? 'Stopping...' : 'Stop Session'}
            </button>
          ) : (
            <button
              onClick={handleStart}
              disabled={startMutation.isPending}
              className="btn-deploy"
            >
              <Play className="w-4 h-4" />
              {startMutation.isPending ? 'Starting...' : 'Start Session'}
            </button>
          )}
        </div>
      </div>

      {/* ── Session Status Banner ── */}
      <div className={`glass-card !p-4 !rounded-2xl flex items-center justify-between ${
        isActive ? '!border-profit/20' : '!border-slate-700/30'
      }`}>
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
            isActive ? 'bg-profit/10' : 'bg-slate-700/30'
          }`}>
            <Power className={`w-5 h-5 ${isActive ? 'text-profit' : 'text-slate-500'}`} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className={`text-sm font-bold ${isActive ? 'text-profit' : 'text-slate-400'}`}>
                {isActive ? 'SESSION ACTIVE' : 'SESSION INACTIVE'}
              </span>
              {isActive && <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />}
            </div>
            <span className="text-xs text-slate-500">
              {isActive
                ? `${runningCount} strategies running | Feed: ${feedMode === 'fyers_live' ? 'Fyers Live' : 'Mock'}`
                : 'Start a session to begin paper trading with virtual capital'}
            </span>
          </div>
        </div>
        {isActive && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setDeployOpen(!deployOpen)}
              className="btn-deploy !py-2 !px-4 !text-sm"
            >
              <Rocket className="w-4 h-4" />
              Deploy Strategy
            </button>
          </div>
        )}
      </div>

      {/* ── Live Strategy P&L (deployed via StrategyBuilder + AI auto-deploy) ── */}
      <div className="glass-card !p-4 !rounded-2xl">
        <DeployedStrategiesPnL />
        <PaperClearHistoryButton />
      </div>

      {/* ── Deploy Strategy Dropdown ── */}
      {deployOpen && isActive && (
        <div className="glass-card !p-5 !rounded-2xl animate-fade-in">
          <h3 className="text-sm font-bold text-white mb-3">Deploy a Strategy</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 mb-4">
            {STRATEGY_OPTIONS.map((s) => (
              <button
                key={s.value}
                onClick={() => setSelectedStrategy(s.value)}
                className={`text-left p-3 rounded-xl border transition-all duration-200 ${
                  selectedStrategy === s.value
                    ? 'border-accent/30 bg-accent/5'
                    : 'border-terminal-border hover:border-slate-600 hover:bg-white/[0.02]'
                }`}
              >
                <div className="text-sm font-medium text-white">{s.label}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">{s.desc}</div>
                <div className="mt-1">
                  <span className="strategy-tag !text-[9px] !px-2 !py-0.5">{s.category}</span>
                </div>
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-500">
              {selectedStrategy ? `Selected: ${selectedStrategy.replace(/_/g, ' ')}` : 'Select a strategy to deploy'}
            </span>
            <div className="flex gap-2">
              <button onClick={() => { setDeployOpen(false); setSelectedStrategy(''); }} className="px-4 py-2 text-sm text-slate-400 hover:text-slate-200 transition-colors">
                Cancel
              </button>
              <button
                onClick={handleDeploy}
                disabled={!selectedStrategy || deployMutation.isPending}
                className="btn-deploy !py-2 !px-5 !text-sm"
              >
                <Rocket className="w-3.5 h-3.5" />
                {deployMutation.isPending ? 'Deploying...' : 'Deploy'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Stats Cards (only when active) ── */}
      {isActive && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <MetricCard label="Capital" value={stats.initial_capital || stats.capital || 2000000} prefix="Rs " icon={DollarSign} colorClass="text-white" />
          <MetricCard label="Net P&L" value={stats.total_pnl ?? stats.net_pnl ?? 0} prefix="Rs " icon={TrendingUp} />
          <MetricCard label="Total Trades" value={stats.total_trades ?? stats.trades_count ?? 0} icon={BarChart3} colorClass="text-white" />
          <MetricCard label="Win Rate" value={stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(0)}%` : '--'} icon={Trophy} colorClass={
            (stats.win_rate ?? 0) >= 0.6 ? 'text-profit' : (stats.win_rate ?? 0) >= 0.45 ? 'text-yellow-400' : 'text-loss'
          } />
        </div>
      )}

      {/* ── Tabs ── */}
      {isActive && (
        <>
          <div className="flex gap-1 glass-card !rounded-xl !p-1.5">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                  activeTab === tab.id
                    ? 'bg-accent/15 text-accent shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
                }`}
              >
                {tab.label}
                {tab.id === 'strategies' && runningCount > 0 && (
                  <span className="ml-1.5 text-xs text-accent/60">({runningCount})</span>
                )}
                {tab.id === 'positions' && Array.isArray(positions) && positions.length > 0 && (
                  <span className="ml-1.5 text-xs text-accent/60">({positions.length})</span>
                )}
              </button>
            ))}
          </div>

          {/* ── Deployed Strategies ── */}
          {activeTab === 'strategies' && (
            <div className="space-y-3">
              {/* Filter toggle */}
              <div className="flex items-center justify-between">
                <div className="flex gap-1 bg-slate-800/40 rounded-lg p-0.5">
                  <button
                    onClick={() => setStrategyFilter('running')}
                    className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
                      strategyFilter === 'running' ? 'bg-accent/15 text-accent' : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Running ({runningCount})
                  </button>
                  <button
                    onClick={() => setStrategyFilter('all')}
                    className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${
                      strategyFilter === 'all' ? 'bg-accent/15 text-accent' : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    All ({sortedStrategies.length})
                  </button>
                </div>
              </div>

              {strategies.length > 0 ? strategies.map((s, i) => {
                const pnl = s.pnl ?? s.total_pnl ?? 0;
                const isRunning = (s.status || '').toLowerCase() === 'running' || (s.status || '').toLowerCase() === 'active';
                const isEntered = !!s.entered;
                return (
                  <div key={s.strategy_id || i} className="glass-card glass-card-interactive !p-5 !rounded-2xl">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-white text-sm font-bold ${
                          isRunning ? (isEntered ? 'bg-gradient-to-br from-accent to-accent-light' : 'bg-yellow-600/80') : 'bg-slate-700'
                        }`}>
                          {(s.strategy_name || s.name || 'S').charAt(0)}
                        </div>
                        <div>
                          <div className="text-sm font-semibold text-white flex items-center gap-2">
                            {s.strategy_name || s.name}
                            {s.ai_deployed && <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/30 font-bold">AI</span>}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[10px] font-mono text-slate-500">{s.strategy_id}</span>
                            <StatusBadge status={s.status || 'running'} />
                            {isRunning && isEntered && (
                              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-profit/15 text-profit border border-profit/30">ENTERED</span>
                            )}
                            {isRunning && !isEntered && (
                              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 animate-pulse">WAITING</span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-4">
                        <div className="text-right">
                          <div className={`text-lg font-bold font-mono ${
                            !isEntered && pnl === 0 ? 'text-slate-500' : pnl >= 0 ? 'text-profit' : 'text-loss'
                          }`}>
                            {!isEntered && pnl === 0 ? '--' : `${pnl >= 0 ? '+' : ''}Rs ${formatINR(pnl)}`}
                          </div>
                          <div className="text-[10px] text-slate-500">
                            {isEntered
                              ? `${s.positions_count ?? s.trades_count ?? 0} legs`
                              : 'Awaiting entry conditions'}
                          </div>
                        </div>
                        {isRunning && (
                          <button
                            onClick={() => handleStopStrategy(s.strategy_id, s.ai_deployed)}
                            disabled={stopStrategyMutation.isPending || stopDeployedMutation.isPending}
                            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-loss/10 border border-loss/20 text-loss text-xs font-semibold hover:bg-loss/20 transition-all"
                          >
                            <Pause className="w-3.5 h-3.5" />
                            Stop
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              }) : (
                <div className="glass-card !p-8 !rounded-2xl text-center">
                  <Layers className="w-10 h-10 text-slate-600 mx-auto mb-3" />
                  <div className="text-sm text-slate-400 font-medium">No strategies deployed</div>
                  <div className="text-xs text-slate-500 mt-1">Click "Deploy Strategy" above to get started</div>
                </div>
              )}
            </div>
          )}

          {/* ── Positions ── */}
          {activeTab === 'positions' && (
            <Card title={`Paper Positions (${Array.isArray(positions) ? positions.length : 0})`}>
              <DataTable
                columns={positionColumns}
                data={Array.isArray(positions) ? positions : []}
                emptyMessage="No open positions in paper trading"
              />
            </Card>
          )}

          {/* ── Order History ── */}
          {activeTab === 'orders' && (
            <Card title={`Paper Orders (${Array.isArray(orders) ? orders.length : 0})`}>
              <DataTable
                columns={orderColumns}
                data={Array.isArray(orders) ? orders : []}
                emptyMessage="No orders placed yet"
              />
            </Card>
          )}
        </>
      )}

      {/* ── Inactive State ── */}
      {!isActive && (
        <div className="glass-card !p-12 !rounded-2xl text-center">
          <div className="w-16 h-16 rounded-2xl bg-accent/10 flex items-center justify-center mx-auto mb-4">
            <Activity className="w-8 h-8 text-accent" />
          </div>
          <h2 className="text-lg font-bold text-white mb-2">Start Paper Trading</h2>
          <p className="text-sm text-slate-400 max-w-md mx-auto mb-6">
            Test your strategies with Rs 20,00,000 virtual capital using live market data.
            No real money at risk — perfect for validating strategies before going live.
          </p>
          <div className="grid grid-cols-3 gap-4 max-w-lg mx-auto mb-6">
            {[
              { icon: DollarSign, label: 'Virtual Capital', value: 'Rs 20L' },
              { icon: Zap, label: 'Data Feed', value: 'Live/Mock' },
              { icon: Target, label: 'Strategies', value: '13 Available' },
            ].map((f) => (
              <div key={f.label} className="glass-card !p-3 !rounded-xl text-center">
                <f.icon className="w-5 h-5 text-accent mx-auto mb-1" />
                <div className="text-[10px] text-slate-500 uppercase">{f.label}</div>
                <div className="text-sm font-bold text-white">{f.value}</div>
              </div>
            ))}
          </div>
          <button onClick={handleStart} disabled={startMutation.isPending} className="btn-deploy !py-3 !px-8">
            <Play className="w-5 h-5" />
            {startMutation.isPending ? 'Starting...' : 'Start Paper Trading'}
          </button>
        </div>
      )}
    </div>
  );
}

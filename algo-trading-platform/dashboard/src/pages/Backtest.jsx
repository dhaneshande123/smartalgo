import { useState, useEffect, useMemo } from 'react';
import Card from '../components/common/Card';
import EquityCurve from '../components/charts/EquityCurve';
import {
  useRunBacktest, useBacktestResults, useBacktestList,
  useVbtStrategies, useVbtObjectives,
  useRunVbtBacktest, useRunVbtOptimize, useVbtReport,
} from '../hooks/useApi';
import { downloadCsv, formatBacktestForExport, formatEquityCurveForExport } from '../utils/exportCsv';
import { UNDERLYINGS } from '../context/UnderlyingContext';

// ─── Event-Driven Strategy Options ───
const ED_STRATEGY_OPTIONS = [
  { value: 'iron_condor', label: 'Iron Condor' },
  { value: 'straddle', label: 'Straddle' },
  { value: 'momentum', label: 'Momentum Breakout' },
  { value: 'mean_reversion', label: 'Mean Reversion' },
  { value: 'supertrend', label: 'Supertrend' },
  { value: 'gamma_scalping', label: 'Gamma Scalping' },
  { value: 'vwap_scalper', label: 'VWAP Scalper' },
  { value: 'orb_options', label: 'ORB Options' },
  { value: 'expiry_day', label: 'Expiry Day' },
  { value: 'pair_trading', label: 'Pair Trading' },
];

const RESOLUTION_OPTIONS = [
  { value: '1', label: '1 min' },
  { value: '5', label: '5 min' },
  { value: '15', label: '15 min' },
  { value: '30', label: '30 min' },
  { value: 'D', label: 'Daily' },
];

const DEFAULT_PARAMS = {
  rsi_reversal: { rsi_period: 14, rsi_oversold: 30, rsi_overbought: 70 },
  macd_crossover: { macd_fast: 12, macd_slow: 26, macd_signal: 9 },
  bollinger_breakout: { bb_period: 20, bb_std: 2.0 },
  supertrend: { st_period: 10, st_multiplier: 3.0 },
  ema_crossover: { ema_fast: 9, ema_slow: 21 },
};

const PARAM_LABELS = {
  rsi_period: 'RSI Period',
  rsi_oversold: 'Oversold',
  rsi_overbought: 'Overbought',
  macd_fast: 'Fast EMA',
  macd_slow: 'Slow EMA',
  macd_signal: 'Signal',
  bb_period: 'Period',
  bb_std: 'Std Dev',
  st_period: 'ATR Period',
  st_multiplier: 'Multiplier',
  ema_fast: 'Fast EMA',
  ema_slow: 'Slow EMA',
};

const VBT_METRIC_LABELS = {
  net_return_pct: { label: 'Net Return', suffix: '%', good: v => v > 0 },
  total_return_pct: { label: 'Gross Return', suffix: '%', good: v => v > 0 },
  sharpe_ratio: { label: 'Sharpe', suffix: '', good: v => v > 1 },
  sortino_ratio: { label: 'Sortino', suffix: '', good: v => v > 1 },
  max_drawdown_pct: { label: 'Max Drawdown', suffix: '%', good: v => v > -10 },
  win_rate: { label: 'Win Rate', suffix: '%', good: v => v > 50 },
  profit_factor: { label: 'Profit Factor', suffix: 'x', good: v => v > 1.5 },
  total_trades: { label: 'Trades', suffix: '', good: () => true },
  avg_trade_pnl: { label: 'Avg Trade P&L', suffix: '', good: v => v > 0 },
  total_charges: { label: 'Total Charges', suffix: '', good: () => true },
  calmar_ratio: { label: 'Calmar', suffix: '', good: v => v > 1 },
  avg_holding_bars: { label: 'Avg Hold (bars)', suffix: '', good: () => true },
};

const ED_METRIC_LABELS = {
  totalReturn: { label: 'Total Return', suffix: '%' },
  cagr: { label: 'CAGR', suffix: '%' },
  sharpe: { label: 'Sharpe Ratio', suffix: '' },
  sortino: { label: 'Sortino Ratio', suffix: '' },
  maxDrawdown: { label: 'Max Drawdown', suffix: '%' },
  winRate: { label: 'Win Rate', suffix: '%' },
  profitFactor: { label: 'Profit Factor', suffix: 'x' },
  totalTrades: { label: 'Total Trades', suffix: '' },
  avgWin: { label: 'Avg Win', suffix: '' },
  avgLoss: { label: 'Avg Loss', suffix: '' },
  expectancy: { label: 'Expectancy', suffix: '' },
  calmar: { label: 'Calmar Ratio', suffix: '' },
};

function Spinner({ size = 4 }) {
  return (
    <svg className={`animate-spin w-${size} h-${size}`} viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

function MetricCard({ label, value, suffix = '', isGood, isBad }) {
  const color = isBad ? 'text-loss' : isGood ? 'text-profit' : 'text-white';
  const formatted = typeof value === 'number'
    ? (Math.abs(value) >= 1000 ? value.toLocaleString('en-IN', { maximumFractionDigits: 0 }) : value)
    : value;
  return (
    <div className="bg-slate-800/50 rounded-lg p-2.5">
      <div className="text-xs text-slate-500 mb-0.5">{label}</div>
      <div className={`text-lg font-bold font-mono ${color}`}>
        {formatted}{suffix}
      </div>
    </div>
  );
}

function MonthlyReturnsGrid({ monthlyReturns }) {
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  const grouped = useMemo(() => {
    if (!monthlyReturns || typeof monthlyReturns !== 'object') return {};
    if (Array.isArray(monthlyReturns)) {
      return monthlyReturns.reduce((acc, m) => {
        const label = m.month || m.label || '';
        acc[label] = m.return ?? m.ret ?? 0;
        return acc;
      }, {});
    }
    const yearMap = {};
    for (const [key, val] of Object.entries(monthlyReturns)) {
      const [year, month] = key.split('-');
      if (!yearMap[year]) yearMap[year] = {};
      yearMap[year][parseInt(month, 10)] = val;
    }
    return yearMap;
  }, [monthlyReturns]);

  if (Array.isArray(monthlyReturns)) {
    return (
      <div className="grid grid-cols-6 md:grid-cols-12 gap-1">
        {monthlyReturns.map((m) => {
          const ret = m.return ?? m.ret ?? 0;
          const label = m.month || m.label || '';
          const intensity = Math.min(Math.abs(ret) / 6, 1);
          const bg = ret >= 0
            ? `rgba(34, 197, 94, ${0.1 + intensity * 0.5})`
            : `rgba(239, 68, 68, ${0.1 + intensity * 0.5})`;
          return (
            <div key={label} className="rounded-md p-2 text-center border border-terminal-border" style={{ backgroundColor: bg }}>
              <div className="text-xs text-slate-400 mb-0.5">{label}</div>
              <div className={`text-sm font-bold font-mono ${ret >= 0 ? 'text-profit' : 'text-loss'}`}>
                {ret >= 0 ? '+' : ''}{ret}%
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  const years = Object.keys(grouped).sort();
  if (years.length === 0) return <div className="text-xs text-slate-500">No monthly data</div>;

  return (
    <div className="space-y-2">
      {years.map(year => (
        <div key={year}>
          <div className="text-xs text-slate-400 mb-1 font-mono">{year}</div>
          <div className="grid grid-cols-6 md:grid-cols-12 gap-1">
            {months.map((m, idx) => {
              const val = grouped[year]?.[idx + 1];
              if (val === undefined) {
                return (
                  <div key={m} className="rounded-md p-1.5 text-center border border-terminal-border bg-slate-800/20">
                    <div className="text-[10px] text-slate-600">{m}</div>
                    <div className="text-xs font-mono text-slate-600">—</div>
                  </div>
                );
              }
              const intensity = Math.min(Math.abs(val) / 6, 1);
              const bg = val >= 0
                ? `rgba(34, 197, 94, ${0.1 + intensity * 0.5})`
                : `rgba(239, 68, 68, ${0.1 + intensity * 0.5})`;
              return (
                <div key={m} className="rounded-md p-1.5 text-center border border-terminal-border" style={{ backgroundColor: bg }}>
                  <div className="text-[10px] text-slate-400">{m}</div>
                  <div className={`text-xs font-bold font-mono ${val >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {val >= 0 ? '+' : ''}{val}%
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 1: Event-Driven Backtest (existing functionality)
// ═══════════════════════════════════════════════════════════════════

function EventDrivenTab() {
  const [config, setConfig] = useState({
    strategy: 'iron_condor', underlying: 'NIFTY',
    startDate: '2025-01-01', endDate: '2025-12-31', capital: 2000000,
  });
  const [activeJobId, setActiveJobId] = useState(null);
  const [results, setResults] = useState(null);
  const [jobError, setJobError] = useState(null);

  const runBacktest = useRunBacktest();
  const { data: backtestList } = useBacktestList();
  const { data: jobData } = useBacktestResults(activeJobId);

  useEffect(() => {
    if (!jobData) return;
    if (jobData.status === 'completed') {
      setResults({ metrics: jobData.metrics || {}, equityCurve: jobData.equityCurve || [], monthlyReturns: jobData.monthlyReturns || [], source: jobData.source || 'fyers_backtest' });
      setActiveJobId(null);
      setJobError(null);
    } else if (jobData.status === 'failed') {
      setJobError(jobData.error || 'Backtest failed');
      setActiveJobId(null);
    }
  }, [jobData]);

  const handleRun = () => {
    setJobError(null);
    setResults(null);
    runBacktest.mutate(config, {
      onSuccess: (data) => {
        if (data?.job_id) setActiveJobId(data.job_id);
        else if (data?.metrics) setResults(data);
      },
      onError: (err) => setJobError(err?.response?.data?.detail || err.message || 'Failed to start backtest'),
    });
  };

  const handleChange = (key, value) => setConfig(prev => ({ ...prev, [key]: value }));
  const isRunning = runBacktest.isPending || !!activeJobId;

  return (
    <div className="space-y-4">
      <Card title="Event-Driven Configuration">
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Strategy</label>
            <select value={config.strategy} onChange={e => handleChange('strategy', e.target.value)} className="input-field">
              {ED_STRATEGY_OPTIONS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Underlying</label>
            <select value={config.underlying} onChange={e => handleChange('underlying', e.target.value)} className="input-field">
              {UNDERLYINGS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Start Date</label>
            <input type="date" value={config.startDate} onChange={e => handleChange('startDate', e.target.value)} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">End Date</label>
            <input type="date" value={config.endDate} onChange={e => handleChange('endDate', e.target.value)} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Capital (₹)</label>
            <input type="number" value={config.capital} onChange={e => handleChange('capital', Number(e.target.value))} className="input-field" />
          </div>
          <div className="flex items-end">
            <button onClick={handleRun} disabled={isRunning} className="btn-primary w-full">
              {isRunning ? <span className="flex items-center justify-center gap-2"><Spinner /> Running...</span> : 'Run Backtest'}
            </button>
          </div>
        </div>
        {activeJobId && (
          <div className="mt-3 flex items-center gap-2 text-sm">
            <Spinner /><span className="text-blue-400">Downloading history and running backtest...</span>
            <span className="text-slate-500 font-mono text-xs">{activeJobId}</span>
          </div>
        )}
        {jobError && <div className="mt-3 p-2 bg-loss/10 border border-loss/20 rounded text-sm text-loss">{jobError}</div>}
      </Card>

      {backtestList?.jobs?.length > 0 && (
        <Card title="Recent Backtests">
          <div className="space-y-1">
            {backtestList.jobs.slice(0, 5).map(job => (
              <div key={job.job_id} className="flex items-center justify-between text-xs p-2 bg-slate-800/30 rounded">
                <span className="text-slate-400 font-mono">{job.job_id}</span>
                <span className="text-slate-300">{job.strategy}</span>
                <span className="text-slate-500">{job.start_date} to {job.end_date}</span>
                <span className={`font-medium ${job.status === 'completed' ? 'text-profit' : job.status === 'running' ? 'text-blue-400' : job.status === 'failed' ? 'text-loss' : 'text-slate-400'}`}>{job.status}</span>
                {job.total_return_pct !== undefined && (
                  <span className={`font-mono ${job.total_return_pct >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {job.total_return_pct >= 0 ? '+' : ''}{job.total_return_pct}%
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {results && (
        <>
          {results.source && <div className="text-xs text-slate-500">Results from: <span className="font-mono text-blue-400">{results.source}</span></div>}
          <Card title="Performance Metrics">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              {Object.entries(results.metrics).map(([key, value]) => {
                const info = ED_METRIC_LABELS[key] || { label: key, suffix: '' };
                const isNeg = typeof value === 'number' && value < 0;
                const isGood = key === 'maxDrawdown' ? value > -10 : key === 'winRate' ? value > 50 : value > 0;
                return <MetricCard key={key} label={info.label} value={value} suffix={info.suffix} isGood={isGood} isBad={isNeg} />;
              })}
            </div>
          </Card>
          {results.equityCurve?.length > 0 && (
            <Card title="Equity Curve"><EquityCurve data={results.equityCurve} height={300} /></Card>
          )}
          {results.monthlyReturns?.length > 0 && (
            <Card title="Monthly Returns"><MonthlyReturnsGrid monthlyReturns={results.monthlyReturns} /></Card>
          )}
        </>
      )}

      {!results && (
        <div className="text-center py-12 text-slate-500">
          <div className="text-4xl mb-3">📊</div>
          <div className="text-sm">Configure and run a backtest to see results</div>
          <div className="text-xs mt-1 text-slate-600">Uses event-driven bar-by-bar replay with simulated broker</div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 2: VectorBT (Fast Vectorized Backtesting)
// ═══════════════════════════════════════════════════════════════════

function VectorBTTab() {
  const { data: strategiesData } = useVbtStrategies();
  const runVbt = useRunVbtBacktest();
  const reportMut = useVbtReport();

  const [config, setConfig] = useState({
    strategy: 'rsi_reversal', symbol: 'NIFTY', resolution: '5',
    start_date: '2025-01-01', end_date: '2025-12-31',
    initial_capital: 10000000, lot_size: 75, sl_pct: '', tp_pct: '',
  });
  const [params, setParams] = useState(DEFAULT_PARAMS.rsi_reversal);
  const [result, setResult] = useState(null);
  const [qsMetrics, setQsMetrics] = useState(null);
  const [error, setError] = useState(null);

  const strategies = strategiesData?.strategies || [
    { id: 'rsi_reversal', name: 'RSI Mean Reversion' },
    { id: 'macd_crossover', name: 'MACD Crossover' },
    { id: 'bollinger_breakout', name: 'Bollinger Band Reversal' },
    { id: 'supertrend', name: 'Supertrend Follower' },
    { id: 'ema_crossover', name: 'EMA Crossover' },
  ];

  const handleStrategyChange = (stratId) => {
    setConfig(prev => ({ ...prev, strategy: stratId }));
    setParams(DEFAULT_PARAMS[stratId] || {});
  };

  const handleRun = () => {
    setError(null);
    setResult(null);
    setQsMetrics(null);
    const body = {
      ...config,
      initial_capital: Number(config.initial_capital),
      lot_size: Number(config.lot_size),
      params,
      sl_pct: config.sl_pct ? Number(config.sl_pct) : null,
      tp_pct: config.tp_pct ? Number(config.tp_pct) : null,
    };
    runVbt.mutate(body, {
      onSuccess: (data) => {
        if (data?.ok && data.result) {
          setResult(data.result);
          if (data.result.equity_curve?.length > 2) {
            reportMut.mutate(
              { equity_curve: data.result.equity_curve, type: 'metrics' },
              { onSuccess: (r) => { if (r?.ok) setQsMetrics(r.metrics); } },
            );
          }
        } else {
          setError(data?.error || 'Unknown error');
        }
      },
      onError: (err) => setError(err?.response?.data?.error || err.message || 'Backtest failed'),
    });
  };

  const equityCurveForChart = useMemo(() => {
    if (!result?.equity_curve) return [];
    return result.equity_curve.map(([date, equity]) => ({ date, equity }));
  }, [result]);

  const selectedStrategy = strategies.find(s => s.id === config.strategy);

  return (
    <div className="space-y-4">
      {/* Strategy & Config */}
      <Card title="VectorBT Configuration" actions={
        <span className="text-xs text-blue-400/60 font-mono">100x faster vectorized engine</span>
      }>
        {/* Strategy Cards */}
        <div className="mb-4">
          <label className="text-xs text-slate-400 mb-2 block">Strategy</label>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
            {strategies.map(s => (
              <button
                key={s.id}
                onClick={() => handleStrategyChange(s.id)}
                className={`p-2.5 rounded-lg border text-left transition-all ${
                  config.strategy === s.id
                    ? 'border-blue-500 bg-blue-500/10 ring-1 ring-blue-500/30'
                    : 'border-terminal-border bg-slate-800/30 hover:border-slate-600'
                }`}
              >
                <div className="text-sm font-medium text-white">{s.name}</div>
                {s.description && <div className="text-[10px] text-slate-500 mt-0.5 line-clamp-2">{s.description}</div>}
              </button>
            ))}
          </div>
        </div>

        {/* Market & Date Config */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mb-4">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Symbol</label>
            <select value={config.symbol} onChange={e => setConfig(p => ({ ...p, symbol: e.target.value }))} className="input-field">
              {UNDERLYINGS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Timeframe</label>
            <select value={config.resolution} onChange={e => setConfig(p => ({ ...p, resolution: e.target.value }))} className="input-field">
              {RESOLUTION_OPTIONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Start Date</label>
            <input type="date" value={config.start_date} onChange={e => setConfig(p => ({ ...p, start_date: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">End Date</label>
            <input type="date" value={config.end_date} onChange={e => setConfig(p => ({ ...p, end_date: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Capital (₹)</label>
            <input type="number" value={config.initial_capital} onChange={e => setConfig(p => ({ ...p, initial_capital: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block" title="Stop loss percentage from entry price">SL %</label>
            <input type="number" step="0.5" placeholder="e.g. 2" value={config.sl_pct} onChange={e => setConfig(p => ({ ...p, sl_pct: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block" title="Take profit percentage from entry price">TP %</label>
            <input type="number" step="0.5" placeholder="e.g. 4" value={config.tp_pct} onChange={e => setConfig(p => ({ ...p, tp_pct: e.target.value }))} className="input-field" />
          </div>
        </div>

        {/* Strategy Parameters */}
        <div className="mb-4">
          <label className="text-xs text-slate-400 mb-2 block">
            {selectedStrategy?.name || config.strategy} Parameters
          </label>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
            {Object.entries(params).map(([key, val]) => (
              <div key={key}>
                <label className="text-[10px] text-slate-500 mb-0.5 block">{PARAM_LABELS[key] || key}</label>
                <input
                  type="number"
                  step={typeof val === 'number' && !Number.isInteger(val) ? '0.1' : '1'}
                  value={val}
                  onChange={e => setParams(p => ({ ...p, [key]: Number(e.target.value) }))}
                  className="input-field"
                />
              </div>
            ))}
          </div>
        </div>

        <button onClick={handleRun} disabled={runVbt.isPending} className="btn-primary">
          {runVbt.isPending ? <span className="flex items-center gap-2"><Spinner /> Running VectorBT...</span> : 'Run Vectorized Backtest'}
        </button>

        {error && <div className="mt-3 p-2 bg-loss/10 border border-loss/20 rounded text-sm text-loss">{error}</div>}
      </Card>

      {/* Results */}
      {result && (
        <>
          {/* Metrics Grid */}
          <Card title="Performance Metrics" actions={
            <div className="flex items-center gap-3 text-xs">
              <span className="text-slate-500">
                {result.candles_count?.toLocaleString('en-IN') || '—'} candles
              </span>
              <span className="text-slate-500">
                Params: <span className="font-mono text-blue-400">{JSON.stringify(result.params_used)}</span>
              </span>
              <button onClick={() => {
                downloadCsv(formatBacktestForExport(result), `vbt_${result.strategy}_${new Date().toISOString().slice(0,10)}.csv`);
                if (result.equity_curve?.length > 0) downloadCsv(formatEquityCurveForExport(result.equity_curve), `vbt_equity_${result.strategy}_${new Date().toISOString().slice(0,10)}.csv`);
              }} className="p-1 rounded hover:bg-slate-700/50 text-slate-400 hover:text-white" title="Export results + equity curve to CSV">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              </button>
            </div>
          }>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              {Object.entries(VBT_METRIC_LABELS).map(([key, info]) => {
                const val = result[key];
                if (val === undefined) return null;
                return (
                  <MetricCard
                    key={key}
                    label={info.label}
                    value={val}
                    suffix={info.suffix}
                    isGood={info.good(val)}
                    isBad={typeof val === 'number' && val < 0}
                  />
                );
              })}
            </div>
          </Card>

          {/* QuantStats Deep Metrics */}
          {qsMetrics && !qsMetrics.error && (
            <Card title="QuantStats Analytics" actions={
              <span className="text-xs text-emerald-400/60 font-mono">powered by quantstats</span>
            }>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
                {[
                  ['CAGR', qsMetrics.cagr_pct, '%'],
                  ['Volatility', qsMetrics.volatility_pct, '%'],
                  ['Skew', qsMetrics.skew, ''],
                  ['Kurtosis', qsMetrics.kurtosis, ''],
                  ['Best Day', qsMetrics.best_day_pct, '%'],
                  ['Worst Day', qsMetrics.worst_day_pct, '%'],
                  ['Avg DD', qsMetrics.avg_drawdown_pct, '%'],
                ].map(([label, val, suffix]) => val !== undefined && (
                  <MetricCard
                    key={label}
                    label={label}
                    value={val}
                    suffix={suffix}
                    isGood={label === 'Best Day' || (typeof val === 'number' && val > 0)}
                    isBad={typeof val === 'number' && val < 0 && label !== 'Skew' && label !== 'Kurtosis'}
                  />
                ))}
              </div>
            </Card>
          )}

          {/* Equity Curve */}
          {equityCurveForChart.length > 0 && (
            <Card title="Equity Curve">
              <EquityCurve data={equityCurveForChart} height={320} />
            </Card>
          )}

          {/* Monthly Returns */}
          {result.monthly_returns && Object.keys(result.monthly_returns).length > 0 && (
            <Card title="Monthly Returns Heatmap">
              <MonthlyReturnsGrid monthlyReturns={result.monthly_returns} />
            </Card>
          )}
        </>
      )}

      {!result && !runVbt.isPending && !error && (
        <div className="text-center py-12 text-slate-500">
          <div className="text-4xl mb-3">⚡</div>
          <div className="text-sm">Select a strategy, tune parameters, and run</div>
          <div className="text-xs mt-1 text-slate-600">VectorBT processes thousands of candles in seconds with TA-Lib indicators</div>
          <div className="text-xs mt-2 text-yellow-500/60">Requires cached candles — use Backtest &gt; Data Pipeline to download first</div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 3: Optimizer (Optuna-powered parameter search)
// ═══════════════════════════════════════════════════════════════════

function OptimizerTab() {
  const { data: strategiesData } = useVbtStrategies();
  const { data: objectivesData } = useVbtObjectives();
  const runOptimize = useRunVbtOptimize();

  const [config, setConfig] = useState({
    strategy: 'rsi_reversal', symbol: 'NIFTY', resolution: '5',
    start_date: '2025-01-01', end_date: '2025-12-31',
    initial_capital: 10000000, lot_size: 75,
    n_trials: 50, objective: 'sharpe',
    sl_pct: '', tp_pct: '',
  });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const strategies = strategiesData?.strategies || [
    { id: 'rsi_reversal', name: 'RSI Mean Reversion' },
    { id: 'macd_crossover', name: 'MACD Crossover' },
    { id: 'bollinger_breakout', name: 'Bollinger Band Reversal' },
    { id: 'supertrend', name: 'Supertrend Follower' },
    { id: 'ema_crossover', name: 'EMA Crossover' },
  ];

  const objectives = objectivesData?.objectives || [
    { id: 'sharpe', name: 'Sharpe Ratio' },
    { id: 'sortino', name: 'Sortino Ratio' },
    { id: 'return', name: 'Net Return %' },
    { id: 'calmar', name: 'Calmar Ratio' },
    { id: 'win_rate', name: 'Win Rate %' },
    { id: 'profit_factor', name: 'Profit Factor' },
  ];

  const handleRun = () => {
    setError(null);
    setResult(null);
    const body = {
      ...config,
      initial_capital: Number(config.initial_capital),
      lot_size: Number(config.lot_size),
      n_trials: Number(config.n_trials),
      sl_pct: config.sl_pct ? Number(config.sl_pct) : null,
      tp_pct: config.tp_pct ? Number(config.tp_pct) : null,
    };
    runOptimize.mutate(body, {
      onSuccess: (data) => {
        if (data?.ok && data.result) setResult(data.result);
        else setError(data?.error || 'Optimization failed');
      },
      onError: (err) => setError(err?.response?.data?.error || err.message || 'Optimization failed'),
    });
  };

  const bestEquityCurve = useMemo(() => {
    if (!result?.best_backtest?.equity_curve) return [];
    return result.best_backtest.equity_curve.map(([date, equity]) => ({ date, equity }));
  }, [result]);

  return (
    <div className="space-y-4">
      <Card title="Optuna Optimizer" actions={
        <span className="text-xs text-purple-400/60 font-mono">TPE intelligent search</span>
      }>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3 mb-4">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Strategy</label>
            <select value={config.strategy} onChange={e => setConfig(p => ({ ...p, strategy: e.target.value }))} className="input-field">
              {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Objective</label>
            <select value={config.objective} onChange={e => setConfig(p => ({ ...p, objective: e.target.value }))} className="input-field">
              {objectives.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block" title="Number of parameter combinations to try">Trials</label>
            <input type="number" min={10} max={200} value={config.n_trials} onChange={e => setConfig(p => ({ ...p, n_trials: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Symbol</label>
            <select value={config.symbol} onChange={e => setConfig(p => ({ ...p, symbol: e.target.value }))} className="input-field">
              <option value="NIFTY">NIFTY</option>
              <option value="BANKNIFTY">BANKNIFTY</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Start</label>
            <input type="date" value={config.start_date} onChange={e => setConfig(p => ({ ...p, start_date: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">End</label>
            <input type="date" value={config.end_date} onChange={e => setConfig(p => ({ ...p, end_date: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">SL %</label>
            <input type="number" step="0.5" placeholder="opt" value={config.sl_pct} onChange={e => setConfig(p => ({ ...p, sl_pct: e.target.value }))} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">TP %</label>
            <input type="number" step="0.5" placeholder="opt" value={config.tp_pct} onChange={e => setConfig(p => ({ ...p, tp_pct: e.target.value }))} className="input-field" />
          </div>
        </div>

        <button onClick={handleRun} disabled={runOptimize.isPending} className="btn-primary">
          {runOptimize.isPending ? (
            <span className="flex items-center gap-2"><Spinner /> Optimizing ({config.n_trials} trials)...</span>
          ) : `Optimize (${config.n_trials} trials)`}
        </button>

        {error && <div className="mt-3 p-2 bg-loss/10 border border-loss/20 rounded text-sm text-loss">{error}</div>}
      </Card>

      {/* Optimization Results */}
      {result && (
        <>
          {/* Best Parameters */}
          <Card title="Best Parameters Found" actions={
            <span className="text-xs text-slate-500">{result.total_trials} trials completed</span>
          }>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <div className="text-xs text-slate-400 mb-2">Optimal Parameters</div>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(result.best_params || {}).map(([key, val]) => (
                    <div key={key} className="bg-slate-800/50 rounded-lg p-2.5 border border-blue-500/20">
                      <div className="text-[10px] text-slate-500">{PARAM_LABELS[key] || key}</div>
                      <div className="text-lg font-bold font-mono text-blue-400">{typeof val === 'number' ? (Number.isInteger(val) ? val : val.toFixed(2)) : val}</div>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-400 mb-2">Best Result</div>
                <div className="bg-slate-800/50 rounded-lg p-3 border border-emerald-500/20">
                  <div className="text-xs text-slate-500">{objectives.find(o => o.id === result.objective)?.name || result.objective}</div>
                  <div className="text-3xl font-bold font-mono text-profit">{result.best_value}</div>
                  <div className="text-xs text-slate-500 mt-1">Strategy: {result.strategy}</div>
                </div>
                {result.best_backtest && (
                  <div className="grid grid-cols-3 gap-2 mt-2">
                    <MetricCard label="Return" value={result.best_backtest.net_return_pct} suffix="%" isGood={result.best_backtest.net_return_pct > 0} isBad={result.best_backtest.net_return_pct < 0} />
                    <MetricCard label="Sharpe" value={result.best_backtest.sharpe_ratio} isGood={result.best_backtest.sharpe_ratio > 1} />
                    <MetricCard label="Max DD" value={result.best_backtest.max_drawdown_pct} suffix="%" isGood={result.best_backtest.max_drawdown_pct > -10} isBad={result.best_backtest.max_drawdown_pct < -15} />
                  </div>
                )}
              </div>
            </div>
          </Card>

          {/* Best Equity Curve */}
          {bestEquityCurve.length > 0 && (
            <Card title="Best Trial — Equity Curve">
              <EquityCurve data={bestEquityCurve} height={280} />
            </Card>
          )}

          {/* Trial History */}
          {result.all_trials?.length > 0 && (
            <Card title="Trial History" actions={
              <span className="text-xs text-slate-500">Top {result.all_trials.length} trials shown</span>
            }>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-terminal-border">
                      <th className="text-left text-slate-500 py-1.5 px-2">#</th>
                      <th className="text-left text-slate-500 py-1.5 px-2">Params</th>
                      <th className="text-right text-slate-500 py-1.5 px-2">Value</th>
                      <th className="text-right text-slate-500 py-1.5 px-2">Return %</th>
                      <th className="text-right text-slate-500 py-1.5 px-2">Sharpe</th>
                      <th className="text-right text-slate-500 py-1.5 px-2">Trades</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.all_trials.map((trial, idx) => (
                      <tr key={idx} className={`border-b border-terminal-border/30 ${idx === 0 ? 'bg-emerald-500/5' : 'hover:bg-slate-800/30'}`}>
                        <td className="py-1.5 px-2 text-slate-400 font-mono">{trial.number ?? idx + 1}</td>
                        <td className="py-1.5 px-2 font-mono text-slate-300">
                          {Object.entries(trial.params || {}).map(([k, v]) => `${k}=${typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(1) : v}`).join(', ')}
                        </td>
                        <td className={`py-1.5 px-2 text-right font-mono font-medium ${(trial.value ?? 0) > 0 ? 'text-profit' : 'text-loss'}`}>
                          {trial.value?.toFixed(3) ?? '—'}
                        </td>
                        <td className={`py-1.5 px-2 text-right font-mono ${(trial.net_return_pct ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                          {trial.net_return_pct?.toFixed(1) ?? '—'}%
                        </td>
                        <td className="py-1.5 px-2 text-right font-mono text-slate-300">
                          {trial.sharpe_ratio?.toFixed(2) ?? '—'}
                        </td>
                        <td className="py-1.5 px-2 text-right font-mono text-slate-400">
                          {trial.total_trades ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}

      {!result && !runOptimize.isPending && !error && (
        <div className="text-center py-12 text-slate-500">
          <div className="text-4xl mb-3">🧬</div>
          <div className="text-sm">Optuna will search parameter space to find optimal settings</div>
          <div className="text-xs mt-1 text-slate-600">Uses TPE (Tree-structured Parzen Estimator) for intelligent exploration</div>
          <div className="text-xs mt-2 text-yellow-500/60">50 trials takes ~30s, 200 trials ~2min depending on data size</div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Main Backtest Page
// ═══════════════════════════════════════════════════════════════════

const TABS = [
  { id: 'vectorbt', label: 'VectorBT', icon: '⚡', description: 'Fast vectorized backtesting' },
  { id: 'optimizer', label: 'Optimizer', icon: '🧬', description: 'Optuna parameter search' },
  { id: 'event-driven', label: 'Event-Driven', icon: '📊', description: 'Bar-by-bar replay' },
];

export default function Backtest() {
  const [activeTab, setActiveTab] = useState('vectorbt');

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Tab Switcher */}
      <div className="flex items-center gap-1 bg-slate-800/30 rounded-lg p-1 border border-terminal-border">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition-all ${
              activeTab === tab.id
                ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-700/30 border border-transparent'
            }`}
            title={tab.description}
          >
            <span className="mr-1.5">{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'vectorbt' && <VectorBTTab />}
      {activeTab === 'optimizer' && <OptimizerTab />}
      {activeTab === 'event-driven' && <EventDrivenTab />}
    </div>
  );
}

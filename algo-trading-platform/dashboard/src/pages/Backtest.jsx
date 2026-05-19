import React, { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import EquityCurve from '../components/charts/EquityCurve';
import { useRunBacktest, useBacktestResults, useBacktestList } from '../hooks/useApi';

const fallbackResults = {
  metrics: {
    totalReturn: 28.5, cagr: 32.1, sharpe: 1.85, sortino: 2.4,
    maxDrawdown: -8.2, winRate: 62, profitFactor: 2.1, totalTrades: 245,
    avgWin: 18500, avgLoss: -12000, expectancy: 4200, calmar: 3.91,
  },
  equityCurve: Array.from({ length: 60 }, (_, i) => ({
    date: new Date(2025, 0, 1 + i * 5).toISOString().slice(0, 10),
    equity: 1000000 + i * 5000 + Math.sin(i / 3) * 20000 + Math.random() * 10000,
  })),
  monthlyReturns: [
    { month: 'Jan', return: 3.2 }, { month: 'Feb', return: -1.5 }, { month: 'Mar', return: 4.8 },
    { month: 'Apr', return: 2.1 }, { month: 'May', return: -0.8 }, { month: 'Jun', return: 5.2 },
    { month: 'Jul', return: 1.9 }, { month: 'Aug', return: -2.3 }, { month: 'Sep', return: 3.7 },
    { month: 'Oct', return: 4.1 }, { month: 'Nov', return: -1.2 }, { month: 'Dec', return: 6.3 },
  ],
};

const metricLabels = {
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

const STRATEGY_OPTIONS = [
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

export default function Backtest() {
  const [config, setConfig] = useState({
    strategy: 'iron_condor',
    underlying: 'NIFTY',
    startDate: '2025-01-01',
    endDate: '2025-12-31',
    capital: 2000000,
  });
  const [activeJobId, setActiveJobId] = useState(null);
  const [results, setResults] = useState(null);
  const [jobError, setJobError] = useState(null);

  const runBacktest = useRunBacktest();
  const { data: backtestList } = useBacktestList();
  const { data: jobData } = useBacktestResults(activeJobId);

  // Poll for job results
  useEffect(() => {
    if (!jobData) return;
    if (jobData.status === 'completed') {
      setResults({
        metrics: jobData.metrics || {},
        equityCurve: jobData.equityCurve || [],
        monthlyReturns: jobData.monthlyReturns || [],
        source: jobData.source || 'fyers_backtest',
      });
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
        if (data?.job_id) {
          setActiveJobId(data.job_id);
        } else if (data?.metrics) {
          // Direct results (fallback)
          setResults(data);
        }
      },
      onError: (err) => {
        setJobError(err?.response?.data?.detail || err.message || 'Failed to start backtest');
      },
    });
  };

  const handleChange = (key, value) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const isRunning = runBacktest.isPending || !!activeJobId;
  const displayResults = results || fallbackResults;
  const hasRealResults = results !== null;

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Configuration Form */}
      <Card title="Backtest Configuration">
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Strategy</label>
            <select value={config.strategy} onChange={(e) => handleChange('strategy', e.target.value)} className="input-field">
              {STRATEGY_OPTIONS.map(s => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Underlying</label>
            <select value={config.underlying} onChange={(e) => handleChange('underlying', e.target.value)} className="input-field">
              <option value="NIFTY">NIFTY</option>
              <option value="BANKNIFTY">BANKNIFTY</option>
              <option value="FINNIFTY">FINNIFTY</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Start Date</label>
            <input type="date" value={config.startDate} onChange={(e) => handleChange('startDate', e.target.value)} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">End Date</label>
            <input type="date" value={config.endDate} onChange={(e) => handleChange('endDate', e.target.value)} className="input-field" />
          </div>
          <div>
            <label className="text-xs text-slate-400 mb-1 block">Capital (Rs)</label>
            <input type="number" value={config.capital} onChange={(e) => handleChange('capital', Number(e.target.value))} className="input-field" />
          </div>
          <div className="flex items-end">
            <button onClick={handleRun} disabled={isRunning} className="btn-primary w-full">
              {isRunning ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                  Running...
                </span>
              ) : 'Run Backtest'}
            </button>
          </div>
        </div>
        {activeJobId && (
          <div className="mt-3 flex items-center gap-2 text-sm">
            <svg className="animate-spin w-4 h-4 text-blue-400" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
            <span className="text-blue-400">Downloading Fyers historical data and running backtest...</span>
            <span className="text-slate-500 font-mono text-xs">{activeJobId}</span>
          </div>
        )}
        {jobError && (
          <div className="mt-3 p-2 bg-loss/10 border border-loss/20 rounded text-sm text-loss">
            {jobError}
          </div>
        )}
      </Card>

      {/* Previous Backtests */}
      {backtestList?.jobs?.length > 0 && (
        <Card title="Recent Backtests">
          <div className="space-y-1">
            {backtestList.jobs.slice(0, 5).map((job) => (
              <div key={job.job_id} className="flex items-center justify-between text-xs p-2 bg-slate-800/30 rounded">
                <span className="text-slate-400 font-mono">{job.job_id}</span>
                <span className="text-slate-300">{job.strategy}</span>
                <span className="text-slate-500">{job.start_date} to {job.end_date}</span>
                <span className={`font-medium ${
                  job.status === 'completed' ? 'text-profit' :
                  job.status === 'running' ? 'text-blue-400' :
                  job.status === 'failed' ? 'text-loss' : 'text-slate-400'
                }`}>{job.status}</span>
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

      {displayResults && (
        <>
          {/* Source indicator */}
          {hasRealResults && displayResults.source && (
            <div className="text-xs text-slate-500">
              Results from: <span className="font-mono text-blue-400">{displayResults.source}</span>
            </div>
          )}
          {!hasRealResults && (
            <div className="text-xs text-yellow-500/70">Showing sample results. Run a backtest to see real data from Fyers.</div>
          )}

          {/* Metrics Table */}
          <Card title="Performance Metrics">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              {Object.entries(displayResults.metrics).map(([key, value]) => {
                const info = metricLabels[key] || { label: key, suffix: '' };
                const isNeg = typeof value === 'number' && value < 0;
                const isGood = key === 'maxDrawdown' ? value > -10 : key === 'winRate' ? value > 50 : value > 0;
                return (
                  <div key={key} className="bg-slate-800/50 rounded-lg p-2.5">
                    <div className="text-xs text-slate-500 mb-0.5">{info.label}</div>
                    <div className={`text-lg font-bold font-mono ${isNeg ? 'text-loss' : isGood ? 'text-profit' : 'text-white'}`}>
                      {typeof value === 'number' && key.includes('avg')
                        ? value.toLocaleString('en-IN')
                        : value}
                      {info.suffix}
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Equity Curve */}
          {displayResults.equityCurve?.length > 0 && (
            <Card title="Equity Curve">
              <EquityCurve data={displayResults.equityCurve} height={300} />
            </Card>
          )}

          {/* Monthly Returns Heatmap */}
          {displayResults.monthlyReturns?.length > 0 && (
            <Card title="Monthly Returns">
              <div className="grid grid-cols-12 gap-1">
                {displayResults.monthlyReturns.map((m) => {
                  const ret = m.return ?? m.ret ?? 0;
                  const label = m.month || m.label || '';
                  const intensity = Math.min(Math.abs(ret) / 6, 1);
                  const bg = ret >= 0
                    ? `rgba(34, 197, 94, ${0.1 + intensity * 0.5})`
                    : `rgba(239, 68, 68, ${0.1 + intensity * 0.5})`;
                  return (
                    <div
                      key={label}
                      className="rounded-md p-2 text-center border border-terminal-border"
                      style={{ backgroundColor: bg }}
                    >
                      <div className="text-xs text-slate-400 mb-0.5">{label}</div>
                      <div className={`text-sm font-bold font-mono ${ret >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {ret >= 0 ? '+' : ''}{ret}%
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

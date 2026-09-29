import React, { useState, useEffect, useRef } from 'react';
import {
  Rocket, TrendingUp, TrendingDown, Gauge, Clock, AlertTriangle,
  Shield, Target, Activity, ChevronDown, ChevronUp, Zap, BarChart3,
} from 'lucide-react';
import { useUnderlying } from '../context/UnderlyingContext';
import {
  useFlyHighSignal, useFlyHighConfig, useFlyHighPerformance,
  useSetFlyHighConfig, useDeployFlyHigh, useFlyHighTicker,
} from '../hooks/useApi';

function StatCard({ label, value, sub, icon: Icon, color = 'text-slate-300' }) {
  return (
    <div className="glass-card !rounded-xl !p-3 flex items-center gap-3">
      {Icon && <Icon className={`w-5 h-5 ${color} flex-shrink-0`} />}
      <div className="min-w-0">
        <div className={`text-lg font-bold font-mono ${color}`}>{value}</div>
        <div className="text-xs text-slate-400 truncate">{label}</div>
        {sub && <div className="text-[10px] text-slate-500">{sub}</div>}
      </div>
    </div>
  );
}

export default function FlyHigh() {
  const { underlying } = useUnderlying();
  const { data: sig } = useFlyHighSignal(underlying);
  const { data: cfg } = useFlyHighConfig();
  const { data: perf } = useFlyHighPerformance();
  const { data: ticker } = useFlyHighTicker(underlying);
  const deployMut = useDeployFlyHigh();
  const configMut = useSetFlyHighConfig();
  const [showConfig, setShowConfig] = useState(false);
  const [localCfg, setLocalCfg] = useState({});
  const debounceRef = useRef(null);
  useEffect(() => { if (cfg) setLocalCfg(prev => Object.keys(prev).length ? prev : { ...cfg }); }, [cfg]);
  const handleCfgChange = (key, value, type) => {
    const parsed = type === 'number' ? Number(value) : value;
    setLocalCfg(prev => ({ ...prev, [key]: parsed }));
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => configMut.mutate({ [key]: parsed }), 600);
  };

  const hasSignal = sig?.has_signal;
  const direction = sig?.direction || '';
  const isBullish = direction === 'BULLISH';
  const adx = sig?.adx ?? 0;
  const vwap = ticker?.vwap || sig?.vwap || 0;
  const spot = ticker?.spot || sig?.spot || 0;
  const spotVsVwap = spot && vwap ? ((spot - vwap) / vwap * 100).toFixed(2) : '0.00';
  const blockers = sig?.blockers || [];
  const tradesToday = sig?.trades_today ?? 0;
  const maxTrades = cfg?.max_trades_per_day ?? 2;
  const crossInfo = sig?.candle_info || {};

  const handleDeploy = () => {
    if (!hasSignal) return;
    deployMut.mutate({ symbol: underlying });
  };

  const handleToggleAuto = () => {
    configMut.mutate({ auto_deploy: !cfg?.auto_deploy });
  };

  return (
    <div className="space-y-4 max-w-6xl mx-auto">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Rocket className="w-6 h-6 text-accent" />
          <h1 className="text-xl font-bold">Fly-High Strategy</h1>
          <span className="text-xs text-slate-400 bg-slate-800/50 px-2 py-0.5 rounded-full">VWAP Crossover</span>
        </div>
        <div className="flex items-center gap-2 ml-auto flex-wrap">
          {/* Auto-deploy toggle — hands-free deploy when a signal forms */}
          <button
            onClick={handleToggleAuto}
            disabled={configMut.isPending}
            title={cfg?.auto_deploy
              ? 'Auto-deploy ON — Fly-High deploys automatically when a valid crossover forms (paper). Click to disable.'
              : 'Auto-deploy OFF — signals are shown but you deploy manually. Click to enable hands-free deploy.'}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold transition-all ${
              cfg?.auto_deploy ? 'bg-profit/20 text-profit ring-1 ring-profit/40' : 'bg-slate-700/40 text-slate-400'
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            {cfg?.auto_deploy ? `AUTO-DEPLOY ON (${underlying})` : 'Auto-Deploy OFF'}
          </button>
          <span className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold ${
            adx >= 25 ? 'bg-profit/15 text-profit' : adx >= 20 ? 'bg-yellow-500/15 text-yellow-400' : 'bg-slate-700/40 text-slate-400'
          }`}>
            <Gauge className="w-3.5 h-3.5" />
            ADX {adx.toFixed(0)} {adx >= 25 ? '— Strong' : adx >= 20 ? '— Trending' : '— Weak'}
          </span>
          <span className={`px-3 py-1.5 rounded-full text-xs font-bold ${
            tradesToday >= maxTrades ? 'bg-red-500/15 text-red-400' : 'bg-slate-700/40 text-slate-400'
          }`}>
            {tradesToday}/{maxTrades} trades today
          </span>
          <span className="px-3 py-1.5 rounded-full text-xs font-bold bg-accent/15 text-accent">{underlying}</span>
        </div>
      </div>

      {/* ── Auto-deploy active banner ── */}
      {cfg?.auto_deploy && (
        <div className="glass-card !rounded-xl !p-3 flex items-start gap-2 bg-profit/5 border border-profit/20">
          <Zap className="w-4 h-4 text-profit flex-shrink-0 mt-0.5" />
          <div className="text-xs text-slate-300">
            <span className="font-bold text-profit">Hands-free auto-deploy is ON</span> for {underlying}. Fly-High
            will deploy automatically when a valid VWAP crossover forms — within {cfg?.entry_start ?? '09:20'}–{cfg?.entry_cutoff ?? '14:30'},
            max {maxTrades} trades/day, one position per underlying. Paper mode.
          </div>
        </div>
      )}

      {/* ── Live P&L strip ── */}
      {(perf?.running_count > 0 || perf?.closed_count > 0) && (
        <div className="glass-card !rounded-2xl !p-4">
          <div className="flex flex-wrap items-center gap-6">
            <div>
              <div className="text-xs text-slate-400 mb-0.5">Net P&L (open + closed)</div>
              <div className={`text-2xl font-bold font-mono ${(perf?.net_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                {(perf?.net_pnl ?? 0) >= 0 ? '+' : ''}₹{(perf?.net_pnl ?? 0).toLocaleString('en-IN')}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-0.5">Open P&L ({perf?.running_count ?? 0} running)</div>
              <div className={`text-lg font-bold font-mono ${(perf?.open_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                {(perf?.open_pnl ?? 0) >= 0 ? '+' : ''}₹{(perf?.open_pnl ?? 0).toLocaleString('en-IN')}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-0.5">Realized P&L ({perf?.closed_count ?? 0} closed)</div>
              <div className={`text-lg font-bold font-mono ${(perf?.total_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                {(perf?.total_pnl ?? 0) >= 0 ? '+' : ''}₹{(perf?.total_pnl ?? 0).toLocaleString('en-IN')}
              </div>
            </div>
            {perf?.open_positions?.length > 0 && (
              <div className="flex-1 min-w-[200px]">
                <div className="text-xs text-slate-400 mb-1">Running positions</div>
                <div className="space-y-1">
                  {perf.open_positions.map((p, i) => (
                    <div key={i} className="flex items-center justify-between gap-2 text-xs bg-slate-800/30 rounded-lg px-2.5 py-1.5">
                      <span className={`font-bold ${p.side === 'SELL' ? 'text-loss' : 'text-profit'}`}>{p.side || 'BUY'}</span>
                      <span className="text-slate-300 truncate max-w-[120px]">{p.strike}{p.option_type}</span>
                      <span className="text-slate-500 font-mono">@{p.entry_price?.toFixed(1) || '—'}</span>
                      <span className="text-slate-400 font-mono">→ {p.ltp?.toFixed(1) || '—'}</span>
                      <span className={`font-mono font-bold ${p.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {p.pnl >= 0 ? '+' : ''}₹{p.pnl.toLocaleString('en-IN')}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── VWAP Status Bar ── */}
      <div className="glass-card !rounded-2xl !p-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <div>
              <div className="text-xs text-slate-400 mb-0.5">Spot</div>
              <div className="text-2xl font-bold font-mono">{spot ? spot.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—'}</div>
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-0.5">VWAP</div>
              <div className="text-2xl font-bold font-mono text-accent">{vwap ? vwap.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—'}</div>
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-0.5">Spot vs VWAP</div>
              <div className={`text-lg font-bold font-mono ${Number(spotVsVwap) > 0 ? 'text-profit' : Number(spotVsVwap) < 0 ? 'text-loss' : 'text-slate-400'}`}>
                {Number(spotVsVwap) > 0 ? '+' : ''}{spotVsVwap}%
              </div>
            </div>
          </div>

          {/* Signal + Deploy */}
          <div className="flex items-center gap-3">
            {hasSignal ? (
              <div className={`flex items-center gap-2 px-4 py-2 rounded-xl ${
                isBullish ? 'bg-profit/10 border border-profit/30' : 'bg-loss/10 border border-loss/30'
              }`}>
                {isBullish ? <TrendingUp className="w-5 h-5 text-profit" /> : <TrendingDown className="w-5 h-5 text-loss" />}
                <div>
                  <div className={`text-sm font-bold ${isBullish ? 'text-profit' : 'text-loss'}`}>
                    {direction} — {sig?.option_type} {sig?.strike}
                  </div>
                  <div className="text-xs text-slate-400">
                    SL: {sig?.sl_points?.toFixed(1)} pts | Target: 1:1 R:R | Premium: ₹{sig?.premium?.toFixed(1)}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800/50 border border-slate-700/50">
                <Clock className="w-5 h-5 text-slate-500" />
                <div>
                  <div className="text-sm font-medium text-slate-400">Waiting for crossover</div>
                  <div className="text-xs text-slate-500 max-w-[260px] truncate">{sig?.reason || 'Scanning...'}</div>
                </div>
              </div>
            )}

            <button
              onClick={handleDeploy}
              disabled={!hasSignal || deployMut.isPending}
              className={`px-5 py-3 rounded-xl font-bold text-sm transition-all ${
                hasSignal
                  ? 'bg-accent hover:bg-accent/80 text-black cursor-pointer'
                  : 'bg-slate-700/50 text-slate-500 cursor-not-allowed'
              }`}
            >
              {deployMut.isPending ? 'Deploying...' : '🚀 Deploy'}
            </button>
          </div>
        </div>
      </div>

      {/* ── Blockers ── */}
      {blockers.length > 0 && (
        <div className="glass-card !rounded-xl !p-3 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-slate-400 space-y-0.5">
            {blockers.map((b, i) => <div key={i}>• {b}</div>)}
          </div>
        </div>
      )}

      {/* ── Crossover Detail + Stats ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Crossover info */}
        <div className="glass-card !rounded-2xl !p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
            <Activity className="w-4 h-4 text-accent" />
            Crossover Detail
          </h3>
          {crossInfo.crossed ? (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-400">Direction</span>
                <span className={crossInfo.direction === 'BULLISH' ? 'text-profit font-bold' : 'text-loss font-bold'}>
                  {crossInfo.direction}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Candle Close</span>
                <span className="font-mono">{crossInfo.close?.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">VWAP at Close</span>
                <span className="font-mono text-accent">{crossInfo.vwap?.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">Prev Close</span>
                <span className="font-mono">{crossInfo.prev_close?.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">SL Level (prev candle {crossInfo.direction === 'BULLISH' ? 'low' : 'high'})</span>
                <span className="font-mono text-loss">
                  {crossInfo.direction === 'BULLISH' ? crossInfo.curr_candle_low?.toFixed(2) : crossInfo.curr_candle_high?.toFixed(2)}
                </span>
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-500 text-center py-6">
              No crossover detected on last closed 5-min candle
            </div>
          )}
        </div>

        {/* Performance */}
        <div className="glass-card !rounded-2xl !p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-accent" />
            Performance
          </h3>
          {perf && perf.closed_count > 0 ? (
            <div className="grid grid-cols-2 gap-3">
              <StatCard label="Win Rate" value={`${(perf.win_rate * 100).toFixed(0)}%`} icon={Target}
                color={perf.win_rate >= 0.5 ? 'text-profit' : 'text-loss'} />
              <StatCard label="Total P&L" value={`₹${perf.total_pnl?.toLocaleString('en-IN')}`} icon={Activity}
                color="text-profit" />
              <StatCard label="Avg R" value={perf.avg_r?.toFixed(2)} icon={Gauge}
                color={perf.avg_r > 0 ? 'text-profit' : 'text-loss'} />
              <StatCard label="Avg Hold" value={`${perf.avg_hold_minutes?.toFixed(0)}m`} icon={Clock}
                color="text-slate-300" />
            </div>
          ) : (
            <div className="text-sm text-slate-500 text-center py-6">
              {perf?.running_count ? `${perf.running_count} running — no closed trades yet` : 'No trades yet — deploy to start'}
            </div>
          )}
        </div>
      </div>

      {/* ── Strategy Rules ── */}
      <div className="glass-card !rounded-2xl !p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
            <Shield className="w-4 h-4 text-accent" />
            Strategy Rules
          </h3>
          <button onClick={() => setShowConfig(!showConfig)} className="text-xs text-accent hover:text-accent/80 flex items-center gap-1">
            {showConfig ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            {showConfig ? 'Hide' : 'Edit'} Config
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Entry</div>
            <div className="font-medium">5-min close crosses VWAP</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Gate</div>
            <div className="font-medium">ADX ≥ {cfg?.adx_min ?? 20}</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Strike</div>
            <div className="font-medium">1-ITM (high delta)</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">SL</div>
            <div className="font-medium">Prev candle low/high (max {cfg?.max_sl_points ?? 30}pts)</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Target</div>
            <div className="font-medium">Book {cfg?.book_partial_pct ?? 50}% @ 1:1 R:R</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Trail</div>
            <div className="font-medium">{cfg?.trail_giveback_pct ?? 30}% give-back from peak</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Max Hold</div>
            <div className="font-medium">{cfg?.max_hold_minutes ?? 45} min</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Window</div>
            <div className="font-medium">{cfg?.entry_start ?? '09:20'} – {cfg?.entry_cutoff ?? '14:30'}</div>
          </div>
        </div>

        {showConfig && (
          <div className="mt-4 pt-4 border-t border-slate-700/50 grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { key: 'risk_per_trade', label: 'Risk/Trade (₹)', type: 'number' },
              { key: 'adx_min', label: 'Min ADX', type: 'number' },
              { key: 'max_sl_points', label: 'Max SL Points', type: 'number' },
              { key: 'max_trades_per_day', label: 'Max Trades/Day', type: 'number' },
              { key: 'book_partial_pct', label: 'Book Partial %', type: 'number' },
              { key: 'trail_giveback_pct', label: 'Trail Give-back %', type: 'number' },
              { key: 'max_hold_minutes', label: 'Max Hold (min)', type: 'number' },
              { key: 'premium_floor_pct', label: 'Floor Stop %', type: 'number' },
            ].map(({ key, label, type }) => (
              <div key={key}>
                <label className="text-xs text-slate-500 block mb-1">{label}</label>
                <input
                  type={type}
                  value={localCfg[key] ?? cfg?.[key] ?? ''}
                  onChange={(e) => handleCfgChange(key, e.target.value, type)}
                  className="w-full bg-slate-800/50 border border-slate-700 rounded-lg px-2.5 py-1.5 text-sm font-mono focus:border-accent focus:outline-none"
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Recent Trades ── */}
      {perf?.recent?.length > 0 && (
        <div className="glass-card !rounded-2xl !p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">Recent Trades</h3>
          <div className="space-y-1.5">
            {perf.recent.map((t, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs bg-slate-800/30 rounded-lg px-3 py-2">
                <span className={`font-bold flex-shrink-0 ${t.side === 'SELL' ? 'text-loss' : 'text-profit'}`}>{t.side || 'BUY'}</span>
                <span className="text-slate-300 truncate max-w-[100px]">{t.strike}{t.option_type}</span>
                <span className="text-slate-500 font-mono flex-shrink-0">@{t.entry_price?.toFixed(1) || '—'} → {t.exit_price?.toFixed(1) || '—'}</span>
                <span className="text-slate-500 flex-shrink-0">{t.exit_reason}</span>
                <span className={`font-mono font-bold flex-shrink-0 ${(t.pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {(t.pnl ?? 0) >= 0 ? '+' : ''}₹{t.pnl?.toLocaleString('en-IN')}
                </span>
                <span className={`font-mono flex-shrink-0 ${t.r >= 0 ? 'text-profit' : 'text-loss'}`}>{t.r > 0 ? '+' : ''}{t.r}R</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Deploy success toast */}
      {deployMut.isSuccess && (
        <div className="fixed bottom-4 right-4 bg-profit/90 text-black px-4 py-2 rounded-xl font-bold text-sm animate-pulse">
          Fly-High deployed successfully!
        </div>
      )}
    </div>
  );
}

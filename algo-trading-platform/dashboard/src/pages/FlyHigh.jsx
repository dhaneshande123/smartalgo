import React, { useState, useEffect, useRef } from 'react';
import {
  Rocket, Gauge, Clock, AlertTriangle, Shield, Target, Activity,
  ChevronDown, ChevronUp, BarChart3, History, LogOut, Power, Square, CheckCircle2,
} from 'lucide-react';
import { useUnderlying } from '../context/UnderlyingContext';
import {
  useFlyHighSignal, useFlyHighConfig, useFlyHighPerformance,
  useSetFlyHighConfig, useDeployFlyHigh, useStopFlyHigh, useFlyHighTicker,
  useFlyHighSignalLog, useRiskMetrics, useDeactivateKillSwitch, useStopDeployedStrategy,
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

const fmt = (v, d = 1) => (v > 0 ? Number(v).toFixed(d) : '—');

// One option chart (1-ITM CE or PE): the three entry rules + live status
function ChartPanel({ side, panel, live, adxMin }) {
  const p = panel || {};
  const ltp = live?.ltp || p.ltp || 0;
  const vwap = p.vwap || live?.vwap || 0;
  const ready = !!p.ready;
  const rules = [
    { ok: p.cross === 'BULLISH', label: 'VWAP cross-up', detail: p.cross_type ? `${p.cross_type}` : p.above_vwap ? 'above VWAP, no fresh cross' : 'below VWAP' },
    { ok: p.st_dir === 'UP', label: 'SuperTrend UP', detail: p.st_dir ? `${p.st_dir} @ ${fmt(p.st_value)}` : '—' },
    { ok: (p.adx || 0) >= adxMin, label: `ADX ≥ ${adxMin}`, detail: p.adx ? Number(p.adx).toFixed(1) : '—' },
  ];
  return (
    <div className={`glass-card !rounded-2xl !p-4 ${ready ? 'border border-profit/40 bg-profit/5' : ''}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${side === 'CE' ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'}`}>{side}</span>
          <span className="font-mono font-bold">{p.strike || '—'}</span>
          <span className="text-[10px] text-slate-500">1-ITM · 5-min</span>
        </div>
        {ready
          ? <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-profit/20 text-profit">ENTRY SETUP</span>
          : <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-700/40 text-slate-400">WATCHING</span>}
      </div>
      <div className="flex items-end gap-5 mb-3">
        <div>
          <div className="text-[10px] text-slate-500">LTP</div>
          <div className="text-xl font-bold font-mono">{ltp ? `₹${Number(ltp).toFixed(2)}` : '—'}</div>
        </div>
        <div>
          <div className="text-[10px] text-slate-500">VWAP</div>
          <div className="text-lg font-bold font-mono text-accent">{vwap ? `₹${Number(vwap).toFixed(2)}` : '—'}</div>
        </div>
        <div>
          <div className="text-[10px] text-slate-500" title="Close of the last completed 5-min candle">Last close</div>
          <div className={`text-lg font-bold font-mono ${p.above_vwap ? 'text-profit' : 'text-loss'}`}>{fmt(p.close, 2)}</div>
        </div>
      </div>
      <div className="space-y-1 text-xs">
        {rules.map((r) => (
          <div key={r.label} className="flex items-center justify-between bg-slate-800/30 rounded-lg px-2.5 py-1.5">
            <span className={r.ok ? 'text-profit' : 'text-slate-400'}>{r.ok ? '✓' : '✗'} {r.label}</span>
            <span className="font-mono text-slate-400">{r.detail}</span>
          </div>
        ))}
      </div>
      {p.signal_candle && (
        <div className="mt-2 text-[11px] text-slate-500">
          Last candle {p.signal_candle.time}: L {fmt(p.signal_candle.low)} · H {fmt(p.signal_candle.high)}
          {ready && <span className="text-loss ml-1">→ SL = {fmt(p.signal_candle.low)}</span>}
        </div>
      )}
      {!ready && p.blockers?.length > 0 && !p.blockers.every((b) => /cross-up|SuperTrend|ADX/.test(b)) && (
        <div className="mt-2 text-[11px] text-yellow-400/80">{p.blockers.filter((b) => !/cross-up|SuperTrend|ADX/.test(b)).join(' · ')}</div>
      )}
    </div>
  );
}

export default function FlyHigh() {
  const { underlying } = useUnderlying();
  const { data: sig } = useFlyHighSignal(underlying);
  const { data: cfg } = useFlyHighConfig();
  const { data: perf } = useFlyHighPerformance();
  const { data: ticker } = useFlyHighTicker(underlying);
  const { data: logData } = useFlyHighSignalLog(underlying);
  const deployMut = useDeployFlyHigh();
  const stopMut = useStopFlyHigh();
  const configMut = useSetFlyHighConfig();
  const { data: riskData } = useRiskMetrics();
  const deactivateKill = useDeactivateKillSwitch();
  const exitPosition = useStopDeployedStrategy();
  const isKillActive = !!riskData?.kill_switch_active;
  const [showConfig, setShowConfig] = useState(false);
  const [logFilter, setLogFilter] = useState('missed');
  const [localCfg, setLocalCfg] = useState({});
  const debounceRef = useRef(null);
  useEffect(() => { if (cfg) setLocalCfg(prev => Object.keys(prev).length ? prev : { ...cfg }); }, [cfg]);
  const handleCfgChange = (key, value, type) => {
    const parsed = type === 'number' ? Number(value) : value;
    setLocalCfg(prev => ({ ...prev, [key]: parsed }));
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => configMut.mutate({ [key]: parsed }), 600);
  };

  // perf.armed lists only armed symbols and is fresh (5s); the signal response is cached 15s
  const armed = perf ? !!perf.armed?.[underlying] : !!sig?.armed;
  const busy = deployMut.isPending || stopMut.isPending;
  const hasSignal = sig?.has_signal;
  const spot = ticker?.spot || sig?.spot || 0;
  const tradesToday = sig?.trades_today ?? 0;
  const maxTrades = cfg?.max_trades_per_day ?? 2;
  const adxMin = cfg?.adx_min ?? 10;
  const openPositions = perf?.open_positions || [];
  const signals = logData?.signals || [];
  const missedCount = signals.filter((s) => s.missed).length;
  const takenCount = signals.filter((s) => s.taken).length;
  const shownSignals = signals.filter((s) =>
    logFilter === 'missed' ? s.missed : logFilter === 'taken' ? s.taken : logFilter === 'blocked' ? s.blocked : true);
  // Only the non-chart blockers (window, cap, conflict…) — chart rules are shown per panel
  const globalBlockers = (sig?.blockers || []).filter((b) => b !== 'no entry setup on CE or PE chart');

  let status;
  if (!armed) status = { text: 'Not deployed', sub: 'Signals are shown, but no trades will be taken', cls: 'text-slate-400' };
  else if (isKillActive) status = { text: 'Deployed — blocked by kill switch', sub: 'Reset the kill switch to allow entries', cls: 'text-red-400' };
  else if (openPositions.length) status = { text: 'Deployed — managing position', sub: 'SL / 1:1 target / SuperTrend trail are enforced automatically', cls: 'text-profit' };
  else if (tradesToday >= maxTrades) status = { text: 'Deployed — daily trade cap reached', sub: `${tradesToday}/${maxTrades} trades today`, cls: 'text-yellow-400' };
  else status = { text: 'Deployed — waiting for signal', sub: `Enters automatically on the first valid CE/PE setup (${cfg?.entry_start ?? '09:20'}–${cfg?.entry_cutoff ?? '15:15'})`, cls: 'text-profit' };

  return (
    <div className="space-y-4 max-w-6xl mx-auto">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Rocket className="w-6 h-6 text-accent" />
          <h1 className="text-xl font-bold">Fly-High Strategy</h1>
          <span className="text-xs text-slate-400 bg-slate-800/50 px-2 py-0.5 rounded-full">CE / PE chart · VWAP + SuperTrend + ADX</span>
        </div>
        <div className="flex items-center gap-2 ml-auto flex-wrap">
          <span className={`px-3 py-1.5 rounded-full text-xs font-bold ${
            tradesToday >= maxTrades ? 'bg-red-500/15 text-red-400' : 'bg-slate-700/40 text-slate-400'
          }`}>
            {tradesToday}/{maxTrades} trades today
          </span>
          <span className="px-3 py-1.5 rounded-full text-xs font-bold bg-accent/15 text-accent">
            {underlying} {spot ? spot.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : ''}
          </span>
          <button
            onClick={isKillActive ? () => deactivateKill.mutate('manual reset via Fly-High') : undefined}
            disabled={!isKillActive || deactivateKill.isPending}
            title={isKillActive ? 'Kill switch is ON — all entries blocked. Click to reset.' : 'Kill switch is OFF — entries allowed.'}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold transition-all ${
              isKillActive
                ? 'bg-red-500/20 text-red-400 ring-1 ring-red-500/40 cursor-pointer hover:bg-red-500/30'
                : 'bg-slate-700/40 text-slate-400 cursor-default'
            }`}
          >
            <Shield className="w-3.5 h-3.5" />
            {deactivateKill.isPending ? 'Resetting…' : isKillActive ? 'KILL SWITCH ON — Click to Reset' : 'Kill Switch OFF'}
          </button>
        </div>
      </div>

      {/* ── Kill switch banner ── */}
      {isKillActive && (
        <div className="glass-card !rounded-xl !p-3 flex items-center gap-3 bg-red-500/10 border border-red-500/30">
          <Shield className="w-5 h-5 text-red-400 flex-shrink-0" />
          <div className="flex-1 text-xs text-slate-300">
            <span className="font-bold text-red-400">Kill Switch is ACTIVE</span> — Fly-High will not take new entries.
            {riskData?.kill_switch_meta?.reason && (
              <span className="text-slate-500 ml-1">Reason: {riskData.kill_switch_meta.reason}</span>
            )}
          </div>
          <button
            onClick={() => deactivateKill.mutate('manual reset via Fly-High')}
            disabled={deactivateKill.isPending}
            className="px-3 py-1.5 rounded-lg text-xs font-bold bg-red-500/20 text-red-300 hover:bg-red-500/30 border border-red-500/30 transition-all disabled:opacity-50"
          >
            {deactivateKill.isPending ? 'Resetting…' : 'Reset Kill Switch'}
          </button>
        </div>
      )}

      {/* ── Deploy control ── */}
      <div className={`glass-card !rounded-2xl !p-4 ${armed ? 'border border-profit/30' : ''}`}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className={`w-3 h-3 rounded-full ${armed ? 'bg-profit animate-pulse' : 'bg-slate-600'}`} />
            <div>
              <div className={`text-base font-bold ${status.cls}`}>{status.text}</div>
              <div className="text-xs text-slate-500">{status.sub}</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {hasSignal && (
              <div className="text-xs text-right">
                <div className="font-bold text-profit">Signal: BUY {sig?.strike}{sig?.option_type} @ ₹{fmt(sig?.premium)}</div>
                <div className="text-slate-400">SL ₹{fmt(sig?.sl_premium)} · TGT ₹{fmt(sig?.target_premium)} · {sig?.lots} lot{sig?.lots > 1 ? 's' : ''}</div>
              </div>
            )}
            {armed ? (
              <button
                onClick={() => stopMut.mutate({ symbol: underlying })}
                disabled={busy}
                title="Stop new entries for today. An open position stays managed (SL / target / trail) and can be exited below."
                className="flex items-center gap-2 px-5 py-3 rounded-xl font-bold text-sm bg-red-500/15 text-red-400 hover:bg-red-500/25 border border-red-500/30 transition-all disabled:opacity-50"
              >
                <Square className="w-4 h-4" />
                {stopMut.isPending ? 'Stopping…' : 'Stop Strategy'}
              </button>
            ) : (
              <button
                onClick={() => deployMut.mutate({ symbol: underlying })}
                disabled={busy}
                title="Deploy for today: the backend watches the CE/PE charts and enters/exits on its own. Re-deploy each trading day."
                className="flex items-center gap-2 px-5 py-3 rounded-xl font-bold text-sm bg-accent hover:bg-accent/80 text-black transition-all disabled:opacity-50"
              >
                <Power className="w-4 h-4" />
                {deployMut.isPending ? 'Deploying…' : 'Deploy Strategy'}
              </button>
            )}
          </div>
        </div>
        {globalBlockers.length > 0 && (
          <div className="mt-3 flex items-start gap-2 text-xs text-slate-400">
            <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0" />
            <div className="space-y-0.5">{globalBlockers.map((b, i) => <div key={i}>{b}</div>)}</div>
          </div>
        )}
        {(deployMut.isError || stopMut.isError) && (
          <div className="mt-2 text-xs text-loss">
            {(deployMut.error || stopMut.error)?.response?.data?.detail || (deployMut.error || stopMut.error)?.message}
          </div>
        )}
      </div>

      {/* ── CE / PE charts ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ChartPanel side="CE" panel={sig?.ce} live={ticker?.ce} adxMin={adxMin} />
        <ChartPanel side="PE" panel={sig?.pe} live={ticker?.pe} adxMin={adxMin} />
      </div>

      {/* ── Live P&L + running positions ── */}
      {(perf?.running_count > 0 || perf?.closed_count > 0) && (
        <div className="glass-card !rounded-2xl !p-4">
          <div className="flex flex-wrap items-center gap-6">
            <div>
              <div className="text-xs text-slate-400 mb-0.5">Today's Net P&L</div>
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
            {openPositions.length > 0 && (
              <div className="flex-1 min-w-[260px]">
                <div className="text-xs text-slate-400 mb-1">Running positions</div>
                <div className="space-y-1">
                  {openPositions.map((p) => (
                    <div key={p.strategy_id} className="flex flex-wrap items-center justify-between gap-2 text-xs bg-slate-800/30 rounded-lg px-2.5 py-1.5">
                      <span className="font-bold text-profit">BUY</span>
                      <span className="text-slate-300">{p.strike}{p.option_type}</span>
                      <span className="text-accent font-mono">{p.lots || 1}L{p.lot_size ? ` (${p.qty})` : ''}</span>
                      <span className="text-slate-500 font-mono">@{fmt(p.entry_price)}</span>
                      <span className="text-slate-300 font-mono">→ {fmt(p.ltp)}</span>
                      {p.sl_premium > 0 && <span className="text-loss font-mono text-[10px]" title="Stop loss: signal-candle low on the option chart">SL:{fmt(p.sl_premium)}</span>}
                      {p.target_premium > 0 && (
                        <span className={`font-mono text-[10px] ${p.booked ? 'text-slate-500 line-through' : 'text-profit'}`} title="1:1 target — books partial">
                          TGT:{fmt(p.target_premium)}
                        </span>
                      )}
                      {p.booked && (
                        <span className="text-yellow-400 font-mono text-[10px]" title="Runner trails the option SuperTrend line (never below entry)">
                          TRAIL:{p.trail_sl > p.entry_price ? fmt(p.trail_sl) : 'BE'}
                        </span>
                      )}
                      <span className={`font-mono font-bold ${p.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {p.pnl >= 0 ? '+' : ''}₹{p.pnl.toLocaleString('en-IN')}
                      </span>
                      <button
                        onClick={() => exitPosition.mutate(p.strategy_id)}
                        disabled={exitPosition.isPending}
                        title="Exit this position now (square off)"
                        className="flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold bg-red-500/15 text-red-400 hover:bg-red-500/25 border border-red-500/20 transition-all disabled:opacity-50"
                      >
                        <LogOut className="w-3 h-3" />
                        {exitPosition.isPending ? 'Exiting…' : 'Exit'}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Today's signals: missed / traded / blocked (recorded live, kept for the evening) ── */}
      <div className="glass-card !rounded-2xl !p-4">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
            <History className="w-4 h-4 text-accent" />
            Today's Signals
          </h3>
          <div className="flex items-center gap-1 ml-2">
            {[
              { key: 'missed', label: `Missed (${missedCount})` },
              { key: 'taken', label: `Traded (${takenCount})` },
              { key: 'blocked', label: 'Blocked' },
              { key: 'all', label: `All (${signals.length})` },
            ].map((f) => (
              <button
                key={f.key}
                onClick={() => setLogFilter(f.key)}
                className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all ${
                  logFilter === f.key ? 'bg-accent/20 text-accent' : 'bg-slate-800/40 text-slate-400 hover:text-slate-200'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <span className="text-xs text-slate-500 font-normal ml-auto"
            title="Recorded live during market hours and saved, so you can review after the close. Outcome = what a trade with signal-candle-low SL and 1:1 target would have done.">
            Saved for after-market review · read-only
          </span>
        </div>
        {shownSignals.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-slate-700/50">
                  <th className="text-left py-1.5 px-2 font-medium">Time</th>
                  <th className="text-left py-1.5 px-2 font-medium">Contract</th>
                  <th className="text-right py-1.5 px-2 font-medium">Entry ₹</th>
                  <th className="text-right py-1.5 px-2 font-medium">SL ₹</th>
                  <th className="text-right py-1.5 px-2 font-medium">Target ₹</th>
                  <th className="text-right py-1.5 px-2 font-medium">ADX</th>
                  <th className="text-center py-1.5 px-2 font-medium">ST</th>
                  <th className="text-center py-1.5 px-2 font-medium">Outcome</th>
                  <th className="text-right py-1.5 px-2 font-medium" title="Premium points per unit: exit − entry">Result</th>
                  <th className="text-left py-1.5 px-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {shownSignals.map((s, i) => (
                  <tr key={i} className={`border-b border-slate-800/30 hover:bg-slate-800/20 ${s.blocked ? 'opacity-60' : ''}`}>
                    <td className="py-2 px-2 font-mono text-slate-300">{s.time}</td>
                    <td className={`py-2 px-2 font-mono font-bold ${s.option_type === 'CE' ? 'text-profit' : 'text-loss'}`}>{s.strike} {s.option_type}</td>
                    <td className="py-2 px-2 text-right font-mono text-slate-300">{fmt(s.entry_premium)}</td>
                    <td className="py-2 px-2 text-right font-mono text-loss">{fmt(s.sl_premium)}</td>
                    <td className="py-2 px-2 text-right font-mono text-profit">{fmt(s.target_premium)}</td>
                    <td className="py-2 px-2 text-right font-mono text-slate-400">{s.adx ? Number(s.adx).toFixed(0) : '—'}</td>
                    <td className={`py-2 px-2 text-center font-mono ${s.supertrend_dir === 'UP' ? 'text-profit' : 'text-loss'}`}>{s.supertrend_dir || '—'}</td>
                    <td className="py-2 px-2 text-center">
                      {s.blocked ? <span className="text-slate-500">—</span> : (
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                          s.outcome === 'Target Hit' ? 'bg-profit/15 text-profit' :
                          s.outcome === 'SL Hit' ? 'bg-loss/15 text-loss' :
                          'bg-yellow-500/15 text-yellow-400'
                        }`}>
                          {s.outcome === 'open' ? 'Open' : s.outcome}{s.exit_time ? ` @ ${s.exit_time}` : ''}
                        </span>
                      )}
                    </td>
                    <td className={`py-2 px-2 text-right font-mono ${s.blocked || !s.pnl_points ? 'text-slate-500' : s.pnl_points > 0 ? 'text-profit' : 'text-loss'}`}>
                      {s.blocked || !s.pnl_points ? '—' : `${s.pnl_points > 0 ? '+' : ''}${Number(s.pnl_points).toFixed(1)} pts`}
                    </td>
                    <td className="py-2 px-2">
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                        s.taken ? 'bg-blue-500/15 text-blue-400' :
                        s.blocked ? 'bg-red-500/15 text-red-400' : 'bg-orange-500/15 text-orange-400'
                      }`}>
                        {s.taken ? <span className="inline-flex items-center gap-1"><CheckCircle2 className="w-3 h-3" />Traded</span>
                          : s.blocked ? s.blocked_reason
                          : `Missed${s.missed_reason ? ` — ${s.missed_reason}` : ''}`}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-sm text-slate-500 text-center py-4">
            {signals.length > 0
              ? `No ${logFilter} signals today`
              : logData?.reason || 'No VWAP cross-ups on the CE/PE charts yet today'}
          </div>
        )}
      </div>

      {/* ── Performance ── */}
      <div className="glass-card !rounded-2xl !p-4">
        <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-accent" />
          Performance (today)
        </h3>
        {perf && perf.closed_count > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Win Rate" value={`${(perf.win_rate * 100).toFixed(0)}%`} icon={Target}
              color={perf.win_rate >= 0.5 ? 'text-profit' : 'text-loss'} />
            <StatCard label="Total P&L" value={`₹${perf.total_pnl?.toLocaleString('en-IN')}`} icon={Activity}
              color={perf.total_pnl >= 0 ? 'text-profit' : 'text-loss'} />
            <StatCard label="Avg R" value={perf.avg_r?.toFixed(2)} icon={Gauge}
              color={perf.avg_r > 0 ? 'text-profit' : 'text-loss'} />
            <StatCard label="Avg Hold" value={`${perf.avg_hold_minutes?.toFixed(0)}m`} icon={Clock}
              color="text-slate-300" />
          </div>
        ) : (
          <div className="text-sm text-slate-500 text-center py-4">
            {perf?.running_count ? `${perf.running_count} running — no closed trades yet` : 'No trades yet today'}
          </div>
        )}
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
            <div className="text-slate-500">Charts</div>
            <div className="font-medium">1-ITM CE & PE, 5-min</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Entry</div>
            <div className="font-medium">VWAP cross-up + SuperTrend UP + ADX ≥ {adxMin}</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">SL</div>
            <div className="font-medium">Signal candle low (option chart)</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Target</div>
            <div className="font-medium">{(cfg?.book_partial_pct ?? 50) >= 100 ? 'Full exit @ 1:1' : `Book ${cfg?.book_partial_pct ?? 50}% @ 1:1`}</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Trail (runner)</div>
            <div className="font-medium">Option SuperTrend line, never below entry</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Conflict</div>
            <div className="font-medium">CE & PE same candle → skip</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Max Hold</div>
            <div className="font-medium">{cfg?.max_hold_minutes ?? 45} min</div>
          </div>
          <div className="bg-slate-800/30 rounded-lg p-2.5">
            <div className="text-slate-500">Window</div>
            <div className="font-medium">{cfg?.entry_start ?? '09:20'} – {cfg?.entry_cutoff ?? '15:15'}, max {maxTrades}/day</div>
          </div>
        </div>

        {showConfig && (
          <div className="mt-4 pt-4 border-t border-slate-700/50 grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { key: 'risk_per_trade', label: 'Risk/Trade (₹)', type: 'number' },
              { key: 'adx_min', label: 'Min ADX', type: 'number' },
              { key: 'max_lots', label: 'Max Lots', type: 'number' },
              { key: 'max_trades_per_day', label: 'Max Trades/Day', type: 'number' },
              { key: 'supertrend_period', label: 'SuperTrend Period', type: 'number' },
              { key: 'supertrend_multiplier', label: 'SuperTrend Mult', type: 'number' },
              { key: 'book_partial_pct', label: 'Book at Target % (100 = full exit)', type: 'number' },
              { key: 'trail_giveback_pct', label: 'Fallback Give-back %', type: 'number' },
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

      {/* ── Today's Trades ── */}
      {perf?.recent?.length > 0 && (
        <div className="glass-card !rounded-2xl !p-4">
          <h3 className="text-sm font-semibold text-slate-300 mb-3">Today's Trades</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-slate-700/50">
                  <th className="text-left py-1.5 px-2 font-medium">Exit</th>
                  <th className="text-left py-1.5 px-2 font-medium">Instrument</th>
                  <th className="text-right py-1.5 px-2 font-medium">Qty</th>
                  <th className="text-right py-1.5 px-2 font-medium">Entry ₹</th>
                  <th className="text-right py-1.5 px-2 font-medium">Exit ₹</th>
                  <th className="text-right py-1.5 px-2 font-medium">SL ₹</th>
                  <th className="text-right py-1.5 px-2 font-medium">Target ₹</th>
                  <th className="text-center py-1.5 px-2 font-medium">Outcome</th>
                  <th className="text-right py-1.5 px-2 font-medium">P&L</th>
                  <th className="text-right py-1.5 px-2 font-medium">R</th>
                </tr>
              </thead>
              <tbody>
                {perf.recent.map((t, i) => {
                  const instrument = t.symbol || (t.strike ? `${t.underlying || 'NIFTY'} ${t.strike} ${t.option_type}` : `${t.option_type || '—'}`);
                  const exitTime = t.exited_at ? new Date(t.exited_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '—';
                  const outcome = t.outcome || t.exit_reason || '—';
                  return (
                    <tr key={i} className="border-b border-slate-800/30 hover:bg-slate-800/20">
                      <td className="py-1.5 px-2 text-slate-500 font-mono text-[10px]">{exitTime}</td>
                      <td className="py-1.5 px-2 text-slate-300 font-mono">{instrument}</td>
                      <td className="py-1.5 px-2 text-right text-accent font-mono">{t.lots || 1}L ({t.qty || '—'})</td>
                      <td className="py-1.5 px-2 text-right text-slate-300 font-mono">{t.entry_price > 0 ? `₹${t.entry_price.toFixed(2)}` : '—'}</td>
                      <td className="py-1.5 px-2 text-right text-slate-300 font-mono">{t.exit_price > 0 ? `₹${t.exit_price.toFixed(2)}` : '—'}</td>
                      <td className="py-1.5 px-2 text-right text-loss font-mono">{fmt(t.sl_premium)}</td>
                      <td className="py-1.5 px-2 text-right text-profit font-mono">{fmt(t.target_premium)}</td>
                      <td className="py-1.5 px-2 text-center">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                          outcome === 'Target Hit' ? 'bg-profit/15 text-profit' :
                          outcome === 'SL Hit' ? 'bg-loss/15 text-loss' :
                          'bg-slate-700/40 text-slate-400'
                        }`}>{outcome}</span>
                      </td>
                      <td className={`py-1.5 px-2 text-right font-mono font-bold ${(t.pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {(t.pnl ?? 0) >= 0 ? '+' : ''}₹{t.pnl?.toLocaleString('en-IN')}
                      </td>
                      <td className={`py-1.5 px-2 text-right font-mono ${(t.r ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {t.r > 0 ? '+' : ''}{t.r}R
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

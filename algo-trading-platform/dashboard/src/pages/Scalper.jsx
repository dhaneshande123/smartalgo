import { useState } from 'react';
import {
  Zap, TrendingUp, TrendingDown, Target, ShieldAlert, Clock,
  Layers, CheckCircle2, XCircle, Rocket, Activity, AlertTriangle, Gauge,
  BarChart3, Trophy,
} from 'lucide-react';
import Card from '../components/common/Card';
import { useUnderlying } from '../context/UnderlyingContext';
import { useToast } from '../components/common/ToastProvider';
import {
  useScalperSignal, useScalperConfig, useSetScalperConfig, useDeployScalp,
  useDeployedStrategies, useScalperPerformance,
} from '../hooks/useApi';

// ── Level source → colour/label ──
const SOURCE_META = {
  cpr: { label: 'CPR', color: 'text-purple-400' },
  prev_day: { label: 'Prev Day', color: 'text-blue-400' },
  vwap: { label: 'VWAP', color: 'text-cyan-400' },
  orb: { label: 'ORB', color: 'text-amber-400' },
  round: { label: 'Round', color: 'text-slate-400' },
  oi: { label: 'OI Wall', color: 'text-pink-400' },
};

function fmt(n, d = 0) {
  if (n == null || isNaN(n)) return '--';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d });
}

// ════════════════════════════════════════════════════════════════════
// S/R level rail — visualizes levels around spot
// ════════════════════════════════════════════════════════════════════
function LevelRail({ levels = [], spot }) {
  if (!levels.length || !spot) {
    return <div className="text-center py-6 text-sm text-slate-500">Levels load when market data is live</div>;
  }
  const prices = levels.map((l) => l.price).concat(spot);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const pos = (p) => `${((max - p) / range) * 100}%`;

  // Sort for the side list (resistance high → support low)
  const sorted = [...levels].sort((a, b) => b.price - a.price);

  return (
    <div className="flex gap-4">
      {/* visual rail */}
      <div className="relative w-40 flex-shrink-0" style={{ height: 280 }}>
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-slate-700/50" />
        {levels.map((l, i) => {
          const isRes = l.kind === 'resistance';
          const isPivot = l.kind === 'pivot';
          return (
            <div key={i} className="absolute left-0 right-0 flex items-center gap-1" style={{ top: pos(l.price), transform: 'translateY(-50%)' }}>
              <div className={`flex-1 h-px ${isPivot ? 'bg-purple-500/40' : isRes ? 'bg-loss/40' : 'bg-profit/40'}`} />
              <span className={`text-[9px] font-mono ${isPivot ? 'text-purple-400' : isRes ? 'text-loss' : 'text-profit'}`}>
                {fmt(l.price)}
              </span>
            </div>
          );
        })}
        {/* spot marker */}
        <div className="absolute left-0 right-0 flex items-center gap-1 z-10" style={{ top: pos(spot), transform: 'translateY(-50%)' }}>
          <div className="flex-1 h-0.5 bg-accent" />
          <span className="text-[10px] font-mono font-bold text-accent bg-terminal-bg px-1 rounded">{fmt(spot, 2)}</span>
        </div>
      </div>

      {/* level list */}
      <div className="flex-1 space-y-1 overflow-y-auto" style={{ maxHeight: 280 }}>
        {sorted.map((l, i) => {
          const meta = SOURCE_META[l.source] || { label: l.source, color: 'text-slate-400' };
          const above = l.price >= spot;
          return (
            <div key={i} className="flex items-center justify-between text-xs py-1 px-2 rounded bg-slate-800/30">
              <div className="flex items-center gap-2">
                <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded bg-slate-700/40 ${meta.color}`}>{meta.label}</span>
                <span className="text-slate-300">{l.name}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono text-slate-400">{fmt(l.price)}</span>
                <span className={`text-[9px] ${above ? 'text-loss' : 'text-profit'}`}>{above ? '▲ R' : '▼ S'}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Performance + exit attribution
// ════════════════════════════════════════════════════════════════════
const TONE_COLOR = { profit: '#34D399', loss: '#F87171', neutral: '#94a3b8' };

function PerfStat({ label, value, tone = 'neutral', sub }) {
  return (
    <div className="rounded-xl bg-slate-800/30 px-3 py-2.5">
      <div className="text-[9px] text-slate-500 uppercase font-semibold tracking-wide">{label}</div>
      <div className="text-lg font-bold font-mono" style={{ color: TONE_COLOR[tone] }}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function ScalpPerformance({ perf }) {
  if (!perf || perf.closed_count === 0) {
    return (
      <Card title="Scalp Performance">
        <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 10 }}>
          Win rate, average R, and which exit fired — populates once scalps close
        </div>
        <div className="text-center py-6">
          <BarChart3 className="w-8 h-8 text-slate-600 mx-auto mb-2" />
          <div className="text-sm text-slate-500">No closed scalps yet</div>
          <div className="text-xs text-slate-600 mt-1">
            {perf?.running_count ? `${perf.running_count} running — stats appear when they exit` : 'Deploy a scalp to start tracking'}
          </div>
        </div>
      </Card>
    );
  }

  const wr = Math.round((perf.win_rate || 0) * 100);
  const pnlTone = 'profit';
  const rTone = perf.avg_r > 0 ? 'profit' : perf.avg_r < 0 ? 'loss' : 'neutral';
  const maxBucket = Math.max(1, ...(perf.exit_breakdown || []).map((b) => b.count));

  return (
    <Card title="Scalp Performance">
      <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 10 }}>
        Across {perf.closed_count} closed scalp{perf.closed_count === 1 ? '' : 's'} · risk ₹{fmt(perf.risk_per_trade)}/trade
        {perf.running_count ? ` · ${perf.running_count} running` : ''}
      </div>

      {/* Headline stats */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-4">
        <PerfStat label="Net P&L" value={`${perf.total_pnl >= 0 ? '+' : ''}₹${fmt(perf.total_pnl)}`} tone={pnlTone} />
        <PerfStat label="Win Rate" value={`${wr}%`} tone={wr >= 50 ? 'profit' : wr >= 40 ? 'neutral' : 'loss'} sub={`${perf.wins}W / ${perf.losses}L`} />
        <PerfStat label="Avg R" value={`${perf.avg_r >= 0 ? '+' : ''}${perf.avg_r}R`} tone={rTone} sub="per trade" />
        <PerfStat label="Profit Factor" value={perf.profit_factor} tone={perf.profit_factor >= 1 ? 'profit' : 'loss'} />
        <PerfStat label="Expectancy" value={`${perf.expectancy >= 0 ? '+' : ''}₹${fmt(perf.expectancy)}`} tone={perf.expectancy >= 0 ? 'profit' : 'loss'} sub="per trade" />
        <PerfStat label="Avg Hold" value={`${Math.round(perf.avg_hold_minutes)}m`} tone="neutral" />
      </div>

      {/* Exit attribution — the key insight */}
      <div className="rounded-xl border border-slate-700/30 p-3">
        <div className="text-[11px] font-semibold text-slate-400 uppercase mb-2 flex items-center gap-1.5">
          <Target className="w-3 h-3" /> Which exit fired
        </div>
        <div className="space-y-1.5">
          {(perf.exit_breakdown || []).map((b) => (
            <div key={b.bucket} className="flex items-center gap-2">
              <div className="w-32 flex-shrink-0 text-xs text-slate-300">{b.label}</div>
              <div className="flex-1 h-5 rounded bg-slate-800/40 relative overflow-hidden">
                <div className="h-full rounded transition-all" style={{
                  width: `${(b.count / maxBucket) * 100}%`,
                  background: `${TONE_COLOR[b.tone]}33`,
                  borderRight: `2px solid ${TONE_COLOR[b.tone]}`,
                }} />
                <div className="absolute inset-0 flex items-center px-2 gap-2 text-[10px]">
                  <span className="text-slate-300 font-semibold">{b.count}×</span>
                  <span className="text-slate-500">{Math.round(b.win_rate * 100)}% win</span>
                </div>
              </div>
              <div className="w-20 text-right text-xs font-mono font-semibold text-profit">
                {b.pnl >= 0 ? '+' : ''}₹{fmt(b.pnl)}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Best / worst */}
      {(perf.best || perf.worst) && (
        <div className="grid grid-cols-2 gap-2 mt-3">
          {perf.best && (
            <div className="rounded-lg bg-profit/5 border border-profit/15 px-3 py-2">
              <div className="text-[9px] text-profit/70 uppercase font-semibold flex items-center gap-1"><Trophy className="w-2.5 h-2.5" /> Best</div>
              <div className="text-sm font-bold text-profit font-mono">+₹{fmt(perf.best.pnl)}</div>
              <div className="text-[10px] text-slate-500 truncate">{perf.best.name} · {perf.best.bucket}</div>
            </div>
          )}
          {perf.worst && (
            <div className="rounded-lg bg-loss/5 border border-loss/15 px-3 py-2">
              <div className="text-[9px] text-loss/70 uppercase font-semibold">Worst</div>
              <div className="text-sm font-bold text-loss font-mono">₹{fmt(perf.worst.pnl)}</div>
              <div className="text-[10px] text-slate-500 truncate">{perf.worst.name} · {perf.worst.bucket}</div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main Scalper page
// ════════════════════════════════════════════════════════════════════
export default function Scalper() {
  const { underlying } = useUnderlying();
  const toast = useToast();
  const { data: sig } = useScalperSignal(underlying);
  const { data: cfg } = useScalperConfig();
  const setConfig = useSetScalperConfig();
  const deployScalp = useDeployScalp();
  const { data: deployedData } = useDeployedStrategies();
  const { data: perf } = useScalperPerformance();

  const [deploying, setDeploying] = useState(false);

  // Active scalps (running scalper strategies)
  const scalps = (deployedData?.strategies || []).filter(
    (s) => s.risk_params?.scalp && s.status === 'RUNNING'
  );

  const hasSignal = sig?.has_signal;
  const isExpiry = sig?.is_expiry;
  const blockers = sig?.blockers || [];

  const handleDeploy = async (force = false) => {
    setDeploying(true);
    try {
      await deployScalp.mutateAsync({ symbol: underlying, force });
      toast?.addToast?.({ level: 'success', message: `Scalp deployed on ${underlying}`, source: 'scalper' });
    } catch (e) {
      toast?.addToast?.({ level: 'WARNING', message: e?.response?.data?.detail || 'Deploy failed', source: 'scalper' });
    } finally {
      setDeploying(false);
    }
  };

  const toggleExpiryOnly = () => {
    if (cfg) setConfig.mutate({ expiry_only: !cfg.expiry_only });
  };

  const autoOn = cfg?.auto_deploy || false;
  const toggleAuto = () => {
    if (cfg) setConfig.mutate({ auto_deploy: !autoOn, auto_symbols: [underlying] });
  };

  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      {/* ── Header ── */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Zap className="w-5 h-5 text-yellow-400" /> Expiry Scalper
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            S/R-driven OTM option buying — catch the 1₹→50₹ gamma moves on expiry. Paper mode.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Hands-free auto-arm toggle */}
          <button
            onClick={toggleAuto}
            disabled={setConfig.isPending}
            title={autoOn
              ? `Auto-arm ON for ${cfg?.auto_symbols?.join(', ') || underlying} — deploys a scalp automatically the instant a confirmed signal forms (strict gate, max ${cfg?.max_trades_per_day ?? 8} trades/day, kill-switch aware)`
              : 'Turn on hands-free auto-arm: the scalper deploys by itself when a confirmed signal forms'}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold border transition-all ${
              autoOn ? 'bg-profit/15 text-profit border-profit/30' : 'bg-slate-700/40 text-slate-400 border-white/5 hover:text-slate-200'
            }`}>
            <span className={`relative w-7 h-4 rounded-full transition-colors ${autoOn ? 'bg-profit/40' : 'bg-slate-600/50'}`}>
              <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${autoOn ? 'left-3.5' : 'left-0.5'}`} />
            </span>
            {autoOn ? `AUTO-ARM ON (${(cfg?.auto_symbols || [underlying]).join(',')})` : 'AUTO-ARM OFF'}
          </button>
          <span className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold ${
            isExpiry ? 'bg-yellow-500/15 text-yellow-400' : 'bg-slate-700/40 text-slate-400'
          }`}>
            <Clock className="w-3.5 h-3.5" />
            {isExpiry ? 'EXPIRY DAY' : 'Not Expiry'}
          </span>
          <span className="px-3 py-1.5 rounded-full text-xs font-bold bg-accent/15 text-accent">{underlying}</span>
        </div>
      </div>

      {/* ── Auto-arm active banner ── */}
      {autoOn && (
        <div className="glass-card !rounded-2xl !p-3 flex items-center gap-3 !border-profit/20">
          <Zap className="w-4 h-4 text-profit flex-shrink-0" />
          <div className="flex-1 text-xs text-slate-300">
            <span className="font-semibold text-profit">Hands-free auto-arm is ON</span> for {(cfg?.auto_symbols || [underlying]).join(', ')}.
            The scalper will deploy automatically when a confirmed signal forms — within {cfg?.entry_start}–{cfg?.entry_cutoff},
            max {cfg?.max_trades_per_day ?? 8} trades/day, one position per underlying. Paper mode.
          </div>
        </div>
      )}

      {/* ── Expiry-only warning ── */}
      {cfg && cfg.expiry_only && !isExpiry && (
        <div className="glass-card !rounded-2xl !p-4 flex items-center gap-3 !border-yellow-500/20">
          <AlertTriangle className="w-5 h-5 text-yellow-400 flex-shrink-0" />
          <div className="flex-1 text-sm text-slate-300">
            Scalper is armed for <span className="font-semibold text-yellow-400">expiry days only</span>. Today isn't an expiry for {underlying}, so it won't auto-fire.
          </div>
          <button onClick={toggleExpiryOnly} className="text-xs font-semibold text-accent hover:text-accent-light whitespace-nowrap">
            Allow any day →
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* ── Signal panel ── */}
        <div className="lg:col-span-2 space-y-4">
          <Card title="Live Setup">
            <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 10 }}>
              The engine only fires on a confirmed breakout in a trending regime — chop is filtered out
            </div>

            {hasSignal ? (
              <div className="space-y-4">
                {/* Signal hero */}
                <div className={`rounded-2xl p-4 border ${
                  sig.action === 'BUY_CE' ? 'bg-profit/5 border-profit/30' : 'bg-loss/5 border-loss/30'
                }`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${
                        sig.action === 'BUY_CE' ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
                      }`}>
                        {sig.action === 'BUY_CE' ? <TrendingUp className="w-6 h-6" /> : <TrendingDown className="w-6 h-6" />}
                      </div>
                      <div>
                        <div className="text-lg font-bold text-white">
                          BUY {sig.strike} {sig.option_type}
                        </div>
                        <div className="text-xs text-slate-400">{sig.reason}</div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-2xl font-bold font-mono text-white">₹{fmt(sig.premium, 2)}</div>
                      <div className="text-[11px] text-slate-500">{sig.lots} lots · {sig.qty} qty</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mt-4">
                    <div className="rounded-lg bg-slate-900/40 px-3 py-2">
                      <div className="text-[9px] text-slate-500 uppercase font-semibold flex items-center gap-1"><Gauge className="w-3 h-3" /> ADX</div>
                      <div className="text-sm font-bold text-white font-mono">{fmt(sig.adx, 0)}</div>
                    </div>
                    <div className="rounded-lg bg-slate-900/40 px-3 py-2">
                      <div className="text-[9px] text-slate-500 uppercase font-semibold flex items-center gap-1"><Target className="w-3 h-3" /> Level</div>
                      <div className="text-sm font-bold text-white font-mono">{sig.level_name}</div>
                    </div>
                    <div className="rounded-lg bg-slate-900/40 px-3 py-2">
                      <div className="text-[9px] text-slate-500 uppercase font-semibold flex items-center gap-1"><ShieldAlert className="w-3 h-3" /> Stop</div>
                      <div className="text-sm font-bold text-loss font-mono">{fmt(sig.stop_level)}</div>
                    </div>
                  </div>

                  <button
                    onClick={() => handleDeploy(false)}
                    disabled={deploying}
                    className="btn-deploy w-full mt-4 !py-2.5"
                  >
                    <Rocket className="w-4 h-4" />
                    {deploying ? 'Deploying…' : 'Deploy Scalp (Paper)'}
                  </button>
                </div>

                {/* Exit plan */}
                <div className="rounded-xl border border-slate-700/30 p-3">
                  <div className="text-[11px] font-semibold text-slate-400 uppercase mb-2">Exit Plan</div>
                  <div className="grid grid-cols-2 gap-2 text-xs text-slate-300">
                    <div className="flex items-center gap-1.5"><CheckCircle2 className="w-3 h-3 text-profit" /> Book 50% at +{cfg?.book_partial_pct ?? 50}%</div>
                    <div className="flex items-center gap-1.5"><CheckCircle2 className="w-3 h-3 text-profit" /> Then stop → breakeven</div>
                    <div className="flex items-center gap-1.5"><CheckCircle2 className="w-3 h-3 text-profit" /> Trail give-back {cfg?.trail_giveback_pct ?? 30}%</div>
                    <div className="flex items-center gap-1.5"><ShieldAlert className="w-3 h-3 text-loss" /> Structural stop @ {fmt(sig.stop_level)}</div>
                    <div className="flex items-center gap-1.5"><ShieldAlert className="w-3 h-3 text-loss" /> Premium floor −{cfg?.premium_floor_pct ?? 35}%</div>
                    <div className="flex items-center gap-1.5"><Clock className="w-3 h-3 text-blue-400" /> EOD square-off 15:15</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="rounded-2xl border border-slate-700/30 bg-slate-800/20 p-5 text-center">
                  <Activity className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                  <div className="text-sm text-slate-400 font-medium">No setup right now</div>
                  <div className="text-xs text-slate-500 mt-1">{sig?.reason || 'Waiting for a confirmed breakout…'}</div>
                </div>

                {/* Why blocked — transparency */}
                {blockers.length > 0 && (
                  <div className="rounded-xl border border-slate-700/30 p-3">
                    <div className="text-[11px] font-semibold text-slate-400 uppercase mb-2">Waiting on</div>
                    <div className="space-y-1.5">
                      {blockers.map((b, i) => (
                        <div key={i} className="flex items-start gap-2 text-xs text-slate-400">
                          <XCircle className="w-3.5 h-3.5 text-slate-600 mt-0.5 flex-shrink-0" />
                          <span>{b}</span>
                        </div>
                      ))}
                    </div>
                    <button
                      onClick={() => handleDeploy(true)}
                      disabled={deploying}
                      className="mt-3 text-[11px] font-medium text-yellow-400/80 hover:text-yellow-400 transition-colors"
                      title="Override the regime gate and deploy anyway (risky — bypasses chop/trend filter)"
                    >
                      {deploying ? 'Deploying…' : 'Force deploy anyway (override gate) →'}
                    </button>
                  </div>
                )}
              </div>
            )}
          </Card>

          {/* ── Active scalps ── */}
          <Card title={`Running Scalps (${scalps.length})`}>
            {scalps.length > 0 ? (
              <div className="space-y-2">
                {scalps.map((s) => {
                  const pnl = Number(s.pnl) || 0;
                  const pos = s.positions?.[0];
                  const booked = s._scalp_booked;
                  return (
                    <div key={s.strategy_id} className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-3">
                      <div className="flex items-center justify-between">
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-white truncate">{s.name}</div>
                          <div className="text-[10px] text-slate-500 flex items-center gap-2 mt-0.5">
                            {pos && <span className="font-mono">{pos.symbol} ×{pos.qty}</span>}
                            {s.entered ? <span className="text-profit">● ENTERED</span> : <span className="text-yellow-400">⏳ WAITING</span>}
                            {booked && <span className="text-accent">½ booked · trailing</span>}
                          </div>
                        </div>
                        <div className="text-lg font-bold font-mono text-profit">
                          {pnl >= 0 ? '+' : ''}₹{fmt(pnl)}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="text-center py-6">
                <Layers className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <div className="text-sm text-slate-500">No active scalps</div>
              </div>
            )}
          </Card>
        </div>

        {/* ── S/R levels rail ── */}
        <Card title="Support / Resistance">
          <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 10 }}>
            Live levels the engine watches for breakouts
          </div>
          <LevelRail levels={sig?.levels} spot={sig?.spot} />
        </Card>
      </div>

      {/* ── Performance + exit attribution ── */}
      <ScalpPerformance perf={perf} />

      {/* ── Config strip ── */}
      {cfg && (
        <Card title="Scalper Settings">
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 text-xs">
            <ConfigStat label="Risk / Trade" value={`₹${fmt(cfg.risk_per_trade)}`} />
            <ConfigStat label="Regime" value={cfg.regime} />
            <ConfigStat label="ADX Min" value={cfg.regime === 'strict' ? cfg.adx_min_strict : cfg.adx_min_balanced} />
            <ConfigStat label="Entry Window" value={`${cfg.entry_start}–${cfg.entry_cutoff}`} />
            <ConfigStat label="Book / Trail" value={`+${cfg.book_partial_pct}% / ${cfg.trail_giveback_pct}%`} />
            <ConfigStat label="Max Trades" value={cfg.max_trades_per_day} />
          </div>
        </Card>
      )}
    </div>
  );
}

function ConfigStat({ label, value }) {
  return (
    <div className="rounded-lg bg-slate-800/30 px-3 py-2">
      <div className="text-[9px] text-slate-500 uppercase font-semibold">{label}</div>
      <div className="text-sm font-bold text-white capitalize">{value}</div>
    </div>
  );
}

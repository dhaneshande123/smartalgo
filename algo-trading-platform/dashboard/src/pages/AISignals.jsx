import { useState } from 'react';
import {
  Brain, Minus, Zap, Target, Play,
  Activity, RefreshCw, CheckCircle, AlertTriangle,
  ArrowUpRight, ArrowDownRight, Rocket, Power,
  TrendingUp, BarChart3, Flame, Shield,
} from 'lucide-react';
import { useUnderlying, UNDERLYINGS } from '../context/UnderlyingContext';
import {
  useMarketRegime,
  useStrategySignals,
  useAutoDeployRecommendations as useAutoDeployRecs,
  useExecuteAutoDeploy,
  useAutoDeployConfig,
  useSetAutoDeployConfig,
  usePaperTradingStatus as usePaperStatus,
  useStartPaperTrading as useStartPaper,
  usePaperTradingStats as usePaperStats,
  useDeployPaperStrategy,
} from '../hooks/useApi';

/* ── Signal Badge ── */
const SIGNAL_STYLES = {
  STRONG_ENTRY: { icon: Rocket,        label: 'Strong Entry', color: '#22c55e', bg: 'rgba(34,197,94,0.1)',   border: 'rgba(34,197,94,0.25)' },
  ENTRY:        { icon: ArrowUpRight,   label: 'Entry',        color: '#3b82f6', bg: 'rgba(59,130,246,0.1)',  border: 'rgba(59,130,246,0.25)' },
  WAIT:         { icon: Minus,          label: 'Wait',         color: '#eab308', bg: 'rgba(234,179,8,0.1)',   border: 'rgba(234,179,8,0.25)' },
  NEUTRAL:      { icon: Minus,          label: 'Neutral',      color: '#64748b', bg: 'rgba(100,116,139,0.1)', border: 'rgba(100,116,139,0.2)' },
  AVOID:        { icon: ArrowDownRight, label: 'Avoid',        color: '#ef4444', bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.2)' },
  EXIT:         { icon: AlertTriangle,  label: 'Exit',         color: '#f97316', bg: 'rgba(249,115,22,0.1)',  border: 'rgba(249,115,22,0.2)' },
  HOLD:         { icon: Minus,          label: 'Hold',         color: '#eab308', bg: 'rgba(234,179,8,0.1)',   border: 'rgba(234,179,8,0.2)' },
  BUY:          { icon: ArrowUpRight,   label: 'Buy',          color: '#22c55e', bg: 'rgba(34,197,94,0.1)',   border: 'rgba(34,197,94,0.2)' },
  SELL:         { icon: ArrowDownRight, label: 'Sell',         color: '#ef4444', bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.2)' },
};

function SignalBadge({ signal, confidence }) {
  const s = SIGNAL_STYLES[signal] || SIGNAL_STYLES.NEUTRAL;
  const Icon = s.icon;
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-bold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.color }}>
      <Icon className="w-3 h-3" /> {s.label}
      {confidence != null && <span className="opacity-60 ml-0.5">{Math.round(confidence > 1 ? confidence : confidence * 100)}%</span>}
    </span>
  );
}

/* ── Confidence Bar ── */
function ConfidenceBar({ value, height = 6 }) {
  const pct = Math.min(Math.round(value > 1 ? value : value * 100), 100);
  const color = pct >= 75 ? '#22c55e' : pct >= 60 ? '#eab308' : pct >= 40 ? '#f97316' : '#ef4444';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 rounded-full" style={{ background: 'rgba(51,65,85,0.3)', height }}>
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-[11px] font-mono font-bold w-9 text-right" style={{ color }}>{pct}%</span>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   Main Component
   ════════════════════════════════════════════════════════════ */
export default function AISignals() {
  const { underlying } = useUnderlying();
  const [deployingId, setDeployingId] = useState(null);
  const [deployedIds, setDeployedIds] = useState(new Set());
  const [autoDeploying, setAutoDeploying] = useState(false);
  const [error, setError] = useState(null);

  const { data: regimeData, refetch: refetchRegime }   = useMarketRegime(underlying);
  const { data: signalsData, refetch: refetchSignals }  = useStrategySignals(underlying);
  const { data: recsData }     = useAutoDeployRecs(underlying);
  const executeDeploy          = useExecuteAutoDeploy();
  const { data: autoCfg }      = useAutoDeployConfig();
  const setAutoCfg             = useSetAutoDeployConfig();
  const deployPaper            = useDeployPaperStrategy();
  const { data: paperStatus }  = usePaperStatus();
  const startPaper             = useStartPaper();
  const { data: paperStats }   = usePaperStats(paperStatus?.active);
  const isPaperActive          = paperStatus?.active || false;

  const regime  = regimeData || {};
  const signals = signalsData?.signals || [];
  const recs    = recsData?.recommendations || [];

  const regimeCode = regime.regime_code || regime.regime || '';
  const isUp       = regimeCode.includes('UP') || regimeCode.includes('TRENDING_UP');
  const isDown     = regimeCode.includes('DOWN');
  const isRange    = regimeCode.includes('RANGE');
  const isVol      = regimeCode.includes('HIGH_VOL') || regimeCode.includes('CRISIS');
  const regimeColor = isUp ? '#22c55e' : isDown ? '#ef4444' : isRange ? '#3b82f6' : isVol ? '#f97316' : '#64748b';

  const rawConf = regime.confidence || 0;
  const confidencePct = Math.round(rawConf > 1 ? rawConf : rawConf * 100);

  const handleRefresh = () => { refetchRegime(); refetchSignals(); };

  const autoEnabled = autoCfg?.enabled || false;
  const handleToggleAuto = async () => {
    setError(null);
    try {
      if (!autoEnabled && !isPaperActive) {
        await startPaper.mutateAsync({ initial_capital: 1000000, symbols: UNDERLYINGS });
      }
      // Enable for the currently-selected underlying (kept in sync below)
      await setAutoCfg.mutateAsync({ enabled: !autoEnabled, symbols: [underlying] });
    } catch (err) {
      setError(`Auto-deploy toggle failed: ${err?.response?.data?.detail || err?.message || 'Unknown error'}`);
    }
  };

  const handleDeploy = async (rec) => {
    setDeployingId(rec.strategy_id);
    setError(null);
    try {
      if (!isPaperActive) {
        await startPaper.mutateAsync({ initial_capital: 1000000, symbols: UNDERLYINGS });
      }
      await deployPaper.mutateAsync({
        strategy_class: rec.strategy_class || rec.strategy_id,
        name: rec.strategy_name || rec.strategy_id,
        underlying,
      });
      setDeployedIds(prev => new Set([...prev, rec.strategy_id]));
    } catch (err) {
      setError(`Deploy failed: ${err?.response?.data?.detail || err?.message || 'Unknown error'}`);
    } finally {
      setDeployingId(null);
    }
  };

  const handleAutoDeployAll = async () => {
    setAutoDeploying(true);
    setError(null);
    try {
      if (!isPaperActive) {
        await startPaper.mutateAsync({ initial_capital: 1000000, symbols: UNDERLYINGS });
      }
      const result = await executeDeploy.mutateAsync({ auto_deploy: true });
      if (result?.deployed) {
        setDeployedIds(prev => new Set([...prev, ...result.deployed.map(d => d.strategy_id)]));
      }
    } catch (err) {
      setError(`Auto-deploy failed: ${err?.response?.data?.detail || err?.message || 'Unknown error'}`);
    } finally {
      setAutoDeploying(false);
    }
  };

  return (
    <div className="space-y-5 max-w-[1440px] mx-auto animate-fade-in">

      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">AI Signal Engine</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Live regime classification, strategy scoring, and one-click deploy
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!isPaperActive && (
            <button onClick={() => startPaper.mutate({ initial_capital: 1000000, symbols: UNDERLYINGS })}
              disabled={startPaper.isPending}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent/15 text-accent hover:bg-accent/25 transition-colors">
              <Power className="w-3.5 h-3.5" /> {startPaper.isPending ? 'Starting...' : 'Start Paper'}
            </button>
          )}
          <button onClick={handleRefresh}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-slate-800/40 text-slate-400 hover:text-slate-200 border border-white/5 transition-colors">
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
        </div>
      </div>

      {/* ── Error Banner ── */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" /> {error}
          <button onClick={() => setError(null)} className="ml-auto text-red-500 hover:text-red-300 text-xs">Dismiss</button>
        </div>
      )}

      {/* ── Paper Stats Bar (compact) ── */}
      {isPaperActive && paperStats && (
        <div className="glass-card !p-3">
          <div className="flex items-center gap-6 flex-wrap">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-profit animate-pulse" />
              <span className="text-xs font-semibold text-white">Paper Engine Active</span>
            </div>
            {[
              ['Capital', `₹${((paperStats.initial_capital || 1000000) / 100000).toFixed(0)}L`],
              ['P&L', `${(paperStats.total_pnl || 0) >= 0 ? '+' : ''}₹${Math.abs(paperStats.total_pnl || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`],
              ['Trades', paperStats.total_trades || 0],
              ['Win Rate', `${Math.round(paperStats.win_rate > 1 ? paperStats.win_rate : (paperStats.win_rate || 0) * 100)}%`],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center gap-1.5">
                <span className="text-[10px] text-slate-600">{label}:</span>
                <span className={`text-xs font-bold font-mono ${
                  label === 'P&L' ? ((paperStats.total_pnl || 0) >= 0 ? 'text-profit' : 'text-loss') : 'text-slate-300'
                }`}>{value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Regime + VIX Row ── */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
        {/* Regime Card */}
        <div className="glass-card !p-5" style={{ borderColor: `${regimeColor}20` }}>
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: `${regimeColor}15` }}>
                <Brain className="w-5 h-5" style={{ color: regimeColor }} />
              </div>
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-600 mb-0.5">Market Regime</div>
                <div className="text-lg font-bold text-white">{regime.regime_label || 'Loading...'}</div>
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-slate-600 mb-0.5">Confidence</div>
              <div className="text-2xl font-bold font-mono" style={{ color: regimeColor }}>{confidencePct}%</div>
            </div>
          </div>

          {/* Confidence bar */}
          <div className="w-full h-2 rounded-full mb-4" style={{ background: 'rgba(51,65,85,0.3)' }}>
            <div className="h-full rounded-full transition-all duration-700" style={{ width: `${confidencePct}%`, background: regimeColor }} />
          </div>

          {/* Indicators */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[
              ['Trend', regime.trend || '--'],
              ['Vol Regime', (regime.vol_regime || '').replace(/_/g, ' ') || '--'],
              [`${underlying} Chg`, regime.nifty_change_pct != null ? `${regime.nifty_change_pct >= 0 ? '+' : ''}${regime.nifty_change_pct.toFixed(2)}%` : '--'],
              ['Day Range', regime.intraday_range_pct != null ? `${regime.intraday_range_pct}%` : '--'],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg px-3 py-2 text-center bg-slate-800/30 border border-white/[0.03]">
                <div className="text-[9px] text-slate-600 uppercase tracking-wider mb-0.5">{label}</div>
                <div className="text-sm font-bold font-mono text-slate-200">{value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* VIX + Spot Stack */}
        <div className="flex flex-col gap-4">
          <div className="glass-card !p-4 flex-1">
            <div className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-1">India VIX</div>
            <div className="text-3xl font-bold font-mono" style={{
              color: (regime.vix || 0) > 20 ? '#ef4444' : (regime.vix || 0) > 15 ? '#eab308' : '#22c55e'
            }}>
              {regime.vix?.toFixed(2) || '--'}
            </div>
            <div className="text-[10px] mt-1" style={{
              color: (regime.vix || 0) > 20 ? '#ef4444' : (regime.vix || 0) > 15 ? '#eab308' : '#22c55e'
            }}>
              {(regime.vix || 0) > 20 ? 'High — sell premium' : (regime.vix || 0) > 15 ? 'Moderate' : 'Low — buy options cheap'}
            </div>
          </div>
          <div className="glass-card !p-4 flex-1">
            <div className="text-[10px] font-semibold text-slate-600 uppercase tracking-wider mb-1">{underlying} Spot</div>
            <div className="text-xl font-bold font-mono text-white">
              {regime.nifty_ltp ? Number(regime.nifty_ltp).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '--'}
            </div>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-xs font-mono" style={{ color: (regime.nifty_change_pct || 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                {(regime.nifty_change_pct || 0) >= 0 ? '+' : ''}{(regime.nifty_change_pct || 0).toFixed(2)}%
              </span>
              {regime.is_expiry_day && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-400 font-bold">EXPIRY</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Strategy Signals ── */}
      <div className="glass-card !p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-bold text-white">Strategy Signals</h2>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent/10 text-accent font-semibold">{signals.length}</span>
          </div>
          <span className="text-[10px] text-slate-600">Auto-refresh 5s</span>
        </div>

        {signals.length === 0 ? (
          <div className="text-center py-10">
            <Activity className="w-8 h-8 mx-auto mb-2 text-slate-700" />
            <p className="text-sm text-slate-600">Waiting for market data...</p>
          </div>
        ) : (
          <div className="space-y-2">
            {signals.map((sig) => {
              const name = sig.strategy_name || sig.strategy_id?.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Strategy';
              const conf = sig.confidence != null ? sig.confidence : 50;
              const confPct = Math.round(conf > 1 ? conf : conf * 100);
              const isDeployed = deployedIds.has(sig.strategy_id);
              const isDeploying = deployingId === sig.strategy_id;

              return (
                <div key={sig.strategy_id}
                  className="rounded-xl p-4 bg-slate-800/20 border border-white/[0.04] hover:border-white/[0.08] transition-colors">
                  <div className="flex items-center gap-3">
                    {/* Name + Signal */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap mb-1.5">
                        <span className="text-sm font-semibold text-white">{name}</span>
                        <SignalBadge signal={sig.signal} />
                        {sig.regime_fit && (
                          <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase ${
                            sig.regime_fit === 'HIGH' ? 'bg-profit/10 text-profit' :
                            sig.regime_fit === 'MEDIUM' ? 'bg-yellow-500/10 text-yellow-400' :
                            'bg-loss/10 text-loss'
                          }`}>{sig.regime_fit} FIT</span>
                        )}
                      </div>
                      {sig.action && <p className="text-[11px] text-slate-500 mb-1.5">{sig.action}</p>}
                      {sig.triggers?.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {sig.triggers.map((t, i) => (
                            <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800/50 text-slate-400 font-mono border border-white/[0.03]">{t}</span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Confidence */}
                    <div className="w-24 flex-shrink-0">
                      <ConfidenceBar value={conf} height={5} />
                    </div>

                    {/* Deploy */}
                    {confPct >= 65 && (
                      <button onClick={() => handleDeploy(sig)}
                        disabled={isDeploying || isDeployed}
                        className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all flex-shrink-0 ${
                          isDeployed
                            ? 'bg-profit/10 text-profit border border-profit/20'
                            : 'bg-accent/15 text-accent hover:bg-accent/25 border border-accent/20 active:scale-95'
                        }`}>
                        {isDeploying ? <div className="w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
                          : isDeployed ? <CheckCircle className="w-3 h-3" />
                          : <Play className="w-3 h-3" />}
                        {isDeploying ? '...' : isDeployed ? 'Live' : 'Deploy'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Auto-Deploy Recommendations ── */}
      <div className="glass-card !p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Target className="w-4 h-4 text-profit" />
            <h2 className="text-sm font-bold text-white">Auto-Deploy Ready</h2>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-profit/10 text-profit font-semibold">{recs.length} ready</span>
          </div>
          <div className="flex items-center gap-3">
            {/* Hands-free auto-deploy toggle */}
            <button
              onClick={handleToggleAuto}
              disabled={setAutoCfg.isPending}
              title={autoEnabled
                ? `Auto-deploy ON for ${underlying} — high-confidence signals (≥70%) deploy to paper automatically every ~60s`
                : 'Turn on hands-free auto-deploy: high-confidence signals deploy to paper by themselves'}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-[11px] font-semibold border transition-all ${
                autoEnabled
                  ? 'bg-profit/15 text-profit border-profit/30'
                  : 'bg-slate-800/40 text-slate-400 border-white/5 hover:text-slate-200'
              }`}>
              <span className={`relative w-7 h-4 rounded-full transition-colors ${autoEnabled ? 'bg-profit/40' : 'bg-slate-600/50'}`}>
                <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${autoEnabled ? 'left-3.5' : 'left-0.5'}`} />
              </span>
              {autoEnabled ? `Auto-Deploy ON (${autoCfg?.symbols?.join(', ') || underlying})` : 'Auto-Deploy OFF'}
            </button>
            {recs.length > 0 && (
              <button onClick={handleAutoDeployAll} disabled={autoDeploying}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold transition-all active:scale-95"
                style={{ background: 'linear-gradient(135deg, #22c55e, #16a34a)', color: '#fff', boxShadow: '0 2px 12px rgba(34,197,94,0.25)' }}>
                {autoDeploying ? (
                  <><div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Deploying...</>
                ) : (
                  <><Rocket className="w-3.5 h-3.5" /> Deploy All ({recs.length})</>
                )}
              </button>
            )}
          </div>
        </div>

        {recs.length === 0 ? (
          <div className="text-center py-8">
            <Target className="w-8 h-8 mx-auto mb-2 text-slate-700" />
            <p className="text-sm text-slate-600">No recommendations right now</p>
            <p className="text-[10px] text-slate-700 mt-1">AI needs confidence &ge; 70% to recommend</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {recs.map((rec) => {
              const recDeployed  = deployedIds.has(rec.strategy_id);
              const recDeploying = deployingId === rec.strategy_id;
              const conf = Math.min(Math.round(rec.confidence > 1 ? rec.confidence : rec.confidence * 100), 100);
              return (
                <div key={rec.strategy_id} className={`rounded-xl p-4 border transition-colors ${
                  recDeployed ? 'bg-profit/[0.03] border-profit/15' : 'bg-slate-800/20 border-white/[0.04]'
                }`}>
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-white">{rec.strategy_name || rec.strategy_id}</div>
                      <div className="text-[10px] text-slate-500 mt-0.5">{rec.reason}</div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className="text-lg font-bold font-mono" style={{ color: conf >= 75 ? '#22c55e' : '#eab308' }}>{conf}%</div>
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <ConfidenceBar value={rec.confidence} height={4} />
                    <button onClick={() => handleDeploy(rec)} disabled={recDeploying || recDeployed}
                      className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all flex-shrink-0 ${
                        recDeployed
                          ? 'bg-profit/10 text-profit'
                          : 'bg-accent/15 text-accent hover:bg-accent/25 active:scale-95'
                      }`}>
                      {recDeploying ? '...' : recDeployed ? <><CheckCircle className="w-3 h-3" /> Deployed</> : <><Play className="w-3 h-3" /> Deploy</>}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Disclaimer */}
      <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl text-[11px] text-slate-600"
        style={{ background: 'rgba(245,158,11,0.04)', border: '1px solid rgba(245,158,11,0.1)' }}>
        <AlertTriangle className="w-3.5 h-3.5 text-yellow-700 mt-0.5 flex-shrink-0" />
        AI signals are generated from live market data and historical patterns. Past performance does not guarantee future results.
        Always review signals before deployment. Auto-deploy executes in Paper Trading mode only.
      </div>
    </div>
  );
}

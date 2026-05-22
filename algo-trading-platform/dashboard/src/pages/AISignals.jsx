import { useState } from 'react';
import {
  Brain, Minus, Zap, Target,
  Activity, RefreshCw, CheckCircle, AlertTriangle,
  ArrowUpRight, ArrowDownRight,
} from 'lucide-react';
import { useTheme } from '../context/ThemeContext';
import {
  useMarketRegime,
  useStrategySignals,
  useAutoDeployRecommendations as useAutoDeployRecs,
  useExecuteAutoDeploy,
  usePaperTradingStatus as usePaperStatus,
  useStartPaperTrading as useStartPaper,
  usePaperTradingStats as usePaperStats,
} from '../hooks/useApi';

// ── Fallback Data ─────────────────────────────────────────────────────────
const fallbackRegime = {
  regime_label: 'Trending Bullish',
  regime_code: 'TRENDING',
  confidence: 0.78,
  vix: 14.52,
  trend_strength: 0.65,
  breadth: 0.72,
  indicators: {
    adx: 28.4,
    rsi: 58.2,
    macd_signal: 'bullish',
    bb_position: 'upper_half',
  },
};

const fallbackSignals = {
  signals: [
    { strategy_id: 'iron-condor-nifty', strategy_name: 'Iron Condor NIFTY', signal: 'BUY', confidence: 0.82, expected_return: 2.8, risk_reward: 1.9, regime_fit: 'HIGH', triggers: ['Low VIX', 'Range bound', 'High IV rank'] },
    { strategy_id: 'straddle-banknifty', strategy_name: 'Straddle BANKNIFTY', signal: 'HOLD', confidence: 0.61, expected_return: 1.4, risk_reward: 1.2, regime_fit: 'MEDIUM', triggers: ['Moderate VIX', 'Pending event'] },
    { strategy_id: 'momentum-breakout', strategy_name: 'Momentum Breakout', signal: 'BUY', confidence: 0.74, expected_return: 3.2, risk_reward: 2.1, regime_fit: 'HIGH', triggers: ['ADX > 25', 'Volume surge', 'ATH breakout'] },
    { strategy_id: 'mean-reversion', strategy_name: 'Mean Reversion', signal: 'AVOID', confidence: 0.55, expected_return: 0.8, risk_reward: 0.9, regime_fit: 'LOW', triggers: ['Strong trend', 'RSI not extreme'] },
    { strategy_id: 'gamma-scalping', strategy_name: 'Gamma Scalping', signal: 'BUY', confidence: 0.69, expected_return: 2.1, risk_reward: 1.6, regime_fit: 'MEDIUM', triggers: ['IV crush opportunity', 'Weekly expiry'] },
    { strategy_id: 'expiry-day', strategy_name: 'Expiry Day Strategy', signal: 'HOLD', confidence: 0.58, expected_return: 1.9, risk_reward: 1.4, regime_fit: 'MEDIUM', triggers: ['3 days to expiry', 'Theta decay'] },
  ],
};

const fallbackRecs = {
  recommendations: [
    { strategy_id: 'iron-condor-nifty', strategy_name: 'Iron Condor NIFTY', reason: 'Low VIX (14.5) and range-bound market ideal for premium collection', auto_deploy: true, confidence: 0.82, regime: 'Range Bound' },
    { strategy_id: 'momentum-breakout', strategy_name: 'Momentum Breakout', reason: 'Strong ADX signal with volume confirmation above 20-day average', auto_deploy: true, confidence: 0.74, regime: 'Trending' },
  ],
};

// ── Helper Components ─────────────────────────────────────────────────────
function SignalBadge({ signal }) {
  const config = {
    BUY:          { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.25)',  color: '#22c55e', icon: ArrowUpRight },
    STRONG_ENTRY: { bg: 'rgba(34,197,94,0.15)',  border: 'rgba(34,197,94,0.35)',  color: '#16a34a', icon: ArrowUpRight },
    ENTRY:        { bg: 'rgba(34,197,94,0.10)',  border: 'rgba(34,197,94,0.2)',   color: '#22c55e', icon: ArrowUpRight },
    SELL:         { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.25)',  color: '#ef4444', icon: ArrowDownRight },
    EXIT:         { bg: 'rgba(239,68,68,0.10)',  border: 'rgba(239,68,68,0.2)',   color: '#ef4444', icon: ArrowDownRight },
    HOLD:         { bg: 'rgba(234,179,8,0.12)',  border: 'rgba(234,179,8,0.25)',  color: '#eab308', icon: Minus },
    NEUTRAL:      { bg: 'rgba(100,116,139,0.12)',border: 'rgba(100,116,139,0.2)', color: '#94a3b8', icon: Minus },
    WAIT:         { bg: 'rgba(100,116,139,0.10)',border: 'rgba(100,116,139,0.18)',color: '#64748b', icon: Minus },
    AVOID:        { bg: 'rgba(100,116,139,0.12)',border: 'rgba(100,116,139,0.25)',color: '#64748b', icon: Minus },
  };
  const c = config[signal] || config.NEUTRAL;
  const Icon = c.icon;
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-bold"
      style={{ background: c.bg, border: `1px solid ${c.border}`, color: c.color }}>
      <Icon className="w-3 h-3" />
      {signal}
    </span>
  );
}

function FitBadge({ fit }) {
  const colors = {
    HIGH: { bg: 'rgba(34,197,94,0.1)', color: '#22c55e' },
    MEDIUM: { bg: 'rgba(234,179,8,0.1)', color: '#eab308' },
    LOW: { bg: 'rgba(239,68,68,0.1)', color: '#ef4444' },
  };
  const c = colors[fit] || colors.MEDIUM;
  return (
    <span className="px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider"
      style={{ background: c.bg, color: c.color }}>
      {fit}
    </span>
  );
}

function ConfidenceBar({ value }) {
  // Normalize: backend may return 0–100 int or 0–1 float
  const pct = Math.min(Math.round(value > 1 ? value : value * 100), 100);
  const color = pct >= 75 ? '#22c55e' : pct >= 60 ? '#eab308' : '#ef4444';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full" style={{ background: 'rgba(51,65,85,0.4)' }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs font-mono font-bold w-8 text-right" style={{ color }}>{pct}%</span>
    </div>
  );
}

const REGIME_COLORS = {
  TRENDING: { color: '#22c55e', bg: 'rgba(34,197,94,0.08)', border: 'rgba(34,197,94,0.2)' },
  RANGE_BOUND: { color: '#3b82f6', bg: 'rgba(59,130,246,0.08)', border: 'rgba(59,130,246,0.2)' },
  VOLATILE: { color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)' },
  NEUTRAL: { color: '#94a3b8', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)' },
};

// ── Main Component ────────────────────────────────────────────────────────
export default function AISignals() {
  const { theme } = useTheme();
  const [deployingId, setDeployingId] = useState(null);
  const [deployedIds, setDeployedIds] = useState(new Set());
  const [autoDeploying, setAutoDeploying] = useState(false);

  const { data: regimeData, refetch: refetchRegime } = useMarketRegime();
  const { data: signalsData, refetch: refetchSignals } = useStrategySignals();
  const { data: recsData } = useAutoDeployRecs();
  const executeDeploy = useExecuteAutoDeploy();
  const { data: paperStatus } = usePaperStatus();
  const startPaper = useStartPaper();
  const { data: paperStats } = usePaperStats();
  const isPaperActive = paperStatus?.active || false;

  const regime = regimeData || fallbackRegime;
  const signals = signalsData?.signals || fallbackSignals.signals;
  const recs = recsData?.recommendations || fallbackRecs.recommendations;

  const regimeCode = regime.regime_code || regime.regime || 'NEUTRAL';
  // Map backend regime codes to frontend color keys
  const regimeColorKey = regimeCode.startsWith('TRENDING') ? 'TRENDING' : regimeCode === 'CRISIS' ? 'VOLATILE' : REGIME_COLORS[regimeCode] ? regimeCode : 'NEUTRAL';
  const rc = REGIME_COLORS[regimeColorKey] || REGIME_COLORS.NEUTRAL;
  const rawConf = regime.confidence || 0;
  const confidencePct = Math.round(rawConf > 1 ? rawConf : rawConf * 100);

  const handleRefresh = () => {
    refetchRegime();
    refetchSignals();
  };

  const handleDeploy = async (rec) => {
    setDeployingId(rec.strategy_id);
    try {
      // Auto-start paper session if not active
      if (!isPaperActive) {
        await startPaper.mutateAsync();
      }
      await executeDeploy.mutateAsync({ strategy_id: rec.strategy_id, auto_deploy: true });
      setDeployedIds(prev => new Set([...prev, rec.strategy_id]));
    } catch {
      // silently handle
    } finally {
      setDeployingId(null);
    }
  };

  const handleAutoDeployAll = async () => {
    setAutoDeploying(true);
    try {
      if (!isPaperActive) {
        await startPaper.mutateAsync();
      }
      const result = await executeDeploy.mutateAsync({ auto_deploy: true });
      if (result?.deployed) {
        const ids = result.deployed.map(d => d.strategy_id);
        setDeployedIds(prev => new Set([...prev, ...ids]));
      }
    } catch {
    } finally {
      setAutoDeploying(false);
    }
  };

  const cardStyle = theme === 'dark'
    ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)', borderRadius: '16px' }
    : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.08)', borderRadius: '16px', boxShadow: '0 1px 4px rgba(0,0,0,0.06)' };

  return (
    <div className="space-y-4 animate-fade-in">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
            AI Signals & Auto-Deploy
          </h1>
          <p className="text-sm mt-0.5" style={{ color: '#64748b' }}>
            Machine learning signals, market regime detection, and automated strategy recommendations
          </p>
        </div>
        <button
          onClick={handleRefresh}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all"
          style={{ background: 'rgba(124,58,237,0.1)', border: '1px solid rgba(124,58,237,0.2)', color: '#a855f7' }}
        >
          <RefreshCw className={`w-4 h-4 ${regimeLoading || signalsLoading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* ── Paper Trading P&L Summary ─────────────────────────── */}
      {isPaperActive && paperStats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Capital', value: `₹${((paperStats.initial_capital || paperStats.capital || 2000000) / 100000).toFixed(1)}L`, color: '#f1f5f9' },
            { label: 'Net P&L', value: `${(paperStats.total_pnl ?? paperStats.net_pnl ?? 0) >= 0 ? '+' : ''}₹${((paperStats.total_pnl ?? paperStats.net_pnl ?? 0)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`, color: (paperStats.total_pnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' },
            { label: 'Trades', value: paperStats.total_trades ?? paperStats.trades_count ?? 0, color: '#60a5fa' },
            { label: 'Win Rate', value: paperStats.win_rate != null ? `${Math.round((paperStats.win_rate > 1 ? paperStats.win_rate : paperStats.win_rate * 100))}%` : '--', color: (paperStats.win_rate ?? 0) >= 0.6 ? '#22c55e' : '#eab308' },
          ].map((m) => (
            <div key={m.label} className="rounded-xl p-4 text-center" style={cardStyle}>
              <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: '#64748b' }}>{m.label}</div>
              <div className="text-xl font-extrabold font-mono" style={{ color: m.color }}>{m.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── Market Regime ────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Main Regime Card */}
        <div className="lg:col-span-2 p-6 rounded-2xl" style={{ ...cardStyle, border: `1px solid ${rc.border}`, background: rc.bg }}>
          <div className="flex items-start justify-between mb-5">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-xl flex items-center justify-center"
                style={{ background: `${rc.color}20` }}>
                <Brain className="w-5 h-5" style={{ color: rc.color }} />
              </div>
              <div>
                <div className="text-xs font-bold uppercase tracking-wider mb-1" style={{ color: rc.color }}>
                  Market Regime
                </div>
                <div className="text-2xl font-extrabold" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
                  {regime.regime_label}
                </div>
              </div>
            </div>
            <div className="text-right">
              <div className="text-xs" style={{ color: '#64748b' }}>Confidence</div>
              <div className="text-3xl font-extrabold" style={{ color: rc.color }}>{confidencePct}%</div>
            </div>
          </div>

          {/* Confidence bar */}
          <div className="mb-5">
            <div className="w-full h-2.5 rounded-full" style={{ background: 'rgba(51,65,85,0.4)' }}>
              <div className="h-full rounded-full transition-all" style={{ width: `${confidencePct}%`, background: rc.color }} />
            </div>
          </div>

          {/* Indicators grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Trend', value: regime.trend || regime.indicators?.trend || '--' },
              { label: 'Vol Regime', value: (regime.vol_regime || '').replace(/_/g, ' ') || '--' },
              { label: 'NIFTY Chg%', value: regime.nifty_change_pct != null ? `${regime.nifty_change_pct > 0 ? '+' : ''}${regime.nifty_change_pct.toFixed(2)}%` : (regime.indicators?.adx?.toFixed(1) || '--') },
              { label: 'Day Range', value: regime.intraday_range_pct != null ? `${regime.intraday_range_pct}%` : (regime.indicators?.rsi?.toFixed(1) || '--') },
            ].map((ind) => (
              <div key={ind.label} className="rounded-xl px-3 py-2.5 text-center"
                style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.05)' }}>
                <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: '#64748b' }}>{ind.label}</div>
                <div className="text-lg font-bold font-mono" style={{ color: '#f1f5f9' }}>{ind.value}</div>
              </div>
            ))}
          </div>
        </div>

        {/* VIX + MACD mini cards */}
        <div className="flex flex-col gap-4">
          <div className="flex-1 p-5 rounded-2xl" style={cardStyle}>
            <div className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: '#64748b' }}>India VIX</div>
            <div className="text-4xl font-extrabold font-mono mb-1"
              style={{ color: (regime.vix || 0) > 20 ? '#ef4444' : (regime.vix || 0) > 15 ? '#eab308' : '#22c55e' }}>
              {regime.vix?.toFixed(2) || '--'}
            </div>
            <div className="text-xs" style={{ color: (regime.vix || 0) > 20 ? '#ef4444' : (regime.vix || 0) > 15 ? '#eab308' : '#22c55e' }}>
              {(regime.vix || 0) > 20 ? '⚠ High Volatility' : (regime.vix || 0) > 15 ? '⚡ Moderate' : '✓ Low Volatility'}
            </div>
          </div>
          <div className="flex-1 p-5 rounded-2xl" style={cardStyle}>
            <div className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: '#64748b' }}>NIFTY Spot</div>
            <div className="text-2xl font-extrabold font-mono mb-1" style={{ color: '#f1f5f9' }}>
              {regime.nifty_ltp ? Number(regime.nifty_ltp).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '--'}
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span style={{ color: (regime.nifty_change_pct || 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                {(regime.nifty_change_pct || 0) >= 0 ? '▲' : '▼'} {Math.abs(regime.nifty_change_pct || 0).toFixed(2)}%
              </span>
              {regime.is_expiry_day && <span className="px-1.5 py-0.5 rounded text-[10px] font-bold" style={{ background: 'rgba(234,179,8,0.15)', color: '#eab308' }}>EXPIRY DAY</span>}
            </div>
          </div>
        </div>
      </div>

      {/* ── Strategy Signals ─────────────────────────────────── */}
      <div style={cardStyle} className="p-5 rounded-2xl">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Zap className="w-4.5 h-4.5" style={{ color: '#a855f7' }} />
            <h2 className="text-sm font-bold" style={{ color: '#ffffff' }}>Strategy Signals</h2>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold"
              style={{ background: 'rgba(124,58,237,0.15)', color: '#a855f7' }}>
              {signals.length} strategies
            </span>
          </div>
          <div className="text-xs" style={{ color: '#475569' }}>Updated every 15s</div>
        </div>

        <div className="space-y-3">
          {signals.map((sig) => {
            const name = sig.strategy_name || sig.strategy_id?.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Strategy';
            const conf = sig.confidence != null ? sig.confidence : 50;
            return (
              <div key={sig.strategy_id}
                className="rounded-xl p-4 transition-all hover:border-opacity-30"
                style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(100,116,139,0.08)' }}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2.5 mb-2 flex-wrap">
                      <span className="text-sm font-semibold" style={{ color: '#f1f5f9' }}>{name}</span>
                      <SignalBadge signal={sig.signal} />
                      {sig.regime_fit && <FitBadge fit={sig.regime_fit} />}
                    </div>
                    {/* Action / recommendation text from backend */}
                    {sig.action && (
                      <p className="text-xs leading-relaxed mb-2" style={{ color: '#94a3b8' }}>{sig.action}</p>
                    )}
                    <div className="mb-1">
                      <ConfidenceBar value={conf} />
                    </div>
                    {/* Triggers if present */}
                    {sig.triggers && sig.triggers.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-2">
                        {sig.triggers.map((t) => (
                          <span key={t} className="px-2 py-0.5 rounded-md text-[11px]"
                            style={{ background: 'rgba(100,116,139,0.1)', color: '#94a3b8', border: '1px solid rgba(100,116,139,0.12)' }}>
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex-shrink-0 text-right">
                    <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: '#475569' }}>Confidence</div>
                    <div className="text-2xl font-bold font-mono" style={{ color: conf >= 70 ? '#22c55e' : conf >= 50 ? '#eab308' : '#ef4444' }}>
                      {conf}%
                    </div>
                    {sig.expected_return != null && (
                      <div className="text-[10px] mt-1" style={{ color: '#64748b' }}>
                        Exp: +{sig.expected_return}%
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Auto-Deploy Recommendations ──────────────────────── */}
      <div style={cardStyle} className="p-5 rounded-2xl">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Target className="w-4.5 h-4.5" style={{ color: '#22c55e' }} />
            <h2 className="text-sm font-bold" style={{ color: '#ffffff' }}>Auto-Deploy Recommendations</h2>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold"
              style={{ background: 'rgba(34,197,94,0.1)', color: '#22c55e' }}>
              {recs.length} ready
            </span>
          </div>
          {recs.length > 0 && (
            <button
              onClick={handleAutoDeployAll}
              disabled={autoDeploying}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all"
              style={{
                background: 'linear-gradient(135deg, #22c55e, #16a34a)',
                color: '#fff',
                boxShadow: '0 2px 12px rgba(34,197,94,0.3)',
              }}
            >
              {autoDeploying ? (
                <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Deploying All...</>
              ) : (
                <><Zap className="w-4 h-4" /> Auto-Deploy All to Paper</>
              )}
            </button>
          )}
        </div>

        {recs.length === 0 ? (
          <div className="text-center py-10">
            <Activity className="w-10 h-10 mx-auto mb-3" style={{ color: 'rgba(100,116,139,0.3)' }} />
            <div className="text-sm" style={{ color: '#475569' }}>No auto-deploy recommendations at this time</div>
            <div className="text-xs mt-1" style={{ color: '#334155' }}>Check back when market conditions change</div>
          </div>
        ) : (
          <div className="space-y-3">
            {recs.map((rec) => {
              const isDeployed = deployedIds.has(rec.strategy_id);
              const isDeploying = deployingId === rec.strategy_id;
              return (
                <div key={rec.strategy_id}
                  className="rounded-xl p-4 flex items-start justify-between gap-4"
                  style={{
                    background: isDeployed ? 'rgba(34,197,94,0.04)' : 'rgba(0,0,0,0.2)',
                    border: `1px solid ${isDeployed ? 'rgba(34,197,94,0.2)' : 'rgba(100,116,139,0.08)'}`,
                  }}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <span className="text-sm font-semibold" style={{ color: '#f1f5f9' }}>
                        {rec.strategy_name || rec.strategy_id?.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                      </span>
                      {rec.signal && <SignalBadge signal={rec.signal} />}
                      <span className="text-[11px] font-bold" style={{ color: '#22c55e' }}>
                        {Math.min(Math.round(rec.confidence > 1 ? rec.confidence : rec.confidence * 100), 100)}% confidence
                      </span>
                    </div>
                    <p className="text-xs leading-relaxed mb-1" style={{ color: '#94a3b8' }}>{rec.reason}</p>
                    {rec.action && <p className="text-xs leading-relaxed" style={{ color: '#64748b' }}>→ {rec.action}</p>}
                  </div>
                  <button
                    onClick={() => handleDeploy(rec)}
                    disabled={isDeploying || isDeployed}
                    className="flex-shrink-0 flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all disabled:opacity-60"
                    style={isDeployed ? {
                      background: 'rgba(34,197,94,0.1)',
                      border: '1px solid rgba(34,197,94,0.25)',
                      color: '#22c55e',
                    } : {
                      background: 'linear-gradient(135deg, #7c3aed, #6366f1)',
                      color: '#fff',
                      boxShadow: '0 2px 12px rgba(124,58,237,0.25)',
                    }}
                  >
                    {isDeploying ? (
                      <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Deploying...</>
                    ) : isDeployed ? (
                      <><CheckCircle className="w-4 h-4" /> Deployed</>
                    ) : (
                      <><Zap className="w-4 h-4" /> Deploy Now</>
                    )}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Disclaimer ───────────────────────────────────────── */}
      <div className="flex items-start gap-3 px-4 py-3 rounded-xl"
        style={{ background: 'rgba(234,179,8,0.04)', border: '1px solid rgba(234,179,8,0.1)' }}>
        <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#ca8a04' }} />
        <p className="text-xs leading-relaxed" style={{ color: '#92400e' }}>
          AI signals are generated from historical patterns and current market conditions. Past performance does not guarantee future results.
          Always review signals manually before deployment. Auto-deploy executes in Paper Trading mode unless live trading is explicitly enabled.
        </p>
      </div>
    </div>
  );
}

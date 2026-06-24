import { useState, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, Zap, Target, Shield, Activity,
  ChevronRight, AlertTriangle, Crosshair, BarChart3, Gauge,
  Rocket, Info, CheckCircle2, Clock, XCircle,
} from 'lucide-react';

// ─── Helpers ─────────────────────────────────────────────────────────────────

const num = (v) => Number(v) || 0;

const DIRECTION_CONFIG = {
  BULLISH: {
    color: '#10b981',
    bg: 'rgba(16,185,129,0.12)',
    border: 'rgba(16,185,129,0.25)',
    gradient: 'from-emerald-500 to-emerald-600',
    label: 'BULLISH',
    icon: TrendingUp,
    action: 'BUY CE',
    actionBg: 'bg-gradient-to-r from-emerald-500 to-emerald-600',
  },
  BEARISH: {
    color: '#ef4444',
    bg: 'rgba(239,68,68,0.12)',
    border: 'rgba(239,68,68,0.25)',
    gradient: 'from-red-500 to-red-600',
    label: 'BEARISH',
    icon: TrendingDown,
    action: 'BUY PE',
    actionBg: 'bg-gradient-to-r from-red-500 to-red-600',
  },
  NEUTRAL: {
    color: '#f59e0b',
    bg: 'rgba(245,158,11,0.12)',
    border: 'rgba(245,158,11,0.25)',
    gradient: 'from-amber-500 to-amber-600',
    label: 'NEUTRAL',
    icon: Activity,
    action: 'WAIT',
    actionBg: 'bg-gradient-to-r from-amber-500 to-amber-600',
  },
};

const STATUS_CONFIG = {
  CONFIRMED: { color: '#10b981', icon: CheckCircle2, label: 'CONFIRMED', desc: 'Signal stable across multiple refreshes' },
  FORMING:   { color: '#f59e0b', icon: Clock,        label: 'FORMING',   desc: 'Signal just appeared — waiting for confirmation' },
  WEAK:      { color: '#64748b', icon: XCircle,      label: 'WEAK',      desc: 'Low confidence — no actionable signal' },
};

const FACTOR_CONFIG = {
  buildup:             { icon: BarChart3,  label: 'Buildup Analysis',       weight: '35%', desc: 'OI change + price change pattern — most reliable indicator' },
  pcr:                 { icon: Gauge,      label: 'PCR Extreme',            weight: '25%', desc: 'Put-Call Ratio at extremes signals mean reversion' },
  max_pain:            { icon: Target,     label: 'Max Pain Gravity',       weight: '20%', desc: 'Spot vs max-pain distance — expiry day magnet' },
  support_resistance:  { icon: Shield,     label: 'Support/Resistance',     weight: '20%', desc: 'Spot crossing OI walls (highest call/put OI strikes)' },
};

// ─── Confidence Ring (SVG) ───────────────────────────────────────────────────

function ConfidenceRing({ value, size = 120, color, theme }) {
  const isDark = theme === 'dark';
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - Math.min(100, Math.max(0, value)) / 100);

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="transform -rotate-90">
        {/* Background ring */}
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke={isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.06)'}
          strokeWidth="8"
        />
        {/* Value ring */}
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          style={{ transition: 'stroke-dashoffset 0.8s ease' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-black font-mono" style={{ color }}>{Math.round(value)}%</span>
        <span className={`text-[9px] uppercase tracking-widest ${isDark ? 'text-slate-500' : 'text-slate-400'}`}>Confidence</span>
      </div>
    </div>
  );
}

// ─── Factor Card ─────────────────────────────────────────────────────────────

function FactorCard({ name, factor, theme }) {
  const isDark = theme === 'dark';
  const config = FACTOR_CONFIG[name] || { icon: Activity, label: name, weight: '?%', desc: '' };
  const Icon = config.icon;
  const dirConfig = DIRECTION_CONFIG[factor.direction] || DIRECTION_CONFIG.NEUTRAL;
  const score = num(factor.score);

  const cardBg = isDark
    ? { background: 'rgba(19,23,32,0.5)', border: `1px solid ${dirConfig.border}` }
    : { background: 'rgba(255,255,255,0.8)', border: `1px solid ${dirConfig.border}`, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' };

  return (
    <div className="rounded-xl p-3.5 transition-all duration-300 hover:scale-[1.01]" style={cardBg}>
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{ background: dirConfig.bg }}>
            <Icon className="w-3.5 h-3.5" style={{ color: dirConfig.color }} />
          </div>
          <div>
            <div className={`text-xs font-bold ${isDark ? 'text-slate-200' : 'text-slate-800'}`}>{config.label}</div>
            <div className="text-[9px] text-slate-500">{config.weight} weight</div>
          </div>
        </div>
        <span
          className="text-[10px] font-bold px-2 py-0.5 rounded-full"
          style={{ background: dirConfig.bg, color: dirConfig.color, border: `1px solid ${dirConfig.border}` }}
        >
          {factor.direction}
        </span>
      </div>

      {/* Score bar */}
      <div className="mb-2">
        <div className="flex justify-between text-[10px] mb-0.5">
          <span className="text-slate-500">Factor Score</span>
          <span className="font-mono font-bold" style={{ color: dirConfig.color }}>{score.toFixed(0)}%</span>
        </div>
        <div className={`h-1.5 rounded-full ${isDark ? 'bg-slate-800' : 'bg-slate-200'}`}>
          <div className="h-full rounded-full transition-all duration-500"
            style={{ width: `${Math.min(100, score)}%`, background: dirConfig.color }} />
        </div>
      </div>

      {/* Reasoning */}
      <div className={`text-[10px] leading-relaxed ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>
        {factor.reasoning}
      </div>

      {/* Description tooltip */}
      <div className={`text-[9px] mt-1.5 italic ${isDark ? 'text-slate-600' : 'text-slate-400'}`}>
        {config.desc}
      </div>
    </div>
  );
}

// ─── Deploy Confirmation Modal ───────────────────────────────────────────────

function DeployConfirmModal({ signal, onConfirm, onClose, loading, theme }) {
  const isDark = theme === 'dark';
  const dirConfig = DIRECTION_CONFIG[signal.direction] || DIRECTION_CONFIG.NEUTRAL;

  const bgStyle = isDark
    ? { background: 'rgba(15, 18, 25, 0.98)', border: '1px solid rgba(100,116,139,0.2)' }
    : { background: 'rgba(255,255,255,0.99)', border: '1px solid rgba(0,0,0,0.1)', boxShadow: '0 25px 50px rgba(0,0,0,0.15)' };

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-[440px] rounded-2xl overflow-hidden animate-fade-in" style={bgStyle} onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className={`px-5 py-4 border-b ${isDark ? 'border-slate-700/50' : 'border-slate-200'}`}>
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-white ${dirConfig.actionBg}`}>
              <Rocket className="w-5 h-5" />
            </div>
            <div>
              <div className={`text-base font-bold ${isDark ? 'text-white' : 'text-slate-900'}`}>Deploy OI Signal</div>
              <div className="text-xs text-slate-500">Paper Trading Mode</div>
            </div>
          </div>
        </div>

        {/* Signal Summary */}
        <div className={`px-5 py-4 space-y-3 ${isDark ? 'bg-slate-900/30' : 'bg-slate-50/50'}`}>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-slate-500">Direction</span>
              <div className="font-bold mt-0.5" style={{ color: dirConfig.color }}>{signal.direction}</div>
            </div>
            <div>
              <span className="text-slate-500">Confidence</span>
              <div className="font-bold font-mono mt-0.5" style={{ color: dirConfig.color }}>{signal.confidence}%</div>
            </div>
            <div>
              <span className="text-slate-500">Strike</span>
              <div className={`font-bold font-mono mt-0.5 ${isDark ? 'text-white' : 'text-slate-900'}`}>
                {signal.recommended_strike} {signal.recommended_type}
              </div>
            </div>
            <div>
              <span className="text-slate-500">Premium</span>
              <div className={`font-bold font-mono mt-0.5 ${isDark ? 'text-white' : 'text-slate-900'}`}>
                {signal.recommended_ltp ? `Rs ${num(signal.recommended_ltp).toFixed(2)}` : 'Market'}
              </div>
            </div>
          </div>

          {/* Strategy config */}
          <div className={`rounded-lg p-3 text-[11px] ${isDark ? 'bg-slate-800/60' : 'bg-white'}`}>
            <div className="font-semibold mb-1 text-slate-400">Auto-configured:</div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <span className="text-slate-500">SL:</span>{' '}
                <span className={`font-mono ${isDark ? 'text-red-400' : 'text-red-600'}`}>30%</span>
              </div>
              <div>
                <span className="text-slate-500">Target:</span>{' '}
                <span className={`font-mono ${isDark ? 'text-emerald-400' : 'text-emerald-600'}`}>50%</span>
              </div>
              <div>
                <span className="text-slate-500">Max Hold:</span>{' '}
                <span className={`font-mono ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>2hr</span>
              </div>
            </div>
          </div>
        </div>

        {/* Disclaimer */}
        <div className={`px-5 py-2 text-[10px] ${isDark ? 'text-amber-400/70 bg-amber-500/5' : 'text-amber-700 bg-amber-50'}`}>
          <AlertTriangle className="w-3 h-3 inline mr-1" />
          OI signals are analytical tools, not guaranteed predictions. Paper trading only. Past patterns do not guarantee future results.
        </div>

        {/* Actions */}
        <div className={`px-5 py-4 flex gap-3 border-t ${isDark ? 'border-slate-700/50' : 'border-slate-200'}`}>
          <button onClick={onClose}
            className={`flex-1 py-2.5 rounded-xl text-sm font-semibold transition-all
              ${isDark ? 'text-slate-400 hover:text-slate-200 bg-slate-800/60 hover:bg-slate-700/60' : 'text-slate-500 hover:text-slate-700 bg-slate-100'}`}>
            Cancel
          </button>
          <button onClick={onConfirm} disabled={loading}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold text-white ${dirConfig.actionBg} shadow-lg transition-all disabled:opacity-50`}>
            <Rocket className="w-4 h-4" />
            {loading ? 'Deploying...' : 'Deploy (Paper)'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Main Component ──────────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

export default function OISignals({ signalData, theme, onDeploy, deployLoading }) {
  const isDark = theme === 'dark';
  const [showDeployModal, setShowDeployModal] = useState(false);

  const cardStyle = isDark
    ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' }
    : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.06)', boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' };

  const signal = signalData?.signal || {};
  const factors = signalData?.factors || {};
  const meta = signalData?.meta || {};

  const dirConfig = DIRECTION_CONFIG[signal.direction] || DIRECTION_CONFIG.NEUTRAL;
  const statusConfig = STATUS_CONFIG[signal.status] || STATUS_CONFIG.WEAK;
  const DirIcon = dirConfig.icon;
  const StatusIcon = statusConfig.icon;

  const confidence = num(signal.confidence);
  const hasSignal = signal.direction && signal.direction !== 'NEUTRAL' && confidence >= 40;
  const canDeploy = signal.can_deploy === true;

  const handleDeploy = () => {
    if (signal.deploy_payload && onDeploy) {
      onDeploy(signal.deploy_payload);
      setShowDeployModal(false);
    }
  };

  // ── Empty state ──
  if (!signalData || !signal.direction) {
    return (
      <div className="rounded-xl p-8 text-center" style={cardStyle}>
        <Zap className={`w-8 h-8 mx-auto mb-3 ${isDark ? 'text-slate-600' : 'text-slate-300'}`} />
        <div className={`text-sm ${isDark ? 'text-slate-500' : 'text-slate-400'}`}>
          Loading OI signals... Waiting for option chain data.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 animate-fade-in">

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* DISCLAIMER BANNER                                                  */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div className={`rounded-xl px-4 py-2.5 flex items-start gap-2 text-[11px] leading-relaxed
        ${isDark ? 'bg-amber-500/5 text-amber-400/80 border border-amber-500/10' : 'bg-amber-50 text-amber-700 border border-amber-200'}`}>
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <div>
          <strong>OI-based signals are analytical tools, not predictions.</strong> Open Interest shows positioning, not guaranteed direction.
          Market makers hedge positions — large OI doesn't always mean conviction. Always use stop-losses and proper position sizing.
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* SIGNAL HERO CARD                                                   */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div className="rounded-xl overflow-hidden" style={{
        ...cardStyle,
        borderColor: hasSignal ? dirConfig.border : undefined,
        borderWidth: hasSignal ? '1.5px' : undefined,
      }}>
        <div className="p-5">
          <div className="flex items-start justify-between gap-4">

            {/* Left: Direction + Action */}
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-12 h-12 rounded-xl flex items-center justify-center"
                  style={{ background: dirConfig.bg, border: `1.5px solid ${dirConfig.border}` }}>
                  <DirIcon className="w-6 h-6" style={{ color: dirConfig.color }} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xl font-black tracking-tight" style={{ color: dirConfig.color }}>
                      {dirConfig.label}
                    </span>
                    <span
                      className="flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
                      style={{ background: `${statusConfig.color}20`, color: statusConfig.color, border: `1px solid ${statusConfig.color}40` }}
                    >
                      <StatusIcon className="w-3 h-3" />
                      {statusConfig.label}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5">
                    {statusConfig.desc}
                    {signal.stability_count > 0 && ` (${signal.stability_count} consecutive ticks)`}
                  </div>
                </div>
              </div>

              {/* Action badge */}
              {hasSignal ? (
                <div className="flex items-center gap-3">
                  <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-white font-bold text-sm ${dirConfig.actionBg} shadow-md`}>
                    <Zap className="w-4 h-4" />
                    {dirConfig.action}
                  </div>
                  {signal.recommended_strike && (
                    <div className={`text-sm ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>
                      <span className="font-mono font-bold">{signal.recommended_strike}</span>
                      <span className="text-slate-500 ml-1">{signal.recommended_type}</span>
                      {signal.recommended_ltp > 0 && (
                        <span className="text-slate-500 ml-2">
                          @ Rs <span className="font-mono font-semibold" style={{ color: dirConfig.color }}>
                            {num(signal.recommended_ltp).toFixed(2)}
                          </span>
                        </span>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className={`text-sm ${isDark ? 'text-slate-500' : 'text-slate-400'}`}>
                  No strong directional signal. OI data is balanced or low confidence.
                </div>
              )}
            </div>

            {/* Right: Confidence Ring */}
            <ConfidenceRing value={confidence} color={dirConfig.color} theme={theme} />
          </div>
        </div>

        {/* Deploy button bar */}
        {hasSignal && (
          <div className={`px-5 py-3 flex items-center justify-between border-t
            ${isDark ? 'border-slate-800/50 bg-slate-900/30' : 'border-slate-100 bg-slate-50/50'}`}>
            <div className="flex items-center gap-2 text-[11px]">
              <Info className="w-3.5 h-3.5 text-slate-500" />
              <span className="text-slate-500">
                {canDeploy
                  ? 'Signal confirmed — ready to deploy with auto stop-loss & target'
                  : confidence >= 60
                    ? 'Signal forming — wait for confirmation before deploying'
                    : 'Confidence too low for deployment (need 60%+)'}
              </span>
            </div>
            <button
              onClick={() => setShowDeployModal(true)}
              disabled={!canDeploy}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all
                ${canDeploy
                  ? `text-white ${dirConfig.actionBg} shadow-md hover:shadow-lg hover:scale-[1.02]`
                  : `${isDark ? 'text-slate-600 bg-slate-800/50 cursor-not-allowed' : 'text-slate-400 bg-slate-100 cursor-not-allowed'}`
                }`}
            >
              <Rocket className="w-3.5 h-3.5" />
              Deploy Signal
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* 4-FACTOR BREAKDOWN                                                 */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div>
        <div className={`text-[11px] font-bold uppercase tracking-widest mb-3 ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>
          4-Factor Analysis
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {Object.entries(factors).map(([name, factor]) => (
            <FactorCard key={name} name={name} factor={factor} theme={theme} />
          ))}
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* REASONING + META                                                   */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Reasoning */}
        <div className="rounded-xl p-4" style={cardStyle}>
          <div className={`text-[11px] font-bold uppercase tracking-widest mb-2 ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>
            Signal Reasoning
          </div>
          <div className="text-[10px] text-slate-500 mb-3">
            How each factor contributed to the final signal
          </div>
          {(signal.reasoning || []).length === 0 ? (
            <div className={`text-xs italic ${isDark ? 'text-slate-600' : 'text-slate-400'}`}>No reasoning available</div>
          ) : (
            <ul className="space-y-2">
              {signal.reasoning.map((r, i) => (
                <li key={i} className="flex items-start gap-2">
                  <Crosshair className="w-3 h-3 mt-0.5 shrink-0" style={{ color: dirConfig.color }} />
                  <span className={`text-xs leading-relaxed ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>{r}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Meta info */}
        <div className="rounded-xl p-4" style={cardStyle}>
          <div className={`text-[11px] font-bold uppercase tracking-widest mb-2 ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>
            Signal Metadata
          </div>
          <div className="text-[10px] text-slate-500 mb-3">
            Underlying computation details
          </div>
          <div className="grid grid-cols-2 gap-y-2.5 gap-x-4 text-xs">
            {[
              { label: 'Symbol', value: meta.symbol || '—', mono: true },
              { label: 'Spot', value: meta.spot ? num(meta.spot).toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '—', mono: true },
              { label: 'VIX', value: meta.vix ? num(meta.vix).toFixed(2) : '—', mono: true },
              { label: 'Chain Strikes', value: meta.chain_strikes || 0, mono: true },
              { label: 'Bullish Score', value: meta.bullish_score ? (meta.bullish_score * 100).toFixed(1) + '%' : '0%', color: '#10b981', mono: true },
              { label: 'Bearish Score', value: meta.bearish_score ? (meta.bearish_score * 100).toFixed(1) + '%' : '0%', color: '#ef4444', mono: true },
              { label: 'Agreeing Factors', value: `${meta.agreeing_factors || 0} / 4`, mono: true },
              { label: 'Lot Size', value: meta.lot_size || 75, mono: true },
            ].map(({ label, value, color, mono }) => (
              <div key={label} className="flex justify-between">
                <span className="text-slate-500">{label}</span>
                <span className={`font-semibold ${mono ? 'font-mono' : ''}`}
                  style={{ color: color || (isDark ? '#e2e8f0' : '#1e293b') }}>
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* HOW IT WORKS (educational)                                         */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div className={`rounded-xl p-4 ${isDark ? 'bg-blue-500/5 border border-blue-500/10' : 'bg-blue-50 border border-blue-200'}`}>
        <div className="flex items-center gap-2 mb-2">
          <Info className={`w-4 h-4 ${isDark ? 'text-blue-400' : 'text-blue-600'}`} />
          <span className={`text-xs font-bold ${isDark ? 'text-blue-400' : 'text-blue-700'}`}>How OI Signals Work</span>
        </div>
        <div className={`text-[11px] leading-relaxed space-y-1.5 ${isDark ? 'text-blue-300/70' : 'text-blue-800/70'}`}>
          <p><strong>Buildup Analysis (35%):</strong> Combines OI change direction with price change. If OI rises while price falls, it's new short positions (bearish). ATM strikes are weighted 3x more.</p>
          <p><strong>PCR Extreme (25%):</strong> When Put-Call Ratio hits extremes (above 1.3 = bullish, below 0.7 = bearish), it signals mean-reversion opportunities.</p>
          <p><strong>Max Pain Gravity (20%):</strong> The strike with highest combined OI acts as a price magnet near expiry. Spot far from max-pain suggests gravitational pull.</p>
          <p><strong>S/R Breach (20%):</strong> Highest Call OI = resistance, Highest Put OI = support. When spot crosses these walls, it signals breakout/breakdown.</p>
          <p><strong>Stability Filter:</strong> Signals must persist across {'>'}= 2 consecutive refreshes before being marked "CONFIRMED" and eligible for deployment.</p>
        </div>
      </div>

      {/* Deploy Modal */}
      {showDeployModal && canDeploy && (
        <DeployConfirmModal
          signal={signal}
          onConfirm={handleDeploy}
          onClose={() => setShowDeployModal(false)}
          loading={deployLoading}
          theme={theme}
        />
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from 'react';
import {
  Zap, ShieldAlert, TrendingUp, TrendingDown,
  AlertTriangle, Activity, BarChart3,
} from 'lucide-react';
import { useRiskMetrics, useCircuitBreakers, useKillSwitch, useStressTests, useGreeks, useDeactivateKillSwitch } from '../hooks/useApi';
import { useTheme } from '../context/ThemeContext';
import RiskLimitsEditor from './Risk/RiskLimitsEditor';
import BreachBanner from './Risk/BreachBanner';

// ── Fallback data ────────────────────────────────────────────────────────────
const fallbackCircuitBreakers = [
  { id: 1, name: 'Max Daily Loss',     status: 'healthy', threshold: 200000, current: 45000,  unit: 'Rs'   },
  { id: 2, name: 'Max Drawdown',       status: 'healthy', threshold: 5,      current: 2.3,    unit: '%'    },
  { id: 3, name: 'Max Position Size',  status: 'warning', threshold: 500,    current: 425,    unit: 'lots' },
  { id: 4, name: 'Order Rate Limit',   status: 'healthy', threshold: 50,     current: 12,     unit: '/min' },
  { id: 5, name: 'API Error Rate',     status: 'healthy', threshold: 5,      current: 0.5,    unit: '%'    },
  { id: 6, name: 'Margin Utilization', status: 'warning', threshold: 80,     current: 62,     unit: '%'    },
];

const fallbackRisk = {
  drawdown: 2.3,
  drawdownLimit: 5,
  var95: 85000,
  var99: 142000,
  maxLoss: 150000,
  currentLoss: 45000,
  marginUsed: 62,
  sharpe: 1.85,
};

const fallbackStress = [
  { scenario: 'NIFTY -5%',        pnlImpact: -125000, deltaImpact: -45,  vegaImpact: 12000  },
  { scenario: 'NIFTY +5%',        pnlImpact: -85000,  deltaImpact: 32,   vegaImpact: 8000   },
  { scenario: 'VIX +50%',         pnlImpact: -210000, deltaImpact: -8,   vegaImpact: 45000  },
  { scenario: 'VIX -30%',         pnlImpact: 95000,   deltaImpact: 3,    vegaImpact: -22000 },
  { scenario: 'Flash Crash -10%', pnlImpact: -380000, deltaImpact: -120, vegaImpact: 85000  },
];

const fallbackGreeks = { delta: 0.35, gamma: -0.08, theta: 154.5, vega: -279.3 };
const greekLimits   = { delta: 1,     gamma: 0.2,   theta: 500,   vega: 600    };
const greekColors   = {
  delta: '#3b82f6',
  gamma: '#f59e0b',
  theta: '#10b981',
  vega:  '#a78bfa',
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatIndian(num) {
  if (num === null || num === undefined) return '0';
  const abs = Math.abs(num);
  const str = abs.toFixed(0);
  const lastThree = str.slice(-3);
  const rest = str.slice(0, -3);
  const formatted = rest
    ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + lastThree
    : lastThree;
  return (num < 0 ? '-' : '') + 'Rs\u00A0' + formatted;
}

// ── Kill Switch component ─────────────────────────────────────────────────────
function KillSwitchBanner({ killSwitch, deactivateKillSwitch, isActiveOnBackend, killMeta, isDark }) {
  // 'idle' | 'confirm' | 'activated' (locally driven for confirm flow)
  // When the backend already has the kill switch ON we show "activated" mode
  // regardless of local state.
  const [state, setState] = useState('idle');
  const [countdown, setCountdown] = useState(3);

  // Sync local state with backend
  useEffect(() => {
    if (isActiveOnBackend && state !== 'activated') {
      setState('activated');
    } else if (!isActiveOnBackend && state === 'activated') {
      setState('idle');
    }
  }, [isActiveOnBackend]);  // eslint-disable-line react-hooks/exhaustive-deps

  const handleActivate = useCallback(() => {
    setState('confirm');
    setCountdown(3);
  }, []);

  const handleConfirm = useCallback(() => {
    killSwitch.mutate('manual via Risk page');
    setState('activated');
  }, [killSwitch]);

  const handleCancel = useCallback(() => {
    setState('idle');
  }, []);

  const handleDeactivate = useCallback(() => {
    deactivateKillSwitch.mutate('manual reset via Risk page');
  }, [deactivateKillSwitch]);

  // Countdown tick when in confirm state
  useEffect(() => {
    if (state !== 'confirm') return;
    if (countdown <= 0) return;
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [state, countdown]);

  const cardStyle = {
    background: state === 'activated'
      ? 'rgba(127,29,29,0.25)'
      : state === 'confirm'
      ? 'rgba(127,29,29,0.18)'
      : isDark ? 'rgba(19,23,32,0.85)' : 'rgba(255,255,255,0.82)',
    border: `1px solid ${state === 'activated' ? 'rgba(239,68,68,0.6)' : state === 'confirm' ? 'rgba(239,68,68,0.45)' : isDark ? 'rgba(239,68,68,0.25)' : 'rgba(0,0,0,0.06)'}`,
    boxShadow: !isDark && state === 'idle' ? '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' : undefined,
    borderRadius: 12,
    padding: '20px 24px',
    position: 'relative',
    overflow: 'hidden',
    transition: 'all 0.3s ease',
  };

  return (
    <div style={cardStyle} title="Emergency stop — Immediately closes all positions and blocks new trades">
      {/* Subtle red glow stripe at top */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 2,
        background: 'linear-gradient(90deg, transparent, rgba(239,68,68,0.6), transparent)',
      }} />

      {state === 'idle' && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{
              width: 44, height: 44, borderRadius: 10,
              background: 'rgba(239,68,68,0.12)',
              border: '1px solid rgba(239,68,68,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              <Zap style={{ width: 22, height: 22, color: '#ef4444' }} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 16, fontWeight: 800, color: '#fca5a5', letterSpacing: '0.02em' }}>
                  ⚡ EMERGENCY KILL SWITCH
                </span>
                {/* Blinking red dot */}
                <span style={{
                  width: 8, height: 8, borderRadius: '50%', background: '#ef4444',
                  display: 'inline-block',
                  animation: 'killPulse 1.2s ease-in-out infinite',
                }} />
              </div>
              <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 3 }}>
                Immediately closes <strong style={{ color: '#fca5a5' }}>ALL positions</strong> and cancels{' '}
                <strong style={{ color: '#fca5a5' }}>ALL pending orders</strong> across all strategies
              </p>
            </div>
          </div>
          <button
            onClick={handleActivate}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '10px 22px',
              background: 'linear-gradient(135deg, #dc2626, #991b1b)',
              border: '1px solid rgba(239,68,68,0.5)',
              borderRadius: 8, color: '#fff', fontSize: 13, fontWeight: 700,
              cursor: 'pointer', letterSpacing: '0.04em',
              boxShadow: '0 4px 16px rgba(239,68,68,0.25)',
              transition: 'all 0.2s',
              flexShrink: 0,
            }}
          >
            <Zap style={{ width: 15, height: 15 }} />
            ACTIVATE KILL SWITCH
          </button>
        </div>
      )}

      {state === 'confirm' && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <AlertTriangle style={{ width: 28, height: 28, color: '#f97316', flexShrink: 0 }} />
            <div>
              <p style={{ fontSize: 15, fontWeight: 800, color: '#fca5a5', margin: 0 }}>
                Are you absolutely sure?
              </p>
              <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 3 }}>
                This action is <strong style={{ color: '#f97316' }}>irreversible</strong> and will immediately flatten all open positions.
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
            {/* Countdown circle */}
            <div style={{
              width: 36, height: 36, borderRadius: '50%',
              background: 'rgba(239,68,68,0.15)',
              border: '2px solid rgba(239,68,68,0.5)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 14, fontWeight: 800, color: '#ef4444', fontFamily: 'monospace',
            }}>
              {countdown}
            </div>
            <button
              onClick={handleConfirm}
              style={{
                padding: '9px 20px',
                background: 'linear-gradient(135deg, #dc2626, #7f1d1d)',
                border: '1px solid rgba(239,68,68,0.6)',
                borderRadius: 8, color: '#fff', fontSize: 13, fontWeight: 800,
                cursor: 'pointer', letterSpacing: '0.06em',
                boxShadow: '0 4px 16px rgba(239,68,68,0.35)',
              }}
            >
              CONFIRM
            </button>
            <button
              onClick={handleCancel}
              style={{
                padding: '9px 18px',
                background: 'rgba(100,116,139,0.15)',
                border: '1px solid rgba(100,116,139,0.3)',
                borderRadius: 8, color: '#94a3b8', fontSize: 13, fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {state === 'activated' && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flex: 1 }}>
            {/* Spinning activation indicator (only when freshly triggered) */}
            <div style={{
              width: 44, height: 44, borderRadius: 10,
              background: 'rgba(239,68,68,0.15)',
              border: '2px solid rgba(239,68,68,0.5)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0,
            }}>
              <Zap style={{ width: 22, height: 22, color: '#ef4444' }} />
            </div>
            <div>
              <p style={{ fontSize: 16, fontWeight: 800, color: '#ef4444', margin: 0, letterSpacing: '0.04em' }}>
                ⚡ KILL SWITCH ACTIVE
              </p>
              <p style={{ fontSize: 12, color: '#94a3b8', marginTop: 3 }}>
                {killMeta?.reason ? (
                  <>
                    Reason: <strong style={{ color: '#fca5a5' }}>{killMeta.reason}</strong>
                    {killMeta?.triggered_by && (
                      <> · By <strong style={{ color: '#fca5a5' }}>{killMeta.triggered_by}</strong></>
                    )}
                  </>
                ) : 'All new deploys blocked. All open strategies stopped.'}
              </p>
            </div>
          </div>
          <button
            onClick={handleDeactivate}
            disabled={deactivateKillSwitch.isPending}
            style={{
              padding: '9px 18px',
              background: 'rgba(100,116,139,0.18)',
              border: '1px solid rgba(100,116,139,0.4)',
              borderRadius: 8, color: '#cbd5e1', fontSize: 12, fontWeight: 700,
              cursor: deactivateKillSwitch.isPending ? 'wait' : 'pointer',
              letterSpacing: '0.04em', flexShrink: 0,
            }}
          >
            {deactivateKillSwitch.isPending ? 'Resetting…' : 'RESET KILL SWITCH'}
          </button>
        </div>
      )}

      {/* Inline keyframe styles via a style tag trick */}
      <style>{`
        @keyframes killPulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.3; transform: scale(0.8); }
        }
        @keyframes spinKill {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
export default function Risk() {
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const { data: riskData } = useRiskMetrics();
  const { data: cbData }   = useCircuitBreakers();
  const { data: stressData } = useStressTests();
  const { data: greeksData } = useGreeks();
  const killSwitch = useKillSwitch();
  const deactivateKillSwitch = useDeactivateKillSwitch();
  const isKillSwitchActive = !!riskData?.kill_switch_active;
  const killMeta = cbData?.kill_switch_meta || riskData?.kill_switch_meta;

  // ── Normalize risk ────────────────────────────────────────────────────────
  const risk = riskData
    ? {
        drawdown:      parseFloat((Math.abs((riskData.current_drawdown ?? riskData.drawdown ?? 0) * (riskData.current_drawdown < 1 ? 100 : 1))).toFixed(2)),
        drawdownLimit: parseFloat((Math.abs((riskData.max_drawdown ?? 0.05) * (riskData.max_drawdown < 1 ? 100 : 1))).toFixed(2)),
        var95:         Math.round(riskData.portfolio_var_1d_95 ?? riskData.var95 ?? 0),
        var99:         Math.round(riskData.portfolio_var_1d_99 ?? riskData.var99 ?? 0),
        maxLoss:       Math.abs(riskData.daily_loss_limit ?? riskData.maxLoss ?? 150000),
        currentLoss:   Math.abs(riskData.daily_loss_used ?? riskData.currentLoss ?? 0),
        marginUsed:    Math.round((riskData.margin_utilization ?? riskData.marginUsed ?? 0) * (riskData.margin_utilization <= 1 ? 100 : 1)),
        sharpe:        riskData.sharpe ?? 0,
      }
    : fallbackRisk;

  const rawCb = cbData?.circuit_breakers || cbData;
  const circuitBreakers = Array.isArray(rawCb) ? rawCb : fallbackCircuitBreakers;

  const rawStress = stressData?.scenarios || stressData;
  const stressTests = Array.isArray(rawStress) ? rawStress : fallbackStress;

  // ── Normalize Greeks ────────────────────────────────────────────────────
  const greeks = greeksData
    ? {
        delta: greeksData.net_delta ?? greeksData.delta ?? 0,
        gamma: greeksData.net_gamma ?? greeksData.gamma ?? 0,
        theta: greeksData.net_theta ?? greeksData.theta ?? 0,
        vega: greeksData.net_vega ?? greeksData.vega ?? 0,
      }
    : fallbackGreeks;

  const drawdownPct = Math.min((risk.drawdown / risk.drawdownLimit) * 100, 100);
  const dailyLossPct = Math.min((risk.currentLoss / risk.maxLoss) * 100, 100);

  const cardStyle = {
    background: isDark ? 'rgba(19,23,32,0.6)' : 'rgba(255,255,255,0.82)',
    border: isDark ? '1px solid rgba(100,116,139,0.12)' : '1px solid rgba(0,0,0,0.06)',
    borderRadius: 12,
    boxShadow: isDark ? undefined : '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)',
  };

  return (
    <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <ShieldAlert style={{ width: 20, height: 20, color: '#ef4444' }} />
        <h1 style={{ fontSize: 20, fontWeight: 700, color: isDark ? '#f1f5f9' : '#1e293b', margin: 0 }}>Risk Management</h1>
      </div>

      {/* ── Kill Switch Banner ────────────────────────────────────────────── */}
      <KillSwitchBanner
        killSwitch={killSwitch}
        deactivateKillSwitch={deactivateKillSwitch}
        isActiveOnBackend={isKillSwitchActive}
        killMeta={killMeta}
        isDark={isDark}
      />

      {/* ── Live Breach Banner (auto-hides when no breaches) ───────────────── */}
      <BreachBanner isDark={isDark} />

      {/* ── 4 Risk Metric Cards ───────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>

        {/* Drawdown */}
        <div style={{ ...cardStyle, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#64748b' }}>
              Drawdown
            </span>
            <TrendingDown style={{ width: 14, height: 14, color: '#ef4444' }} />
          </div>
          <div style={{ fontSize: 9, color: '#64748b', marginTop: 1 }}>How much you've lost from your highest point</div>
          <div style={{ fontSize: 26, fontWeight: 800, fontFamily: 'monospace', color: drawdownPct > 70 ? '#ef4444' : drawdownPct > 40 ? '#f59e0b' : '#22c55e' }}>
            {risk.drawdown.toFixed(1)}%
          </div>
          <div style={{ marginTop: 10 }}>
            <div style={{ width: '100%', height: 5, background: 'rgba(100,116,139,0.18)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 4,
                width: `${drawdownPct}%`,
                background: drawdownPct > 70 ? 'linear-gradient(90deg,#dc2626,#ef4444)' : drawdownPct > 40 ? '#f59e0b' : '#22c55e',
                transition: 'width 0.5s ease',
              }} />
            </div>
          </div>
          <div style={{ fontSize: 10, color: '#475569', marginTop: 5 }}>
            {drawdownPct.toFixed(0)}% of {risk.drawdownLimit}% limit
          </div>
        </div>

        {/* VaR 95% */}
        <div style={{ ...cardStyle, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#64748b' }}>
              VaR 95%
            </span>
            <Activity style={{ width: 14, height: 14, color: '#f59e0b' }} />
          </div>
          <div style={{ fontSize: 9, color: '#64748b', marginTop: 1 }}>Maximum expected 1-day loss (95% confidence)</div>
          <div style={{ fontSize: 22, fontWeight: 800, fontFamily: 'monospace', color: '#f59e0b' }}>
            {formatIndian(risk.var95)}
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 8 }}>
            1-day at 95% confidence
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
            VaR 99%: <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>{formatIndian(risk.var99)}</span>
          </div>
        </div>

        {/* Daily Loss vs Limit */}
        <div style={{ ...cardStyle, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#64748b' }}>
              Daily Loss
            </span>
            <AlertTriangle style={{ width: 14, height: 14, color: dailyLossPct > 70 ? '#ef4444' : '#f59e0b' }} />
          </div>
          <div style={{ fontSize: 9, color: '#64748b', marginTop: 1 }}>Total loss today vs your configured daily limit</div>
          <div style={{ fontSize: 22, fontWeight: 800, fontFamily: 'monospace', color: dailyLossPct > 70 ? '#ef4444' : '#f87171' }}>
            {formatIndian(risk.currentLoss)}
          </div>
          <div style={{ marginTop: 10 }}>
            <div style={{ width: '100%', height: 5, background: 'rgba(100,116,139,0.18)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 4,
                width: `${dailyLossPct}%`,
                background: dailyLossPct > 80 ? 'linear-gradient(90deg,#dc2626,#ef4444)' : dailyLossPct > 50 ? '#f59e0b' : '#22c55e',
                transition: 'width 0.5s ease',
              }} />
            </div>
          </div>
          <div style={{ fontSize: 10, color: '#475569', marginTop: 5 }}>
            Limit: <span style={{ fontFamily: 'monospace' }}>{formatIndian(risk.maxLoss)}</span>
          </div>
        </div>

        {/* Sharpe Ratio */}
        <div style={{ ...cardStyle, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#64748b' }}>
              Sharpe Ratio
            </span>
            <BarChart3 style={{ width: 14, height: 14, color: '#3b82f6' }} />
          </div>
          <div style={{ fontSize: 9, color: '#64748b', marginTop: 1 }}>Risk-adjusted return — Above 1.5 is good, above 2.0 is excellent</div>
          <div style={{
            fontSize: 26, fontWeight: 800, fontFamily: 'monospace',
            color: risk.sharpe >= 1.5 ? '#22c55e' : risk.sharpe >= 0.8 ? '#f59e0b' : '#ef4444',
          }}>
            {risk.sharpe.toFixed(2)}
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 8 }}>
            {risk.sharpe >= 1.5 ? '✓ Excellent' : risk.sharpe >= 0.8 ? '~ Acceptable' : '✗ Below target'}
          </div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
            <span title="How much of your capital is locked as margin for open positions">Margin used</span>: <span style={{ color: '#94a3b8', fontFamily: 'monospace' }}>{risk.marginUsed}%</span>
          </div>
        </div>
      </div>

      {/* ── Circuit Breakers Grid ─────────────────────────────────────────── */}
      <div style={{ ...cardStyle, padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div>
            <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
              Circuit Breakers
            </h3>
            <p style={{ fontSize: 9, color: '#64748b', margin: '3px 0 0 0' }}>Automatic safety checks — triggers warning when limits are approached, halts trading when breached</p>
          </div>
          <span style={{ fontSize: 11, color: '#475569' }}>
            {circuitBreakers.filter((c) => c.status === 'healthy').length} / {circuitBreakers.length} healthy
          </span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12 }}>
          {circuitBreakers.map((cb) => {
            const pct = Math.min((cb.current / cb.threshold) * 100, 100);
            const barColor = pct > 80 ? '#ef4444' : pct > 60 ? '#f59e0b' : '#22c55e';
            const statusColor = cb.status === 'healthy' ? '#22c55e' : cb.status === 'warning' ? '#f59e0b' : '#ef4444';
            const statusBg = cb.status === 'healthy' ? 'rgba(34,197,94,0.1)' : cb.status === 'warning' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)';
            return (
              <div
                key={cb.id}
                style={{
                  background: isDark ? 'rgba(15,20,30,0.5)' : 'rgba(0,0,0,0.02)',
                  border: `1px solid ${pct > 80 ? 'rgba(239,68,68,0.25)' : pct > 60 ? 'rgba(245,158,11,0.2)' : 'rgba(100,116,139,0.12)'}`,
                  borderRadius: 10, padding: '14px 16px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{cb.name}</span>
                  <span style={{
                    fontSize: 10, fontWeight: 700, padding: '2px 7px',
                    background: statusBg, color: statusColor,
                    border: `1px solid ${statusColor}33`,
                    borderRadius: 5, textTransform: 'uppercase', letterSpacing: '0.05em',
                  }}>
                    {cb.status}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 8 }}>
                  <div>
                    <div style={{ fontSize: 10, color: '#475569', marginBottom: 2 }}>Current</div>
                    <div style={{ fontSize: 18, fontWeight: 800, fontFamily: 'monospace', color: barColor }}>
                      {cb.unit === 'Rs' ? formatIndian(cb.current) : `${cb.current}${cb.unit}`}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 10, color: '#475569', marginBottom: 2 }}>Limit</div>
                    <div style={{ fontSize: 13, fontFamily: 'monospace', color: '#64748b' }}>
                      {cb.unit === 'Rs' ? formatIndian(cb.threshold) : `${cb.threshold}${cb.unit}`}
                    </div>
                  </div>
                </div>
                {/* Progress bar */}
                <div style={{ width: '100%', height: 6, background: 'rgba(100,116,139,0.18)', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', borderRadius: 4,
                    width: `${pct}%`,
                    background: pct > 80
                      ? 'linear-gradient(90deg,#dc2626,#ef4444)'
                      : pct > 60 ? '#f59e0b' : '#22c55e',
                    transition: 'width 0.5s ease',
                    boxShadow: pct > 80 ? '0 0 8px rgba(239,68,68,0.4)' : 'none',
                  }} />
                </div>
                <div style={{ fontSize: 10, color: '#475569', marginTop: 4, textAlign: 'right' }}>
                  {pct.toFixed(0)}% utilized
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Greeks Exposure ───────────────────────────────────────────────── */}
      <div style={{ ...cardStyle, padding: '16px 20px' }}>
        <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: '0 0 4px 0' }}>
          Greeks Exposure vs Limits
        </h3>
        <p style={{ fontSize: 9, color: '#64748b', margin: '0 0 12px 0' }}>How sensitive your portfolio is to market changes</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
          {(['delta', 'gamma', 'theta', 'vega']).map((key) => {
            const val = greeks[key];
            const limit = greekLimits[key];
            const pct = Math.min((Math.abs(val) / limit) * 100, 100);
            const color = greekColors[key];
            const isNeg = val < 0;
            const barColor = pct > 80 ? '#ef4444' : pct > 60 ? '#f59e0b' : color;
            const greekTitles = {
              delta: 'Portfolio moves this much per Rs 1 change in NIFTY',
              gamma: 'Rate of Delta change — Higher = more sensitive to big moves',
              theta: 'Daily time decay — Negative means you lose this amount per day',
              vega: 'Sensitivity to volatility — P&L change per 1% IV move',
            };
            return (
              <div
                key={key}
                title={greekTitles[key]}
                style={{
                  background: isDark ? 'rgba(15,20,30,0.5)' : 'rgba(0,0,0,0.02)',
                  border: `1px solid ${pct > 80 ? 'rgba(239,68,68,0.2)' : 'rgba(100,116,139,0.12)'}`,
                  borderRadius: 10, padding: '14px 16px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b' }}>
                    {key.charAt(0).toUpperCase() + key.slice(1)}
                  </span>
                  {isNeg
                    ? <TrendingDown style={{ width: 14, height: 14, color: '#ef4444' }} />
                    : <TrendingUp style={{ width: 14, height: 14, color: '#22c55e' }} />
                  }
                </div>
                <div style={{ fontSize: 22, fontWeight: 800, fontFamily: 'monospace', color, marginBottom: 10 }}>
                  {val > 0 ? '+' : ''}{val.toFixed(2)}
                </div>
                {/* Segmented progress bar */}
                <div style={{ width: '100%', height: 6, background: 'rgba(100,116,139,0.18)', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', borderRadius: 4,
                    width: `${pct}%`,
                    background: pct > 80 ? 'linear-gradient(90deg,#dc2626,#ef4444)' : pct > 60 ? '#f59e0b' : color,
                    boxShadow: pct > 80 ? `0 0 6px ${color}66` : 'none',
                    transition: 'width 0.5s ease',
                  }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 5 }}>
                  <span style={{ fontSize: 10, color: '#475569' }}>{pct.toFixed(0)}% of limit</span>
                  <span style={{ fontSize: 10, color: '#475569', fontFamily: 'monospace' }}>
                    ±{limit > 100 ? limit.toFixed(0) : limit}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Stress Test Table ─────────────────────────────────────────────── */}
      <div style={{ ...cardStyle, padding: '16px 20px' }}>
        <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: '0 0 4px 0' }}>
          Stress Test Results
        </h3>
        <p style={{ fontSize: 9, color: '#64748b', margin: '0 0 12px 0' }}>What would happen to your portfolio under extreme market scenarios</p>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>
                {[
                  { label: 'Scenario', tip: 'Market shock scenario being simulated' },
                  { label: 'P&L Impact', tip: 'Estimated profit or loss if this scenario occurs' },
                  { label: 'Delta Impact', tip: 'How much your Delta exposure changes under this scenario' },
                  { label: 'Vega Impact', tip: 'How much your Vega exposure changes under this scenario' },
                  { label: 'Severity', tip: 'How dangerous this scenario is: LOW / MEDIUM / HIGH / CRITICAL' },
                ].map((col) => (
                  <th
                    key={col.label}
                    title={col.tip}
                    style={{
                      padding: '8px 14px',
                      textAlign: col.label === 'Scenario' ? 'left' : 'right',
                      fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                      letterSpacing: '0.07em', color: '#475569',
                      borderBottom: '1px solid rgba(100,116,139,0.12)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stressTests.map((row, i) => {
                const severity = Math.abs(row.pnlImpact);
                const severityLabel = severity > 300000 ? 'CRITICAL' : severity > 150000 ? 'HIGH' : severity > 80000 ? 'MEDIUM' : 'LOW';
                const severityColor = severity > 300000 ? '#ef4444' : severity > 150000 ? '#f97316' : severity > 80000 ? '#f59e0b' : '#22c55e';
                const severityBg = severity > 300000 ? 'rgba(239,68,68,0.12)' : severity > 150000 ? 'rgba(249,115,22,0.12)' : severity > 80000 ? 'rgba(245,158,11,0.12)' : 'rgba(34,197,94,0.1)';
                return (
                  <tr
                    key={i}
                    style={{
                      background: i % 2 === 0 ? 'transparent' : isDark ? 'rgba(15,20,30,0.3)' : 'rgba(0,0,0,0.02)',
                      borderLeft: row.pnlImpact < -200000 ? '2px solid rgba(239,68,68,0.4)' : '2px solid transparent',
                    }}
                  >
                    <td style={{ padding: '10px 14px', whiteSpace: 'nowrap' }}>
                      <span style={{ fontWeight: 600, color: '#f1f5f9' }}>{row.scenario}</span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <span style={{
                        fontFamily: 'monospace', fontWeight: 700,
                        color: row.pnlImpact >= 0 ? '#22c55e' : '#ef4444',
                      }}>
                        {row.pnlImpact >= 0 ? '+' : ''}{formatIndian(row.pnlImpact)}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <span style={{
                        fontFamily: 'monospace', fontWeight: 600,
                        color: row.deltaImpact >= 0 ? '#60a5fa' : '#f87171',
                      }}>
                        {row.deltaImpact >= 0 ? '+' : ''}{row.deltaImpact}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <span style={{
                        fontFamily: 'monospace', color: '#94a3b8',
                      }}>
                        {row.vegaImpact >= 0 ? '+' : ''}{row.vegaImpact?.toLocaleString('en-IN')}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <span style={{
                        fontSize: 10, fontWeight: 700, padding: '3px 8px',
                        background: severityBg, color: severityColor,
                        border: `1px solid ${severityColor}33`,
                        borderRadius: 5, letterSpacing: '0.05em',
                      }}>
                        {severityLabel}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Risk Limits Editor ───────────────────────────────────────────── */}
      <p style={{ fontSize: 10, color: '#64748b', margin: '4px 0 -8px 0' }}>Configure your trading safety limits — the system auto-stops trading when these are breached</p>
      <RiskLimitsEditor isDark={isDark} />
    </div>
  );
}

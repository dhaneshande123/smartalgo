import { useState, useEffect } from 'react';
import { Settings, Save, RotateCcw, AlertCircle, Check } from 'lucide-react';
import { useRiskLimits, useUpdateRiskLimits } from '../../hooks/useApi';

/**
 * Risk Limits Editor
 *
 * Inline editor for the configured risk limits. Reads current values via
 * /api/risk/limits, allows in-place edit, persists via POST.
 *
 * Conservative defaults match the backend's risk_engine.DEFAULT_RISK_LIMITS.
 */

const LIMIT_DEFS = [
  // Loss & drawdown
  { key: 'max_daily_loss',                  label: 'Max Daily Loss',                unit: 'Rs',  group: 'Loss',     step: 1000,  min: 1000,    description: 'Maximum allowed total P&L loss in a single trading day' },
  { key: 'max_drawdown_pct',                label: 'Max Drawdown',                  unit: '%',   group: 'Loss',     step: 0.5,   min: 1,        description: 'Maximum allowed peak-to-trough drawdown percentage' },
  { key: 'consecutive_losses_limit',        label: 'Max Consecutive Losses',        unit: '#',   group: 'Loss',     step: 1,     min: 2,        description: 'Trigger pause after N consecutive losing trades' },

  // Position sizing
  { key: 'max_position_value',              label: 'Max Position Value',            unit: 'Rs',  group: 'Sizing',   step: 100000,min: 100000,   description: 'Maximum notional value per single position' },
  { key: 'max_portfolio_value',             label: 'Max Portfolio Value',           unit: 'Rs',  group: 'Sizing',   step: 1000000,min: 1000000, description: 'Maximum total portfolio notional across all strategies' },
  { key: 'max_open_strategies',             label: 'Max Open Strategies',           unit: '#',   group: 'Sizing',   step: 1,     min: 1,        description: 'Maximum number of strategies running simultaneously' },
  { key: 'position_concentration_limit_pct',label: 'Max Concentration',             unit: '%',   group: 'Sizing',   step: 0.05,  min: 0.05,     description: 'Maximum % of capital in a single underlying' },

  // Greeks exposure
  { key: 'max_delta_exposure',              label: 'Max Net Delta',                 unit: '',    group: 'Greeks',   step: 50,    min: 50,       description: 'Maximum absolute net portfolio delta' },
  { key: 'max_gamma_exposure',              label: 'Max Net Gamma',                 unit: '',    group: 'Greeks',   step: 10,    min: 10,       description: 'Maximum absolute net portfolio gamma' },
  { key: 'max_vega_exposure',               label: 'Max Net Vega',                  unit: 'Rs',  group: 'Greeks',   step: 5000,  min: 5000,     description: 'Maximum portfolio vega (Rs per 1% IV move)' },
  { key: 'max_theta_decay',                 label: 'Max Theta Decay',               unit: 'Rs/d',group: 'Greeks',   step: 5000,  min: -100000,  description: 'Maximum tolerated daily theta decay (negative = against you)' },

  // Throughput
  { key: 'max_orders_per_minute',           label: 'Max Orders/min',                unit: '#',   group: 'Throughput', step: 5,   min: 5,        description: 'Order rate limit to prevent runaway loops' },

  // Behaviour
  { key: 'alert_threshold_pct',             label: 'Alert Threshold',               unit: '%',   group: 'Behaviour',step: 0.05,  min: 0.5,      description: 'Fire WARN alert at this % of any limit (0.80 = 80%)' },
  { key: 'auto_kill_threshold_pct',         label: 'Auto-Kill Threshold',           unit: '%',   group: 'Behaviour',step: 0.05,  min: 0.8,      description: 'Auto-engage kill switch at this % of daily_loss/drawdown' },
];

const GROUPS = ['Loss', 'Sizing', 'Greeks', 'Throughput', 'Behaviour'];

function formatValue(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (unit === 'Rs' || unit === 'Rs/d') {
    return 'Rs ' + Number(v).toLocaleString('en-IN');
  }
  if (unit === '%') {
    // Stored as percent already (e.g., 5.0 means 5%) OR ratio (0.80). Heuristic:
    // values >= 0 && <= 1 are ratios; show as multiplied. Others as-is.
    if (v <= 1 && (v === 0 || v >= 0.05)) {
      return (v * 100).toFixed(1) + '%';
    }
    return Number(v).toFixed(2) + '%';
  }
  return Number(v).toLocaleString('en-IN');
}

export default function RiskLimitsEditor({ isDark }) {
  const { data: limitsData, isLoading } = useRiskLimits();
  const updateMutation = useUpdateRiskLimits();
  const [edited, setEdited] = useState({});
  const [saveStatus, setSaveStatus] = useState('idle'); // idle | saving | saved | error

  // Reset local edits when fresh data lands
  useEffect(() => {
    if (limitsData && Object.keys(edited).length === 0) {
      // do nothing — initial load
    }
  }, [limitsData]);

  const currentValue = (key) => {
    if (key in edited) return edited[key];
    return limitsData?.[key] ?? '';
  };

  const isDirty = Object.keys(edited).length > 0;

  const handleChange = (key, raw) => {
    const num = parseFloat(raw);
    if (raw === '' || Number.isNaN(num)) {
      setEdited((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    } else {
      setEdited((prev) => ({ ...prev, [key]: num }));
    }
    setSaveStatus('idle');
  };

  const handleSave = async () => {
    if (!isDirty) return;
    setSaveStatus('saving');
    try {
      await updateMutation.mutateAsync(edited);
      setSaveStatus('saved');
      setEdited({});
      setTimeout(() => setSaveStatus('idle'), 2500);
    } catch (e) {
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 4000);
    }
  };

  const handleReset = () => {
    setEdited({});
    setSaveStatus('idle');
  };

  const cardStyle = {
    background: isDark ? 'rgba(19,23,32,0.6)' : 'rgba(255,255,255,0.82)',
    border: isDark ? '1px solid rgba(100,116,139,0.12)' : '1px solid rgba(0,0,0,0.06)',
    borderRadius: 12,
    padding: '18px 22px',
    boxShadow: isDark ? undefined : '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)',
  };

  const inputStyle = (isEdited) => ({
    width: '100%',
    padding: '6px 10px',
    background: isDark ? 'rgba(15,20,30,0.7)' : 'rgba(255,255,255,1)',
    border: `1px solid ${isEdited ? '#fbbf24' : isDark ? 'rgba(100,116,139,0.2)' : 'rgba(0,0,0,0.1)'}`,
    borderRadius: 6,
    color: isDark ? '#f1f5f9' : '#1e293b',
    fontFamily: 'monospace',
    fontSize: 13,
    fontWeight: 600,
    textAlign: 'right',
    outline: 'none',
  });

  return (
    <div style={cardStyle}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Settings style={{ width: 16, height: 16, color: '#7c3aed' }} />
          <h3 style={{ fontSize: 14, fontWeight: 700, color: isDark ? '#f1f5f9' : '#1e293b', margin: 0, letterSpacing: '0.02em' }}>
            Risk Limits Configuration
          </h3>
          {limitsData?.source === 'state_store' && (
            <span style={{
              fontSize: 9, fontWeight: 700, padding: '2px 6px', marginLeft: 4,
              background: 'rgba(34,197,94,0.12)', color: '#22c55e',
              border: '1px solid rgba(34,197,94,0.25)', borderRadius: 4,
              letterSpacing: '0.06em',
            }}>
              PERSISTED
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {saveStatus === 'saved' && (
            <span style={{ fontSize: 11, color: '#22c55e', display: 'flex', alignItems: 'center', gap: 4 }}>
              <Check style={{ width: 12, height: 12 }} /> Saved
            </span>
          )}
          {saveStatus === 'error' && (
            <span style={{ fontSize: 11, color: '#ef4444', display: 'flex', alignItems: 'center', gap: 4 }}>
              <AlertCircle style={{ width: 12, height: 12 }} /> Save failed
            </span>
          )}
          {isDirty && (
            <>
              <button
                onClick={handleReset}
                disabled={saveStatus === 'saving'}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '5px 10px',
                  background: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.04)',
                  border: '1px solid rgba(100,116,139,0.2)',
                  borderRadius: 6,
                  color: isDark ? '#cbd5e1' : '#475569',
                  fontSize: 11, fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                <RotateCcw style={{ width: 12, height: 12 }} /> Discard
              </button>
              <button
                onClick={handleSave}
                disabled={saveStatus === 'saving'}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '5px 12px',
                  background: 'linear-gradient(135deg, #7c3aed, #a855f7)',
                  border: 'none',
                  borderRadius: 6,
                  color: 'white',
                  fontSize: 11, fontWeight: 700,
                  cursor: saveStatus === 'saving' ? 'wait' : 'pointer',
                  boxShadow: '0 2px 6px rgba(124, 58, 237, 0.25)',
                  letterSpacing: '0.02em',
                }}
              >
                <Save style={{ width: 12, height: 12 }} />
                {saveStatus === 'saving' ? 'Saving…' : `Save ${Object.keys(edited).length}`}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div style={{ padding: 30, textAlign: 'center', color: '#64748b', fontSize: 12 }}>
          Loading risk limits…
        </div>
      )}

      {!isLoading && limitsData && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 18 }}>
          {GROUPS.map((group) => {
            const groupLimits = LIMIT_DEFS.filter((l) => l.group === group);
            if (groupLimits.length === 0) return null;
            return (
              <div key={group}>
                <div style={{
                  fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: '0.1em', color: '#64748b', marginBottom: 8,
                  borderBottom: `1px solid ${isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.06)'}`,
                  paddingBottom: 4,
                }}>
                  {group}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {groupLimits.map((def) => {
                    const val = currentValue(def.key);
                    const original = limitsData?.[def.key];
                    const isDirtyField = def.key in edited;
                    return (
                      <div key={def.key} style={{ display: 'grid', gridTemplateColumns: '1fr 130px', gap: 10, alignItems: 'center' }}>
                        <div title={def.description}>
                          <div style={{ fontSize: 12, fontWeight: 600, color: isDark ? '#cbd5e1' : '#334155' }}>
                            {def.label}
                          </div>
                          <div style={{ fontSize: 10, color: '#64748b', marginTop: 2 }}>
                            {def.description}
                          </div>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <input
                            type="number"
                            value={val}
                            step={def.step}
                            min={def.min}
                            onChange={(e) => handleChange(def.key, e.target.value)}
                            style={inputStyle(isDirtyField)}
                          />
                          {def.unit && (
                            <span style={{ fontSize: 10, fontWeight: 600, color: '#64748b', minWidth: 24 }}>
                              {def.unit}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Help footer */}
      <div style={{
        marginTop: 14, padding: '8px 12px',
        background: isDark ? 'rgba(124,58,237,0.06)' : 'rgba(124,58,237,0.04)',
        border: '1px solid rgba(124,58,237,0.15)', borderRadius: 6,
        fontSize: 11, color: '#7c3aed', lineHeight: 1.5,
      }}>
        <strong>Conservative auto-kill:</strong> When daily loss or drawdown hits 100% of limit,
        the system auto-engages the kill switch and squares off all open strategies.
        WARN alerts fire at the alert threshold (default 80%).
      </div>
    </div>
  );
}

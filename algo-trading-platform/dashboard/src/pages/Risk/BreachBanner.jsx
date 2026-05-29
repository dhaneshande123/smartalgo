import { AlertTriangle, Zap } from 'lucide-react';
import { useRiskBreaches } from '../../hooks/useApi';

/**
 * Live breach banner.
 * Polls /api/risk/breaches every 5s, shows the most severe breach.
 * Auto-hides when there are no breaches.
 */

export default function BreachBanner({ isDark }) {
  const { data } = useRiskBreaches();
  const breaches = data?.breaches || [];

  if (breaches.length === 0) return null;

  // Highest severity wins (BREACH > WARN)
  const sorted = [...breaches].sort((a, b) => {
    if (a.severity === 'BREACH' && b.severity !== 'BREACH') return -1;
    if (b.severity === 'BREACH' && a.severity !== 'BREACH') return 1;
    return (b.utilization_pct || 0) - (a.utilization_pct || 0);
  });

  const worst = sorted[0];
  const hasBreach = sorted.some((b) => b.severity === 'BREACH');
  const color = hasBreach ? '#ef4444' : '#f59e0b';
  const bgColor = hasBreach ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.08)';
  const borderColor = hasBreach ? 'rgba(239,68,68,0.3)' : 'rgba(245,158,11,0.3)';
  const Icon = hasBreach ? Zap : AlertTriangle;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 14,
      padding: '12px 18px',
      background: bgColor,
      border: `1px solid ${borderColor}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 10,
    }}>
      <Icon style={{ width: 22, height: 22, color, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: 700, color,
          marginBottom: 2, letterSpacing: '0.02em',
        }}>
          {hasBreach ? 'LIMIT BREACH' : 'APPROACHING LIMIT'}
          {sorted.length > 1 && (
            <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 600, opacity: 0.7 }}>
              +{sorted.length - 1} more
            </span>
          )}
        </div>
        <div style={{ fontSize: 12, color: isDark ? '#cbd5e1' : '#334155', lineHeight: 1.4 }}>
          {worst.message}
        </div>
      </div>
      {data?.should_auto_kill && (
        <span style={{
          fontSize: 10, fontWeight: 700, padding: '4px 9px',
          background: 'rgba(239,68,68,0.18)', color: '#ef4444',
          border: '1px solid rgba(239,68,68,0.35)', borderRadius: 5,
          letterSpacing: '0.06em', whiteSpace: 'nowrap',
        }}>
          AUTO-KILL ARMED
        </span>
      )}
    </div>
  );
}

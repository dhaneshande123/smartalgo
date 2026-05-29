import { useMemo } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell, Legend,
} from 'recharts';

// ─── Helpers ─────────────────────────────────────────────────────────────────

const num = (v) => Number(v) || 0;

const fmt = (v) => {
  const abs = Math.abs(v);
  if (abs >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)} K`;
  return v.toLocaleString('en-IN');
};

const fmtCompact = (v) => {
  const abs = Math.abs(v);
  if (abs >= 1e7) return `${(v / 1e7).toFixed(1)}Cr`;
  if (abs >= 1e5) return `${(v / 1e5).toFixed(1)}L`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
};

const pctStr = (v) => {
  const n = num(v);
  if (n === 0) return '0.0%';
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}%`;
};

// ─── Activity Classification ────────────────────────────────────────────────

function classifyActivity(oiChange, pricePct) {
  const oi = num(oiChange);
  const px = num(pricePct);
  if (Math.abs(oi) < 50 && Math.abs(px) < 0.05) return 'No Change';
  if (oi > 0 && px > 0)  return 'Long Build-Up';
  if (oi > 0 && px <= 0) return 'Short Build-Up';
  if (oi < 0 && px < 0)  return 'Long Unwinding';
  if (oi < 0 && px >= 0) return 'Short Covering';
  return 'No Change';
}

const ACTIVITY_COLORS = {
  'Long Build-Up':  { bg: 'rgba(16,185,129,0.15)', text: '#10b981', border: 'rgba(16,185,129,0.3)' },
  'Short Build-Up': { bg: 'rgba(239,68,68,0.15)',   text: '#ef4444', border: 'rgba(239,68,68,0.3)' },
  'Long Unwinding': { bg: 'rgba(249,115,22,0.15)',  text: '#f97316', border: 'rgba(249,115,22,0.3)' },
  'Short Covering':  { bg: 'rgba(59,130,246,0.15)',   text: '#3b82f6', border: 'rgba(59,130,246,0.3)' },
  'No Change':       { bg: 'rgba(100,116,139,0.1)',   text: '#64748b', border: 'rgba(100,116,139,0.2)' },
};

// ─── Sub-components ──────────────────────────────────────────────────────────

function ActivityBadge({ activity }) {
  const c = ACTIVITY_COLORS[activity] || ACTIVITY_COLORS['No Change'];
  return (
    <span
      className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
      style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}` }}
    >
      {activity}
    </span>
  );
}

function SummaryCard({ label, value, subtext, description, color, arrow, theme }) {
  const isDark = theme === 'dark';
  const cardBg = isDark
    ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' }
    : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.06)', boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' };

  return (
    <div className="rounded-xl p-4 flex flex-col items-center justify-center gap-1" style={cardBg}>
      <div className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">{label}</div>
      <div className="flex items-center gap-1">
        {arrow && (
          <span style={{ color }} className="text-lg font-bold">
            {arrow === 'up' ? '▲' : '▼'}
          </span>
        )}
        <span className="text-2xl font-bold font-mono" style={{ color: color || (isDark ? '#e2e8f0' : '#1e293b') }}>
          {value}
        </span>
      </div>
      {subtext && <div className="text-[11px] text-slate-500">{subtext}</div>}
      {description && (
        <div style={{ fontSize: 10, color: isDark ? '#64748b' : '#94a3b8', marginTop: 2, textAlign: 'center', lineHeight: '1.3' }}>
          {description}
        </div>
      )}
    </div>
  );
}

function OIChangeTooltip({ active, payload, label, theme }) {
  if (!active || !payload?.length) return null;
  const isDark = theme === 'dark';
  return (
    <div
      className="rounded-lg px-3 py-2 text-xs shadow-xl"
      style={isDark
        ? { background: 'rgba(19,23,32,0.97)', border: '1px solid rgba(100,116,139,0.2)' }
        : { background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(0,0,0,0.08)', boxShadow: '0 4px 20px rgba(0,0,0,0.1)' }
      }
    >
      <div className={`font-bold mb-1 ${isDark ? 'text-white' : 'text-slate-900'}`}>Strike {label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.fill || p.color }} className="flex gap-2">
          <span>{p.name}:</span>
          <span className="font-mono">{fmt(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

function SectionHeader({ children, theme }) {
  return (
    <div className={`text-[11px] font-bold uppercase tracking-widest mb-3 ${theme === 'dark' ? 'text-slate-400' : 'text-slate-600'}`}>
      {children}
    </div>
  );
}

// ─── Top Strikes Table ───────────────────────────────────────────────────────

function TopStrikesTable({ title, rows, theme, emptyMsg }) {
  const isDark = theme === 'dark';
  if (!rows || rows.length === 0) {
    return (
      <div>
        <div className={`text-[10px] font-bold uppercase tracking-widest mb-2 ${isDark ? 'text-slate-500' : 'text-slate-500'}`}>
          {title}
        </div>
        <div className={`text-xs italic ${isDark ? 'text-slate-600' : 'text-slate-400'}`}>{emptyMsg || 'No data'}</div>
      </div>
    );
  }
  return (
    <div>
      <div className={`text-[10px] font-bold uppercase tracking-widest mb-2 ${isDark ? 'text-slate-500' : 'text-slate-500'}`}>
        {title}
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className={isDark ? 'text-slate-500' : 'text-slate-500'}>
            <th className="text-left py-1 font-medium">Strike</th>
            <th className="text-right py-1 font-medium">OI Change</th>
            <th className="text-right py-1 font-medium">% Chg</th>
            <th className="text-right py-1 font-medium">Activity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.strike}
              className={`border-t ${isDark ? 'border-slate-800/30' : 'border-slate-100'}`}
            >
              <td className={`py-1.5 font-mono font-semibold ${isDark ? 'text-slate-200' : 'text-slate-800'}`}>
                {r.strike}
              </td>
              <td className={`py-1.5 text-right font-mono tabular-nums ${r.oiChange >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {r.oiChange >= 0 ? '+' : ''}{fmt(r.oiChange)}
              </td>
              <td className={`py-1.5 text-right font-mono tabular-nums ${num(r.oiChangePct) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {pctStr(r.oiChangePct)}
              </td>
              <td className="py-1.5 text-right">
                <ActivityBadge activity={r.activity} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// ─── Main Component ──────────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════════

export default function OIAnalysis({ chain, spot, symbol, vix, theme }) {
  const isDark = theme === 'dark';

  const cardStyle = isDark
    ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' }
    : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.06)', boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' };

  // ── Guard: empty data ──────────────────────────────────────────────────────

  const safeChain = useMemo(() => {
    if (!Array.isArray(chain) || chain.length === 0) return [];
    return chain;
  }, [chain]);

  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 1: Summary computations
  // ══════════════════════════════════════════════════════════════════════════

  const summary = useMemo(() => {
    if (safeChain.length === 0) {
      return {
        pcr: 0, pcrLabel: 'No Data', pcrColor: '#64748b',
        netCallOIChange: 0, netPutOIChange: 0,
        maxPainStrike: null, resistanceStrike: null, supportStrike: null,
      };
    }

    const totalCallOI = safeChain.reduce((a, r) => a + num(r.call_oi), 0);
    const totalPutOI = safeChain.reduce((a, r) => a + num(r.put_oi), 0);
    const pcr = totalCallOI > 0 ? totalPutOI / totalCallOI : 0;

    let pcrLabel, pcrColor;
    if (pcr < 0.7)       { pcrLabel = 'Bearish';  pcrColor = '#ef4444'; }
    else if (pcr <= 1.0)  { pcrLabel = 'Neutral';  pcrColor = '#f59e0b'; }
    else                  { pcrLabel = 'Bullish';  pcrColor = '#10b981'; }

    const netCallOIChange = safeChain.reduce((a, r) => a + num(r.call_oi_change), 0);
    const netPutOIChange = safeChain.reduce((a, r) => a + num(r.put_oi_change), 0);

    // Max Pain = strike with highest combined OI
    let maxPainStrike = null;
    let maxCombinedOI = 0;
    safeChain.forEach((r) => {
      const combined = num(r.call_oi) + num(r.put_oi);
      if (combined > maxCombinedOI) {
        maxCombinedOI = combined;
        maxPainStrike = r.strike;
      }
    });

    // Resistance = highest call OI strike
    let resistanceStrike = null;
    let maxCallOI = 0;
    safeChain.forEach((r) => {
      const callOI = num(r.call_oi);
      if (callOI > maxCallOI) {
        maxCallOI = callOI;
        resistanceStrike = r.strike;
      }
    });

    // Support = highest put OI strike
    let supportStrike = null;
    let maxPutOI = 0;
    safeChain.forEach((r) => {
      const putOI = num(r.put_oi);
      if (putOI > maxPutOI) {
        maxPutOI = putOI;
        supportStrike = r.strike;
      }
    });

    return {
      pcr, pcrLabel, pcrColor,
      netCallOIChange, netPutOIChange,
      maxPainStrike, resistanceStrike, supportStrike,
    };
  }, [safeChain]);

  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 2: OI Interpretation Table (±10 strikes around ATM)
  // ══════════════════════════════════════════════════════════════════════════

  const interpretationRows = useMemo(() => {
    if (safeChain.length === 0) return [];

    const atmIdx = safeChain.findIndex((r) => r.isATM);
    const centerIdx = atmIdx >= 0 ? atmIdx : Math.floor(safeChain.length / 2);
    const startIdx = Math.max(0, centerIdx - 10);
    const endIdx = Math.min(safeChain.length, centerIdx + 11);
    const slice = safeChain.slice(startIdx, endIdx);

    return slice.map((r) => ({
      strike: r.strike,
      isATM: r.isATM,
      callActivity: classifyActivity(r.call_oi_change, r.call_change_pct),
      callOIChange: num(r.call_oi_change),
      callPricePct: num(r.call_change_pct),
      putActivity: classifyActivity(r.put_oi_change, r.put_change_pct),
      putOIChange: num(r.put_oi_change),
      putPricePct: num(r.put_change_pct),
    }));
  }, [safeChain]);

  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 3: Top Strikes (buildup / unwinding)
  // ══════════════════════════════════════════════════════════════════════════

  const topStrikes = useMemo(() => {
    if (safeChain.length === 0) {
      return { callBuildup: [], callUnwinding: [], putBuildup: [], putUnwinding: [] };
    }

    const withCallActivity = safeChain.map((r) => ({
      strike: r.strike,
      oiChange: num(r.call_oi_change),
      oiChangePct: num(r.call_oi_change_pct),
      activity: classifyActivity(r.call_oi_change, r.call_change_pct),
    }));

    const withPutActivity = safeChain.map((r) => ({
      strike: r.strike,
      oiChange: num(r.put_oi_change),
      oiChangePct: num(r.put_oi_change_pct),
      activity: classifyActivity(r.put_oi_change, r.put_change_pct),
    }));

    const callBuildup = [...withCallActivity]
      .filter((r) => r.oiChange > 0)
      .sort((a, b) => b.oiChange - a.oiChange)
      .slice(0, 5);

    const callUnwinding = [...withCallActivity]
      .filter((r) => r.oiChange < 0)
      .sort((a, b) => a.oiChange - b.oiChange)
      .slice(0, 5);

    const putBuildup = [...withPutActivity]
      .filter((r) => r.oiChange > 0)
      .sort((a, b) => b.oiChange - a.oiChange)
      .slice(0, 5);

    const putUnwinding = [...withPutActivity]
      .filter((r) => r.oiChange < 0)
      .sort((a, b) => a.oiChange - b.oiChange)
      .slice(0, 5);

    return { callBuildup, callUnwinding, putBuildup, putUnwinding };
  }, [safeChain]);

  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 4: OI Change Bar Chart Data (±15 strikes around ATM)
  // ══════════════════════════════════════════════════════════════════════════

  const chartData = useMemo(() => {
    if (safeChain.length === 0) return [];

    const atmIdx = safeChain.findIndex((r) => r.isATM);
    const centerIdx = atmIdx >= 0 ? atmIdx : Math.floor(safeChain.length / 2);
    const startIdx = Math.max(0, centerIdx - 15);
    const endIdx = Math.min(safeChain.length, centerIdx + 16);

    return safeChain.slice(startIdx, endIdx).map((r) => ({
      strike: r.strike,
      'Call OI Change': num(r.call_oi_change),
      'Put OI Change': num(r.put_oi_change),
      isATM: r.isATM,
    }));
  }, [safeChain]);

  // ── Empty state ────────────────────────────────────────────────────────────

  if (safeChain.length === 0) {
    return (
      <div className="rounded-xl p-8 text-center" style={cardStyle}>
        <div className={`text-sm ${isDark ? 'text-slate-500' : 'text-slate-400'}`}>
          No option chain data available for OI analysis.
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════════════════
  // RENDER
  // ══════════════════════════════════════════════════════════════════════════

  return (
    <div className="space-y-4 animate-fade-in">

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* SECTION 1: Summary Cards                                          */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div>
        <SectionHeader theme={theme}>OI Analysis Summary</SectionHeader>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">

          {/* PCR (OI) */}
          <div className="rounded-xl p-4 flex flex-col items-center justify-center gap-1" style={cardStyle}>
            <div className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">PCR (OI)</div>
            <div className="text-2xl font-bold font-mono" style={{ color: summary.pcrColor }}>
              {summary.pcr.toFixed(2)}
            </div>
            <span
              className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
              style={{ background: `${summary.pcrColor}22`, color: summary.pcrColor }}
            >
              {summary.pcrLabel}
            </span>
            <div style={{ fontSize: 10, color: isDark ? '#64748b' : '#94a3b8', marginTop: 2, textAlign: 'center', lineHeight: '1.3' }}>
              Higher = more puts = bullish for market
            </div>
          </div>

          {/* Net Call OI Change */}
          <SummaryCard
            label="Net Call OI Chg"
            value={fmt(summary.netCallOIChange)}
            subtext={summary.netCallOIChange > 0 ? 'Call Writing (Bearish)' : summary.netCallOIChange < 0 ? 'Call Unwinding (Bullish)' : '—'}
            description="Rising = sellers expect price won't go above"
            color={summary.netCallOIChange > 0 ? '#ef4444' : summary.netCallOIChange < 0 ? '#10b981' : '#64748b'}
            arrow={summary.netCallOIChange > 0 ? 'up' : summary.netCallOIChange < 0 ? 'down' : null}
            theme={theme}
          />

          {/* Net Put OI Change */}
          <SummaryCard
            label="Net Put OI Chg"
            value={fmt(summary.netPutOIChange)}
            subtext={summary.netPutOIChange > 0 ? 'Put Writing (Bullish)' : summary.netPutOIChange < 0 ? 'Put Unwinding (Bearish)' : '—'}
            description="Rising = sellers expect price won't fall below"
            color={summary.netPutOIChange > 0 ? '#10b981' : summary.netPutOIChange < 0 ? '#ef4444' : '#64748b'}
            arrow={summary.netPutOIChange > 0 ? 'up' : summary.netPutOIChange < 0 ? 'down' : null}
            theme={theme}
          />

          {/* Max Pain Strike */}
          <SummaryCard
            label="Max Pain"
            value={summary.maxPainStrike != null ? summary.maxPainStrike.toLocaleString('en-IN') : '—'}
            subtext="Highest combined OI"
            description="Market often gravitates here by expiry"
            color="#f59e0b"
            theme={theme}
          />

          {/* Resistance */}
          <SummaryCard
            label="Resistance"
            value={summary.resistanceStrike != null ? summary.resistanceStrike.toLocaleString('en-IN') : '—'}
            subtext="Max Call OI"
            description="Sellers defend this — price struggles above"
            color="#ef4444"
            theme={theme}
          />

          {/* Support */}
          <SummaryCard
            label="Support"
            value={summary.supportStrike != null ? summary.supportStrike.toLocaleString('en-IN') : '—'}
            subtext="Max Put OI"
            description="Sellers defend this — price unlikely below"
            color="#10b981"
            theme={theme}
          />
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* SECTION 2: OI Interpretation Table                                */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div className="rounded-xl overflow-hidden" style={cardStyle}>
        <div className={`px-4 py-3 border-b ${isDark ? 'border-slate-800/60' : 'border-slate-200'}`}>
          <SectionHeader theme={theme}>Market Maker Activity Analysis</SectionHeader>
          <div className="flex flex-wrap gap-3 mt-2">
            {[
              { label: 'Long Build-Up', desc: '(OI ↑ + Price ↑) Fresh buying — bullish' },
              { label: 'Short Build-Up', desc: '(OI ↑ + Price ↓) Fresh selling — bearish' },
              { label: 'Long Unwinding', desc: '(OI ↓ + Price ↓) Buyers exiting — fading' },
              { label: 'Short Covering', desc: '(OI ↓ + Price ↑) Sellers closing — can push up' },
            ].map(({ label, desc }) => {
              const c = ACTIVITY_COLORS[label];
              return (
                <div key={label} className="flex items-start gap-1.5">
                  <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap shrink-0"
                    style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}` }}>
                    {label}
                  </span>
                  <span style={{ fontSize: 10, color: isDark ? '#64748b' : '#94a3b8', lineHeight: '1.4' }}>
                    {desc}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="overflow-auto" style={{ maxHeight: '520px' }}>
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10">
              <tr style={{ background: isDark ? 'rgba(11,14,20,0.97)' : 'rgba(248,250,252,0.97)' }}>
                <th className={`px-3 py-2 text-center font-semibold ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>Strike</th>
                <th className="px-3 py-2 text-center font-semibold text-emerald-400">Call Activity</th>
                <th className="px-3 py-2 text-right font-semibold text-emerald-400">Call OI Chg</th>
                <th className="px-3 py-2 text-right font-semibold text-emerald-400">Call Price %</th>
                <th className="px-3 py-2 text-center font-semibold text-red-400">Put Activity</th>
                <th className="px-3 py-2 text-right font-semibold text-red-400">Put OI Chg</th>
                <th className="px-3 py-2 text-right font-semibold text-red-400">Put Price %</th>
              </tr>
            </thead>
            <tbody>
              {interpretationRows.length === 0 ? (
                <tr>
                  <td colSpan={7} className={`px-4 py-6 text-center italic ${isDark ? 'text-slate-600' : 'text-slate-400'}`}>
                    No strikes to analyze
                  </td>
                </tr>
              ) : (
                interpretationRows.map((row) => (
                  <tr
                    key={row.strike}
                    className={`border-t transition-colors duration-100 ${isDark ? 'border-slate-800/30 hover:bg-slate-700/15' : 'border-slate-100 hover:bg-slate-50'}
                      ${row.isATM ? 'border-l-2 border-l-blue-500' : ''}`}
                  >
                    <td className={`px-3 py-2 text-center font-mono font-bold ${row.isATM ? 'text-blue-400' : isDark ? 'text-slate-200' : 'text-slate-800'}`}>
                      {row.strike}
                      {row.isATM && <span className="ml-1 text-[9px] font-bold text-blue-400">ATM</span>}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <ActivityBadge activity={row.callActivity} />
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${row.callOIChange >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {row.callOIChange >= 0 ? '+' : ''}{fmt(row.callOIChange)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${row.callPricePct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pctStr(row.callPricePct)}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <ActivityBadge activity={row.putActivity} />
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${row.putOIChange >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {row.putOIChange >= 0 ? '+' : ''}{fmt(row.putOIChange)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${row.putPricePct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pctStr(row.putPricePct)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* SECTION 3: Top Strikes Dashboard                                  */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div>
        <SectionHeader theme={theme}>Top Strikes Dashboard</SectionHeader>
        <p style={{ fontSize: 10, color: isDark ? '#64748b' : '#94a3b8', marginTop: -8, marginBottom: 10, lineHeight: '1.3' }}>
          Where market makers are building or exiting the most positions
        </p>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

          {/* Left: Call Side */}
          <div className="rounded-xl p-4 space-y-5" style={cardStyle}>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block" />
              <span className={`text-xs font-bold uppercase tracking-widest ${isDark ? 'text-emerald-400' : 'text-emerald-600'}`}>
                Call Side
              </span>
            </div>

            <TopStrikesTable
              title="Top 5 Call OI Buildup"
              rows={topStrikes.callBuildup}
              theme={theme}
              emptyMsg="No call OI buildup detected"
            />

            <div className={`border-t ${isDark ? 'border-slate-800/40' : 'border-slate-200'} pt-4`}>
              <TopStrikesTable
                title="Top 5 Call OI Unwinding"
                rows={topStrikes.callUnwinding}
                theme={theme}
                emptyMsg="No call OI unwinding detected"
              />
            </div>
          </div>

          {/* Right: Put Side */}
          <div className="rounded-xl p-4 space-y-5" style={cardStyle}>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block" />
              <span className={`text-xs font-bold uppercase tracking-widest ${isDark ? 'text-red-400' : 'text-red-600'}`}>
                Put Side
              </span>
            </div>

            <TopStrikesTable
              title="Top 5 Put OI Buildup"
              rows={topStrikes.putBuildup}
              theme={theme}
              emptyMsg="No put OI buildup detected"
            />

            <div className={`border-t ${isDark ? 'border-slate-800/40' : 'border-slate-200'} pt-4`}>
              <TopStrikesTable
                title="Top 5 Put OI Unwinding"
                rows={topStrikes.putUnwinding}
                theme={theme}
                emptyMsg="No put OI unwinding detected"
              />
            </div>
          </div>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════ */}
      {/* SECTION 4: OI Change Bar Chart                                    */}
      {/* ═══════════════════════════════════════════════════════════════════ */}

      <div className="rounded-xl p-4" style={cardStyle}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <SectionHeader theme={theme}>OI Change Distribution</SectionHeader>
            <p className={`text-[11px] -mt-2 ${isDark ? 'text-slate-500' : 'text-slate-500'}`}>
              Call vs Put OI change per strike ({'±'}15 strikes around ATM)
            </p>
            <p style={{ fontSize: 10, color: isDark ? '#64748b' : '#94a3b8', marginTop: 2, lineHeight: '1.3' }}>
              Green bars = new call contracts, Red bars = new put contracts. Taller = more activity.
            </p>
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="flex items-center gap-1">
              <span className="w-3 h-2 rounded-sm bg-emerald-500 inline-block" /> Call OI Chg
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-2 rounded-sm bg-red-500 inline-block" /> Put OI Chg
            </span>
          </div>
        </div>

        {chartData.length === 0 ? (
          <div className={`text-center py-12 text-sm italic ${isDark ? 'text-slate-600' : 'text-slate-400'}`}>
            No chart data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData} barCategoryGap="20%" margin={{ top: 8, right: 12, bottom: 4, left: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.1)" />
              <XAxis
                dataKey="strike"
                tick={{ fill: '#64748b', fontSize: 10 }}
                stroke="rgba(100,116,139,0.15)"
                tickFormatter={(v) => String(v)}
                angle={-45}
                textAnchor="end"
                height={50}
              />
              <YAxis
                tick={{ fill: '#64748b', fontSize: 10 }}
                stroke="rgba(100,116,139,0.15)"
                tickFormatter={(v) => fmtCompact(v)}
                width={52}
              />
              <Tooltip content={<OIChangeTooltip theme={theme} />} />
              <Legend
                wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }}
                formatter={(value) => (
                  <span style={{ color: '#94a3b8', fontSize: '11px' }}>{value}</span>
                )}
              />
              <ReferenceLine y={0} stroke="rgba(100,116,139,0.3)" strokeDasharray="3 3" />
              <Bar dataKey="Call OI Change" radius={[3, 3, 0, 0]}>
                {chartData.map((entry) => (
                  <Cell
                    key={`call-${entry.strike}`}
                    fill={entry.isATM ? '#3b82f6' : '#10b981'}
                    fillOpacity={entry.isATM ? 1 : 0.8}
                  />
                ))}
              </Bar>
              <Bar dataKey="Put OI Change" radius={[3, 3, 0, 0]}>
                {chartData.map((entry) => (
                  <Cell
                    key={`put-${entry.strike}`}
                    fill={entry.isATM ? '#60a5fa' : '#ef4444'}
                    fillOpacity={entry.isATM ? 1 : 0.8}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

    </div>
  );
}

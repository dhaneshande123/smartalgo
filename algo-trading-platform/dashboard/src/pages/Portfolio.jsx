import { useState, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, RefreshCw, Search,
  Activity, DollarSign, BarChart3, Layers,
} from 'lucide-react';
import {
  AreaChart, Area, BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import Card from '../components/common/Card';
import { useTheme } from '../context/ThemeContext';
import { usePositions, usePnL, useGreeks, useMarginUsage, usePnLEquityCurve } from '../hooks/useApi';

// ── Fallback data (zeros — no mock) ──────────────────────────────────────────
const fallbackPositions = [];
const fallbackEquityCurve = [];
const fallbackPnL = { realized: 0, unrealized: 0, charges: 0, net: 0 };
const fallbackMargin = { used: 0, available: 0, total: 0, utilization: 0 };
const fallbackGreeks = { delta: 0, gamma: 0, theta: 0, vega: 0 };

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

function formatIndianFull(num) {
  if (num === null || num === undefined) return 'Rs 0';
  const abs = Math.abs(num);
  const str = abs.toFixed(2);
  const [intPart, decPart] = str.split('.');
  const lastThree = intPart.slice(-3);
  const rest = intPart.slice(0, -3);
  const formatted = rest
    ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + lastThree
    : lastThree;
  return (num < 0 ? '-' : '') + 'Rs\u00A0' + formatted + '.' + decPart;
}

// ── Custom Tooltip for AreaChart ──────────────────────────────────────────────
const EquityTooltip = ({ active, payload, label, theme }) => {
  if (!active || !payload || !payload.length) return null;
  const isDark = theme !== 'light';
  return (
    <div style={{
      background: isDark ? 'rgba(11,14,20,0.97)' : 'rgba(255,255,255,0.97)',
      border: isDark ? '1px solid rgba(124,58,237,0.4)' : '1px solid rgba(0,0,0,0.08)',
      borderRadius: 8,
      padding: '8px 14px',
      boxShadow: isDark ? '0 4px 24px rgba(0,0,0,0.5)' : '0 4px 20px rgba(0,0,0,0.1)',
    }}>
      <p style={{ color: isDark ? '#94a3b8' : '#64748b', fontSize: 11, marginBottom: 4 }}>{label}</p>
      <p style={{ color: '#a78bfa', fontWeight: 700, fontSize: 15, fontFamily: 'monospace' }}>
        {formatIndian(payload[0].value)}
      </p>
    </div>
  );
};

// ── Custom Tooltip for BarChart ───────────────────────────────────────────────
const PnLTooltip = ({ active, payload, label, theme }) => {
  if (!active || !payload || !payload.length) return null;
  const val = payload[0].value;
  const isDark = theme !== 'light';
  return (
    <div style={{
      background: isDark ? 'rgba(11,14,20,0.97)' : 'rgba(255,255,255,0.97)',
      border: isDark
        ? `1px solid ${val >= 0 ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)'}`
        : '1px solid rgba(0,0,0,0.08)',
      borderRadius: 8,
      padding: '8px 14px',
      boxShadow: isDark ? undefined : '0 4px 20px rgba(0,0,0,0.1)',
    }}>
      <p style={{ color: isDark ? '#94a3b8' : '#64748b', fontSize: 11, marginBottom: 4 }}>{label}</p>
      <p style={{ color: val >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, fontFamily: 'monospace', fontSize: 13 }}>
        {val >= 0 ? '+' : ''}{formatIndian(val)}
      </p>
    </div>
  );
};

// ── Greek limits for progress bars ───────────────────────────────────────────
const greekLimits = { delta: 500, gamma: 100, theta: 25000, vega: 50000 };
const greekColors = { delta: '#3b82f6', gamma: '#f59e0b', theta: '#10b981', vega: '#a78bfa' };

// ── ATM detection helper ──────────────────────────────────────────────────────
function isATM(symbol) {
  // Crude check: symbol contains NIFTY 24000 or BANKNIFTY 51200 style ATM strikes
  // We flag if the strike is within the last seen ATM band
  const atmStrikes = ['24000', '24100', '51200', '51000'];
  return atmStrikes.some((s) => symbol.includes(s));
}

// ─────────────────────────────────────────────────────────────────────────────
export default function Portfolio() {
  const { theme } = useTheme();
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [lastRefreshed, setLastRefreshed] = useState(new Date());

  const { data: positionsData, refetch: refetchPositions } = usePositions();
  const { data: pnlData, refetch: refetchPnL } = usePnL();
  const { data: greeksData } = useGreeks();
  const { data: marginData } = useMarginUsage();
  const { data: equityCurveData } = usePnLEquityCurve();

  // ── Normalize positions ───────────────────────────────────────────────────
  const rawPositions = positionsData?.positions || positionsData;
  const positions = Array.isArray(rawPositions)
    ? rawPositions.map((p) => ({
        ...p,
        // Normalize field names — backend may send qty/quantity, avgPrice/avg_price etc.
        symbol: p.symbol || p.instrument || '',
        type: p.type || p.option_type || (p.symbol?.endsWith('CE') ? 'CE' : p.symbol?.endsWith('PE') ? 'PE' : ''),
        qty: Number(p.qty ?? p.quantity ?? 0),
        avgPrice: Number(p.avgPrice ?? p.avg_price ?? p.average_price ?? 0),
        ltp: Number(p.ltp ?? 0),
        pnl: Number(p.pnl ?? p.pnl_unrealized ?? 0),
        strategy: p.strategy || p.strategy_id || '',
        expiry: p.expiry || '',
      }))
    : fallbackPositions;

  // ── Normalize P&L ─────────────────────────────────────────────────────────
  const pnl = pnlData
    ? {
        realized: pnlData.realized_pnl ?? pnlData.realized ?? 0,
        unrealized: pnlData.unrealized_pnl ?? pnlData.unrealized ?? 0,
        charges: pnlData.charges?.total ?? pnlData.charges ?? 0,
        net: pnlData.net_pnl ?? pnlData.net ?? 0,
      }
    : fallbackPnL;

  // ── Normalize margin ──────────────────────────────────────────────────────
  const margin = marginData
    ? {
        used: marginData.margin_used ?? marginData.used ?? 0,
        available: marginData.margin_available ?? marginData.available ?? 0,
        total: marginData.total_margin ?? marginData.total ?? 0,
        utilization: Math.round(
          (marginData.margin_utilization ?? marginData.utilization ?? 0) *
            (marginData.margin_utilization <= 1 ? 100 : 1)
        ),
      }
    : fallbackMargin;

  // ── Normalize Equity Curve ────────────────────────────────────────────────
  const rawCurve = equityCurveData?.curve || equityCurveData;
  const equityCurve = Array.isArray(rawCurve) && rawCurve.length > 0 ? rawCurve : fallbackEquityCurve;

  // ── Normalize Greeks ──────────────────────────────────────────────────────
  const greeks = greeksData
    ? {
        delta: greeksData.net_delta ?? greeksData.delta ?? 0,
        gamma: greeksData.net_gamma ?? greeksData.gamma ?? 0,
        theta: greeksData.net_theta ?? greeksData.theta ?? 0,
        vega: greeksData.net_vega ?? greeksData.vega ?? 0,
      }
    : fallbackGreeks;

  // ── P&L by strategy (for BarChart) ───────────────────────────────────────
  const strategyPnLMap = useMemo(() => {
    const m = {};
    positions.forEach((p) => {
      const s = p.strategy || 'Unallocated';
      m[s] = (m[s] || 0) + (p.pnl || 0);
    });
    return Object.entries(m).map(([name, value]) => ({ name, value }));
  }, [positions]);

  // ── Filtered positions ────────────────────────────────────────────────────
  const filteredPositions = useMemo(() => {
    return positions.filter((p) => {
      const matchSearch = !search || p.symbol.toLowerCase().includes(search.toLowerCase()) ||
        (p.strategy || '').toLowerCase().includes(search.toLowerCase());
      const matchType = typeFilter === 'ALL' || p.type === typeFilter;
      return matchSearch && matchType;
    });
  }, [positions, search, typeFilter]);

  const handleRefresh = () => {
    refetchPositions();
    refetchPnL();
    setLastRefreshed(new Date());
  };

  const netPositive = pnl.net >= 0;
  const realizedPositive = pnl.realized >= 0;
  const unrealizedPositive = pnl.unrealized >= 0;

  // ── Shared card style ─────────────────────────────────────────────────────
  const isDark = theme !== 'light';
  const cardStyle = isDark
    ? {
        background: 'rgba(19,23,32,0.6)',
        border: '1px solid rgba(100,116,139,0.12)',
        borderRadius: 12,
      }
    : {
        background: 'rgba(255,255,255,0.82)',
        border: '1px solid rgba(0,0,0,0.08)',
        borderRadius: 12,
        boxShadow: '0 4px 20px rgba(0,0,0,0.1)',
      };

  return (
    <div className="animate-fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Header Row ───────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Layers style={{ width: 20, height: 20, color: '#7c3aed' }} />
          <h1 style={{ fontSize: 20, fontWeight: 700, color: isDark ? '#f1f5f9' : '#1e293b', margin: 0 }}>Portfolio</h1>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)',
            borderRadius: 20, padding: '3px 10px',
          }}>
            <div style={{
              width: 7, height: 7, borderRadius: '50%', background: '#22c55e',
              animation: 'pulse 2s infinite',
            }} />
            <span style={{ fontSize: 11, color: '#22c55e', fontWeight: 600 }}>LIVE</span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 11, color: '#64748b' }}>
            Updated {lastRefreshed.toLocaleTimeString()}
          </span>
          <button
            onClick={handleRefresh}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px',
              background: 'rgba(59,130,246,0.12)', border: '1px solid rgba(59,130,246,0.3)',
              borderRadius: 8, color: '#60a5fa', fontSize: 13, fontWeight: 500, cursor: 'pointer',
            }}
          >
            <RefreshCw style={{ width: 14, height: 14 }} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── 4 Metric Cards ───────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
        {[
          {
            label: 'Net P&L',
            value: pnl.net,
            positive: netPositive,
            icon: <Activity style={{ width: 16, height: 16, color: netPositive ? '#22c55e' : '#ef4444' }} />,
            subtitle: 'After charges',
          },
          {
            label: 'Realized P&L',
            value: pnl.realized,
            positive: realizedPositive,
            icon: <DollarSign style={{ width: 16, height: 16, color: realizedPositive ? '#22c55e' : '#ef4444' }} />,
            subtitle: 'Booked trades',
          },
          {
            label: 'Unrealized P&L',
            value: pnl.unrealized,
            positive: unrealizedPositive,
            icon: <TrendingUp style={{ width: 16, height: 16, color: unrealizedPositive ? '#22c55e' : '#ef4444' }} />,
            subtitle: 'Open positions',
          },
          {
            label: 'Margin Used',
            value: null,
            percent: margin.utilization,
            positive: margin.utilization < 70,
            icon: <BarChart3 style={{ width: 16, height: 16, color: margin.utilization > 80 ? '#ef4444' : margin.utilization > 60 ? '#f59e0b' : '#3b82f6' }} />,
            subtitle: `${formatIndian(margin.used)} of ${formatIndian(margin.total)}`,
          },
        ].map((card, i) => (
          <div key={i} style={{ ...cardStyle, padding: '16px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#64748b' }}>
                {card.label}
              </span>
              {card.icon}
            </div>
            {card.value !== null ? (
              <div style={{
                fontSize: 22, fontWeight: 800, fontFamily: 'monospace',
                color: card.positive ? '#22c55e' : '#ef4444',
              }}>
                {card.value >= 0 ? '+' : ''}{formatIndianFull(card.value)}
              </div>
            ) : (
              <>
                <div style={{
                  fontSize: 22, fontWeight: 800, fontFamily: 'monospace',
                  color: card.percent > 80 ? '#ef4444' : card.percent > 60 ? '#f59e0b' : '#3b82f6',
                }}>
                  {card.percent}%
                </div>
                <div style={{ marginTop: 8 }}>
                  <div style={{ width: '100%', height: 4, background: 'rgba(100,116,139,0.2)', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%', borderRadius: 4,
                      width: `${card.percent}%`,
                      background: card.percent > 80 ? '#ef4444' : card.percent > 60 ? '#f59e0b' : '#3b82f6',
                      transition: 'width 0.5s ease',
                    }} />
                  </div>
                </div>
              </>
            )}
            <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>{card.subtitle}</div>
          </div>
        ))}
      </div>

      {/* ── Middle Row: Equity Curve + Strategy P&L ──────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>

        {/* Equity Curve */}
        <div style={{ ...cardStyle, padding: '16px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
              Portfolio Equity Curve
            </h3>
            <span style={{ fontSize: 11, color: '#475569' }}>Intraday</span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={equityCurve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.35} />
                  <stop offset="50%" stopColor="#3b82f6" stopOpacity={0.12} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={isDark ? '#1e2433' : '#e2e8f0'} vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fill: '#475569', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                interval={4}
              />
              <YAxis
                tick={{ fill: '#475569', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `${(v / 1e6).toFixed(2)}M`}
                width={54}
              />
              <Tooltip content={<EquityTooltip theme={theme} />} />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#7c3aed"
                strokeWidth={2}
                fill="url(#equityGradient)"
                dot={false}
                activeDot={{ r: 4, fill: '#7c3aed', strokeWidth: 0 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* P&L by Strategy */}
        <div style={{ ...cardStyle, padding: '16px 20px' }}>
          <div style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
              P&amp;L by Strategy
            </h3>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={strategyPnLMap}
              layout="vertical"
              margin={{ top: 0, right: 10, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={isDark ? '#1e2433' : '#e2e8f0'} horizontal={false} />
              <XAxis
                type="number"
                tick={{ fill: '#475569', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `${v >= 0 ? '+' : ''}${(v / 1000).toFixed(0)}K`}
              />
              <YAxis
                type="category"
                dataKey="name"
                tick={{ fill: '#94a3b8', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={110}
              />
              <Tooltip content={<PnLTooltip theme={theme} />} />
              <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={22}>
                {strategyPnLMap.map((entry, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={entry.value >= 0 ? 'rgba(34,197,94,0.75)' : 'rgba(239,68,68,0.75)'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ── Greeks Row ───────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
        {(['delta', 'gamma', 'theta', 'vega']).map((key) => {
          const val = greeks[key];
          const limit = greekLimits[key];
          const pct = Math.min((Math.abs(val) / limit) * 100, 100);
          const color = greekColors[key];
          const isNeg = val < 0;
          return (
            <div key={key} style={{ ...cardStyle, padding: '14px 18px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b' }}>
                  {key.charAt(0).toUpperCase() + key.slice(1)}
                </span>
                {isNeg
                  ? <TrendingDown style={{ width: 14, height: 14, color: '#ef4444' }} />
                  : <TrendingUp style={{ width: 14, height: 14, color: '#22c55e' }} />
                }
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, fontFamily: 'monospace', color, marginBottom: 8 }}>
                {val > 0 ? '+' : ''}{val.toFixed(2)}
              </div>
              <div style={{ width: '100%', height: 4, background: 'rgba(100,116,139,0.15)', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{
                  height: '100%', borderRadius: 4,
                  width: `${pct}%`,
                  background: pct > 80 ? '#ef4444' : pct > 60 ? '#f59e0b' : color,
                  transition: 'width 0.5s ease',
                }} />
              </div>
              <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>
                {pct.toFixed(0)}% of limit ({limit > 100 ? limit.toFixed(0) : limit})
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Positions Table ───────────────────────────────────────────────── */}
      <div style={{ ...cardStyle, padding: '16px 20px' }}>
        {/* Table Header & Filter Row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', margin: 0 }}>
              Positions
            </h3>
            <span style={{
              fontSize: 11, fontWeight: 600,
              background: 'rgba(124,58,237,0.15)', color: '#a78bfa',
              border: '1px solid rgba(124,58,237,0.25)',
              borderRadius: 10, padding: '2px 8px',
            }}>
              {filteredPositions.length} / {positions.length}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* Type Filter Buttons */}
            {['ALL', 'CE', 'PE'].map((t) => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                style={{
                  padding: '4px 12px', fontSize: 11, fontWeight: 600,
                  borderRadius: 6, cursor: 'pointer',
                  background: typeFilter === t
                    ? (t === 'CE' ? 'rgba(59,130,246,0.25)' : t === 'PE' ? 'rgba(239,68,68,0.25)' : 'rgba(124,58,237,0.25)')
                    : 'rgba(100,116,139,0.08)',
                  border: typeFilter === t
                    ? `1px solid ${t === 'CE' ? 'rgba(59,130,246,0.5)' : t === 'PE' ? 'rgba(239,68,68,0.5)' : 'rgba(124,58,237,0.5)'}`
                    : '1px solid rgba(100,116,139,0.15)',
                  color: typeFilter === t
                    ? (t === 'CE' ? '#60a5fa' : t === 'PE' ? '#f87171' : '#a78bfa')
                    : '#64748b',
                  transition: 'all 0.15s',
                }}
              >
                {t}
              </button>
            ))}
            {/* Search */}
            <div style={{ position: 'relative' }}>
              <Search style={{ width: 13, height: 13, color: '#475569', position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)' }} />
              <input
                type="text"
                placeholder="Search symbol or strategy…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  background: isDark ? 'rgba(15,20,30,0.7)' : 'rgba(241,245,249,0.9)', border: isDark ? '1px solid rgba(100,116,139,0.2)' : '1px solid rgba(0,0,0,0.1)',
                  borderRadius: 8, padding: '5px 10px 5px 28px',
                  fontSize: 12, color: isDark ? '#e2e8f0' : '#1e293b', outline: 'none', width: 220,
                }}
              />
            </div>
          </div>
        </div>

        {/* Table */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr>
                {['Symbol', 'Type', 'Qty', 'Avg Price', 'LTP', 'P&L', 'Strategy', 'Expiry'].map((col) => (
                  <th
                    key={col}
                    style={{
                      padding: '8px 12px',
                      textAlign: col === 'Qty' || col === 'Avg Price' || col === 'LTP' || col === 'P&L' ? 'right' : 'left',
                      fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                      letterSpacing: '0.07em', color: '#475569',
                      borderBottom: '1px solid rgba(100,116,139,0.12)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredPositions.map((pos, i) => {
                const atm = isATM(pos.symbol);
                const rowBg = atm
                  ? 'rgba(124,58,237,0.06)'
                  : i % 2 === 0 ? 'transparent' : (isDark ? 'rgba(15,20,30,0.3)' : 'rgba(0,0,0,0.02)');
                const ltpChange = pos.ltp - pos.avgPrice;
                return (
                  <tr
                    key={pos.id || i}
                    style={{
                      background: rowBg,
                      borderLeft: atm ? '2px solid rgba(124,58,237,0.5)' : '2px solid transparent',
                      transition: 'background 0.15s',
                    }}
                  >
                    {/* Symbol */}
                    <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontWeight: 600, color: isDark ? '#f1f5f9' : '#1e293b' }}>{pos.symbol}</span>
                        {atm && (
                          <span style={{
                            fontSize: 9, fontWeight: 700, padding: '1px 5px',
                            background: 'rgba(124,58,237,0.2)', color: '#a78bfa',
                            borderRadius: 4, letterSpacing: '0.05em',
                          }}>ATM</span>
                        )}
                      </div>
                    </td>
                    {/* Type Badge */}
                    <td style={{ padding: '9px 12px' }}>
                      <span style={{
                        display: 'inline-block', padding: '2px 8px',
                        fontSize: 11, fontWeight: 700, borderRadius: 5,
                        background: pos.type === 'CE' ? 'rgba(59,130,246,0.15)' : 'rgba(239,68,68,0.15)',
                        color: pos.type === 'CE' ? '#60a5fa' : '#f87171',
                        border: `1px solid ${pos.type === 'CE' ? 'rgba(59,130,246,0.3)' : 'rgba(239,68,68,0.3)'}`,
                      }}>
                        {pos.type}
                      </span>
                    </td>
                    {/* Qty */}
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                      <span style={{ fontFamily: 'monospace', fontWeight: 700, color: pos.qty < 0 ? '#ef4444' : '#22c55e' }}>
                        {pos.qty > 0 ? '+' : ''}{pos.qty}
                      </span>
                    </td>
                    {/* Avg Price */}
                    <td style={{ padding: '9px 12px', textAlign: 'right', fontFamily: 'monospace', color: '#94a3b8' }}>
                      {pos.avgPrice?.toFixed(2)}
                    </td>
                    {/* LTP */}
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                      <span style={{ fontFamily: 'monospace', color: ltpChange >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                        {pos.ltp?.toFixed(2)}
                      </span>
                    </td>
                    {/* P&L */}
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                      <span style={{
                        fontFamily: 'monospace', fontWeight: 700,
                        color: pos.pnl >= 0 ? '#22c55e' : '#ef4444',
                      }}>
                        {pos.pnl >= 0 ? '+' : ''}{pos.pnl?.toFixed(2)}
                      </span>
                    </td>
                    {/* Strategy */}
                    <td style={{ padding: '9px 12px', color: '#94a3b8', fontSize: 12, whiteSpace: 'nowrap' }}>
                      {pos.strategy || '—'}
                    </td>
                    {/* Expiry */}
                    <td style={{ padding: '9px 12px', color: '#64748b', fontSize: 12, whiteSpace: 'nowrap', fontFamily: 'monospace' }}>
                      {pos.expiry || '—'}
                    </td>
                  </tr>
                );
              })}
              {filteredPositions.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    style={{ padding: '32px', textAlign: 'center', color: '#475569', fontSize: 13 }}
                  >
                    No positions match the current filter.
                  </td>
                </tr>
              )}
            </tbody>
            {/* Footer: totals row */}
            {filteredPositions.length > 0 && (
              <tfoot>
                <tr style={{ borderTop: '1px solid rgba(100,116,139,0.2)' }}>
                  <td colSpan={5} style={{ padding: '8px 12px', fontSize: 11, color: '#475569', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    Total ({filteredPositions.length} legs)
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                    {(() => {
                      const total = filteredPositions.reduce((s, p) => s + (p.pnl || 0), 0);
                      return (
                        <span style={{
                          fontFamily: 'monospace', fontWeight: 800, fontSize: 14,
                          color: total >= 0 ? '#22c55e' : '#ef4444',
                        }}>
                          {total >= 0 ? '+' : ''}{total.toFixed(2)}
                        </span>
                      );
                    })()}
                  </td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>
    </div>
  );
}

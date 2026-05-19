import React, { useState, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, DollarSign, BarChart3, Receipt,
  ArrowUpRight, ArrowDownRight, Clock, Trophy, Target, Percent,
  ChevronDown, ChevronUp, Filter,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, PieChart, Pie, Legend,
} from 'recharts';
import Card from '../components/common/Card';
import MetricCard from '../components/common/MetricCard';
import DataTable from '../components/common/DataTable';
import StatusBadge from '../components/common/StatusBadge';
import {
  usePnLSummary, usePnLByStrategy, usePnLCharges, usePnLEquityCurve, usePnLTradeBook,
} from '../hooks/useApi';

// ── Fallback data ──────────────────────────────────────────────────
const fallbackSummary = {
  realized_pnl: 12450,
  unrealized_pnl: 3200,
  total_pnl: 15650,
  net_pnl: 15120,
  peak_pnl: 18200,
  max_drawdown_today: 3100,
  charges: { brokerage: 95, stt: 132, exchange_txn_fee: 38, gst: 68, sebi_fee: 8, stamp_duty: 38, total: 379 },
};

const fallbackStrategies = [
  { strategy_id: 'iron-condor', name: 'NIFTY Weekly Iron Condor', realized_pnl: 4520, unrealized_pnl: 1200, charges: 142, net_pnl: 5578, trades_today: 8, win_rate: 0.75 },
  { strategy_id: 'straddle-bnf', name: 'BANKNIFTY ATM Straddle', realized_pnl: 6800, unrealized_pnl: 800, charges: 165, net_pnl: 7435, trades_today: 4, win_rate: 0.80 },
  { strategy_id: 'momentum', name: 'NIFTY Momentum Scalper', realized_pnl: -1200, unrealized_pnl: 0, charges: 98, net_pnl: -1298, trades_today: 12, win_rate: 0.42 },
];

const fallbackCharges = {
  charges: { brokerage: 95, stt: 132, exchange_txn_fee: 38, gst: 68, sebi_fee: 8, stamp_duty: 38, total: 379 },
  by_segment: { equity: 57, fno_futures: 95, fno_options: 227 },
};

const CHARGE_COLORS = ['#7c3aed', '#3b82f6', '#06b6d4', '#f59e0b', '#ef4444', '#10b981'];
const SEGMENT_COLORS = ['#60a5fa', '#a78bfa', '#f97316'];

// ── Trade Book Columns ──────────────────────────────────────────────
const tradeColumns = [
  { key: 'trade_id', label: 'ID', render: (v) => <span className="font-mono text-blue-400 text-xs">{v || '-'}</span> },
  { key: 'symbol', label: 'Symbol', render: (v) => <span className="font-medium text-white">{v}</span> },
  { key: 'side', label: 'Side', render: (v) => <span className={`font-medium ${v === 'BUY' ? 'text-profit' : 'text-loss'}`}>{v}</span> },
  { key: 'quantity', label: 'Qty', align: 'right', render: (v) => <span className="font-mono">{v}</span> },
  { key: 'price', label: 'Price', align: 'right', render: (v) => <span className="font-mono">{v?.toFixed(2)}</span> },
  {
    key: 'pnl', label: 'P&L', align: 'right',
    render: (v) => <span className={`font-mono font-medium ${v >= 0 ? 'text-profit' : 'text-loss'}`}>{v >= 0 ? '+' : ''}{v?.toFixed(2)}</span>,
  },
  { key: 'charges', label: 'Charges', align: 'right', render: (v) => <span className="font-mono text-slate-400">{v?.toFixed(2)}</span> },
  {
    key: 'net_pnl', label: 'Net', align: 'right',
    render: (v) => <span className={`font-mono font-semibold ${v >= 0 ? 'text-profit' : 'text-loss'}`}>{v >= 0 ? '+' : ''}{v?.toFixed(2)}</span>,
  },
];

// ── Helpers ─────────────────────────────────────────────────────────
function formatINR(val) {
  if (val == null) return '--';
  const abs = Math.abs(val);
  if (abs >= 10000000) return `${(val / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `${(val / 100000).toFixed(2)} L`;
  return val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

// ── Custom Recharts Tooltip ─────────────────────────────────────────
function EquityTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass-card !p-3 !rounded-xl text-xs">
      <div className="text-slate-400 mb-1">{label}</div>
      <div className="font-mono font-bold text-white">
        Rs {payload[0]?.value?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main Component
// ════════════════════════════════════════════════════════════════════
export default function PnLAnalytics() {
  const { data: summaryData } = usePnLSummary();
  const { data: strategyData } = usePnLByStrategy();
  const { data: chargesData } = usePnLCharges();
  const { data: equityData } = usePnLEquityCurve();
  const { data: tradeBookData } = usePnLTradeBook();

  const [activeTab, setActiveTab] = useState('overview');
  const [tradeFilter, setTradeFilter] = useState('all'); // all | profit | loss

  // ── Parse API responses ──
  const summary = summaryData || fallbackSummary;
  const strategies = strategyData?.strategies || fallbackStrategies;
  const charges = chargesData || fallbackCharges;
  const trades = tradeBookData?.trades || [];

  // ── Equity curve data ──
  const equityCurve = useMemo(() => {
    if (!equityData?.data_points?.length) return null;
    return equityData.data_points.map((p) => {
      const ts = new Date(p.timestamp);
      return {
        time: `${String(ts.getHours()).padStart(2, '0')}:${String(ts.getMinutes()).padStart(2, '0')}`,
        equity: p.equity,
      };
    });
  }, [equityData]);

  // ── Charges breakdown for pie ──
  const chargesBreakdown = useMemo(() => {
    const c = charges?.charges || {};
    return [
      { name: 'Brokerage', value: c.brokerage || 0 },
      { name: 'STT', value: c.stt || 0 },
      { name: 'Exchange', value: c.exchange_txn_fee || 0 },
      { name: 'GST', value: c.gst || 0 },
      { name: 'SEBI', value: c.sebi_fee || 0 },
      { name: 'Stamp Duty', value: c.stamp_duty || 0 },
    ].filter((e) => e.value > 0);
  }, [charges]);

  const segmentBreakdown = useMemo(() => {
    const s = charges?.by_segment || {};
    return [
      { name: 'Equity', value: s.equity || 0 },
      { name: 'FnO Futures', value: s.fno_futures || 0 },
      { name: 'FnO Options', value: s.fno_options || 0 },
    ].filter((e) => e.value > 0);
  }, [charges]);

  // ── Strategy bar chart data ──
  const strategyBarData = useMemo(() => {
    return strategies.map((s) => ({
      name: s.name?.replace(/^(NIFTY|BANKNIFTY)\s*/i, '').substring(0, 18),
      net_pnl: s.net_pnl || 0,
      win_rate: Math.round((s.win_rate || 0) * 100),
    }));
  }, [strategies]);

  // ── Filtered trades ──
  const filteredTrades = useMemo(() => {
    if (tradeFilter === 'profit') return trades.filter((t) => t.net_pnl >= 0);
    if (tradeFilter === 'loss') return trades.filter((t) => t.net_pnl < 0);
    return trades;
  }, [trades, tradeFilter]);

  // ── Derived stats ──
  const totalCharges = charges?.charges?.total || 0;
  const netPnl = summary.net_pnl || 0;
  const totalPnl = summary.total_pnl || 0;
  const peakPnl = summary.peak_pnl || 0;
  const maxDD = summary.max_drawdown_today || 0;
  const winningStrategies = strategies.filter((s) => (s.net_pnl || 0) > 0).length;
  const initialCapital = equityData?.initial_capital || 1500000;
  const currentEquity = equityData?.current_equity || initialCapital + netPnl;
  const returnPct = initialCapital ? ((currentEquity - initialCapital) / initialCapital * 100).toFixed(2) : 0;

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'strategies', label: 'By Strategy' },
    { id: 'charges', label: 'Charges' },
    { id: 'trades', label: 'Trade Book' },
  ];

  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">P&L Analytics</h1>
          <p className="text-xs text-slate-400 mt-0.5">Intraday performance, charges, and strategy attribution</p>
        </div>
        <div className="flex items-center gap-2">
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold ${
            netPnl >= 0 ? 'bg-profit/10 text-profit' : 'bg-loss/10 text-loss'
          }`}>
            {netPnl >= 0 ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
            {netPnl >= 0 ? '+' : ''}Rs {formatINR(netPnl)}
          </div>
          <div className="text-xs text-slate-500 font-mono">
            {returnPct >= 0 ? '+' : ''}{returnPct}%
          </div>
        </div>
      </div>

      {/* ── Tab Navigation ──────────────────────────────────────── */}
      <div className="flex gap-1 p-1 glass-card !rounded-xl !p-1.5">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === tab.id
                ? 'bg-accent/15 text-accent shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ════════════════════════════════════════════════════════ */}
      {/* OVERVIEW TAB                                            */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeTab === 'overview' && (
        <>
          {/* ── Top Metric Cards ── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger-1 animate-fade-in">
            <MetricCard label="Net P&L" value={netPnl} prefix="Rs " icon={DollarSign} />
            <MetricCard label="Realized" value={summary.realized_pnl || 0} prefix="Rs " icon={TrendingUp} />
            <MetricCard label="Unrealized" value={summary.unrealized_pnl || 0} prefix="Rs " icon={Target} />
            <MetricCard label="Total Charges" value={-(totalCharges)} prefix="Rs " icon={Receipt} />
          </div>

          {/* ── Secondary Stats ── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 stagger-2 animate-fade-in">
            <div className="glass-card !p-3 !rounded-xl">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Peak P&L</span>
                <Trophy className="w-4 h-4 text-yellow-500" />
              </div>
              <div className="text-xl font-bold font-mono text-profit">
                +Rs {formatINR(peakPnl)}
              </div>
            </div>
            <div className="glass-card !p-3 !rounded-xl">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Max Drawdown</span>
                <TrendingDown className="w-4 h-4 text-loss" />
              </div>
              <div className="text-xl font-bold font-mono text-loss">
                -Rs {formatINR(maxDD)}
              </div>
            </div>
            <div className="glass-card !p-3 !rounded-xl">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Return %</span>
                <Percent className="w-4 h-4 text-accent" />
              </div>
              <div className={`text-xl font-bold font-mono ${returnPct >= 0 ? 'text-profit' : 'text-loss'}`}>
                {returnPct >= 0 ? '+' : ''}{returnPct}%
              </div>
            </div>
            <div className="glass-card !p-3 !rounded-xl">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Winning Strategies</span>
                <BarChart3 className="w-4 h-4 text-blue-400" />
              </div>
              <div className="text-xl font-bold font-mono text-white">
                {winningStrategies}<span className="text-slate-500 text-sm">/{strategies.length}</span>
              </div>
            </div>
          </div>

          {/* ── Intraday Equity Curve ── */}
          <Card title="Intraday Equity Curve" actions={
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 font-mono">
                Capital: Rs {formatINR(initialCapital)}
              </span>
              <span className={`text-xs font-mono font-bold ${currentEquity >= initialCapital ? 'text-profit' : 'text-loss'}`}>
                Now: Rs {formatINR(currentEquity)}
              </span>
            </div>
          }>
            {equityCurve ? (
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={equityCurve} margin={{ top: 5, right: 5, bottom: 5, left: 10 }}>
                  <defs>
                    <linearGradient id="pnlEquityGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" />
                  <XAxis
                    dataKey="time"
                    tick={{ fill: '#64748b', fontSize: 10 }}
                    stroke="#1e2433"
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tick={{ fill: '#64748b', fontSize: 10 }}
                    tickFormatter={(v) => `${(v / 100000).toFixed(1)}L`}
                    stroke="#1e2433"
                    domain={['dataMin - 5000', 'dataMax + 5000']}
                  />
                  <Tooltip content={<EquityTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke="#7c3aed"
                    strokeWidth={2}
                    fill="url(#pnlEquityGradient)"
                    dot={false}
                    activeDot={{ r: 4, fill: '#7c3aed', stroke: '#fff', strokeWidth: 2 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-[300px] text-slate-500 text-sm">
                <Clock className="w-5 h-5 mr-2" /> Waiting for equity data...
              </div>
            )}
          </Card>

          {/* ── Quick Strategy Snapshot ── */}
          <Card title="Strategy P&L Snapshot">
            <div className="space-y-3">
              {strategies.map((s, i) => {
                const pnl = s.net_pnl || 0;
                const maxPnl = Math.max(...strategies.map((st) => Math.abs(st.net_pnl || 0)), 1);
                const barPct = Math.min(Math.abs(pnl) / maxPnl * 100, 100);
                return (
                  <div key={s.strategy_id || i} className="group">
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${pnl >= 0 ? 'bg-profit' : 'bg-loss'}`} />
                        <span className="text-sm text-slate-300 font-medium">{s.name}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-[10px] text-slate-500 font-mono">{s.trades_today} trades</span>
                        <span className="text-[10px] text-slate-500 font-mono">WR: {Math.round((s.win_rate || 0) * 100)}%</span>
                        <span className={`font-mono text-sm font-bold ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                          {pnl >= 0 ? '+' : ''}Rs {formatINR(pnl)}
                        </span>
                      </div>
                    </div>
                    <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden gauge-track">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${pnl >= 0 ? 'bg-profit' : 'bg-loss'}`}
                        style={{ width: `${barPct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* BY STRATEGY TAB                                         */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeTab === 'strategies' && (
        <>
          {/* ── Strategy Bar Chart ── */}
          <Card title="Net P&L by Strategy">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={strategyBarData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" />
                <XAxis
                  dataKey="name"
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  stroke="#1e2433"
                />
                <YAxis
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                  stroke="#1e2433"
                />
                <Tooltip
                  contentStyle={{ backgroundColor: '#131720', border: '1px solid #1e2433', borderRadius: '12px', fontSize: 12 }}
                  labelStyle={{ color: '#94a3b8' }}
                  formatter={(value) => [`Rs ${value?.toLocaleString('en-IN')}`, 'Net P&L']}
                />
                <Bar dataKey="net_pnl" radius={[6, 6, 0, 0]} maxBarSize={48}>
                  {strategyBarData.map((entry, i) => (
                    <Cell key={i} fill={entry.net_pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>

          {/* ── Strategy Detail Cards ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {strategies.map((s, i) => {
              const pnl = s.net_pnl || 0;
              const winRate = Math.round((s.win_rate || 0) * 100);
              return (
                <div key={s.strategy_id || i} className="glass-card glass-card-interactive !p-5 !rounded-2xl">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-white text-xs font-bold ${
                        pnl >= 0 ? 'bg-gradient-to-br from-profit to-emerald-600' : 'bg-gradient-to-br from-loss to-red-600'
                      }`}>
                        {s.name?.charAt(0) || 'S'}
                      </div>
                      <div>
                        <div className="text-sm font-semibold text-white">{s.name}</div>
                        <div className="text-[10px] text-slate-500">{s.strategy_id}</div>
                      </div>
                    </div>
                    <div className={`text-lg font-bold font-mono ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {pnl >= 0 ? '+' : ''}{formatINR(pnl)}
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 mb-3">
                    <div>
                      <div className="text-[10px] text-slate-500 uppercase">Realized</div>
                      <div className={`text-sm font-mono ${(s.realized_pnl || 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {(s.realized_pnl || 0) >= 0 ? '+' : ''}{formatINR(s.realized_pnl)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-500 uppercase">Unrealized</div>
                      <div className={`text-sm font-mono ${(s.unrealized_pnl || 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                        {(s.unrealized_pnl || 0) >= 0 ? '+' : ''}{formatINR(s.unrealized_pnl)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-500 uppercase">Charges</div>
                      <div className="text-sm font-mono text-slate-400">-{formatINR(s.charges)}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-500 uppercase">Trades</div>
                      <div className="text-sm font-mono text-white">{s.trades_today}</div>
                    </div>
                  </div>

                  {/* Win Rate Bar */}
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="text-slate-500 uppercase">Win Rate</span>
                      <span className={`font-mono font-bold ${winRate >= 60 ? 'text-profit' : winRate >= 45 ? 'text-yellow-400' : 'text-loss'}`}>
                        {winRate}%
                      </span>
                    </div>
                    <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden gauge-track">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          winRate >= 60 ? 'bg-profit' : winRate >= 45 ? 'bg-yellow-500' : 'bg-loss'
                        }`}
                        style={{ width: `${winRate}%` }}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* CHARGES TAB                                             */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeTab === 'charges' && (
        <>
          {/* ── Total Charges Banner ── */}
          <div className="glass-card !p-5 !rounded-2xl">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs text-slate-400 uppercase tracking-wider font-medium mb-1">Total Charges Today</div>
                <div className="text-3xl font-bold font-mono text-loss">
                  Rs {formatINR(totalCharges)}
                </div>
                <div className="text-xs text-slate-500 mt-1">
                  {totalPnl !== 0 ? `${((totalCharges / Math.abs(totalPnl)) * 100).toFixed(1)}% of gross P&L` : ''}
                </div>
              </div>
              <Receipt className="w-10 h-10 text-slate-600" />
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* ── Charges Breakdown Pie ── */}
            <Card title="Charges Breakdown">
              {chargesBreakdown.length > 0 ? (
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie
                      data={chargesBreakdown}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={100}
                      paddingAngle={3}
                      dataKey="value"
                    >
                      {chargesBreakdown.map((_, i) => (
                        <Cell key={i} fill={CHARGE_COLORS[i % CHARGE_COLORS.length]} fillOpacity={0.85} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{ backgroundColor: '#131720', border: '1px solid #1e2433', borderRadius: '12px', fontSize: 12 }}
                      formatter={(value) => [`Rs ${value?.toFixed(2)}`, '']}
                    />
                    <Legend
                      verticalAlign="bottom"
                      iconType="circle"
                      iconSize={8}
                      wrapperStyle={{ fontSize: '11px', color: '#94a3b8' }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-[280px] text-slate-500 text-sm">No charge data</div>
              )}
            </Card>

            {/* ── Charges Detail List ── */}
            <Card title="Itemized Charges">
              <div className="space-y-0">
                {chargesBreakdown.map((item, i) => {
                  const pct = totalCharges ? ((item.value / totalCharges) * 100).toFixed(1) : 0;
                  return (
                    <div key={item.name} className="flex items-center justify-between py-3 border-b border-terminal-border last:border-0">
                      <div className="flex items-center gap-3">
                        <div className="w-3 h-3 rounded-full" style={{ backgroundColor: CHARGE_COLORS[i % CHARGE_COLORS.length] }} />
                        <span className="text-sm text-slate-300">{item.name}</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <span className="text-xs text-slate-500 font-mono">{pct}%</span>
                        <span className="font-mono text-sm text-white font-medium">Rs {item.value.toFixed(2)}</span>
                      </div>
                    </div>
                  );
                })}
                <div className="flex items-center justify-between pt-3 mt-1">
                  <span className="text-sm font-semibold text-slate-300">Total</span>
                  <span className="font-mono text-sm font-bold text-loss">Rs {totalCharges.toFixed(2)}</span>
                </div>
              </div>
            </Card>
          </div>

          {/* ── Segment Breakdown ── */}
          <Card title="Charges by Segment">
            <div className="grid grid-cols-3 gap-4">
              {segmentBreakdown.map((seg, i) => {
                const pct = totalCharges ? ((seg.value / totalCharges) * 100).toFixed(0) : 0;
                return (
                  <div key={seg.name} className="glass-card !p-4 !rounded-xl text-center">
                    <div className="w-10 h-10 rounded-xl mx-auto mb-2 flex items-center justify-center" style={{ backgroundColor: `${SEGMENT_COLORS[i]}20` }}>
                      <DollarSign className="w-5 h-5" style={{ color: SEGMENT_COLORS[i] }} />
                    </div>
                    <div className="text-xs text-slate-400 mb-1">{seg.name}</div>
                    <div className="text-lg font-bold font-mono text-white">Rs {seg.value.toFixed(0)}</div>
                    <div className="text-[10px] text-slate-500 mt-0.5">{pct}% of total</div>
                  </div>
                );
              })}
            </div>
          </Card>
        </>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* TRADE BOOK TAB                                          */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeTab === 'trades' && (
        <>
          {/* ── Trade Summary Bar ── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="glass-card !p-3 !rounded-xl">
              <div className="text-xs text-slate-400 uppercase tracking-wider mb-1">Total Trades</div>
              <div className="text-xl font-bold font-mono text-white">{trades.length}</div>
            </div>
            <div className="glass-card !p-3 !rounded-xl">
              <div className="text-xs text-slate-400 uppercase tracking-wider mb-1">Gross P&L</div>
              <div className={`text-xl font-bold font-mono ${(tradeBookData?.total_pnl || 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                {(tradeBookData?.total_pnl || 0) >= 0 ? '+' : ''}Rs {formatINR(tradeBookData?.total_pnl || 0)}
              </div>
            </div>
            <div className="glass-card !p-3 !rounded-xl">
              <div className="text-xs text-slate-400 uppercase tracking-wider mb-1">Total Charges</div>
              <div className="text-xl font-bold font-mono text-loss">
                Rs {formatINR(tradeBookData?.total_charges || 0)}
              </div>
            </div>
            <div className="glass-card !p-3 !rounded-xl">
              <div className="text-xs text-slate-400 uppercase tracking-wider mb-1">Profitable</div>
              <div className="text-xl font-bold font-mono text-profit">
                {trades.filter((t) => (t.net_pnl || 0) >= 0).length}
                <span className="text-slate-500 text-sm">/{trades.length}</span>
              </div>
            </div>
          </div>

          {/* ── Filter Tabs ── */}
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-slate-500" />
            {['all', 'profit', 'loss'].map((f) => (
              <button
                key={f}
                onClick={() => setTradeFilter(f)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                  tradeFilter === f
                    ? f === 'profit' ? 'bg-profit/15 text-profit' : f === 'loss' ? 'bg-loss/15 text-loss' : 'bg-accent/15 text-accent'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
                }`}
              >
                {f === 'all' ? 'All Trades' : f === 'profit' ? 'Profitable' : 'Loss-making'}
              </button>
            ))}
            <span className="text-xs text-slate-500 ml-auto">{filteredTrades.length} trades</span>
          </div>

          {/* ── Trade Table ── */}
          <Card title={`Trade Book (${filteredTrades.length})`}>
            <DataTable columns={tradeColumns} data={filteredTrades} />
          </Card>
        </>
      )}
    </div>
  );
}

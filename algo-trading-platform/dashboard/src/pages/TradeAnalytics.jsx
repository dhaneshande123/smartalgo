import { useState, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, Trophy, Target, Percent, BarChart3,
  ArrowUpRight, ArrowDownRight, Activity, Clock, Zap, Shield,
  ChevronDown, Filter, Download, RefreshCw, ListOrdered,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, ComposedChart, Line,
} from 'recharts';
import Card from '../components/common/Card';
import MetricCard from '../components/common/MetricCard';
import DataTable from '../components/common/DataTable';
import LoadingState from '../components/common/LoadingState';
import EmptyState from '../components/common/EmptyState';
import { useTradeAnalytics, useTradeLog } from '../hooks/useApi';

// ── Formatting helpers ──────────────────────────────────────────
const fmt = (v) =>
  typeof v === 'number'
    ? v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : v ?? '-';

const fmtINR = (v) => (typeof v === 'number' ? `₹${fmt(Math.abs(v))}` : '-');

const pnlColor = (v) =>
  typeof v === 'number' ? (v > 0 ? 'text-profit' : v < 0 ? 'text-loss' : 'text-slate-400') : '';

const pnlBg = (v) =>
  typeof v === 'number' ? (v > 0 ? 'bg-profit/10' : v < 0 ? 'bg-loss/10' : 'bg-slate-700/30') : '';

// ── KPI card row ────────────────────────────────────────────────
function KPIRow({ summary }) {
  if (!summary) return null;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <MetricCard
        label="Net P&L"
        value={summary.total_pnl}
        prefix="₹"
        icon={summary.total_pnl >= 0 ? TrendingUp : TrendingDown}
      />
      <MetricCard
        label="Gross P&L"
        value={summary.gross_pnl ?? summary.total_pnl}
        prefix="₹"
        icon={summary.gross_pnl >= 0 ? ArrowUpRight : ArrowDownRight}
      />
      <MetricCard
        label={summary.gross_win_rate != null ? 'Net Win Rate' : 'Win Rate'}
        value={summary.win_rate}
        suffix="%"
        icon={Target}
        colorClass={summary.win_rate >= 50 ? 'text-profit' : 'text-loss'}
      />
      <MetricCard
        label="Profit Factor"
        value={summary.profit_factor === 'inf' ? '∞' : summary.profit_factor}
        icon={Zap}
        colorClass={
          summary.profit_factor === 'inf' || summary.profit_factor > 1
            ? 'text-profit'
            : 'text-loss'
        }
      />
      <MetricCard
        label="Sharpe Ratio"
        value={summary.sharpe_ratio}
        icon={Trophy}
        colorClass={summary.sharpe_ratio >= 1 ? 'text-profit' : summary.sharpe_ratio >= 0 ? 'text-yellow-400' : 'text-loss'}
      />
      <MetricCard
        label="Max Drawdown"
        value={summary.max_drawdown}
        prefix="₹"
        icon={Shield}
        colorClass="text-loss"
      />
    </div>
  );
}

// ── Secondary stats row ─────────────────────────────────────────
function StatsBar({ summary }) {
  if (!summary) return null;
  const items = [
    { label: 'Trades', value: summary.total_trades, icon: ListOrdered },
    { label: 'Running', value: summary.running_strategies, icon: Activity },
    { label: 'Wins', value: summary.win_count, color: 'text-profit' },
    { label: 'Losses', value: summary.loss_count, color: 'text-loss' },
    ...(summary.gross_win_rate != null ? [{ label: 'Gross WR', value: `${summary.gross_win_rate}%`, color: summary.gross_win_rate >= 50 ? 'text-profit' : 'text-loss' }] : []),
    { label: 'Avg Win', value: fmtINR(summary.avg_win), color: 'text-profit' },
    { label: 'Avg Loss', value: fmtINR(summary.avg_loss), color: 'text-loss' },
    { label: 'Best', value: fmtINR(summary.best_trade), color: 'text-profit' },
    { label: 'Worst', value: fmtINR(summary.worst_trade), color: 'text-loss' },
    { label: 'Expectancy', value: fmtINR(summary.expectancy), color: pnlColor(summary.expectancy) },
  ];
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2 px-1">
      {items.map((it) => (
        <div key={it.label} className="flex items-center gap-1.5 text-xs">
          {it.icon && <it.icon className="w-3.5 h-3.5 text-slate-500" />}
          <span className="text-slate-500">{it.label}:</span>
          <span className={`font-mono font-semibold ${it.color || 'text-white'}`}>
            {typeof it.value === 'number' ? it.value : it.value}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Equity curve chart ──────────────────────────────────────────
function EquityCurve({ data }) {
  if (!data?.length) {
    return <EmptyState message="No completed trades yet — equity curve will appear here" size="compact" />;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
        <XAxis
          dataKey="timestamp"
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={(v) => v ? v.substring(11, 16) : ''}
        />
        <YAxis
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={(v) => `₹${v}`}
        />
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: '#94a3b8' }}
          formatter={(value, name) => [`₹${fmt(value)}`, name === 'equity' ? 'Cumulative P&L' : 'Trade P&L']}
          labelFormatter={(v) => v || ''}
        />
        <Area type="monotone" dataKey="equity" stroke="#22d3ee" fill="url(#equityFill)" strokeWidth={2} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Daily P&L bar chart ─────────────────────────────────────────
function DailyPnLChart({ data }) {
  if (!data?.length) {
    return <EmptyState message="No daily P&L data yet" size="compact" />;
  }
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
        <XAxis
          dataKey="date"
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={(v) => v?.substring(5) || ''}
        />
        <YAxis
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={(v) => `₹${v}`}
        />
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
          formatter={(value) => [`₹${fmt(value)}`, 'Day P&L']}
        />
        <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.pnl >= 0 ? '#22c55e' : '#ef4444'} fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Strategy breakdown table ────────────────────────────────────
const breakdownColumns = [
  {
    key: 'name',
    label: 'Strategy',
    render: (v, row) => (
      <div>
        <span className="text-white font-medium text-sm">{v || row.strategy_class || '-'}</span>
        {row.strategy_class && row.strategy_class !== v && (
          <span className="ml-2 text-xs text-slate-500">{row.strategy_class}</span>
        )}
      </div>
    ),
  },
  { key: 'total_trades', label: 'Trades', align: 'right', render: (v) => <span className="font-mono">{v}</span> },
  {
    key: 'wins',
    label: 'W / L',
    align: 'right',
    render: (v, row) => (
      <span className="font-mono">
        <span className="text-profit">{row.wins}</span>
        <span className="text-slate-500"> / </span>
        <span className="text-loss">{row.losses}</span>
      </span>
    ),
  },
  {
    key: 'total_pnl',
    label: 'Total P&L',
    align: 'right',
    render: (v) => (
      <span className={`font-mono font-semibold ${pnlColor(v)}`}>
        {v >= 0 ? '+' : '-'}{fmtINR(v)}
      </span>
    ),
  },
  {
    key: 'best_trade',
    label: 'Best',
    align: 'right',
    render: (v) => <span className="font-mono text-profit text-xs">+{fmtINR(v)}</span>,
  },
  {
    key: 'worst_trade',
    label: 'Worst',
    align: 'right',
    render: (v) => <span className="font-mono text-loss text-xs">-{fmtINR(v)}</span>,
  },
];

// ── Trade log table ─────────────────────────────────────────────
const tradeLogColumns = [
  {
    key: 'timestamp',
    label: 'Time',
    render: (v) => (
      <span className="font-mono text-xs text-slate-400">
        {v ? v.substring(0, 19).replace('T', ' ') : '-'}
      </span>
    ),
  },
  {
    key: 'action',
    label: 'Action',
    render: (v) => (
      <span
        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold ${
          v === 'ENTRY' ? 'bg-blue-500/15 text-blue-400' : 'bg-orange-500/15 text-orange-400'
        }`}
      >
        {v}
      </span>
    ),
  },
  {
    key: 'side',
    label: 'Side',
    render: (v) => (
      <span className={`font-semibold text-xs ${v === 'BUY' ? 'text-profit' : 'text-loss'}`}>
        {v}
      </span>
    ),
  },
  {
    key: 'symbol',
    label: 'Symbol',
    render: (v) => <span className="font-medium text-white text-sm">{v || '-'}</span>,
  },
  {
    key: 'qty',
    label: 'Qty',
    align: 'right',
    render: (v) => <span className="font-mono text-sm">{v}</span>,
  },
  {
    key: 'price',
    label: 'Price',
    align: 'right',
    render: (v) => <span className="font-mono text-sm">{fmt(v)}</span>,
  },
  {
    key: 'strategy_id',
    label: 'Strategy',
    render: (v) => (
      <span className="font-mono text-xs text-slate-500 truncate max-w-[120px] inline-block">
        {v || '-'}
      </span>
    ),
  },
  {
    key: 'reason',
    label: 'Reason',
    render: (v) =>
      v ? (
        <span className="text-xs text-slate-400 truncate max-w-[150px] inline-block">{v}</span>
      ) : (
        <span className="text-slate-600">-</span>
      ),
  },
];

// ── Tab selector ────────────────────────────────────────────────
function TabButton({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium rounded-lg transition-all ${
        active
          ? 'bg-accent/15 text-accent border border-accent/30'
          : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
      }`}
    >
      {children}
    </button>
  );
}

// ── Main page ───────────────────────────────────────────────────
export default function TradeAnalytics() {
  const { data, isLoading, error } = useTradeAnalytics();
  const { data: tradeLogData } = useTradeLog();
  const [activeTab, setActiveTab] = useState('overview');

  const analytics = data || {};
  const summary = analytics.summary || {};
  const strategyBreakdown = analytics.strategy_breakdown || [];
  const equityCurve = analytics.equity_curve || [];
  const dailyPnl = analytics.daily_pnl || [];
  const recentTrades = analytics.recent_trades || [];
  const trades = tradeLogData?.trades || recentTrades;

  if (isLoading) return <LoadingState message="Loading trade analytics..." />;
  if (error)
    return (
      <div className="text-center py-16 text-red-400">
        Failed to load analytics. Backend may be down.
      </div>
    );

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Trade Analytics</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Performance metrics from {summary.total_trades || 0} completed trades
            {summary.running_strategies > 0 && (
              <span className="text-accent ml-1">
                + {summary.running_strategies} running
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TabButton active={activeTab === 'overview'} onClick={() => setActiveTab('overview')}>
            Overview
          </TabButton>
          <TabButton active={activeTab === 'trades'} onClick={() => setActiveTab('trades')}>
            Trade Log
          </TabButton>
          <TabButton active={activeTab === 'strategies'} onClick={() => setActiveTab('strategies')}>
            By Strategy
          </TabButton>
        </div>
      </div>

      {/* KPI Cards */}
      <KPIRow summary={summary} />

      {/* Secondary stats */}
      <StatsBar summary={summary} />

      {/* Tab content */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Equity Curve */}
          <Card title="Equity Curve">
            <EquityCurve data={equityCurve} />
          </Card>

          {/* Daily P&L */}
          <Card title="Daily P&L">
            <DailyPnLChart data={dailyPnl} />
          </Card>

          {/* Win / Loss distribution */}
          <Card title="Trade Distribution" className="lg:col-span-2">
            <div className="flex items-center gap-6">
              {/* Visual bar */}
              <div className="flex-1">
                <div className="flex h-8 rounded-lg overflow-hidden">
                  {summary.win_count > 0 && (
                    <div
                      className="bg-profit/70 flex items-center justify-center text-xs font-bold text-white"
                      style={{ width: `${summary.win_rate}%` }}
                    >
                      {summary.win_count}W
                    </div>
                  )}
                  {summary.breakeven_count > 0 && (
                    <div
                      className="bg-slate-600/70 flex items-center justify-center text-xs font-bold text-white"
                      style={{
                        width: `${(summary.breakeven_count / Math.max(summary.total_trades, 1)) * 100}%`,
                      }}
                    >
                      {summary.breakeven_count}BE
                    </div>
                  )}
                  {summary.loss_count > 0 && (
                    <div
                      className="bg-loss/70 flex items-center justify-center text-xs font-bold text-white"
                      style={{
                        width: `${(summary.loss_count / Math.max(summary.total_trades, 1)) * 100}%`,
                      }}
                    >
                      {summary.loss_count}L
                    </div>
                  )}
                  {summary.total_trades === 0 && (
                    <div className="w-full bg-slate-700/30 flex items-center justify-center text-xs text-slate-500">
                      No trades yet
                    </div>
                  )}
                </div>
                <div className="flex justify-between mt-1.5 text-[10px] text-slate-500">
                  <span>Win Rate: {summary.win_rate || 0}%</span>
                  <span>
                    Profit Factor:{' '}
                    {summary.profit_factor === 'inf' ? '∞' : summary.profit_factor || 0}
                  </span>
                </div>
              </div>

              {/* Legend cards */}
              <div className="flex gap-3">
                <div className="glass-card !p-3 !rounded-xl text-center min-w-[90px]">
                  <div className="text-xs text-slate-500 mb-0.5">Avg Win</div>
                  <div className="font-mono text-sm font-bold text-profit">
                    +{fmtINR(summary.avg_win)}
                  </div>
                </div>
                <div className="glass-card !p-3 !rounded-xl text-center min-w-[90px]">
                  <div className="text-xs text-slate-500 mb-0.5">Avg Loss</div>
                  <div className="font-mono text-sm font-bold text-loss">
                    -{fmtINR(summary.avg_loss)}
                  </div>
                </div>
              </div>
            </div>
          </Card>

          {/* Charges Breakdown */}
          {summary.total_charges > 0 && (
            <Card title="Charges & Slippage" className="lg:col-span-2">
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
                {[
                  { label: 'Brokerage', key: 'brokerage' },
                  { label: 'STT', key: 'stt' },
                  { label: 'Exchange Fee', key: 'exchange_txn_fee' },
                  { label: 'GST', key: 'gst' },
                  { label: 'SEBI', key: 'sebi_charges' },
                  { label: 'Stamp Duty', key: 'stamp_duty' },
                  { label: 'Slippage', key: 'slippage_cost' },
                ].map(({ label, key }) => {
                  const val = summary.charges_breakdown?.[key] ?? 0;
                  return (
                    <div key={key} className="glass-card !p-3 !rounded-xl text-center">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">{label}</div>
                      <div className="font-mono text-sm font-semibold text-orange-400">{fmtINR(val)}</div>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center justify-between mt-3 pt-3 border-t border-terminal-border">
                <div className="text-xs text-slate-500">
                  Gross P&L: <span className={`font-mono font-semibold ${pnlColor(summary.gross_pnl)}`}>
                    {summary.gross_pnl >= 0 ? '+' : '-'}{fmtINR(summary.gross_pnl)}
                  </span>
                </div>
                <div className="text-xs text-slate-500">
                  Total Charges: <span className="font-mono font-semibold text-orange-400">-{fmtINR(summary.total_charges)}</span>
                </div>
                <div className="text-xs text-slate-500">
                  Net P&L: <span className={`font-mono font-bold ${pnlColor(summary.total_pnl)}`}>
                    {summary.total_pnl >= 0 ? '+' : '-'}{fmtINR(summary.total_pnl)}
                  </span>
                </div>
              </div>
            </Card>
          )}
        </div>
      )}

      {activeTab === 'trades' && (
        <Card
          title={`Trade Log (${trades.length} entries)`}
          actions={
            <span className="text-[10px] text-slate-500">
              Persisted in SQLite — survives restarts
            </span>
          }
        >
          <DataTable
            columns={tradeLogColumns}
            data={trades}
            emptyMessage="No trades recorded yet. Deploy a strategy to see entry/exit events here."
          />
        </Card>
      )}

      {activeTab === 'strategies' && (
        <Card
          title={`Strategy Breakdown (${strategyBreakdown.length})`}
          actions={
            <span className="text-[10px] text-slate-500">
              Aggregated from all deployed strategies
            </span>
          }
        >
          <DataTable
            columns={breakdownColumns}
            data={strategyBreakdown}
            emptyMessage="No strategy data yet. Deploy and run strategies to see performance here."
          />
        </Card>
      )}
    </div>
  );
}

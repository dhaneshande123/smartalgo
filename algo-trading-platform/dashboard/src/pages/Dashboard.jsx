import React, { useState, useEffect, useRef } from 'react';
import { TrendingUp, TrendingDown, DollarSign, Activity, Target, BarChart3, Wifi, Radio } from 'lucide-react';
import Card from '../components/common/Card';
import MetricCard from '../components/common/MetricCard';
import StatusBadge from '../components/common/StatusBadge';
import DataTable from '../components/common/DataTable';
import EquityCurve from '../components/charts/EquityCurve';
import GreeksGauge from '../components/charts/GreeksGauge';
import { useStrategies, useGreeks, useRiskMetrics } from '../hooks/useApi';
import { useMarketDataStream, usePortfolioStream, useOrderStream, useAlertStream } from '../hooks/useWebSocket';

const fallbackRisk = { drawdown: -2.3, var95: 85000, marginUsed: 62, maxLoss: -150000 };

const strategyColumns = [
  { key: 'name', label: 'Strategy' },
  { key: 'status', label: 'Status', render: (v) => <StatusBadge status={v} /> },
  {
    key: 'pnl', label: 'P&L', align: 'right',
    render: (v) => (
      <span className={`font-mono ${v >= 0 ? 'text-profit' : 'text-loss'}`}>
        {v >= 0 ? '+' : ''}{v?.toLocaleString('en-IN')}
      </span>
    ),
  },
  { key: 'positions', label: 'Positions', align: 'right' },
];

const orderColumns = [
  { key: 'order_id', label: 'ID', render: (v) => <span className="font-mono text-blue-400 text-xs">{v}</span> },
  { key: 'symbol', label: 'Symbol', render: (v) => <span className="font-medium text-white">{v}</span> },
  { key: 'side', label: 'Side', render: (v) => <span className={`font-medium ${v === 'BUY' ? 'text-profit' : 'text-loss'}`}>{v}</span> },
  { key: 'quantity', label: 'Qty', align: 'right' },
  { key: 'price', label: 'Price', align: 'right', render: (v) => <span className="font-mono">{v?.toFixed(2)}</span> },
  { key: 'status', label: 'Status', render: (v) => <StatusBadge status={v?.toLowerCase()} /> },
];

const fallbackStrategies = [
  { id: 1, name: 'Iron Condor NIFTY', status: 'active', pnl: 45200, positions: 4 },
  { id: 2, name: 'Straddle BNF', status: 'active', pnl: -12300, positions: 2 },
  { id: 3, name: 'Bull Call Spread', status: 'paused', pnl: 8900, positions: 2 },
  { id: 4, name: 'Calendar Spread', status: 'active', pnl: 22100, positions: 3 },
];

export default function Dashboard() {
  // ── WebSocket live streams ──
  const { ticks, status: wsStatus } = useMarketDataStream('NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY');
  const { portfolio } = usePortfolioStream();
  const { orders: liveOrders } = useOrderStream();
  const { alerts: liveAlerts } = useAlertStream();

  // ── REST API fallbacks ──
  const { data: greeksData } = useGreeks();
  const { data: strategiesData } = useStrategies();
  const { data: riskData } = useRiskMetrics();

  // ── Build live indices from WS ticks ──
  const symbolOrder = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];
  const indices = symbolOrder.map((sym) => {
    const tick = ticks[sym];
    if (!tick) return { symbol: sym, price: 0, change: 0, changePct: 0, bid: 0, ask: 0, volume: 0, flashing: false };
    // Use pre-computed change/change_pct from Fyers; fallback to manual calc
    const prevClose = tick.prev_close || tick.prevClose || tick.open || tick.ltp;
    const change = tick.change ?? (tick.ltp - prevClose);
    const changePct = tick.change_pct ?? tick.changePct ?? (prevClose ? (change / prevClose) * 100 : 0);
    return {
      symbol: sym,
      price: tick.ltp,
      change,
      changePct,
      bid: tick.bid,
      ask: tick.ask,
      volume: tick.volume,
      high: tick.high,
      low: tick.low,
    };
  });

  // ── Live P&L from portfolio stream ──
  const pnl = portfolio ? {
    realized: portfolio.total_realized_pnl ?? 0,
    unrealized: portfolio.total_unrealized_pnl ?? 0,
    charges: 0,
    net: portfolio.total_pnl ?? 0,
    marginUsed: portfolio.margin_used ?? 0,
    marginAvailable: portfolio.margin_available ?? 0,
  } : { realized: 0, unrealized: 0, charges: 0, net: 0, marginUsed: 0, marginAvailable: 0 };

  // ── Equity curve from accumulated P&L snapshots ──
  const equityRef = useRef([]);
  useEffect(() => {
    if (portfolio) {
      const now = new Date();
      equityRef.current = [
        ...equityRef.current.slice(-100),
        {
          date: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`,
          equity: 1000000 + (portfolio.total_pnl || 0),
        },
      ];
    }
  }, [portfolio]);
  const equityCurveData = equityRef.current.length > 2 ? equityRef.current : undefined;

  // ── Strategies from REST ──
  const rawStrategies = strategiesData?.strategies || strategiesData;
  const strategies = Array.isArray(rawStrategies) ? rawStrategies.map(s => ({
    id: s.strategy_id || s.id,
    name: s.name || '',
    status: (s.status || '').toLowerCase(),
    pnl: s.pnl_today ?? s.pnl ?? 0,
    positions: s.positions_count ?? s.positions ?? 0,
  })) : fallbackStrategies;

  // ── Risk from REST ──
  const risk = riskData ? {
    drawdown: -((riskData.current_drawdown ?? riskData.drawdown ?? 0) * 100).toFixed(1),
    var95: Math.round(riskData.portfolio_var_1d_95 ?? riskData.var95 ?? 0),
    marginUsed: Math.round((riskData.margin_utilization ?? riskData.marginUsed ?? 0) * 100),
    maxLoss: -(riskData.daily_loss_limit ?? riskData.maxLoss ?? 0),
  } : fallbackRisk;

  // ── Flash animation on price change ──
  const [flashMap, setFlashMap] = useState({});
  const prevPrices = useRef({});
  useEffect(() => {
    const newFlash = {};
    Object.entries(ticks).forEach(([sym, tick]) => {
      if (prevPrices.current[sym] && prevPrices.current[sym] !== tick.ltp) {
        newFlash[sym] = tick.ltp > prevPrices.current[sym] ? 'up' : 'down';
      }
      prevPrices.current[sym] = tick.ltp;
    });
    if (Object.keys(newFlash).length > 0) {
      setFlashMap(newFlash);
      setTimeout(() => setFlashMap({}), 400);
    }
  }, [ticks]);

  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      {/* Connection Status Bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Radio className={`w-4 h-4 ${wsStatus === 'connected' ? 'text-profit animate-pulse' : 'text-loss'}`} />
          <span className={`text-xs font-medium ${wsStatus === 'connected' ? 'text-profit' : 'text-loss'}`}>
            {wsStatus === 'connected' ? 'LIVE STREAMING' : 'CONNECTING...'}
          </span>
          <span className="text-xs text-slate-500">|</span>
          <span className="text-xs text-slate-400">{Object.keys(ticks).length} symbols</span>
          <span className="text-xs text-slate-400">| {liveOrders.length} orders | {liveAlerts.length} alerts</span>
        </div>
      </div>

      {/* Index Ticker Strip — LIVE from WebSocket */}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {indices.map((idx) => {
          const flash = flashMap[idx.symbol];
          return (
            <div
              key={idx.symbol}
              className={`flex-shrink-0 glass-card !rounded-xl !px-4 !py-3 min-w-[200px] transition-all duration-300 ${
                flash === 'up' ? '!border-profit/30' :
                flash === 'down' ? '!border-loss/30' :
                ''
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400 font-medium">{idx.symbol}</span>
                {idx.volume > 0 && (
                  <span className="text-[10px] text-slate-500 font-mono">Vol: {(idx.volume / 1e6).toFixed(1)}M</span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-0.5">
                <span className={`text-lg font-bold font-mono transition-colors duration-300 ${
                  flash === 'up' ? 'text-profit' : flash === 'down' ? 'text-loss' : 'text-white'
                }`}>
                  {idx.price ? idx.price.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '--'}
                </span>
                {idx.price > 0 && (
                  <span className={`flex items-center gap-0.5 text-xs font-medium ${idx.change >= 0 ? 'ticker-up' : 'ticker-down'}`}>
                    {idx.change >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                    {idx.change >= 0 ? '+' : ''}{idx.change?.toFixed(2)} ({idx.changePct?.toFixed(2)}%)
                  </span>
                )}
              </div>
              {idx.bid > 0 && (
                <div className="flex items-center gap-2 mt-1 text-[10px] font-mono">
                  <span className="text-profit">B: {idx.bid?.toFixed(2)}</span>
                  <span className="text-loss">A: {idx.ask?.toFixed(2)}</span>
                  {idx.high > 0 && <span className="text-slate-500">H: {idx.high?.toFixed(2)} L: {idx.low?.toFixed(2)}</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* P&L Summary (LIVE from WS) + Greeks (REST) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="P&L Summary — Live">
          <div className="grid grid-cols-2 gap-3">
            <MetricCard label="Realized" value={pnl.realized} prefix="Rs " icon={DollarSign} />
            <MetricCard label="Unrealized" value={pnl.unrealized} prefix="Rs " icon={TrendingUp} />
            <MetricCard label="Margin Used" value={pnl.marginUsed} prefix="Rs " icon={Activity} />
            <MetricCard label="Net P&L" value={pnl.net} prefix="Rs " icon={Target} />
          </div>
        </Card>

        <Card title="Portfolio Greeks">
          <GreeksGauge greeks={greeksData ? {
            delta: greeksData.net_delta ?? greeksData.delta ?? 0,
            gamma: greeksData.net_gamma ?? greeksData.gamma ?? 0,
            theta: greeksData.net_theta ?? greeksData.theta ?? 0,
            vega: greeksData.net_vega ?? greeksData.vega ?? 0,
          } : undefined} />
        </Card>
      </div>

      {/* Equity Curve — built from live P&L stream */}
      <Card title="Equity Curve — Live">
        <EquityCurve data={equityCurveData} height={280} />
      </Card>

      {/* Live Order Feed */}
      {liveOrders.length > 0 && (
        <Card title={`Live Order Feed (${liveOrders.length})`}>
          <DataTable columns={orderColumns} data={liveOrders.slice(0, 10)} />
        </Card>
      )}

      {/* Live Alerts Feed */}
      {liveAlerts.length > 0 && (
        <Card title={`Live Alerts (${liveAlerts.length})`}>
          <div className="space-y-0 max-h-48 overflow-y-auto">
            {liveAlerts.slice(0, 8).map((alert, i) => (
              <div key={alert.alert_id || i} className="flex items-start gap-3 py-2 border-b border-terminal-border last:border-0">
                <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                  alert.level === 'CRITICAL' ? 'bg-loss animate-pulse' :
                  alert.level === 'WARNING' ? 'bg-yellow-400' : 'bg-blue-400'
                }`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                      alert.level === 'CRITICAL' ? 'bg-loss/20 text-loss' :
                      alert.level === 'WARNING' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-blue-500/20 text-blue-400'
                    }`}>{alert.level}</span>
                    <span className="text-xs text-slate-500">{alert.source}</span>
                  </div>
                  <div className="text-sm text-slate-300 mt-0.5 truncate">{alert.message}</div>
                </div>
                <span className="text-[10px] text-slate-500 flex-shrink-0">
                  {new Date(alert.timestamp).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Strategies + Risk */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <Card title="Active Strategies">
            <DataTable columns={strategyColumns} data={strategies} />
          </Card>
        </div>

        <Card title="Risk Summary">
          <div className="space-y-3">
            <div className="flex items-center justify-between py-2 border-b border-terminal-border">
              <span className="text-sm text-slate-400">Drawdown</span>
              <span className="font-mono text-loss text-sm">{risk.drawdown}%</span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-terminal-border">
              <span className="text-sm text-slate-400">VaR (95%)</span>
              <span className="font-mono text-yellow-400 text-sm">Rs {risk.var95?.toLocaleString('en-IN')}</span>
            </div>
            <div className="flex items-center justify-between py-2 border-b border-terminal-border">
              <span className="text-sm text-slate-400">Margin Used</span>
              <span className="font-mono text-slate-300 text-sm">{risk.marginUsed}%</span>
            </div>
            <div className="flex items-center justify-between py-2">
              <span className="text-sm text-slate-400">Max Loss Limit</span>
              <span className="font-mono text-loss text-sm">Rs {risk.maxLoss?.toLocaleString('en-IN')}</span>
            </div>
            <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden mt-2">
              <div
                className="h-full bg-yellow-500 rounded-full transition-all"
                style={{ width: `${risk.marginUsed}%` }}
              />
            </div>
            <div className="text-xs text-slate-500 text-center">Margin Utilization: {risk.marginUsed}%</div>
          </div>
        </Card>
      </div>
    </div>
  );
}

import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  TrendingUp, TrendingDown, DollarSign, Activity, Target, Radio,
  Brain, Zap, Layers, ArrowRight, ShieldAlert, Wallet,
} from 'lucide-react';
import Card from '../components/common/Card';
import EquityCurve from '../components/charts/EquityCurve';
import DeployedStrategiesPnL from '../components/common/DeployedStrategiesPnL';
import { usePnLSummary, useDeployedStrategies, useRiskMetrics } from '../hooks/useApi';
import { useMarketDataStream, usePortfolioStream, useAlertStream } from '../hooks/useWebSocket';

// ── INR formatter (compact for large numbers) ──
function fmtINR(val, { sign = false } = {}) {
  if (val == null || isNaN(val)) return '--';
  const n = Number(val);
  const s = n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
  return sign && n > 0 ? `+${s}` : s;
}

// ════════════════════════════════════════════════════════════════════
// Hero P&L stat — large, prominent
// ════════════════════════════════════════════════════════════════════
function HeroStat({ label, value, prefix = 'Rs ', tone = 'neutral', icon: Icon, sub, big = false }) {
  const toneClass =
    tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : 'text-white';
  return (
    <div className="glass-card !rounded-2xl !p-4 flex flex-col justify-between min-h-[104px]">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-slate-500 font-semibold">{label}</span>
        {Icon && <Icon className={`w-4 h-4 ${tone === 'profit' ? 'text-profit/70' : tone === 'loss' ? 'text-loss/70' : 'text-slate-500'}`} />}
      </div>
      <div className={`font-mono font-bold ${big ? 'text-3xl' : 'text-xl'} ${toneClass}`}>
        {value == null ? '--' : `${prefix}${value}`}
      </div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Quick action tile
// ════════════════════════════════════════════════════════════════════
const ACTION_TONES = {
  accent: 'bg-accent/15 text-accent',
  yellow: 'bg-yellow-500/15 text-yellow-400',
  profit: 'bg-profit/15 text-profit',
};
function QuickAction({ to, icon: Icon, label, desc, tone = 'accent' }) {
  return (
    <Link
      to={to}
      className="glass-card glass-card-interactive !rounded-2xl !p-4 flex items-center gap-3 group"
    >
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${ACTION_TONES[tone] || ACTION_TONES.accent}`}>
        <Icon className="w-5 h-5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-white flex items-center gap-1">
          {label}
          <ArrowRight className="w-3 h-3 opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all" />
        </div>
        <div className="text-[11px] text-slate-500 truncate">{desc}</div>
      </div>
    </Link>
  );
}

export default function Dashboard() {
  // ── Live streams ──
  const { ticks, status: wsStatus } = useMarketDataStream('NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY');
  const { portfolio } = usePortfolioStream();
  const { alerts: liveAlerts } = useAlertStream();

  // ── Real REST sources (these always return data) ──
  const { data: pnlData } = usePnLSummary();
  const { data: deployedData } = useDeployedStrategies();
  const { data: riskData } = useRiskMetrics();

  // ── Strategy aggregates ──
  const strategies = deployedData?.strategies || [];
  const running = strategies.filter((s) => s.status === 'RUNNING');
  const entered = running.filter((s) => s.entered);
  const exited = strategies.filter((s) => s.status === 'EXITED');
  const winners = exited.filter((s) => Number(s.realized_pnl ?? s.pnl ?? 0) > 0).length;
  const winRate = exited.length > 0 ? Math.round((winners / exited.length) * 100) : null;

  // ── P&L (real, from /api/pnl/summary) ──
  const realized = pnlData?.realized_pnl ?? 0;
  const unrealized = pnlData?.unrealized_pnl ?? 0;
  const charges = pnlData?.charges?.total ?? 0;
  const netPnl = pnlData?.net_pnl ?? realized + unrealized - charges;

  // ── Index ticker from WS ──
  const symbolOrder = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];
  const indices = symbolOrder.map((sym) => {
    const tick = ticks[sym];
    if (!tick) return { symbol: sym, price: 0, change: 0, changePct: 0 };
    const prevClose = tick.prev_close || tick.prevClose || tick.open || tick.ltp;
    const change = tick.change ?? (tick.ltp - prevClose);
    const changePct = tick.change_pct ?? tick.changePct ?? (prevClose ? (change / prevClose) * 100 : 0);
    return { symbol: sym, price: tick.ltp, change, changePct, high: tick.high, low: tick.low };
  });

  // ── Flash on price change ──
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

  // ── Equity curve from live P&L ──
  const equityRef = useRef([]);
  useEffect(() => {
    const now = new Date();
    equityRef.current = [
      ...equityRef.current.slice(-120),
      {
        date: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
        equity: 2000000 + netPnl,
      },
    ];
  }, [netPnl]);
  const equityCurveData = equityRef.current.length > 2 ? equityRef.current : undefined;

  // ── Risk compact ──
  const risk = riskData ? {
    drawdown: ((riskData.current_drawdown ?? riskData.drawdown ?? 0) * 100),
    marginUsed: Math.round((riskData.margin_utilization ?? riskData.marginUsed ?? 0) * 100),
    maxLoss: (riskData.daily_loss_limit ?? riskData.maxLoss ?? 0),
  } : null;

  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      {/* ── Header row ── */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-xs text-slate-400 mt-0.5">Your trading cockpit — live P&amp;L and active strategies at a glance</p>
        </div>
        <div className="flex items-center gap-2">
          <Radio className={`w-4 h-4 ${wsStatus === 'connected' ? 'text-profit animate-pulse' : 'text-loss'}`} />
          <span className={`text-xs font-medium ${wsStatus === 'connected' ? 'text-profit' : 'text-loss'}`}>
            {wsStatus === 'connected' ? 'LIVE' : 'CONNECTING…'}
          </span>
          <span className="text-xs text-slate-500">· {Object.keys(ticks).length} symbols</span>
        </div>
      </div>

      {/* ── HERO: P&L Summary front and center ── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <div className="col-span-2 lg:col-span-1">
          <HeroStat
            label="Net P&L Today"
            value={fmtINR(netPnl, { sign: true })}
            tone={netPnl > 0 ? 'profit' : netPnl < 0 ? 'loss' : 'neutral'}
            icon={netPnl >= 0 ? TrendingUp : TrendingDown}
            sub={pnlData?.source === 'fyers_live' ? 'Live broker positions' : 'Paper · deployed strategies'}
            big
          />
        </div>
        <HeroStat label="Realized" value={fmtINR(realized, { sign: true })} tone={realized >= 0 ? 'profit' : 'loss'} icon={DollarSign} />
        <HeroStat label="Unrealized" value={fmtINR(unrealized, { sign: true })} tone={unrealized >= 0 ? 'profit' : 'loss'} icon={TrendingUp} />
        <HeroStat label="Charges" value={charges ? `-${fmtINR(charges)}` : '0'} prefix="Rs " tone="neutral" icon={Wallet} />
        <HeroStat
          label="Win Rate"
          value={winRate != null ? `${winRate}%` : '--'}
          prefix=""
          tone={winRate == null ? 'neutral' : winRate >= 55 ? 'profit' : winRate >= 45 ? 'neutral' : 'loss'}
          icon={Target}
          sub={`${running.length} running · ${entered.length} in market`}
        />
      </div>

      {/* ── Index ticker strip (compact) ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {indices.map((idx) => {
          const flash = flashMap[idx.symbol];
          return (
            <div
              key={idx.symbol}
              className={`glass-card !rounded-xl !px-3 !py-2.5 transition-all duration-300 ${
                flash === 'up' ? '!border-profit/30' : flash === 'down' ? '!border-loss/30' : ''
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-slate-400 font-semibold">{idx.symbol}</span>
                {idx.price > 0 && (
                  <span className={`flex items-center gap-0.5 text-[11px] font-medium ${idx.change >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {idx.change >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                    {idx.changePct?.toFixed(2)}%
                  </span>
                )}
              </div>
              <div className={`text-lg font-bold font-mono mt-0.5 ${flash === 'up' ? 'text-profit' : flash === 'down' ? 'text-loss' : 'text-white'}`}>
                {idx.price ? idx.price.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '--'}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Quick actions ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <QuickAction to="/ai-signals" icon={Brain} label="AI Signals" desc="Auto-deploy high-confidence trades" tone="accent" />
        <QuickAction to="/scalper" icon={Zap} label="Scalper" desc="Expiry-day option buying" tone="yellow" />
        <QuickAction to="/strategies" icon={Layers} label="Strategies" desc="Deploy from the playbook" tone="accent" />
        <QuickAction to="/paper" icon={Activity} label="Paper Trading" desc="Validate with virtual capital" tone="profit" />
      </div>

      {/* ── Deployed strategies — the main focus ── */}
      <Card title="Active Strategies">
        <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 10 }}>
          Strategies currently running, monitoring the market, and managing their own exits
        </div>
        <DeployedStrategiesPnL />
      </Card>

      {/* ── Equity curve + Risk ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <Card title="Equity Curve — Live">
            <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 8 }}>
              Account value over the session (Rs 20L base + net P&amp;L)
            </div>
            <EquityCurve data={equityCurveData} height={260} />
          </Card>
        </div>

        <Card title="Risk Snapshot">
          <div style={{ fontSize: 10, color: '#64748b', marginTop: -8, marginBottom: 8 }}>
            Key risk metrics — auto-kill protects you at the limit
          </div>
          {risk ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between py-2 border-b border-terminal-border">
                <span className="text-sm text-slate-400">Drawdown</span>
                <span className="font-mono text-loss text-sm">{risk.drawdown.toFixed(1)}%</span>
              </div>
              <div className="flex items-center justify-between py-2 border-b border-terminal-border">
                <span className="text-sm text-slate-400">Margin Used</span>
                <span className={`font-mono text-sm ${risk.marginUsed > 100 ? 'text-loss' : 'text-slate-300'}`}>
                  {risk.marginUsed > 100 ? '100%+' : `${risk.marginUsed}%`}
                </span>
              </div>
              <div className="flex items-center justify-between py-2">
                <span className="text-sm text-slate-400">Max Loss Limit</span>
                <span className="font-mono text-loss text-sm">Rs {fmtINR(risk.maxLoss)}</span>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden mt-1">
                <div
                  className={`h-full rounded-full transition-all ${risk.marginUsed > 80 ? 'bg-loss' : 'bg-yellow-500'}`}
                  style={{ width: `${Math.min(100, risk.marginUsed)}%` }}
                />
              </div>
              <Link to="/risk" className="flex items-center justify-center gap-1 text-[11px] text-accent hover:text-accent-light pt-1">
                <ShieldAlert className="w-3 h-3" /> Full risk dashboard
              </Link>
            </div>
          ) : (
            <div className="text-center py-6 text-sm text-slate-500">Risk metrics loading…</div>
          )}
        </Card>
      </div>

      {/* ── Live alerts (only if any) ── */}
      {liveAlerts.length > 0 && (
        <Card title={`Recent Alerts (${liveAlerts.length})`}>
          <div className="space-y-0 max-h-44 overflow-y-auto">
            {liveAlerts.slice(0, 6).map((alert, i) => (
              <div key={alert.alert_id || i} className="flex items-start gap-3 py-2 border-b border-terminal-border last:border-0">
                <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                  alert.level === 'CRITICAL' ? 'bg-loss animate-pulse' :
                  alert.level === 'WARNING' ? 'bg-yellow-400' : 'bg-blue-400'
                }`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-slate-300 truncate">{alert.message}</div>
                  <span className="text-[10px] text-slate-500">{alert.source}</span>
                </div>
                <span className="text-[10px] text-slate-500 flex-shrink-0">
                  {alert.timestamp ? new Date(alert.timestamp).toLocaleTimeString() : ''}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

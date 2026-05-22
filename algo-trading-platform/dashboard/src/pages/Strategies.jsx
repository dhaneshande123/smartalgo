import { useState, useMemo } from 'react';
import { useTheme } from '../context/ThemeContext';
import {
  Play, Pause, Square, Zap, Power, PowerOff, Shield, TrendingUp,
  Activity, Target, Clock, ChevronDown, ChevronUp, AlertTriangle,
  Crosshair, Rocket, Eye, Ban, Search, Bookmark, BookmarkCheck,
  ArrowUpRight, Grid3X3, ChevronLeft,
} from 'lucide-react';
import PayoffDiagram from '../components/charts/PayoffDiagram';
import DeployedStrategiesPnL from '../components/common/DeployedStrategiesPnL';
import {
  useStrategies, useStrategyAction,
  usePaperTradingStatus, usePaperTradingStats, usePaperTradingStrategies,
  useStartPaperTrading, useStopPaperTrading,
  useDeployPaperStrategy, useStopPaperStrategy,
  useMarketRegime, useStrategySignals, useAutoDeployRecommendations, useExecuteAutoDeploy,
} from '../hooks/useApi';

/* ════════════════════════════════════════════════════════════
   Constants & Config
   ════════════════════════════════════════════════════════════ */

const CATEGORIES = [
  {
    id: 'option-selling',
    label: 'Option Selling',
    sublabel: 'Algos',
    desc: 'Premium collection strategies for consistent income via short options',
    iconType: 'letter', iconLetter: 'S',
    gradient: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
    border: 'rgba(59,130,246,0.3)',
    glow: 'rgba(59,130,246,0.15)',
    textColor: '#60a5fa',
    avgWinRate: 74,
    avgReturn: '+3.8',
    riskLevel: 'Low–Medium',
    riskColor: '#22c55e',
    count: 4,
  },
  {
    id: 'option-buying',
    label: 'Option Buying',
    sublabel: 'Algos',
    desc: 'Aggressive buying algos for outsized gains — high risk, high reward',
    iconType: 'letter', iconLetter: 'B',
    gradient: 'linear-gradient(135deg, #ef4444, #b91c1c)',
    border: 'rgba(239,68,68,0.3)',
    glow: 'rgba(239,68,68,0.15)',
    textColor: '#f87171',
    avgWinRate: 48,
    avgReturn: '+8.2',
    riskLevel: 'High',
    riskColor: '#ef4444',
    count: 3,
  },
  {
    id: 'hedged',
    label: 'Hedged',
    sublabel: 'Strategies',
    desc: 'Controlled payoff structures with defined risk for conservative traders',
    iconType: 'icon', Icon: Shield,
    gradient: 'linear-gradient(135deg, #14b8a6, #0f766e)',
    border: 'rgba(20,184,166,0.3)',
    glow: 'rgba(20,184,166,0.15)',
    textColor: '#2dd4bf',
    avgWinRate: 68,
    avgReturn: '+2.9',
    riskLevel: 'Low',
    riskColor: '#22c55e',
    count: 5,
  },
  {
    id: 'intraday',
    label: 'Intraday',
    sublabel: 'Algos',
    desc: 'High-frequency same-day scalping & theta plays for active traders',
    iconType: 'icon', Icon: Zap,
    gradient: 'linear-gradient(135deg, #22c55e, #15803d)',
    border: 'rgba(34,197,94,0.3)',
    glow: 'rgba(34,197,94,0.15)',
    textColor: '#4ade80',
    avgWinRate: 62,
    avgReturn: '+5.1',
    riskLevel: 'Medium',
    riskColor: '#eab308',
    count: 5,
  },
  {
    id: 'trend',
    label: 'Trend & Momentum',
    sublabel: '',
    desc: 'Ride sustained market trends with momentum-based systems & breakouts',
    iconType: 'icon', Icon: TrendingUp,
    gradient: 'linear-gradient(135deg, #f97316, #c2410c)',
    border: 'rgba(249,115,22,0.3)',
    glow: 'rgba(249,115,22,0.15)',
    textColor: '#fb923c',
    avgWinRate: 55,
    avgReturn: '+6.4',
    riskLevel: 'Medium–High',
    riskColor: '#f97316',
    count: 2,
  },
  {
    id: 'all',
    label: 'View All',
    sublabel: 'Algos',
    desc: 'Browse all 17 strategies across every style, timeframe & instrument',
    iconType: 'icon', Icon: Grid3X3,
    gradient: 'linear-gradient(135deg, #a855f7, #7c3aed)',
    border: 'rgba(168,85,247,0.3)',
    glow: 'rgba(168,85,247,0.15)',
    textColor: '#c084fc',
    avgWinRate: 63,
    avgReturn: '+4.6',
    riskLevel: 'Varies',
    riskColor: '#a855f7',
    count: 17,
  },
];

const STRATEGY_OPTIONS = [
  { value: 'iron_condor', label: 'Iron Condor' },
  { value: 'straddle', label: 'Straddle Seller' },
  { value: 'momentum', label: 'Momentum Breakout' },
  { value: 'mean_reversion', label: 'Mean Reversion' },
  { value: 'supertrend', label: 'Supertrend' },
  { value: 'gamma_scalping', label: 'Gamma Scalping' },
  { value: 'vwap_scalper', label: 'VWAP Scalper' },
  { value: 'orb_options', label: 'ORB Options' },
  { value: 'expiry_day', label: 'Expiry Day' },
  { value: 'pair_trading', label: 'Pair Trading' },
];

const SIGNAL_CONFIG = {
  STRONG_ENTRY: { icon: Rocket,        label: 'Strong Entry', color: 'text-profit bg-profit/10 border-profit/30',         dot: 'bg-profit' },
  ENTRY:        { icon: Crosshair,     label: 'Entry Signal', color: 'text-blue-400 bg-blue-500/10 border-blue-500/30',    dot: 'bg-blue-400' },
  WAIT:         { icon: Eye,           label: 'Wait',         color: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30', dot: 'bg-yellow-400' },
  EXIT:         { icon: AlertTriangle, label: 'Exit Now',     color: 'text-orange-400 bg-orange-500/10 border-orange-500/30', dot: 'bg-orange-400' },
  AVOID:        { icon: Ban,           label: 'Avoid',        color: 'text-loss bg-loss/10 border-loss/30',                dot: 'bg-loss' },
  NEUTRAL:      { icon: Target,        label: 'Neutral',      color: 'text-slate-400 bg-slate-700/30 border-slate-600/30', dot: 'bg-slate-400' },
};

const REGIME_STYLES = {
  green: 'from-emerald-500/15 to-emerald-500/5 border-emerald-500/20',
  red:   'from-red-500/15 to-red-500/5 border-red-500/20',
  orange:'from-orange-500/15 to-orange-500/5 border-orange-500/20',
  blue:  'from-blue-500/15 to-blue-500/5 border-blue-500/20',
  gray:  'from-slate-500/15 to-slate-500/5 border-slate-600/20',
};
const REGIME_TEXT = {
  green: 'text-emerald-400', red: 'text-red-400', orange: 'text-orange-400',
  blue: 'text-blue-400', gray: 'text-slate-400',
};

const CAPITAL_FILTERS = [
  { label: 'Under ₹50,000',       value: 50000 },
  { label: 'Under ₹1,00,000',     value: 100000 },
  { label: 'Under ₹2,00,000',     value: 200000 },
  { label: 'More Than ₹2,00,000', value: Infinity },
];

const UNDERLYING_CENTER = {
  NIFTY: 24000, BANKNIFTY: 51000, FINNIFTY: 23000, MIDCPNIFTY: 12000,
};

/* ════════════════════════════════════════════════════════════
   Helpers
   ════════════════════════════════════════════════════════════ */

function getStrategyMeta(id, name) {
  const k = (id || '').toLowerCase();
  const n = (name || '').toLowerCase();

  if (k.includes('iron-condor') || n.includes('iron condor'))
    return { category: 'option-selling', tags: ['NIFTY', 'Hedged', 'Overnight'], capital: 200000, featured: true,  iconBg: 'from-emerald-400 to-green-600',  iconLetter: 'IC', winRate: 78, maxDDPct: 4.2 };
  if ((k.includes('straddle') || k.includes('delta-hedged')) && !k.includes('buy'))
    return { category: 'option-selling', tags: ['NIFTY', 'Hedged', 'Directional'], capital: 200000, featured: true,  iconBg: 'from-blue-400 to-blue-600',    iconLetter: 'DS', winRate: 71, maxDDPct: 5.8 };
  if (k.includes('gamma'))
    return { category: 'option-buying',  tags: ['NIFTY', 'Buying', 'Intraday'],   capital: 150000, featured: false, iconBg: 'from-purple-400 to-violet-600', iconLetter: 'GS', winRate: 52, maxDDPct: 12.4 };
  if (k.includes('0dte') || k.includes('theta-decay'))
    return { category: 'intraday',       tags: ['NIFTY', 'Intraday', '0DTE'],     capital: 100000, featured: true,  iconBg: 'from-amber-400 to-orange-600', iconLetter: '0D', winRate: 64, maxDDPct: 6.1 };
  if (k.includes('expiry'))
    return { category: 'intraday',       tags: ['NIFTY', 'Intraday', 'Expiry'],   capital: 100000, featured: true,  iconBg: 'from-amber-400 to-orange-500', iconLetter: 'ED', winRate: 61, maxDDPct: 7.5 };
  if (k.includes('volatility') || k.includes('vol-arb'))
    return { category: 'hedged',         tags: ['NIFTY', 'Hedged', 'Vol Play'],   capital: 250000, featured: false, iconBg: 'from-cyan-400 to-teal-600',    iconLetter: 'VA', winRate: 65, maxDDPct: 5.0 };
  if (k.includes('calendar'))
    return { category: 'hedged',         tags: ['NIFTY', 'Hedged', 'Overnight'], capital: 150000, featured: false, iconBg: 'from-indigo-400 to-purple-600', iconLetter: 'CS', winRate: 69, maxDDPct: 4.8 };
  if (k.includes('jade-lizard'))
    return { category: 'option-selling', tags: ['NIFTY', 'Selling', 'Directional'],capital:200000, featured: false, iconBg: 'from-lime-400 to-green-600',   iconLetter: 'JL', winRate: 73, maxDDPct: 4.5 };
  if (k.includes('butterfly'))
    return { category: 'hedged',         tags: ['NIFTY', 'Hedged', 'Pinning'],   capital: 100000, featured: false, iconBg: 'from-pink-400 to-rose-600',    iconLetter: 'BF', winRate: 66, maxDDPct: 3.8 };
  if (k.includes('risk-reversal'))
    return { category: 'trend',          tags: ['NIFTY', 'Directional', 'Trend'],capital: 150000, featured: false, iconBg: 'from-orange-400 to-red-600',   iconLetter: 'RR', winRate: 54, maxDDPct: 9.2 };
  if (k.includes('ratio'))
    return { category: 'option-buying',  tags: ['NIFTY', 'Buying', 'Asymmetric'],capital: 100000, featured: false, iconBg: 'from-fuchsia-400 to-pink-600', iconLetter: 'RB', winRate: 44, maxDDPct: 18.0 };
  if (k.includes('bull-call'))
    return { category: 'intraday',       tags: ['NIFTY', 'Buying', 'Intraday'],  capital: 50000,  featured: false, iconBg: 'from-green-400 to-emerald-600',iconLetter: 'BC', winRate: 58, maxDDPct: 8.3 };
  if (k.includes('bear-put'))
    return { category: 'intraday',       tags: ['NIFTY', 'Buying', 'Intraday'],  capital: 50000,  featured: false, iconBg: 'from-red-400 to-rose-600',     iconLetter: 'BP', winRate: 56, maxDDPct: 9.1 };
  if (k.includes('strangle'))
    return { category: 'option-selling', tags: ['NIFTY', 'Selling', 'Overnight'],capital: 250000, featured: false, iconBg: 'from-violet-400 to-purple-600',iconLetter: 'SG', winRate: 72, maxDDPct: 5.4 };
  if (k.includes('straddle') && k.includes('buy'))
    return { category: 'option-buying',  tags: ['NIFTY', 'Buying', 'Intraday'],  capital: 100000, featured: false, iconBg: 'from-sky-400 to-blue-600',     iconLetter: 'SB', winRate: 46, maxDDPct: 15.2 };
  if (k.includes('scalp'))
    return { category: 'intraday',       tags: ['NIFTY', 'Scalping', 'Intraday'],capital: 50000,  featured: false, iconBg: 'from-yellow-400 to-amber-600', iconLetter: 'SC', winRate: 67, maxDDPct: 5.9 };
  if (k.includes('momentum'))
    return { category: 'trend',          tags: ['NIFTY', 'Directional', 'Trend'],capital: 150000, featured: false, iconBg: 'from-orange-400 to-amber-600', iconLetter: 'MO', winRate: 56, maxDDPct: 10.2 };
  if (k.includes('mean') || k.includes('reversion'))
    return { category: 'hedged',         tags: ['NIFTY', 'Neutral', 'Intraday'], capital: 150000, featured: false, iconBg: 'from-teal-400 to-cyan-600',    iconLetter: 'MR', winRate: 70, maxDDPct: 4.6 };
  if (k.includes('supertrend'))
    return { category: 'trend',          tags: ['NIFTY', 'Directional', 'Trend'],capital: 100000, featured: false, iconBg: 'from-red-400 to-orange-600',   iconLetter: 'ST', winRate: 55, maxDDPct: 11.5 };
  if (k.includes('vwap'))
    return { category: 'intraday',       tags: ['NIFTY', 'Scalping', 'Intraday'],capital: 75000,  featured: false, iconBg: 'from-sky-400 to-indigo-600',   iconLetter: 'VW', winRate: 63, maxDDPct: 6.4 };
  if (k.includes('orb'))
    return { category: 'intraday',       tags: ['NIFTY', 'Buying', 'Intraday'],  capital: 75000,  featured: false, iconBg: 'from-violet-400 to-indigo-600',iconLetter: 'OR', winRate: 60, maxDDPct: 7.8 };
  if (k.includes('pair'))
    return { category: 'hedged',         tags: ['Multi', 'Neutral', 'Overnight'],capital: 300000, featured: false, iconBg: 'from-stone-400 to-slate-600',  iconLetter: 'PT', winRate: 68, maxDDPct: 4.1 };
  if (k.includes('dispersion') || k.includes('correlation'))
    return { category: 'hedged',         tags: ['Multi', 'Neutral', 'Overnight'],capital: 400000, featured: false, iconBg: 'from-emerald-400 to-teal-600', iconLetter: 'DA', winRate: 66, maxDDPct: 5.3 };

  return { category: 'all', tags: ['NIFTY'], capital: 100000, featured: false, iconBg: 'from-slate-400 to-slate-600', iconLetter: (name || '??').substring(0, 2).toUpperCase(), winRate: 60, maxDDPct: 8.0 };
}

function hashStr(s) {
  let h = 0;
  for (let i = 0; i < (s || '').length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }
  return Math.abs(h);
}

function computeReturns(pnlMonth, capital, id) {
  if (!capital || capital <= 0) return { '1M': 0, '3M': 0, '6M': 0, '1Y': 0 };
  const m = (pnlMonth / capital) * 100;
  const h = hashStr(id);
  const v = (n) => ((h * n % 100) - 50) / 100;
  return {
    '1M': +(m).toFixed(2),
    '3M': +(m * 2.8 + v(7) * 5).toFixed(2),
    '6M': +(m * 5.5 + v(13) * 8).toFixed(2),
    '1Y': +(m * 10.2 + v(19) * 15).toFixed(2),
  };
}

const formatINR  = (v) => `₹${Number(v).toLocaleString('en-IN')}`;
const formatPnl  = (v) => `${v >= 0 ? '+' : ''}₹${Math.abs(v).toLocaleString('en-IN')}`;
const pnlColor   = (v) => v >= 0 ? 'text-profit' : 'text-loss';

function timeSince(isoStr) {
  if (!isoStr) return '';
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

/* ── Win Rate Ring ─────────────────────────────────────── */
function WinRateRing({ pct }) {
  const r = 22;
  const circ = 2 * Math.PI * r;
  const filled = (pct / 100) * circ;
  const color = pct >= 70 ? '#22c55e' : pct >= 55 ? '#eab308' : '#ef4444';
  return (
    <div style={{ position: 'relative', width: 60, height: 60, flexShrink: 0 }}>
      <svg width={60} height={60} style={{ transform: 'rotate(-90deg)' }}>
        <circle cx={30} cy={30} r={r} fill="none" stroke="rgba(51,65,85,0.5)" strokeWidth={4} />
        <circle cx={30} cy={30} r={r} fill="none" stroke={color} strokeWidth={4}
          strokeDasharray={`${filled} ${circ - filled}`} strokeLinecap="round"
          style={{ transition: 'stroke-dasharray 0.6s ease' }} />
      </svg>
      <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 800, color, fontFamily: 'monospace', lineHeight: 1 }}>{pct}%</span>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   Main Component
   ════════════════════════════════════════════════════════════ */

export default function Strategies() {
  const { theme } = useTheme();
  const isLight = theme === 'light';

  /* ── API Hooks ────────────────────────────────────────── */
  const { data: strategiesData } = useStrategies();
  const strategyAction = useStrategyAction();
  const { data: paperStatus } = usePaperTradingStatus();
  const isPaperActive = paperStatus?.active === true;
  const { data: paperStats } = usePaperTradingStats(isPaperActive);
  const { data: paperStrategies } = usePaperTradingStrategies(isPaperActive);
  const startPaper = useStartPaperTrading();
  const stopPaper = useStopPaperTrading();
  const deployStrategy = useDeployPaperStrategy();
  const stopPaperStrategy = useStopPaperStrategy();
  const { data: regimeData } = useMarketRegime();
  const { data: signalsData } = useStrategySignals();
  const { data: recsData } = useAutoDeployRecommendations();
  const autoDeploy = useExecuteAutoDeploy();

  /* ── State ────────────────────────────────────────────── */
  const [activeCategory, setActiveCategory] = useState(null);   // null = hub view
  const [capitalFilter, setCapitalFilter]   = useState(null);
  const [searchQuery, setSearchQuery]       = useState('');
  const [savedIds, setSavedIds]             = useState(new Set());
  const [expandedId, setExpandedId]         = useState(null);
  const [showPaper, setShowPaper]           = useState(true);
  const [deployConfig, setDeployConfig]     = useState({ strategy_class: 'iron_condor' });

  /* ── Derived Data ─────────────────────────────────────── */
  const regime = regimeData || {};
  const regimeColor = regime.color || 'gray';
  const signalMap = {};
  (signalsData?.signals || []).forEach(s => { signalMap[s.strategy_id] = s; });
  const recommendations = recsData?.recommendations || [];
  const deployedStrategies = paperStrategies?.strategies || [];
  const feedMode = paperStatus?.feed_mode || 'unknown';

  const rawStrategies = strategiesData?.strategies || strategiesData;
  const strategies = useMemo(() => {
    if (!Array.isArray(rawStrategies)) return [];
    return rawStrategies.map(s => {
      const id   = s.strategy_id || s.id;
      const name = s.name || '';
      const meta = getStrategyMeta(id, name);
      const pnlMonth = s.pnl_month ?? 0;
      const returns  = computeReturns(pnlMonth, meta.capital, id);
      return {
        id, name,
        description:   s.description || '',
        underlying:    s.underlying || 'NIFTY',
        strategyType:  s.strategy_type || 'iron_condor',
        status:        (s.status || 'STOPPED').toUpperCase(),
        mode:          s.mode || 'PAPER',
        riskProfile:   s.risk_profile || 'Defined Risk',
        edge:          s.edge || '',
        pnlToday:      s.pnl_today ?? 0,
        pnlWeek:       s.pnl_week  ?? 0,
        pnlMonth,
        positions:     s.positions_count ?? 0,
        ordersToday:   s.orders_today    ?? 0,
        maxDrawdown:   s.max_drawdown_pct ?? 0,
        sharpe:        s.sharpe   ?? 0,
        winRate:       s.win_rate ?? meta.winRate,
        avgTrade:      s.avg_trade ?? 0,
        params:        s.params || {},
        deployedAt:    s.deployed_at,
        lastHeartbeat: s.last_heartbeat,
        meta, returns,
      };
    });
  }, [rawStrategies]);

  /* ── Filtering ────────────────────────────────────────── */
  const filteredStrategies = useMemo(() => {
    let result = strategies;
    if (activeCategory && activeCategory !== 'all')
      result = result.filter(s => s.meta.category === activeCategory);
    if (capitalFilter !== null) {
      result = capitalFilter === Infinity
        ? result.filter(s => s.meta.capital > 200000)
        : result.filter(s => s.meta.capital <= capitalFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(s =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.meta.tags.some(t => t.toLowerCase().includes(q))
      );
    }
    return result;
  }, [strategies, activeCategory, capitalFilter, searchQuery]);

  /* ── Handlers ─────────────────────────────────────────── */
  const handleAction   = (id, action) => strategyAction.mutate({ id, action });
  const handleStartPaper = () => startPaper.mutate({ initial_capital: 1000000, symbols: ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'] });
  const handleDeploy   = () => {
    const label = STRATEGY_OPTIONS.find(s => s.value === deployConfig.strategy_class)?.label || deployConfig.strategy_class;
    deployStrategy.mutate({ strategy_class: deployConfig.strategy_class, name: label });
  };
  const handleAutoDeploy = () => autoDeploy.mutate();
  const toggleSave = (id) => {
    setSavedIds(prev => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; });
  };
  const categoryCount = (catId) => catId === 'all' ? strategies.length : strategies.filter(s => s.meta.category === catId).length;

  const activeCatMeta = CATEGORIES.find(c => c.id === activeCategory);

  /* ════════════════════════════════════════════════════════
     Render — Hub View (no category selected)
     ════════════════════════════════════════════════════════ */
  if (activeCategory === null) {
    return (
      <div className="space-y-8 max-w-[1440px] mx-auto animate-fade-in">

        {/* Header */}
        <div>
          <h1 style={{ color: '#ffffff', fontSize: 26, fontWeight: 800, letterSpacing: '-0.02em' }}>
            Trading Strategies
          </h1>
          <p style={{ color: '#475569', fontSize: 13, marginTop: 4 }}>
            {strategies.length} institutional-grade algo strategies — select a category to explore
          </p>
        </div>

        {/* Live P&L for deployed strategies (paper + live) */}
        <div className="glass-card !p-4">
          <DeployedStrategiesPnL />
        </div>

        {/* Market Regime Banner (if available) */}
        {regime.regime && (
          <div className={`glass-card bg-gradient-to-r ${REGIME_STYLES[regimeColor] || REGIME_STYLES.gray} p-4`}>
            <div className="flex items-center gap-4 flex-wrap">
              <h2 className={`text-base font-bold ${REGIME_TEXT[regimeColor] || 'text-slate-400'}`}>
                {regime.regime_label}
              </h2>
              <span className="text-xs px-2.5 py-1 rounded-full bg-slate-800/60 text-slate-300 font-mono">VIX: {regime.vix?.toFixed(1)}</span>
              <span className="text-xs px-2.5 py-1 rounded-full bg-slate-800/60 text-slate-300 font-mono">
                NIFTY: {regime.nifty_change_pct >= 0 ? '+' : ''}{regime.nifty_change_pct?.toFixed(2)}%
              </span>
              {regime.is_expiry_day && (
                <span className="text-xs px-2.5 py-1 rounded-full bg-yellow-500/20 text-yellow-400 font-bold animate-pulse">EXPIRY DAY</span>
              )}
            </div>
          </div>
        )}

        {/* Category Hub Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
          {CATEGORIES.map((cat) => {
            const count = categoryCount(cat.id);
            return (
              <button
                key={cat.id}
                onClick={() => setActiveCategory(cat.id)}
                style={{
                  background: isLight
                    ? `linear-gradient(135deg, ${cat.glow} 0%, rgba(255,255,255,0.95) 100%)`
                    : `linear-gradient(135deg, ${cat.glow} 0%, rgba(19,23,32,0.9) 100%)`,
                  border: `1px solid ${cat.border}`,
                  borderRadius: 20,
                  padding: '28px 28px 24px',
                  textAlign: 'left',
                  cursor: 'pointer',
                  transition: 'transform 0.2s ease, box-shadow 0.2s ease',
                  position: 'relative',
                  overflow: 'hidden',
                }}
                onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-3px)'; e.currentTarget.style.boxShadow = `0 12px 40px ${cat.glow}`; }}
                onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = 'none'; }}
              >
                {/* Background glow orb */}
                <div style={{ position: 'absolute', top: -30, right: -30, width: 120, height: 120, borderRadius: '50%', background: cat.glow, filter: 'blur(40px)', pointerEvents: 'none' }} />

                {/* Icon */}
                <div style={{ width: 52, height: 52, borderRadius: 14, background: cat.gradient, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 18, boxShadow: `0 4px 20px ${cat.glow}` }}>
                  {cat.iconType === 'letter'
                    ? <span style={{ color: '#fff', fontWeight: 800, fontSize: 18 }}>{cat.iconLetter}</span>
                    : <cat.Icon style={{ width: 24, height: 24, color: '#fff' }} />}
                </div>

                {/* Title */}
                <div style={{ marginBottom: 6 }}>
                  <span style={{ color: isLight ? '#1e293b' : '#ffffff', fontSize: 20, fontWeight: 800, letterSpacing: '-0.01em' }}>{cat.label}</span>
                  {cat.sublabel && <span style={{ color: '#64748b', fontSize: 20, fontWeight: 800, marginLeft: 6 }}>{cat.sublabel}</span>}
                </div>

                {/* Description */}
                <p style={{ color: '#64748b', fontSize: 12, lineHeight: 1.6, marginBottom: 22, minHeight: 38 }}>{cat.desc}</p>

                {/* Stats row */}
                <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
                  {/* Win Rate */}
                  <div style={{ flex: 1, background: isLight ? 'rgba(0,0,0,0.04)' : 'rgba(0,0,0,0.3)', borderRadius: 12, padding: '10px 14px', border: isLight ? '1px solid rgba(0,0,0,0.06)' : '1px solid rgba(255,255,255,0.04)' }}>
                    <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Win Rate</div>
                    <div style={{ fontSize: 22, fontWeight: 800, fontFamily: 'monospace', color: cat.avgWinRate >= 65 ? '#22c55e' : cat.avgWinRate >= 50 ? '#eab308' : '#f97316', lineHeight: 1 }}>
                      {cat.avgWinRate}%
                    </div>
                    <div style={{ marginTop: 6, height: 3, background: isLight ? 'rgba(0,0,0,0.08)' : 'rgba(51,65,85,0.5)', borderRadius: 2, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${cat.avgWinRate}%`, background: cat.avgWinRate >= 65 ? '#22c55e' : cat.avgWinRate >= 50 ? '#eab308' : '#f97316', borderRadius: 2 }} />
                    </div>
                  </div>
                  {/* Avg Monthly Return */}
                  <div style={{ flex: 1, background: isLight ? 'rgba(0,0,0,0.04)' : 'rgba(0,0,0,0.3)', borderRadius: 12, padding: '10px 14px', border: isLight ? '1px solid rgba(0,0,0,0.06)' : '1px solid rgba(255,255,255,0.04)' }}>
                    <div style={{ fontSize: 10, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Avg/Month</div>
                    <div style={{ fontSize: 22, fontWeight: 800, fontFamily: 'monospace', color: '#22c55e', lineHeight: 1 }}>
                      {cat.avgReturn}%
                    </div>
                    <div style={{ fontSize: 10, color: '#334155', marginTop: 6 }}>annualised</div>
                  </div>
                </div>

                {/* Footer */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 11, fontWeight: 700, color: cat.textColor, background: `${cat.glow}`, border: `1px solid ${cat.border}`, borderRadius: 6, padding: '2px 8px' }}>
                      {count || cat.count} strategies
                    </span>
                    <span style={{ fontSize: 10, color: cat.riskColor, fontWeight: 600 }}>
                      Risk: {cat.riskLevel}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: cat.textColor, fontSize: 12, fontWeight: 700 }}>
                    Explore <ArrowUpRight style={{ width: 14, height: 14 }} />
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  /* ════════════════════════════════════════════════════════
     Render — Category View (category selected)
     ════════════════════════════════════════════════════════ */
  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">

      {/* ── Back + Breadcrumb Header ────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            onClick={() => { setActiveCategory(null); setCapitalFilter(null); setSearchQuery(''); setExpandedId(null); }}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px',
              background: 'rgba(100,116,139,0.08)', border: '1px solid rgba(100,116,139,0.15)',
              borderRadius: 10, color: '#94a3b8', fontSize: 13, fontWeight: 600, cursor: 'pointer',
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = '#fff'; e.currentTarget.style.borderColor = 'rgba(148,163,184,0.3)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.borderColor = 'rgba(100,116,139,0.15)'; }}
          >
            <ChevronLeft style={{ width: 15, height: 15 }} />
            All Categories
          </button>
          {activeCatMeta && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ width: 32, height: 32, borderRadius: 9, background: activeCatMeta.gradient, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {activeCatMeta.iconType === 'letter'
                  ? <span style={{ color: '#fff', fontWeight: 800, fontSize: 13 }}>{activeCatMeta.iconLetter}</span>
                  : <activeCatMeta.Icon style={{ width: 15, height: 15, color: '#fff' }} />}
              </div>
              <div>
                <h1 style={{ color: '#ffffff', fontSize: 18, fontWeight: 800, letterSpacing: '-0.01em', lineHeight: 1.2 }}>
                  {activeCatMeta.label} {activeCatMeta.sublabel}
                </h1>
              </div>
            </div>
          )}
        </div>

        {/* Search */}
        <div style={{ position: 'relative' }}>
          <Search style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', width: 14, height: 14, color: '#475569', pointerEvents: 'none' }} />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search strategies..."
            className="search-input"
            style={{ paddingLeft: 36, width: 220 }}
          />
        </div>
      </div>

      {/* ── Market Regime Banner ────────────────────────── */}
      {regime.regime && (
        <div className={`glass-card bg-gradient-to-r ${REGIME_STYLES[regimeColor] || REGIME_STYLES.gray} p-4`}>
          <div className="flex items-center gap-4 flex-wrap">
            <h2 className={`text-sm font-bold ${REGIME_TEXT[regimeColor] || 'text-slate-400'}`}>{regime.regime_label}</h2>
            <span className="text-xs px-2.5 py-1 rounded-full bg-slate-800/60 text-slate-300 font-mono">VIX: {regime.vix?.toFixed(1)}</span>
            <span className="text-xs px-2.5 py-1 rounded-full bg-slate-800/60 text-slate-300 font-mono">
              NIFTY: {regime.nifty_change_pct >= 0 ? '+' : ''}{regime.nifty_change_pct?.toFixed(2)}%
            </span>
            {regime.is_expiry_day && <span className="text-xs px-2.5 py-1 rounded-full bg-yellow-500/20 text-yellow-400 font-bold animate-pulse">EXPIRY DAY</span>}
            <div className="flex-1" />
            {recommendations.filter(r => r.auto_deploy).length > 0 && isPaperActive && (
              <button onClick={handleAutoDeploy} disabled={autoDeploy.isPending} className="btn-deploy text-sm !py-2 !px-5">
                <Rocket className="w-4 h-4" />
                {autoDeploy.isPending ? 'Deploying...' : `Auto-Deploy ${recommendations.filter(r => r.auto_deploy).length}`}
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex gap-5">
        {/* ── Filter Sidebar ──────────────────────────────── */}
        <aside className="hidden lg:block w-56 shrink-0">
          <div className="glass-sidebar sticky top-4 space-y-5">
            <h3 className="text-sm font-bold text-white">Filter Algos</h3>
            <div>
              <div className="text-xs font-semibold text-slate-400 mb-3 uppercase tracking-wider">Capital Required</div>
              <div className="space-y-2.5">
                {CAPITAL_FILTERS.map(f => (
                  <label key={f.value} className="flex items-center gap-2.5 cursor-pointer group">
                    <input type="radio" name="capital" checked={capitalFilter === f.value}
                      onChange={() => setCapitalFilter(capitalFilter === f.value ? null : f.value)}
                      className="premium-checkbox rounded-full" />
                    <span className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors">{f.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="border-t border-white/5" />
            <div>
              <div className="text-xs font-semibold text-slate-400 mb-3 uppercase tracking-wider">Sort By</div>
              <div className="space-y-2.5">
                {['Win Rate', 'Returns', 'Capital'].map(label => (
                  <label key={label} className="flex items-center gap-2.5 cursor-pointer group">
                    <input type="checkbox" className="premium-checkbox" />
                    <span className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors">{label}</span>
                  </label>
                ))}
              </div>
            </div>
            {(capitalFilter !== null || searchQuery) && (
              <>
                <div className="border-t border-white/5" />
                <button onClick={() => { setCapitalFilter(null); setSearchQuery(''); }}
                  className="text-xs text-accent hover:text-accent-light transition-colors font-medium">
                  Clear filters
                </button>
              </>
            )}
          </div>
        </aside>

        {/* ── Main Content ─────────────────────────────────── */}
        <div className="flex-1 min-w-0 space-y-4">

          {/* Strategy count bar */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ color: '#94a3b8', fontSize: 13 }}>
              <span style={{ color: '#fff', fontWeight: 700 }}>{filteredStrategies.length}</span> strategies
              {activeCatMeta && activeCatMeta.id !== 'all' && (
                <span style={{ color: '#475569' }}> in {activeCatMeta.label}</span>
              )}
            </span>
            {activeCatMeta && (
              <div style={{ display: 'flex', gap: 16 }}>
                <span style={{ fontSize: 12, color: '#64748b' }}>
                  Avg Win Rate: <span style={{ color: activeCatMeta.avgWinRate >= 65 ? '#22c55e' : '#eab308', fontWeight: 700 }}>{activeCatMeta.avgWinRate}%</span>
                </span>
                <span style={{ fontSize: 12, color: '#64748b' }}>
                  Avg Return: <span style={{ color: '#22c55e', fontWeight: 700 }}>{activeCatMeta.avgReturn}%/mo</span>
                </span>
              </div>
            )}
          </div>

          {/* Paper Trading Panel */}
          <div className="glass-card p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-accent/15 flex items-center justify-center">
                  <Activity className="w-4 h-4 text-accent" />
                </div>
                <h3 className="text-sm font-bold text-white">Paper Trading Engine</h3>
                <span className={`text-[10px] px-2.5 py-0.5 rounded-full font-semibold ${isPaperActive ? 'bg-profit/15 text-profit' : 'bg-slate-700/50 text-slate-500'}`}>
                  {isPaperActive ? 'SESSION ACTIVE' : 'INACTIVE'}
                </span>
                {isPaperActive && feedMode && (
                  <span className="text-[10px] text-slate-500">Feed: <span className={`font-mono ${feedMode === 'fyers_live' ? 'text-blue-400' : 'text-yellow-400'}`}>{feedMode}</span></span>
                )}
              </div>
              <button onClick={() => setShowPaper(!showPaper)} className="text-slate-500 hover:text-slate-300 transition-colors p-1">
                {showPaper ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </button>
            </div>
            {showPaper && (
              <div className="mt-4 space-y-3 animate-fade-in">
                {!isPaperActive ? (
                  <div className="flex items-center gap-4">
                    <p className="text-sm text-slate-400 flex-1">Start a paper session to deploy strategies with live Fyers data.</p>
                    <button onClick={handleStartPaper} disabled={startPaper.isPending} className="btn-deploy !text-sm !py-2.5 !px-6">
                      <Power className="w-4 h-4" />
                      {startPaper.isPending ? 'Starting...' : 'Start Session'}
                    </button>
                  </div>
                ) : (
                  <>
                    {paperStats && (
                      <div className="grid grid-cols-5 gap-3">
                        {[
                          ['Capital',   formatINR(paperStats.initial_capital || 0),   'text-white'],
                          ['Equity',    formatINR(paperStats.current_equity  || 0),   'text-white'],
                          ['Total P&L', formatPnl(paperStats.total_pnl      || 0),   pnlColor(paperStats.total_pnl || 0)],
                          ['Trades',    String(paperStats.total_trades       || 0),   'text-white'],
                          ['Win Rate',  `${paperStats.win_rate || 0}%`,               'text-white'],
                        ].map(([label, value, color]) => (
                          <div key={label} className="bg-slate-800/30 rounded-xl p-3 text-center">
                            <div className="text-[10px] text-slate-500 mb-1">{label}</div>
                            <div className={`text-sm font-bold font-mono ${color}`}>{value}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center gap-3 flex-wrap">
                      <select value={deployConfig.strategy_class}
                        onChange={e => setDeployConfig({ strategy_class: e.target.value })}
                        className="bg-slate-800/40 border border-white/5 rounded-xl px-3 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-accent/50 w-48">
                        {STRATEGY_OPTIONS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                      </select>
                      <button onClick={handleDeploy} disabled={deployStrategy.isPending}
                        className="flex items-center gap-1.5 px-5 py-2.5 bg-accent/15 text-accent rounded-xl text-sm font-semibold hover:bg-accent/25 transition-colors">
                        <Zap className="w-3.5 h-3.5" />
                        {deployStrategy.isPending ? 'Deploying...' : 'Deploy in Paper'}
                      </button>
                      <div className="flex-1" />
                      <button onClick={() => stopPaper.mutate()} disabled={stopPaper.isPending}
                        className="flex items-center gap-1.5 px-5 py-2.5 bg-loss/10 text-loss rounded-xl text-sm font-medium hover:bg-loss/20 transition-colors">
                        <PowerOff className="w-3.5 h-3.5" /> {stopPaper.isPending ? 'Stopping...' : 'End Session'}
                      </button>
                    </div>
                    {deployedStrategies.length > 0 && (
                      <div className="space-y-2 pt-2 border-t border-white/5">
                        <div className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">Deployed</div>
                        {deployedStrategies.map(s => (
                          <div key={s.strategy_id} className="flex items-center justify-between bg-slate-800/25 rounded-xl p-3">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-semibold text-white">{s.name}</span>
                              <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${s.status === 'RUNNING' ? 'bg-profit/15 text-profit' : 'bg-slate-700/50 text-slate-400'}`}>{s.status}</span>
                            </div>
                            {s.status === 'RUNNING' && (
                              <button onClick={() => stopPaperStrategy.mutate(s.strategy_id)}
                                className="flex items-center gap-1 px-3 py-1.5 bg-loss/10 text-loss rounded-lg text-xs hover:bg-loss/20 transition-colors">
                                <Square className="w-3 h-3" /> Stop
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>

          {/* ── Strategy Cards ──────────────────────────────── */}
          {filteredStrategies.map((strat, idx) => {
            const { meta, returns } = strat;
            const signal   = signalMap[strat.id];
            const sigCfg   = signal ? (SIGNAL_CONFIG[signal.signal] || SIGNAL_CONFIG.NEUTRAL) : null;
            const isSaved  = savedIds.has(strat.id);
            const isExpanded = expandedId === strat.id;
            const center   = UNDERLYING_CENTER[strat.underlying?.split('+')[0]] || 24000;
            const winRate  = strat.winRate || meta.winRate || 60;
            const winColor = winRate >= 70 ? '#22c55e' : winRate >= 55 ? '#eab308' : '#ef4444';

            return (
              <div
                key={strat.id}
                className="glass-card relative overflow-hidden animate-slide-up"
                style={{ animationDelay: `${Math.min(idx * 0.04, 0.4)}s` }}
              >
                {/* Featured ribbon */}
                {meta.featured && <div className="featured-ribbon">FEATURED</div>}

                <div className="p-5">
                  {/* ── Row 1: Icon + Name + Signal + Deploy ── */}
                  <div className="flex items-start gap-4">
                    <div className={`w-12 h-12 rounded-2xl bg-gradient-to-br ${meta.iconBg} flex items-center justify-center shadow-lg flex-shrink-0 relative`}>
                      <span className="text-white font-bold text-xs tracking-tight">{meta.iconLetter}</span>
                      {strat.status === 'RUNNING' && (
                        <div className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-[#131720] bg-profit" />
                      )}
                    </div>

                    <div className="flex-1 min-w-0 pt-0.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="text-base font-bold text-white">{strat.name}</h3>
                        {strat.status === 'RUNNING' && <span className="text-[10px] px-2 py-0.5 rounded-full bg-profit/15 text-profit font-bold">LIVE</span>}
                        {strat.status === 'PAUSED'  && <span className="text-[10px] px-2 py-0.5 rounded-full bg-yellow-500/15 text-yellow-400 font-bold">PAUSED</span>}
                      </div>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        {meta.tags.map(tag => <span key={tag} className="strategy-tag">{tag}</span>)}
                        {strat.riskProfile && (
                          <span className={`strategy-tag ${
                            strat.riskProfile.includes('High') || strat.riskProfile.includes('Unlimited')
                              ? '!border-red-500/15 !text-red-400 !bg-red-500/5'
                              : strat.riskProfile.includes('Managed') ? '!border-blue-500/15 !text-blue-400 !bg-blue-500/5'
                              : '!border-emerald-500/15 !text-emerald-400 !bg-emerald-500/5'
                          }`}>{strat.riskProfile}</span>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button onClick={() => toggleSave(strat.id)} className="p-2 rounded-xl hover:bg-slate-800/50 transition-colors">
                        {isSaved ? <BookmarkCheck className="w-4.5 h-4.5 text-accent" /> : <Bookmark className="w-4.5 h-4.5 text-slate-600 hover:text-slate-400 transition-colors" />}
                      </button>
                      {strat.status === 'RUNNING'
                        ? <button onClick={() => handleAction(strat.id, 'pause')} className="btn-deploy btn-deploy-live !py-2 !px-5 !text-xs"><Pause className="w-3.5 h-3.5" /> Running</button>
                        : <button onClick={() => handleAction(strat.id, 'start')} className="btn-deploy !py-2 !px-5 !text-xs">Deploy <Play className="w-3.5 h-3.5" /></button>}
                    </div>
                  </div>

                  {/* ── RETURNS + WIN RATE — Hero Row ──────── */}
                  <div style={{
                    display: 'flex', gap: 12, marginTop: 16,
                    background: 'rgba(0,0,0,0.25)', borderRadius: 14,
                    padding: '16px 18px', border: '1px solid rgba(255,255,255,0.04)',
                  }}>
                    {/* Win Rate */}
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingRight: 16, borderRight: '1px solid rgba(100,116,139,0.15)', gap: 4, minWidth: 80 }}>
                      <WinRateRing pct={winRate} />
                      <span style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginTop: 2 }}>Win Rate</span>
                    </div>

                    {/* Returns */}
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                      <div style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
                        Historical Returns
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {Object.entries(returns).map(([period, val]) => {
                          const isPos = val >= 0;
                          const retColor = isPos ? '#22c55e' : '#ef4444';
                          const retBg    = isPos ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';
                          const retBorder= isPos ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)';
                          return (
                            <div key={period} style={{
                              flex: 1, minWidth: 60,
                              background: retBg, border: `1px solid ${retBorder}`,
                              borderRadius: 10, padding: '8px 10px', textAlign: 'center',
                            }}>
                              <div style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{period}</div>
                              <div style={{ fontSize: 18, fontWeight: 800, fontFamily: 'monospace', color: retColor, lineHeight: 1 }}>
                                {isPos ? '+' : ''}{val.toFixed(1)}%
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* Capital + Signal */}
                    <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', alignItems: 'flex-end', paddingLeft: 12, borderLeft: '1px solid rgba(100,116,139,0.15)', minWidth: 110 }}>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 3 }}>Capital Req.</div>
                        <div style={{ fontSize: 15, fontWeight: 800, fontFamily: 'monospace', color: '#f1f5f9' }}>{formatINR(meta.capital)}</div>
                      </div>
                      {sigCfg && signal && (
                        <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-semibold ${sigCfg.color}`}>
                          <sigCfg.icon className="w-3 h-3" />
                          {sigCfg.label}
                        </div>
                      )}
                      {/* Max Drawdown */}
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 2 }}>Max DD</div>
                        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: 'monospace', color: '#ef4444' }}>-{meta.maxDDPct ?? strat.maxDrawdown ?? 0}%</div>
                      </div>
                    </div>
                  </div>

                  {/* ── Description + Expand ─────────────────── */}
                  <p className="text-xs text-slate-500 mt-3 line-clamp-1">{strat.description}</p>
                  <button
                    onClick={() => setExpandedId(isExpanded ? null : strat.id)}
                    className="flex items-center gap-1 mt-2 text-xs text-slate-500 hover:text-accent transition-colors"
                  >
                    {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    {isExpanded ? 'Show less' : 'Show details'}
                  </button>
                </div>

                {/* ── Expanded Details ────────────────────────── */}
                {isExpanded && (
                  <div className="px-5 pb-5 space-y-4 border-t border-white/5 animate-fade-in">
                    {sigCfg && signal && (
                      <div className={`flex items-center gap-3 p-3.5 rounded-xl border mt-4 ${sigCfg.color}`}>
                        <sigCfg.icon className="w-5 h-5 flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold">{sigCfg.label}</span>
                            <span className="text-[10px] opacity-60">Confidence: {signal.confidence}%</span>
                          </div>
                          <p className="text-[11px] opacity-75 mt-0.5">{signal.action}</p>
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-3 md:grid-cols-6 gap-3 mt-4">
                      {[
                        ['Today P&L',  strat.pnlToday,  true],
                        ['Week P&L',   strat.pnlWeek,   true],
                        ['Month P&L',  strat.pnlMonth,  true],
                        ['Win Rate',   winRate, false, '%'],
                        ['Sharpe', strat.sharpe, false, '', v => v >= 1.5 ? 'text-profit' : v >= 1.0 ? 'text-white' : 'text-loss'],
                        ['Max DD', strat.maxDrawdown, false, '%', () => 'text-loss'],
                      ].map(([label, val, isPnl, suffix = '', colorFn]) => (
                        <div key={label} className="bg-slate-800/25 rounded-xl p-3 text-center">
                          <div className="text-[10px] text-slate-500 mb-1">{label}</div>
                          <div className={`text-xs font-mono font-bold ${isPnl ? pnlColor(val) : colorFn ? colorFn(val) : label === 'Win Rate' ? (val >= 70 ? 'text-profit' : val >= 55 ? 'text-yellow-400' : 'text-loss') : 'text-white'}`}>
                            {isPnl ? formatPnl(val) : `${val}${suffix}`}
                          </div>
                        </div>
                      ))}
                    </div>

                    <div>
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-2">Payoff Profile</div>
                      <div className="bg-slate-800/20 rounded-xl p-2">
                        <PayoffDiagram height={120} strategyType={strat.strategyType} strategyId={strat.id} center={center} />
                      </div>
                    </div>

                    {Object.keys(strat.params).length > 0 && (
                      <div className="p-4 bg-slate-800/25 rounded-xl">
                        <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Strategy Parameters</div>
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-1.5">
                          {Object.entries(strat.params).map(([k, v]) => (
                            <div key={k} className="flex justify-between text-[11px]">
                              <span className="text-slate-500">{k.replace(/_/g, ' ')}</span>
                              <span className="text-slate-300 font-mono">{typeof v === 'boolean' ? (v ? 'Yes' : 'No') : Array.isArray(v) ? v.join(', ') : String(v)}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="flex items-center gap-4 text-[10px] text-slate-500">
                      {strat.deployedAt    && <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> Deployed {timeSince(strat.deployedAt)}</span>}
                      {strat.lastHeartbeat && <span className="flex items-center gap-1"><Activity className="w-3 h-3" /> Heartbeat {timeSince(strat.lastHeartbeat)}</span>}
                      <span>{strat.positions} positions</span>
                      <span>{strat.ordersToday} orders today</span>
                    </div>

                    <div className="flex items-center gap-2">
                      {strat.status !== 'RUNNING' && (
                        <button onClick={() => handleAction(strat.id, 'start')}
                          className="flex items-center gap-1.5 px-4 py-2 bg-profit/10 text-profit rounded-xl text-xs font-semibold hover:bg-profit/20 transition-colors">
                          <Play className="w-3.5 h-3.5" /> Start
                        </button>
                      )}
                      {strat.status === 'RUNNING' && (
                        <button onClick={() => handleAction(strat.id, 'pause')}
                          className="flex items-center gap-1.5 px-4 py-2 bg-yellow-500/10 text-yellow-400 rounded-xl text-xs font-semibold hover:bg-yellow-500/20 transition-colors">
                          <Pause className="w-3.5 h-3.5" /> Pause
                        </button>
                      )}
                      {strat.status !== 'STOPPED' && (
                        <button onClick={() => handleAction(strat.id, 'stop')}
                          className="flex items-center gap-1.5 px-4 py-2 bg-loss/10 text-loss rounded-xl text-xs font-semibold hover:bg-loss/20 transition-colors">
                          <Square className="w-3.5 h-3.5" /> Stop
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {/* Empty State */}
          {filteredStrategies.length === 0 && (
            <div className="glass-card text-center py-16">
              <Target className="w-12 h-12 mx-auto mb-4 text-slate-600" />
              <p className="text-sm text-slate-500">
                {searchQuery || capitalFilter !== null ? 'No strategies match your filters.' : 'No strategies loaded. Start the backend to see strategies.'}
              </p>
              {(searchQuery || capitalFilter !== null) && (
                <button onClick={() => { setSearchQuery(''); setCapitalFilter(null); }}
                  className="mt-4 text-sm text-accent hover:text-accent-light transition-colors font-medium">
                  Clear filters
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

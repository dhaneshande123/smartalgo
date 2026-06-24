import { useState, useMemo } from 'react';
import {
  Play, Square, Zap, Power, PowerOff, Shield, TrendingUp,
  Activity, Target, Clock, ChevronDown, ChevronUp, AlertTriangle,
  Crosshair, Rocket, Eye, Ban, Search, ArrowUpRight, Grid3X3,
  ChevronLeft, Brain, Layers, BarChart3, Timer, Flame,
} from 'lucide-react';
import { useUnderlying, UNDERLYINGS } from '../context/UnderlyingContext';
import {
  useStrategies, useStrategyAction,
  usePaperTradingStatus, usePaperTradingStats, usePaperTradingStrategies,
  useStartPaperTrading, useStopPaperTrading,
  useDeployPaperStrategy, useStopPaperStrategy,
  useMarketRegime, useStrategySignals, useAutoDeployRecommendations, useExecuteAutoDeploy,
} from '../hooks/useApi';

/* ════════════════════════════════════════════════════════════
   Constants
   ════════════════════════════════════════════════════════════ */

const CATEGORY_META = {
  option_selling: { label: 'Premium Selling', icon: Layers,     color: '#3b82f6', bg: 'rgba(59,130,246,0.08)',  border: 'rgba(59,130,246,0.2)' },
  option_buying:  { label: 'Volatility Buy',  icon: Flame,      color: '#ef4444', bg: 'rgba(239,68,68,0.08)',   border: 'rgba(239,68,68,0.2)' },
  directional:    { label: 'Directional',     icon: TrendingUp, color: '#f97316', bg: 'rgba(249,115,22,0.08)',  border: 'rgba(249,115,22,0.2)' },
  hedged:         { label: 'Hedged',          icon: Shield,     color: '#14b8a6', bg: 'rgba(20,184,166,0.08)',  border: 'rgba(20,184,166,0.2)' },
  intraday:       { label: 'Intraday',        icon: Zap,        color: '#22c55e', bg: 'rgba(34,197,94,0.08)',   border: 'rgba(34,197,94,0.2)' },
};

const SIGNAL_CONFIG = {
  STRONG_ENTRY: { icon: Rocket,        label: 'Strong Entry', color: '#22c55e', bg: 'rgba(34,197,94,0.1)',   border: 'rgba(34,197,94,0.25)' },
  ENTRY:        { icon: Crosshair,     label: 'Entry',        color: '#3b82f6', bg: 'rgba(59,130,246,0.1)',  border: 'rgba(59,130,246,0.25)' },
  WAIT:         { icon: Eye,           label: 'Wait',         color: '#eab308', bg: 'rgba(234,179,8,0.1)',   border: 'rgba(234,179,8,0.25)' },
  NEUTRAL:      { icon: Target,        label: 'Neutral',      color: '#64748b', bg: 'rgba(100,116,139,0.1)', border: 'rgba(100,116,139,0.25)' },
  AVOID:        { icon: Ban,           label: 'Avoid',        color: '#ef4444', bg: 'rgba(239,68,68,0.1)',   border: 'rgba(239,68,68,0.25)' },
  EXIT:         { icon: AlertTriangle, label: 'Exit',         color: '#f97316', bg: 'rgba(249,115,22,0.1)',  border: 'rgba(249,115,22,0.25)' },
};

const formatINR = (v) => `₹${Number(v).toLocaleString('en-IN')}`;

/* ════════════════════════════════════════════════════════════
   Main Component
   ════════════════════════════════════════════════════════════ */

export default function Strategies() {
  const { underlying } = useUnderlying();

  const { data: strategiesData } = useStrategies();
  const { data: paperStatus }    = usePaperTradingStatus();
  const isPaperActive            = paperStatus?.active === true;
  const { data: paperStats }     = usePaperTradingStats(isPaperActive);
  const { data: paperStrategies }= usePaperTradingStrategies(isPaperActive);
  const startPaper               = useStartPaperTrading();
  const stopPaper                = useStopPaperTrading();
  const deployStrategy           = useDeployPaperStrategy();
  const stopPaperStrategy        = useStopPaperStrategy();
  const { data: regimeData }     = useMarketRegime(underlying);
  const { data: signalsData }    = useStrategySignals(underlying);
  const { data: recsData }       = useAutoDeployRecommendations(underlying);
  const autoDeploy               = useExecuteAutoDeploy();

  const [activeCategory, setActiveCategory] = useState(null);
  const [searchQuery, setSearchQuery]       = useState('');
  const [expandedId, setExpandedId]         = useState(null);

  const regime = regimeData || {};
  const signalMap = {};
  (signalsData?.signals || []).forEach(s => { signalMap[s.strategy_id] = s; });
  const recommendations = recsData?.recommendations || [];
  const deployedPaperStrategies = paperStrategies?.strategies || [];

  const strategies = useMemo(() => {
    const raw = strategiesData?.strategies || [];
    if (!Array.isArray(raw)) return [];
    return raw.map(s => ({
      ...s,
      id: s.strategy_id || s.id,
    }));
  }, [strategiesData]);

  const grouped = useMemo(() => {
    const g = {};
    strategies.forEach(s => {
      const cat = s.category || 'other';
      if (!g[cat]) g[cat] = [];
      g[cat].push(s);
    });
    return g;
  }, [strategies]);

  const filtered = useMemo(() => {
    let list = strategies;
    if (activeCategory) list = list.filter(s => s.category === activeCategory);
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(s =>
        s.name?.toLowerCase().includes(q) ||
        s.description?.toLowerCase().includes(q) ||
        s.edge?.toLowerCase().includes(q)
      );
    }
    return list;
  }, [strategies, activeCategory, searchQuery]);

  const handleDeploy = (strategyClass, name) => {
    if (!isPaperActive) {
      startPaper.mutate(
        { initial_capital: 1000000, symbols: UNDERLYINGS },
        { onSuccess: () => deployStrategy.mutate({ strategy_class: strategyClass, name, underlying }) }
      );
    } else {
      deployStrategy.mutate({ strategy_class: strategyClass, name, underlying });
    }
  };

  const isDeployed = (strategyClass) =>
    deployedPaperStrategies.some(s =>
      s.strategy_class === strategyClass && s.status === 'RUNNING'
    );

  /* ════════════════════════════════════════════════════════════
     Render
     ════════════════════════════════════════════════════════════ */
  return (
    <div className="space-y-5 max-w-[1440px] mx-auto animate-fade-in">

      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">
            Strategy Engine
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {strategies.length} institutional strategies — deploy to paper with one click
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600 pointer-events-none" />
            <input type="text" value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search..." className="bg-slate-800/40 border border-white/5 rounded-xl pl-9 pr-3 py-2 text-sm text-slate-200 w-48 focus:outline-none focus:border-accent/50 placeholder:text-slate-600" />
          </div>
        </div>
      </div>

      {/* ── Market Regime + Paper Status Row ── */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-4">
        {/* Regime */}
        <div className="glass-card !p-4">
          <div className="flex items-center gap-4 flex-wrap">
            <Brain className="w-5 h-5 text-accent flex-shrink-0" />
            <div>
              <div className="text-sm font-bold text-white">{regime.regime_label || 'Loading...'}</div>
              <div className="text-[10px] text-slate-500">Market Regime</div>
            </div>
            <div className="flex items-center gap-2 ml-auto flex-wrap">
              <span className="text-xs px-2.5 py-1 rounded-lg bg-slate-800/60 text-slate-300 font-mono">
                VIX {regime.vix?.toFixed(1) || '--'}
              </span>
              <span className="text-xs px-2.5 py-1 rounded-lg bg-slate-800/60 text-slate-300 font-mono">
                {underlying} {regime.nifty_change_pct != null ? `${regime.nifty_change_pct >= 0 ? '+' : ''}${regime.nifty_change_pct?.toFixed(2)}%` : '--'}
              </span>
              {regime.is_expiry_day && (
                <span className="text-[10px] px-2.5 py-1 rounded-lg bg-yellow-500/15 text-yellow-400 font-bold animate-pulse">EXPIRY</span>
              )}
              {recommendations.filter(r => r.auto_deploy).length > 0 && (
                <button onClick={() => autoDeploy.mutate()} disabled={autoDeploy.isPending}
                  className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-accent/15 text-accent text-xs font-semibold hover:bg-accent/25 transition-colors">
                  <Rocket className="w-3.5 h-3.5" />
                  {autoDeploy.isPending ? 'Deploying...' : `Auto-Deploy ${recommendations.filter(r => r.auto_deploy).length}`}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Paper Engine Toggle */}
        <div className="glass-card !p-4 flex items-center gap-4 min-w-[280px]">
          <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${isPaperActive ? 'bg-profit animate-pulse' : 'bg-slate-600'}`} />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-bold text-white">Paper Engine</div>
            <div className="text-[10px] text-slate-500">
              {isPaperActive
                ? `P&L: ${paperStats?.total_pnl >= 0 ? '+' : ''}₹${Math.abs(paperStats?.total_pnl || 0).toLocaleString('en-IN')} | ${paperStats?.total_trades || 0} trades`
                : 'Click Start to begin'}
            </div>
          </div>
          {isPaperActive ? (
            <button onClick={() => stopPaper.mutate()} disabled={stopPaper.isPending}
              className="flex items-center gap-1.5 px-4 py-2 bg-loss/10 text-loss rounded-lg text-xs font-semibold hover:bg-loss/20 transition-colors">
              <PowerOff className="w-3.5 h-3.5" /> {stopPaper.isPending ? 'Stopping...' : 'Stop'}
            </button>
          ) : (
            <button onClick={() => startPaper.mutate({ initial_capital: 1000000, symbols: UNDERLYINGS })}
              disabled={startPaper.isPending}
              className="flex items-center gap-1.5 px-4 py-2 bg-accent/15 text-accent rounded-lg text-xs font-semibold hover:bg-accent/25 transition-colors">
              <Power className="w-3.5 h-3.5" /> {startPaper.isPending ? 'Starting...' : 'Start'}
            </button>
          )}
        </div>
      </div>

      {/* ── Deployed Strategies (if any) ── */}
      {deployedPaperStrategies.length > 0 && (
        <div className="glass-card !p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity className="w-4 h-4 text-accent" />
            <span className="text-sm font-bold text-white">Active Deployments</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-profit/15 text-profit font-semibold">
              {deployedPaperStrategies.filter(s => s.status === 'RUNNING').length} running
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {deployedPaperStrategies.map(s => (
              <div key={s.strategy_id} className="flex items-center justify-between bg-slate-800/30 rounded-xl p-3">
                <div className="flex items-center gap-2 min-w-0">
                  <div className={`w-2 h-2 rounded-full flex-shrink-0 ${s.status === 'RUNNING' ? 'bg-profit' : 'bg-slate-600'}`} />
                  <span className="text-sm font-medium text-white truncate">{s.name}</span>
                  <span className="text-[10px] text-slate-500 font-mono">{s.underlying || underlying}</span>
                </div>
                {s.status === 'RUNNING' && (
                  <button onClick={() => stopPaperStrategy.mutate(s.strategy_id)}
                    className="flex items-center gap-1 px-3 py-1.5 bg-loss/10 text-loss rounded-lg text-[11px] font-medium hover:bg-loss/20 transition-colors flex-shrink-0 ml-2">
                    <Square className="w-3 h-3" /> Stop
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Category Tabs ── */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
        <button onClick={() => setActiveCategory(null)}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-all whitespace-nowrap ${
            !activeCategory ? 'bg-accent/15 text-accent' : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
          }`}>
          <Grid3X3 className="w-3.5 h-3.5" /> All ({strategies.length})
        </button>
        {Object.entries(CATEGORY_META).map(([key, meta]) => {
          const count = (grouped[key] || []).length;
          if (count === 0) return null;
          const Icon = meta.icon;
          return (
            <button key={key} onClick={() => setActiveCategory(activeCategory === key ? null : key)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold transition-all whitespace-nowrap ${
                activeCategory === key ? 'text-white' : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]'
              }`}
              style={activeCategory === key ? { background: meta.bg, color: meta.color, border: `1px solid ${meta.border}` } : {}}>
              <Icon className="w-3.5 h-3.5" /> {meta.label} ({count})
            </button>
          );
        })}
      </div>

      {/* ── Strategy Cards ── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {filtered.map((strat) => {
          const signal = signalMap[strat.id];
          const sigCfg = signal ? (SIGNAL_CONFIG[signal.signal] || SIGNAL_CONFIG.NEUTRAL) : null;
          const catMeta = CATEGORY_META[strat.category] || CATEGORY_META.intraday;
          const deployed = isDeployed(strat.strategy_class || strat.id);
          const isExpanded = expandedId === strat.id;
          const winRate = strat.win_rate || 50;
          const winColor = winRate >= 70 ? '#22c55e' : winRate >= 55 ? '#eab308' : '#ef4444';
          const CatIcon = catMeta.icon;

          return (
            <div key={strat.id} className="glass-card overflow-hidden">
              <div className="p-5">
                {/* Row 1: Category + Name + Signal */}
                <div className="flex items-start gap-3">
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                    style={{ background: catMeta.bg, border: `1px solid ${catMeta.border}` }}>
                    <CatIcon className="w-5 h-5" style={{ color: catMeta.color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="text-sm font-bold text-white">{strat.name}</h3>
                      {deployed && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-profit/15 text-profit font-bold">DEPLOYED</span>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-500 mt-0.5 line-clamp-2">{strat.edge || strat.description}</p>
                  </div>
                  {sigCfg && signal && (
                    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold flex-shrink-0"
                      style={{ background: sigCfg.bg, border: `1px solid ${sigCfg.border}`, color: sigCfg.color }}>
                      <sigCfg.icon className="w-3 h-3" /> {sigCfg.label}
                      <span className="opacity-60 ml-0.5">{signal.confidence}%</span>
                    </div>
                  )}
                </div>

                {/* Row 2: Stats Bar */}
                <div className="flex items-center gap-3 mt-4 flex-wrap">
                  {/* Win Rate */}
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg relative">
                      <svg width={32} height={32} style={{ transform: 'rotate(-90deg)' }}>
                        <circle cx={16} cy={16} r={12} fill="none" stroke="rgba(51,65,85,0.4)" strokeWidth={3} />
                        <circle cx={16} cy={16} r={12} fill="none" stroke={winColor} strokeWidth={3}
                          strokeDasharray={`${(winRate / 100) * 75.4} ${75.4 - (winRate / 100) * 75.4}`} strokeLinecap="round" />
                      </svg>
                    </div>
                    <div>
                      <div className="text-xs font-bold font-mono" style={{ color: winColor }}>{winRate}%</div>
                      <div className="text-[9px] text-slate-600">Win Rate</div>
                    </div>
                  </div>

                  <div className="w-px h-8 bg-white/5" />

                  {/* Avg Return */}
                  <div>
                    <div className="text-xs font-bold font-mono text-profit">+{strat.avg_return_pct || 0}%</div>
                    <div className="text-[9px] text-slate-600">Avg/Month</div>
                  </div>

                  <div className="w-px h-8 bg-white/5" />

                  {/* Max Loss */}
                  <div>
                    <div className="text-xs font-bold font-mono text-loss">-{strat.max_loss_pct || 0}%</div>
                    <div className="text-[9px] text-slate-600">Max Loss</div>
                  </div>

                  <div className="w-px h-8 bg-white/5" />

                  {/* Capital */}
                  <div>
                    <div className="text-xs font-bold font-mono text-slate-300">{formatINR(strat.capital_req || 100000)}</div>
                    <div className="text-[9px] text-slate-600">Capital Req</div>
                  </div>

                  <div className="flex-1" />

                  {/* Deploy Button */}
                  {deployed ? (
                    <span className="text-[11px] px-3 py-1.5 rounded-lg bg-profit/10 text-profit font-semibold border border-profit/20">
                      Running
                    </span>
                  ) : (
                    <button onClick={() => handleDeploy(strat.strategy_class || strat.id, strat.name)}
                      disabled={deployStrategy.isPending}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-[11px] font-semibold transition-all
                        bg-accent/15 text-accent hover:bg-accent/25 border border-accent/20 active:scale-95">
                      <Play className="w-3 h-3" />
                      {deployStrategy.isPending ? 'Deploying...' : 'Deploy'}
                    </button>
                  )}
                </div>

                {/* Row 3: Regime Fit Tags */}
                <div className="flex items-center gap-1.5 mt-3 flex-wrap">
                  <span className="text-[9px] text-slate-600 mr-1">Ideal:</span>
                  {(strat.ideal_regime || []).map(r => (
                    <span key={r} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800/50 text-slate-400 font-mono">
                      {r.replace(/_/g, ' ')}
                    </span>
                  ))}
                  {strat.schedule_window?.length === 2 && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800/50 text-slate-400 font-mono flex items-center gap-1">
                      <Timer className="w-2.5 h-2.5" /> {strat.schedule_window[0]}–{strat.schedule_window[1]}
                    </span>
                  )}
                </div>

                {/* Expand Toggle */}
                <button onClick={() => setExpandedId(isExpanded ? null : strat.id)}
                  className="flex items-center gap-1 mt-3 text-[11px] text-slate-600 hover:text-accent transition-colors">
                  {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  {isExpanded ? 'Less' : 'Details'}
                </button>
              </div>

              {/* Expanded Details */}
              {isExpanded && (
                <div className="px-5 pb-5 space-y-4 border-t border-white/5 pt-4 animate-fade-in">
                  {/* Description */}
                  <p className="text-xs text-slate-400 leading-relaxed">{strat.description}</p>

                  {/* Legs */}
                  {strat.default_legs?.length > 0 && (
                    <div>
                      <div className="text-[10px] text-slate-600 font-semibold uppercase tracking-wider mb-2">Option Legs</div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        {strat.default_legs.map((leg, i) => (
                          <div key={i} className={`rounded-lg p-2.5 text-center text-xs font-mono ${
                            leg.action === 'SELL'
                              ? 'bg-red-500/5 border border-red-500/15 text-red-400'
                              : 'bg-emerald-500/5 border border-emerald-500/15 text-emerald-400'
                          }`}>
                            <div className="font-bold">{leg.action} {leg.type}</div>
                            <div className="text-[10px] text-slate-500 mt-0.5">
                              {leg.offset === 0 ? 'ATM' : `${leg.offset > 0 ? '+' : ''}${leg.offset}pts`}
                              {leg.lots > 1 && ` x${leg.lots}`}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Entry Conditions */}
                  {strat.entry_conditions?.length > 0 && (
                    <div>
                      <div className="text-[10px] text-slate-600 font-semibold uppercase tracking-wider mb-2">Entry Conditions</div>
                      <div className="flex flex-wrap gap-1.5">
                        {strat.entry_conditions.map((c, i) => (
                          <span key={i} className="text-[10px] px-2 py-1 rounded-lg bg-slate-800/40 text-slate-300 font-mono border border-white/5">
                            {c.indicator}{c.period ? `(${c.period})` : ''} {c.operator} {String(c.value)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Risk Params */}
                  {strat.risk_params && Object.keys(strat.risk_params).length > 0 && (
                    <div>
                      <div className="text-[10px] text-slate-600 font-semibold uppercase tracking-wider mb-2">Risk Management</div>
                      <div className="grid grid-cols-3 gap-2">
                        {Object.entries(strat.risk_params).map(([k, v]) => (
                          <div key={k} className="bg-slate-800/25 rounded-lg p-2.5 text-center">
                            <div className="text-[9px] text-slate-600 mb-0.5">{k.replace(/([A-Z])/g, ' $1').replace(/^./, s => s.toUpperCase())}</div>
                            <div className="text-xs font-bold font-mono text-slate-300">
                              {typeof v === 'number' && v > 100 ? formatINR(v) : `${v}${k.includes('Pct') || k.includes('pct') ? '%' : ''}`}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* AI Signal (if available) */}
                  {sigCfg && signal && (
                    <div className="rounded-xl p-3.5" style={{ background: sigCfg.bg, border: `1px solid ${sigCfg.border}` }}>
                      <div className="flex items-center gap-2 mb-2">
                        <sigCfg.icon className="w-4 h-4" style={{ color: sigCfg.color }} />
                        <span className="text-xs font-bold" style={{ color: sigCfg.color }}>
                          {sigCfg.label} — {signal.confidence}% confidence
                        </span>
                      </div>
                      {signal.action && <p className="text-[11px] text-slate-400">{signal.action}</p>}
                      {signal.triggers?.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {signal.triggers.map((t, i) => (
                            <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-black/20 text-slate-400 font-mono">{t}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Empty State */}
      {filtered.length === 0 && (
        <div className="glass-card text-center py-16">
          <Target className="w-10 h-10 mx-auto mb-3 text-slate-600" />
          <p className="text-sm text-slate-500">
            {searchQuery ? 'No strategies match your search.' : 'No strategies available.'}
          </p>
          {searchQuery && (
            <button onClick={() => setSearchQuery('')}
              className="mt-3 text-sm text-accent hover:text-accent-light transition-colors font-medium">
              Clear search
            </button>
          )}
        </div>
      )}

      {/* Disclaimer */}
      <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl text-[11px] text-slate-500"
        style={{ background: 'rgba(245,158,11,0.04)', border: '1px solid rgba(245,158,11,0.1)' }}>
        <AlertTriangle className="w-3.5 h-3.5 text-yellow-600 mt-0.5 flex-shrink-0" />
        Win rates and returns are based on theoretical models, not live trading. Always paper-test before deploying real capital.
      </div>
    </div>
  );
}

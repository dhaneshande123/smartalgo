import React, { useState, useMemo, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts';
import {
  Plus, Trash2, Copy, Save, Play, RotateCcw, ChevronDown,
  TrendingUp, TrendingDown, Shield, Zap, AlertTriangle,
  Settings2, Layers, Target, Calculator, DollarSign,
} from 'lucide-react';
import { useIndices } from '../hooks/useApi';
import { useToast } from '../components/common/ToastProvider';

/* ════════════════════════════════════════════════════════════
   Constants
   ════════════════════════════════════════════════════════════ */

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];

const SPOT_PRICES = { NIFTY: 24050, BANKNIFTY: 51200, FINNIFTY: 23100, MIDCPNIFTY: 12400 };

const LOT_SIZES = { NIFTY: 25, BANKNIFTY: 15, FINNIFTY: 25, MIDCPNIFTY: 50 };

const STRATEGY_TEMPLATES = [
  { name: 'Iron Condor', legs: [
    { type: 'CE', action: 'SELL', offset: 200, premium: 85 },
    { type: 'CE', action: 'BUY', offset: 400, premium: 35 },
    { type: 'PE', action: 'SELL', offset: -200, premium: 80 },
    { type: 'PE', action: 'BUY', offset: -400, premium: 30 },
  ]},
  { name: 'Short Straddle', legs: [
    { type: 'CE', action: 'SELL', offset: 0, premium: 180 },
    { type: 'PE', action: 'SELL', offset: 0, premium: 175 },
  ]},
  { name: 'Short Strangle', legs: [
    { type: 'CE', action: 'SELL', offset: 300, premium: 65 },
    { type: 'PE', action: 'SELL', offset: -300, premium: 60 },
  ]},
  { name: 'Bull Call Spread', legs: [
    { type: 'CE', action: 'BUY', offset: 0, premium: 180 },
    { type: 'CE', action: 'SELL', offset: 200, premium: 85 },
  ]},
  { name: 'Bear Put Spread', legs: [
    { type: 'PE', action: 'BUY', offset: 0, premium: 175 },
    { type: 'PE', action: 'SELL', offset: -200, premium: 80 },
  ]},
  { name: 'Long Butterfly', legs: [
    { type: 'CE', action: 'BUY', offset: -200, premium: 280 },
    { type: 'CE', action: 'SELL', offset: 0, premium: 180, lots: 2 },
    { type: 'CE', action: 'BUY', offset: 200, premium: 85 },
  ]},
  { name: 'Jade Lizard', legs: [
    { type: 'PE', action: 'SELL', offset: -200, premium: 80 },
    { type: 'CE', action: 'SELL', offset: 200, premium: 85 },
    { type: 'CE', action: 'BUY', offset: 400, premium: 35 },
  ]},
  { name: 'Ratio Backspread', legs: [
    { type: 'CE', action: 'SELL', offset: 0, premium: 180 },
    { type: 'CE', action: 'BUY', offset: 200, premium: 85, lots: 2 },
  ]},
];

const RISK_PARAMS = {
  maxLossPerTrade: { label: 'Max Loss / Trade', unit: '₹', default: 10000, min: 1000, max: 500000, step: 1000 },
  maxLossPerDay: { label: 'Max Loss / Day', unit: '₹', default: 25000, min: 5000, max: 1000000, step: 5000 },
  maxPositions: { label: 'Max Open Positions', unit: '', default: 4, min: 1, max: 20, step: 1 },
  trailingStopPct: { label: 'Trailing Stop', unit: '%', default: 30, min: 5, max: 80, step: 5 },
  targetPct: { label: 'Target Profit', unit: '%', default: 50, min: 10, max: 100, step: 5 },
  reEntryCount: { label: 'Max Re-entries', unit: '', default: 1, min: 0, max: 5, step: 1 },
};

const SCHEDULE_OPTIONS = [
  { value: 'market_open', label: '09:20 — Market Open' },
  { value: 'mid_morning', label: '10:30 — Mid Morning' },
  { value: 'afternoon', label: '13:00 — Afternoon' },
  { value: 'custom', label: 'Custom Time' },
];

/* ════════════════════════════════════════════════════════════
   Payoff Calculator
   ════════════════════════════════════════════════════════════ */

function computePayoff(legs, underlying, spotPrice, lotSize) {
  const strikes = legs.map(l => spotPrice + (l.offset || 0));
  const minStrike = Math.min(...strikes);
  const maxStrike = Math.max(...strikes);
  const range = Math.max(maxStrike - minStrike, 500);
  const step = underlying === 'BANKNIFTY' ? 25 : 10;

  const points = [];
  for (let s = spotPrice - range * 1.5; s <= spotPrice + range * 1.5; s += step) {
    let pnl = 0;
    legs.forEach(leg => {
      const strike = spotPrice + (leg.offset || 0);
      const lots = leg.lots || 1;
      const qty = lots * lotSize;
      const premium = leg.premium || 0;
      const sign = leg.action === 'BUY' ? 1 : -1;

      let intrinsic = 0;
      if (leg.type === 'CE') intrinsic = Math.max(s - strike, 0);
      else intrinsic = Math.max(strike - s, 0);

      pnl += sign * (intrinsic - premium) * qty;
    });
    points.push({ spot: Math.round(s), pnl: Math.round(pnl) });
  }
  return points;
}

function computeStats(payoffData, legs, lotSize) {
  const pnls = payoffData.map(d => d.pnl);
  const maxProfit = Math.max(...pnls);
  const maxLoss = Math.min(...pnls);

  // Find breakeven points (where PnL crosses zero)
  const breakevens = [];
  for (let i = 1; i < payoffData.length; i++) {
    if ((payoffData[i - 1].pnl <= 0 && payoffData[i].pnl >= 0) ||
        (payoffData[i - 1].pnl >= 0 && payoffData[i].pnl <= 0)) {
      breakevens.push(payoffData[i].spot);
    }
  }

  // Net premium
  let netPremium = 0;
  legs.forEach(leg => {
    const sign = leg.action === 'SELL' ? 1 : -1;
    const qty = (leg.lots || 1) * lotSize;
    netPremium += sign * (leg.premium || 0) * qty;
  });

  // Total margin estimate (rough)
  const totalLots = legs.reduce((s, l) => s + (l.lots || 1), 0);
  const marginEst = totalLots * lotSize * 50; // rough estimate

  return { maxProfit, maxLoss, breakevens, netPremium, marginEst };
}

/* ════════════════════════════════════════════════════════════
   Payoff Tooltip
   ════════════════════════════════════════════════════════════ */

function PayoffTooltip({ active, payload }) {
  if (!active || !payload?.[0]?.payload) return null;
  const { spot, pnl } = payload[0].payload;
  return (
    <div className="bg-terminal-card/95 backdrop-blur border border-terminal-border rounded-lg px-3 py-2 shadow-xl text-xs">
      <div className="text-slate-400">Spot: <span className="font-mono text-white">{spot?.toLocaleString('en-IN')}</span></div>
      <div className={`font-mono font-semibold ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
        P&L: {pnl >= 0 ? '+' : ''}₹{Math.abs(pnl).toLocaleString('en-IN')}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   Main Component
   ════════════════════════════════════════════════════════════ */

export default function StrategyBuilder() {
  const toast = useToast();
  const { data: indicesData } = useIndices();

  // Core state
  const [underlying, setUnderlying] = useState('NIFTY');
  const [strategyName, setStrategyName] = useState('Custom Strategy');
  const [legs, setLegs] = useState([
    { id: 1, type: 'CE', action: 'SELL', offset: 200, premium: 85, lots: 1 },
    { id: 2, type: 'CE', action: 'BUY', offset: 400, premium: 35, lots: 1 },
    { id: 3, type: 'PE', action: 'SELL', offset: -200, premium: 80, lots: 1 },
    { id: 4, type: 'PE', action: 'BUY', offset: -400, premium: 30, lots: 1 },
  ]);
  const [nextId, setNextId] = useState(5);
  const [riskParams, setRiskParams] = useState(
    Object.fromEntries(Object.entries(RISK_PARAMS).map(([k, v]) => [k, v.default]))
  );
  const [schedule, setSchedule] = useState('market_open');
  const [customTime, setCustomTime] = useState('09:20');
  const [activeTab, setActiveTab] = useState('legs'); // 'legs' | 'risk' | 'schedule'

  // Live spot price from API
  const spotPrice = useMemo(() => {
    const rawIndices = indicesData?.indices || indicesData;
    if (!Array.isArray(rawIndices)) return SPOT_PRICES[underlying];
    const match = rawIndices.find(i => (i.symbol || '').toUpperCase().includes(underlying));
    return match?.ltp || match?.price || SPOT_PRICES[underlying];
  }, [indicesData, underlying]);

  const lotSize = LOT_SIZES[underlying];

  // Payoff calculation
  const payoffData = useMemo(() => computePayoff(legs, underlying, spotPrice, lotSize), [legs, underlying, spotPrice, lotSize]);
  const stats = useMemo(() => computeStats(payoffData, legs, lotSize), [payoffData, legs, lotSize]);

  // Leg management
  const addLeg = useCallback(() => {
    setLegs(prev => [...prev, { id: nextId, type: 'CE', action: 'BUY', offset: 0, premium: 100, lots: 1 }]);
    setNextId(n => n + 1);
  }, [nextId]);

  const removeLeg = useCallback((id) => {
    setLegs(prev => prev.filter(l => l.id !== id));
  }, []);

  const updateLeg = useCallback((id, field, value) => {
    setLegs(prev => prev.map(l => l.id === id ? { ...l, [field]: value } : l));
  }, []);

  const loadTemplate = useCallback((template) => {
    setStrategyName(template.name);
    setLegs(template.legs.map((l, i) => ({ id: i + 1, ...l, lots: l.lots || 1 })));
    setNextId(template.legs.length + 1);
    toast?.addToast?.(`Loaded ${template.name} template`, 'info');
  }, [toast]);

  const resetLegs = useCallback(() => {
    setLegs([]);
    setNextId(1);
    setStrategyName('Custom Strategy');
  }, []);

  const handleDeploy = useCallback(() => {
    toast?.addToast?.(`Strategy "${strategyName}" queued for deployment on ${underlying}`, 'success');
  }, [toast, strategyName, underlying]);

  const handleSave = useCallback(() => {
    toast?.addToast?.(`Strategy "${strategyName}" saved to library`, 'info');
  }, [toast, strategyName]);

  // Payoff chart domain
  const pnlDomain = useMemo(() => {
    if (!payoffData.length) return [-1000, 1000];
    const pnls = payoffData.map(d => d.pnl);
    const min = Math.min(...pnls);
    const max = Math.max(...pnls);
    const pad = Math.max(Math.abs(max - min) * 0.15, 500);
    return [min - pad, max + pad];
  }, [payoffData]);

  return (
    <div className="space-y-3 animate-fade-in">
      {/* ── Header ── */}
      <div className="glass-card !p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent to-accent-light flex items-center justify-center">
              <Layers className="w-4 h-4 text-white" />
            </div>
            <div>
              <input
                value={strategyName}
                onChange={e => setStrategyName(e.target.value)}
                className="bg-transparent text-white text-sm font-bold border-b border-transparent hover:border-slate-600 focus:border-accent focus:outline-none px-0 py-0.5 w-48"
              />
              <div className="text-[10px] text-slate-500">STRATEGY BUILDER</div>
            </div>
          </div>

          <select
            value={underlying}
            onChange={e => setUnderlying(e.target.value)}
            className="bg-terminal-bg border border-terminal-border rounded-lg px-3 py-1.5 text-sm text-white focus:border-accent focus:outline-none"
          >
            {UNDERLYINGS.map(u => <option key={u} value={u}>{u}</option>)}
          </select>

          <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.03] border border-terminal-border">
            <Target className="w-3.5 h-3.5 text-slate-500" />
            <span className="text-[10px] text-slate-500">SPOT</span>
            <span className="text-sm font-mono font-bold text-white">{spotPrice.toLocaleString('en-IN')}</span>
          </div>

          <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.03] border border-terminal-border">
            <Calculator className="w-3.5 h-3.5 text-slate-500" />
            <span className="text-[10px] text-slate-500">LOT</span>
            <span className="text-sm font-mono font-semibold text-white">{lotSize}</span>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button onClick={handleSave} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-300 bg-terminal-bg border border-terminal-border rounded-lg hover:border-accent/50 transition-colors">
              <Save className="w-3.5 h-3.5" /> Save
            </button>
            <button onClick={handleDeploy} className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-semibold text-white bg-gradient-to-r from-accent to-accent-light rounded-lg hover:shadow-lg hover:shadow-accent/20 transition-all">
              <Play className="w-3.5 h-3.5" /> Deploy
            </button>
          </div>
        </div>
      </div>

      {/* ── Templates ── */}
      <div className="glass-card !p-3">
        <div className="flex items-center gap-2 mb-2">
          <Zap className="w-3.5 h-3.5 text-accent" />
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Quick Templates</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {STRATEGY_TEMPLATES.map(t => (
            <button
              key={t.name}
              onClick={() => loadTemplate(t)}
              className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-terminal-bg border border-terminal-border rounded-lg hover:border-accent/50 hover:text-white transition-all"
            >
              {t.name}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
        {/* ── Left: Leg Builder + Config ── */}
        <div className="lg:col-span-3 space-y-3">
          {/* Tabs */}
          <div className="flex gap-1 bg-terminal-card rounded-lg p-0.5 border border-terminal-border">
            {[
              { key: 'legs', label: 'Option Legs', icon: Layers },
              { key: 'risk', label: 'Risk Params', icon: Shield },
              { key: 'schedule', label: 'Schedule', icon: Settings2 },
            ].map(tab => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-md flex-1 justify-center transition-all ${
                  activeTab === tab.key
                    ? 'bg-accent/15 text-accent'
                    : 'text-slate-400 hover:text-white hover:bg-white/[0.04]'
                }`}
              >
                <tab.icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Legs Tab */}
          {activeTab === 'legs' && (
            <div className="glass-card !p-3 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  {legs.length} Leg{legs.length !== 1 ? 's' : ''} Configured
                </span>
                <div className="flex gap-2">
                  <button onClick={resetLegs} className="flex items-center gap-1 px-2 py-1 text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
                    <RotateCcw className="w-3 h-3" /> Reset
                  </button>
                  <button onClick={addLeg} className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-semibold text-accent bg-accent/10 rounded-md hover:bg-accent/20 transition-colors">
                    <Plus className="w-3 h-3" /> Add Leg
                  </button>
                </div>
              </div>

              {legs.length === 0 ? (
                <div className="text-center py-8 text-slate-500 text-sm">
                  No legs configured. Add a leg or select a template above.
                </div>
              ) : (
                <div className="space-y-2">
                  {legs.map((leg, idx) => (
                    <div key={leg.id} className="flex flex-wrap items-center gap-2 p-2.5 rounded-xl bg-white/[0.02] border border-terminal-border hover:border-slate-600 transition-colors">
                      <span className="w-6 h-6 rounded-full bg-slate-700/50 flex items-center justify-center text-[10px] font-bold text-slate-400">
                        {idx + 1}
                      </span>

                      {/* Action: BUY/SELL */}
                      <select
                        value={leg.action}
                        onChange={e => updateLeg(leg.id, 'action', e.target.value)}
                        className={`w-20 px-2 py-1.5 text-xs font-bold rounded-lg border ${
                          leg.action === 'BUY'
                            ? 'bg-profit/10 border-profit/30 text-profit'
                            : 'bg-loss/10 border-loss/30 text-loss'
                        }`}
                      >
                        <option value="BUY">BUY</option>
                        <option value="SELL">SELL</option>
                      </select>

                      {/* Type: CE/PE */}
                      <select
                        value={leg.type}
                        onChange={e => updateLeg(leg.id, 'type', e.target.value)}
                        className="w-16 px-2 py-1.5 text-xs font-semibold bg-terminal-bg border border-terminal-border rounded-lg text-white"
                      >
                        <option value="CE">CE</option>
                        <option value="PE">PE</option>
                      </select>

                      {/* Strike Offset */}
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] text-slate-500">Strike</span>
                        <input
                          type="number"
                          value={spotPrice + leg.offset}
                          onChange={e => updateLeg(leg.id, 'offset', Number(e.target.value) - spotPrice)}
                          className="w-24 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none"
                          step={underlying === 'BANKNIFTY' ? 100 : 50}
                        />
                      </div>

                      {/* Premium */}
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] text-slate-500">Prem</span>
                        <input
                          type="number"
                          value={leg.premium}
                          onChange={e => updateLeg(leg.id, 'premium', Number(e.target.value))}
                          className="w-20 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none"
                          step={0.5}
                          min={0}
                        />
                      </div>

                      {/* Lots */}
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] text-slate-500">Lots</span>
                        <input
                          type="number"
                          value={leg.lots}
                          onChange={e => updateLeg(leg.id, 'lots', Math.max(1, Number(e.target.value)))}
                          className="w-14 px-2 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none"
                          min={1}
                          max={20}
                        />
                      </div>

                      {/* Qty display */}
                      <span className="text-[10px] text-slate-500 font-mono">
                        = {(leg.lots || 1) * lotSize} qty
                      </span>

                      {/* Remove */}
                      <button
                        onClick={() => removeLeg(leg.id)}
                        className="ml-auto p-1.5 rounded-lg text-slate-600 hover:text-loss hover:bg-loss/10 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Risk Params Tab */}
          {activeTab === 'risk' && (
            <div className="glass-card !p-3 space-y-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Risk Management</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {Object.entries(RISK_PARAMS).map(([key, cfg]) => (
                  <div key={key} className="p-3 rounded-xl bg-white/[0.02] border border-terminal-border">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs text-slate-400">{cfg.label}</span>
                      <span className="text-xs font-mono font-semibold text-white">
                        {cfg.unit === '₹' ? '₹' : ''}{riskParams[key]?.toLocaleString('en-IN')}{cfg.unit === '%' ? '%' : ''}
                      </span>
                    </div>
                    <input
                      type="range"
                      value={riskParams[key]}
                      onChange={e => setRiskParams(p => ({ ...p, [key]: Number(e.target.value) }))}
                      min={cfg.min}
                      max={cfg.max}
                      step={cfg.step}
                      className="w-full h-1.5 rounded-full appearance-none bg-terminal-border cursor-pointer accent-accent"
                    />
                    <div className="flex justify-between text-[9px] text-slate-600 mt-1">
                      <span>{cfg.unit === '₹' ? '₹' : ''}{cfg.min.toLocaleString()}{cfg.unit === '%' ? '%' : ''}</span>
                      <span>{cfg.unit === '₹' ? '₹' : ''}{cfg.max.toLocaleString()}{cfg.unit === '%' ? '%' : ''}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Schedule Tab */}
          {activeTab === 'schedule' && (
            <div className="glass-card !p-3 space-y-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Entry Schedule</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {SCHEDULE_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => setSchedule(opt.value)}
                    className={`flex items-center gap-2 p-3 rounded-xl border text-left transition-all ${
                      schedule === opt.value
                        ? 'border-accent/50 bg-accent/10 text-white'
                        : 'border-terminal-border bg-white/[0.02] text-slate-400 hover:border-slate-600'
                    }`}
                  >
                    <div className={`w-3 h-3 rounded-full border-2 ${
                      schedule === opt.value ? 'border-accent bg-accent' : 'border-slate-600'
                    }`} />
                    <span className="text-xs font-medium">{opt.label}</span>
                  </button>
                ))}
              </div>
              {schedule === 'custom' && (
                <div className="flex items-center gap-2 mt-2">
                  <span className="text-xs text-slate-400">Entry Time (IST):</span>
                  <input
                    type="time"
                    value={customTime}
                    onChange={e => setCustomTime(e.target.value)}
                    className="px-3 py-1.5 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none"
                  />
                </div>
              )}

              <div className="mt-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Exit Rules</span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                  <div className="flex items-center gap-2 p-3 rounded-xl border border-terminal-border bg-white/[0.02]">
                    <span className="text-[10px] text-slate-500 w-20">Square-off</span>
                    <span className="text-xs font-mono text-white">15:15 IST</span>
                  </div>
                  <div className="flex items-center gap-2 p-3 rounded-xl border border-terminal-border bg-white/[0.02]">
                    <span className="text-[10px] text-slate-500 w-20">Expiry Day</span>
                    <span className="text-xs font-mono text-white">Auto-exit at 15:00</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* ── Right: Payoff Diagram + Stats ── */}
        <div className="lg:col-span-2 space-y-3">
          {/* Strategy Stats */}
          <div className="glass-card !p-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Strategy P&L Profile</span>
            <div className="grid grid-cols-2 gap-2 mt-2">
              <div className="p-2.5 rounded-lg bg-white/[0.03] border border-terminal-border">
                <div className="text-[10px] text-slate-500 uppercase">Max Profit</div>
                <div className={`text-sm font-mono font-bold ${stats.maxProfit >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {stats.maxProfit > 9999999 ? 'Unlimited' : `₹${stats.maxProfit.toLocaleString('en-IN')}`}
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-white/[0.03] border border-terminal-border">
                <div className="text-[10px] text-slate-500 uppercase">Max Loss</div>
                <div className="text-sm font-mono font-bold text-loss">
                  {stats.maxLoss < -9999999 ? 'Unlimited' : `₹${Math.abs(stats.maxLoss).toLocaleString('en-IN')}`}
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-white/[0.03] border border-terminal-border">
                <div className="text-[10px] text-slate-500 uppercase">Net Premium</div>
                <div className={`text-sm font-mono font-bold ${stats.netPremium >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {stats.netPremium >= 0 ? '+' : '-'}₹{Math.abs(stats.netPremium).toLocaleString('en-IN')}
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-white/[0.03] border border-terminal-border">
                <div className="text-[10px] text-slate-500 uppercase">Breakeven{stats.breakevens.length > 1 ? 's' : ''}</div>
                <div className="text-sm font-mono font-bold text-blue-400">
                  {stats.breakevens.length > 0
                    ? stats.breakevens.map(b => b.toLocaleString('en-IN')).join(', ')
                    : '—'}
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-white/[0.03] border border-terminal-border">
                <div className="text-[10px] text-slate-500 uppercase">Risk/Reward</div>
                <div className="text-sm font-mono font-bold text-accent">
                  {stats.maxLoss !== 0 ? `1:${Math.abs(stats.maxProfit / stats.maxLoss).toFixed(1)}` : '—'}
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-white/[0.03] border border-terminal-border">
                <div className="text-[10px] text-slate-500 uppercase">Margin Est.</div>
                <div className="text-sm font-mono font-bold text-white">
                  ₹{stats.marginEst.toLocaleString('en-IN')}
                </div>
              </div>
            </div>
          </div>

          {/* Payoff Chart */}
          <div className="glass-card !p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Payoff at Expiry</span>
              <span className="text-[10px] text-slate-600">{legs.length} legs</span>
            </div>
            <div style={{ height: 260 }}>
              {legs.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={payoffData} margin={{ top: 10, right: 10, bottom: 0, left: 5 }}>
                    <defs>
                      <linearGradient id="payoffProfit" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#22c55e" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="payoffLoss" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#ef4444" stopOpacity={0} />
                        <stop offset="100%" stopColor="#ef4444" stopOpacity={0.3} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" vertical={false} />
                    <XAxis
                      dataKey="spot"
                      tick={{ fontSize: 9, fill: '#475569' }}
                      axisLine={{ stroke: '#1e2433' }}
                      tickLine={false}
                      tickFormatter={v => v.toLocaleString('en-IN')}
                      minTickGap={40}
                    />
                    <YAxis
                      domain={pnlDomain}
                      tick={{ fontSize: 9, fill: '#475569' }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={v => `${v >= 0 ? '+' : ''}${(v / 1000).toFixed(0)}K`}
                      width={42}
                    />
                    <Tooltip content={<PayoffTooltip />} />
                    <ReferenceLine y={0} stroke="#475569" strokeWidth={1} />
                    <ReferenceLine x={spotPrice} stroke="#7c3aed" strokeDasharray="4 4" strokeWidth={1.5} label={{ value: 'SPOT', position: 'top', fill: '#7c3aed', fontSize: 9 }} />
                    {stats.breakevens.map((be, i) => (
                      <ReferenceLine key={i} x={be} stroke="#3b82f6" strokeDasharray="3 3" strokeWidth={1} />
                    ))}
                    <Area
                      type="monotone"
                      dataKey="pnl"
                      stroke="#7c3aed"
                      strokeWidth={2}
                      fill="url(#payoffProfit)"
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-full text-slate-600 text-sm">
                  Add legs to see payoff diagram
                </div>
              )}
            </div>
          </div>

          {/* Leg Summary */}
          {legs.length > 0 && (
            <div className="glass-card !p-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2 block">Position Summary</span>
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className="text-left py-1 text-slate-500 font-medium">Leg</th>
                    <th className="text-center py-1 text-slate-500 font-medium">Action</th>
                    <th className="text-right py-1 text-slate-500 font-medium">Strike</th>
                    <th className="text-right py-1 text-slate-500 font-medium">Premium</th>
                    <th className="text-right py-1 text-slate-500 font-medium">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {legs.map((leg, i) => {
                    const strike = spotPrice + leg.offset;
                    const qty = (leg.lots || 1) * lotSize;
                    const val = leg.premium * qty * (leg.action === 'SELL' ? 1 : -1);
                    return (
                      <tr key={leg.id} className="border-t border-terminal-border/50">
                        <td className="py-1.5 font-mono text-white">{underlying} {strike} {leg.type}</td>
                        <td className={`py-1.5 text-center font-bold ${leg.action === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                          {leg.action}
                        </td>
                        <td className="py-1.5 text-right font-mono text-slate-300">{strike.toLocaleString('en-IN')}</td>
                        <td className="py-1.5 text-right font-mono text-slate-300">₹{leg.premium}</td>
                        <td className={`py-1.5 text-right font-mono font-semibold ${val >= 0 ? 'text-profit' : 'text-loss'}`}>
                          {val >= 0 ? '+' : '-'}₹{Math.abs(val).toLocaleString('en-IN')}
                        </td>
                      </tr>
                    );
                  })}
                  <tr className="border-t-2 border-terminal-border">
                    <td colSpan={4} className="py-1.5 text-right text-slate-400 font-semibold">NET</td>
                    <td className={`py-1.5 text-right font-mono font-bold ${stats.netPremium >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {stats.netPremium >= 0 ? '+' : '-'}₹{Math.abs(stats.netPremium).toLocaleString('en-IN')}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

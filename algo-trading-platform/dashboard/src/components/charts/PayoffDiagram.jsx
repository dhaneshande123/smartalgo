import React, { useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';

const PAYOFF_GENERATORS = {
  iron_condor: (center) => {
    const shortPut = center - 200;
    const longPut = center - 400;
    const shortCall = center + 200;
    const longCall = center + 400;
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      let payoff;
      if (spot <= longPut) payoff = -15;
      else if (spot <= shortPut) payoff = -15 + ((spot - longPut) / (shortPut - longPut)) * 55;
      else if (spot <= shortCall) payoff = 40;
      else if (spot <= longCall) payoff = 40 - ((spot - shortCall) / (longCall - shortCall)) * 55;
      else payoff = -15;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  straddle: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const dist = Math.abs(spot - center);
      const payoff = 45 - dist * 0.06;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  gamma_scalping: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const dist = Math.abs(spot - center);
      const payoff = -25 + dist * 0.055;
      return { spot, payoff: Math.round(Math.max(payoff, -25) * 100) / 100 };
    });
  },

  expiry_day: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const dist = Math.abs(spot - center);
      if (dist < 600) return { spot, payoff: 35 };
      const payoff = 35 - (dist - 600) * 0.12;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  mean_reversion: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const norm = (spot - center) / 1000;
      const payoff = 30 * Math.exp(-norm * norm * 2) - 10;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  jade_lizard: (center) => {
    const shortPut = center - 300;
    const shortCall = center + 300;
    const longCall = center + 500;
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      let payoff;
      if (spot <= shortPut) payoff = -20 + (spot - (shortPut - 500)) * 0.04;
      else if (spot <= shortCall) payoff = 42;
      else if (spot <= longCall) payoff = 42;
      else payoff = 42;
      if (spot < shortPut - 500) payoff = Math.max(payoff, -40);
      return { spot, payoff: Math.round(Math.min(payoff, 42) * 100) / 100 };
    });
  },

  butterfly: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const dist = Math.abs(spot - center);
      let payoff;
      if (dist <= 100) payoff = 60 - dist * 0.3;
      else if (dist <= 200) payoff = 30 - (dist - 100) * 0.35;
      else payoff = -5;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  risk_reversal: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const norm = (spot - center) / 500;
      const payoff = 25 * Math.tanh(norm * 0.8);
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  ratio_backspread: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const diff = spot - center;
      let payoff;
      if (diff < -500) payoff = -10 + Math.abs(diff + 500) * 0.06;
      else if (diff < 0) payoff = -10 - diff * 0.02;
      else if (diff < 200) payoff = -10;
      else payoff = -10 + (diff - 200) * 0.05;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  collar: (center) => {
    const putStrike = center - 400;
    const callStrike = center + 400;
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      let payoff;
      if (spot <= putStrike) payoff = -15;
      else if (spot <= callStrike) payoff = -15 + ((spot - putStrike) / (callStrike - putStrike)) * 30;
      else payoff = 15;
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },

  dispersion: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const norm = (spot - center) / 800;
      const payoff = 20 - 35 * norm * norm + 8 * Math.pow(norm, 4);
      return { spot, payoff: Math.round(Math.max(payoff, -20) * 100) / 100 };
    });
  },

  momentum: (center) => {
    return Array.from({ length: 81 }, (_, i) => {
      const spot = center - 2000 + i * 50;
      const diff = (spot - center) / 500;
      const payoff = -5 + 35 * Math.max(0, diff - 0.3);
      return { spot, payoff: Math.round(payoff * 100) / 100 };
    });
  },
};

const STRATEGY_TYPE_MAP = {
  'iron_condor': 'iron_condor',
  'straddle': 'straddle',
  'gamma_scalping': 'gamma_scalping',
  'expiry_day': 'expiry_day',
  'mean_reversion': 'mean_reversion',
  'momentum': 'momentum',
  'pair_trading': 'dispersion',
  'supertrend': 'momentum',
  'vwap_scalper': 'momentum',
  'orb_options': 'momentum',
};

const getPayoffForStrategy = (strategyType, strategyId) => {
  if (strategyId?.includes('jade-lizard')) return 'jade_lizard';
  if (strategyId?.includes('butterfly')) return 'butterfly';
  if (strategyId?.includes('risk-reversal')) return 'risk_reversal';
  if (strategyId?.includes('ratio-backspread')) return 'ratio_backspread';
  if (strategyId?.includes('collar') || strategyId?.includes('protective')) return 'collar';
  if (strategyId?.includes('dispersion')) return 'dispersion';
  if (strategyId?.includes('calendar')) return 'mean_reversion';
  if (strategyId?.includes('gamma')) return 'gamma_scalping';
  return STRATEGY_TYPE_MAP[strategyType] || 'iron_condor';
};

export default function PayoffDiagram({ data, height = 200, strategyType, strategyId, center = 24000 }) {
  const chartData = useMemo(() => {
    if (data) return data;
    const payoffType = getPayoffForStrategy(strategyType, strategyId);
    const generator = PAYOFF_GENERATORS[payoffType] || PAYOFF_GENERATORS.iron_condor;
    return generator(center);
  }, [data, strategyType, strategyId, center]);

  const hasNegative = chartData.some(d => d.payoff < 0);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 10 }}>
        <defs>
          <linearGradient id={`payoff-pos-${strategyId || 'default'}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
          <linearGradient id={`payoff-neg-${strategyId || 'default'}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0.3} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" />
        <XAxis
          dataKey="spot"
          tick={{ fill: '#64748b', fontSize: 9 }}
          stroke="#1e2433"
          tickFormatter={(v) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fill: '#64748b', fontSize: 9 }}
          stroke="#1e2433"
          tickFormatter={(v) => `${v > 0 ? '+' : ''}${v}`}
        />
        <ReferenceLine y={0} stroke="#475569" strokeWidth={1.5} />
        <Tooltip
          contentStyle={{ backgroundColor: '#131720', border: '1px solid #1e2433', borderRadius: '8px', fontSize: 11 }}
          formatter={(value) => [`₹${value > 0 ? '+' : ''}${value}`, 'P&L']}
          labelFormatter={(v) => `Spot: ${v.toLocaleString('en-IN')}`}
        />
        <Area
          type="monotone"
          dataKey="payoff"
          stroke="#22c55e"
          strokeWidth={2}
          fill={`url(#payoff-pos-${strategyId || 'default'})`}
          dot={false}
          baseValue={0}
        />
        {hasNegative && (
          <Area
            type="monotone"
            dataKey="payoff"
            stroke="#ef4444"
            strokeWidth={0}
            fill={`url(#payoff-neg-${strategyId || 'default'})`}
            dot={false}
            baseValue={0}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}

import React from 'react';

function GaugeBar({ label, value, limit, unit = '' }) {
  const safeValue = value ?? 0;
  const pct = limit ? Math.min(Math.abs(safeValue / limit) * 100, 100) : 0;
  const color = pct > 80 ? 'bg-loss' : pct > 60 ? 'bg-yellow-500' : 'bg-blue-500';

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400 font-medium">{label}</span>
        <span className="text-slate-300 font-mono">
          {typeof safeValue === 'number' ? safeValue.toFixed(2) : safeValue}{unit}
          {limit ? <span className="text-slate-500"> / {limit}{unit}</span> : null}
        </span>
      </div>
      <div className="w-full h-2 bg-slate-800 dark:bg-slate-800 rounded-full overflow-hidden gauge-track">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function GreeksGauge({ greeks, limits }) {
  const defaultGreeks = greeks || { delta: 0, gamma: 0, theta: 0, vega: 0 };
  const defaultLimits = limits || { delta: 500, gamma: 100, theta: -25000, vega: 50000 };

  return (
    <div className="space-y-3">
      <GaugeBar label="Delta" value={defaultGreeks.delta} limit={defaultLimits.delta} />
      <GaugeBar label="Gamma" value={defaultGreeks.gamma} limit={defaultLimits.gamma} />
      <GaugeBar label="Theta" value={defaultGreeks.theta} limit={defaultLimits.theta} unit="/day" />
      <GaugeBar label="Vega" value={defaultGreeks.vega} limit={defaultLimits.vega} />
    </div>
  );
}

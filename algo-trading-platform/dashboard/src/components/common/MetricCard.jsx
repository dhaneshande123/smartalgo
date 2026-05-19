import React from 'react';

export default function MetricCard({ label, value, prefix = '', suffix = '', change, icon: Icon, colorClass }) {
  const safeValue = value ?? 0;
  const valueColor = colorClass
    ? colorClass
    : typeof safeValue === 'number'
    ? safeValue >= 0
      ? 'text-profit'
      : 'text-loss'
    : 'text-white';

  const displayValue = typeof safeValue === 'number'
    ? `${prefix}${Math.abs(safeValue).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${suffix}`
    : `${prefix}${safeValue}${suffix}`;

  return (
    <div className="glass-card !p-3 !rounded-xl">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">{label}</span>
        {Icon && <Icon className="w-4 h-4 text-slate-500" />}
      </div>
      <div className={`text-xl font-bold font-mono ${valueColor}`}>
        {typeof safeValue === 'number' && safeValue < 0 && '-'}{displayValue}
      </div>
      {change !== undefined && change !== null && (
        <div className={`text-xs mt-0.5 ${change >= 0 ? 'text-profit' : 'text-loss'}`}>
          {change >= 0 ? '+' : ''}{change}%
        </div>
      )}
    </div>
  );
}

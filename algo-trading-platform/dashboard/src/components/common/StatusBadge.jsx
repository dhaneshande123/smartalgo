import React from 'react';

const statusStyles = {
  active: 'bg-profit/10 text-profit',
  running: 'bg-profit/10 text-profit',
  healthy: 'bg-profit/10 text-profit',
  filled: 'bg-profit/10 text-profit',
  paused: 'bg-yellow-500/10 text-yellow-400',
  warning: 'bg-yellow-500/10 text-yellow-400',
  pending: 'bg-blue-500/10 text-blue-400',
  open: 'bg-blue-500/10 text-blue-400',
  stopped: 'bg-slate-500/10 text-slate-400',
  inactive: 'bg-slate-500/10 text-slate-400',
  cancelled: 'bg-slate-500/10 text-slate-400',
  error: 'bg-loss/10 text-loss',
  rejected: 'bg-loss/10 text-loss',
  critical: 'bg-loss/10 text-loss',
  degraded: 'bg-yellow-500/10 text-yellow-400',
};

export default function StatusBadge({ status }) {
  const key = (status || '').toLowerCase();
  const style = statusStyles[key] || 'bg-slate-500/10 text-slate-400';

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium capitalize ${style}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${style.includes('profit') ? 'bg-profit' : style.includes('loss') ? 'bg-loss' : style.includes('yellow') ? 'bg-yellow-400' : style.includes('blue') ? 'bg-blue-400' : 'bg-slate-400'}`} />
      {status}
    </span>
  );
}

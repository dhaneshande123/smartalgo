import React from 'react';
import { Inbox } from 'lucide-react';

export default function EmptyState({ message = 'No data available', icon: Icon = Inbox, size = 'default' }) {
  const isCompact = size === 'compact';
  return (
    <div className={`flex flex-col items-center justify-center ${isCompact ? 'py-6' : 'py-12'} animate-fade-in`}>
      <div className={`${isCompact ? 'w-10 h-10' : 'w-14 h-14'} rounded-2xl bg-slate-800/50 border border-slate-700/30 flex items-center justify-center mb-3`}>
        <Icon className={`${isCompact ? 'w-5 h-5' : 'w-7 h-7'} text-slate-600`} />
      </div>
      <p className={`${isCompact ? 'text-xs' : 'text-sm'} text-slate-500 font-medium`}>
        {message}
      </p>
    </div>
  );
}

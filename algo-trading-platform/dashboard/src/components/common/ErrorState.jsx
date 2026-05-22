import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export default function ErrorState({ message = 'Failed to load data', onRetry, size = 'default' }) {
  const isCompact = size === 'compact';
  return (
    <div className={`flex flex-col items-center justify-center ${isCompact ? 'py-6' : 'py-12'} animate-fade-in`}>
      <div className={`${isCompact ? 'w-10 h-10' : 'w-14 h-14'} rounded-2xl bg-loss/10 border border-loss/20 flex items-center justify-center mb-3`}>
        <AlertTriangle className={`${isCompact ? 'w-5 h-5' : 'w-7 h-7'} text-loss`} />
      </div>
      <p className={`${isCompact ? 'text-xs' : 'text-sm'} text-slate-400 font-medium mb-3`}>
        {message}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-accent bg-accent/10 border border-accent/20 rounded-lg hover:bg-accent/20 transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Try Again
        </button>
      )}
    </div>
  );
}

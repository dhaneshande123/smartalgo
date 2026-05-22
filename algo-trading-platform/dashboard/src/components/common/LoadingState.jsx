import React from 'react';
import { Loader2 } from 'lucide-react';

export default function LoadingState({ message = 'Loading data...', size = 'default' }) {
  const isCompact = size === 'compact';
  return (
    <div className={`flex flex-col items-center justify-center ${isCompact ? 'py-8' : 'py-16'} animate-fade-in`}>
      <Loader2
        className={`${isCompact ? 'w-6 h-6' : 'w-10 h-10'} text-accent animate-spin`}
        style={{ animationDuration: '1.2s' }}
      />
      <p className={`${isCompact ? 'text-xs mt-2' : 'text-sm mt-4'} text-slate-500 font-medium`}>
        {message}
      </p>
    </div>
  );
}

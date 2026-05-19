import React from 'react';

export default function Card({ title, children, className = '', actions }) {
  return (
    <div className={`glass-card p-4 ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between mb-3">
          {title && (
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
              {title}
            </h3>
          )}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

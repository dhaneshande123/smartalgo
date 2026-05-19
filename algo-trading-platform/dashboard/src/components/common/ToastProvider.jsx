import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { AlertTriangle, AlertCircle, Info, CheckCircle, X } from 'lucide-react';
import { useAlertStream } from '../../hooks/useWebSocket';

// ── Context ────────────────────────────────────────────────────────
const ToastContext = createContext(null);

export function useToast() {
  return useContext(ToastContext);
}

// ── Constants ──────────────────────────────────────────────────────
const TOAST_DURATION = {
  CRITICAL: 8000,
  WARNING: 5000,
  INFO: 4000,
  success: 3000,
};

const ICON_MAP = {
  CRITICAL: AlertCircle,
  WARNING: AlertTriangle,
  INFO: Info,
  success: CheckCircle,
};

const STYLE_MAP = {
  CRITICAL: {
    border: 'border-loss/30',
    bg: 'bg-loss/5',
    icon: 'text-loss',
    dot: 'bg-loss',
    glow: 'shadow-loss/10',
  },
  WARNING: {
    border: 'border-yellow-500/30',
    bg: 'bg-yellow-500/5',
    icon: 'text-yellow-400',
    dot: 'bg-yellow-400',
    glow: 'shadow-yellow-500/10',
  },
  INFO: {
    border: 'border-blue-500/30',
    bg: 'bg-blue-500/5',
    icon: 'text-blue-400',
    dot: 'bg-blue-400',
    glow: 'shadow-blue-500/10',
  },
  success: {
    border: 'border-profit/30',
    bg: 'bg-profit/5',
    icon: 'text-profit',
    dot: 'bg-profit',
    glow: 'shadow-profit/10',
  },
};

// ── Single Toast Component ─────────────────────────────────────────
function Toast({ toast, onDismiss }) {
  const [exiting, setExiting] = useState(false);
  const timerRef = useRef(null);
  const level = toast.level || 'INFO';
  const styles = STYLE_MAP[level] || STYLE_MAP.INFO;
  const Icon = ICON_MAP[level] || Info;

  const dismiss = useCallback(() => {
    setExiting(true);
    setTimeout(() => onDismiss(toast.id), 300);
  }, [toast.id, onDismiss]);

  useEffect(() => {
    const duration = TOAST_DURATION[level] || 4000;
    timerRef.current = setTimeout(dismiss, duration);
    return () => clearTimeout(timerRef.current);
  }, [dismiss, level]);

  // Pause on hover
  const handleMouseEnter = () => clearTimeout(timerRef.current);
  const handleMouseLeave = () => {
    const duration = TOAST_DURATION[level] || 4000;
    timerRef.current = setTimeout(dismiss, duration / 2);
  };

  return (
    <div
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={`toast-item flex items-start gap-3 w-[380px] p-4 rounded-2xl border backdrop-blur-xl
        ${styles.border} ${styles.bg}
        shadow-lg ${styles.glow}
        ${exiting ? 'toast-exit' : 'toast-enter'}
        transition-all duration-300 cursor-pointer`}
      onClick={dismiss}
      style={{
        background: 'var(--toast-bg)',
      }}
    >
      {/* Icon */}
      <div className={`flex-shrink-0 mt-0.5 ${styles.icon}`}>
        <Icon className="w-5 h-5" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`w-1.5 h-1.5 rounded-full ${styles.dot} ${level === 'CRITICAL' ? 'animate-pulse' : ''}`} />
          <span className={`text-[10px] font-bold uppercase tracking-wider ${styles.icon}`}>
            {level}
          </span>
          {toast.source && (
            <span className="text-[10px] text-slate-500">{toast.source}</span>
          )}
        </div>
        <div className="text-sm text-slate-200 leading-snug line-clamp-2">
          {toast.message}
        </div>
        {toast.timestamp && (
          <div className="text-[10px] text-slate-500 mt-1">
            {new Date(toast.timestamp).toLocaleTimeString()}
          </div>
        )}
      </div>

      {/* Close */}
      <button
        onClick={(e) => { e.stopPropagation(); dismiss(); }}
        className="flex-shrink-0 text-slate-500 hover:text-slate-300 transition-colors mt-0.5"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

// ── Toast Container ────────────────────────────────────────────────
function ToastContainer({ toasts, onDismiss }) {
  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-3 pointer-events-none">
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto">
          <Toast toast={t} onDismiss={onDismiss} />
        </div>
      ))}
    </div>
  );
}

// ── Provider ───────────────────────────────────────────────────────
let toastId = 0;

export default function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const seenAlertIds = useRef(new Set());
  const { alerts } = useAlertStream();

  const addToast = useCallback((toast) => {
    const id = ++toastId;
    setToasts((prev) => [...prev.slice(-4), { ...toast, id }]); // max 5 visible
    return id;
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Bridge WebSocket alerts into toasts
  useEffect(() => {
    if (!alerts.length) return;
    const latest = alerts[0];
    const alertKey = latest.alert_id || `${latest.timestamp}-${latest.message}`;

    if (seenAlertIds.current.has(alertKey)) return;
    seenAlertIds.current.add(alertKey);

    // Keep set from growing unbounded
    if (seenAlertIds.current.size > 100) {
      const entries = [...seenAlertIds.current];
      seenAlertIds.current = new Set(entries.slice(-50));
    }

    addToast({
      level: latest.level || 'INFO',
      message: latest.message || 'New alert',
      source: latest.source,
      timestamp: latest.timestamp,
    });
  }, [alerts, addToast]);

  const value = { addToast, dismissToast };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </ToastContext.Provider>
  );
}

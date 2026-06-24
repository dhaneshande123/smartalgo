import { useState, useEffect, useRef, useCallback } from 'react';
import { Bell, BellOff, Check, CheckCheck, AlertTriangle, AlertCircle, Info, Trash2, ExternalLink } from 'lucide-react';
import { useAlerts, useAcknowledgeAlert } from '../../hooks/useApi';
import { useTheme } from '../../context/ThemeContext';

const SEVERITY_STYLES = {
  CRITICAL: { icon: AlertCircle, color: 'text-loss', bg: 'bg-loss/10', dot: 'bg-loss', border: 'border-loss/20' },
  BREACH: { icon: AlertCircle, color: 'text-loss', bg: 'bg-loss/10', dot: 'bg-loss', border: 'border-loss/20' },
  WARN: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', dot: 'bg-yellow-400', border: 'border-yellow-500/20' },
  WARNING: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-500/10', dot: 'bg-yellow-400', border: 'border-yellow-500/20' },
  INFO: { icon: Info, color: 'text-blue-400', bg: 'bg-blue-500/10', dot: 'bg-blue-400', border: 'border-blue-500/20' },
};

function requestDesktopPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function sendDesktopNotification(title, body, level) {
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      const icon = level === 'CRITICAL' || level === 'BREACH' ? '🔴' : level === 'WARN' || level === 'WARNING' ? '⚠️' : 'ℹ️';
      new Notification(`${icon} ${title}`, { body, tag: `smartalgo-${Date.now()}`, silent: false });
    } catch { /* desktop notifications not supported */ }
  }
}

function timeAgo(timestamp) {
  if (!timestamp) return '';
  const now = new Date();
  const then = new Date(timestamp);
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

export default function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const [desktopEnabled, setDesktopEnabled] = useState(false);
  const [readIds, setReadIds] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem('smartalgo-read-notifications') || '[]'));
    } catch { return new Set(); }
  });
  const prevAlertCount = useRef(0);
  const panelRef = useRef(null);
  const { theme } = useTheme();

  const { data: alertsData } = useAlerts();
  const acknowledgeAlert = useAcknowledgeAlert();

  const alerts = alertsData?.alerts || [];
  const unreadCount = alerts.filter(a => !a.acknowledged && !readIds.has(a.alert_id)).length;

  useEffect(() => {
    setDesktopEnabled('Notification' in window && Notification.permission === 'granted');
  }, []);

  // Desktop notification for new critical/warning alerts
  useEffect(() => {
    if (alerts.length > prevAlertCount.current && prevAlertCount.current > 0) {
      const newAlerts = alerts.slice(0, alerts.length - prevAlertCount.current);
      for (const alert of newAlerts) {
        if (!alert.acknowledged && (alert.severity === 'CRITICAL' || alert.severity === 'BREACH')) {
          sendDesktopNotification(alert.title || 'Risk Alert', alert.message, alert.severity);
        }
      }
    }
    prevAlertCount.current = alerts.length;
  }, [alerts]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  // Persist read state
  useEffect(() => {
    localStorage.setItem('smartalgo-read-notifications', JSON.stringify([...readIds]));
  }, [readIds]);

  const markRead = useCallback((alertId) => {
    setReadIds(prev => new Set([...prev, alertId]));
  }, []);

  const markAllRead = useCallback(() => {
    setReadIds(prev => new Set([...prev, ...alerts.map(a => a.alert_id)]));
  }, [alerts]);

  const handleAcknowledge = useCallback((alertId) => {
    acknowledgeAlert.mutate(alertId);
    markRead(alertId);
  }, [acknowledgeAlert, markRead]);

  const toggleDesktop = useCallback(() => {
    if (!desktopEnabled) {
      requestDesktopPermission();
      setTimeout(() => setDesktopEnabled(Notification.permission === 'granted'), 1000);
    } else {
      setDesktopEnabled(false);
    }
  }, [desktopEnabled]);

  return (
    <div className="relative" ref={panelRef}>
      {/* Bell Button */}
      <button
        onClick={() => setOpen(!open)}
        className={`relative flex items-center justify-center w-8 h-8 rounded-xl transition-all ${
          open
            ? 'bg-blue-500/15 text-blue-400'
            : 'bg-white/[0.04] hover:bg-white/[0.08] text-slate-400 hover:text-white'
        }`}
        title={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
      >
        <Bell className="w-4 h-4" />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 flex items-center justify-center rounded-full bg-loss text-white text-[9px] font-bold px-1 animate-pulse">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown Panel */}
      {open && (
        <div
          className="absolute right-0 top-full mt-2 z-50 w-[380px] max-h-[500px] rounded-xl border border-terminal-border shadow-2xl overflow-hidden flex flex-col"
          style={{
            background: theme === 'dark' ? 'rgba(15, 23, 42, 0.97)' : 'rgba(255, 255, 255, 0.97)',
            backdropFilter: 'blur(20px)',
          }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-terminal-border">
            <div className="flex items-center gap-2">
              <Bell className="w-4 h-4 text-slate-400" />
              <span className="text-sm font-semibold text-white">Notifications</span>
              {unreadCount > 0 && (
                <span className="px-1.5 py-0.5 rounded-full bg-loss/15 text-loss text-[10px] font-bold">
                  {unreadCount}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={toggleDesktop}
                className={`p-1.5 rounded-lg text-xs transition-colors ${
                  desktopEnabled ? 'text-profit hover:bg-profit/10' : 'text-slate-500 hover:bg-slate-700/50 hover:text-slate-300'
                }`}
                title={desktopEnabled ? 'Desktop notifications ON' : 'Enable desktop notifications'}
              >
                {desktopEnabled ? <Bell className="w-3.5 h-3.5" /> : <BellOff className="w-3.5 h-3.5" />}
              </button>
              {unreadCount > 0 && (
                <button
                  onClick={markAllRead}
                  className="p-1.5 rounded-lg text-slate-500 hover:bg-slate-700/50 hover:text-slate-300 transition-colors"
                  title="Mark all as read"
                >
                  <CheckCheck className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>

          {/* Alert List */}
          <div className="overflow-y-auto flex-1">
            {alerts.length === 0 ? (
              <div className="py-12 text-center text-slate-500">
                <Bell className="w-8 h-8 mx-auto mb-2 opacity-30" />
                <div className="text-sm">No notifications yet</div>
                <div className="text-xs mt-1 text-slate-600">Risk alerts and trade events will appear here</div>
              </div>
            ) : (
              <div className="divide-y divide-terminal-border/50">
                {alerts.slice(0, 50).map(alert => {
                  const severity = alert.severity || 'INFO';
                  const styles = SEVERITY_STYLES[severity] || SEVERITY_STYLES.INFO;
                  const Icon = styles.icon;
                  const isRead = alert.acknowledged || readIds.has(alert.alert_id);

                  return (
                    <div
                      key={alert.alert_id}
                      className={`px-4 py-3 hover:bg-slate-800/30 transition-colors cursor-pointer ${
                        isRead ? 'opacity-60' : ''
                      }`}
                      onClick={() => markRead(alert.alert_id)}
                    >
                      <div className="flex items-start gap-3">
                        <div className={`flex-shrink-0 mt-0.5 p-1 rounded-lg ${styles.bg}`}>
                          <Icon className={`w-3.5 h-3.5 ${styles.color}`} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            {!isRead && <span className={`w-1.5 h-1.5 rounded-full ${styles.dot}`} />}
                            <span className={`text-[10px] font-bold uppercase tracking-wider ${styles.color}`}>
                              {severity}
                            </span>
                            <span className="text-[10px] text-slate-600 ml-auto flex-shrink-0">
                              {timeAgo(alert.created_at || alert.timestamp)}
                            </span>
                          </div>
                          {alert.title && (
                            <div className="text-xs font-medium text-slate-300 mb-0.5 truncate">{alert.title}</div>
                          )}
                          <div className="text-xs text-slate-400 line-clamp-2">{alert.message}</div>
                          {alert.source && (
                            <div className="text-[10px] text-slate-600 mt-1">Source: {alert.source}</div>
                          )}
                        </div>
                        {!alert.acknowledged && (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleAcknowledge(alert.alert_id); }}
                            className="flex-shrink-0 p-1 rounded hover:bg-slate-700/50 text-slate-500 hover:text-profit transition-colors"
                            title="Acknowledge"
                          >
                            <Check className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer */}
          {alerts.length > 0 && (
            <div className="px-4 py-2 border-t border-terminal-border flex items-center justify-between">
              <span className="text-[10px] text-slate-600">
                {alertsData?.total_today || alerts.length} today
              </span>
              <a
                href="/monitoring"
                className="flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 transition-colors"
                onClick={() => setOpen(false)}
              >
                View all <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

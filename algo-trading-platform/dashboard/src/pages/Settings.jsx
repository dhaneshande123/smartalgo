import { useState, useEffect } from 'react';
import {
  Settings as SettingsIcon, Server, Shield, Wifi, Palette, Clock, Cpu,
  Globe, Lock, Unlock, CheckCircle, XCircle, AlertTriangle, Eye, EyeOff,
  Sun, Moon, Monitor, Key, User, LogOut, Copy, Link2, Unlink, ExternalLink, Loader, RefreshCw,
} from 'lucide-react';
import Card from '../components/common/Card';
import StatusBadge from '../components/common/StatusBadge';
import {
  useHealth, useSystemInfo, useSystemConfig, useRiskLimits,
  useFyersStatus, useInitFyersConnect, useFyersConnectionStatus, useDisconnectFyers, useReconnectFyers,
} from '../hooks/useApi';
import { useAuth } from '../contexts/AuthContext';

// ── Helpers ─────────────────────────────────────────────────────────
function formatINR(val) {
  if (val == null) return '--';
  const abs = Math.abs(val);
  if (abs >= 10000000) return `Rs ${(val / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000)   return `Rs ${(val / 100000).toFixed(2)} L`;
  return `Rs ${val.toLocaleString('en-IN')}`;
}

function InfoRow({ label, value, icon: Icon, status, mono = false }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-terminal-border last:border-0">
      <div className="flex items-center gap-2">
        {Icon && <Icon className="w-4 h-4 text-slate-500" />}
        <span className="text-sm text-slate-400">{label}</span>
      </div>
      <div className="flex items-center gap-2">
        {status === 'ok'      && <CheckCircle  className="w-3.5 h-3.5 text-profit" />}
        {status === 'error'   && <XCircle      className="w-3.5 h-3.5 text-loss" />}
        {status === 'warning' && <AlertTriangle className="w-3.5 h-3.5 text-yellow-400" />}
        <span className={`text-sm text-white ${mono ? 'font-mono' : ''}`}>{value ?? '--'}</span>
      </div>
    </div>
  );
}

function SectionHeader({ icon: Icon, title, subtitle }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <div className="w-9 h-9 rounded-xl bg-accent/10 flex items-center justify-center">
        <Icon className="w-5 h-5 text-accent" />
      </div>
      <div>
        <h2 className="text-sm font-bold text-white">{title}</h2>
        {subtitle && <p className="text-[11px] text-slate-500">{subtitle}</p>}
      </div>
    </div>
  );
}

function LimitRow({ label, value, formatted, warn }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-terminal-border last:border-0">
      <span className="text-sm text-slate-400">{label}</span>
      <div className="flex items-center gap-2">
        {warn && <AlertTriangle className="w-3.5 h-3.5 text-yellow-400" />}
        <span className="text-sm font-mono text-white">{formatted || value}</span>
      </div>
    </div>
  );
}

// ── Plan badge colours ───────────────────────────────────────────────
function PlanBadge({ plan }) {
  const meta = {
    enterprise: { label: 'Enterprise', color: '#a78bfa', bg: 'rgba(167,139,250,0.12)' },
    pro:        { label: 'Pro',        color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
    starter:    { label: 'Starter',    color: '#64748b', bg: 'rgba(100,116,139,0.12)' },
  }[plan] || { label: plan || 'Free', color: '#64748b', bg: 'rgba(100,116,139,0.12)' };

  return (
    <span className="text-xs font-bold px-2.5 py-0.5 rounded-full uppercase tracking-wide"
      style={{ color: meta.color, background: meta.bg }}>
      {meta.label}
    </span>
  );
}

// ── Masked secret field ──────────────────────────────────────────────
function SecretField({ label, value, onChange, placeholder, textarea = false, rows = 1 }) {
  const [visible, setVisible] = useState(false);

  const inputClass =
    'flex-1 bg-slate-800/60 border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-white font-mono ' +
    'placeholder:text-slate-600 focus:outline-none focus:border-blue-500/60 transition-colors duration-150';

  return (
    <div>
      <label className="block text-xs font-semibold text-slate-400 mb-1.5">{label}</label>
      <div className="flex items-start gap-2">
        {textarea ? (
          <textarea
            rows={rows}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            type={visible ? 'text' : 'password'}
            className={`${inputClass} resize-none`}
            style={{ filter: visible ? 'none' : 'blur(3px)', transition: 'filter 0.2s' }}
          />
        ) : (
          <input
            type={visible ? 'text' : 'password'}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            className={inputClass}
          />
        )}
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="p-2 rounded-lg border border-slate-700/50 text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors duration-150 flex-shrink-0 mt-0.5"
          title={visible ? 'Hide' : 'Show'}
        >
          {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main Component
// ════════════════════════════════════════════════════════════════════
export default function Settings() {
  const { data: healthData } = useHealth();
  const { data: sysInfo }    = useSystemInfo();
  const { data: sysConfig }  = useSystemConfig();
  const { data: riskLimits } = useRiskLimits();
  const { user, logout }     = useAuth();

  const [showKeys,       setShowKeys]     = useState(false);
  const [activeSection,  setActiveSection] = useState('system');

  // ── Fyers OAuth state ──
  const [fyersAppId, setFyersAppId] = useState('');
  const [fyersSecretKey, setFyersSecretKey] = useState('');
  const [fyersBanner, setFyersBanner] = useState(null);
  const [isPolling, setIsPolling] = useState(false);

  const { data: fyersStatusData } = useFyersStatus();
  const fyersStatus = fyersStatusData || {};
  const initConnect = useInitFyersConnect();
  const { data: connStatusData } = useFyersConnectionStatus(isPolling);
  const connStatus = connStatusData || {};
  const disconnectFyers = useDisconnectFyers();
  const reconnectFyers = useReconnectFyers();

  // ── Parse data ──
  const health     = healthData || {};
  const info       = sysInfo    || {};
  const config     = sysConfig  || {};
  const limits     = riskLimits || {};
  const components = health.components || {};
  const brokers    = config.brokers    || {};

  const sections = [
    { id: 'system',      label: 'System',     icon: Server  },
    { id: 'risk',        label: 'Risk Limits', icon: Shield  },
    { id: 'connections', label: 'Connections', icon: Wifi    },
    { id: 'appearance',  label: 'Appearance',  icon: Palette },
    { id: 'api-keys',    label: 'API Keys',    icon: Key     },
    { id: 'account',     label: 'Account',     icon: User    },
  ];

  // ── Theme handling ──
  const isDark = document.documentElement.classList.contains('dark');
  const setTheme = (mode) => {
    if (mode === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  };

  // Stop polling when connected or error
  useEffect(() => {
    if (connStatus.status === 'connected' || connStatus.status === 'error') {
      setIsPolling(false);
      if (connStatus.status === 'connected') {
        setFyersBanner({ type: 'success', msg: 'Fyers connected! Live data feed is active.' });
      } else if (connStatus.status === 'error') {
        setFyersBanner({ type: 'error', msg: connStatus.message || 'Connection failed' });
      }
    }
  }, [connStatus.status, connStatus.message]);

  // ── Fyers connect handler ──
  const handleFyersConnect = async () => {
    setFyersBanner(null);
    try {
      const resp = await initConnect.mutateAsync({
        app_id: fyersAppId,
        secret_key: fyersSecretKey,
      });
      if (resp.auth_url) {
        setIsPolling(true);
        window.open(resp.auth_url, '_blank', 'noopener');
        setFyersBanner({ type: 'info', msg: 'Fyers login opened — complete authentication in the new tab.' });
      }
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to start connection';
      setFyersBanner({ type: 'error', msg: detail });
    }
  };

  const handleFyersDisconnect = async () => {
    setFyersBanner(null);
    try {
      await disconnectFyers.mutateAsync();
      setFyersBanner({ type: 'success', msg: 'Disconnected from Fyers.' });
    } catch (err) {
      setFyersBanner({ type: 'error', msg: err?.message || 'Disconnect failed' });
    }
  };

  const handleFyersReconnect = async () => {
    setFyersBanner(null);
    try {
      const res = await reconnectFyers.mutateAsync();
      if (res?.live_feed_connected) {
        setFyersBanner({ type: 'success', msg: 'Reconnected using stored token.' });
      } else {
        setFyersBanner({ type: 'error', msg: res?.message || 'Reconnect failed — token may have expired. Use Connect Fyers.' });
      }
    } catch (err) {
      setFyersBanner({ type: 'error', msg: err?.response?.data?.detail || err?.message || 'Reconnect failed' });
    }
  };

  const isLiveConnected = fyersStatus.live_feed_connected || connStatus.live_feed_connected;
  return (
    <div className="space-y-4 max-w-[1440px] mx-auto animate-fade-in">
      {/* ── Header ── */}
      <div>
        <h1 className="text-xl font-bold text-white tracking-tight">Settings</h1>
        <p className="text-xs text-slate-400 mt-0.5">Platform configuration, risk limits, and preferences</p>
      </div>

      {/* ── Section Navigation ── */}
      <div className="flex flex-wrap gap-1 glass-card !rounded-xl !p-1.5">
        {sections.map((s) => {
          const Icon = s.icon;
          return (
            <button
              key={s.id}
              onClick={() => setActiveSection(s.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                activeSection === s.id
                  ? 'bg-accent/15 text-accent shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
              }`}
            >
              <Icon className="w-4 h-4" />
              {s.label}
            </button>
          );
        })}
      </div>

      {/* ════════════════════════════════════════════════════════ */}
      {/* SYSTEM INFO                                             */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeSection === 'system' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <SectionHeader icon={Server} title="Platform Info" subtitle="Core system details" />
            <div className="space-y-0">
              <InfoRow label="Platform Version"      value={info.platform_version || health.version}   icon={Cpu} />
              <InfoRow label="Trading Mode"          value={info.trading_mode || config.mode}          icon={Globe} />
              <InfoRow label="Timezone"              value={info.timezone || config.timezone}          icon={Clock} />
              <InfoRow label="Market Hours"          value={info.market_open && info.market_close ? `${info.market_open} — ${info.market_close}` : '09:15 — 15:30'} icon={Clock} />
              <InfoRow label="Uptime"                value={health.uptime_human}                       icon={Server} />
              <InfoRow label="Primary Broker"        value={info.primary_broker || config.primary_broker} icon={Wifi} />
              <InfoRow label="Backup Broker"         value={info.backup_broker || 'None'}              icon={Wifi} />
              <InfoRow label="Strategies Configured" value={info.strategies_configured}               icon={SettingsIcon} />
              <InfoRow label="Event Bus"             value={info.event_bus_backend || 'in_memory'}    icon={Cpu} />
              <InfoRow label="Log Level"             value={config.log_level || 'INFO'} />
            </div>
          </Card>

          <Card>
            <SectionHeader icon={CheckCircle} title="Component Health" subtitle="Live status of all subsystems" />
            <div className="space-y-0">
              {Object.entries(components).length > 0 ? (
                Object.entries(components).map(([name, status]) => {
                  const label = name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
                  return (
                    <div key={name} className="flex items-center justify-between py-3 border-b border-terminal-border last:border-0">
                      <span className="text-sm text-slate-400">{label}</span>
                      <StatusBadge status={status} />
                    </div>
                  );
                })
              ) : (
                <div className="text-sm text-slate-500 py-4 text-center">Loading component health...</div>
              )}
            </div>
          </Card>

          {info.python_version && (
            <Card className="lg:col-span-2">
              <SectionHeader icon={Cpu} title="Runtime Environment" />
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8">
                <InfoRow label="Python Version" value={info.python_version?.split(' ')[0]} mono />
                <InfoRow label="OS Platform"    value={info.os_platform} mono />
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* RISK LIMITS                                             */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeSection === 'risk' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <SectionHeader icon={Shield} title="Order Limits" subtitle="Per-order and rate constraints" />
            <div className="space-y-0">
              <LimitRow label="Max Order Value"     value={limits.max_order_value}          formatted={formatINR(limits.max_order_value)} />
              <LimitRow label="Max Loss per Order"  value={limits.max_loss_per_order}       formatted={formatINR(limits.max_loss_per_order)} />
              <LimitRow label="Max Qty per Order"   value={limits.max_quantity_per_order}   formatted={limits.max_quantity_per_order?.toLocaleString()} />
              <LimitRow label="Max Open Orders"     value={limits.max_open_orders}          formatted={limits.max_open_orders?.toString()} />
              <LimitRow label="Max Orders/Min"      value={limits.max_orders_per_minute}    formatted={limits.max_orders_per_minute?.toString()} />
            </div>
          </Card>

          <Card>
            <SectionHeader icon={Shield} title="Portfolio Limits" subtitle="Portfolio-level risk boundaries" />
            <div className="space-y-0">
              <LimitRow label="Max Position Value"     value={limits.max_position_value}     formatted={formatINR(limits.max_position_value)} />
              <LimitRow label="Max Portfolio Value"    value={limits.max_portfolio_value}    formatted={formatINR(limits.max_portfolio_value)} />
              <LimitRow label="Max Loss per Strategy"  value={limits.max_loss_per_strategy}  formatted={formatINR(limits.max_loss_per_strategy)} />
              <LimitRow label="Max Loss per Day"       value={limits.max_loss_per_day}       formatted={formatINR(limits.max_loss_per_day)} />
              <LimitRow
                label="Position Concentration"
                value={limits.position_concentration_limit}
                formatted={limits.position_concentration_limit ? `${(limits.position_concentration_limit * 100).toFixed(0)}%` : '--'}
              />
            </div>
          </Card>

          <Card className="lg:col-span-2">
            <SectionHeader icon={Shield} title="Greeks Exposure Limits" subtitle="Maximum allowed portfolio greeks" />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[
                { label: 'Max Delta', value: limits.max_greeks_delta, color: 'text-blue-400' },
                { label: 'Max Gamma', value: limits.max_greeks_gamma, color: 'text-purple-400' },
                { label: 'Max Vega',  value: limits.max_greeks_vega,  color: 'text-cyan-400' },
              ].map((g) => (
                <div key={g.label} className="glass-card !p-4 !rounded-xl text-center">
                  <div className="text-xs text-slate-400 uppercase tracking-wider mb-1">{g.label}</div>
                  <div className={`text-2xl font-bold font-mono ${g.color}`}>
                    {g.value != null ? g.value.toLocaleString() : '--'}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* CONNECTIONS                                              */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeSection === 'connections' && (
        <div className="space-y-4">
          <Card>
            <SectionHeader icon={Wifi} title="Broker Connections" subtitle="Configured broker API integrations" />
            {Object.entries(brokers).length > 0 ? (
              <div className="space-y-4">
                {Object.entries(brokers).map(([name, broker]) => (
                  <div key={name} className="glass-card !p-4 !rounded-xl">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center">
                          <Wifi className="w-4 h-4 text-accent" />
                        </div>
                        <div>
                          <div className="text-sm font-semibold text-white capitalize">{name}</div>
                          <div className="text-[10px] text-slate-500">{broker.provider || 'API Broker'}</div>
                        </div>
                      </div>
                      <StatusBadge status={broker.api_key ? 'active' : 'inactive'} />
                    </div>
                    <div className="space-y-0">
                      <div className="flex items-center justify-between py-2 border-b border-terminal-border">
                        <span className="text-xs text-slate-400">API Key</span>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono text-slate-300">
                            {showKeys
                              ? (broker.api_key || 'Not set')
                              : (broker.api_key ? '••••••••' + broker.api_key.slice(-4) : 'Not set')}
                          </span>
                          <button onClick={() => setShowKeys(!showKeys)} className="text-slate-500 hover:text-slate-300 transition-colors">
                            {showKeys ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      </div>
                      {broker.api_secret && (
                        <div className="flex items-center justify-between py-2 border-b border-terminal-border">
                          <span className="text-xs text-slate-400">API Secret</span>
                          <span className="text-xs font-mono text-slate-300">
                            {showKeys ? broker.api_secret : '••••••••'}
                          </span>
                        </div>
                      )}
                      {broker.redirect_url && (
                        <div className="flex items-center justify-between py-2">
                          <span className="text-xs text-slate-400">Redirect URL</span>
                          <span className="text-xs font-mono text-slate-300 truncate max-w-[200px]">{broker.redirect_url}</span>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-8">
                <Wifi className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <div className="text-sm text-slate-500">No brokers configured</div>
                <div className="text-xs text-slate-600 mt-1">Add broker credentials in your platform config</div>
              </div>
            )}
          </Card>

          {config.dashboard && (
            <Card>
              <SectionHeader icon={Globe} title="Dashboard Configuration" subtitle="Web interface settings" />
              <div className="space-y-0">
                <InfoRow label="Host"         value={config.dashboard.host}  icon={Globe} mono />
                <InfoRow label="Port"         value={config.dashboard.port}  icon={Globe} mono />
                <InfoRow label="Auth Enabled" value={config.dashboard.auth_enabled ? 'Yes' : 'No'} icon={config.dashboard.auth_enabled ? Lock : Unlock} status={config.dashboard.auth_enabled ? 'ok' : 'warning'} />
                <InfoRow label="CORS Origins" value={config.dashboard.cors_origins?.join(', ') || '*'} icon={Globe} />
              </div>
            </Card>
          )}

          {config.market_data && (
            <Card>
              <SectionHeader icon={Server} title="Market Data Feed" subtitle="Data provider configuration" />
              <div className="space-y-0">
                {Object.entries(config.market_data).map(([key, val]) => {
                  const label   = key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
                  const display = typeof val === 'boolean' ? (val ? 'Enabled' : 'Disabled') : typeof val === 'object' ? JSON.stringify(val) : String(val ?? '--');
                  return <InfoRow key={key} label={label} value={display} />;
                })}
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* APPEARANCE                                               */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeSection === 'appearance' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <SectionHeader icon={Palette} title="Theme" subtitle="Choose your preferred appearance" />
            <div className="grid grid-cols-2 gap-3">
              {[
                { mode: 'light', label: 'Light', icon: Sun,  desc: 'Clean and bright',   bg: 'bg-gradient-to-br from-slate-50 to-slate-200' },
                { mode: 'dark',  label: 'Dark',  icon: Moon, desc: 'Easy on the eyes',   bg: 'bg-gradient-to-br from-slate-900 to-slate-800' },
              ].map((t) => {
                const isActive = (t.mode === 'dark' && isDark) || (t.mode === 'light' && !isDark);
                const Icon = t.icon;
                return (
                  <button
                    key={t.mode}
                    onClick={() => setTheme(t.mode)}
                    className={`glass-card glass-card-interactive !p-5 !rounded-2xl text-left transition-all duration-300 ${isActive ? '!border-accent/30 ring-2 ring-accent/10' : ''}`}
                  >
                    <div className={`w-full h-20 rounded-xl mb-3 ${t.bg} flex items-center justify-center`}>
                      <Icon className={`w-8 h-8 ${t.mode === 'dark' ? 'text-slate-300' : 'text-slate-600'}`} />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-semibold text-white">{t.label}</div>
                        <div className="text-[11px] text-slate-500">{t.desc}</div>
                      </div>
                      {isActive && (
                        <div className="w-5 h-5 rounded-full bg-accent flex items-center justify-center">
                          <CheckCircle className="w-3.5 h-3.5 text-white" />
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </Card>

          <Card>
            <SectionHeader icon={Monitor} title="Display Preferences" subtitle="UI customization" />
            <div className="space-y-4">
              <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                <div>
                  <div className="text-sm text-slate-300">Currency Format</div>
                  <div className="text-[11px] text-slate-500">Indian Rupee (INR)</div>
                </div>
                <span className="text-sm font-mono text-white">Rs / INR</span>
              </div>
              <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                <div>
                  <div className="text-sm text-slate-300">Number Format</div>
                  <div className="text-[11px] text-slate-500">Indian numbering system</div>
                </div>
                <span className="text-sm font-mono text-white">en-IN</span>
              </div>
              <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                <div>
                  <div className="text-sm text-slate-300">Timezone</div>
                  <div className="text-[11px] text-slate-500">Market timezone</div>
                </div>
                <span className="text-sm font-mono text-white">Asia/Kolkata (IST)</span>
              </div>
              <div className="flex items-center justify-between py-3">
                <div>
                  <div className="text-sm text-slate-300">Data Refresh</div>
                  <div className="text-[11px] text-slate-500">WebSocket + REST polling</div>
                </div>
                <span className="text-sm font-mono text-profit">Real-time</span>
              </div>
            </div>
          </Card>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* API KEYS — Fyers OAuth Connect                           */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeSection === 'api-keys' && (
        <div className="space-y-4">
          {/* ── Connection Status Card ── */}
          <Card>
            <div className="flex items-center justify-between mb-5">
              <SectionHeader icon={Link2} title="Fyers Broker Connection" subtitle="Connect your Fyers account for live market data" />
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold tracking-wide border ${
                  isLiveConnected
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
                    : isPolling
                    ? 'bg-amber-500/10 text-amber-400 border-amber-500/25'
                    : 'bg-slate-500/10 text-slate-400 border-slate-500/25'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${
                    isLiveConnected ? 'bg-emerald-400 animate-pulse' : isPolling ? 'bg-amber-400 animate-pulse' : 'bg-slate-500'
                  }`} />
                  {isLiveConnected ? 'CONNECTED' : isPolling ? 'AUTHENTICATING...' : 'DISCONNECTED'}
                </span>
              </div>
            </div>

            {/* Banner */}
            {fyersBanner && (
              <div className={`flex items-center gap-2.5 px-4 py-3 rounded-lg mb-4 text-sm font-medium ${
                fyersBanner.type === 'success' ? 'bg-emerald-500/10 border border-emerald-500/25 text-emerald-400'
                : fyersBanner.type === 'info' ? 'bg-blue-500/10 border border-blue-500/25 text-blue-400'
                : 'bg-red-500/10 border border-red-500/25 text-red-400'
              }`}>
                {fyersBanner.type === 'success' ? <CheckCircle className="w-4 h-4 flex-shrink-0" />
                  : fyersBanner.type === 'info' ? <ExternalLink className="w-4 h-4 flex-shrink-0" />
                  : <XCircle className="w-4 h-4 flex-shrink-0" />}
                {fyersBanner.msg}
              </div>
            )}

            {/* Connected state */}
            {isLiveConnected ? (
              <div className="space-y-4">
                <div className="rounded-xl p-4" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)' }}>
                  <div className="flex items-center gap-3 mb-3">
                    <div className="w-10 h-10 rounded-xl bg-emerald-500/15 flex items-center justify-center">
                      <CheckCircle className="w-5 h-5 text-emerald-400" />
                    </div>
                    <div>
                      <div className="text-sm font-bold text-emerald-400">Fyers Live Feed Active</div>
                      <div className="text-[11px] text-slate-500">Real-time market data streaming via WebSocket + REST</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 mt-3">
                    <div className="rounded-lg bg-slate-800/40 px-3 py-2">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">App ID</div>
                      <div className="text-sm font-mono text-white mt-0.5">{fyersStatus.app_id || '—'}</div>
                    </div>
                    <div className="rounded-lg bg-slate-800/40 px-3 py-2">
                      <div className="text-[10px] text-slate-500 uppercase tracking-wider">Token Status</div>
                      <div className="text-sm font-mono text-emerald-400 mt-0.5">
                        {fyersStatus.access_token_set ? 'Valid' : 'Missing'}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-3 border-t border-terminal-border">
                  <p className="text-[11px] text-slate-500">
                    Reconnect retries with the saved token (no re-login). Disconnect clears it.
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleFyersReconnect}
                      disabled={reconnectFyers.isPending}
                      title="Retry the live feed using the stored token — use if the feed dropped but the token is still valid today"
                      className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold
                        bg-accent/10 text-accent border border-accent/20
                        hover:bg-accent/20 active:scale-95 transition-all duration-150
                        disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <RefreshCw className={`w-4 h-4 ${reconnectFyers.isPending ? 'animate-spin' : ''}`} />
                      {reconnectFyers.isPending ? 'Reconnecting...' : 'Reconnect'}
                    </button>
                    <button
                      onClick={handleFyersDisconnect}
                      disabled={disconnectFyers.isPending}
                      className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold
                        bg-red-500/10 text-red-400 border border-red-500/20
                        hover:bg-red-500/20 active:scale-95 transition-all duration-150
                        disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Unlink className="w-4 h-4" />
                      {disconnectFyers.isPending ? 'Disconnecting...' : 'Disconnect'}
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              /* Disconnected state — show connect form */
              <div className="space-y-5">
                {/* Stored token exists — offer a one-click reconnect before the
                    full re-auth form, so a dropped feed never leaves the user
                    stuck without a recovery button. */}
                {fyersStatus.access_token_set && (
                  <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg"
                    style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
                    <div>
                      <div className="text-sm font-semibold text-amber-400">Feed dropped — a saved token is still on file</div>
                      <div className="text-[11px] text-slate-500">
                        Try Reconnect first (no re-login). If the token expired, use Connect Fyers below.
                      </div>
                    </div>
                    <button
                      onClick={handleFyersReconnect}
                      disabled={reconnectFyers.isPending}
                      className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold flex-shrink-0
                        bg-accent/10 text-accent border border-accent/20
                        hover:bg-accent/20 active:scale-95 transition-all duration-150
                        disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <RefreshCw className={`w-4 h-4 ${reconnectFyers.isPending ? 'animate-spin' : ''}`} />
                      {reconnectFyers.isPending ? 'Reconnecting...' : 'Reconnect'}
                    </button>
                  </div>
                )}

                <div className="flex items-start gap-2.5 px-3 py-3 rounded-lg text-xs"
                  style={{ background: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.15)' }}>
                  <Key className="w-3.5 h-3.5 text-blue-400 mt-0.5 flex-shrink-0" />
                  <span className="text-slate-400">
                    Enter your Fyers API credentials below and click <span className="text-blue-400 font-semibold">Connect</span>.
                    A Fyers login page will open where you can authenticate with your TOTP/OTP.
                    The access token is fetched automatically — no manual copy-paste needed.
                  </span>
                </div>

                <SecretField
                  label="App ID"
                  value={fyersAppId}
                  onChange={setFyersAppId}
                  placeholder="e.g. 2VCPOWXCZM-100"
                />
                <SecretField
                  label="Secret Key"
                  value={fyersSecretKey}
                  onChange={setFyersSecretKey}
                  placeholder="Your Fyers API secret key"
                />

                {/* Polling status */}
                {isPolling && (
                  <div className="flex items-center gap-3 px-4 py-3 rounded-lg"
                    style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
                    <Loader className="w-4 h-4 text-amber-400 animate-spin" />
                    <div>
                      <div className="text-sm font-medium text-amber-400">Waiting for authentication...</div>
                      <div className="text-[11px] text-slate-500">
                        {connStatus.message || 'Complete the login in the Fyers tab. This will update automatically.'}
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex items-center justify-between pt-4 border-t border-terminal-border">
                  <p className="text-[11px] text-slate-500">
                    Opens Fyers login in a new tab. Token is saved automatically.
                  </p>
                  <button
                    onClick={handleFyersConnect}
                    disabled={initConnect.isPending || isPolling || !fyersAppId || !fyersSecretKey}
                    className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold bg-accent text-white
                      disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 active:scale-95 transition-all duration-150"
                  >
                    {initConnect.isPending ? (
                      <>
                        <span className="w-3.5 h-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                        Starting...
                      </>
                    ) : (
                      <>
                        <Link2 className="w-4 h-4" />
                        Connect to Fyers
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}
          </Card>

          {/* Security Notes */}
          <Card>
            <SectionHeader icon={Lock} title="Security Notes" subtitle="Best practices for API credentials" />
            <ul className="space-y-2.5 text-sm text-slate-400">
              {[
                'Your access token is fetched via OAuth and stored locally in the .env file.',
                'Fyers access tokens expire daily — reconnect each trading day.',
                'The secret key is permanent; treat it like a password.',
                'All credentials stay on your local machine (localhost only).',
                'Enable IP whitelisting in your Fyers developer account for extra security.',
              ].map((tip) => (
                <li key={tip} className="flex items-start gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-slate-500 mt-1.5 flex-shrink-0" />
                  {tip}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════ */}
      {/* ACCOUNT                                                  */}
      {/* ════════════════════════════════════════════════════════ */}
      {activeSection === 'account' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <SectionHeader icon={User} title="Account Information" subtitle="Your profile and plan details" />

            {user ? (
              <div className="space-y-0">
                {/* Avatar */}
                <div className="flex items-center gap-4 py-4 border-b border-terminal-border">
                  <div className="w-14 h-14 rounded-full bg-accent/20 flex items-center justify-center text-2xl font-bold text-accent">
                    {user.name?.charAt(0)?.toUpperCase() || 'U'}
                  </div>
                  <div>
                    <div className="text-base font-bold text-white">{user.name}</div>
                    <div className="text-sm text-slate-400">{user.email}</div>
                    <div className="mt-1">
                      <PlanBadge plan={user.plan} />
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                  <span className="text-sm text-slate-400">User ID</span>
                  <span className="text-xs font-mono text-slate-500">{user.id}</span>
                </div>
                <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                  <span className="text-sm text-slate-400">Role</span>
                  <span className="text-sm font-semibold text-white capitalize">{user.role}</span>
                </div>
                <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                  <span className="text-sm text-slate-400">Plan</span>
                  <PlanBadge plan={user.plan} />
                </div>
                <div className="flex items-center justify-between py-3 border-b border-terminal-border">
                  <span className="text-sm text-slate-400">Email</span>
                  <span className="text-sm font-mono text-slate-300">{user.email}</span>
                </div>
                <div className="flex items-center justify-between py-3">
                  <span className="text-sm text-slate-400">Account Created</span>
                  <span className="text-sm text-slate-300">
                    {user.createdAt
                      ? new Date(user.createdAt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
                      : '—'}
                  </span>
                </div>
              </div>
            ) : (
              <div className="py-8 text-center text-slate-500 text-sm">No user session found.</div>
            )}
          </Card>

          <Card>
            <SectionHeader icon={Shield} title="Session & Security" subtitle="Manage your active session" />
            <div className="space-y-4">
              <div className="flex items-center gap-3 px-4 py-4 rounded-xl"
                style={{ background: 'rgba(16,185,129,0.05)', border: '1px solid rgba(16,185,129,0.15)' }}>
                <CheckCircle className="w-5 h-5 text-emerald-400 flex-shrink-0" />
                <div>
                  <div className="text-sm font-semibold text-white">Session Active</div>
                  <div className="text-[11px] text-slate-500 mt-0.5">Authenticated via local session store</div>
                </div>
              </div>

              <div className="text-[11px] text-slate-500 leading-relaxed">
                Your session is stored locally in this browser. Logging out will clear all session data
                and redirect you to the login page.
              </div>

              <button
                onClick={logout}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold
                  border border-red-500/30 text-red-400 hover:bg-red-500/8 hover:border-red-500/50
                  active:scale-[0.98] transition-all duration-150"
              >
                <LogOut className="w-4 h-4" />
                Sign Out
              </button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

import { useState } from 'react';
import {
  Settings as SettingsIcon, Server, Shield, Wifi, Palette, Clock, Cpu,
  Globe, Lock, Unlock, CheckCircle, XCircle, AlertTriangle, Eye, EyeOff,
  Sun, Moon, Monitor, User, LogOut,
} from 'lucide-react';
import Card from '../components/common/Card';
import StatusBadge from '../components/common/StatusBadge';
import {
  useHealth, useSystemInfo, useSystemConfig, useRiskLimits,
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

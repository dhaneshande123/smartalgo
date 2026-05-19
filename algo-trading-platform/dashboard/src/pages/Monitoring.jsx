import React, { useState } from 'react';
import {
  Activity, AlertTriangle, CheckCircle, XCircle, Clock,
  Cpu, HardDrive, Wifi, Server,
} from 'lucide-react';
import { useTheme } from '../context/ThemeContext';
import {
  AreaChart, Area, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { useMonitoringHealth, useAlerts, usePerformanceMetrics, useSystemResources, useAcknowledgeAlert } from '../hooks/useApi';

// ─── Fallback data ────────────────────────────────────────────────────────────
const fallbackHealth = [
  { component: 'Order Execution Engine', status: 'healthy',  latency: 2.3,   uptime: 99.98, lastCheck: '14:30:01' },
  { component: 'Market Data Feed',        status: 'healthy',  latency: 0.8,   uptime: 99.95, lastCheck: '14:30:00' },
  { component: 'Risk Engine',             status: 'healthy',  latency: 1.5,   uptime: 99.99, lastCheck: '14:30:01' },
  { component: 'Strategy Engine',         status: 'warning',  latency: 15.2,  uptime: 99.85, lastCheck: '14:29:58' },
  { component: 'Database',                status: 'healthy',  latency: 3.1,   uptime: 99.97, lastCheck: '14:30:00' },
  { component: 'Broker API',              status: 'degraded', latency: 250.0, uptime: 98.50, lastCheck: '14:29:55' },
];

const fallbackAlerts = [
  { id: 1, severity: 'warning',  message: 'Strategy Engine latency above threshold (15.2ms)',    time: '14:29:58', acknowledged: false },
  { id: 2, severity: 'critical', message: 'Broker API response time degraded (250ms)',           time: '14:28:30', acknowledged: false },
  { id: 3, severity: 'warning',  message: 'Margin utilization above 60% threshold',              time: '14:15:00', acknowledged: true },
  { id: 4, severity: 'info',     message: 'Daily P&L report generated successfully',             time: '14:00:00', acknowledged: true },
  { id: 5, severity: 'warning',  message: 'Order rate approaching limit (42/50 per min)',        time: '13:45:22', acknowledged: false },
];

const fallbackPerf = Array.from({ length: 20 }, (_, i) => ({
  time: `14:${String(10 + i).padStart(2, '0')}`,
  ordersPerSec: +(8 + Math.random() * 12).toFixed(2),
  latency:      +(1.5 + Math.random() * 5).toFixed(2),
}));

const fallbackResources = { cpu: 45, memory: 62, disk: 38, network: 12 };

// ─── Helpers ──────────────────────────────────────────────────────────────────
function getCardStyle(theme) {
  return theme === 'dark'
    ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' }
    : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.08)', boxShadow: '0 1px 4px rgba(0,0,0,0.06)' };
}

function getTooltipStyle(theme) {
  return theme === 'dark'
    ? { background: 'rgba(19,23,32,0.97)', border: '1px solid rgba(100,116,139,0.2)' }
    : { background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(0,0,0,0.1)', boxShadow: '0 2px 8px rgba(0,0,0,0.1)' };
}

function statusMeta(status) {
  switch (status) {
    case 'healthy': return { icon: CheckCircle, color: '#10b981', bg: 'rgba(16,185,129,0.08)', border: 'rgba(16,185,129,0.2)', label: 'Healthy' };
    case 'warning': return { icon: AlertTriangle, color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.2)', label: 'Warning' };
    case 'degraded': return { icon: AlertTriangle, color: '#f97316', bg: 'rgba(249,115,22,0.08)', border: 'rgba(249,115,22,0.2)', label: 'Degraded' };
    default: return { icon: XCircle, color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)', label: 'Critical' };
  }
}

function latencyColor(ms) {
  if (ms < 10)  return { text: '#10b981', bg: 'rgba(16,185,129,0.12)' };
  if (ms < 50)  return { text: '#f59e0b', bg: 'rgba(245,158,11,0.12)' };
  return           { text: '#ef4444', bg: 'rgba(239,68,68,0.12)' };
}

function gaugeColor(pct) {
  if (pct < 60) return '#10b981';
  if (pct < 80) return '#f59e0b';
  return '#ef4444';
}

function severityMeta(sev) {
  switch (sev) {
    case 'critical': return { color: '#ef4444', bg: 'rgba(239,68,68,0.12)', border: '#ef4444' };
    case 'warning':  return { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', border: '#f59e0b' };
    default:         return { color: '#3b82f6', bg: 'rgba(59,130,246,0.12)', border: '#3b82f6' };
  }
}

// ─── Circular SVG gauge ───────────────────────────────────────────────────────
function CircularGauge({ label, value, icon: Icon }) {
  const color = gaugeColor(value);
  const circumference = 2 * Math.PI * 15.9155;
  const dash = (value / 100) * circumference;

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative w-24 h-24">
        <svg className="w-24 h-24 -rotate-90" viewBox="0 0 36 36">
          <circle cx="18" cy="18" r="15.9155" fill="none" stroke="rgba(30,36,51,0.9)" strokeWidth="3" />
          <circle
            cx="18" cy="18" r="15.9155"
            fill="none"
            stroke={color}
            strokeWidth="3"
            strokeDasharray={`${dash} ${circumference}`}
            strokeLinecap="round"
            style={{ transition: 'stroke-dasharray 0.8s ease, stroke 0.4s ease' }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-0.5">
          <Icon className="w-4 h-4" style={{ color }} />
          <span className="text-sm font-bold font-mono text-white">{value}%</span>
        </div>
      </div>
      <span className="text-xs text-slate-400 font-medium">{label}</span>
    </div>
  );
}

// ─── Component Health Card ────────────────────────────────────────────────────
function HealthCard({ comp, theme }) {
  const meta  = statusMeta(comp.status);
  const lmeta = latencyColor(comp.latency);
  const StatusIcon = meta.icon;

  return (
    <div className="rounded-xl p-4 flex flex-col gap-3"
      style={{ background: meta.bg, border: `1px solid ${meta.border}` }}>
      {/* Top row */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <StatusIcon className="w-6 h-6 flex-shrink-0" style={{ color: meta.color }} />
          <span className="text-sm font-bold text-white leading-tight truncate">{comp.component}</span>
        </div>
        {/* Latency badge */}
        <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded-full flex-shrink-0"
          style={{ color: lmeta.text, background: lmeta.bg }}>
          {comp.latency}ms
        </span>
      </div>

      {/* Uptime bar */}
      <div>
        <div className="flex justify-between text-[11px] mb-1">
          <span className="text-slate-500">Uptime</span>
          <span className="font-mono text-white">{comp.uptime}%</span>
        </div>
        <div className={`h-1.5 rounded-full ${theme === 'dark' ? 'bg-slate-800' : 'bg-slate-200'}`}>
          <div className="h-full rounded-full transition-all duration-500"
            style={{ width: `${comp.uptime}%`, background: meta.color }} />
        </div>
      </div>

      {/* Last check */}
      <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
        <Clock className="w-3 h-3" />
        <span>Last check: {comp.lastCheck}</span>
      </div>
    </div>
  );
}

// ─── Custom chart tooltip ─────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label, theme }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg px-3 py-2 text-xs shadow-xl"
      style={getTooltipStyle(theme)}>
      <div className={`font-semibold mb-1 ${theme === 'dark' ? 'text-slate-300' : 'text-slate-700'}`}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex gap-2" style={{ color: p.stroke || p.color }}>
          <span>{p.name}:</span>
          <span className="font-mono font-bold">{Number(p.value).toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────
export default function Monitoring() {
  const { theme } = useTheme();
  const [alertTab, setAlertTab]   = useState('all');
  const [localAcked, setLocalAcked] = useState(new Set());

  const { data: healthData }    = useMonitoringHealth();
  const { data: alertsData }    = useAlerts();
  const { data: perfData }      = usePerformanceMetrics();
  const { data: resourceData }  = useSystemResources();
  const ackAlert                = useAcknowledgeAlert();

  // ── Normalise ──
  const rawHealth = healthData?.components || healthData;
  const health    = Array.isArray(rawHealth) ? rawHealth : fallbackHealth;

  const rawAlerts = alertsData?.alerts || alertsData;
  const baseAlerts = Array.isArray(rawAlerts) ? rawAlerts : fallbackAlerts;
  // Merge local acks so optimistic UI works without server round-trip
  const alerts    = baseAlerts.map((a) => ({ ...a, acknowledged: a.acknowledged || localAcked.has(a.id) }));

  const rawPerf = perfData?.metrics || perfData;
  const perf    = Array.isArray(rawPerf) ? rawPerf : fallbackPerf;

  const resources = (resourceData && typeof resourceData === 'object' && resourceData.cpu !== undefined)
    ? resourceData : fallbackResources;

  // ── Summary stats ──
  const totalComponents  = health.length;
  const healthyCount     = health.filter((c) => c.status === 'healthy').length;
  const issueCount       = totalComponents - healthyCount;
  const unackedCount     = alerts.filter((a) => !a.acknowledged).length;
  const criticalCount    = alerts.filter((a) => a.severity === 'critical' && !a.acknowledged).length;

  // ── Alert filter ──
  const filteredAlerts = alerts.filter((a) => {
    if (alertTab === 'unacknowledged') return !a.acknowledged;
    if (alertTab === 'critical')       return a.severity === 'critical';
    return true;
  });

  const handleAck = (id) => {
    setLocalAcked((s) => new Set([...s, id]));
    ackAlert.mutate(id);
  };

  return (
    <div className="space-y-4 animate-fade-in">

      {/* ── 1. Summary Bar ─────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-3">
        {[
          {
            label: 'Total Components',
            value: totalComponents,
            color: '#64748b',
            bg: 'rgba(100,116,139,0.08)',
            border: 'rgba(100,116,139,0.2)',
          },
          {
            label: 'Healthy',
            value: healthyCount,
            color: '#10b981',
            bg: 'rgba(16,185,129,0.08)',
            border: 'rgba(16,185,129,0.25)',
          },
          {
            label: issueCount > 0 ? 'Issues Detected' : 'No Issues',
            value: issueCount,
            color: issueCount > 0 ? '#ef4444' : '#10b981',
            bg:    issueCount > 0 ? 'rgba(239,68,68,0.08)' : 'rgba(16,185,129,0.08)',
            border: issueCount > 0 ? 'rgba(239,68,68,0.25)' : 'rgba(16,185,129,0.25)',
          },
        ].map((s) => (
          <div key={s.label}
            className="flex items-center gap-3 px-5 py-3 rounded-xl"
            style={{ background: s.bg, border: `1px solid ${s.border}` }}>
            <span className="text-2xl font-bold font-mono" style={{ color: s.color }}>{s.value}</span>
            <span className="text-sm text-slate-400 font-medium">{s.label}</span>
          </div>
        ))}
      </div>

      {/* ── 2. Component Health Grid ────────────────────────────────── */}
      <div className="rounded-xl p-4" style={getCardStyle(theme)}>
        <div className="flex items-center gap-2 mb-4">
          <Server className="w-4 h-4 text-slate-400" />
          <h3 className="text-sm font-bold text-white">Component Health</h3>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {health.map((comp) => <HealthCard key={comp.component} comp={comp} theme={theme} />)}
        </div>
      </div>

      {/* ── 3. Active Alerts Panel ──────────────────────────────────── */}
      <div className="rounded-xl overflow-hidden" style={getCardStyle(theme)}>
        {/* Tabs */}
        <div className={`flex items-center gap-1 px-4 pt-3 border-b ${theme === 'dark' ? 'border-slate-800/60' : 'border-slate-200'}`}>
          <div className="flex items-center gap-2 mr-3">
            <Activity className="w-4 h-4 text-slate-400" />
            <h3 className="text-sm font-bold text-white">Active Alerts</h3>
          </div>
          {[
            { key: 'all',            label: 'All',             count: alerts.length },
            { key: 'unacknowledged', label: 'Unacknowledged',  count: unackedCount },
            { key: 'critical',       label: 'Critical',        count: criticalCount },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setAlertTab(tab.key)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-lg border-b-2 transition-colors duration-150
                ${alertTab === tab.key
                  ? 'text-white border-blue-500 bg-blue-500/5'
                  : 'text-slate-500 border-transparent hover:text-slate-300'}`}
            >
              {tab.label}
              {tab.count > 0 && (
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full
                  ${tab.key === 'critical' && tab.count > 0
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-slate-700 text-slate-300'}`}>
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Alert rows */}
        <div className={`divide-y ${theme === 'dark' ? 'divide-slate-800/40' : 'divide-slate-200'}`}>
          {filteredAlerts.length === 0 ? (
            <div className="py-10 text-center text-sm text-slate-500">No alerts in this category</div>
          ) : filteredAlerts.map((alert) => {
            const meta = severityMeta(alert.severity);
            return (
              <div
                key={alert.id}
                className={`flex items-center gap-4 px-4 py-3 transition-opacity duration-200
                  ${alert.acknowledged ? 'opacity-50' : 'opacity-100'}`}
                style={{ borderLeft: `3px solid ${meta.border}` }}
              >
                {/* Severity badge */}
                <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full flex-shrink-0"
                  style={{ color: meta.color, background: meta.bg }}>
                  {alert.severity}
                </span>

                {/* Message */}
                <div className={`flex-1 min-w-0 ${alert.acknowledged ? 'line-through' : ''}`}>
                  <div className="text-sm text-white truncate">{alert.message}</div>
                  <div className="text-[11px] text-slate-500 mt-0.5">{alert.time}</div>
                </div>

                {/* Ack button */}
                {!alert.acknowledged && (
                  <button
                    onClick={() => handleAck(alert.id)}
                    className="flex-shrink-0 text-[11px] font-medium px-3 py-1.5 rounded-lg border border-slate-600/50 text-slate-300
                      hover:border-blue-500/50 hover:text-blue-400 hover:bg-blue-500/5 transition-all duration-150"
                  >
                    Acknowledge
                  </button>
                )}
                {alert.acknowledged && (
                  <span className="flex-shrink-0 text-[11px] text-slate-600 font-medium">Acked</span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 4. Performance Charts Row ───────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Orders/sec area chart */}
        <div className="rounded-xl p-4" style={getCardStyle(theme)}>
          <div className="mb-3">
            <h3 className="text-sm font-bold text-white">Orders / Second</h3>
            <p className="text-[11px] text-slate-500">Execution throughput over time</p>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={perf} margin={{ top: 4, right: 8, bottom: 4, left: 4 }}>
              <defs>
                <linearGradient id="blueGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.1)" />
              <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} stroke="rgba(100,116,139,0.1)" />
              <YAxis tick={{ fill: '#64748b', fontSize: 10 }} stroke="rgba(100,116,139,0.1)" width={30} />
              <Tooltip content={<ChartTooltip theme={theme} />} />
              <Area
                type="monotone"
                dataKey="ordersPerSec"
                name="Orders/sec"
                stroke="#3b82f6"
                strokeWidth={2}
                fill="url(#blueGrad)"
                dot={false}
                activeDot={{ r: 4, fill: '#3b82f6' }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Latency line chart */}
        <div className="rounded-xl p-4" style={getCardStyle(theme)}>
          <div className="mb-3">
            <h3 className="text-sm font-bold text-white">Execution Latency</h3>
            <p className="text-[11px] text-slate-500">End-to-end latency in milliseconds</p>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={perf} margin={{ top: 4, right: 8, bottom: 4, left: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.1)" />
              <XAxis dataKey="time" tick={{ fill: '#64748b', fontSize: 10 }} stroke="rgba(100,116,139,0.1)" />
              <YAxis tick={{ fill: '#64748b', fontSize: 10 }} stroke="rgba(100,116,139,0.1)" width={30} unit="ms" />
              <Tooltip content={<ChartTooltip theme={theme} />} />
              <Line
                type="monotone"
                dataKey="latency"
                name="Latency"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: '#f59e0b' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ── 5. System Resources ─────────────────────────────────────── */}
      <div className="rounded-xl p-5" style={getCardStyle(theme)}>
        <div className="flex items-center gap-2 mb-5">
          <Cpu className="w-4 h-4 text-slate-400" />
          <h3 className="text-sm font-bold text-white">System Resources</h3>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
          <CircularGauge label="CPU Usage"   value={resources.cpu}     icon={Cpu} />
          <CircularGauge label="Memory"      value={resources.memory}  icon={Server} />
          <CircularGauge label="Disk"        value={resources.disk}    icon={HardDrive} />
          <CircularGauge label="Network I/O" value={resources.network} icon={Wifi} />
        </div>
        {/* Threshold legend */}
        <div className={`flex items-center justify-center gap-6 mt-5 pt-4 border-t ${theme === 'dark' ? 'border-slate-800/50' : 'border-slate-200'} text-[11px]`}>
          {[
            { color: '#10b981', label: 'Normal (<60%)' },
            { color: '#f59e0b', label: 'Warning (60–80%)' },
            { color: '#ef4444', label: 'Critical (>80%)' },
          ].map((l) => (
            <span key={l.label} className="flex items-center gap-1.5 text-slate-500">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

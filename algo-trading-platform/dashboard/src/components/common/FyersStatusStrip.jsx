import React from 'react';
import { NavLink } from 'react-router-dom';
import { KeyRound, Gauge, Radio, Rocket } from 'lucide-react';
import { useFyersStatusStrip } from '../../hooks/useApi';

const TONE = {
  good: 'text-profit',
  warn: 'text-amber-500',
  bad: 'text-loss',
  idle: 'text-slate-500',
};

function fmtLeft(sec) {
  if (sec == null) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata',
    });
  } catch {
    return '';
  }
}

function Item({ icon: Icon, tone, title, children }) {
  return (
    <span className={`flex items-center gap-1 whitespace-nowrap ${TONE[tone]}`} title={title}>
      <Icon className="w-3 h-3 shrink-0" />
      {children}
    </span>
  );
}

/**
 * Thin status strip under the header: Fyers token, request budget, feed and
 * Fly-High arm state — the four things that decide whether Fly-High can trade.
 */
export default function FyersStatusStrip() {
  const { data, isError } = useFyersStatusStrip();

  if (isError || !data) {
    return (
      <div className="flex items-center gap-4 h-6 px-3 md:px-5 text-[10px] md:text-[11px] border-b border-terminal-border bg-terminal-bg">
        <span className={TONE.bad}>Backend unreachable — status unavailable</span>
      </div>
    );
  }

  const { token = {}, requests = {}, feed = {}, flyhigh = {} } = data;

  // Token
  const tokenTone = token.state === 'valid'
    ? (token.seconds_left != null && token.seconds_left < 3600 ? 'warn' : 'good')
    : 'bad';
  const tokenText = {
    valid: `Token valid · expires ${fmtTime(token.expires_at)}${token.seconds_left != null ? ` (${fmtLeft(token.seconds_left)})` : ''}`,
    expired: `Token expired ${fmtTime(token.expires_at)} — log in again`,
    rejected: 'Token rejected by Fyers — log in again',
    missing: 'No Fyers token — log in',
  }[token.state] || 'Token unknown';

  // Request budget
  const used = requests.requests_last_min ?? 0;
  const limit = requests.fyers_limit_per_min ?? 200;
  const soft = requests.soft_cap_per_min ?? 150;
  const hits = requests.fyers_429_last_min ?? 0;
  const held = requests.throttled_last_min ?? 0;
  const reqTone = hits > 0 ? 'bad' : used >= soft ? 'warn' : 'good';
  const pct = Math.min(100, (used / limit) * 100);
  const bp = requests.by_priority || {};

  // Feed
  const feedTone = { live: 'good', stale: 'warn', degraded: 'warn', offline: 'bad' }[feed.state] || 'idle';
  const feedText = {
    live: 'Feed live',
    stale: `Feed stale (${Math.round(feed.last_tick_age_seconds ?? 0)}s no tick)`,
    degraded: 'Feed degraded',
    offline: 'Feed offline',
  }[feed.state] || 'Feed unknown';

  // Fly-High
  const armed = flyhigh.armed || [];

  return (
    <div className="flex items-center gap-4 h-6 px-3 md:px-5 text-[10px] md:text-[11px] font-medium border-b border-terminal-border bg-terminal-bg overflow-x-auto">
      <Item icon={KeyRound} tone={tokenTone} title="Fyers access tokens expire every day at 06:00 IST">
        {token.state === 'valid' ? tokenText : <NavLink to="/broker" className="underline">{tokenText}</NavLink>}
      </Item>

      <Item
        icon={Gauge}
        tone={reqTone}
        title={`Fyers REST requests in the last 60s (plan limit ${limit}/min, 5/sec). `
          + `Orders + Fly-High: ${bp.critical ?? 0}, market data: ${bp.high ?? 0}, account reads: ${bp.low ?? 0}. `
          + `Market data stops at ${soft}/min so the rest stays free for Fly-High and orders.`}
      >
        <span className="font-mono">{used}/{limit}</span> req/min
        <span className="inline-block w-12 h-1 rounded-full bg-white/[0.08] overflow-hidden">
          <span
            className={`block h-full ${reqTone === 'good' ? 'bg-profit' : reqTone === 'warn' ? 'bg-amber-500' : 'bg-loss'}`}
            style={{ width: `${pct}%` }}
          />
        </span>
        {held > 0 && <span className={TONE.warn}>· {held} held back</span>}
        {hits > 0 && <span className={TONE.bad}>· {hits} Fyers rate-limit hits</span>}
      </Item>

      <Item icon={Radio} tone={feedTone} title="Fyers connection and WebSocket tick stream">
        {feedText}
      </Item>

      <Item icon={Rocket} tone={armed.length ? 'good' : 'idle'} title="Fly-High arms for the current trading day only">
        <NavLink to="/flyhigh">
          {armed.length ? `Fly-High armed: ${armed.join(', ')}` : 'Fly-High not armed'}
        </NavLink>
      </Item>
    </div>
  );
}

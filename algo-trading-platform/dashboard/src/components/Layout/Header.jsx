import React, { useState, useEffect } from 'react';
import { Wifi, WifiOff, Clock, Sun, Moon, Menu, LogOut, User, Crown, Sparkles, Undo2, Zap, Palette } from 'lucide-react';
import { useHealth } from '../../hooks/useApi';
import { useTheme } from '../../context/ThemeContext';
import { useUnderlying, UNDERLYINGS } from '../../context/UnderlyingContext';
import { useAuth } from '../../contexts/AuthContext';
import ModeToggle from '../common/ModeToggle';
import NotificationCenter from '../common/NotificationCenter';

function safeFormatTime(date) {
  try {
    const h = String(date.getHours()).padStart(2, '0');
    const m = String(date.getMinutes()).padStart(2, '0');
    const s = String(date.getSeconds()).padStart(2, '0');
    return `${h}:${m}:${s}`;
  } catch {
    return '--:--:--';
  }
}

export default function Header({ onMobileMenuToggle }) {
  const { data: health, isError } = useHealth();
  const { theme, toggleTheme, uiStyle, toggleUiStyle, toggleProStyle } = useTheme();
  const { underlying, setUnderlying } = useUnderlying();
  const { user, logout, isAdmin } = useAuth();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  let istTime;
  try {
    istTime = new Date(time.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
  } catch {
    istTime = time;
  }
  const hours = istTime.getHours();
  const isMarketOpen = hours >= 9 && hours < 16 && istTime.getDay() > 0 && istTime.getDay() < 6;
  const apiUp = !isError && health;
  const feedState = health?.components?.market_data_feed;
  const liveFeed = feedState === 'fyers_live';
  // Three states: live Fyers feed (green), API up but mock/no feed (amber), API down (red)
  const connState = !apiUp ? 'down' : liveFeed ? 'live' : 'mock';
  const connLabel = connState === 'live' ? 'Live Data' : connState === 'mock' ? 'Mock Data' : 'Disconnected';
  const connected = liveFeed;

  return (
    <header
      className="flex items-center justify-between h-12 px-3 md:px-5 border-b border-terminal-border"
      style={{
        background: theme === 'dark' ? 'rgba(19, 23, 32, 0.8)' : 'rgba(255, 255, 255, 0.88)',
        backdropFilter: 'blur(24px)',
        WebkitBackdropFilter: 'blur(24px)',
        ...(theme === 'light' ? { boxShadow: '0 1px 8px rgba(0,0,0,0.05)' } : {}),
      }}
    >
      <div className="flex items-center gap-2 md:gap-4">
        {/* Mobile hamburger */}
        <button
          onClick={onMobileMenuToggle}
          className="flex md:hidden items-center justify-center w-8 h-8 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors"
        >
          <Menu className="w-5 h-5" />
        </button>

        <h1 className="text-xs md:text-sm font-semibold text-slate-300 tracking-wide uppercase hidden sm:block">
          Algo Trading Platform
        </h1>
        <span
          className={`flex items-center gap-1 md:gap-1.5 text-[10px] md:text-xs font-medium px-2 md:px-2.5 py-1 rounded-full ${
            isMarketOpen
              ? 'bg-profit/10 text-profit'
              : 'bg-slate-700/50 text-slate-400'
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${isMarketOpen ? 'bg-profit animate-pulse' : 'bg-slate-500'}`} />
          <span className="hidden xs:inline">{isMarketOpen ? 'MARKET OPEN' : 'MARKET CLOSED'}</span>
          <span className="xs:hidden">{isMarketOpen ? 'OPEN' : 'CLOSED'}</span>
        </span>

        {/* Underlying selector */}
        <div className="hidden md:flex items-center rounded-lg overflow-hidden border border-white/[0.06]"
          style={{ background: 'rgba(255,255,255,0.03)' }}>
          {UNDERLYINGS.map(sym => {
            const short = { MIDCPNIFTY: 'MIDCP', BANKNIFTY: 'BANK', FINNIFTY: 'FIN', NIFTY: 'NIFTY', SENSEX: 'SENSEX' }[sym] || sym;
            const active = underlying === sym;
            return (
              <button
                key={sym}
                onClick={() => setUnderlying(sym)}
                className={`px-2 py-1 text-[10px] font-bold tracking-wider transition-all ${
                  active
                    ? 'bg-accent/20 text-accent'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.04]'
                }`}
                title={sym}
              >
                {short}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-2 md:gap-4">
        {/* Live / Paper trading mode toggle */}
        <ModeToggle />

        <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-400">
          <Clock className="w-3.5 h-3.5" />
          <span className="font-mono">{safeFormatTime(istTime)} IST</span>
        </div>

        <div
          className={`flex items-center gap-1 md:gap-1.5 text-[10px] md:text-xs font-medium px-2 md:px-2.5 py-1 rounded-full ${
            connState === 'live'
              ? 'bg-profit/10 text-profit'
              : connState === 'mock'
              ? 'bg-amber-500/10 text-amber-500'
              : 'bg-loss/10 text-loss'
          }`}
          title={
            connState === 'live'
              ? 'Fyers live feed connected — real market data'
              : connState === 'mock'
              ? 'Fyers feed NOT connected — showing simulated data. Reconnect in Settings → API Keys.'
              : 'Backend unreachable'
          }
        >
          {connState === 'live' ? <Wifi className="w-3 md:w-3.5 h-3 md:h-3.5" /> : <WifiOff className="w-3 md:w-3.5 h-3 md:h-3.5" />}
          <span className="hidden sm:inline">{connLabel}</span>
        </div>

        {/* Notification Center */}
        <NotificationCenter />

        {/* Switch to Latest UI — Classic ↔ Pro */}
        <button
          onClick={toggleProStyle}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold transition-all duration-300 ${
            uiStyle === 'pro'
              ? 'bg-[#38BDF8]/10 text-[#38BDF8] border border-[#38BDF8]/25 shadow-[0_0_12px_rgba(56,189,248,0.15)] hover:shadow-[0_0_20px_rgba(56,189,248,0.25)]'
              : 'bg-white/[0.04] text-slate-400 border border-transparent hover:bg-white/[0.08] hover:text-slate-200'
          }`}
          title={uiStyle === 'pro' ? 'Switch to Classic UI' : 'Switch to Latest UI'}
        >
          {uiStyle === 'pro' ? (
            <>
              <Undo2 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Classic</span>
            </>
          ) : (
            <>
              <Zap className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Latest UI</span>
            </>
          )}
        </button>

        {/* Sci-Fi Toggle */}
        <button
          onClick={toggleUiStyle}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold transition-all duration-300 ${
            uiStyle === 'scifi'
              ? 'bg-[#00f0ff]/10 text-[#00f0ff] border border-[#00f0ff]/25 shadow-[0_0_12px_rgba(0,240,255,0.15)] hover:shadow-[0_0_20px_rgba(0,240,255,0.25)]'
              : 'bg-white/[0.04] text-slate-400 border border-transparent hover:bg-white/[0.08] hover:text-slate-200'
          }`}
          title={uiStyle === 'scifi' ? 'Switch to Classic UI' : 'Switch to Sci-Fi UI'}
        >
          {uiStyle === 'scifi' ? (
            <>
              <Undo2 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Classic</span>
            </>
          ) : (
            <>
              <Sparkles className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Sci-Fi</span>
            </>
          )}
        </button>

        {/* Dark/Light Toggle (disabled when scifi or pro is active) */}
        <button
          onClick={toggleTheme}
          className={`flex items-center justify-center w-8 h-8 rounded-xl transition-all ${
            uiStyle !== 'classic'
              ? 'bg-white/[0.02] text-slate-600 cursor-not-allowed opacity-40'
              : 'bg-white/[0.04] hover:bg-white/[0.08] text-slate-400 hover:text-accent'
          }`}
          title={uiStyle !== 'classic' ? 'Dark/Light toggle disabled in themed mode' : theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          disabled={uiStyle !== 'classic'}
        >
          {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        {/* User menu */}
        {user && (
          <div className="relative">
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-white/[0.06] transition-colors"
            >
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold ${
                isAdmin ? 'bg-accent/20 text-accent' : 'bg-blue-500/20 text-blue-400'
              }`}>
                {isAdmin ? <Crown className="w-3.5 h-3.5" /> : user.name?.charAt(0)?.toUpperCase() || 'U'}
              </div>
              <span className="hidden md:block text-xs text-slate-300 font-medium max-w-[80px] truncate">
                {user.name}
              </span>
            </button>
            {showUserMenu && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowUserMenu(false)} />
                <div className="absolute right-0 top-full mt-1 z-50 w-56 rounded-xl border border-terminal-border shadow-2xl overflow-hidden"
                  style={{ background: theme === 'dark' ? 'rgba(19,23,32,0.95)' : 'rgba(255,255,255,0.95)', backdropFilter: 'blur(20px)', ...(theme === 'light' ? { boxShadow: '0 8px 30px rgba(0,0,0,0.12)', borderColor: 'rgba(0,0,0,0.1)' } : {}) }}>
                  <div className="px-4 py-3 border-b border-terminal-border">
                    <div className="text-sm font-semibold text-white">{user.name}</div>
                    <div className="text-xs text-slate-500 truncate">{user.email}</div>
                    <div className="mt-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-accent/10 text-accent">
                      {user.plan} plan
                    </div>
                  </div>
                  <div className="p-1.5">
                    <button
                      onClick={() => { setShowUserMenu(false); logout(); }}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-red-400 hover:bg-red-500/10 transition-colors"
                    >
                      <LogOut className="w-4 h-4" />
                      Sign Out
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </header>
  );
}

import React, { useState, useEffect } from 'react';
import { Wifi, WifiOff, Clock, Sun, Moon, Menu, LogOut, User, Crown } from 'lucide-react';
import { useHealth } from '../../hooks/useApi';
import { useTheme } from '../../context/ThemeContext';
import { useAuth } from '../../contexts/AuthContext';
import ModeToggle from '../common/ModeToggle';

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
  const { theme, toggleTheme } = useTheme();
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
  const connected = !isError && health;

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
      </div>

      <div className="flex items-center gap-2 md:gap-4">
        {/* Live / Paper trading mode toggle */}
        <ModeToggle />

        <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-400">
          <Clock className="w-3.5 h-3.5" />
          <span className="font-mono">{safeFormatTime(istTime)} IST</span>
        </div>

        <div className={`flex items-center gap-1 md:gap-1.5 text-[10px] md:text-xs font-medium px-2 md:px-2.5 py-1 rounded-full ${
          connected ? 'bg-profit/10 text-profit' : 'bg-loss/10 text-loss'
        }`}>
          {connected ? <Wifi className="w-3 md:w-3.5 h-3 md:h-3.5" /> : <WifiOff className="w-3 md:w-3.5 h-3 md:h-3.5" />}
          <span className="hidden sm:inline">{connected ? 'Connected' : 'Disconnected'}</span>
        </div>

        <button
          onClick={toggleTheme}
          className="flex items-center justify-center w-8 h-8 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-400 hover:text-accent transition-all"
          title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
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

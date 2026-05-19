import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  BarChart3,
  Briefcase,
  Bot,
  ShieldAlert,
  FileText,
  FlaskConical,
  Activity,
  ChevronLeft,
  ChevronRight,
  TrendingUp,
  IndianRupee,
  Settings,
  FlaskRound,
  CandlestickChart,
  Wrench,
  X,
  Flame,
  Brain,
} from 'lucide-react';
import { useTheme } from '../../context/ThemeContext';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/market', icon: BarChart3, label: 'Market Data' },
  { to: '/charts', icon: CandlestickChart, label: 'Charts' },
  { to: '/portfolio', icon: Briefcase, label: 'Portfolio' },
  { to: '/strategies', icon: Bot, label: 'Strategies' },
  { to: '/builder', icon: Wrench, label: 'Builder' },
  { to: '/risk', icon: ShieldAlert, label: 'Risk' },
  { to: '/orders', icon: FileText, label: 'Orders' },
  { to: '/backtest', icon: FlaskConical, label: 'Backtest' },
  { to: '/pnl', icon: IndianRupee, label: 'P&L Analytics' },
  { to: '/iv-surface', icon: Flame, label: 'IV Surface' },
  { to: '/paper', icon: FlaskRound, label: 'Paper Trading' },
  { to: '/ai-signals', icon: Brain, label: 'AI Signals' },
  { to: '/monitoring', icon: Activity, label: 'Monitoring' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export default function Sidebar({ collapsed, onToggle, mobileOpen, onMobileClose }) {
  const { theme } = useTheme();
  return (
    <>
      {/* Mobile backdrop overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={onMobileClose}
        />
      )}

      <aside
        className={`
          flex flex-col border-r border-terminal-border
          ${collapsed ? 'md:w-16' : 'md:w-56'}
          fixed md:relative z-50 h-full w-64 md:w-auto
          mobile-sidebar ${mobileOpen ? 'mobile-sidebar-open' : ''}
        `}
        style={{
          background: theme === 'dark' ? 'rgba(19, 23, 32, 0.95)' : 'rgba(255, 255, 255, 0.92)',
          backdropFilter: 'blur(24px)',
          WebkitBackdropFilter: 'blur(24px)',
          ...(theme === 'light' ? { boxShadow: '2px 0 16px rgba(0,0,0,0.06)', borderColor: 'rgba(0,0,0,0.08)' } : {}),
        }}
      >
        {/* Logo + mobile close */}
        <div className="flex items-center justify-between h-14 px-3 border-b border-terminal-border">
          <div className="flex items-center">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-accent to-accent-light flex items-center justify-center shadow-lg flex-shrink-0">
              <TrendingUp className="w-4.5 h-4.5 text-white" />
            </div>
            {(!collapsed || mobileOpen) && (
              <span className="ml-2.5 text-lg font-bold text-white tracking-tight">
                SmartAlgo
              </span>
            )}
          </div>
          {/* Close button on mobile */}
          <button
            onClick={onMobileClose}
            className="flex md:hidden items-center justify-center w-8 h-8 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-3 space-y-0.5 overflow-y-auto">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              onClick={onMobileClose}
              className={({ isActive }) =>
                `flex items-center px-3 py-2.5 mx-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                  isActive
                    ? 'bg-accent/15 text-accent border-l-2 border-accent shadow-sm shadow-accent/5'
                    : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
                }`
              }
            >
              <Icon className="w-5 h-5 flex-shrink-0" />
              {(!collapsed || mobileOpen) && <span className="ml-3">{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Collapse toggle — desktop only */}
        <button
          onClick={onToggle}
          className="hidden md:flex items-center justify-center h-10 border-t border-terminal-border text-slate-500 hover:text-accent transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
      </aside>
    </>
  );
}

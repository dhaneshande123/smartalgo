import React from 'react';
import { Link } from 'react-router-dom';
import {
  TrendingUp,
  BarChart3,
  Shield,
  Zap,
  ArrowRight,
  Activity,
  Layers,
  LineChart,
  Target,
  Globe,
  ChevronRight,
  Star,
  Sparkles,
  Check,
} from 'lucide-react';

const FEATURES = [
  { icon: Activity, title: 'Real-Time Market Data', desc: 'Live streaming quotes, option chains & indices from NSE via Fyers API with sub-second latency.', gradient: 'linear-gradient(135deg, #3b82f6, #60a5fa)' },
  { icon: LineChart, title: 'Advanced Charting', desc: 'Candlestick charts with SMA, EMA, Bollinger Bands, RSI, MACD and multi-timeframe analysis.', gradient: 'linear-gradient(135deg, #22c55e, #4ade80)' },
  { icon: Layers, title: 'Strategy Builder', desc: 'Build multi-leg option strategies with visual payoff diagrams, risk params & scheduling.', gradient: 'linear-gradient(135deg, #a855f7, #c084fc)' },
  { icon: Shield, title: 'Risk Management', desc: 'Portfolio Greeks, circuit breakers, kill switch & real-time margin monitoring.', gradient: 'linear-gradient(135deg, #ef4444, #f87171)' },
  { icon: Target, title: 'Paper Trading', desc: 'Test strategies risk-free with simulated execution before deploying real capital.', gradient: 'linear-gradient(135deg, #eab308, #facc15)' },
  { icon: BarChart3, title: 'IV Surface Heatmap', desc: 'Interactive implied volatility surface with smile curves and term structure analysis.', gradient: 'linear-gradient(135deg, #06b6d4, #22d3ee)' },
];

const STATS = [
  { value: '50+', label: 'Trading Strategies' },
  { value: '<1ms', label: 'Signal Latency' },
  { value: '99.9%', label: 'Uptime SLA' },
  { value: '24/5', label: 'Market Coverage' },
];

const PRICING = [
  {
    name: 'Starter', price: 'Free', period: '', tier: 'starter',
    features: ['Delayed market data', 'Basic dashboard', '1 paper strategy', 'Option chain viewer', 'Community support'],
    cta: 'Get Started',
    style: { border: 'rgba(100,116,139,0.2)', gradient: 'linear-gradient(135deg, #475569, #64748b)' },
  },
  {
    name: 'Pro', price: '2,999', period: '/mo', tier: 'pro', popular: true,
    features: ['Real-time data', 'Advanced charts & indicators', '5 paper strategies', 'Strategy builder & IV surface', 'P&L analytics', 'Email support'],
    cta: 'Start Free Trial',
    style: { border: 'rgba(99,102,241,0.3)', gradient: 'linear-gradient(135deg, #6366f1, #818cf8)' },
  },
  {
    name: 'Enterprise', price: '9,999', period: '/mo', tier: 'enterprise',
    features: ['Everything in Pro', 'Unlimited strategies', 'Live trading integration', 'Risk management suite', 'Custom backtesting', 'Priority support & API'],
    cta: 'Contact Sales',
    style: { border: 'rgba(124,58,237,0.3)', gradient: 'linear-gradient(135deg, #7c3aed, #a855f7)' },
  },
];

export default function Landing() {
  return (
    <div className="auth-page min-h-screen bg-[#060910] overflow-x-hidden" style={{ color: '#e2e8f0' }}>
      {/* ── Navbar ───────────────────────────────────────── */}
      <nav className="fixed top-0 left-0 right-0 z-50" style={{
        background: 'rgba(6, 9, 16, 0.8)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
      }}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #7c3aed, #a855f7, #6366f1)' }}>
              <Zap className="w-4 h-4" style={{ color: '#fff' }} />
            </div>
            <span className="text-lg font-bold" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>SmartAlgo</span>
          </div>
          <div className="hidden md:flex items-center gap-8 text-sm" style={{ color: '#94a3b8' }}>
            <a href="#features" className="hover:text-white transition-colors">Features</a>
            <a href="#stats" className="hover:text-white transition-colors">Performance</a>
            <a href="#pricing" className="hover:text-white transition-colors">Pricing</a>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/login" className="px-5 py-2 text-sm font-medium transition-colors" style={{ color: '#cbd5e1' }}>
              Log In
            </Link>
            <Link to="/signup"
              className="px-5 py-2.5 text-sm font-semibold rounded-lg transition-all"
              style={{
                background: 'linear-gradient(135deg, #7c3aed, #6366f1)',
                color: '#fff',
                boxShadow: '0 2px 12px rgba(124,58,237,0.3)',
              }}>
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* ── Hero ─────────────────────────────────────────── */}
      <section className="relative pt-36 pb-24 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto">
        {/* Ambient glows */}
        <div className="auth-glow absolute top-16 left-[20%] w-[500px] h-[500px] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.12) 0%, transparent 70%)' }} />
        <div className="auth-glow absolute top-32 right-[15%] w-[400px] h-[400px] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 70%)', animationDelay: '2s' }} />

        <div className="relative text-center max-w-4xl mx-auto">
          <div className="auth-fade-up inline-flex items-center gap-2 px-4 py-2 rounded-full mb-8"
            style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.2)' }}>
            <Sparkles className="w-3.5 h-3.5" style={{ color: '#a855f7' }} />
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: '#a855f7' }}>
              Institutional-Grade Algo Trading
            </span>
          </div>

          <h1 className="auth-fade-up auth-delay-1 text-4xl sm:text-5xl lg:text-7xl font-extrabold leading-[1.05] tracking-tight"
            style={{ color: '#ffffff' }}>
            Algorithmic Options
            <br />
            <span style={{
              background: 'linear-gradient(135deg, #a855f7, #6366f1, #38bdf8)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}>
              Trading Platform
            </span>
          </h1>

          <p className="auth-fade-up auth-delay-2 mt-7 text-lg sm:text-xl max-w-2xl mx-auto leading-relaxed" style={{ color: '#94a3b8' }}>
            Deploy sophisticated options strategies on the Indian stock market
            with real-time data, advanced analytics, and institutional risk controls.
          </p>

          <div className="auth-fade-up auth-delay-3 mt-12 flex flex-col sm:flex-row items-center justify-center gap-4">
            <Link to="/signup"
              className="auth-gradient-btn group flex items-center gap-2.5 px-9 py-4 rounded-xl font-semibold text-base transition-all duration-300"
              style={{
                background: 'linear-gradient(135deg, #7c3aed, #6366f1, #7c3aed)',
                backgroundSize: '200% 200%',
                color: '#fff',
                boxShadow: '0 4px 24px rgba(124,58,237,0.35), 0 0 60px rgba(124,58,237,0.1)',
              }}>
              Start Free Trial
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
            </Link>
            <Link to="/login"
              className="flex items-center gap-2.5 px-9 py-4 rounded-xl font-medium text-base transition-all duration-300"
              style={{
                border: '1px solid rgba(100,116,139,0.25)',
                color: '#cbd5e1',
                background: 'rgba(255,255,255,0.02)',
              }}>
              <Globe className="w-4 h-4" style={{ color: '#94a3b8' }} />
              Live Demo
            </Link>
          </div>
        </div>

        {/* Dashboard preview mockup */}
        <div className="auth-fade-up auth-delay-4 relative mt-20 max-w-5xl mx-auto">
          <div className="rounded-2xl overflow-hidden relative auth-shimmer"
            style={{
              background: 'linear-gradient(135deg, rgba(15,23,42,0.8) 0%, rgba(10,15,25,0.9) 100%)',
              border: '1px solid rgba(100,116,139,0.12)',
              boxShadow: '0 20px 60px rgba(0,0,0,0.4), 0 0 80px rgba(124,58,237,0.06)',
            }}>
            {/* Title bar */}
            <div className="flex items-center gap-2 px-5 py-3.5" style={{ borderBottom: '1px solid rgba(100,116,139,0.1)' }}>
              <div className="flex gap-2">
                <div className="w-3 h-3 rounded-full" style={{ background: '#ef4444' }} />
                <div className="w-3 h-3 rounded-full" style={{ background: '#eab308' }} />
                <div className="w-3 h-3 rounded-full" style={{ background: '#22c55e' }} />
              </div>
              <div className="ml-4 flex-1 h-6 rounded-md max-w-xs" style={{ background: 'rgba(255,255,255,0.04)' }} />
            </div>
            {/* Content */}
            <div className="p-6 space-y-4">
              <div className="grid grid-cols-4 gap-4">
                {[
                  { name: 'NIFTY', val: '23,848', chg: '+0.42%', up: true },
                  { name: 'BANKNIFTY', val: '55,524', chg: '-0.18%', up: false },
                  { name: 'FINNIFTY', val: '26,020', chg: '+0.31%', up: true },
                  { name: 'VIX', val: '14.52', chg: '-2.10%', up: false },
                ].map((idx) => (
                  <div key={idx.name} className="rounded-xl p-4"
                    style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(100,116,139,0.08)' }}>
                    <div className="text-[11px] font-semibold mb-1.5" style={{ color: '#64748b' }}>{idx.name}</div>
                    <div className="text-lg font-bold" style={{ color: '#f1f5f9' }}>{idx.val}</div>
                    <div className="text-xs mt-1 font-medium" style={{ color: idx.up ? '#22c55e' : '#ef4444' }}>
                      {idx.chg}
                    </div>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div className="col-span-2 h-48 rounded-xl flex items-center justify-center"
                  style={{ background: 'rgba(255,255,255,0.015)', border: '1px solid rgba(100,116,139,0.06)' }}>
                  <TrendingUp className="w-16 h-16" style={{ color: 'rgba(100,116,139,0.15)' }} />
                </div>
                <div className="h-48 rounded-xl flex items-center justify-center"
                  style={{ background: 'rgba(255,255,255,0.015)', border: '1px solid rgba(100,116,139,0.06)' }}>
                  <BarChart3 className="w-12 h-12" style={{ color: 'rgba(100,116,139,0.15)' }} />
                </div>
              </div>
            </div>
          </div>
          {/* Bottom glow */}
          <div className="absolute -bottom-6 left-[20%] right-[20%] h-12 rounded-full pointer-events-none"
            style={{ background: 'radial-gradient(ellipse, rgba(124,58,237,0.15) 0%, transparent 70%)' }} />
        </div>
      </section>

      {/* ── Stats ────────────────────────────────────────── */}
      <section id="stats" className="py-20" style={{ borderTop: '1px solid rgba(100,116,139,0.08)', borderBottom: '1px solid rgba(100,116,139,0.08)' }}>
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 grid grid-cols-2 md:grid-cols-4 gap-8">
          {STATS.map((s) => (
            <div key={s.label} className="text-center">
              <div className="text-4xl sm:text-5xl font-extrabold mb-2" style={{
                background: 'linear-gradient(135deg, #a855f7, #6366f1, #38bdf8)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                letterSpacing: '-0.03em',
              }}>
                {s.value}
              </div>
              <div className="text-sm font-medium" style={{ color: '#64748b' }}>{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ─────────────────────────────────────── */}
      <section id="features" className="py-24 px-4 sm:px-6 lg:px-8 max-w-7xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-3xl sm:text-4xl font-bold mb-4" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
            Everything You Need to{' '}
            <span style={{
              background: 'linear-gradient(135deg, #a855f7, #6366f1, #38bdf8)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}>Trade Smarter</span>
          </h2>
          <p className="text-base max-w-xl mx-auto" style={{ color: '#64748b' }}>
            Professional-grade tools designed for systematic options trading on Indian markets.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {FEATURES.map((f) => (
            <div key={f.title} className="group rounded-2xl p-6 lg:p-7 transition-all duration-400"
              style={{
                background: 'rgba(15, 23, 42, 0.4)',
                border: '1px solid rgba(100,116,139,0.1)',
                backdropFilter: 'blur(8px)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'rgba(100,116,139,0.2)';
                e.currentTarget.style.background = 'rgba(15, 23, 42, 0.6)';
                e.currentTarget.style.transform = 'translateY(-4px)';
                e.currentTarget.style.boxShadow = '0 16px 50px rgba(0,0,0,0.25)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'rgba(100,116,139,0.1)';
                e.currentTarget.style.background = 'rgba(15, 23, 42, 0.4)';
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = 'none';
              }}>
              <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5"
                style={{ background: f.gradient, boxShadow: `0 4px 16px ${f.gradient.includes('#3b82f6') ? 'rgba(59,130,246,0.2)' : 'rgba(124,58,237,0.15)'}` }}>
                <f.icon className="w-5 h-5" style={{ color: '#fff' }} />
              </div>
              <h3 className="text-lg font-semibold mb-2.5" style={{ color: '#f1f5f9' }}>{f.title}</h3>
              <p className="text-sm leading-relaxed" style={{ color: '#94a3b8' }}>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Pricing ──────────────────────────────────────── */}
      <section id="pricing" className="py-24 px-4 sm:px-6 lg:px-8 max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-3xl sm:text-4xl font-bold mb-4" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
            Simple, Transparent{' '}
            <span style={{
              background: 'linear-gradient(135deg, #a855f7, #6366f1, #38bdf8)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}>Pricing</span>
          </h2>
          <p className="text-base" style={{ color: '#64748b' }}>Start free. Upgrade when you&apos;re ready.</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 lg:gap-6">
          {PRICING.map((plan) => (
            <div key={plan.name} className="relative rounded-2xl p-6 lg:p-7 transition-all duration-400 group"
              style={{
                background: 'rgba(15, 23, 42, 0.5)',
                border: `1.5px solid ${plan.style.border}`,
                backdropFilter: 'blur(12px)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-4px)';
                e.currentTarget.style.boxShadow = '0 20px 50px rgba(0,0,0,0.3)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.boxShadow = 'none';
              }}>
              {plan.popular && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-4 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider"
                  style={{ background: 'linear-gradient(135deg, #6366f1, #818cf8)', color: '#fff', boxShadow: '0 2px 12px rgba(99,102,241,0.4)' }}>
                  Most Popular
                </div>
              )}

              <div className="text-sm font-semibold mb-2" style={{ color: '#94a3b8' }}>{plan.name}</div>
              <div className="flex items-baseline gap-1 mb-7">
                {plan.price !== 'Free' && <span className="text-sm" style={{ color: '#64748b' }}>₹</span>}
                <span className="text-4xl font-extrabold" style={{ color: '#ffffff', letterSpacing: '-0.03em' }}>{plan.price}</span>
                {plan.period && <span className="text-sm" style={{ color: '#475569' }}>{plan.period}</span>}
              </div>
              <ul className="space-y-3 mb-8">
                {plan.features.map((f) => (
                  <li key={f} className="flex items-start gap-2.5">
                    <Check className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#a855f7' }} />
                    <span className="text-[13px]" style={{ color: '#cbd5e1' }}>{f}</span>
                  </li>
                ))}
              </ul>
              <Link to="/signup"
                className="block w-full text-center py-3.5 rounded-xl font-semibold text-sm transition-all duration-300"
                style={
                  plan.popular
                    ? {
                        background: 'linear-gradient(135deg, #6366f1, #818cf8)',
                        color: '#fff',
                        boxShadow: '0 4px 16px rgba(99,102,241,0.3)',
                      }
                    : {
                        border: `1px solid ${plan.style.border}`,
                        color: '#cbd5e1',
                        background: 'rgba(255,255,255,0.02)',
                      }
                }>
                {plan.cta}
              </Link>
            </div>
          ))}
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────── */}
      <footer style={{ borderTop: '1px solid rgba(100,116,139,0.08)' }} className="py-12 px-4 sm:px-6 lg:px-8">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #7c3aed, #a855f7)' }}>
              <Zap className="w-3 h-3" style={{ color: '#fff' }} />
            </div>
            <span className="text-sm font-semibold" style={{ color: '#64748b' }}>SmartAlgo Trading Platform</span>
          </div>
          <div className="text-xs" style={{ color: '#334155' }}>
            Built for Indian markets. Not financial advice. Trade at your own risk.
          </div>
        </div>
      </footer>
    </div>
  );
}

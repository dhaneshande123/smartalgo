import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import {
  Zap,
  Mail,
  Lock,
  Eye,
  EyeOff,
  ArrowRight,
  AlertCircle,
  Shield,
  TrendingUp,
  Activity,
} from 'lucide-react';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email || !password) {
      setError('Please fill in all fields');
      return;
    }
    setLoading(true);
    try {
      const result = await login(email, password);
      if (result.success) {
        navigate('/', { replace: true });
      } else {
        setError(result.error);
      }
    } catch (err) {
      setError('Login failed. Please try again.');
    }
    setLoading(false);
  };

  return (
    <div className="auth-page min-h-screen flex bg-[#060910] relative overflow-hidden">
      {/* ── Ambient background glows ──────────────────────── */}
      <div
        className="auth-glow absolute top-[-10%] left-[15%] w-[600px] h-[600px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.12) 0%, transparent 70%)' }}
      />
      <div
        className="auth-glow absolute bottom-[-10%] right-[10%] w-[500px] h-[500px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 70%)', animationDelay: '2s' }}
      />
      <div
        className="absolute top-[30%] right-[30%] w-[300px] h-[300px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(139,92,246,0.06) 0%, transparent 70%)' }}
      />

      {/* ── Subtle grid pattern ───────────────────────────── */}
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.03]"
        style={{
          backgroundImage: `linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px),
                            linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)`,
          backgroundSize: '60px 60px',
        }}
      />

      {/* ── Left panel (branding) ────────────────────────── */}
      <div className="hidden lg:flex lg:w-[48%] relative flex-col justify-between p-12 xl:p-16">
        <div className="auth-fade-up">
          <Link to="/landing" className="flex items-center gap-3 mb-20">
            <div
              className="w-11 h-11 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #7c3aed 0%, #a855f7 50%, #6366f1 100%)' }}
            >
              <Zap className="w-5 h-5" style={{ color: '#ffffff' }} />
            </div>
            <span className="text-xl font-bold" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>SmartAlgo</span>
          </Link>

          <h1 className="text-5xl xl:text-6xl font-extrabold leading-[1.1] mb-6" style={{ color: '#ffffff', letterSpacing: '-0.03em' }}>
            Welcome back,
            <br />
            <span
              style={{
                background: 'linear-gradient(135deg, #a855f7, #6366f1, #38bdf8)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}
            >
              Trader.
            </span>
          </h1>
          <p className="text-lg xl:text-xl leading-relaxed max-w-lg" style={{ color: '#94a3b8' }}>
            Access your algorithmic trading dashboard with real-time market data, strategy management, and portfolio analytics.
          </p>
        </div>

        {/* Feature cards */}
        <div className="auth-fade-up auth-delay-3 space-y-3 mb-8">
          {[
            { icon: Shield, text: 'Bank-grade encryption', sub: '256-bit AES' },
            { icon: Activity, text: 'Real-time streaming', sub: 'Fyers API v3' },
            { icon: TrendingUp, text: 'Algo execution', sub: '<1ms latency' },
          ].map(({ icon: Icon, text, sub }) => (
            <div
              key={text}
              className="flex items-center gap-4 px-5 py-3.5 rounded-xl"
              style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.06)',
              }}
            >
              <div
                className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{ background: 'rgba(124,58,237,0.15)' }}
              >
                <Icon className="w-4 h-4" style={{ color: '#a855f7' }} />
              </div>
              <div>
                <div className="text-sm font-semibold" style={{ color: '#e2e8f0' }}>{text}</div>
                <div className="text-xs" style={{ color: '#64748b' }}>{sub}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Right panel (form) ───────────────────────────── */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8 lg:p-12">
        <div className="w-full max-w-[420px]">
          {/* Mobile logo */}
          <div className="lg:hidden auth-fade-up flex items-center gap-3 mb-10">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #7c3aed 0%, #a855f7 50%, #6366f1 100%)' }}
            >
              <Zap className="w-4.5 h-4.5" style={{ color: '#ffffff' }} />
            </div>
            <span className="text-lg font-bold" style={{ color: '#ffffff' }}>SmartAlgo</span>
          </div>

          {/* Heading */}
          <div className="auth-fade-up auth-delay-1">
            <h2 className="text-3xl font-bold mb-2" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
              Sign in to your account
            </h2>
            <p className="text-[15px] mb-8" style={{ color: '#64748b' }}>
              Don&apos;t have an account?{' '}
              <Link
                to="/signup"
                className="font-semibold transition-colors hover:underline"
                style={{ color: '#a855f7' }}
              >
                Create one free
              </Link>
            </p>
          </div>

          {/* Error message */}
          {error && (
            <div
              className="auth-scale-in flex items-center gap-3 px-4 py-3.5 rounded-xl mb-6"
              style={{
                background: 'rgba(239, 68, 68, 0.08)',
                border: '1px solid rgba(239, 68, 68, 0.2)',
              }}
            >
              <AlertCircle className="w-4.5 h-4.5 flex-shrink-0" style={{ color: '#f87171' }} />
              <span className="text-sm font-medium" style={{ color: '#fca5a5' }}>{error}</span>
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="auth-fade-up auth-delay-2 space-y-5">
            {/* Email */}
            <div>
              <label className="block text-[11px] font-bold uppercase tracking-[0.1em] mb-2.5" style={{ color: '#94a3b8' }}>
                Email Address
              </label>
              <div className="relative group">
                <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px] transition-colors" style={{ color: '#475569' }} />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="auth-input w-full pl-12 pr-4 py-3.5 rounded-xl text-[15px] outline-none"
                  autoComplete="email"
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <label className="block text-[11px] font-bold uppercase tracking-[0.1em] mb-2.5" style={{ color: '#94a3b8' }}>
                Password
              </label>
              <div className="relative group">
                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px] transition-colors" style={{ color: '#475569' }} />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  className="auth-input w-full pl-12 pr-12 py-3.5 rounded-xl text-[15px] outline-none"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-4 top-1/2 -translate-y-1/2 transition-colors hover:opacity-80"
                  style={{ color: '#64748b' }}
                >
                  {showPassword ? <EyeOff className="w-[18px] h-[18px]" /> : <Eye className="w-[18px] h-[18px]" />}
                </button>
              </div>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="auth-gradient-btn w-full flex items-center justify-center gap-2.5 py-3.5 rounded-xl font-semibold text-[15px] transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed"
              style={{
                background: 'linear-gradient(135deg, #7c3aed, #6366f1, #7c3aed)',
                backgroundSize: '200% 200%',
                color: '#ffffff',
                boxShadow: '0 4px 20px rgba(124, 58, 237, 0.3), 0 0 40px rgba(124, 58, 237, 0.1)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.boxShadow = '0 6px 30px rgba(124, 58, 237, 0.45), 0 0 60px rgba(124, 58, 237, 0.15)';
                e.currentTarget.style.transform = 'translateY(-1px)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = '0 4px 20px rgba(124, 58, 237, 0.3), 0 0 40px rgba(124, 58, 237, 0.1)';
                e.currentTarget.style.transform = 'translateY(0)';
              }}
            >
              {loading ? (
                <div
                  className="w-5 h-5 border-2 rounded-full animate-spin"
                  style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#ffffff' }}
                />
              ) : (
                <>
                  Sign In
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          {/* Divider */}
          <div className="auth-fade-up auth-delay-3 flex items-center gap-4 my-8">
            <div className="flex-1 h-px" style={{ background: 'rgba(100,116,139,0.15)' }} />
            <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: '#475569' }}>Demo Access</span>
            <div className="flex-1 h-px" style={{ background: 'rgba(100,116,139,0.15)' }} />
          </div>

          {/* Demo info */}
          <div
            className="auth-fade-up auth-delay-4 auth-glass-card rounded-xl p-5 relative overflow-hidden auth-shimmer"
          >
            <div className="relative z-10">
              <div className="flex items-center gap-2 mb-3.5">
                <div
                  className="w-6 h-6 rounded-md flex items-center justify-center"
                  style={{ background: 'rgba(124,58,237,0.15)' }}
                >
                  <Shield className="w-3 h-3" style={{ color: '#a855f7' }} />
                </div>
                <span className="text-[11px] font-bold uppercase tracking-[0.1em]" style={{ color: '#a855f7' }}>
                  Demo Access
                </span>
              </div>
              <p className="text-xs" style={{ color: '#64748b' }}>
                Use your registered credentials to sign in. Contact your admin for access.
              </p>
            </div>
          </div>

          {/* Footer text */}
          <p className="auth-fade-up auth-delay-5 text-center mt-8 text-xs" style={{ color: '#334155' }}>
            Protected by 256-bit encryption. Your data is safe.
          </p>
        </div>
      </div>
    </div>
  );
}

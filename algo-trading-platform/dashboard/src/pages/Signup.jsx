import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth, PLANS } from '../contexts/AuthContext';
import {
  Zap,
  Mail,
  Lock,
  User,
  Eye,
  EyeOff,
  ArrowRight,
  ArrowLeft,
  AlertCircle,
  Check,
  ChevronRight,
  Crown,
  Star,
  Sparkles,
} from 'lucide-react';

const PLAN_ICONS = { starter: Star, pro: Zap, enterprise: Crown };
const PLAN_GRADIENTS = {
  starter: 'linear-gradient(135deg, #475569, #64748b)',
  pro: 'linear-gradient(135deg, #6366f1, #818cf8)',
  enterprise: 'linear-gradient(135deg, #7c3aed, #a855f7)',
};
const PLAN_GLOWS = {
  starter: 'rgba(100, 116, 139, 0.15)',
  pro: 'rgba(99, 102, 241, 0.2)',
  enterprise: 'rgba(124, 58, 237, 0.25)',
};
const PLAN_ACCENT = {
  starter: '#94a3b8',
  pro: '#818cf8',
  enterprise: '#a855f7',
};

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();

  const [step, setStep] = useState(1);
  const [selectedPlan, setSelectedPlan] = useState('pro');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleContinue = () => setStep(2);

  const handleSubmit = (e) => {
    e.preventDefault();
    setError('');
    if (!name || !email || !password) { setError('Please fill in all fields'); return; }
    if (password.length < 6) { setError('Password must be at least 6 characters'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match'); return; }

    setLoading(true);
    setTimeout(() => {
      const result = signup(name, email, password, selectedPlan);
      if (result.success) navigate('/', { replace: true });
      else setError(result.error);
      setLoading(false);
    }, 500);
  };

  const passwordStrength =
    (password.length >= 6 ? 1 : 0) +
    (/[A-Z]/.test(password) ? 1 : 0) +
    (/[0-9]/.test(password) ? 1 : 0) +
    (/[^A-Za-z0-9]/.test(password) ? 1 : 0);
  const strengthColors = ['#ef4444', '#f97316', '#eab308', '#22c55e'];
  const strengthLabels = ['Weak', 'Fair', 'Good', 'Strong'];

  return (
    <div className="auth-page min-h-screen bg-[#060910] relative overflow-hidden">
      {/* ── Ambient glows ─────────────────────────────────── */}
      <div className="auth-glow absolute top-[-5%] right-[20%] w-[600px] h-[600px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.1) 0%, transparent 70%)' }} />
      <div className="auth-glow absolute bottom-[10%] left-[15%] w-[500px] h-[500px] rounded-full pointer-events-none"
        style={{ background: 'radial-gradient(circle, rgba(99,102,241,0.08) 0%, transparent 70%)', animationDelay: '2s' }} />

      {/* Grid */}
      <div className="absolute inset-0 pointer-events-none opacity-[0.02]"
        style={{
          backgroundImage: `linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px),
                            linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)`,
          backgroundSize: '60px 60px',
        }} />

      {/* ── Header ────────────────────────────────────────── */}
      <div className="relative z-10 max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 pt-8">
        <div className="auth-fade-up flex items-center justify-between">
          <Link to="/landing" className="flex items-center gap-3 group">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center transition-transform group-hover:scale-105"
              style={{ background: 'linear-gradient(135deg, #7c3aed, #a855f7, #6366f1)' }}>
              <Zap className="w-4.5 h-4.5" style={{ color: '#fff' }} />
            </div>
            <span className="text-lg font-bold" style={{ color: '#fff', letterSpacing: '-0.02em' }}>SmartAlgo</span>
          </Link>
          <div className="text-sm" style={{ color: '#64748b' }}>
            Already have an account?{' '}
            <Link to="/login" className="font-semibold hover:underline" style={{ color: '#a855f7' }}>Sign in</Link>
          </div>
        </div>
      </div>

      {/* ── Step Indicator ────────────────────────────────── */}
      <div className="relative z-10 max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 mt-10">
        <div className="auth-fade-up auth-delay-1 flex items-center justify-center gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all"
              style={{
                background: step >= 1 ? 'linear-gradient(135deg, #7c3aed, #6366f1)' : 'rgba(30,41,59,0.8)',
                color: step >= 1 ? '#fff' : '#475569',
                boxShadow: step >= 1 ? '0 2px 12px rgba(124,58,237,0.3)' : 'none',
              }}>
              {step > 1 ? <Check className="w-3.5 h-3.5" /> : '1'}
            </div>
            <span className="text-sm font-semibold" style={{ color: step >= 1 ? '#e2e8f0' : '#475569' }}>Choose Plan</span>
          </div>

          <div className="w-16 h-0.5 rounded-full transition-all" style={{ background: step >= 2 ? 'linear-gradient(90deg, #7c3aed, #6366f1)' : 'rgba(51,65,85,0.3)' }} />

          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all"
              style={{
                background: step >= 2 ? 'linear-gradient(135deg, #7c3aed, #6366f1)' : 'rgba(30,41,59,0.8)',
                color: step >= 2 ? '#fff' : '#475569',
                boxShadow: step >= 2 ? '0 2px 12px rgba(124,58,237,0.3)' : 'none',
              }}>
              2
            </div>
            <span className="text-sm font-semibold" style={{ color: step >= 2 ? '#e2e8f0' : '#475569' }}>Create Account</span>
          </div>
        </div>
      </div>

      {/* ═══ STEP 1 ═══════════════════════════════════════ */}
      {step === 1 && (
        <div className="relative z-10 max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 pb-16 mt-10">
          <div className="auth-fade-up auth-delay-2 text-center mb-12">
            <h1 className="text-3xl sm:text-4xl font-bold mb-3" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
              Choose Your Plan
            </h1>
            <p className="text-base" style={{ color: '#64748b' }}>
              Start free, upgrade anytime. No credit card required.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5 lg:gap-6">
            {PLANS.map((plan, idx) => {
              const Icon = PLAN_ICONS[plan.id];
              const isSelected = selectedPlan === plan.id;
              const accent = PLAN_ACCENT[plan.id];

              return (
                <button
                  key={plan.id}
                  onClick={() => setSelectedPlan(plan.id)}
                  className={`auth-fade-up auth-delay-${idx + 2} relative text-left rounded-2xl p-6 lg:p-7 transition-all duration-400 group`}
                  style={{
                    background: isSelected ? 'rgba(15, 23, 42, 0.7)' : 'rgba(15, 23, 42, 0.35)',
                    border: `1.5px solid ${isSelected ? accent + '40' : 'rgba(51,65,85,0.2)'}`,
                    boxShadow: isSelected
                      ? `0 0 0 1px ${accent}15, 0 8px 40px rgba(0,0,0,0.3), 0 0 60px ${PLAN_GLOWS[plan.id]}`
                      : '0 2px 8px rgba(0,0,0,0.1)',
                    backdropFilter: 'blur(16px)',
                    transform: isSelected ? 'scale(1.02)' : 'scale(1)',
                  }}
                >
                  {/* Popular badge */}
                  {plan.popular && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-4 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider"
                      style={{
                        background: 'linear-gradient(135deg, #6366f1, #818cf8)',
                        color: '#fff',
                        boxShadow: '0 2px 12px rgba(99,102,241,0.4)',
                      }}>
                      Most Popular
                    </div>
                  )}

                  {/* Selection indicator */}
                  <div className="absolute top-5 right-5 w-5.5 h-5.5 rounded-full flex items-center justify-center transition-all"
                    style={{
                      background: isSelected ? accent : 'transparent',
                      border: `2px solid ${isSelected ? accent : 'rgba(71,85,105,0.4)'}`,
                      boxShadow: isSelected ? `0 0 12px ${accent}40` : 'none',
                    }}>
                    {isSelected && <Check className="w-3 h-3" style={{ color: '#fff' }} />}
                  </div>

                  {/* Icon */}
                  <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5"
                    style={{ background: PLAN_GRADIENTS[plan.id], boxShadow: `0 4px 16px ${PLAN_GLOWS[plan.id]}` }}>
                    <Icon className="w-5 h-5" style={{ color: '#fff' }} />
                  </div>

                  {/* Plan name & price */}
                  <div className="text-sm font-semibold mb-1.5" style={{ color: '#94a3b8' }}>{plan.name}</div>
                  <div className="flex items-baseline gap-1 mb-6">
                    {plan.price > 0 && <span className="text-sm" style={{ color: '#64748b' }}>₹</span>}
                    <span className="text-3xl font-extrabold" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
                      {plan.priceLabel}
                    </span>
                    {plan.period && <span className="text-sm" style={{ color: '#475569' }}>{plan.period}</span>}
                  </div>

                  {/* Features */}
                  <ul className="space-y-3">
                    {plan.features.map((f) => (
                      <li key={f} className="flex items-start gap-2.5">
                        <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" style={{ color: accent }} />
                        <span className="text-[13px] leading-snug" style={{ color: '#cbd5e1' }}>{f}</span>
                      </li>
                    ))}
                  </ul>
                </button>
              );
            })}
          </div>

          <div className="auth-fade-up auth-delay-5 mt-10 flex justify-center">
            <button
              onClick={handleContinue}
              className="auth-gradient-btn group flex items-center gap-2.5 px-10 py-4 rounded-xl font-semibold text-[15px] transition-all duration-300"
              style={{
                background: 'linear-gradient(135deg, #7c3aed, #6366f1, #7c3aed)',
                backgroundSize: '200% 200%',
                color: '#fff',
                boxShadow: '0 4px 20px rgba(124,58,237,0.3), 0 0 40px rgba(124,58,237,0.1)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.boxShadow = '0 6px 30px rgba(124,58,237,0.45), 0 0 60px rgba(124,58,237,0.15)';
                e.currentTarget.style.transform = 'translateY(-2px)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = '0 4px 20px rgba(124,58,237,0.3), 0 0 40px rgba(124,58,237,0.1)';
                e.currentTarget.style.transform = 'translateY(0)';
              }}
            >
              Continue with {PLANS.find((p) => p.id === selectedPlan)?.name}
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
            </button>
          </div>
        </div>
      )}

      {/* ═══ STEP 2 ═══════════════════════════════════════ */}
      {step === 2 && (
        <div className="relative z-10 max-w-[440px] mx-auto px-4 sm:px-6 lg:px-8 pb-16 mt-8">
          {/* Plan badge */}
          <div className="auth-fade-up flex justify-center mb-8">
            <div className="inline-flex items-center gap-2.5 px-5 py-2.5 rounded-full"
              style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.2)' }}>
              <Check className="w-3.5 h-3.5" style={{ color: '#a855f7' }} />
              <span className="text-sm font-semibold" style={{ color: '#a855f7' }}>
                {PLANS.find((p) => p.id === selectedPlan)?.name} Plan
              </span>
              <button onClick={() => setStep(1)} className="flex items-center gap-1 text-xs font-medium hover:underline ml-1"
                style={{ color: '#64748b' }}>
                <ArrowLeft className="w-3 h-3" /> Change
              </button>
            </div>
          </div>

          {/* Heading */}
          <div className="auth-fade-up auth-delay-1 text-center mb-8">
            <h1 className="text-2xl sm:text-3xl font-bold mb-2" style={{ color: '#ffffff', letterSpacing: '-0.02em' }}>
              Create Your Account
            </h1>
            <p className="text-[15px]" style={{ color: '#64748b' }}>Set up your trading account in seconds.</p>
          </div>

          {/* Error */}
          {error && (
            <div className="auth-scale-in flex items-center gap-3 px-4 py-3.5 rounded-xl mb-6"
              style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
              <AlertCircle className="w-4.5 h-4.5 flex-shrink-0" style={{ color: '#f87171' }} />
              <span className="text-sm font-medium" style={{ color: '#fca5a5' }}>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="auth-fade-up auth-delay-2 space-y-5">
            {/* Name */}
            <div>
              <label className="block text-[11px] font-bold uppercase tracking-[0.1em] mb-2.5" style={{ color: '#94a3b8' }}>
                Full Name
              </label>
              <div className="relative">
                <User className="absolute left-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px]" style={{ color: '#475569' }} />
                <input type="text" value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="John Doe" className="auth-input w-full pl-12 pr-4 py-3.5 rounded-xl text-[15px] outline-none" autoComplete="name" />
              </div>
            </div>

            {/* Email */}
            <div>
              <label className="block text-[11px] font-bold uppercase tracking-[0.1em] mb-2.5" style={{ color: '#94a3b8' }}>
                Email Address
              </label>
              <div className="relative">
                <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px]" style={{ color: '#475569' }} />
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com" className="auth-input w-full pl-12 pr-4 py-3.5 rounded-xl text-[15px] outline-none" autoComplete="email" />
              </div>
            </div>

            {/* Password */}
            <div>
              <label className="block text-[11px] font-bold uppercase tracking-[0.1em] mb-2.5" style={{ color: '#94a3b8' }}>
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px]" style={{ color: '#475569' }} />
                <input type={showPassword ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder="Min 6 characters"
                  className="auth-input w-full pl-12 pr-12 py-3.5 rounded-xl text-[15px] outline-none" autoComplete="new-password" />
                <button type="button" onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-4 top-1/2 -translate-y-1/2 hover:opacity-80 transition-opacity" style={{ color: '#64748b' }}>
                  {showPassword ? <EyeOff className="w-[18px] h-[18px]" /> : <Eye className="w-[18px] h-[18px]" />}
                </button>
              </div>
              {/* Strength meter */}
              {password && (
                <div className="mt-3 flex items-center gap-3">
                  <div className="flex gap-1 flex-1">
                    {[1, 2, 3, 4].map((i) => (
                      <div key={i} className="h-1 flex-1 rounded-full transition-all duration-300"
                        style={{ background: i <= passwordStrength ? strengthColors[passwordStrength - 1] : 'rgba(51,65,85,0.3)' }} />
                    ))}
                  </div>
                  <span className="text-[11px] font-semibold" style={{ color: strengthColors[passwordStrength - 1] || '#475569' }}>
                    {passwordStrength > 0 ? strengthLabels[passwordStrength - 1] : ''}
                  </span>
                </div>
              )}
            </div>

            {/* Confirm Password */}
            <div>
              <label className="block text-[11px] font-bold uppercase tracking-[0.1em] mb-2.5" style={{ color: '#94a3b8' }}>
                Confirm Password
              </label>
              <div className="relative">
                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px]" style={{ color: '#475569' }} />
                <input type={showPassword ? 'text' : 'password'} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Re-enter password"
                  className="auth-input w-full pl-12 pr-12 py-3.5 rounded-xl text-[15px] outline-none"
                  style={
                    confirmPassword
                      ? confirmPassword === password
                        ? { borderColor: 'rgba(34,197,94,0.4)' }
                        : { borderColor: 'rgba(239,68,68,0.4)' }
                      : {}
                  }
                  autoComplete="new-password" />
                {confirmPassword && confirmPassword === password && (
                  <Check className="absolute right-4 top-1/2 -translate-y-1/2 w-[18px] h-[18px]" style={{ color: '#22c55e' }} />
                )}
              </div>
            </div>

            {/* Submit */}
            <button type="submit" disabled={loading}
              className="auth-gradient-btn w-full flex items-center justify-center gap-2.5 py-3.5 rounded-xl font-semibold text-[15px] transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed mt-2"
              style={{
                background: 'linear-gradient(135deg, #7c3aed, #6366f1, #7c3aed)',
                backgroundSize: '200% 200%',
                color: '#fff',
                boxShadow: '0 4px 20px rgba(124,58,237,0.3), 0 0 40px rgba(124,58,237,0.1)',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.boxShadow = '0 6px 30px rgba(124,58,237,0.45), 0 0 60px rgba(124,58,237,0.15)';
                e.currentTarget.style.transform = 'translateY(-1px)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = '0 4px 20px rgba(124,58,237,0.3), 0 0 40px rgba(124,58,237,0.1)';
                e.currentTarget.style.transform = 'translateY(0)';
              }}>
              {loading ? (
                <div className="w-5 h-5 border-2 rounded-full animate-spin"
                  style={{ borderColor: 'rgba(255,255,255,0.3)', borderTopColor: '#fff' }} />
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  Create Account
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <p className="auth-fade-up auth-delay-4 mt-8 text-center text-xs" style={{ color: '#334155' }}>
            By creating an account, you agree to our Terms of Service and Privacy Policy.
          </p>
        </div>
      )}
    </div>
  );
}

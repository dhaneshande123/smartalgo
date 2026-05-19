import React, { createContext, useContext, useState, useEffect } from 'react';

const AuthContext = createContext(null);

/* ── Default admin account ──────────────────────────────────── */
const ADMIN_USER = {
  id: 'admin-001',
  email: 'admin@smartalgo.in',
  password: 'SmartAlgo@2024',
  name: 'Admin',
  role: 'admin',
  plan: 'enterprise',
  createdAt: '2024-01-01T00:00:00Z',
};

/* ── Plan definitions ────────────────────────────────────────── */
export const PLANS = [
  {
    id: 'starter',
    name: 'Starter',
    price: 0,
    priceLabel: 'Free',
    period: '',
    features: [
      'Live market data (delayed 1 min)',
      'Basic dashboard & charts',
      'Paper trading (1 strategy)',
      'Option chain viewer',
      'Community support',
    ],
    limits: { strategies: 1, backtests: 5 },
    color: 'slate',
    popular: false,
  },
  {
    id: 'pro',
    name: 'Pro',
    price: 2999,
    priceLabel: '2,999',
    period: '/month',
    features: [
      'Real-time market data',
      'Advanced charts & indicators',
      'Paper trading (5 strategies)',
      'Strategy builder',
      'IV Surface & Greeks',
      'P&L analytics',
      'Email support',
    ],
    limits: { strategies: 5, backtests: 50 },
    color: 'blue',
    popular: true,
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price: 9999,
    priceLabel: '9,999',
    period: '/month',
    features: [
      'Everything in Pro',
      'Unlimited strategies',
      'Live trading (Fyers, Zerodha)',
      'Risk management suite',
      'Auto-deploy & kill switch',
      'Custom backtesting',
      'Priority support & API access',
    ],
    limits: { strategies: Infinity, backtests: Infinity },
    color: 'accent',
    popular: false,
  },
];

/* ── Helper: get users from localStorage ─────────────────────── */
function getStoredUsers() {
  try {
    const raw = localStorage.getItem('smartalgo_users');
    return raw ? JSON.parse(raw) : [ADMIN_USER];
  } catch {
    return [ADMIN_USER];
  }
}

function storeUsers(users) {
  localStorage.setItem('smartalgo_users', JSON.stringify(users));
}

function getStoredSession() {
  try {
    const raw = localStorage.getItem('smartalgo_session');
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function storeSession(session) {
  if (session) {
    localStorage.setItem('smartalgo_session', JSON.stringify(session));
  } else {
    localStorage.removeItem('smartalgo_session');
  }
}

/* ── Auth Provider ───────────────────────────────────────────── */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  // Restore session on mount
  useEffect(() => {
    const session = getStoredSession();
    if (session) {
      setUser(session);
    }
    setLoading(false);
  }, []);

  // Ensure admin always exists
  useEffect(() => {
    const users = getStoredUsers();
    const hasAdmin = users.some((u) => u.email === ADMIN_USER.email);
    if (!hasAdmin) {
      storeUsers([ADMIN_USER, ...users]);
    }
  }, []);

  const login = (email, password) => {
    const users = getStoredUsers();
    const found = users.find(
      (u) => u.email.toLowerCase() === email.toLowerCase() && u.password === password
    );
    if (!found) {
      return { success: false, error: 'Invalid email or password' };
    }
    const session = {
      id: found.id,
      email: found.email,
      name: found.name,
      role: found.role,
      plan: found.plan,
    };
    setUser(session);
    storeSession(session);
    return { success: true };
  };

  const signup = (name, email, password, plan = 'starter') => {
    const users = getStoredUsers();
    const exists = users.some((u) => u.email.toLowerCase() === email.toLowerCase());
    if (exists) {
      return { success: false, error: 'Email already registered' };
    }
    const newUser = {
      id: `user-${Date.now()}`,
      email,
      password,
      name,
      role: 'user',
      plan,
      createdAt: new Date().toISOString(),
    };
    storeUsers([...users, newUser]);
    const session = {
      id: newUser.id,
      email: newUser.email,
      name: newUser.name,
      role: newUser.role,
      plan: newUser.plan,
    };
    setUser(session);
    storeSession(session);
    return { success: true };
  };

  const logout = () => {
    setUser(null);
    storeSession(null);
  };

  const isAdmin = user?.role === 'admin';
  const isAuthenticated = !!user;

  return (
    <AuthContext.Provider
      value={{ user, loading, login, signup, logout, isAdmin, isAuthenticated }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

export default AuthContext;

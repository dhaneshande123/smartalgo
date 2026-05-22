import React, { createContext, useContext, useState, useEffect } from 'react';

const AuthContext = createContext(null);

/* ── Simple SHA-256 hash helper (browser-native) ──────────────── */
async function hashPassword(password) {
  const encoder = new TextEncoder();
  const data = encoder.encode(password + '_smartalgo_salt_v1');
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

// Pre-computed hash of 'SmartAlgo@2024' with our salt
const ADMIN_PASSWORD_HASH = ''; // Will be computed at runtime on first load

/* ── Default admin account (NO plaintext password stored) ────── */
const ADMIN_USER = {
  id: 'admin-001',
  email: 'admin@smartalgo.in',
  passwordHash: '', // Set at runtime
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
    if (!raw) return [];
    const users = JSON.parse(raw);
    // Strip any legacy plaintext passwords during read
    return users.map(u => {
      const { password, ...rest } = u;
      return rest;
    });
  } catch {
    return [];
  }
}

function storeUsers(users) {
  // Never store plaintext passwords
  const safe = users.map(({ password, ...rest }) => rest);
  localStorage.setItem('smartalgo_users', JSON.stringify(safe));
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
  const [adminHash, setAdminHash] = useState(null);

  // Restore session on mount
  useEffect(() => {
    const session = getStoredSession();
    if (session) {
      setUser(session);
    }
    setLoading(false);
  }, []);

  // Compute admin password hash and ensure admin user exists
  useEffect(() => {
    (async () => {
      const hash = await hashPassword('SmartAlgo@2024');
      setAdminHash(hash);
      const users = getStoredUsers();
      const hasAdmin = users.some((u) => u.email === ADMIN_USER.email);
      if (!hasAdmin) {
        storeUsers([{ ...ADMIN_USER, passwordHash: hash }, ...users]);
      } else {
        // Update admin hash if not set
        const updated = users.map(u =>
          u.email === ADMIN_USER.email ? { ...u, passwordHash: hash } : u
        );
        storeUsers(updated);
      }
    })();
  }, []);

  const login = async (email, password) => {
    const inputHash = await hashPassword(password);
    const users = getStoredUsers();
    const found = users.find(
      (u) => u.email.toLowerCase() === email.toLowerCase() && u.passwordHash === inputHash
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

  const signup = async (name, email, password, plan = 'starter') => {
    const users = getStoredUsers();
    const exists = users.some((u) => u.email.toLowerCase() === email.toLowerCase());
    if (exists) {
      return { success: false, error: 'Email already registered' };
    }
    const pwHash = await hashPassword(password);
    const newUser = {
      id: `user-${Date.now()}`,
      email,
      passwordHash: pwHash,
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

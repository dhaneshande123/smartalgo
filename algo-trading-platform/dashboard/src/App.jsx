import React, { useState, Component } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import Sidebar from './components/Layout/Sidebar';
import Header from './components/Layout/Header';
import Dashboard from './pages/Dashboard';
import MarketData from './pages/MarketData';
import Portfolio from './pages/Portfolio';
import Strategies from './pages/Strategies';
import Risk from './pages/Risk';
import Orders from './pages/Orders';
import Backtest from './pages/Backtest';
import Monitoring from './pages/Monitoring';
import PnLAnalytics from './pages/PnLAnalytics';
import ToastProvider from './components/common/ToastProvider';
import Settings from './pages/Settings';
import PaperTrading from './pages/PaperTrading';
import Charts from './pages/Charts';
import StrategyBuilder from './pages/StrategyBuilder';
import IVSurface from './pages/IVSurface';
import AISignals from './pages/AISignals';
import Scalper from './pages/Scalper';
import TradeAnalytics from './pages/TradeAnalytics';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Signup from './pages/Signup';

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-full bg-terminal-bg p-8">
          <div className="bg-terminal-card border border-terminal-border rounded-lg p-6 max-w-lg w-full">
            <h2 className="text-lg font-semibold text-red-400 mb-2">Something went wrong</h2>
            <p className="text-sm text-slate-400 mb-4">
              {this.state.error?.message || 'An unexpected error occurred.'}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium transition-colors"
            >
              Try Again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ── Route guard: redirect to login if not authenticated ──── */
function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-[#0b0e14]">
        <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
      </div>
    );
  }
  return isAuthenticated ? children : <Navigate to="/landing" replace />;
}

/* ── Public route: redirect to dashboard if already logged in */
function PublicRoute({ children }) {
  const { isAuthenticated, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-[#0b0e14]">
        <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
      </div>
    );
  }
  return isAuthenticated ? <Navigate to="/" replace /> : children;
}

/* ── Authenticated app layout with sidebar + header ───────── */
function AuthenticatedApp() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      <ErrorBoundary>
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
          mobileOpen={mobileMenuOpen}
          onMobileClose={() => setMobileMenuOpen(false)}
        />
      </ErrorBoundary>
      <div className="flex flex-col flex-1 overflow-hidden min-w-0">
        <ErrorBoundary>
          <Header onMobileMenuToggle={() => setMobileMenuOpen(!mobileMenuOpen)} />
        </ErrorBoundary>
        <main className="flex-1 overflow-y-auto p-3 md:p-4 bg-terminal-bg">
          <ErrorBoundary>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/market" element={<MarketData />} />
              <Route path="/charts" element={<Charts />} />
              <Route path="/portfolio" element={<Portfolio />} />
              <Route path="/strategies" element={<Strategies />} />
              <Route path="/builder" element={<StrategyBuilder />} />
              <Route path="/risk" element={<Risk />} />
              <Route path="/orders" element={<Orders />} />
              <Route path="/backtest" element={<Backtest />} />
              <Route path="/pnl" element={<PnLAnalytics />} />
              <Route path="/iv-surface" element={<IVSurface />} />
              <Route path="/ai-signals" element={<AISignals />} />
              <Route path="/scalper" element={<Scalper />} />
              <Route path="/trade-analytics" element={<TradeAnalytics />} />
              <Route path="/paper" element={<PaperTrading />} />
              <Route path="/monitoring" element={<Monitoring />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        {/* Public routes — no toasts */}
        <Route path="/landing" element={<PublicRoute><Landing /></PublicRoute>} />
        <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
        <Route path="/signup" element={<PublicRoute><Signup /></PublicRoute>} />

        {/* Protected app — all dashboard routes with toasts */}
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <ToastProvider>
                <AuthenticatedApp />
              </ToastProvider>
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  );
}

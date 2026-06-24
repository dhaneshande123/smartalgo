import { useState, useEffect, useRef, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, ArrowUp, ArrowDown,
  ChevronDown, X, ShoppingCart, Tag,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts';
import Card from '../components/common/Card';
import { useIndices, useOptionChain, useExpiries, useLotSizes, usePaperTradingStatus, usePlacePaperOrder, useStartPaperTrading, useOISignals, useDeployStrategy } from '../hooks/useApi';
import { useTheme } from '../context/ThemeContext';
import { useUnderlying } from '../context/UnderlyingContext';
import { useToast } from '../components/common/ToastProvider';
import OIAnalysis from './MarketData/OIAnalysis';
import OISignals from './MarketData/OISignals';

// ─── Fallback data ────────────────────────────────────────────────────────────
const fallbackIndices = [
  { symbol: 'NIFTY 50', price: 24150.75, change: 85.30, changePct: 0.35, high: 24220.10, low: 24050.40, open: 24065.45, prevClose: 24065.45 },
  { symbol: 'BANKNIFTY', price: 51230.40, change: -120.50, changePct: -0.23, high: 51450.00, low: 51100.20, open: 51350.90, prevClose: 51350.90 },
  { symbol: 'INDIA VIX', price: 13.25, change: -0.45, changePct: -3.28, high: 13.80, low: 13.10, open: 13.70, prevClose: 13.70 },
];

const ATM = 24000;
const fallbackChain = Array.from({ length: 20 }, (_, i) => {
  const strike = ATM - 1000 + i * 100;
  const isATM = Math.abs(strike - ATM) < 50;
  const dist = Math.abs(strike - ATM);
  return {
    strike, isATM,
    call_ltp: Math.max(2, (ATM - strike + 200) * 0.8 + Math.random() * 20).toFixed(2),
    call_oi: Math.round(isATM ? 1200000 : 800000 - dist * 1000 + Math.random() * 200000),
    call_volume: Math.round(isATM ? 80000 : 40000 - dist * 100),
    call_change_pct: ((Math.random() - 0.5) * 8).toFixed(2),
    put_ltp: Math.max(2, (strike - ATM + 200) * 0.8 + Math.random() * 20).toFixed(2),
    put_oi: Math.round(isATM ? 1400000 : 900000 - dist * 1200 + Math.random() * 200000),
    put_volume: Math.round(isATM ? 90000 : 45000 - dist * 120),
    put_change_pct: ((Math.random() - 0.5) * 8).toFixed(2),
  };
});

const symbols = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];
const fallbackExpiries = ['2026-04-17', '2026-04-24', '2026-05-01', '2026-05-29'];

// Ticker pill data (compact 4 indices)
const tickerData = [
  { symbol: 'NIFTY',      price: 24150.75, changePct: 0.35 },
  { symbol: 'BANKNIFTY',  price: 51230.40, changePct: -0.23 },
  { symbol: 'FINNIFTY',   price: 23845.60, changePct: 0.18 },
  { symbol: 'MIDCPNIFTY', price: 12480.25, changePct: -0.07 },
];

// ─── Custom tooltip for OI Bar chart ─────────────────────────────────────────
function OITooltip({ active, payload, label, theme }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg px-3 py-2 text-xs shadow-xl"
      style={theme === 'dark' ? { background: 'rgba(19,23,32,0.97)', border: '1px solid rgba(100,116,139,0.2)' } : { background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(0,0,0,0.08)', boxShadow: '0 4px 20px rgba(0,0,0,0.1)' }}>
      <div className="font-bold text-white mb-1">Strike {label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.fill }} className="flex gap-2">
          <span>{p.name}:</span>
          <span className="font-mono">{(p.value / 100000).toFixed(2)}L</span>
        </div>
      ))}
    </div>
  );
}

// ─── Animated ticker pill ────────────────────────────────────────────────────
function TickerPill({ symbol, price, changePct }) {
  const isUp = changePct >= 0;
  const [flash, setFlash] = useState('');
  const prevPrice = useRef(price);

  useEffect(() => {
    if (prevPrice.current !== price) {
      setFlash(price > prevPrice.current ? 'flash-green' : 'flash-red');
      prevPrice.current = price;
      const t = setTimeout(() => setFlash(''), 600);
      return () => clearTimeout(t);
    }
  }, [price]);

  return (
    <div
      className={`flex items-center gap-2 px-3 py-1.5 rounded-full border transition-all duration-300 ${flash}
        ${isUp ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-red-500/20 bg-red-500/5'}`}
    >
      <span className="text-xs font-semibold text-slate-300">{symbol}</span>
      <span className="text-xs font-mono font-bold text-white">
        {price.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
      </span>
      <span className={`flex items-center gap-0.5 text-[11px] font-semibold ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
        {isUp ? <ArrowUp className="w-2.5 h-2.5" /> : <ArrowDown className="w-2.5 h-2.5" />}
        {Math.abs(changePct).toFixed(2)}%
      </span>
    </div>
  );
}

// ─── PCR gauge card ──────────────────────────────────────────────────────────
function PCRCard({ value, theme }) {
  const label = value > 1.2 ? 'Bullish' : value < 0.8 ? 'Bearish' : 'Neutral';
  const color = value > 1.2 ? '#10b981' : value < 0.8 ? '#ef4444' : '#f59e0b';
  return (
    <div className="rounded-xl p-4 flex flex-col items-center justify-center gap-1"
      style={theme === 'dark' ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' } : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.06)', boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' }}>
      <div className="text-xs text-slate-500 uppercase tracking-widest cursor-help" title="Put-Call Ratio — Ratio of put OI to call OI. Higher = bullish, Lower = bearish">Put-Call Ratio <span className="text-slate-600 text-[10px]">ⓘ</span></div>
      <div className="text-3xl font-bold font-mono" style={{ color }} title="Put-Call Ratio — Ratio of put OI to call OI. Higher = bullish, Lower = bearish">{value?.toFixed(2)}</div>
      <div className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
        style={{ background: `${color}22`, color }}>
        {label}
      </div>
    </div>
  );
}

// ─── OI bar with label ────────────────────────────────────────────────────────
function OIBar({ label, value, max, color, theme }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-[11px] mb-1">
        <span className="text-slate-400 cursor-help" title={label === 'Total Call OI' ? 'Total open call option contracts across all strikes' : label === 'Total Put OI' ? 'Total open put option contracts across all strikes' : ''}>{label} <span className="text-slate-600 text-[9px]">ⓘ</span></span>
        <span className="font-mono text-white">{(value / 1e7).toFixed(2)} Cr</span>
      </div>
      <div className={`h-2 rounded-full ${theme === 'dark' ? 'bg-slate-800' : 'bg-slate-200'}`}>
        <div className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

// ─── NSE/SEBI Lot Sizes (post-Nov 2024 contracts) ────────────────────────────
// These are fallback values; the live values are fetched from /api/market/lot-sizes
// which uses Fyers symbol master when available.
const LOT_SIZES_FALLBACK = {
  NIFTY: 75,        // doubled from 25 (Nov 2024 SEBI revision)
  BANKNIFTY: 30,    // doubled from 15
  FINNIFTY: 65,     // raised from 40
  MIDCPNIFTY: 120,  // raised from 75
  NIFTYNXT50: 25,
  SENSEX: 20,       // doubled from 10
  BANKEX: 30,       // doubled from 15
};
const getLotSize = (sym, liveLotSizes) =>
  (liveLotSizes && liveLotSizes[sym]) || LOT_SIZES_FALLBACK[sym] || 50;

// ─── Trade Modal ─────────────────────────────────────────────────────────────
function TradeModal({ row, optionType, symbol, expiry, onClose, theme, paperStatus, placePaperOrder, startPaperTrading, toast, lotSizes, chainLotSize }) {
  // Priority: chain's live lot_size from Fyers > /api/market/lot-sizes > local fallback
  const lotSize = chainLotSize || getLotSize(symbol, lotSizes);
  const [lots, setLots] = useState(1);
  const qty = lots * lotSize;
  const [orderType, setOrderType] = useState('MARKET');
  const [limitPrice, setLimitPrice] = useState('');
  const [tradeMode, setTradeMode] = useState('paper'); // 'paper' or 'live'
  const [loading, setLoading] = useState(false);

  const isCall = optionType === 'CE';
  const ltp = isCall ? Number(row.call_ltp) : Number(row.put_ltp);
  const oi = isCall ? Number(row.call_oi) : Number(row.put_oi);
  const vol = isCall ? Number(row.call_volume) : Number(row.put_volume);
  const changePct = isCall ? Number(row.call_change_pct) : Number(row.put_change_pct);
  const optionSymbol = `${symbol} ${row.strike} ${optionType}`;
  const isPaperActive = paperStatus?.active;

  const isDark = theme === 'dark';
  const bgStyle = isDark
    ? { background: 'rgba(15, 18, 25, 0.98)', border: '1px solid rgba(100,116,139,0.2)' }
    : { background: 'rgba(255,255,255,0.99)', border: '1px solid rgba(0,0,0,0.1)', boxShadow: '0 25px 50px rgba(0,0,0,0.15)' };

  const handleTrade = async (side) => {
    if (tradeMode === 'paper') {
      setLoading(true);
      try {
        // Step 1: Auto-start paper session if not active
        if (!isPaperActive) {
          try {
            await startPaperTrading.mutateAsync({
              initial_capital: 1000000,
              slippage_bps: 2,
              commission: 20,
              symbols: ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'],
            });
            // Brief wait for session to initialize
            await new Promise((r) => setTimeout(r, 400));
          } catch (startErr) {
            // 409 = "already active" is fine; only fail for other errors
            const status = startErr?.response?.status;
            if (status !== 409 && status !== 200) {
              throw new Error(startErr?.response?.data?.detail || 'Failed to start paper trading session');
            }
          }
        }

        // Step 2: Validate inputs
        const validLtp = Number.isFinite(ltp) && ltp > 0 ? ltp : 1.0;
        const validQty = Math.max(1, qty);

        // Step 3: Place the order
        const result = await placePaperOrder.mutateAsync({
          symbol: optionSymbol,
          side,
          quantity: validQty,
          order_type: orderType,
          price: orderType === 'LIMIT' ? parseFloat(limitPrice) : null,
          product_type: 'MIS',
          ltp: validLtp,
        });

        // Step 4: Check result — backend now returns 400 on rejection, but also check success field
        if (result && result.success === false) {
          toast?.addToast({
            level: 'CRITICAL',
            message: result.message || `Paper order rejected`,
            source: 'paper_trading',
          });
          return;
        }

        toast?.addToast({
          level: 'success',
          message: `Paper ${side} ${lots}L (${validQty} qty) ${optionSymbol} @ ₹${orderType === 'MARKET' ? validLtp.toFixed(2) : limitPrice}`,
          source: 'paper_trading',
        });
        onClose();
      } catch (err) {
        const detail = err?.response?.data?.detail || err?.message || 'Paper order failed';
        toast?.addToast({
          level: 'CRITICAL',
          message: `Paper order failed: ${detail}`,
          source: 'paper_trading',
        });
      } finally {
        setLoading(false);
      }
    } else {
      // Live mode - show info message (would integrate with real broker)
      toast?.addToast({
        level: 'INFO',
        message: `Live trading: ${side} ${qty}x ${optionSymbol} — route to broker gateway`,
        source: 'live_trading',
      });
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" onClick={onClose}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      {/* Modal */}
      <div className="relative w-[460px] rounded-2xl overflow-hidden animate-fade-in"
        style={bgStyle}
        onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className={`flex items-center justify-between px-5 py-4 border-b ${isDark ? 'border-slate-700/50' : 'border-slate-200'}`}>
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-sm ${isCall ? 'bg-gradient-to-br from-emerald-500 to-emerald-600' : 'bg-gradient-to-br from-red-500 to-red-600'}`}>
              {optionType}
            </div>
            <div>
              <div className={`text-base font-bold ${isDark ? 'text-white' : 'text-slate-900'}`}>{optionSymbol}</div>
              <div className="text-xs text-slate-500">Expiry: {expiry || 'Nearest'}</div>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300 transition-colors p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Live Price Info */}
        <div className={`px-5 py-3 grid grid-cols-4 gap-3 border-b ${isDark ? 'border-slate-700/30 bg-slate-900/30' : 'border-slate-100 bg-slate-50/50'}`}>
          <div className="text-center">
            <div className="text-[10px] text-slate-500 uppercase">LTP</div>
            <div className={`text-lg font-bold font-mono ${isCall ? 'text-emerald-400' : 'text-red-400'}`}>{ltp.toFixed(2)}</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-slate-500 uppercase">Change</div>
            <div className={`text-sm font-mono font-semibold ${changePct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-slate-500 uppercase">OI</div>
            <div className={`text-sm font-mono ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>{(oi / 1000).toFixed(0)}K</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-slate-500 uppercase">Volume</div>
            <div className={`text-sm font-mono ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>{(vol / 1000).toFixed(0)}K</div>
          </div>
        </div>

        {/* Trade Mode Toggle */}
        <div className="px-5 pt-4 pb-2">
          <div className={`flex gap-1 p-1 rounded-xl ${isDark ? 'bg-slate-800/60' : 'bg-slate-100'}`}>
            <button
              onClick={() => setTradeMode('paper')}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold transition-all ${
                tradeMode === 'paper'
                  ? 'bg-gradient-to-r from-blue-500 to-blue-600 text-white shadow-md'
                  : `${isDark ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700'}`
              }`}
            >
              <Tag className="w-4 h-4" />
              Paper Trade
            </button>
            <button
              onClick={() => setTradeMode('live')}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-semibold transition-all ${
                tradeMode === 'live'
                  ? 'bg-gradient-to-r from-amber-500 to-orange-500 text-white shadow-md'
                  : `${isDark ? 'text-slate-400 hover:text-slate-200' : 'text-slate-500 hover:text-slate-700'}`
              }`}
            >
              <ShoppingCart className="w-4 h-4" />
              Live Trade
            </button>
          </div>
          {tradeMode === 'paper' && (
            <div className="mt-2 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
              ✓ Paper trading {isPaperActive ? 'session is active' : '— session auto-starts on first order'}
            </div>
          )}
          {tradeMode === 'live' && (
            <div className="mt-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
              ⚡ Live orders will be routed through the connected broker gateway.
            </div>
          )}
        </div>

        {/* Order Controls */}
        <div className="px-5 py-3 space-y-3">
          {/* Lots */}
          <div className="flex items-center justify-between">
            <label className={`text-xs font-medium ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>
              Lots <span className="text-slate-500 font-normal">(1 lot = {lotSize})</span>
            </label>
            <div className="flex items-center gap-1">
              {[1, 2, 3, 5, 10].map((l) => (
                <button key={l} onClick={() => setLots(l)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
                    lots === l
                      ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                      : `${isDark ? 'text-slate-500 hover:text-slate-300 border border-slate-700/40' : 'text-slate-500 hover:text-slate-700 border border-slate-200'}`
                  }`}>
                  {l}L
                </button>
              ))}
            </div>
          </div>
          {/* Qty display */}
          <div className={`flex items-center justify-between text-xs ${isDark ? 'text-slate-500' : 'text-slate-500'}`}>
            <span>Total Quantity</span>
            <span className={`font-mono font-semibold ${isDark ? 'text-slate-300' : 'text-slate-700'}`}>{lots} × {lotSize} = {qty}</span>
          </div>

          {/* Order Type */}
          <div className="flex items-center justify-between">
            <label className={`text-xs font-medium ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>Order Type</label>
            <div className="flex gap-1">
              {['MARKET', 'LIMIT'].map((t) => (
                <button key={t} onClick={() => setOrderType(t)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                    orderType === t
                      ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                      : `${isDark ? 'text-slate-500 hover:text-slate-300 border border-slate-700/40' : 'text-slate-500 hover:text-slate-700 border border-slate-200'}`
                  }`}>
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* Limit Price */}
          {orderType === 'LIMIT' && (
            <div className="flex items-center justify-between">
              <label className={`text-xs font-medium ${isDark ? 'text-slate-400' : 'text-slate-600'}`}>Limit Price</label>
              <input
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                placeholder={ltp.toFixed(2)}
                step="0.05"
                className={`w-32 px-3 py-1.5 text-right rounded-lg text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/50 ${
                  isDark ? 'bg-slate-800/80 text-white border border-slate-700/40' : 'bg-white text-slate-900 border border-slate-300'
                }`}
              />
            </div>
          )}

          {/* Order Value */}
          <div className={`flex items-center justify-between py-2 px-3 rounded-lg ${isDark ? 'bg-slate-800/40' : 'bg-slate-50'}`}>
            <span className={`text-xs ${isDark ? 'text-slate-500' : 'text-slate-500'}`}>Approx. Order Value</span>
            <span className={`text-sm font-bold font-mono ${isDark ? 'text-white' : 'text-slate-900'}`}>
              ₹{((orderType === 'LIMIT' && limitPrice ? parseFloat(limitPrice) : ltp) * qty).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
            </span>
          </div>
        </div>

        {/* Action Buttons */}
        <div className={`px-5 py-4 flex gap-3 border-t ${isDark ? 'border-slate-700/50' : 'border-slate-200'}`}>
          <button
            onClick={() => handleTrade('BUY')}
            disabled={loading || (orderType === 'LIMIT' && !limitPrice)}
            className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-bold text-white bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-400 hover:to-emerald-500 shadow-lg shadow-emerald-500/20 transition-all disabled:opacity-50"
          >
            <ArrowUp className="w-4 h-4" />
            {loading ? 'Placing...' : `BUY ${tradeMode === 'paper' ? '(Paper)' : '(Live)'}`}
          </button>
          <button
            onClick={() => handleTrade('SELL')}
            disabled={loading || (orderType === 'LIMIT' && !limitPrice)}
            className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-bold text-white bg-gradient-to-r from-red-500 to-red-600 hover:from-red-400 hover:to-red-500 shadow-lg shadow-red-500/20 transition-all disabled:opacity-50"
          >
            <ArrowDown className="w-4 h-4" />
            {loading ? 'Placing...' : `SELL ${tradeMode === 'paper' ? '(Paper)' : '(Live)'}`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Index Detail Card ────────────────────────────────────────────────────────
function IndexCard({ idx, theme }) {
  const isUp = idx.change >= 0;
  return (
    <div className="rounded-xl p-4 flex flex-col gap-3"
      style={theme === 'dark' ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' } : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.06)', boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' }}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-slate-200">{idx.symbol}</span>
        <span className={`flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full
          ${isUp ? 'text-emerald-400 bg-emerald-400/10' : 'text-red-400 bg-red-400/10'}`}>
          {isUp ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
          {isUp ? '+' : ''}{idx.changePct?.toFixed(2)}%
        </span>
      </div>
      <div>
        <div className="text-3xl font-bold font-mono text-white leading-none">
          {idx.price?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
        </div>
        <div className={`text-sm font-mono mt-0.5 ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
          {isUp ? '+' : ''}{idx.change?.toFixed(2)}
        </div>
      </div>
      <div className={`grid grid-cols-2 gap-x-4 gap-y-1.5 pt-2 border-t ${theme === 'dark' ? 'border-slate-800' : 'border-slate-200'}`}>
        {[
          { label: 'Open', val: idx.open },
          { label: 'High', val: idx.high, cls: 'text-emerald-400' },
          { label: 'Prev Close', val: idx.prevClose },
          { label: 'Low',  val: idx.low,  cls: 'text-red-400' },
        ].map(({ label, val, cls }) => (
          <div key={label} className="flex justify-between text-xs">
            <span className="text-slate-500">{label}</span>
            <span className={`font-mono ${cls || 'text-slate-300'}`}>
              {val?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function MarketData() {
  const { theme } = useTheme();
  const { underlying: symbol, setUnderlying: setSymbol } = useUnderlying();
  const toast = useToast();
  const [expiry, setExpiry] = useState('');
  const [tradeModal, setTradeModal] = useState(null); // { row, optionType }
  const [activeTab, setActiveTab] = useState('chain'); // 'chain' | 'oi-analysis' | 'oi-signals'

  const { data: paperStatus } = usePaperTradingStatus();
  const placePaperOrder = usePlacePaperOrder();
  const startPaperTrading = useStartPaperTrading();
  const deployStrategy = useDeployStrategy();
  const { data: lotSizesData } = useLotSizes();
  const lotSizes = lotSizesData?.lot_sizes || LOT_SIZES_FALLBACK;

  const cardStyle = theme === 'dark'
    ? { background: 'rgba(19,23,32,0.6)', border: '1px solid rgba(100,116,139,0.12)' }
    : { background: 'rgba(255,255,255,0.82)', border: '1px solid rgba(0,0,0,0.06)', boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 6px 24px rgba(0,0,0,0.04)' };
  const tooltipStyle = theme === 'dark'
    ? { background: 'rgba(19,23,32,0.97)', border: '1px solid rgba(100,116,139,0.2)' }
    : { background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(0,0,0,0.08)', boxShadow: '0 4px 20px rgba(0,0,0,0.1)' };

  const { data: indicesData } = useIndices();
  const { data: chainData }   = useOptionChain(symbol, expiry);
  const { data: expiryData }  = useExpiries(symbol);
  const { data: oiSignalData } = useOISignals(symbol, expiry);

  // Deploy handler for OI signals
  const [deployLoading, setDeployLoading] = useState(false);
  const handleSignalDeploy = async (payload) => {
    setDeployLoading(true);
    try {
      // Auto-start paper session if not active
      if (!paperStatus?.active) {
        try {
          await startPaperTrading.mutateAsync({
            initial_capital: 1000000,
            slippage_bps: 2,
            commission: 20,
            symbols: ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'],
          });
          await new Promise((r) => setTimeout(r, 400));
        } catch (startErr) {
          const status = startErr?.response?.status;
          if (status !== 409 && status !== 200) {
            throw new Error(startErr?.response?.data?.detail || 'Failed to start paper session');
          }
        }
      }
      const result = await deployStrategy.mutateAsync(payload);
      if (result?.ok === false) {
        toast?.addToast({ level: 'CRITICAL', message: result.message || 'Deploy rejected', source: 'oi_signals' });
      } else {
        toast?.addToast({
          level: 'success',
          message: `OI Signal deployed: ${payload.name} (Paper Mode)`,
          source: 'oi_signals',
        });
      }
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Deploy failed';
      toast?.addToast({ level: 'CRITICAL', message: `Deploy failed: ${detail}`, source: 'oi_signals' });
    } finally {
      setDeployLoading(false);
    }
  };

  // ── Normalise indices ──
  const rawIndices = indicesData?.indices || indicesData;
  const indices = Array.isArray(rawIndices)
    ? rawIndices.map((i) => ({
        symbol: i.symbol || '',
        price:     i.ltp ?? i.price ?? 0,
        change:    i.change ?? 0,
        changePct: i.change_pct ?? i.changePct ?? 0,
        high:      i.high ?? 0, low: i.low ?? 0,
        open:      i.open ?? 0,
        prevClose: i.prev_close ?? i.prevClose ?? 0,
      }))
    : fallbackIndices;

  // ── Normalise chain ──
  const rawChain = chainData?.chain || chainData;
  const chain    = Array.isArray(rawChain) ? rawChain : fallbackChain;

  // ── Normalise expiries ──
  const rawExpiries = expiryData?.expiries || expiryData;
  const expiries = Array.isArray(rawExpiries)
    ? rawExpiries.map((e) => (typeof e === 'string' ? e : e.date || e))
    : fallbackExpiries;

  // ── Live ticker data (derived from indices API instead of static const) ──
  const liveTickerData = useMemo(() => {
    return tickerData.map(t => {
      const match = indices.find(idx =>
        (idx.symbol || '').toUpperCase().includes(t.symbol)
      );
      return match
        ? { symbol: t.symbol, price: match.price ?? t.price, changePct: match.changePct ?? t.changePct }
        : t;
    });
  }, [indices]);

  // ── Derived OI stats ──
  const totalCallOI = chain.reduce((a, r) => a + (Number(r.call_oi) || 0), 0);
  const totalPutOI  = chain.reduce((a, r) => a + (Number(r.put_oi)  || 0), 0);
  const pcr         = totalCallOI > 0 ? totalPutOI / totalCallOI : 1.0;
  const maxCallOI   = Math.max(...chain.map((r) => Number(r.call_oi) || 0));
  const maxPutOI    = Math.max(...chain.map((r) => Number(r.put_oi)  || 0));
  const maxPainStrike = chain.reduce((best, r) =>
    (Number(r.call_oi) || 0) + (Number(r.put_oi) || 0) >
    (Number(best.call_oi) || 0) + (Number(best.put_oi) || 0)
      ? r : best, chain[0] || {})?.strike;
  const atmRow  = chain.find((r) => r.isATM) || chain[Math.floor(chain.length / 2)];
  const indiaVix = chainData?.india_vix || indices.find((i) => i.symbol === 'INDIA VIX')?.price || 13.25;
  const maxOI = Math.max(maxCallOI, maxPutOI);

  // OI chart data (sampled to avoid clutter)
  const oiChartData = chain.map((r) => ({
    strike: r.strike,
    'Call OI': Number(r.call_oi) || 0,
    'Put OI':  Number(r.put_oi)  || 0,
    isATM:     r.isATM,
  }));

  return (
    <div className="space-y-4 animate-fade-in">
      {/* ── 1. Index Ticker Bar ─────────────────────────────────────── */}
      <div className="rounded-xl px-4 py-3 flex flex-wrap items-center gap-3"
        style={cardStyle}>
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mr-1">Live</span>
        {liveTickerData.map((t) => (
          <TickerPill key={t.symbol} {...t} />
        ))}
        <div className="ml-auto flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-[11px] text-slate-500">Market Open</span>
        </div>
      </div>

      {/* ── 2. Index Detail Cards ───────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {indices.map((idx) => <IndexCard key={idx.symbol} idx={idx} theme={theme} />)}
      </div>

      {/* ── 3. PCR + Market Summary Bar ────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <PCRCard value={pcr} theme={theme} />

        {/* Max Pain */}
        <div className="rounded-xl p-4 flex flex-col items-center justify-center gap-1"
          style={cardStyle}>
          <div className="text-xs text-slate-500 uppercase tracking-widest">Max Pain</div>
          <div className="text-3xl font-bold font-mono text-yellow-400">{maxPainStrike || '--'}</div>
          <div className="text-[11px] text-slate-500">Strike</div>
        </div>

        {/* OI comparison */}
        <div className="rounded-xl p-4 col-span-2 md:col-span-1 flex flex-col justify-center gap-3"
          style={cardStyle}>
          <OIBar label="Total Call OI" value={totalCallOI} max={Math.max(totalCallOI, totalPutOI)} color="#10b981" theme={theme} />
          <OIBar label="Total Put OI"  value={totalPutOI}  max={Math.max(totalCallOI, totalPutOI)} color="#ef4444" theme={theme} />
        </div>

        {/* India VIX */}
        <div className="rounded-xl p-4 flex flex-col items-center justify-center gap-1"
          style={cardStyle}>
          <div className="text-xs text-slate-500 uppercase tracking-widest cursor-help" title="India VIX — Market fear gauge. Higher = more expected volatility = options become expensive">India VIX <span className="text-slate-600 text-[10px]">ⓘ</span></div>
          <div className={`text-3xl font-bold font-mono ${indiaVix > 20 ? 'text-red-400' : indiaVix > 15 ? 'text-yellow-400' : 'text-emerald-400'}`}
            title="India VIX — Market fear gauge. Higher = more expected volatility = options become expensive">
            {Number(indiaVix).toFixed(2)}
          </div>
          <div className="text-[11px] text-slate-500">Volatility Index</div>
        </div>
      </div>

      {/* ── 4. OI Bar Chart ────────────────────────────────────────── */}
      <div className="rounded-xl p-4"
        style={cardStyle}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-bold text-white">Open Interest — Strike-wise</h3>
            <p className="text-[11px] text-slate-500">Call OI vs Put OI in Lakhs &nbsp;|&nbsp; ATM highlighted in blue</p>
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="flex items-center gap-1 cursor-help" title="Total open call option contracts across all strikes"><span className="w-3 h-2 rounded-sm bg-emerald-500 inline-block" /> Call OI <span className="text-slate-600 text-[9px]">ⓘ</span></span>
            <span className="flex items-center gap-1 cursor-help" title="Total open put option contracts across all strikes"><span className="w-3 h-2 rounded-sm bg-red-500 inline-block" /> Put OI <span className="text-slate-600 text-[9px]">ⓘ</span></span>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={oiChartData} barCategoryGap="20%" margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.1)" />
            <XAxis
              dataKey="strike"
              tick={{ fill: '#64748b', fontSize: 10 }}
              stroke="rgba(100,116,139,0.15)"
              tickFormatter={(v) => String(v)}
            />
            <YAxis
              tick={{ fill: '#64748b', fontSize: 10 }}
              stroke="rgba(100,116,139,0.15)"
              tickFormatter={(v) => `${(v / 100000).toFixed(0)}L`}
              width={42}
            />
            <Tooltip content={<OITooltip theme={theme} />} />
            <Bar dataKey="Call OI" radius={[3, 3, 0, 0]}>
              {oiChartData.map((entry) => (
                <Cell
                  key={entry.strike}
                  fill={entry.isATM ? '#3b82f6' : '#10b981'}
                  fillOpacity={entry.isATM ? 1 : 0.75}
                />
              ))}
            </Bar>
            <Bar dataKey="Put OI" radius={[3, 3, 0, 0]}>
              {oiChartData.map((entry) => (
                <Cell
                  key={entry.strike}
                  fill={entry.isATM ? '#60a5fa' : '#ef4444'}
                  fillOpacity={entry.isATM ? 1 : 0.75}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* ── 5. Option Chain / OI Analysis ───────────────────────────── */}
      <div className="rounded-xl overflow-hidden"
        style={cardStyle}>
        {/* Header with tab switcher */}
        <div className={`flex items-center justify-between px-4 py-3 border-b ${theme === 'dark' ? 'border-slate-800/60' : 'border-slate-200'}`}>
          <div className="flex items-center gap-4">
            {/* Tab buttons */}
            <div className="flex items-center gap-1 p-0.5 rounded-lg" style={{ background: theme === 'dark' ? 'rgba(15,20,30,0.6)' : 'rgba(0,0,0,0.04)' }}>
              {[
                { id: 'chain', label: 'Option Chain' },
                { id: 'oi-analysis', label: 'OI Analysis' },
                { id: 'oi-signals', label: 'OI Signals' },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className="transition-all duration-200"
                  style={{
                    padding: '5px 14px',
                    borderRadius: 7,
                    fontSize: 12,
                    fontWeight: activeTab === tab.id ? 700 : 500,
                    color: activeTab === tab.id ? (theme === 'dark' ? '#00f0ff' : '#7c3aed') : (theme === 'dark' ? '#94a3b8' : '#64748b'),
                    background: activeTab === tab.id ? (theme === 'dark' ? 'rgba(0,240,255,0.1)' : 'rgba(124,58,237,0.08)') : 'transparent',
                    border: activeTab === tab.id ? `1px solid ${theme === 'dark' ? 'rgba(0,240,255,0.2)' : 'rgba(124,58,237,0.15)'}` : '1px solid transparent',
                    cursor: 'pointer',
                    letterSpacing: '0.01em',
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {/* Spot + ATM info */}
            {chainData?.spot_price > 0 && (
              <div className="hidden sm:flex items-center gap-3 text-[11px] text-slate-400">
                Spot: <span className="font-mono font-bold text-white">
                  {Number(chainData.spot_price).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                </span>
                {chainData?.atm_strike > 0 && (
                  <span className="cursor-help" title="At-The-Money — Strike closest to current spot price">ATM: <span className="font-mono text-blue-400 font-bold">{chainData.atm_strike}</span></span>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="appearance-none text-xs bg-slate-800/80 text-slate-200 border border-slate-700/60 rounded-lg pl-3 pr-7 py-1.5 focus:outline-none focus:border-blue-500/50 cursor-pointer"
              >
                {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-500 pointer-events-none" />
            </div>
            <div className="relative">
              <select
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                className="appearance-none text-xs bg-slate-800/80 text-slate-200 border border-slate-700/60 rounded-lg pl-3 pr-7 py-1.5 focus:outline-none focus:border-blue-500/50 cursor-pointer"
              >
                <option value="">Nearest Expiry</option>
                {expiries.map((e) => {
                  const label = typeof e === 'string' ? e : (e.date || e);
                  const val   = typeof e === 'string' ? e : (e.timestamp || e.date || e);
                  return <option key={label} value={val}>{label}</option>;
                })}
              </select>
              <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-500 pointer-events-none" />
            </div>
          </div>
        </div>

        {/* Tab content */}
        {activeTab === 'oi-signals' ? (
          <div className="p-4">
            <OISignals
              signalData={oiSignalData}
              theme={theme}
              onDeploy={handleSignalDeploy}
              deployLoading={deployLoading}
            />
          </div>
        ) : activeTab === 'oi-analysis' ? (
          <OIAnalysis
            chain={chain}
            spot={chainData?.spot_price || 0}
            symbol={symbol}
            vix={indiaVix}
            theme={theme}
          />
        ) : (
        /* Table — scrollable with sticky header */
        <div className="overflow-auto" style={{ maxHeight: 'calc(100vh - 240px)' }}>
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10">
              <tr>
                <th colSpan="5" className={`py-2 text-center text-emerald-400 font-semibold border-b border-slate-800/60 cursor-help ${theme === 'dark' ? 'bg-emerald-500/5 bg-[#0b0e14]' : 'bg-emerald-50'}`}
                  style={{ background: theme === 'dark' ? 'rgba(11,14,20,0.97)' : 'rgba(240,253,244,0.97)' }}
                  title="Call options — Right to BUY the underlying at strike price">
                  CALLS <span className="text-emerald-500/50 text-[10px]">ⓘ</span>
                </th>
                <th className={`py-2 text-center text-slate-300 font-semibold border-b border-slate-800/60 w-20 cursor-help`}
                  style={{ background: theme === 'dark' ? 'rgba(20,24,36,0.97)' : 'rgba(241,245,249,0.97)' }}
                  title="Exercise price — The price at which the option can be exercised">
                  STRIKE <span className="text-slate-500/50 text-[10px]">ⓘ</span>
                </th>
                <th colSpan="5" className={`py-2 text-center text-red-400 font-semibold border-b border-slate-800/60 cursor-help`}
                  style={{ background: theme === 'dark' ? 'rgba(11,14,20,0.97)' : 'rgba(254,242,242,0.97)' }}
                  title="Put options — Right to SELL the underlying at strike price">
                  PUTS <span className="text-red-500/50 text-[10px]">ⓘ</span>
                </th>
              </tr>
              <tr className={`text-slate-500 border-b border-slate-800/40`}
                style={{ background: theme === 'dark' ? 'rgba(11,14,20,0.97)' : 'rgba(248,250,252,0.97)' }}>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Open Interest — Total contracts currently active at this strike">OI <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="OI Change — New contracts added (+) or closed (-) since yesterday's close">OI Chg <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Volume — Total contracts traded today at this strike">Vol <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Price Change % — Option premium change from yesterday's close">Chg% <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Last Traded Price — Most recent trade price for this option">LTP <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-center font-medium cursor-help"
                  style={{ background: theme === 'dark' ? 'rgba(20,24,36,0.97)' : 'rgba(241,245,249,0.97)' }}
                  title="Exercise price of the option contract">Strike <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Last Traded Price — Most recent trade price for this option">LTP <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Price Change % — Option premium change from yesterday's close">Chg% <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Volume — Total contracts traded today at this strike">Vol <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="OI Change — New contracts added (+) or closed (-) since yesterday's close">OI Chg <span className="text-slate-600 text-[9px]">ⓘ</span></th>
                <th className="px-2 py-2 text-right font-medium cursor-help" title="Open Interest — Total contracts currently active at this strike">OI <span className="text-slate-600 text-[9px]">ⓘ</span></th>
              </tr>
            </thead>
            <tbody>
              {chain.map((row) => {
                const callOIRatio = maxOI > 0 ? (Number(row.call_oi) || 0) / maxOI : 0;
                const putOIRatio  = maxOI > 0 ? (Number(row.put_oi)  || 0) / maxOI : 0;
                const callTint    = callOIRatio > 0.6 ? `rgba(16,185,129,${callOIRatio * 0.12})` : 'transparent';
                const putTint     = putOIRatio  > 0.6 ? `rgba(239,68,68,${putOIRatio   * 0.12})` : 'transparent';

                return (
                  <tr
                    key={row.strike}
                    className={`border-b ${theme === 'dark' ? 'border-slate-800/30' : 'border-slate-100'} cursor-pointer group transition-colors duration-100
                      hover:${theme === 'dark' ? 'bg-slate-700/20' : 'bg-slate-50'}
                      ${row.isATM ? 'border-l-2 border-l-blue-500' : ''}`}
                  >
                    {/* Call OI */}
                    <td className="px-2 py-2 text-right font-mono text-slate-400 tabular-nums hover:bg-emerald-500/10 cursor-pointer transition-colors"
                      style={{ background: callTint }}
                      onClick={() => setTradeModal({ row, optionType: 'CE' })}>
                      {(Number(row.call_oi) || 0).toLocaleString()}
                    </td>
                    {/* Call OI Change */}
                    <td className={`px-2 py-2 text-right font-mono tabular-nums text-[11px] hover:bg-emerald-500/10 cursor-pointer transition-colors
                      ${Number(row.call_oi_change) > 0 ? 'text-emerald-400' : Number(row.call_oi_change) < 0 ? 'text-red-400' : 'text-slate-600'}`}
                      style={{ background: callTint }}
                      onClick={() => setTradeModal({ row, optionType: 'CE' })}
                      title={`OI Change: ${(Number(row.call_oi_change) || 0).toLocaleString()} (${Number(row.call_oi_change_pct || 0).toFixed(1)}%)`}>
                      {Number(row.call_oi_change) > 0 ? '▲' : Number(row.call_oi_change) < 0 ? '▼' : '–'}
                      {' '}{Math.abs(Number(row.call_oi_change) || 0).toLocaleString()}
                    </td>
                    {/* Call Vol */}
                    <td className="px-2 py-2 text-right font-mono text-slate-400 tabular-nums hover:bg-emerald-500/10 cursor-pointer transition-colors"
                      style={{ background: callTint }}
                      onClick={() => setTradeModal({ row, optionType: 'CE' })}>
                      {(Number(row.call_volume) || 0).toLocaleString()}
                    </td>
                    {/* Call Chg% */}
                    <td className={`px-2 py-2 text-right font-mono tabular-nums hover:bg-emerald-500/10 cursor-pointer transition-colors
                      ${Number(row.call_change_pct) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
                      style={{ background: callTint }}
                      onClick={() => setTradeModal({ row, optionType: 'CE' })}>
                      {Number(row.call_change_pct || 0).toFixed(1)}%
                    </td>
                    {/* Call LTP */}
                    <td className="px-2 py-2 text-right font-mono font-semibold text-emerald-400 tabular-nums hover:bg-emerald-500/10 cursor-pointer transition-colors"
                      style={{ background: callTint }}
                      onClick={() => setTradeModal({ row, optionType: 'CE' })}>
                      {Number(row.call_ltp || 0).toFixed(2)}
                    </td>
                    {/* Strike */}
                    <td className={`px-3 py-2 text-center font-mono font-bold ${theme === 'dark' ? 'bg-slate-800/40' : 'bg-slate-100/80'}
                      ${row.isATM ? 'text-blue-400' : 'text-white'}`}>
                      {row.strike}
                      {row.isATM && (
                        <span className="ml-1 text-[9px] font-bold text-blue-400 align-middle cursor-help" title="At-The-Money — Strike closest to current spot price">ATM</span>
                      )}
                    </td>
                    {/* Put LTP */}
                    <td className="px-2 py-2 text-right font-mono font-semibold text-red-400 tabular-nums hover:bg-red-500/10 cursor-pointer transition-colors"
                      style={{ background: putTint }}
                      onClick={() => setTradeModal({ row, optionType: 'PE' })}>
                      {Number(row.put_ltp || 0).toFixed(2)}
                    </td>
                    {/* Put Chg% */}
                    <td className={`px-2 py-2 text-right font-mono tabular-nums hover:bg-red-500/10 cursor-pointer transition-colors
                      ${Number(row.put_change_pct) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
                      style={{ background: putTint }}
                      onClick={() => setTradeModal({ row, optionType: 'PE' })}>
                      {Number(row.put_change_pct || 0).toFixed(1)}%
                    </td>
                    {/* Put Vol */}
                    <td className="px-2 py-2 text-right font-mono text-slate-400 tabular-nums hover:bg-red-500/10 cursor-pointer transition-colors"
                      style={{ background: putTint }}
                      onClick={() => setTradeModal({ row, optionType: 'PE' })}>
                      {(Number(row.put_volume) || 0).toLocaleString()}
                    </td>
                    {/* Put OI Change */}
                    <td className={`px-2 py-2 text-right font-mono tabular-nums text-[11px] hover:bg-red-500/10 cursor-pointer transition-colors
                      ${Number(row.put_oi_change) > 0 ? 'text-emerald-400' : Number(row.put_oi_change) < 0 ? 'text-red-400' : 'text-slate-600'}`}
                      style={{ background: putTint }}
                      onClick={() => setTradeModal({ row, optionType: 'PE' })}
                      title={`OI Change: ${(Number(row.put_oi_change) || 0).toLocaleString()} (${Number(row.put_oi_change_pct || 0).toFixed(1)}%)`}>
                      {Number(row.put_oi_change) > 0 ? '▲' : Number(row.put_oi_change) < 0 ? '▼' : '–'}
                      {' '}{Math.abs(Number(row.put_oi_change) || 0).toLocaleString()}
                    </td>
                    {/* Put OI */}
                    <td className="px-2 py-2 text-right font-mono text-slate-400 tabular-nums hover:bg-red-500/10 cursor-pointer transition-colors"
                      style={{ background: putTint }}
                      onClick={() => setTradeModal({ row, optionType: 'PE' })}>
                      {(Number(row.put_oi) || 0).toLocaleString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
      </div>

      {/* ── Trade Modal ───────────────────────────────────────────── */}
      {tradeModal && (
        <TradeModal
          row={tradeModal.row}
          optionType={tradeModal.optionType}
          symbol={symbol}
          expiry={expiry}
          onClose={() => setTradeModal(null)}
          theme={theme}
          paperStatus={paperStatus}
          placePaperOrder={placePaperOrder}
          startPaperTrading={startPaperTrading}
          toast={toast}
          lotSizes={lotSizes}
          chainLotSize={chainData?.lot_size}
        />
      )}

      {/* flash animation */}
      <style>{`
        @keyframes flashGreen { 0%,100%{background:transparent} 40%{background:rgba(16,185,129,0.25)} }
        @keyframes flashRed   { 0%,100%{background:transparent} 40%{background:rgba(239,68,68,0.25)} }
        .flash-green { animation: flashGreen 0.6s ease; }
        .flash-red   { animation: flashRed   0.6s ease; }
      `}</style>
    </div>
  );
}

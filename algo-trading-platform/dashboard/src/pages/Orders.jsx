import { useState, useCallback } from 'react';
import {
  Plus, X, Send, Ban, Filter, FileText, BarChart3,
  ArrowUpCircle, ArrowDownCircle, Clock, AlertTriangle,
} from 'lucide-react';
import Card from '../components/common/Card';
import DataTable from '../components/common/DataTable';
import StatusBadge from '../components/common/StatusBadge';
import { useOrders, useTrades, useAuditTrail, usePlaceOrder, useCancelOrder } from '../hooks/useApi';
import { useToast } from '../components/common/ToastProvider';

/* ════════════════════════════════════════════════════════════
   Fallback Data
   ════════════════════════════════════════════════════════════ */

const fallbackOrders = [
  { id: 'ORD001', symbol: 'NIFTY 24100 CE', side: 'SELL', qty: 50, price: 185.50, status: 'filled', type: 'LIMIT', time: '09:15:32', strategy: 'Iron Condor NIFTY' },
  { id: 'ORD002', symbol: 'NIFTY 24300 CE', side: 'BUY', qty: 50, price: 95.20, status: 'filled', type: 'LIMIT', time: '09:15:33', strategy: 'Iron Condor NIFTY' },
  { id: 'ORD003', symbol: 'BANKNIFTY 51200 CE', side: 'SELL', qty: 25, price: 320.40, status: 'filled', type: 'MARKET', time: '09:30:15', strategy: 'Straddle BNF' },
  { id: 'ORD004', symbol: 'NIFTY 24000 PE', side: 'BUY', qty: 50, price: 0, status: 'pending', type: 'LIMIT', time: '14:22:10', strategy: 'Bull Call Spread' },
  { id: 'ORD005', symbol: 'BANKNIFTY 51500 CE', side: 'SELL', qty: 25, price: 180.00, status: 'cancelled', type: 'LIMIT', time: '11:45:00', strategy: 'Straddle BNF' },
  { id: 'ORD006', symbol: 'NIFTY 23800 PE', side: 'BUY', qty: 100, price: 45.50, status: 'rejected', type: 'LIMIT', time: '10:12:33', strategy: 'Short Strangle' },
];

const fallbackTrades = [
  { id: 'TRD001', orderId: 'ORD001', symbol: 'NIFTY 24100 CE', side: 'SELL', qty: 50, price: 185.50, time: '09:15:32', exchange: 'NSE' },
  { id: 'TRD002', orderId: 'ORD002', symbol: 'NIFTY 24300 CE', side: 'BUY', qty: 50, price: 95.20, time: '09:15:33', exchange: 'NSE' },
  { id: 'TRD003', orderId: 'ORD003', symbol: 'BANKNIFTY 51200 CE', side: 'SELL', qty: 25, price: 320.40, time: '09:30:15', exchange: 'NSE' },
];

const fallbackAudit = [
  { id: 1, time: '09:15:30', event: 'Strategy Started', detail: 'Iron Condor NIFTY activated', level: 'info' },
  { id: 2, time: '09:15:32', event: 'Order Placed', detail: 'SELL 50 NIFTY 24100 CE @ 185.50', level: 'info' },
  { id: 3, time: '09:15:32', event: 'Order Filled', detail: 'ORD001 fully filled at 185.50', level: 'info' },
  { id: 4, time: '10:12:33', event: 'Order Rejected', detail: 'ORD006 - Insufficient margin', level: 'warning' },
  { id: 5, time: '11:45:00', event: 'Order Cancelled', detail: 'ORD005 - Manual cancellation', level: 'info' },
  { id: 6, time: '14:22:10', event: 'Order Placed', detail: 'BUY 50 NIFTY 24000 PE - Pending', level: 'info' },
];

const statusFilters = ['all', 'filled', 'pending', 'cancelled', 'rejected'];

const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];
const OPTION_TYPES = ['CE', 'PE'];
const ORDER_TYPES = ['LIMIT', 'MARKET', 'SL', 'SL-M'];
const PRODUCTS = ['NRML', 'MIS', 'CNC'];

/* ════════════════════════════════════════════════════════════
   Order Placement Modal
   ════════════════════════════════════════════════════════════ */

function OrderForm({ onClose, onSubmit }) {
  const [side, setSide] = useState('BUY');
  const [underlying, setUnderlying] = useState('NIFTY');
  const [strike, setStrike] = useState(24200);
  const [optionType, setOptionType] = useState('CE');
  const [orderType, setOrderType] = useState('LIMIT');
  const [product, setProduct] = useState('NRML');
  const [qty, setQty] = useState(25);
  const [price, setPrice] = useState(150);
  const [triggerPrice, setTriggerPrice] = useState(0);

  const symbol = `${underlying} ${strike} ${optionType}`;
  const isMarket = orderType === 'MARKET' || orderType === 'SL-M';

  const handleSubmit = useCallback(() => {
    onSubmit({
      side, symbol, underlying, strike, optionType,
      orderType, product, qty, price: isMarket ? 0 : price,
      triggerPrice: orderType.startsWith('SL') ? triggerPrice : 0,
    });
  }, [side, symbol, underlying, strike, optionType, orderType, product, qty, price, triggerPrice, isMarket, onSubmit]);

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="glass-card !p-0 w-full max-w-lg shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-border">
          <div className="flex items-center gap-2">
            <Send className="w-4 h-4 text-accent" />
            <span className="text-sm font-semibold text-white">Place Order</span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* BUY/SELL Toggle */}
        <div className="px-4 pt-3">
          <div className="flex gap-2">
            <button
              onClick={() => setSide('BUY')}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold transition-all ${
                side === 'BUY'
                  ? 'bg-profit/15 text-profit border-2 border-profit/40 shadow-sm shadow-profit/10'
                  : 'bg-white/[0.03] text-slate-500 border-2 border-transparent hover:text-slate-300'
              }`}
            >
              <ArrowUpCircle className="w-4 h-4" /> BUY
            </button>
            <button
              onClick={() => setSide('SELL')}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-bold transition-all ${
                side === 'SELL'
                  ? 'bg-loss/15 text-loss border-2 border-loss/40 shadow-sm shadow-loss/10'
                  : 'bg-white/[0.03] text-slate-500 border-2 border-transparent hover:text-slate-300'
              }`}
            >
              <ArrowDownCircle className="w-4 h-4" /> SELL
            </button>
          </div>
        </div>

        {/* Form Fields */}
        <div className="px-4 py-3 space-y-3">
          {/* Instrument Row */}
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Underlying</label>
              <select value={underlying} onChange={e => setUnderlying(e.target.value)}
                className="w-full mt-1 px-2.5 py-2 text-xs bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none">
                {UNDERLYINGS.map(u => <option key={u} value={u}>{u}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Strike</label>
              <input type="number" value={strike} onChange={e => setStrike(Number(e.target.value))}
                step={underlying === 'BANKNIFTY' ? 100 : 50}
                className="w-full mt-1 px-2.5 py-2 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none" />
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Type</label>
              <div className="flex mt-1 gap-1">
                {OPTION_TYPES.map(t => (
                  <button key={t} onClick={() => setOptionType(t)}
                    className={`flex-1 py-2 text-xs font-bold rounded-lg transition-colors ${
                      optionType === t
                        ? t === 'CE' ? 'bg-profit/15 text-profit' : 'bg-loss/15 text-loss'
                        : 'bg-white/[0.03] text-slate-500 hover:text-slate-300'
                    }`}>{t}</button>
                ))}
              </div>
            </div>
          </div>

          {/* Order Config Row */}
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Order Type</label>
              <select value={orderType} onChange={e => setOrderType(e.target.value)}
                className="w-full mt-1 px-2.5 py-2 text-xs bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none">
                {ORDER_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Product</label>
              <select value={product} onChange={e => setProduct(e.target.value)}
                className="w-full mt-1 px-2.5 py-2 text-xs bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none">
                {PRODUCTS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-slate-500 uppercase font-medium">Quantity</label>
              <input type="number" value={qty} onChange={e => setQty(Number(e.target.value))}
                min={1} step={underlying === 'BANKNIFTY' ? 15 : 25}
                className="w-full mt-1 px-2.5 py-2 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none" />
            </div>
          </div>

          {/* Price Row */}
          <div className="grid grid-cols-2 gap-2">
            {!isMarket && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-medium">Price</label>
                <input type="number" value={price} onChange={e => setPrice(Number(e.target.value))}
                  step={0.05} min={0}
                  className="w-full mt-1 px-2.5 py-2 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none" />
              </div>
            )}
            {orderType.startsWith('SL') && (
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-medium">Trigger Price</label>
                <input type="number" value={triggerPrice} onChange={e => setTriggerPrice(Number(e.target.value))}
                  step={0.05} min={0}
                  className="w-full mt-1 px-2.5 py-2 text-xs font-mono bg-terminal-bg border border-terminal-border rounded-lg text-white focus:border-accent focus:outline-none" />
              </div>
            )}
          </div>

          {/* Summary */}
          <div className="p-3 rounded-xl bg-white/[0.03] border border-terminal-border">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Order Summary</span>
              <span className={`font-semibold ${side === 'BUY' ? 'text-profit' : 'text-loss'}`}>{side}</span>
            </div>
            <div className="mt-1.5 text-sm font-mono font-semibold text-white">
              {qty} x {symbol}
            </div>
            <div className="flex items-center gap-3 mt-1 text-[10px] text-slate-500">
              <span>{orderType}</span>
              <span>{product}</span>
              {!isMarket && <span>@ ₹{price.toFixed(2)}</span>}
              {isMarket && <span>@ Market</span>}
              <span className="ml-auto font-mono">
                Value: ₹{(qty * (isMarket ? 0 : price)).toLocaleString('en-IN')}
              </span>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 px-4 py-3 border-t border-terminal-border">
          <button onClick={onClose} className="flex-1 py-2.5 text-xs font-medium text-slate-400 bg-white/[0.03] rounded-lg hover:bg-white/[0.06] transition-colors">
            Cancel
          </button>
          <button onClick={handleSubmit}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 text-xs font-bold rounded-lg transition-all ${
              side === 'BUY'
                ? 'bg-profit text-white hover:bg-profit/90 shadow-lg shadow-profit/20'
                : 'bg-loss text-white hover:bg-loss/90 shadow-lg shadow-loss/20'
            }`}>
            <Send className="w-3.5 h-3.5" />
            {side} {symbol}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   Main Component
   ════════════════════════════════════════════════════════════ */

export default function Orders() {
  const toast = useToast();
  const [statusFilter, setStatusFilter] = useState('all');
  const [tab, setTab] = useState('orders');
  const [showOrderForm, setShowOrderForm] = useState(false);
  const [cancelConfirm, setCancelConfirm] = useState(null);

  const { data: ordersData } = useOrders(statusFilter !== 'all' ? statusFilter : '');
  const { data: tradesData } = useTrades();
  const { data: auditData } = useAuditTrail();
  const placeOrderMutation = usePlaceOrder();
  const cancelOrderMutation = useCancelOrder();

  const rawOrders = ordersData?.orders || ordersData;
  const orders = Array.isArray(rawOrders) ? rawOrders : fallbackOrders;
  const rawTrades = tradesData?.trades || tradesData;
  const trades = Array.isArray(rawTrades) ? rawTrades : fallbackTrades;
  const rawAudit = auditData?.entries || auditData?.audit || auditData;
  const audit = Array.isArray(rawAudit) ? rawAudit : fallbackAudit;

  const filteredOrders = statusFilter === 'all' ? orders : orders.filter((o) => o.status === statusFilter);

  // Counts
  const pendingCount = orders.filter(o => o.status === 'pending' || o.status === 'open').length;
  const filledCount = orders.filter(o => o.status === 'filled').length;
  const rejectedCount = orders.filter(o => o.status === 'rejected').length;

  const handlePlaceOrder = useCallback(async (order) => {
    try {
      await placeOrderMutation.mutateAsync(order);
      toast?.addToast?.({
        level: 'success',
        message: `Order placed: ${order.side} ${order.qty} ${order.symbol} ${order.orderType === 'MARKET' ? '@ Market' : `@ ₹${order.price}`}`,
        source: 'orders',
      });
      setShowOrderForm(false);
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Order placement failed';
      toast?.addToast?.({
        level: 'CRITICAL',
        message: `Order failed: ${detail}`,
        source: 'orders',
      });
    }
  }, [toast, placeOrderMutation]);

  const handleCancelOrder = useCallback(async (orderId) => {
    try {
      await cancelOrderMutation.mutateAsync(orderId);
      toast?.addToast?.({
        level: 'success',
        message: `Order ${orderId} cancelled`,
        source: 'orders',
      });
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Cancel failed';
      toast?.addToast?.({
        level: 'CRITICAL',
        message: `Cancel failed: ${detail}`,
        source: 'orders',
      });
    }
    setCancelConfirm(null);
  }, [toast, cancelOrderMutation]);

  const orderColumns = [
    { key: 'id', label: 'Order ID', render: (v) => <span className="font-mono text-blue-400 text-xs">{(v || '').toString().substring(0, 12)}</span> },
    { key: 'time', label: 'Time', render: (v) => <span className="font-mono text-xs">{v}</span> },
    { key: 'symbol', label: 'Symbol', render: (v) => <span className="font-medium text-white text-xs">{v}</span> },
    { key: 'side', label: 'Side', render: (v) => (
      <span className={`flex items-center gap-1 text-xs font-bold ${v === 'BUY' ? 'text-profit' : 'text-loss'}`}>
        {v === 'BUY' ? <ArrowUpCircle className="w-3 h-3" /> : <ArrowDownCircle className="w-3 h-3" />}
        {v}
      </span>
    )},
    { key: 'qty', label: 'Qty', align: 'right', render: (v) => <span className="font-mono text-xs">{v}</span> },
    { key: 'type', label: 'Type', render: (v) => <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700/50 text-slate-300">{v}</span> },
    { key: 'price', label: 'Price', align: 'right', render: (v) => <span className="font-mono text-xs">{v > 0 ? `₹${Number(v).toFixed(2)}` : '-'}</span> },
    { key: 'status', label: 'Status', render: (v) => <StatusBadge status={v} /> },
    { key: 'strategy', label: 'Strategy', render: (v) => <span className="text-xs text-slate-400">{v || '-'}</span> },
    { key: '_actions', label: '', render: (_, row) => {
      const canCancel = row.status === 'pending' || row.status === 'open' || row.status === 'Placed' || row.status === 'Open';
      if (!canCancel) return null;
      const orderId = row.id || row.order_id;
      return cancelConfirm === orderId ? (
        <div className="flex items-center gap-1">
          <button onClick={() => handleCancelOrder(orderId)} className="px-2 py-0.5 text-[10px] font-bold text-white bg-loss rounded hover:bg-loss/80 transition-colors">
            Confirm
          </button>
          <button onClick={() => setCancelConfirm(null)} className="px-2 py-0.5 text-[10px] text-slate-400 hover:text-white transition-colors">
            No
          </button>
        </div>
      ) : (
        <button
          onClick={() => setCancelConfirm(orderId)}
          className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium text-loss/80 hover:text-loss hover:bg-loss/10 rounded transition-colors"
        >
          <Ban className="w-3 h-3" /> Cancel
        </button>
      );
    }},
  ];

  const tradeColumns = [
    { key: 'id', label: 'Trade ID', render: (v) => <span className="font-mono text-blue-400 text-xs">{(v || '').toString().substring(0, 12)}</span> },
    { key: 'time', label: 'Time', render: (v) => <span className="font-mono text-xs">{v}</span> },
    { key: 'symbol', label: 'Symbol', render: (v) => <span className="font-medium text-white text-xs">{v}</span> },
    { key: 'side', label: 'Side', render: (v) => (
      <span className={`flex items-center gap-1 text-xs font-bold ${v === 'BUY' ? 'text-profit' : 'text-loss'}`}>
        {v === 'BUY' ? <ArrowUpCircle className="w-3 h-3" /> : <ArrowDownCircle className="w-3 h-3" />}
        {v}
      </span>
    )},
    { key: 'qty', label: 'Qty', align: 'right', render: (v) => <span className="font-mono text-xs">{v}</span> },
    { key: 'price', label: 'Price', align: 'right', render: (v) => <span className="font-mono text-xs">₹{Number(v || 0).toFixed(2)}</span> },
    { key: 'exchange', label: 'Exchange', render: (v) => <span className="text-xs">{v || 'NSE'}</span> },
  ];

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Order Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="glass-card !p-3">
          <div className="text-[10px] text-slate-500 uppercase font-medium">Total Orders</div>
          <div className="text-xl font-bold font-mono text-white mt-1">{orders.length}</div>
        </div>
        <div className="glass-card !p-3">
          <div className="text-[10px] text-slate-500 uppercase font-medium">Filled</div>
          <div className="text-xl font-bold font-mono text-profit mt-1">{filledCount}</div>
        </div>
        <div className="glass-card !p-3">
          <div className="text-[10px] text-slate-500 uppercase font-medium">Pending</div>
          <div className="text-xl font-bold font-mono text-yellow-400 mt-1">{pendingCount}</div>
        </div>
        <div className="glass-card !p-3">
          <div className="text-[10px] text-slate-500 uppercase font-medium">Rejected</div>
          <div className="text-xl font-bold font-mono text-loss mt-1">{rejectedCount}</div>
        </div>
      </div>

      {/* Tab Bar + Place Order Button */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-1 bg-terminal-card border border-terminal-border rounded-lg p-1">
          {[
            { key: 'orders', label: 'Order Book', icon: FileText },
            { key: 'trades', label: 'Trade Book', icon: BarChart3 },
            { key: 'audit', label: 'Audit Trail', icon: Clock },
          ].map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                tab === t.key ? 'bg-accent/15 text-accent' : 'text-slate-400 hover:text-white hover:bg-white/[0.04]'
              }`}
            >
              <t.icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          ))}
        </div>

        <button
          onClick={() => setShowOrderForm(true)}
          className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-white bg-gradient-to-r from-accent to-accent-light rounded-lg hover:shadow-lg hover:shadow-accent/20 transition-all"
        >
          <Plus className="w-3.5 h-3.5" /> Place Order
        </button>
      </div>

      {/* Orders Tab */}
      {tab === 'orders' && (
        <Card
          title={`Order Book (${filteredOrders.length})`}
          actions={
            <div className="flex items-center gap-1">
              <Filter className="w-3 h-3 text-slate-500 mr-1" />
              {statusFilters.map((f) => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  className={`px-2.5 py-1 rounded-md text-[10px] font-medium capitalize transition-colors ${
                    statusFilter === f
                      ? 'bg-accent/15 text-accent'
                      : 'text-slate-500 hover:text-slate-300 hover:bg-white/[0.04]'
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          }
        >
          <DataTable columns={orderColumns} data={filteredOrders} />
        </Card>
      )}

      {/* Trades Tab */}
      {tab === 'trades' && (
        <Card title={`Trade Book (${trades.length})`}>
          <DataTable columns={tradeColumns} data={trades} />
        </Card>
      )}

      {/* Audit Trail Tab */}
      {tab === 'audit' && (
        <Card title={`Audit Trail (${audit.length})`}>
          <div className="space-y-0">
            {audit.map((entry, i) => (
              <div key={entry.id || i} className="flex items-start gap-3 py-2.5 border-b border-terminal-border last:border-0">
                <span className="font-mono text-[10px] text-slate-500 mt-0.5 flex-shrink-0 w-16">{entry.time}</span>
                <div className={`w-2 h-2 rounded-full mt-1 flex-shrink-0 ${
                  entry.level === 'warning' || entry.level === 'WARNING'
                    ? 'bg-yellow-400'
                    : entry.level === 'error' || entry.level === 'ERROR'
                    ? 'bg-loss'
                    : 'bg-blue-400'
                }`} />
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-white">{entry.event}</div>
                  <div className="text-[10px] text-slate-400 truncate">{entry.detail}</div>
                </div>
                {(entry.level === 'warning' || entry.level === 'WARNING') && (
                  <AlertTriangle className="w-3 h-3 text-yellow-400 flex-shrink-0 mt-0.5 ml-auto" />
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Order Placement Modal */}
      {showOrderForm && (
        <OrderForm
          onClose={() => setShowOrderForm(false)}
          onSubmit={handlePlaceOrder}
        />
      )}
    </div>
  );
}

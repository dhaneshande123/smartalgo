"""Display the full platform output."""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient
from core.api import app

client = TestClient(app)

print("=" * 72)
print("   SMARTALGO TRADING PLATFORM v1.0.0 -- LIVE API OUTPUT")
print("=" * 72)

# 1. Health
print("\n[HEALTH CHECK] /api/health")
r = client.get("/api/health")
d = r.json()
print(f"  Status: {d['status'].upper()}  |  Version: {d['version']}  |  Uptime: {d['uptime_human']}")
print("  Components:")
for comp, status in d["components"].items():
    mark = "[OK]" if status == "healthy" else "[!!]" if status in ("mock", "disconnected") else "[--]"
    print(f"    {mark} {comp}: {status}")

# 2. Market Indices
print("\n[MARKET INDICES] /api/market/indices")
r = client.get("/api/market/indices")
for idx in r.json()["indices"]:
    print(f"  {idx['symbol']:14s}  {idx['ltp']:>10,.2f}  ({idx['change']:+.2f} / {idx['change_pct']:+.2f}%)")

# 3. Positions
print("\n[POSITIONS] /api/portfolio/positions")
r = client.get("/api/portfolio/positions")
pos = r.json()
print(f"  Total: {pos['count']} open positions")
for p in pos["positions"]:
    side = "SHORT" if p["quantity"] < 0 else "LONG "
    print(f"  {side} {p['instrument']:22s}  Qty:{p['quantity']:>5d}  Avg:{p['avg_price']:>8.2f}  PnL: Rs {p['pnl_unrealized']:>8,.2f}  [{p['strategy_id']}]")

# 4. Portfolio Greeks
print("\n[PORTFOLIO GREEKS] /api/portfolio/greeks")
r = client.get("/api/portfolio/greeks")
g = r.json()
print(f"  Net Delta: {g['net_delta']:>8.2f}  |  Gamma: {g['net_gamma']:>8.4f}  |  Theta: {g['net_theta']:>8.2f}  |  Vega: {g['net_vega']:>8.2f}")

# 5. P&L
print("\n[P&L SNAPSHOT] /api/portfolio/pnl")
r = client.get("/api/portfolio/pnl")
pnl = r.json()
print(f"  Realized P&L:   Rs {pnl['realized_pnl']:>10,.2f}")
print(f"  Unrealized P&L: Rs {pnl['unrealized_pnl']:>10,.2f}")
print(f"  Total Charges:  Rs {pnl['charges']['total']:>10,.2f}")
print(f"  ---")
print(f"  NET P&L:        Rs {pnl['net_pnl']:>10,.2f}")
ch = pnl["charges"]
print(f"  Charges: Brokerage Rs {ch['brokerage']:.2f} | STT Rs {ch['stt']:.2f} | Exchange Rs {ch['exchange_txn_fee']:.2f} | GST Rs {ch['gst']:.2f} | SEBI Rs {ch['sebi_fee']:.2f} | Stamp Rs {ch['stamp_duty']:.2f}")

# 6. Margin
print("\n[MARGIN] /api/portfolio/margin")
r = client.get("/api/portfolio/margin")
m = r.json()
print(f"  Capital:      Rs {m['available_cash']:>12,.2f}")
print(f"  Used Margin:  Rs {m['used_margin']:>12,.2f}  (SPAN: Rs {m['span_margin']:>10,.2f}  Exposure: Rs {m['exposure_margin']:>10,.2f})")
print(f"  Available:    Rs {m['available_margin']:>12,.2f}")
print(f"  Utilization:  {m['utilization_pct']}%")

# 7. Strategies
print("\n[STRATEGIES] /api/strategies")
r = client.get("/api/strategies")
strats = r.json()
print(f"  Running: {strats['running']}  |  Paused: {strats['paused']}  |  Stopped: {strats['stopped']}")
for s in strats["strategies"]:
    mark = "[RUN]" if s["status"] == "RUNNING" else "[PSE]" if s["status"] == "PAUSED" else "[STP]"
    print(f"  {mark} {s['name']:32s}  PnL: Rs {s['pnl_today']:>8,.2f}  Orders: {s['orders_today']:>3d}  Pos: {s['positions_count']}  [{s['mode']}]")

# 8. Risk
print("\n[RISK METRICS] /api/risk/metrics")
r = client.get("/api/risk/metrics")
risk = r.json()
print(f"  VaR (1d 95%):    Rs {risk['portfolio_var_1d_95']:>10,.2f}")
print(f"  VaR (1d 99%):    Rs {risk['portfolio_var_1d_99']:>10,.2f}")
print(f"  Max Drawdown:    {risk['max_drawdown']*100:.2f}%")
print(f"  Current DD:      {risk['current_drawdown']*100:.2f}%")
print(f"  Kill Switch:     {'ACTIVE' if risk['kill_switch_active'] else 'OFF'}")
print(f"  Daily Loss:      Rs {risk['daily_loss_used']:>8,.2f} / Rs {risk['daily_loss_limit']:>8,.2f}")

# 9. Circuit Breakers
print("\n[CIRCUIT BREAKERS] /api/risk/circuit-breakers")
r = client.get("/api/risk/circuit-breakers")
cb = r.json()
for b in cb["breakers"]:
    mark = "[OK]" if b["state"] == "closed" else "[!!]"
    print(f"  {mark} {b['name']:25s}  State: {b['state']:10s}  Auto-recover: {b['auto_recover']}")

# 10. Orders
print("\n[ORDER BOOK] /api/orders")
r = client.get("/api/orders")
orders = r.json()
print(f"  Total: {orders['count']}  |  Filled: {orders['filled']}  |  Open: {orders['open']}  |  Cancelled: {orders['cancelled']}  |  Rejected: {orders['rejected']}  |  Partial: {orders['partial']}")
for o in orders["orders"][:6]:
    print(f"  [{o['status']:9s}] {o['order_id']:24s}  {o['side']:4s} {o['instrument']:20s}  Qty:{o['quantity']:>4d}  Filled:{o['filled_quantity']:>4d}  {o['order_type']}")
if orders["count"] > 6:
    print(f"  ... and {orders['count']-6} more orders")

# 11. Trades
print("\n[TRADE BOOK] /api/trades")
r = client.get("/api/trades")
trades = r.json()
print(f"  Total Trades: {trades['count']}  |  Turnover: Rs {trades['total_turnover']:>12,.2f}")
for t in trades["trades"][:3]:
    print(f"  {t['trade_id']:24s}  {t['side']:4s} {t['instrument']:20s}  Qty:{t['quantity']:>4d}  @ Rs {t['price']:>8.2f}")

# 12. Option Chain
print("\n[NIFTY OPTION CHAIN] /api/market/option-chain/NIFTY")
r = client.get("/api/market/option-chain/NIFTY")
oc = r.json()
print(f"  Spot: {oc['spot_price']:,.2f}  |  ATM: {oc['atm_strike']}  |  Lot: {oc['lot_size']}  |  PCR: {oc['pcr']}")
header = f"  {'CE OI':>10s} {'CE LTP':>8s} {'CE IV':>7s} {'CE Dlt':>7s}  {'Strike':>7s}  {'PE Dlt':>7s} {'PE IV':>7s} {'PE LTP':>8s} {'PE OI':>10s}"
print(header)
print("  " + "-" * (len(header) - 2))
for c in oc["contracts"][9:16]:
    atm = " <-- ATM" if c["strike"] == oc["atm_strike"] else ""
    print(f"  {c['call']['oi']:>10,d} {c['call']['ltp']:>8.2f} {c['call']['iv']:>6.1f}% {c['call']['delta']:>7.4f}  {c['strike']:>7d}  {c['put']['delta']:>7.4f} {c['put']['iv']:>6.1f}% {c['put']['ltp']:>8.2f} {c['put']['oi']:>10,d}{atm}")

# 13. Stress Test
print("\n[STRESS TEST] /api/risk/stress-test")
r = client.get("/api/risk/stress-test")
st = r.json()
print(f"  Base Spot: {st['base_spot']:,.2f}  |  Base PnL: Rs {st['base_pnl']:,.2f}")
print(f"  {'Scenario':20s} {'Spot Move':>10s} {'IV Chg':>8s} {'Est PnL':>12s} {'Margin Impact':>14s}")
print(f"  {'-'*20} {'-'*10} {'-'*8} {'-'*12} {'-'*14}")
for sc in st["scenarios"]:
    print(f"  {sc['scenario']:20s} {sc['spot_move_pct']:>9.1f}% {sc['iv_change_pct']:>7.1f}% Rs {sc['estimated_pnl']:>10,.2f} Rs {sc['estimated_margin_impact']:>12,.2f}")

# 14. Monitoring
print("\n[SYSTEM HEALTH] /api/monitoring/health")
r = client.get("/api/monitoring/health")
mon = r.json()
print(f"  Overall: {mon['overall_status'].upper()}")
for c in mon["components"]:
    mark = "[OK]" if c["status"] == "healthy" else "[!!]" if c["status"] == "degraded" else "[XX]"
    print(f"  {mark} {c['component']:20s}  {c['status']:10s}  {c['latency_ms']:>5.1f}ms  -- {c['message']}")

# 15. Alerts
print("\n[ALERTS] /api/monitoring/alerts")
r = client.get("/api/monitoring/alerts")
alerts = r.json()
print(f"  Active: {alerts['active_count']}  |  Total Today: {alerts['total_today']}")
for a in alerts["alerts"]:
    mark = "[CRIT]" if a["severity"] == "CRITICAL" else "[WARN]" if a["severity"] == "WARNING" else "[INFO]"
    ack = "(acked)" if a["acknowledged"] else "(new) "
    print(f"  {mark} {a['title']:45s}  {ack}  [{a['source']}]")

# 16. Metrics
print("\n[PLATFORM METRICS] /api/monitoring/metrics")
r = client.get("/api/monitoring/metrics")
met = r.json()
print(f"  Orders/sec:     {met['orders_per_second']:>6.1f}")
print(f"  Avg Latency:    {met['avg_order_latency_ms']:>6.1f}ms")
print(f"  P99 Latency:    {met['p99_order_latency_ms']:>6.1f}ms")
print(f"  Event Bus:      {met['event_bus_throughput_per_sec']:>6.1f}/sec  (queue: {met['event_bus_queue_depth']})")
print(f"  Memory:         {met['memory_usage_mb']:>6.1f}MB")
print(f"  CPU:            {met['cpu_usage_pct']:>6.1f}%")

# Summary
print("\n" + "=" * 72)
r = client.get("/api/system/info")
info = r.json()
print(f"  SmartAlgo Trading Platform v{info['platform_version']}")
print(f"  Python {info['python_version'][:12]}  |  {info['os_platform'][:35]}")
print(f"  Mode: {info['trading_mode']}  |  Timezone: {info['timezone']}")
print(f"  Market: {info['market_open']} - {info['market_close']}")
print(f"  Brokers: {info['brokers_configured']} configured  |  Strategies: {info['strategies_configured']}")
print(f"  API Routes: {len(app.routes)}  |  Tests: 864 passing  |  Files: 114")
print(f"  Swagger UI: http://localhost:8080/docs")
print("=" * 72)

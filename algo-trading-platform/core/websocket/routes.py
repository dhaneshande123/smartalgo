"""
WebSocket route handlers for the algo trading platform.

Endpoints:
    /ws/market-data   — streams live tick data (mock-generated at ~500ms intervals)
    /ws/portfolio      — streams position and P&L updates every 2 seconds
    /ws/orders         — streams order status changes
    /ws/alerts         — streams platform alerts as they fire
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .manager import WebSocketManager

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level manager instance; set from api.py during startup
ws_manager: WebSocketManager | None = None
live_feed = None  # FyersLiveFeed instance, set from api.py


def set_ws_manager(manager: WebSocketManager) -> None:
    """Inject the shared WebSocketManager instance (called at app startup)."""
    global ws_manager
    ws_manager = manager


def set_live_feed(feed) -> None:
    """Inject the live feed instance (called at app startup)."""
    global live_feed
    live_feed = feed


def _get_manager() -> WebSocketManager:
    if ws_manager is None:
        raise RuntimeError("WebSocketManager not initialized")
    return ws_manager


# ===================================================================
# Mock data generators for WebSocket streams
# ===================================================================

_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
_BASE_PRICES = {
    "NIFTY": 22700.0,
    "BANKNIFTY": 51600.0,
    "FINNIFTY": 24100.0,
    "MIDCPNIFTY": 12500.0,
    "RELIANCE": 2950.0,
    "TCS": 3800.0,
    "INFY": 1580.0,
    "HDFCBANK": 1720.0,
    "ICICIBANK": 1280.0,
}


def _generate_tick(symbol: str) -> dict[str, Any]:
    """Generate a single realistic mock tick for the given symbol."""
    base = _BASE_PRICES.get(symbol, 1000.0)
    t = time.time()
    noise = math.sin(t * 0.1 + hash(symbol) % 100) * 0.002
    drift = random.gauss(0, 0.0005)
    ltp = round(base * (1 + noise + drift), 2)
    spread = round(base * 0.0002, 2)

    return {
        "symbol": symbol,
        "ltp": ltp,
        "bid": round(ltp - spread, 2),
        "ask": round(ltp + spread, 2),
        "bid_qty": random.randint(50, 5000),
        "ask_qty": random.randint(50, 5000),
        "open": round(base * (1 + random.uniform(-0.003, 0.003)), 2),
        "high": round(max(ltp, base) * (1 + random.uniform(0, 0.005)), 2),
        "low": round(min(ltp, base) * (1 - random.uniform(0, 0.005)), 2),
        "volume": random.randint(100_000, 10_000_000),
        "oi": random.randint(0, 500_000),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _generate_position() -> dict[str, Any]:
    """Generate a mock portfolio position."""
    symbol = random.choice(_SYMBOLS)
    base = _BASE_PRICES[symbol]
    qty = random.choice([-100, -50, 50, 100, 200])
    avg_price = round(base * (1 + random.uniform(-0.02, 0.02)), 2)
    ltp = round(base * (1 + random.gauss(0, 0.005)), 2)
    unrealized = round((ltp - avg_price) * qty, 2)

    return {
        "symbol": symbol,
        "quantity": qty,
        "average_price": avg_price,
        "ltp": ltp,
        "pnl_unrealized": unrealized,
        "pnl_realized": round(random.uniform(-5000, 8000), 2),
        "product_type": random.choice(["MIS", "NRML"]),
    }


def _generate_portfolio_snapshot() -> dict[str, Any]:
    """Generate a full portfolio P&L snapshot — uses Fyers if connected."""
    # Try live data from Fyers
    if live_feed and hasattr(live_feed, '_fyers') and live_feed._fyers:
        try:
            pos_result = live_feed._fyers.positions()
            if pos_result and pos_result.get("s") == "ok":
                fyers_positions = pos_result.get("netPositions", pos_result.get("overall", []))
                if isinstance(fyers_positions, list):
                    positions = []
                    for p in fyers_positions:
                        sym = p.get("symbol", "")
                        positions.append({
                            "symbol": sym.split(":")[1] if ":" in sym else sym,
                            "quantity": p.get("netQty", p.get("qty", 0)),
                            "avg_price": round(float(p.get("avgPrice", p.get("buyAvgPrice", 0))), 2),
                            "ltp": round(float(p.get("ltp", 0)), 2),
                            "pnl_unrealized": round(float(p.get("unrealizedProfit", p.get("pl", 0))), 2),
                            "pnl_realized": round(float(p.get("realized_profit", p.get("realizedProfit", 0))), 2),
                        })
                    total_unrealized = sum(p["pnl_unrealized"] for p in positions)
                    total_realized = sum(p["pnl_realized"] for p in positions)

                    # Try getting margin from funds
                    margin_used = 0.0
                    margin_available = 0.0
                    try:
                        fund_result = live_feed._fyers.funds()
                        if fund_result and fund_result.get("s") == "ok":
                            for item in (fund_result.get("fund_limit", []) if isinstance(fund_result.get("fund_limit"), list) else []):
                                title = item.get("title", "").lower()
                                val = float(item.get("equityAmount", item.get("amount", 0)))
                                if "utilized" in title or "used" in title:
                                    margin_used = val
                                elif "available" in title or "net" in title:
                                    margin_available = val
                    except Exception:
                        pass

                    return {
                        "positions": positions,
                        "total_unrealized_pnl": round(total_unrealized, 2),
                        "total_realized_pnl": round(total_realized, 2),
                        "total_pnl": round(total_unrealized + total_realized, 2),
                        "margin_used": round(margin_used, 2),
                        "margin_available": round(margin_available, 2),
                        "source": "fyers_live",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
        except Exception as e:
            logger.warning(f"Fyers portfolio snapshot failed: {e}")

    # Fallback: compute from deployed strategies (paper mode) instead of mock
    try:
        from core import api as _api_mod
        deployed = getattr(_api_mod, "_deployed_strategies", {}) or {}
        total_unrealized = 0.0
        total_realized = 0.0
        positions = []
        for strat in deployed.values():
            status = str(strat.get("status", "")).upper()
            if status in ("EXITED", "STOPPED"):
                total_realized += float(strat.get("realized_pnl", strat.get("pnl", 0)) or 0)
            elif status in ("RUNNING", "ENTERED"):
                if strat.get("entered"):
                    total_unrealized += float(strat.get("pnl", 0) or 0)
                    for pos in strat.get("positions", []):
                        positions.append({
                            "symbol": pos.get("symbol", ""),
                            "quantity": pos.get("qty", 0),
                            "avg_price": round(float(pos.get("entry_price", 0) or 0), 2),
                            "ltp": round(float(pos.get("ltp", pos.get("entry_price", 0)) or 0), 2),
                            "pnl_unrealized": round(float(pos.get("pnl", 0) or 0), 2),
                            "pnl_realized": 0.0,
                        })
                else:
                    # Show pending positions (deployed but not yet entered)
                    for pos in strat.get("positions", []):
                        positions.append({
                            "symbol": pos.get("symbol", ""),
                            "quantity": pos.get("qty", 0),
                            "avg_price": 0.0,
                            "ltp": 0.0,
                            "pnl_unrealized": 0.0,
                            "pnl_realized": 0.0,
                            "status": "PENDING_ENTRY",
                        })

        # Margin from Risk Engine
        margin_used = 0.0
        margin_available = 1_000_000.0
        try:
            from core import risk_engine as re
            chain_cache = getattr(_api_mod, "_fyers_chain_cache", {}) or {}
            margin = re.calculate_margin(deployed, chain_cache, available_capital=1_000_000.0)
            margin_used = margin.get("total_margin_required", 0)
            margin_available = margin.get("available_margin", 1_000_000.0)
        except Exception:
            pass

        return {
            "positions": positions,
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_realized_pnl": round(total_realized, 2),
            "total_pnl": round(total_unrealized + total_realized, 2),
            "margin_used": round(margin_used, 2),
            "margin_available": round(margin_available, 2),
            "source": "deployed_strategies",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.warning(f"Deployed strategies portfolio fallback failed: {e}")
        # Zero-state instead of random mock
        return {
            "positions": [],
            "total_unrealized_pnl": 0.0,
            "total_realized_pnl": 0.0,
            "total_pnl": 0.0,
            "margin_used": 0.0,
            "margin_available": 0.0,
            "source": "empty",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _generate_order_update() -> dict[str, Any]:
    """Generate a mock order status update."""
    symbol = random.choice(_SYMBOLS)
    base = _BASE_PRICES[symbol]
    side = random.choice(["BUY", "SELL"])
    status = random.choice(["PLACED", "OPEN", "FILLED", "PARTIAL", "CANCELLED"])
    qty = random.choice([25, 50, 75, 100])
    price = round(base * (1 + random.uniform(-0.01, 0.01)), 2)

    return {
        "order_id": uuid.uuid4().hex[:12],
        "symbol": symbol,
        "side": side,
        "order_type": random.choice(["MARKET", "LIMIT", "SL"]),
        "quantity": qty,
        "price": price,
        "filled_quantity": qty if status == "FILLED" else random.randint(0, qty),
        "average_price": price if status in ("FILLED", "PARTIAL") else 0,
        "status": status,
        "product_type": random.choice(["MIS", "NRML"]),
        "strategy_id": random.choice(["iron-condor-1", "straddle-2", None]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _generate_alert() -> dict[str, Any]:
    """Generate a mock platform alert."""
    alerts = [
        ("WARNING", "risk_engine", "Portfolio delta exceeds threshold: 450 > 400"),
        ("INFO", "strategy_engine", "Iron Condor strategy entered new position"),
        ("CRITICAL", "risk_engine", "Daily loss approaching kill-switch level"),
        ("WARNING", "market_data", "Feed latency spike: 250ms on NIFTY"),
        ("INFO", "oms", "Order filled: BUY 50 NIFTY 24300 CE @ 185.50"),
        ("WARNING", "risk_engine", "Margin utilization at 82%"),
        ("INFO", "strategy_engine", "Straddle adjustment triggered for BANKNIFTY"),
    ]
    level, source, message = random.choice(alerts)

    return {
        "alert_id": uuid.uuid4().hex[:12],
        "level": level,
        "source": source,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "acknowledged": False,
    }


# ===================================================================
# WebSocket endpoint handlers
# ===================================================================


async def _listen_for_client_messages(
    ws: WebSocket,
    client_id: str,
    manager: WebSocketManager,
) -> None:
    """Listen for incoming messages from a client (pong, subscribe, unsubscribe)."""
    try:
        while True:
            raw = await ws.receive_text()
            try:
                import json
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue

            msg_type = msg.get("type", "")

            if msg_type == "pong":
                manager.handle_pong(client_id)
            elif msg_type == "subscribe":
                channels = msg.get("channels", [])
                if isinstance(channels, list):
                    manager.subscribe(client_id, channels)
            elif msg_type == "unsubscribe":
                channels = msg.get("channels", [])
                if isinstance(channels, list):
                    manager.unsubscribe(client_id, channels)

    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/market-data")
async def ws_market_data(
    websocket: WebSocket,
    symbols: str = Query(default="NIFTY,BANKNIFTY", description="Comma-separated symbols"),
) -> None:
    """Stream live market data ticks at ~500ms intervals.

    Query params:
        symbols — comma-separated list of symbols to stream (default: NIFTY,BANKNIFTY)
    """
    manager = _get_manager()
    client_id = f"md-{uuid.uuid4().hex[:8]}"
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    await manager.connect(websocket, client_id, channels=["ticks"])

    # Start listener task for client messages
    listener = asyncio.create_task(
        _listen_for_client_messages(websocket, client_id, manager)
    )

    try:
        use_live = live_feed is not None and live_feed.is_connected
        if use_live:
            logger.info(f"WS market-data: using Fyers cached live feed for {symbol_list}")
        while True:
            for symbol in symbol_list:
                if use_live:
                    cached = live_feed.get_cached_tick(symbol)
                    if cached:
                        tick = {
                            **cached,
                            "bid_qty": random.randint(50, 5000),
                            "ask_qty": random.randint(50, 5000),
                            "oi": cached.get("oi", 0),
                            "source": "fyers_live",
                        }
                    else:
                        tick = _generate_tick(symbol)
                        tick["source"] = "mock_fallback"
                else:
                    tick = _generate_tick(symbol)
                    tick["source"] = "mock"
                await manager.send_to(client_id, {
                    "channel": "ticks",
                    "data": tick,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            await asyncio.sleep(0.2)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        listener.cancel()
        manager.disconnect(client_id)


@router.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket) -> None:
    """Stream portfolio position and P&L updates every 2 seconds."""
    manager = _get_manager()
    client_id = f"pf-{uuid.uuid4().hex[:8]}"

    await manager.connect(websocket, client_id, channels=["positions", "pnl"])

    listener = asyncio.create_task(
        _listen_for_client_messages(websocket, client_id, manager)
    )

    try:
        while True:
            snapshot = _generate_portfolio_snapshot()
            await manager.send_to(client_id, {
                "channel": "pnl",
                "data": snapshot,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        listener.cancel()
        manager.disconnect(client_id)


@router.websocket("/ws/orders")
async def ws_orders(websocket: WebSocket) -> None:
    """Stream order status changes.

    In production this would be driven by real order events from the event bus.
    For now, generates mock order updates every 3-5 seconds.
    """
    manager = _get_manager()
    client_id = f"or-{uuid.uuid4().hex[:8]}"

    await manager.connect(websocket, client_id, channels=["orders", "trades"])

    listener = asyncio.create_task(
        _listen_for_client_messages(websocket, client_id, manager)
    )

    try:
        # Only stream real order events — no more mock order spam.
        # Keep the connection alive with periodic pings; real order updates
        # will be pushed via the event bus / paper trading integration.
        while True:
            await asyncio.sleep(30.0)  # keep-alive interval
            await manager.send_to(client_id, {
                "channel": "ping",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        listener.cancel()
        manager.disconnect(client_id)


@router.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket) -> None:
    """Stream platform alerts as they fire.

    In production, alerts come from the event bus (risk breaches, strategy events).
    For now, generates mock alerts every 5-15 seconds.
    """
    manager = _get_manager()
    client_id = f"al-{uuid.uuid4().hex[:8]}"

    await manager.connect(websocket, client_id, channels=["alerts"])

    listener = asyncio.create_task(
        _listen_for_client_messages(websocket, client_id, manager)
    )

    try:
        # Only stream real alerts from the event bus — no more mock alert spam.
        # Keep the connection alive with periodic pings; real alerts will be
        # pushed via the event bus integration when it is wired up.
        while True:
            await asyncio.sleep(30.0)  # keep-alive interval
            await manager.send_to(client_id, {
                "channel": "ping",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        listener.cancel()
        manager.disconnect(client_id)

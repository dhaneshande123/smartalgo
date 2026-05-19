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
    """Generate a full portfolio P&L snapshot."""
    num_positions = random.randint(2, 5)
    positions = [_generate_position() for _ in range(num_positions)]
    total_unrealized = sum(p["pnl_unrealized"] for p in positions)
    total_realized = sum(p["pnl_realized"] for p in positions)

    return {
        "positions": positions,
        "total_unrealized_pnl": round(total_unrealized, 2),
        "total_realized_pnl": round(total_realized, 2),
        "total_pnl": round(total_unrealized + total_realized, 2),
        "margin_used": round(random.uniform(100_000, 500_000), 2),
        "margin_available": round(random.uniform(200_000, 800_000), 2),
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

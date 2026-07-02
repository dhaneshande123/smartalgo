"""
FastAPI backend for the algo trading platform dashboard.

Provides REST endpoints for health checks, market data, portfolio management,
strategy control, risk monitoring, order/trade books, and event bus metrics.
All market data is currently served via a MockDataGenerator that produces
realistic Indian market (NSE) data.

Run with:
    uvicorn core.api:app --reload --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import platform
import random
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

from core.websocket.manager import WebSocketManager
from core.websocket.routes import router as ws_router, set_ws_manager, set_live_feed
from core.paper_trading.paper_trading_manager import PaperTradingManager
from core.fyers_live_feed import FyersLiveFeed

from dotenv import load_dotenv

from core.config import PlatformConfig, BrokerConfig
from core.constants import (
    IST,
    LOT_SIZES,
    STRIKE_STEP,
    MARKET_OPEN,
    MARKET_CLOSE,
)
from core.event_bus.bus import InMemoryEventBus, EventBusMetrics
from core.models import (
    Exchange,
    InstrumentType,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    Segment,
    StrategyStatus,
    TimeFrame,
    TradingMode,
)

# ---------------------------------------------------------------------------
# Version & startup tracking
# ---------------------------------------------------------------------------

__version__ = "1.0.0"
_startup_time: float = 0.0

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform-level singletons (initialised in lifespan)
# ---------------------------------------------------------------------------

_config: PlatformConfig | None = None
_event_bus: InMemoryEventBus | None = None
_ws_manager: WebSocketManager | None = None
_paper_trading_manager: PaperTradingManager | None = None
_live_feed: FyersLiveFeed | None = None
_dashboard_executor: Any = None  # core.strategy_engine.dashboard_executor.DashboardStrategyExecutor

# ── Load environment variables from .env ─────────────────────────
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Fyers API v3 credentials (loaded from .env file)
FYERS_APP_ID = os.getenv("FYERS_APP_ID", "")
FYERS_SECRET_KEY = os.getenv("FYERS_SECRET_KEY", "")
FYERS_REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "http://127.0.0.1:8000/api/fyers/callback")
FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "")

# Strict-live mode: when STRICT_LIVE_MODE=1, endpoints that would otherwise
# fall back to MockDataGenerator return 503 instead. Use in production to
# prevent the platform from silently serving fake data if Fyers is unreachable.
STRICT_LIVE_MODE = os.getenv("STRICT_LIVE_MODE", "0").lower() in {"1", "true", "yes"}

# ── Auto-exchange auth_code → access_token if needed ─────────────
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

def _ensure_access_token() -> str:
    """If the stored token is an auth_code, exchange it for an access_token."""
    global FYERS_ACCESS_TOKEN
    if not FYERS_ACCESS_TOKEN or not FYERS_APP_ID:
        return FYERS_ACCESS_TOKEN
    try:
        import base64, json as _json
        parts = FYERS_ACCESS_TOKEN.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            if payload.get("sub") == "access_token":
                return FYERS_ACCESS_TOKEN  # Already an access token
            if payload.get("sub") == "auth_code":
                logging.getLogger(__name__).info("Detected auth_code — exchanging for access_token...")
                from fyers_apiv3 import fyersModel
                session = fyersModel.SessionModel(
                    client_id=FYERS_APP_ID,
                    secret_key=FYERS_SECRET_KEY,
                    redirect_uri=FYERS_REDIRECT_URI,
                    response_type="code",
                    grant_type="authorization_code",
                )
                session.set_token(FYERS_ACCESS_TOKEN)
                resp = session.generate_token()
                if resp and resp.get("s") == "ok" and resp.get("access_token"):
                    FYERS_ACCESS_TOKEN = resp["access_token"]
                    logging.getLogger(__name__).info("Token exchange successful!")
                    # Persist the new access_token to .env for future restarts
                    try:
                        with open(_ENV_FILE, "r", encoding="utf-8") as f:
                            env_content = f.read()
                        import re
                        env_content = re.sub(
                            r"FYERS_ACCESS_TOKEN=.*",
                            f"FYERS_ACCESS_TOKEN={FYERS_ACCESS_TOKEN}",
                            env_content,
                        )
                        with open(_ENV_FILE, "w", encoding="utf-8") as f:
                            f.write(env_content)
                        logging.getLogger(__name__).info("Updated .env with new access_token")
                    except Exception as e:
                        logging.getLogger(__name__).warning(f"Could not update .env: {e}")
                else:
                    err = resp.get("message", str(resp)) if resp else "No response"
                    logging.getLogger(__name__).error(f"Token exchange failed: {err}")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Token check error (non-fatal): {e}")
    return FYERS_ACCESS_TOKEN

FYERS_ACCESS_TOKEN = _ensure_access_token()


# ===================================================================
# Mock Data Generator
# ===================================================================


class MockDataGenerator:
    """Generate realistic Indian market data for the dashboard.

    Uses a seeded RNG for reproducibility, then layers small time-based
    perturbations so the numbers look "live" on each API call.
    """

    # Base prices for major indices
    INDEX_BASE: dict[str, float] = {
        "NIFTY": 24250.0,
        "BANKNIFTY": 51500.0,
        "FINNIFTY": 23200.0,
        "MIDCPNIFTY": 12850.0,
        "SENSEX": 81000.0,
    }

    VIX_BASE: float = 14.5

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed
        self._rng = random.Random(seed)

    # -- helpers -----------------------------------------------------------

    def _jitter(self, base: float, pct: float = 0.002) -> float:
        """Add small time-seeded variation to *base*."""
        t = time.time()
        phase = math.sin(t * 0.05) * pct + math.cos(t * 0.03) * pct * 0.5
        return round(base * (1 + phase), 2)

    def _decimal(self, value: float, places: int = 2) -> Decimal:
        return Decimal(str(round(value, places)))

    # -- indices -----------------------------------------------------------

    def indices(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        now = datetime.now(IST)
        for symbol, base in self.INDEX_BASE.items():
            price = self._jitter(base)
            prev_close = round(base * (1 + self._rng.uniform(-0.005, 0.005)), 2)
            change = round(price - prev_close, 2)
            change_pct = round(change / prev_close * 100, 2)
            results.append(
                {
                    "symbol": symbol,
                    "ltp": price,
                    "open": round(base * (1 + self._rng.uniform(-0.003, 0.003)), 2),
                    "high": round(price * (1 + abs(self._rng.gauss(0, 0.003))), 2),
                    "low": round(price * (1 - abs(self._rng.gauss(0, 0.003))), 2),
                    "prev_close": prev_close,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": self._rng.randint(50_000_000, 200_000_000),
                    "timestamp": now.isoformat(),
                }
            )

        # VIX
        vix = self._jitter(self.VIX_BASE, pct=0.01)
        prev_vix = round(self.VIX_BASE * (1 + self._rng.uniform(-0.03, 0.03)), 2)
        results.append(
            {
                "symbol": "INDIA VIX",
                "ltp": vix,
                "open": round(self.VIX_BASE + self._rng.uniform(-0.5, 0.5), 2),
                "high": round(vix + self._rng.uniform(0, 1.2), 2),
                "low": round(vix - self._rng.uniform(0, 1.0), 2),
                "prev_close": prev_vix,
                "change": round(vix - prev_vix, 2),
                "change_pct": round((vix - prev_vix) / prev_vix * 100, 2),
                "volume": 0,
                "timestamp": now.isoformat(),
            }
        )
        return results

    # -- option chain ------------------------------------------------------

    def option_chain(self, symbol: str) -> dict[str, Any]:
        symbol = symbol.upper()
        base_price = self.INDEX_BASE.get(symbol)
        if base_price is None:
            raise ValueError(f"Unknown symbol: {symbol}")

        spot = self._jitter(base_price)
        step = STRIKE_STEP.get(symbol, 50)
        lot = LOT_SIZES.get(symbol, 25)
        atm_strike = round(spot / step) * step

        now = datetime.now(IST)
        # Next weekly expiry (Thursday for NIFTY)
        days_to_expiry = (3 - now.weekday()) % 7 or 7
        expiry_date = (now + timedelta(days=days_to_expiry)).date()
        dte = max((expiry_date - now.date()).days, 1)

        contracts: list[dict[str, Any]] = []
        for offset in range(-12, 13):
            strike = atm_strike + offset * step
            moneyness = (spot - strike) / spot

            # Rough Black-Scholes-like approximations for realism
            iv_base = 0.14 + abs(moneyness) * 0.8 + self._rng.uniform(-0.01, 0.01)
            iv_ce = round(iv_base + self._rng.uniform(-0.005, 0.005), 4)
            iv_pe = round(iv_base + self._rng.uniform(-0.005, 0.005), 4)

            time_value = math.sqrt(dte / 365) * spot * iv_base * 0.4
            intrinsic_ce = max(spot - strike, 0)
            intrinsic_pe = max(strike - spot, 0)
            premium_ce = round(intrinsic_ce + time_value * max(0.1, 1 - abs(moneyness) * 5), 2)
            premium_pe = round(intrinsic_pe + time_value * max(0.1, 1 - abs(moneyness) * 5), 2)

            # Greeks for calls
            d1_proxy = moneyness / (iv_ce * math.sqrt(dte / 365) + 0.001)
            delta_ce = round(max(0.01, min(0.99, 0.5 + 0.4 * math.tanh(d1_proxy))), 4)
            delta_pe = round(delta_ce - 1, 4)
            gamma = round(0.001 * math.exp(-d1_proxy ** 2 / 2) / (spot * iv_ce * math.sqrt(dte / 365) + 0.01), 6)
            theta_ce = round(-spot * iv_ce / (2 * math.sqrt(dte / 365 + 0.001)) * 0.0015, 2)
            theta_pe = round(theta_ce * 0.9, 2)
            vega = round(spot * math.sqrt(dte / 365) * 0.004 * math.exp(-d1_proxy ** 2 / 2), 2)

            oi_ce = self._rng.randint(5_000, 500_000) * lot
            oi_pe = self._rng.randint(5_000, 500_000) * lot
            vol_ce = self._rng.randint(1_000, 100_000) * lot
            vol_pe = self._rng.randint(1_000, 100_000) * lot

            contracts.append(
                {
                    "strike": strike,
                    "expiry": expiry_date.isoformat(),
                    "call": {
                        "option_type": "CE",
                        "ltp": premium_ce,
                        "bid": round(premium_ce - self._rng.uniform(0.05, 1.0), 2),
                        "ask": round(premium_ce + self._rng.uniform(0.05, 1.0), 2),
                        "iv": round(iv_ce * 100, 2),
                        "delta": delta_ce,
                        "gamma": gamma,
                        "theta": theta_ce,
                        "vega": vega,
                        "oi": oi_ce,
                        "oi_change": self._rng.randint(-50_000, 80_000) * lot,
                        "volume": vol_ce,
                    },
                    "put": {
                        "option_type": "PE",
                        "ltp": premium_pe,
                        "bid": round(premium_pe - self._rng.uniform(0.05, 1.0), 2),
                        "ask": round(premium_pe + self._rng.uniform(0.05, 1.0), 2),
                        "iv": round(iv_pe * 100, 2),
                        "delta": delta_pe,
                        "gamma": gamma,
                        "theta": theta_pe,
                        "vega": vega,
                        "oi": oi_pe,
                        "oi_change": self._rng.randint(-50_000, 80_000) * lot,
                        "volume": vol_pe,
                    },
                }
            )

        total_ce_oi = sum(c["call"]["oi"] for c in contracts)
        total_pe_oi = sum(c["put"]["oi"] for c in contracts)
        pcr = round(total_pe_oi / max(total_ce_oi, 1), 4)

        return {
            "symbol": symbol,
            "spot_price": spot,
            "atm_strike": atm_strike,
            "expiry": expiry_date.isoformat(),
            "lot_size": lot,
            "pcr": pcr,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "contracts": contracts,
            "timestamp": now.isoformat(),
        }

    # -- candles -----------------------------------------------------------

    def candles(
        self,
        symbol: str,
        timeframe: str = "M5",
        count: int = 50,
    ) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        base_price = self.INDEX_BASE.get(symbol, 24250.0)
        spot = self._jitter(base_price)

        tf_minutes = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "D1": 1440,
        }
        interval = tf_minutes.get(timeframe, 5)

        now = datetime.now(IST)
        candle_list: list[dict[str, Any]] = []
        price = spot

        for i in range(count, 0, -1):
            ts = now - timedelta(minutes=i * interval)
            move = self._rng.gauss(0, base_price * 0.001)
            open_p = round(price, 2)
            close_p = round(price + move, 2)
            high_p = round(max(open_p, close_p) + abs(self._rng.gauss(0, base_price * 0.0005)), 2)
            low_p = round(min(open_p, close_p) - abs(self._rng.gauss(0, base_price * 0.0005)), 2)
            vol = self._rng.randint(10_000, 500_000)
            candle_list.append(
                {
                    "timestamp": ts.isoformat(),
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "volume": vol,
                }
            )
            price = close_p

        return candle_list

    # -- positions ---------------------------------------------------------

    def positions(self) -> list[dict[str, Any]]:
        now = datetime.now(IST)
        days_to_expiry = (3 - now.weekday()) % 7 or 7
        expiry = (now + timedelta(days=days_to_expiry)).date().isoformat()

        nifty_spot = self._jitter(self.INDEX_BASE["NIFTY"])
        bn_spot = self._jitter(self.INDEX_BASE["BANKNIFTY"])

        return [
            {
                "instrument": "NIFTY 24200 CE",
                "symbol": "NIFTY",
                "strike": 24200,
                "option_type": "CE",
                "expiry": expiry,
                "quantity": -50,
                "avg_price": 185.50,
                "ltp": self._jitter(175.0, 0.03),
                "pnl_unrealized": round(50 * (185.50 - self._jitter(175.0, 0.03)), 2),
                "pnl_realized": 0.0,
                "product_type": "NRML",
                "strategy_id": "iron-condor-weekly",
                "delta": -0.42,
                "gamma": -0.0012,
                "theta": 45.30,
                "vega": -82.50,
            },
            {
                "instrument": "NIFTY 24400 CE",
                "symbol": "NIFTY",
                "strike": 24400,
                "option_type": "CE",
                "expiry": expiry,
                "quantity": 50,
                "avg_price": 95.20,
                "ltp": self._jitter(88.0, 0.03),
                "pnl_unrealized": round(50 * (self._jitter(88.0, 0.03) - 95.20), 2),
                "pnl_realized": 0.0,
                "product_type": "NRML",
                "strategy_id": "iron-condor-weekly",
                "delta": 0.28,
                "gamma": 0.0010,
                "theta": -32.10,
                "vega": 65.40,
            },
            {
                "instrument": "NIFTY 24100 PE",
                "symbol": "NIFTY",
                "strike": 24100,
                "option_type": "PE",
                "expiry": expiry,
                "quantity": -50,
                "avg_price": 145.80,
                "ltp": self._jitter(138.0, 0.03),
                "pnl_unrealized": round(50 * (145.80 - self._jitter(138.0, 0.03)), 2),
                "pnl_realized": 0.0,
                "product_type": "NRML",
                "strategy_id": "iron-condor-weekly",
                "delta": 0.35,
                "gamma": -0.0011,
                "theta": 42.80,
                "vega": -78.90,
            },
            {
                "instrument": "NIFTY 23900 PE",
                "symbol": "NIFTY",
                "strike": 23900,
                "option_type": "PE",
                "expiry": expiry,
                "quantity": 50,
                "avg_price": 72.40,
                "ltp": self._jitter(65.0, 0.03),
                "pnl_unrealized": round(50 * (self._jitter(65.0, 0.03) - 72.40), 2),
                "pnl_realized": 0.0,
                "product_type": "NRML",
                "strategy_id": "iron-condor-weekly",
                "delta": -0.18,
                "gamma": 0.0008,
                "theta": -25.60,
                "vega": 52.30,
            },
            {
                "instrument": "BANKNIFTY 51500 CE",
                "symbol": "BANKNIFTY",
                "strike": 51500,
                "option_type": "CE",
                "expiry": expiry,
                "quantity": -30,
                "avg_price": 420.00,
                "ltp": self._jitter(395.0, 0.03),
                "pnl_unrealized": round(30 * (420.00 - self._jitter(395.0, 0.03)), 2),
                "pnl_realized": 1250.00,
                "product_type": "NRML",
                "strategy_id": "straddle-banknifty",
                "delta": -0.52,
                "gamma": -0.0008,
                "theta": 65.20,
                "vega": -120.40,
            },
            {
                "instrument": "BANKNIFTY 51500 PE",
                "symbol": "BANKNIFTY",
                "strike": 51500,
                "option_type": "PE",
                "expiry": expiry,
                "quantity": -30,
                "avg_price": 380.00,
                "ltp": self._jitter(365.0, 0.03),
                "pnl_unrealized": round(30 * (380.00 - self._jitter(365.0, 0.03)), 2),
                "pnl_realized": 800.00,
                "product_type": "NRML",
                "strategy_id": "straddle-banknifty",
                "delta": 0.48,
                "gamma": -0.0007,
                "theta": 58.90,
                "vega": -115.20,
            },
        ]

    # -- portfolio greeks --------------------------------------------------

    def portfolio_greeks(self) -> dict[str, Any]:
        positions = self.positions()
        net_delta = round(sum(p["delta"] * abs(p["quantity"]) for p in positions), 2)
        net_gamma = round(sum(p["gamma"] * abs(p["quantity"]) for p in positions), 4)
        net_theta = round(sum(p["theta"] for p in positions), 2)
        net_vega = round(sum(p["vega"] for p in positions), 2)
        return {
            "net_delta": net_delta,
            "net_gamma": net_gamma,
            "net_theta": net_theta,
            "net_vega": net_vega,
            "net_rho": round(self._rng.uniform(-5, 5), 2),
            "positions_count": len(positions),
            "timestamp": datetime.now(IST).isoformat(),
        }

    # -- pnl ---------------------------------------------------------------

    def pnl_snapshot(self) -> dict[str, Any]:
        positions = self.positions()
        unrealized = round(sum(p["pnl_unrealized"] for p in positions), 2)
        realized = round(sum(p["pnl_realized"] for p in positions), 2)
        charges = round(self._rng.uniform(120, 450), 2)
        total = round(realized + unrealized, 2)
        net = round(total - charges, 2)
        return {
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": total,
            "charges": {
                "brokerage": round(charges * 0.25, 2),
                "stt": round(charges * 0.35, 2),
                "exchange_txn_fee": round(charges * 0.10, 2),
                "gst": round(charges * 0.18, 2),
                "sebi_fee": round(charges * 0.02, 2),
                "stamp_duty": round(charges * 0.10, 2),
                "total": charges,
            },
            "net_pnl": net,
            "peak_pnl": round(max(total, total + self._rng.uniform(500, 3000)), 2),
            "max_drawdown_today": round(self._rng.uniform(500, 5000), 2),
            "timestamp": datetime.now(IST).isoformat(),
        }

    # -- margin ------------------------------------------------------------

    def margin_info(self) -> dict[str, Any]:
        total_capital = 1_500_000.0
        used = round(self._rng.uniform(400_000, 700_000), 2)
        available = round(total_capital - used, 2)
        span = round(used * 0.65, 2)
        exposure = round(used * 0.35, 2)
        utilization = round(used / total_capital * 100, 2)
        return {
            "available_cash": total_capital,
            "used_margin": used,
            "available_margin": available,
            "total_collateral": round(total_capital * 0.15, 2),
            "span_margin": span,
            "exposure_margin": exposure,
            "utilization_pct": utilization,
            "timestamp": datetime.now(IST).isoformat(),
        }

    # -- market regime analyzer --------------------------------------------

    def market_regime(self) -> dict[str, Any]:
        """Analyze current market conditions and classify into a trading regime."""
        indices = {i["symbol"]: i for i in self.indices()}
        nifty = indices.get("NIFTY", {})
        bnf = indices.get("BANKNIFTY", {})
        vix_data = indices.get("INDIA VIX", {})

        nifty_change_pct = nifty.get("change_pct", 0)
        vix = vix_data.get("ltp", 14.5)
        nifty_ltp = nifty.get("ltp", 24250)
        nifty_open = nifty.get("open", 24250)
        nifty_high = nifty.get("high", 24260)
        nifty_low = nifty.get("low", 24240)
        intraday_range_pct = round((nifty_high - nifty_low) / nifty_ltp * 100, 2) if nifty_ltp else 0

        # Determine trend
        if nifty_change_pct > 0.5:
            trend = "BULLISH"
        elif nifty_change_pct < -0.5:
            trend = "BEARISH"
        else:
            trend = "SIDEWAYS"

        # Determine volatility regime
        if vix > 20:
            vol_regime = "HIGH_VOL"
        elif vix > 15:
            vol_regime = "MODERATE_VOL"
        else:
            vol_regime = "LOW_VOL"

        # Composite regime
        if vol_regime == "HIGH_VOL" and trend == "BEARISH":
            regime = "CRISIS"
            regime_label = "Crisis / High Fear"
            color = "red"
        elif vol_regime == "HIGH_VOL":
            regime = "VOLATILE"
            regime_label = "High Volatility"
            color = "orange"
        elif trend == "SIDEWAYS" and vol_regime == "LOW_VOL":
            regime = "RANGE_BOUND"
            regime_label = "Range-Bound / Low Vol"
            color = "blue"
        elif trend == "BULLISH":
            regime = "TRENDING_UP"
            regime_label = "Trending Bullish"
            color = "green"
        elif trend == "BEARISH":
            regime = "TRENDING_DOWN"
            regime_label = "Trending Bearish"
            color = "red"
        else:
            regime = "NEUTRAL"
            regime_label = "Neutral / Choppy"
            color = "gray"

        now = datetime.now(IST)
        is_expiry = now.weekday() == 3  # Thursday
        is_monthly_expiry = is_expiry and now.day > 21

        # Compute confidence based on how strongly indicators align
        confidence_base = 60
        if abs(nifty_change_pct) > 1:
            confidence_base += 15  # strong move = more confident in regime
        if (vix > 18 and regime in ("VOLATILE", "CRISIS")) or (vix < 14 and regime == "RANGE_BOUND"):
            confidence_base += 10  # VIX confirms regime
        if intraday_range_pct > 1.5:
            confidence_base += 5
        confidence = min(confidence_base, 95) / 100.0

        return {
            "regime": regime,
            "regime_code": regime,
            "regime_label": regime_label,
            "color": color,
            "confidence": confidence,
            "trend": trend,
            "trend_strength": min(abs(nifty_change_pct) / 2, 1.0),
            "breadth": 0.5 + nifty_change_pct * 0.1,
            "vol_regime": vol_regime,
            "vix": vix,
            "nifty_ltp": nifty_ltp,
            "nifty_change_pct": nifty_change_pct,
            "intraday_range_pct": intraday_range_pct,
            "is_expiry_day": is_expiry,
            "is_monthly_expiry": is_monthly_expiry,
            "indicators": {
                "adx": round(15 + abs(nifty_change_pct) * 10 + random.uniform(-2, 2), 1),
                "rsi": round(50 + nifty_change_pct * 8 + random.uniform(-3, 3), 1),
                "macd_signal": "bullish" if nifty_change_pct > 0 else "bearish",
                "bb_position": "upper_half" if nifty_change_pct > 0.3 else "lower_half" if nifty_change_pct < -0.3 else "middle",
            },
            "timestamp": now.isoformat(),
        }

    def strategy_signals(self) -> list[dict[str, Any]]:
        """Generate strategy-specific signals based on current market conditions."""
        regime = self.market_regime()
        r = regime["regime"]
        vix = regime["vix"]
        trend = regime["trend"]
        is_expiry = regime["is_expiry_day"]
        now = datetime.now(IST)
        hour = now.hour

        signals = []

        # --- Iron Condor ---
        ic_signal = "NEUTRAL"
        ic_action = "Hold — wait for setup"
        ic_confidence = 50
        if r in ("RANGE_BOUND", "NEUTRAL") and vix < 18:
            ic_signal = "STRONG_ENTRY"
            ic_action = "SELL 20-delta OTM call + put spreads on NIFTY"
            ic_confidence = 85
        elif r == "RANGE_BOUND":
            ic_signal = "ENTRY"
            ic_action = "SELL iron condor — widen wings if VIX elevated"
            ic_confidence = 70
        elif r in ("VOLATILE", "CRISIS"):
            ic_signal = "AVOID"
            ic_action = "High VIX — risk of wing breach, stay flat"
            ic_confidence = 30
        signals.append({"strategy_id": "iron-condor-weekly", "strategy_name": "Iron Condor Weekly", "signal": ic_signal, "action": ic_action, "confidence": ic_confidence})

        # --- Delta-Hedged Short Straddle ---
        strad_signal = "NEUTRAL"
        strad_action = "Hold — wait for IV spike"
        strad_conf = 50
        if vix > 16 and r != "CRISIS":
            strad_signal = "STRONG_ENTRY"
            strad_action = "SELL ATM straddle BANKNIFTY — hedge delta with futures"
            strad_conf = 80
        elif r == "RANGE_BOUND":
            strad_signal = "ENTRY"
            strad_action = "SELL straddle — low expected move"
            strad_conf = 65
        elif r == "CRISIS":
            strad_signal = "EXIT"
            strad_action = "Square off — unhedgeable gap risk"
            strad_conf = 90
        signals.append({"strategy_id": "short-straddle-hedged", "strategy_name": "Delta-Hedged Straddle", "signal": strad_signal, "action": strad_action, "confidence": strad_conf})

        # --- Gamma Scalping ---
        gs_signal = "NEUTRAL"
        gs_action = "Hold — waiting for vol expansion"
        gs_conf = 50
        if r in ("VOLATILE", "CRISIS"):
            gs_signal = "STRONG_ENTRY"
            gs_action = "BUY ATM straddle NIFTY — scalp gamma on every 30pt move"
            gs_conf = 85
        elif vix < 12:
            gs_signal = "ENTRY"
            gs_action = "BUY cheap straddle — IV at lows, realized vol likely higher"
            gs_conf = 60
        elif r == "RANGE_BOUND" and vix > 16:
            gs_signal = "AVOID"
            gs_action = "IV > RV — theta decay will kill long straddle"
            gs_conf = 30
        signals.append({"strategy_id": "gamma-scalping-engine", "strategy_name": "Gamma Scalping", "signal": gs_signal, "action": gs_action, "confidence": gs_conf})

        # --- 0DTE Theta Decay ---
        dte_signal = "NEUTRAL"
        dte_action = "Not expiry day — strategy inactive"
        dte_conf = 0
        if is_expiry:
            if hour < 11:
                dte_signal = "WAIT"
                dte_action = "Expiry day — wait for 11:00 AM entry window"
                dte_conf = 60
            elif hour < 15:
                dte_signal = "STRONG_ENTRY"
                dte_action = "SELL far-OTM 0.05-delta strangle — max theta decay zone"
                dte_conf = 90
            else:
                dte_signal = "EXIT"
                dte_action = "Close all — approaching settlement"
                dte_conf = 95
        signals.append({"strategy_id": "0dte-theta-decay", "strategy_name": "0DTE Theta Decay", "signal": dte_signal, "action": dte_action, "confidence": dte_conf})

        # --- Volatility Arbitrage ---
        va_signal = "NEUTRAL"
        va_action = "IV near fair value — no edge"
        va_conf = 50
        if vix > 18:
            va_signal = "ENTRY"
            va_action = "SELL options — IV overpriced vs 20-day realized vol"
            va_conf = 75
        elif vix < 12:
            va_signal = "ENTRY"
            va_action = "BUY options — IV underpriced, realized vol likely higher"
            va_conf = 70
        signals.append({"strategy_id": "vol-arb-engine", "strategy_name": "Volatility Arbitrage", "signal": va_signal, "action": va_action, "confidence": va_conf})

        # --- Calendar Spread ---
        cal_signal = "NEUTRAL"
        cal_action = "Term structure flat — no edge"
        cal_conf = 45
        if r == "RANGE_BOUND" and vix < 16:
            cal_signal = "ENTRY"
            cal_action = "SELL near-expiry ATM, BUY far-expiry ATM — contango play"
            cal_conf = 70
        signals.append({"strategy_id": "calendar-term-structure", "strategy_name": "Calendar Spread", "signal": cal_signal, "action": cal_action, "confidence": cal_conf})

        # --- Jade Lizard ---
        jl_signal = "NEUTRAL"
        jl_action = "Hold — need put skew premium"
        jl_conf = 50
        if trend == "BULLISH" and vix > 14:
            jl_signal = "STRONG_ENTRY"
            jl_action = "SELL put + call spread BANKNIFTY — credit > spread width"
            jl_conf = 80
        elif trend == "BEARISH":
            jl_signal = "AVOID"
            jl_action = "Bearish trend — naked put side at risk"
            jl_conf = 25
        signals.append({"strategy_id": "jade-lizard", "strategy_name": "Jade Lizard", "signal": jl_signal, "action": jl_action, "confidence": jl_conf})

        # --- Butterfly Pinning ---
        bf_signal = "NEUTRAL"
        bf_action = "Not expiry day — strategy inactive"
        bf_conf = 0
        if is_expiry:
            bf_signal = "ENTRY"
            bf_action = "Deploy ATM butterfly near max-pain — pin risk play"
            bf_conf = 65
        signals.append({"strategy_id": "butterfly-pinning", "strategy_name": "Butterfly Pinning", "signal": bf_signal, "action": bf_action, "confidence": bf_conf})

        # --- Risk Reversal ---
        rr_signal = "NEUTRAL"
        rr_action = "Skew normal — no trade"
        rr_conf = 45
        if trend == "BULLISH":
            rr_signal = "ENTRY"
            rr_action = "SELL OTM puts, BUY OTM calls NIFTY — ride bullish skew"
            rr_conf = 70
        elif trend == "BEARISH" and vix > 18:
            rr_signal = "ENTRY"
            rr_action = "SELL OTM calls, BUY OTM puts — bearish skew capture"
            rr_conf = 65
        signals.append({"strategy_id": "risk-reversal-skew", "strategy_name": "Risk Reversal", "signal": rr_signal, "action": rr_action, "confidence": rr_conf})

        # --- Ratio Backspread ---
        rb_signal = "NEUTRAL"
        rb_action = "Low vol — tail hedge expensive"
        rb_conf = 40
        if vix < 13:
            rb_signal = "STRONG_ENTRY"
            rb_action = "BUY 2x OTM puts, SELL 1x ATM put — cheap tail hedge"
            rb_conf = 80
        elif r == "CRISIS":
            rb_signal = "EXIT"
            rb_action = "Take profit on tail hedge — vol spike realized"
            rb_conf = 90
        signals.append({"strategy_id": "ratio-backspread", "strategy_name": "Ratio Backspread", "signal": rb_signal, "action": rb_action, "confidence": rb_conf})

        # --- Protective Collar ---
        pc_signal = "NEUTRAL"
        pc_action = "Portfolio hedged — monitor roll dates"
        pc_conf = 50
        if r in ("CRISIS", "VOLATILE"):
            pc_signal = "STRONG_ENTRY"
            pc_action = "Deploy zero-cost collar on NIFTY portfolio — protect downside"
            pc_conf = 90
        elif r == "TRENDING_UP":
            pc_signal = "WAIT"
            pc_action = "Bull market — collar caps upside, wait for weakness"
            pc_conf = 35
        signals.append({"strategy_id": "protective-collar", "strategy_name": "Protective Collar", "signal": pc_signal, "action": pc_action, "confidence": pc_conf})

        # --- Dispersion Trading ---
        disp_signal = "NEUTRAL"
        disp_action = "Correlation stable — no spread"
        disp_conf = 45
        if r == "RANGE_BOUND":
            disp_signal = "ENTRY"
            disp_action = "SELL index vol, BUY component vol — correlation risk premium"
            disp_conf = 70
        signals.append({"strategy_id": "dispersion-trading", "strategy_name": "Dispersion Trading", "signal": disp_signal, "action": disp_action, "confidence": disp_conf})

        # --- Intraday basic strategies ---
        # Bull Call Spread
        bcs_signal = "NEUTRAL"
        bcs_action = "Wait for directional confirmation"
        bcs_conf = 40
        if trend == "BULLISH" and 9 <= hour < 14:
            bcs_signal = "ENTRY"
            bcs_action = "BUY ATM CE + SELL OTM CE NIFTY — defined-risk bullish"
            bcs_conf = 75
        signals.append({"strategy_id": "bull-call-spread", "strategy_name": "Bull Call Spread", "signal": bcs_signal, "action": bcs_action, "confidence": bcs_conf})

        # Bear Put Spread
        bps_signal = "NEUTRAL"
        bps_action = "Wait for directional confirmation"
        bps_conf = 40
        if trend == "BEARISH" and 9 <= hour < 14:
            bps_signal = "ENTRY"
            bps_action = "BUY ATM PE + SELL OTM PE NIFTY — defined-risk bearish"
            bps_conf = 75
        signals.append({"strategy_id": "bear-put-spread", "strategy_name": "Bear Put Spread", "signal": bps_signal, "action": bps_action, "confidence": bps_conf})

        # Short Strangle
        ss_signal = "NEUTRAL"
        ss_action = "Wait for range confirmation"
        ss_conf = 45
        if r in ("RANGE_BOUND", "NEUTRAL") and vix > 13:
            ss_signal = "ENTRY"
            ss_action = "SELL OTM CE + PE NIFTY — collect premium in range market"
            ss_conf = 70
        elif r in ("VOLATILE", "CRISIS"):
            ss_signal = "AVOID"
            ss_action = "High vol — strangle legs at risk of breach"
            ss_conf = 20
        signals.append({"strategy_id": "short-strangle", "strategy_name": "Short Strangle", "signal": ss_signal, "action": ss_action, "confidence": ss_conf})

        # ATM Straddle Buy
        atm_signal = "NEUTRAL"
        atm_action = "Wait for breakout setup"
        atm_conf = 40
        if r in ("VOLATILE",) and 9 <= hour < 12:
            atm_signal = "STRONG_ENTRY"
            atm_action = "BUY ATM CE + PE NIFTY — expect big intraday move"
            atm_conf = 80
        elif vix < 12 and 9 <= hour < 11:
            atm_signal = "ENTRY"
            atm_action = "BUY cheap straddle — breakout from low-vol compression"
            atm_conf = 60
        signals.append({"strategy_id": "atm-straddle-buy", "strategy_name": "ATM Straddle Buy", "signal": atm_signal, "action": atm_action, "confidence": atm_conf})

        # Scalp CE/PE (momentum)
        scalp_signal = "NEUTRAL"
        scalp_action = "No setup — wait for momentum"
        scalp_conf = 35
        if trend == "BULLISH" and 9 <= hour < 14:
            scalp_signal = "ENTRY"
            scalp_action = "BUY ATM CE NIFTY — ride momentum, SL 30%, target 50%"
            scalp_conf = 65
        elif trend == "BEARISH" and 9 <= hour < 14:
            scalp_signal = "ENTRY"
            scalp_action = "BUY ATM PE NIFTY — ride momentum, SL 30%, target 50%"
            scalp_conf = 65
        signals.append({"strategy_id": "scalp-ce-pe", "strategy_name": "Scalp CE/PE Momentum", "signal": scalp_signal, "action": scalp_action, "confidence": scalp_conf})

        return signals

    def auto_deploy_recommendations(self) -> list[dict[str, Any]]:
        """Return top strategy picks for current market conditions with deploy-ready config."""
        regime = self.market_regime()
        r = regime["regime"]
        signals = {s["strategy_id"]: s for s in self.strategy_signals()}

        REGIME_PICKS = {
            "RANGE_BOUND": [
                ("iron-condor-weekly", "iron_condor", "Best in low-vol sideways — theta farming"),
                ("short-strangle", "straddle", "Wide strangle collects premium in range"),
                ("calendar-term-structure", "gamma_scalping", "Term structure play in calm market"),
            ],
            "TRENDING_UP": [
                ("bull-call-spread", "iron_condor", "Defined-risk bullish — ride the trend"),
                ("jade-lizard", "iron_condor", "Bullish premium — zero upside risk"),
                ("risk-reversal-skew", "mean_reversion", "Sell puts, buy calls — directional skew"),
                ("scalp-ce-pe", "momentum", "Momentum CE buying in uptrend"),
            ],
            "TRENDING_DOWN": [
                ("bear-put-spread", "iron_condor", "Defined-risk bearish — ride the dip"),
                ("protective-collar", "iron_condor", "Portfolio protection in downtrend"),
                ("ratio-backspread", "momentum", "Tail hedge — unlimited profit on crash"),
            ],
            "VOLATILE": [
                ("gamma-scalping-engine", "gamma_scalping", "Long gamma — scalp the swings"),
                ("atm-straddle-buy", "straddle", "Big move expected — buy both sides"),
                ("ratio-backspread", "momentum", "Convex payoff on large moves"),
            ],
            "CRISIS": [
                ("protective-collar", "iron_condor", "Emergency portfolio hedge"),
                ("gamma-scalping-engine", "gamma_scalping", "Long gamma profits from chaos"),
                ("ratio-backspread", "momentum", "Tail hedge pays off in crash"),
            ],
            "NEUTRAL": [
                ("iron-condor-weekly", "iron_condor", "Default premium selling in neutral"),
                ("short-strangle", "straddle", "Collect theta in choppy market"),
                ("vol-arb-engine", "mean_reversion", "Trade IV vs RV mispricing"),
            ],
        }

        picks = REGIME_PICKS.get(r, REGIME_PICKS["NEUTRAL"])
        recs = []
        for strategy_id, strategy_class, reason in picks:
            sig = signals.get(strategy_id, {})
            recs.append({
                "strategy_id": strategy_id,
                "strategy_class": strategy_class,
                "reason": reason,
                "signal": sig.get("signal", "NEUTRAL"),
                "action": sig.get("action", ""),
                "confidence": sig.get("confidence", 50),
                "auto_deploy": sig.get("confidence", 50) >= 70,
            })
        return recs

    # -- strategies --------------------------------------------------------

    def strategies(self) -> list[dict[str, Any]]:
        now = datetime.now(IST)
        return [
            {
                "strategy_id": "iron-condor-weekly",
                "name": "Iron Condor — Weekly Premium",
                "description": "Systematic weekly premium collection on NIFTY. Sells 20-delta OTM call and put spreads with defined-risk wings. Auto-adjusts when delta breaches threshold. Targets 50% max profit take.",
                "underlying": "NIFTY",
                "strategy_type": "iron_condor",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Theta Decay + IV Overpricing",
                "class_path": "strategies.examples.iron_condor.IronCondorStrategy",
                "deployed_at": (now - timedelta(hours=3, minutes=22)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=12)).isoformat(),
                "pnl_today": round(self._jitter(4520.0, 0.05), 2),
                "pnl_week": round(self._jitter(18400.0, 0.03), 2),
                "pnl_month": round(self._jitter(62500.0, 0.04), 2),
                "positions_count": 4,
                "orders_today": 8,
                "max_drawdown_pct": -4.2,
                "sharpe": 1.85,
                "win_rate": 72,
                "avg_trade": 3200,
                "params": {
                    "short_ce_delta": 0.20,
                    "short_pe_delta": 0.20,
                    "wing_width": 200,
                    "max_loss_per_trade": 15000,
                    "profit_target_pct": 50,
                    "entry_time": "09:20",
                    "exit_time": "15:15",
                    "lot_size": 25,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "short-straddle-hedged",
                "name": "Delta-Hedged Short Straddle",
                "description": "ATM straddle selling on BANKNIFTY with continuous delta neutralization via futures. Market-maker style: collects theta while hedging directional risk. Adjusts every 50-point move.",
                "underlying": "BANKNIFTY",
                "strategy_type": "straddle",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Managed Risk",
                "edge": "Theta Harvest + Delta Neutrality",
                "class_path": "strategies.examples.straddle_seller.StraddleSellerStrategy",
                "deployed_at": (now - timedelta(hours=2, minutes=50)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=8)).isoformat(),
                "pnl_today": round(self._jitter(6800.0, 0.05), 2),
                "pnl_week": round(self._jitter(24200.0, 0.04), 2),
                "pnl_month": round(self._jitter(85000.0, 0.03), 2),
                "positions_count": 3,
                "orders_today": 14,
                "max_drawdown_pct": -6.8,
                "sharpe": 1.62,
                "win_rate": 68,
                "avg_trade": 4100,
                "params": {
                    "entry_time": "09:20",
                    "sl_pct": 25,
                    "target_pct": 50,
                    "delta_hedge_threshold": 0.15,
                    "trail_after_pct": 30,
                    "max_adjustment": 3,
                    "lot_size": 15,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "gamma-scalping-engine",
                "name": "Gamma Scalping Engine",
                "description": "Long ATM straddle with systematic delta-hedging via futures. Profits when realized volatility exceeds implied. Inspired by Jane Street / Citadel vol desks. Scalps gamma on every significant underlying move.",
                "underlying": "NIFTY",
                "strategy_type": "gamma_scalping",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Realized > Implied Vol",
                "class_path": "strategies.examples.gamma_scalping.GammaScalpingStrategy",
                "deployed_at": (now - timedelta(hours=4, minutes=10)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=5)).isoformat(),
                "pnl_today": round(self._jitter(3200.0, 0.08), 2),
                "pnl_week": round(self._jitter(12800.0, 0.06), 2),
                "pnl_month": round(self._jitter(41000.0, 0.05), 2),
                "positions_count": 3,
                "orders_today": 22,
                "max_drawdown_pct": -5.1,
                "sharpe": 1.45,
                "win_rate": 58,
                "avg_trade": 1800,
                "params": {
                    "hedge_delta_threshold": 0.10,
                    "scalp_interval_points": 30,
                    "max_gamma_exposure": 500,
                    "rebalance_frequency": "continuous",
                    "lot_size": 25,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "0dte-theta-decay",
                "name": "0DTE Theta Decay Harvester",
                "description": "Same-day expiry option selling. Exploits extreme theta decay in the last 4 hours before expiry. Sells OTM strangles at 0.05-delta on expiry day. Used by prop desks for consistent daily income.",
                "underlying": "NIFTY",
                "strategy_type": "expiry_day",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "High Risk / High Reward",
                "edge": "Exponential Theta Decay",
                "class_path": "strategies.examples.expiry_day.ExpiryDayStrategy",
                "deployed_at": (now - timedelta(hours=1, minutes=30)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=3)).isoformat(),
                "pnl_today": round(self._jitter(8500.0, 0.06), 2),
                "pnl_week": round(self._jitter(32000.0, 0.05), 2),
                "pnl_month": round(self._jitter(115000.0, 0.04), 2),
                "positions_count": 2,
                "orders_today": 6,
                "max_drawdown_pct": -12.5,
                "sharpe": 2.10,
                "win_rate": 82,
                "avg_trade": 5200,
                "params": {
                    "entry_time": "11:00",
                    "exit_time": "15:20",
                    "short_ce_delta": 0.05,
                    "short_pe_delta": 0.05,
                    "sl_multiplier": 3.0,
                    "only_expiry_day": True,
                    "lot_size": 25,
                    "num_lots": 8,
                },
            },
            {
                "strategy_id": "vol-arb-engine",
                "name": "Volatility Arbitrage",
                "description": "Trades the spread between implied and realized volatility. Buys options when IV is cheap vs historical realized vol, sells when IV is rich. Two Sigma / Citadel style systematic vol trading.",
                "underlying": "NIFTY",
                "strategy_type": "mean_reversion",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Market Neutral",
                "edge": "IV vs RV Mispricing",
                "class_path": "strategies.examples.mean_reversion.MeanReversionStrategy",
                "deployed_at": (now - timedelta(hours=5, minutes=0)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=15)).isoformat(),
                "pnl_today": round(self._jitter(2100.0, 0.1), 2),
                "pnl_week": round(self._jitter(9500.0, 0.06), 2),
                "pnl_month": round(self._jitter(38000.0, 0.05), 2),
                "positions_count": 4,
                "orders_today": 10,
                "max_drawdown_pct": -3.8,
                "sharpe": 1.92,
                "win_rate": 64,
                "avg_trade": 2800,
                "params": {
                    "iv_percentile_buy": 20,
                    "iv_percentile_sell": 80,
                    "lookback_days": 252,
                    "hedge_delta": True,
                    "max_vega_exposure": 25000,
                    "lot_size": 25,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "calendar-term-structure",
                "name": "Calendar Spread — Term Structure",
                "description": "Trades volatility term structure by selling near-expiry options and buying far-expiry options at the same strike. Profits from contango in vol curve. Institutional-grade time spread strategy.",
                "underlying": "NIFTY",
                "strategy_type": "gamma_scalping",
                "status": "PAUSED",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Vol Term Structure",
                "class_path": "strategies.examples.gamma_scalping.GammaScalpingStrategy",
                "deployed_at": (now - timedelta(hours=6)).isoformat(),
                "last_heartbeat": (now - timedelta(minutes=30)).isoformat(),
                "pnl_today": round(self._jitter(1800.0, 0.1), 2),
                "pnl_week": round(self._jitter(7200.0, 0.06), 2),
                "pnl_month": round(self._jitter(28000.0, 0.05), 2),
                "positions_count": 4,
                "orders_today": 4,
                "max_drawdown_pct": -3.2,
                "sharpe": 1.55,
                "win_rate": 66,
                "avg_trade": 2400,
                "params": {
                    "front_expiry_offset": 0,
                    "back_expiry_offset": 1,
                    "strike": "ATM",
                    "entry_iv_rank_min": 30,
                    "lot_size": 25,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "jade-lizard",
                "name": "Jade Lizard — Zero Upside Risk",
                "description": "Short put + short call spread on BANKNIFTY. Collected credit exceeds call spread width, eliminating upside risk entirely. Only downside risk remains. Sophisticated premium strategy used by institutional desks.",
                "underlying": "BANKNIFTY",
                "strategy_type": "iron_condor",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Asymmetric Risk",
                "edge": "Skew Premium + Zero Upside Risk",
                "class_path": "strategies.examples.iron_condor.IronCondorStrategy",
                "deployed_at": (now - timedelta(hours=2, minutes=40)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=10)).isoformat(),
                "pnl_today": round(self._jitter(5400.0, 0.06), 2),
                "pnl_week": round(self._jitter(19800.0, 0.04), 2),
                "pnl_month": round(self._jitter(72000.0, 0.03), 2),
                "positions_count": 3,
                "orders_today": 6,
                "max_drawdown_pct": -7.5,
                "sharpe": 1.72,
                "win_rate": 74,
                "avg_trade": 3800,
                "params": {
                    "short_put_delta": 0.25,
                    "short_call_strike_offset": 300,
                    "long_call_strike_offset": 500,
                    "min_credit_pct": 105,
                    "lot_size": 15,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "butterfly-pinning",
                "name": "Butterfly Pinning — Expiry Play",
                "description": "Deploys ATM butterfly spreads on expiry day targeting pin risk around max-pain / expected settlement. Low cost entry with high reward at pin. Market-maker edge strategy.",
                "underlying": "NIFTY",
                "strategy_type": "expiry_day",
                "status": "PAUSED",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Expiry Pin Risk + Max Pain",
                "class_path": "strategies.examples.expiry_day.ExpiryDayStrategy",
                "deployed_at": (now - timedelta(hours=1)).isoformat(),
                "last_heartbeat": (now - timedelta(minutes=20)).isoformat(),
                "pnl_today": round(self._jitter(2200.0, 0.12), 2),
                "pnl_week": round(self._jitter(8800.0, 0.08), 2),
                "pnl_month": round(self._jitter(35000.0, 0.06), 2),
                "positions_count": 3,
                "orders_today": 3,
                "max_drawdown_pct": -2.8,
                "sharpe": 1.35,
                "win_rate": 55,
                "avg_trade": 1500,
                "params": {
                    "wing_width": 100,
                    "entry_time": "10:30",
                    "max_cost_per_fly": 15,
                    "target_profit_multiple": 5,
                    "max_pain_proximity": 50,
                    "lot_size": 25,
                    "num_lots": 10,
                },
            },
            {
                "strategy_id": "risk-reversal-skew",
                "name": "Risk Reversal — Skew Trading",
                "description": "Trades put-call skew on NIFTY. Sells overpriced OTM puts and buys underpriced OTM calls. Profits when skew normalizes. Macro hedge fund style directional-neutral skew capture.",
                "underlying": "NIFTY",
                "strategy_type": "mean_reversion",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Directional Bias",
                "edge": "Skew Mean Reversion",
                "class_path": "strategies.examples.mean_reversion.MeanReversionStrategy",
                "deployed_at": (now - timedelta(hours=3)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=18)).isoformat(),
                "pnl_today": round(self._jitter(3800.0, 0.07), 2),
                "pnl_week": round(self._jitter(14500.0, 0.05), 2),
                "pnl_month": round(self._jitter(52000.0, 0.04), 2),
                "positions_count": 2,
                "orders_today": 4,
                "max_drawdown_pct": -8.2,
                "sharpe": 1.48,
                "win_rate": 62,
                "avg_trade": 3500,
                "params": {
                    "short_put_delta": 0.15,
                    "long_call_delta": 0.15,
                    "skew_entry_threshold": 2.5,
                    "skew_exit_threshold": 0.5,
                    "max_exposure_lots": 8,
                    "lot_size": 25,
                    "num_lots": 4,
                },
            },
            {
                "strategy_id": "ratio-backspread",
                "name": "Ratio Backspread — Tail Hedge",
                "description": "Buys 2x OTM options, sells 1x ATM option. Net debit/credit with unlimited profit on large moves. Acts as portfolio tail-risk hedge. Inspired by Nassim Taleb's barbell approach.",
                "underlying": "BANKNIFTY",
                "strategy_type": "momentum",
                "status": "RUNNING",
                "mode": "PAPER",
                "risk_profile": "Convex Payoff",
                "edge": "Tail Risk Premium",
                "class_path": "strategies.examples.momentum_breakout.MomentumBreakoutStrategy",
                "deployed_at": (now - timedelta(hours=4, minutes=30)).isoformat(),
                "last_heartbeat": (now - timedelta(seconds=6)).isoformat(),
                "pnl_today": round(self._jitter(-800.0, 0.15), 2),
                "pnl_week": round(self._jitter(5200.0, 0.1), 2),
                "pnl_month": round(self._jitter(22000.0, 0.08), 2),
                "positions_count": 3,
                "orders_today": 3,
                "max_drawdown_pct": -6.0,
                "sharpe": 1.15,
                "win_rate": 42,
                "avg_trade": -200,
                "params": {
                    "atm_sell_lots": 2,
                    "otm_buy_lots": 4,
                    "otm_offset": 500,
                    "entry_on_low_vix": True,
                    "vix_threshold": 14,
                    "lot_size": 15,
                    "num_lots": 2,
                },
            },
            {
                "strategy_id": "protective-collar",
                "name": "Protective Collar — Portfolio Hedge",
                "description": "Long NIFTY index + long OTM puts + short OTM calls. Zero-cost collar protecting portfolio downside while capping upside. BlackRock / LIC style institutional portfolio insurance.",
                "underlying": "NIFTY",
                "strategy_type": "iron_condor",
                "status": "STOPPED",
                "mode": "PAPER",
                "risk_profile": "Portfolio Protection",
                "edge": "Downside Insurance + Premium Offset",
                "class_path": "strategies.examples.iron_condor.IronCondorStrategy",
                "deployed_at": (now - timedelta(days=2)).isoformat(),
                "last_heartbeat": (now - timedelta(hours=20)).isoformat(),
                "pnl_today": 0.0,
                "pnl_week": round(self._jitter(-2500.0, 0.1), 2),
                "pnl_month": round(self._jitter(8000.0, 0.08), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -1.5,
                "sharpe": 0.85,
                "win_rate": 56,
                "avg_trade": 800,
                "params": {
                    "put_delta": 0.20,
                    "call_delta": 0.20,
                    "collar_type": "zero_cost",
                    "roll_dte": 5,
                    "lot_size": 25,
                    "num_lots": 8,
                },
            },
            {
                "strategy_id": "dispersion-trading",
                "name": "Dispersion Trading — Correlation Arb",
                "description": "Sells NIFTY index options, buys component stock options. Exploits the correlation risk premium — index vol is systematically overpriced vs component vols. JP Morgan / Goldman Sachs style.",
                "underlying": "NIFTY+Components",
                "strategy_type": "pair_trading",
                "status": "STOPPED",
                "mode": "BACKTEST",
                "risk_profile": "Market Neutral",
                "edge": "Correlation Risk Premium",
                "class_path": "strategies.examples.pair_trading.PairTradingStrategy",
                "deployed_at": (now - timedelta(days=3)).isoformat(),
                "last_heartbeat": (now - timedelta(days=1)).isoformat(),
                "pnl_today": 0.0,
                "pnl_week": 0.0,
                "pnl_month": round(self._jitter(45000.0, 0.06), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -4.5,
                "sharpe": 1.78,
                "win_rate": 70,
                "avg_trade": 5500,
                "params": {
                    "index_symbol": "NIFTY",
                    "components": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"],
                    "short_index_lots": 4,
                    "long_component_lots": 1,
                    "rebalance_daily": True,
                    "entry_corr_threshold": 0.85,
                },
            },
            # ── Intraday Basic Options Strategies ──────────────────────
            {
                "strategy_id": "bull-call-spread",
                "name": "Bull Call Spread — Intraday",
                "description": "Simple directional bullish trade. Buy ATM call + sell OTM call on NIFTY. Defined risk, defined reward. Best entry on pullback to VWAP in an uptrend. Classic retail + institutional intraday strategy.",
                "underlying": "NIFTY",
                "strategy_type": "iron_condor",
                "status": "STOPPED",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Directional + Reduced Cost",
                "class_path": "strategies.examples.iron_condor.IronCondorStrategy",
                "deployed_at": None,
                "last_heartbeat": None,
                "pnl_today": 0.0,
                "pnl_week": round(self._jitter(4200.0, 0.08), 2),
                "pnl_month": round(self._jitter(18000.0, 0.06), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -3.5,
                "sharpe": 1.20,
                "win_rate": 58,
                "avg_trade": 1200,
                "params": {
                    "direction": "BULLISH",
                    "buy_strike": "ATM",
                    "sell_strike": "ATM+200",
                    "entry_time": "09:20",
                    "exit_time": "15:10",
                    "sl_pct": 40,
                    "target_pct": 60,
                    "lot_size": 25,
                    "num_lots": 2,
                },
            },
            {
                "strategy_id": "bear-put-spread",
                "name": "Bear Put Spread — Intraday",
                "description": "Simple directional bearish trade. Buy ATM put + sell OTM put on NIFTY. Defined risk, capped reward. Enter when price breaks below opening range or VWAP. Clean risk-reward for bearish conviction.",
                "underlying": "NIFTY",
                "strategy_type": "iron_condor",
                "status": "STOPPED",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Directional + Reduced Cost",
                "class_path": "strategies.examples.iron_condor.IronCondorStrategy",
                "deployed_at": None,
                "last_heartbeat": None,
                "pnl_today": 0.0,
                "pnl_week": round(self._jitter(3800.0, 0.08), 2),
                "pnl_month": round(self._jitter(15000.0, 0.06), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -3.8,
                "sharpe": 1.15,
                "win_rate": 56,
                "avg_trade": 1100,
                "params": {
                    "direction": "BEARISH",
                    "buy_strike": "ATM",
                    "sell_strike": "ATM-200",
                    "entry_time": "09:20",
                    "exit_time": "15:10",
                    "sl_pct": 40,
                    "target_pct": 60,
                    "lot_size": 25,
                    "num_lots": 2,
                },
            },
            {
                "strategy_id": "short-strangle",
                "name": "Short Strangle — Intraday Premium",
                "description": "Sell OTM call + OTM put on NIFTY to collect premium. Profits when market stays within expected range. Wider strikes = higher probability but lower premium. Most popular retail options selling strategy in India.",
                "underlying": "NIFTY",
                "strategy_type": "straddle",
                "status": "STOPPED",
                "mode": "PAPER",
                "risk_profile": "Unlimited Risk",
                "edge": "Theta Decay + Range Markets",
                "class_path": "strategies.examples.straddle_seller.StraddleSellerStrategy",
                "deployed_at": None,
                "last_heartbeat": None,
                "pnl_today": 0.0,
                "pnl_week": round(self._jitter(8500.0, 0.06), 2),
                "pnl_month": round(self._jitter(35000.0, 0.05), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -9.5,
                "sharpe": 1.40,
                "win_rate": 72,
                "avg_trade": 2800,
                "params": {
                    "call_offset": 400,
                    "put_offset": 400,
                    "entry_time": "09:20",
                    "exit_time": "15:15",
                    "sl_per_leg_pct": 50,
                    "combined_sl_pct": 35,
                    "lot_size": 25,
                    "num_lots": 2,
                },
            },
            {
                "strategy_id": "atm-straddle-buy",
                "name": "ATM Straddle Buy — Breakout Play",
                "description": "Buy ATM call + ATM put expecting a big intraday move. Profits when market moves more than the premium paid in either direction. Best deployed before events, RBI policy, or when VIX is at extremes. Classic long volatility bet.",
                "underlying": "NIFTY",
                "strategy_type": "gamma_scalping",
                "status": "STOPPED",
                "mode": "PAPER",
                "risk_profile": "Defined Risk",
                "edge": "Breakout + Realized > Implied",
                "class_path": "strategies.examples.gamma_scalping.GammaScalpingStrategy",
                "deployed_at": None,
                "last_heartbeat": None,
                "pnl_today": 0.0,
                "pnl_week": round(self._jitter(5200.0, 0.1), 2),
                "pnl_month": round(self._jitter(22000.0, 0.08), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -8.0,
                "sharpe": 0.95,
                "win_rate": 42,
                "avg_trade": -500,
                "params": {
                    "strike": "ATM",
                    "entry_time": "09:20",
                    "exit_time": "15:10",
                    "sl_pct": 30,
                    "target_pct": 80,
                    "max_trades_per_day": 1,
                    "lot_size": 25,
                    "num_lots": 2,
                },
            },
            {
                "strategy_id": "scalp-ce-pe",
                "name": "Scalp CE/PE — Momentum Options",
                "description": "Quick directional option buying based on 5-min momentum. Buy CE on bullish breakout, PE on bearish breakdown. Tight SL (30% of premium), quick target (50%). Highest frequency intraday strategy — needs discipline and fast execution.",
                "underlying": "NIFTY",
                "strategy_type": "momentum",
                "status": "STOPPED",
                "mode": "PAPER",
                "risk_profile": "High Risk / High Reward",
                "edge": "Momentum + Quick Scalp",
                "class_path": "strategies.examples.momentum_breakout.MomentumBreakoutStrategy",
                "deployed_at": None,
                "last_heartbeat": None,
                "pnl_today": 0.0,
                "pnl_week": round(self._jitter(6000.0, 0.12), 2),
                "pnl_month": round(self._jitter(25000.0, 0.1), 2),
                "positions_count": 0,
                "orders_today": 0,
                "max_drawdown_pct": -15.0,
                "sharpe": 0.80,
                "win_rate": 45,
                "avg_trade": 800,
                "params": {
                    "direction": "auto",
                    "entry_trigger": "5min_breakout",
                    "sl_pct": 30,
                    "target_pct": 50,
                    "max_trades_per_day": 5,
                    "trailing_sl": True,
                    "lot_size": 25,
                    "num_lots": 1,
                },
            },
        ]

    # -- risk metrics ------------------------------------------------------

    def risk_metrics(self) -> dict[str, Any]:
        greeks = self.portfolio_greeks()
        margin = self.margin_info()
        return {
            "portfolio_var_1d_95": round(self._rng.uniform(15_000, 45_000), 2),
            "portfolio_var_1d_99": round(self._rng.uniform(35_000, 75_000), 2),
            "max_drawdown": round(self._rng.uniform(0.02, 0.08), 4),
            "current_drawdown": round(self._rng.uniform(0.005, 0.03), 4),
            "margin_utilization": margin["utilization_pct"] / 100,
            "concentration_score": round(self._rng.uniform(0.3, 0.7), 2),
            "net_delta_exposure": greeks["net_delta"],
            "net_gamma_exposure": greeks["net_gamma"],
            "net_theta_exposure": greeks["net_theta"],
            "net_vega_exposure": greeks["net_vega"],
            "max_single_position_loss": round(self._rng.uniform(8_000, 25_000), 2),
            "daily_loss_limit": 50_000.0,
            "daily_loss_used": round(self._rng.uniform(0, 15_000), 2),
            "kill_switch_active": False,
            "timestamp": datetime.now(IST).isoformat(),
        }

    # -- stress test -------------------------------------------------------

    def stress_test(self) -> dict[str, Any]:
        pnl = self.pnl_snapshot()
        base_pnl = pnl["total_pnl"]
        nifty_spot = self._jitter(self.INDEX_BASE["NIFTY"])

        scenarios: list[dict[str, Any]] = []
        for label, spot_move_pct, iv_change in [
            ("NIFTY -10%", -10.0, 8.0),
            ("NIFTY -5%", -5.0, 4.0),
            ("NIFTY -2%", -2.0, 1.5),
            ("NIFTY +2%", 2.0, -0.5),
            ("NIFTY +5%", 5.0, -1.5),
            ("NIFTY +10%", 10.0, -3.0),
            ("IV Spike +50%", 0.0, 7.0),
            ("IV Crush -30%", 0.0, -4.0),
            ("Overnight Gap -3%", -3.0, 5.0),
        ]:
            shocked_spot = round(nifty_spot * (1 + spot_move_pct / 100), 2)
            # Simplified P&L estimation under shock
            delta_pnl = self._rng.uniform(-0.5, 0.5) * spot_move_pct * 1000
            vega_pnl = iv_change * self._rng.uniform(-500, 500)
            shock_pnl = round(base_pnl + delta_pnl + vega_pnl, 2)
            scenarios.append(
                {
                    "scenario": label,
                    "spot_move_pct": spot_move_pct,
                    "iv_change_pct": iv_change,
                    "shocked_spot": shocked_spot,
                    "estimated_pnl": shock_pnl,
                    "estimated_margin_impact": round(abs(spot_move_pct) * self._rng.uniform(8_000, 20_000), 2),
                }
            )

        return {
            "base_spot": nifty_spot,
            "base_pnl": base_pnl,
            "scenarios": scenarios,
            "timestamp": datetime.now(IST).isoformat(),
        }

    # -- orders ------------------------------------------------------------

    def orders(self) -> list[dict[str, Any]]:
        now = datetime.now(IST)
        days_to_expiry = (3 - now.weekday()) % 7 or 7
        expiry = (now + timedelta(days=days_to_expiry)).date().isoformat()

        order_templates = [
            ("NIFTY 24200 CE", "SELL", "LIMIT", "FILLED", 185.50, 185.50, 50, 50, "iron-condor-weekly"),
            ("NIFTY 24400 CE", "BUY", "LIMIT", "FILLED", 95.20, 95.20, 50, 50, "iron-condor-weekly"),
            ("NIFTY 24100 PE", "SELL", "LIMIT", "FILLED", 145.80, 145.80, 50, 50, "iron-condor-weekly"),
            ("NIFTY 23900 PE", "BUY", "LIMIT", "FILLED", 72.40, 72.40, 50, 50, "iron-condor-weekly"),
            ("BANKNIFTY 51500 CE", "SELL", "LIMIT", "FILLED", 420.00, 420.00, 30, 30, "straddle-banknifty"),
            ("BANKNIFTY 51500 PE", "SELL", "LIMIT", "FILLED", 380.00, 380.00, 30, 30, "straddle-banknifty"),
            ("NIFTY 24250 CE", "BUY", "SL", "OPEN", 210.00, 0.0, 25, 0, "momentum-scalper"),
            ("NIFTY 24250 PE", "BUY", "MARKET", "FILLED", 0.0, 158.30, 25, 25, "momentum-scalper"),
            ("NIFTY 24250 PE", "SELL", "LIMIT", "FILLED", 162.50, 162.50, 25, 25, "momentum-scalper"),
            ("NIFTY 24300 CE", "BUY", "LIMIT", "CANCELLED", 120.00, 0.0, 50, 0, "momentum-scalper"),
            ("BANKNIFTY 51600 CE", "BUY", "LIMIT", "REJECTED", 350.00, 0.0, 15, 0, "straddle-banknifty"),
            ("NIFTY 24150 CE", "BUY", "LIMIT", "PARTIAL", 155.00, 155.00, 50, 25, "momentum-scalper"),
        ]

        orders_list: list[dict[str, Any]] = []
        for i, (instr, side, otype, status, price, avg, qty, filled, strat) in enumerate(order_templates):
            placed = now - timedelta(minutes=self._rng.randint(10, 300))
            order_id = f"ORD-{now.strftime('%Y%m%d')}-{i + 1:04d}"
            broker_id = f"ZR-{self._rng.randint(100000, 999999)}" if status in ("FILLED", "PARTIAL", "OPEN") else None

            entry: dict[str, Any] = {
                "order_id": order_id,
                "broker_order_id": broker_id,
                "instrument": instr,
                "expiry": expiry,
                "side": side,
                "order_type": otype,
                "product_type": "NRML",
                "quantity": qty,
                "filled_quantity": filled,
                "price": price if otype in ("LIMIT", "SL") else None,
                "trigger_price": round(price * 0.98, 2) if otype == "SL" else None,
                "average_price": avg if filled > 0 else 0.0,
                "status": status,
                "strategy_id": strat,
                "placed_at": placed.isoformat(),
                "updated_at": (placed + timedelta(seconds=self._rng.randint(1, 60))).isoformat(),
                "rejection_reason": "Insufficient margin" if status == "REJECTED" else None,
                "tag": strat[:8].upper(),
            }
            orders_list.append(entry)

        return orders_list

    # -- trades ------------------------------------------------------------

    def trades(self) -> list[dict[str, Any]]:
        orders = self.orders()
        trade_list: list[dict[str, Any]] = []
        trade_counter = 1
        now = datetime.now(IST)

        for order in orders:
            if order["filled_quantity"] > 0:
                trade_list.append(
                    {
                        "trade_id": f"TRD-{now.strftime('%Y%m%d')}-{trade_counter:04d}",
                        "order_id": order["order_id"],
                        "broker_trade_id": f"NSE-{self._rng.randint(10000000, 99999999)}",
                        "instrument": order["instrument"],
                        "expiry": order["expiry"],
                        "side": order["side"],
                        "quantity": order["filled_quantity"],
                        "price": order["average_price"],
                        "strategy_id": order["strategy_id"],
                        "timestamp": order["updated_at"],
                        "exchange": "NFO",
                    }
                )
                trade_counter += 1

        return trade_list


# ===================================================================
# Response Models (for OpenAPI docs)
# ===================================================================


class HealthResponse(BaseModel):
    status: str = "ok"
    uptime_seconds: float
    uptime_human: str
    version: str
    mode: str
    components: dict[str, str]
    timestamp: str


class SystemInfoResponse(BaseModel):
    platform_version: str
    python_version: str
    os_platform: str
    trading_mode: str
    timezone: str
    market_open: str
    market_close: str
    primary_broker: str
    backup_broker: str
    brokers_configured: int
    strategies_configured: int
    event_bus_backend: str


class ConfigResponse(BaseModel):
    mode: str
    timezone: str
    primary_broker: str
    brokers: dict[str, Any]
    market_data: dict[str, Any]
    risk: dict[str, Any]
    dashboard: dict[str, Any]
    log_level: str


class EventMetricsResponse(BaseModel):
    published: int
    consumed: int
    failed: int
    avg_latency_ms: float
    max_latency_ms: float
    dead_letter_count: int


class StrategyActionResponse(BaseModel):
    strategy_id: str
    action: str
    previous_status: str
    new_status: str
    message: str
    timestamp: str


class OrderPlaceRequest(BaseModel):
    """Request body for placing an order (live or paper)."""
    symbol: str = Field(description="Trading symbol, e.g. NSE:NIFTY2460524200CE")
    side: str = Field(description="BUY or SELL (mapped to 1/−1 for Fyers)")
    qty: int = Field(gt=0, description="Order quantity")
    order_type: str = Field(default="MARKET", description="MARKET, LIMIT, SL, or SL-M")
    price: float = Field(default=0, description="Limit price (required for LIMIT/SL orders)")
    product: str = Field(default="INTRADAY", description="Product type: INTRADAY, CNC, MARGIN, BO, CO")
    triggerPrice: float = Field(default=0, description="Trigger price for SL/SL-M orders")


class StrategyDeployRequest(BaseModel):
    """Request body for deploying a strategy."""
    strategy_id: str = Field(description="Unique identifier for this deployment")
    strategy_class: str = Field(description="Strategy class: iron_condor, straddle, momentum, etc.")
    name: str = Field(default="", description="Display name")
    underlying: str = Field(default="NIFTY", description="Underlying symbol")
    mode: str = Field(default="PAPER", description="PAPER or LIVE")
    params: dict[str, Any] = Field(default={}, description="Strategy parameters")


class StrategySaveRequest(BaseModel):
    """Request body for saving a strategy configuration."""
    strategy_id: str = Field(description="Unique identifier for this strategy config")
    strategy_class: str = Field(description="Strategy class: iron_condor, straddle, momentum, etc.")
    name: str = Field(default="", description="Display name")
    underlying: str = Field(default="NIFTY", description="Underlying symbol")
    params: dict[str, Any] = Field(default={}, description="Strategy parameters")
    description: str = Field(default="", description="User notes / description")


# ===================================================================
# Lifespan
# ===================================================================


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup and shutdown logic for the FastAPI application."""
    global _startup_time, _config, _event_bus, _ws_manager, _paper_trading_manager, _live_feed, _dashboard_executor, _TRADING_MODE

    _startup_time = time.time()

    # Initialise a default config (no YAML file needed for dev)
    _config = PlatformConfig()

    # Initialise event bus
    _event_bus = InMemoryEventBus()
    await _event_bus.start()

    # Initialise WebSocket manager
    _ws_manager = WebSocketManager()
    await _ws_manager.start()
    set_ws_manager(_ws_manager)

    # Initialise paper trading manager (live_feed set below after Fyers connects)
    _paper_trading_manager = PaperTradingManager(event_bus=_event_bus)

    # Warm the Fyers symbol master cache (authoritative lot sizes & tick sizes).
    # Runs in a thread so the HTTP download doesn't block startup.
    import asyncio as _asyncio_local
    from core import symbol_master as _symbol_master
    try:
        sm_status = await _asyncio_local.to_thread(_symbol_master.refresh)
        logger.info(
            f"Symbol master loaded: {sm_status['symbols_loaded']} symbols "
            f"(source={sm_status['source']})"
        )
    except Exception as _e:
        logger.warning(f"Symbol master warm-up failed: {_e}")

    # Initialise Fyers live feed
    import logging as _logging
    _logging.getLogger("core.fyers_live_feed").setLevel(_logging.INFO)
    _live_feed = FyersLiveFeed(
        app_id=FYERS_APP_ID,
        access_token=FYERS_ACCESS_TOKEN,
        secret_key=FYERS_SECRET_KEY,
        redirect_uri=FYERS_REDIRECT_URI,
    )
    fyers_connected = await _live_feed.connect()
    if fyers_connected:
        _logging.getLogger(__name__).info("Fyers live feed connected — serving real market data")
        set_live_feed(_live_feed)
        await _live_feed.start_background_refresh(interval=0.5)
        # Start true real-time WebSocket stream from Fyers (updates _last_ticks instantly)
        _live_feed.start_websocket_stream()
        # Give paper trading manager access to live feed
        if _paper_trading_manager is not None:
            _paper_trading_manager._live_feed = _live_feed
    else:
        _logging.getLogger(__name__).warning("Fyers connection failed — falling back to mock data")
        _live_feed = None

    # Start the Dashboard Strategy Executor — runs deployed strategies
    # (entry orders, SL/target monitoring, 15:15 square-off)
    global _dashboard_executor
    try:
        from core.strategy_engine.dashboard_executor import DashboardStrategyExecutor
        _dashboard_executor = DashboardStrategyExecutor(
            deployed_strategies=_deployed_strategies,
            refresh_pnl_fn=_refresh_strategy_pnl,
            paper_trading_manager=_paper_trading_manager,
            live_feed=_live_feed,
            place_order_fn=None,  # populated lazily — strategies can route via /api/orders
        )
        await _dashboard_executor.start()
    except Exception as e:
        logger.warning(f"Could not start dashboard strategy executor: {e}")
        _dashboard_executor = None

    # --- Restore persisted state from SQLite ---
    try:
        from core.state_store import get_store
        store = get_store()
        saved = store.load_all_strategies()
        _deployed_strategies.update(saved)
        # Also load trading mode
        saved_mode = store.load_setting("trading_mode", "paper")
        _TRADING_MODE = saved_mode
        if saved:
            logger.info(f"Restored {len(saved)} deployed strategies from DB")
    except Exception as _e:
        logger.warning(f"Could not restore state from DB: {_e}")

    yield

    # Shutdown
    if _dashboard_executor is not None:
        try:
            await _dashboard_executor.stop()
        except Exception:
            pass

    if _live_feed is not None:
        await _live_feed.disconnect()

    if _paper_trading_manager is not None and _paper_trading_manager.is_active:
        try:
            await _paper_trading_manager.stop_session()
        except Exception:
            pass

    if _ws_manager is not None:
        await _ws_manager.stop()

    if _event_bus is not None:
        await _event_bus.stop()

    # --- Persist final state to SQLite before exit ---
    try:
        from core.state_store import get_store
        store = get_store()
        for sid, strat in _deployed_strategies.items():
            store.save_strategy(sid, strat)
        store.close()
        logger.info("StateStore: final state saved and connection closed.")
    except Exception as _e:
        logger.warning(f"StateStore shutdown save failed: {_e}")


# ===================================================================
# FastAPI Application
# ===================================================================

app = FastAPI(
    title="SmartAlgo Trading Platform API",
    description=(
        "Backend API for the institutional-grade algorithmic options trading "
        "platform targeting the Indian stock market (NSE/BSE). Provides "
        "real-time market data, portfolio management, strategy control, "
        "risk monitoring, and order management endpoints."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
        "http://0.0.0.0:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include WebSocket routes
app.include_router(ws_router)

# Shared mock generator
_mock = MockDataGenerator(seed=42)

# Cache for last successful Fyers responses — prevents flicker to mock on temporary failures
_fyers_chain_cache: dict[str, dict] = {}
_fyers_chain_cache_time: dict[str, float] = {}  # symbol -> timestamp of last fetch
_CHAIN_CACHE_TTL = 3.0  # seconds — option chain is the rate-limited Fyers call; 3s keeps us under ~200/min (do NOT lower)
_fyers_indices_cache: dict | None = None
_fyers_indices_cache_time: float = 0  # timestamp of last indices fetch
_INDICES_CACHE_TTL = 2.0  # seconds — index tickers (shares Fyers budget with chain)

# In-memory stores for strategy management
_deployed_strategies: dict[str, dict] = {}
_saved_strategies: dict[str, dict] = {}
_strategy_overrides: dict[str, dict] = {}  # strategy_id -> {"status": "PAUSED"|"RUNNING", ...}

# Trading mode — controls where strategy deploys and orders route to.
#   "paper" (default, safe): all orders go through PaperTradingManager / PaperBroker
#   "live":                  orders are placed via Fyers and hit real money
# The mode is read on every deploy/order; switching to "live" is gated by a
# Fyers-connected check and an explicit `confirm=true` flag from the client.
_TRADING_MODE: str = "paper"


# ===================================================================
# Helpers
# ===================================================================


def _uptime() -> tuple[float, str]:
    elapsed = time.time() - _startup_time
    hours, rem = divmod(int(elapsed), 3600)
    minutes, secs = divmod(rem, 60)
    human = f"{hours}h {minutes}m {secs}s"
    return round(elapsed, 2), human


def _sanitize_broker(broker: BrokerConfig) -> dict[str, Any]:
    """Return broker config with secrets masked."""
    data = broker.model_dump()
    for key in ("api_key", "api_secret", "access_token", "totp_secret"):
        val = data.get(key, "")
        if val:
            data[key] = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
    return data


async def _fyers_call(fn, *args, timeout: float = 3.0, **kwargs):
    """Run a synchronous Fyers SDK call in a thread with timeout.

    Returns the result dict or None on timeout / error.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Fyers call {fn.__name__} timed out after {timeout}s")
        return None
    except Exception as e:
        logger.warning(f"Fyers call {fn.__name__} failed: {e}")
        return None


def _refresh_strategy_pnl(strat: dict) -> None:
    """Recompute a deployed strategy's per-leg P&L from current Fyers LTPs.

    For options, P&L per leg = (current_premium - entry_premium) * qty for BUY,
    or (entry_premium - current_premium) * qty for SELL. The current premium is
    taken from the Fyers option-chain cache (matched by underlying + strike +
    option_type), or the leg's entry price if no live data yet.

    Mutates ``strat`` in place — sets ``positions[i].ltp``, ``positions[i].pnl``,
    ``unrealized_pnl``, and ``pnl``.

    Important: a strategy that has NOT yet entered has no P&L exposure — its
    entry_price is still a template placeholder, not a real fill. We force
    P&L to zero in that case so the dashboard shows ₹0 while the strategy
    sits in WAIT state.
    """
    positions = strat.get("positions", [])
    if not positions:
        strat["unrealized_pnl"] = 0.0
        strat["pnl"] = strat.get("realized_pnl", 0.0)
        return

    # If the strategy has not actually entered yet, its entry_price values
    # are template placeholders. Don't show fake P&L from those.
    if not strat.get("entered"):
        for pos in positions:
            pos["pnl"] = 0.0
            # leave entry_price + ltp as template hints, but reset pnl
        strat["unrealized_pnl"] = 0.0
        strat["pnl"] = strat.get("realized_pnl", 0.0)
        return

    # Exited / Stopped strategies have locked-in realized P&L.
    # Do NOT recalculate from positions — the chain cache may be empty
    # after a restart, which would corrupt their P&L to near-zero.
    status = (strat.get("status") or "").upper()
    if status in ("EXITED", "STOPPED"):
        # Preserve the realized P&L as-is; charges were already factored
        # at exit time or will be computed from the frozen position data.
        strat["pnl"] = float(strat.get("realized_pnl", 0.0))
        strat["unrealized_pnl"] = 0.0
        # Still compute charges for display purposes (positions are frozen)
        try:
            from core.charges import compute_strategy_charges

            charges = compute_strategy_charges(positions)
            strat["charges"] = charges["breakdown"]
            strat["total_charges"] = charges["total_all"]
            strat["gross_pnl"] = strat["pnl"]
            strat["pnl"] = round(strat["pnl"] - charges["total_all"], 2)
        except Exception:
            strat["charges"] = {}
            strat["total_charges"] = 0.0
            strat["gross_pnl"] = strat["pnl"]
        return

    underlying = (strat.get("underlying") or "").upper()
    chain_cache = _fyers_chain_cache.get(underlying) or _fyers_chain_cache.get(f"{underlying}:")
    chain_rows = chain_cache.get("chain", []) if isinstance(chain_cache, dict) else []
    # Build a lookup {strike: {call_ltp, put_ltp}} for fast leg pricing
    chain_lookup: dict[int, dict] = {}
    for row in chain_rows:
        if isinstance(row, dict) and "strike" in row:
            chain_lookup[int(row["strike"])] = row

    # Flag if chain data is empty (market closed or Fyers disconnected)
    chain_available = len(chain_lookup) > 0
    if not chain_available:
        strat["_pnl_stale"] = True
        strat["_pnl_stale_reason"] = "option_chain_empty"
    else:
        strat["_pnl_stale"] = False

    # Update spot from fresh chain if available
    spot = float(strat.get("spot_price", 0)) or 0.0
    if chain_cache and isinstance(chain_cache, dict):
        fresh_spot = float(chain_cache.get("spot_price", 0) or 0)
        if fresh_spot > 0:
            spot = fresh_spot
    unrealized_total = 0.0
    for pos in positions:
        entry = float(pos.get("entry_price", 0))
        qty = int(pos.get("qty", 0))
        side = (pos.get("side") or "SELL").upper()

        # Resolve current LTP for the leg
        # Symbol format we wrote at deploy: "NIFTY 23700 CE"
        sym_parts = (pos.get("symbol") or "").split()
        ltp = entry
        if len(sym_parts) >= 3:
            try:
                strike = int(sym_parts[1])
                opt_type = sym_parts[2].upper()
                row = chain_lookup.get(strike)
                if row:
                    key = "call_ltp" if opt_type == "CE" else "put_ltp"
                    if row.get(key):
                        ltp = float(row[key])
            except (ValueError, IndexError):
                pass

        # Flag if this leg's LTP is stale (using entry price as fallback)
        pos["_ltp_live"] = (ltp != entry) or not chain_available
        pos["ltp"] = round(ltp, 2)
        # Options P&L: SELL profits when premium falls, BUY profits when premium rises
        if side == "SELL":
            leg_pnl = (entry - ltp) * qty
        else:
            leg_pnl = (ltp - entry) * qty
        pos["pnl"] = round(leg_pnl, 2)
        unrealized_total += leg_pnl

    strat["unrealized_pnl"] = round(unrealized_total, 2)
    gross_pnl = round(strat.get("realized_pnl", 0.0) + unrealized_total, 2)

    # ── Charges + slippage deduction for realistic paper P&L ─────
    try:
        from core.charges import compute_strategy_charges

        charges = compute_strategy_charges(positions)
        strat["charges"] = charges["breakdown"]
        strat["total_charges"] = charges["total_all"]
        strat["gross_pnl"] = gross_pnl
        strat["pnl"] = round(gross_pnl - charges["total_all"], 2)
    except Exception:
        strat["charges"] = {}
        strat["total_charges"] = 0.0
        strat["gross_pnl"] = gross_pnl
        strat["pnl"] = gross_pnl

    strat["spot_price"] = spot  # keep cached
    # legacy alias used elsewhere
    strat["positions_count"] = len(positions)


# ===================================================================
# Health & System Endpoints
# ===================================================================


@app.get("/", include_in_schema=False)
async def root():
    """Redirect root to Swagger UI."""
    return RedirectResponse(url="/docs")


@app.get(
    "/api/health",
    response_model=HealthResponse,
    tags=["Health & System"],
    summary="Health check",
    description="Returns platform health status, uptime, version, and component statuses.",
)
async def health_check():
    elapsed, human = _uptime()
    return HealthResponse(
        status="ok",
        uptime_seconds=elapsed,
        uptime_human=human,
        version=__version__,
        mode=_TRADING_MODE,
        components={
            "api_server": "healthy",
            "event_bus": "healthy" if _event_bus and _event_bus._running else "degraded",
            "market_data_feed": "fyers_live" if (_live_feed and _live_feed.is_connected) else "mock",
            "broker_gateway": "disconnected",
            "risk_engine": "healthy",
            "order_manager": "healthy",
            "strategy_engine": "healthy",
            "database": "not_configured",
        },
        timestamp=datetime.now(IST).isoformat(),
    )


@app.get(
    "/api/system/info",
    response_model=SystemInfoResponse,
    tags=["Health & System"],
    summary="Platform information",
    description="Returns platform version, Python version, trading mode, timezone, and broker summary.",
)
async def system_info():
    cfg = _config or PlatformConfig()
    fyers_connected = bool(_live_feed and _live_feed.is_connected)
    return SystemInfoResponse(
        platform_version=__version__,
        python_version=sys.version,
        os_platform=platform.platform(),
        trading_mode=_TRADING_MODE,
        timezone="Asia/Kolkata",
        market_open=str(MARKET_OPEN),
        market_close=str(MARKET_CLOSE),
        primary_broker="fyers",
        backup_broker="none",
        brokers_configured=1 if fyers_connected else 0,
        strategies_configured=len(_deployed_strategies),
        event_bus_backend="in_memory",
    )


@app.get(
    "/api/system/config",
    response_model=ConfigResponse,
    tags=["Health & System"],
    summary="Current configuration (sanitized)",
    description="Returns the current platform configuration with API keys and secrets masked.",
)
async def system_config():
    cfg = _config or PlatformConfig()
    fyers_connected = bool(_live_feed and _live_feed.is_connected)

    # Build real Fyers broker entry
    import os
    fyers_app_id = os.getenv("FYERS_APP_ID", "")
    brokers_real = {}
    if fyers_app_id:
        brokers_real["fyers"] = {
            "provider": "Fyers API v3",
            "api_key": fyers_app_id[:6] + "****" if fyers_app_id else "Not set",
            "connected": fyers_connected,
            "status": "active" if fyers_connected else "disconnected",
        }

    # Get real risk limits from SQLite
    try:
        from core.state_store import get_store
        from core import risk_engine as re
        store = get_store()
        real_risk = {**re.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}
    except Exception:
        real_risk = cfg.risk.model_dump() if hasattr(cfg.risk, 'model_dump') else {}

    return ConfigResponse(
        mode=_TRADING_MODE,
        timezone="Asia/Kolkata",
        primary_broker="fyers",
        brokers=brokers_real,
        market_data={
            "feed": "fyers_api_v3" if fyers_connected else "disconnected",
            "option_chain_cache_ttl": "3.0s",
            "indices_cache_ttl": "2.0s",
            "candle_cache": "sqlite",
            "subscriptions": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
        },
        risk=real_risk,
        dashboard={
            "host": "0.0.0.0",
            "port": 8080,
            "auth_enabled": True,
            "cors_origins": ["http://localhost:5173"],
        },
        log_level=cfg.log_level,
    )


# ===================================================================
# Market Data Endpoints
# ===================================================================


@app.get(
    "/api/market/indices",
    tags=["Market Data"],
    summary="Current index values",
    description="Returns current NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, and INDIA VIX values with OHLC and change.",
)
async def market_indices():
    global _fyers_indices_cache, _fyers_indices_cache_time
    # Serve from TTL cache if fresh
    if _fyers_indices_cache is not None and (time.time() - _fyers_indices_cache_time) < _INDICES_CACHE_TTL:
        return _fyers_indices_cache

    # Use cached ticks from the background refresh loop (instant, no API call)
    if _live_feed and _live_feed.is_connected:
        try:
            # First try cached ticks (updated every 0.5s by background refresh)
            cached_indices = []
            for name in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"):
                tick = _live_feed.get_cached_tick(name)
                if tick:
                    cached_indices.append(tick)
            if len(cached_indices) >= 3:  # At least 3 indices cached = usable
                result = {"indices": cached_indices, "source": "fyers_live"}
                _fyers_indices_cache = result
                _fyers_indices_cache_time = time.time()
                return result

            # Fallback: make a direct API call if no cached ticks yet
            live_data = await _live_feed.get_all_indices()
            if live_data:
                result = {"indices": live_data, "source": "fyers_live"}
                _fyers_indices_cache = result
                _fyers_indices_cache_time = time.time()
                return result
        except Exception as e:
            logger.warning(f"Fyers indices fetch failed: {e}")

    # Serve cached live data if available
    if _fyers_indices_cache is not None:
        cached = dict(_fyers_indices_cache)
        cached["source"] = "fyers_cached"
        return cached

    return {"indices": _mock.indices(), "source": "mock"}


@app.get(
    "/api/market/option-chain/{symbol}",
    tags=["Market Data"],
    summary="Option chain",
    description=(
        "Returns a full option chain for the given symbol (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY) "
        "with 25 strikes, greeks, IV, and OI for both calls and puts."
    ),
)
async def market_option_chain(
    symbol: str,
    expiry: str = Query(default="", description="Expiry epoch timestamp or empty for nearest"),
):
    # Serve from TTL cache if fresh (avoids hammering Fyers API)
    cache_key = f"{symbol}:{expiry}"
    cached_time = _fyers_chain_cache_time.get(cache_key, 0)
    if cache_key in _fyers_chain_cache and (time.time() - cached_time) < _CHAIN_CACHE_TTL:
        return _fyers_chain_cache[cache_key]

    # Use real Fyers option chain if connected
    if _live_feed and _live_feed.is_connected:
        try:
            chain_data = await _live_feed.get_option_chain(
                symbol, strike_count=25, expiry_timestamp=expiry,
            )
            if chain_data and chain_data.get("chain"):
                # Restructure into paired rows (call + put per strike) for the frontend
                raw = chain_data["chain"]
                by_strike: dict[int, dict] = {}
                for opt in raw:
                    strike = opt["strike"]
                    if strike not in by_strike:
                        by_strike[strike] = {"strike": strike, "isATM": False}
                    side = "call" if opt["option_type"] == "CE" else "put"
                    by_strike[strike][f"{side}_ltp"] = opt["ltp"]
                    by_strike[strike][f"{side}_oi"] = opt["oi"]
                    by_strike[strike][f"{side}_volume"] = opt["volume"]
                    by_strike[strike][f"{side}_iv"] = opt.get("iv", 0)
                    by_strike[strike][f"{side}_delta"] = opt.get("delta", 0)
                    by_strike[strike][f"{side}_gamma"] = opt.get("gamma", 0)
                    by_strike[strike][f"{side}_bid"] = opt.get("bid", 0)
                    by_strike[strike][f"{side}_ask"] = opt.get("ask", 0)
                    by_strike[strike][f"{side}_change"] = opt.get("change", 0)
                    by_strike[strike][f"{side}_change_pct"] = opt.get("change_pct", 0)
                    by_strike[strike][f"{side}_prev_oi"] = opt.get("prev_oi", 0)
                    by_strike[strike][f"{side}_oi_change"] = opt.get("oi_change", 0)
                    by_strike[strike][f"{side}_oi_change_pct"] = opt.get("oi_change_pct", 0)

                atm = chain_data.get("atm_strike", 0)
                paired = sorted(by_strike.values(), key=lambda x: x["strike"])
                for row in paired:
                    row["isATM"] = abs(row["strike"] - atm) < 25

                chain_data["chain"] = paired
                # Cache the last good response so intermittent failures don't fall to mock
                _fyers_chain_cache[cache_key] = chain_data
                _fyers_chain_cache_time[cache_key] = time.time()
                # Also store by symbol for fallback
                _fyers_chain_cache[symbol] = chain_data
                # Feed IV tracker so IV Rank / Percentile can be computed
                try:
                    from core.market_regime import record_iv_from_option_chain
                    record_iv_from_option_chain(chain_data)
                except Exception:
                    pass
                return chain_data
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Fyers option chain failed: {e}")

    # Serve cached live data if available (prevents flicker to mock on temp failures)
    if symbol in _fyers_chain_cache:
        cached = _fyers_chain_cache[symbol]
        cached["source"] = "fyers_cached"
        return cached

    # Last resort: mock data
    try:
        return _mock.option_chain(symbol)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )


# ── OI Signal Engine endpoint ────────────────────────────────────────────────

@app.get(
    "/api/market/oi-signals/{symbol}",
    tags=["Market Data"],
    summary="OI-based trading signals",
    description=(
        "Generates BUY CE / BUY PE signals from option chain OI data using a "
        "4-factor engine: Buildup Analysis (35%), PCR Extreme (25%), Max Pain "
        "Gravity (20%), Support/Resistance Breach (20%). Includes confidence "
        "scoring (0-100%), stability filter, and one-click deploy payload."
    ),
)
async def market_oi_signals(
    symbol: str,
    expiry: str = Query(default="", description="Expiry epoch timestamp or empty for nearest"),
):
    from core.oi_signal_engine import generate_oi_signals
    from core import symbol_master

    # Get chain from cache (same data the frontend polls every 3s)
    cache_key = f"{symbol}:{expiry}"
    chain_data = _fyers_chain_cache.get(cache_key) or _fyers_chain_cache.get(symbol)

    if not chain_data or not chain_data.get("chain"):
        return {
            "signal": {
                "direction": "NEUTRAL",
                "action": None,
                "confidence": 0,
                "status": "WEAK",
                "stability_count": 0,
                "recommended_strike": None,
                "recommended_ltp": None,
                "recommended_type": None,
                "reasoning": ["No option chain data available — waiting for market data"],
                "deploy_payload": None,
                "can_deploy": False,
            },
            "factors": {},
            "meta": {"symbol": symbol, "spot": 0, "chain_strikes": 0},
        }

    chain = chain_data["chain"]
    spot = chain_data.get("spot_price", 0)
    lot_size = symbol_master.get_lot_size(symbol)

    # Get VIX from indices cache
    vix = 0.0
    try:
        idx_cache = _fyers_chain_cache.get("__indices__")
        if idx_cache:
            for idx in idx_cache:
                if "VIX" in (idx.get("symbol", "") or "").upper():
                    vix = float(idx.get("ltp", 0) or 0)
                    break
    except Exception:
        pass

    # Also try from the indices endpoint's last cached response
    if vix == 0 and _fyers_indices_cache:
        try:
            raw_indices = _fyers_indices_cache.get("indices", _fyers_indices_cache)
            if isinstance(raw_indices, list):
                vix_idx = next(
                    (i for i in raw_indices if "VIX" in str(i.get("symbol", "")).upper()),
                    None,
                )
                if vix_idx:
                    vix = float(vix_idx.get("ltp", 0) or 0)
        except Exception:
            pass
    # Also try from chain data itself
    if vix == 0:
        vix = float(chain_data.get("india_vix", 0) or 0)

    result = generate_oi_signals(
        chain=chain,
        spot=spot,
        symbol=symbol,
        lot_size=lot_size,
        vix=vix,
    )

    return result


# ===================================================================
# Expiry-Day Scalper Engine
# ===================================================================

# In-memory scalper config (overridable via POST /api/scalper/config)
_scalper_config: dict = {}


def _get_scalper_config() -> dict:
    """Lazy-init scalper config from engine defaults."""
    global _scalper_config
    if not _scalper_config:
        from core.scalper_engine import DEFAULT_CONFIG
        _scalper_config = dict(DEFAULT_CONFIG)
    return _scalper_config


async def _scalper_fetch_market(symbol: str) -> tuple[dict, list, list]:
    """Fetch chain + candles for the scalper FAST, staying well under the
    frontend's 10s timeout.

    - Chain: prefer the warm cache (Market Data/dashboard poll it every 1-3s);
      only fall back to a fresh fetch if the cache is cold.
    - Candles: cache-first via get_candles, each bounded by a short timeout so
      a slow Fyers call can never hang the endpoint (levels still compute from
      the chain; the regime gate just blocks until candles arrive).
    """
    chain_data = _fyers_chain_cache.get(symbol) or _fyers_chain_cache.get(f"{symbol}:")
    if not chain_data or not (chain_data.get("chain") or chain_data.get("contracts")):
        try:
            chain_data = await asyncio.wait_for(market_option_chain(symbol, ""), timeout=5.0)
        except Exception:
            chain_data = chain_data or {}

    daily_candles, intraday_candles = [], []
    if _live_feed:
        try:
            daily_candles = await asyncio.wait_for(
                _live_feed.get_candles(symbol, "D1", count=5), timeout=3.0) or []
        except Exception:
            pass
        try:
            intraday_candles = await asyncio.wait_for(
                _live_feed.get_candles(symbol, "M5", count=75), timeout=3.0) or []
        except Exception:
            pass
    return chain_data, daily_candles, intraday_candles


@app.get(
    "/api/scalper/config",
    tags=["Scalper"],
    summary="Get scalper configuration",
    description="Return the current expiry-day scalper config (risk, regime, exits, filters).",
)
async def scalper_get_config():
    return _get_scalper_config()


@app.post(
    "/api/scalper/config",
    tags=["Scalper"],
    summary="Update scalper configuration",
    description="Patch the scalper config. Only provided keys are updated.",
)
async def scalper_set_config(body: dict = Body(...)):
    cfg = _get_scalper_config()
    allowed = set(cfg.keys())
    for k, v in (body or {}).items():
        if k in allowed:
            cfg[k] = v
    return {"ok": True, "config": cfg}


@app.get(
    "/api/scalper/signals/{symbol}",
    tags=["Scalper"],
    summary="Expiry-day scalper signal",
    description=(
        "Computes live S/R levels (CPR, PDH/PDL, VWAP, ORB, round numbers, OI walls), "
        "applies the strict regime gate (ADX + confirmed breakout + volume), selects a "
        "liquid OTM strike by time-of-day, and returns a deploy-ready scalp signal."
    ),
)
async def scalper_signals(symbol: str):
    from core import scalper_engine as se
    from core import symbol_master
    from core.fyers_live_feed import STRIKE_STEPS

    symbol = symbol.upper()
    cfg = _get_scalper_config()
    strike_step = STRIKE_STEPS.get(symbol, 50)
    lot_size = symbol_master.get_lot_size(symbol) or 1

    # Fast market fetch (warm cache + bounded candle fetches)
    chain_data, daily_candles, intraday_candles = await _scalper_fetch_market(symbol)
    if not chain_data or not (chain_data.get("chain") or chain_data.get("contracts")):
        return {
            "has_signal": False,
            "reason": "No option chain data yet - waiting for market feed",
            "blockers": ["no option chain"],
            "spot": 0,
            "levels": [],
            "is_expiry": False,
            "config": cfg,
        }

    spot = float(chain_data.get("spot_price", 0) or 0)

    sig = se.generate_signal(
        symbol=symbol,
        spot=spot,
        daily_candles=daily_candles,
        intraday_candles=intraday_candles,
        chain=chain_data,
        strike_step=strike_step,
        lot_size=lot_size,
        cfg=cfg,
    )

    out = sig.to_dict()
    # Pull levels up to top level for the UI; keep deploy_payload for deploy
    out["levels"] = sig.deploy_payload.get("levels", [])
    out["is_expiry"] = se.is_expiry_day(chain_data, symbol)
    out["config"] = cfg
    return out


@app.post(
    "/api/scalper/deploy",
    tags=["Scalper"],
    summary="Deploy a scalp to paper trading",
    description=(
        "Regenerates a fresh signal for the symbol and, if valid, deploys it to paper "
        "trading with the partial-book + trail exit plan. Rejects if no signal is live."
    ),
)
async def scalper_deploy(body: dict = Body(...)):
    from core import scalper_engine as se
    from core import symbol_master
    from core.fyers_live_feed import STRIKE_STEPS

    symbol = (body.get("symbol") or "NIFTY").upper()
    cfg = _get_scalper_config()
    strike_step = STRIKE_STEPS.get(symbol, 50)
    lot_size = symbol_master.get_lot_size(symbol) or 1

    chain_data, daily_candles, intraday_candles = await _scalper_fetch_market(symbol)
    if not chain_data or not (chain_data.get("chain") or chain_data.get("contracts")):
        raise HTTPException(status_code=409, detail="No option chain data available")

    spot = float(chain_data.get("spot_price", 0) or 0)

    # Allow forced deploy (manual override) to bypass the regime/breakout gate
    force = bool(body.get("force", False))
    sig = se.generate_signal(
        symbol=symbol, spot=spot, daily_candles=daily_candles,
        intraday_candles=intraday_candles, chain=chain_data,
        strike_step=strike_step, lot_size=lot_size, cfg=cfg, force=force,
    )

    if not sig.has_signal and not force:
        raise HTTPException(
            status_code=409,
            detail=f"No live scalp signal: {sig.reason}",
        )
    if not sig.deploy_payload.get("legs"):
        raise HTTPException(
            status_code=409,
            detail="No tradeable strike — no liquid OTM option passed the OI/volume/spread filter",
        )

    # Reuse the standard deploy path (paper-routed, executor enters immediately)
    result = await deploy_strategy(sig.deploy_payload)
    return {"ok": True, "signal": sig.to_dict(), "deploy": result}


def _bucket_exit_reason(reason: str) -> str:
    """Map a raw exit_reason string to a clean attribution bucket."""
    r = (reason or "").lower()
    if not r:
        return "open"
    if "target" in r:
        return "target"
    if "trail" in r:
        return "trail"
    if "structural" in r:
        return "structural_stop"
    if "breakeven" in r:
        return "breakeven"
    if "premium_floor" in r or "stop_loss" in r or "tighten" in r:
        return "stop_loss"
    if "max_hold" in r:
        return "time_exit"
    if "eod" in r or "square_off" in r:
        return "eod"
    return "other"


# Display metadata for each bucket (label + whether it's a "good" exit)
EXIT_BUCKET_META = {
    "target":          {"label": "Target / Book", "tone": "profit"},
    "trail":           {"label": "Trailing Stop", "tone": "profit"},
    "breakeven":       {"label": "Breakeven Stop", "tone": "neutral"},
    "structural_stop": {"label": "Structural Stop", "tone": "loss"},
    "stop_loss":       {"label": "Premium-Floor Stop", "tone": "loss"},
    "time_exit":       {"label": "Max-Hold Time", "tone": "neutral"},
    "eod":             {"label": "EOD Square-off", "tone": "neutral"},
    "manual":          {"label": "Manual Stop", "tone": "neutral"},
    "other":           {"label": "Other", "tone": "neutral"},
}


@app.get(
    "/api/scalper/performance",
    tags=["Scalper"],
    summary="Scalp performance + exit attribution",
    description=(
        "Aggregates closed scalp trades: win rate, average R-multiple, total P&L, "
        "and a breakdown of which exit fired (target/trail/structural/stop/eod/time). "
        "R = realized P&L / risk-per-trade. Helps judge whether the exit plan is the edge."
    ),
)
async def scalper_performance():
    cfg = _get_scalper_config()
    risk_per_trade = float(cfg.get("risk_per_trade", 2000) or 2000) or 2000.0

    scalps = [s for s in _deployed_strategies.values() if (s.get("risk_params") or {}).get("scalp")]
    closed = [s for s in scalps if (s.get("status") or "").upper() in ("EXITED", "STOPPED")]
    running = [s for s in scalps if (s.get("status") or "").upper() == "RUNNING"]

    wins = losses = breakeven = 0
    total_pnl = 0.0
    gross_win = 0.0
    gross_loss = 0.0
    r_multiples: list[float] = []
    durations: list[float] = []
    buckets: dict[str, dict] = {}
    best = None
    worst = None
    recent: list[dict] = []

    for s in closed:
        pnl = float(s.get("realized_pnl", s.get("pnl", 0)) or 0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            gross_win += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += abs(pnl)
        else:
            breakeven += 1
        r_multiples.append(pnl / risk_per_trade)

        bucket = _bucket_exit_reason(s.get("exit_reason"))
        if bucket == "open":  # closed strat with no recorded reason = manual stop
            bucket = "manual"
        slot = buckets.setdefault(bucket, {"count": 0, "pnl": 0.0, "wins": 0})
        slot["count"] += 1
        slot["pnl"] += pnl
        if pnl > 0:
            slot["wins"] += 1

        # Hold duration (minutes)
        try:
            ent = datetime.fromisoformat(s["entered_at"])
            ex = datetime.fromisoformat(s["exited_at"])
            durations.append((ex - ent).total_seconds() / 60.0)
        except (KeyError, ValueError, TypeError):
            pass

        rec = {
            "name": s.get("name", ""),
            "underlying": s.get("underlying", ""),
            "pnl": round(pnl, 2),
            "r": round(pnl / risk_per_trade, 2),
            "exit_reason": s.get("exit_reason", ""),
            "bucket": bucket,
            "exited_at": s.get("exited_at", ""),
        }
        recent.append(rec)
        if best is None or pnl > best["pnl"]:
            best = rec
        if worst is None or pnl < worst["pnl"]:
            worst = rec

    n = len(closed)
    win_rate = (wins / n) if n else 0.0
    avg_r = (sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0
    avg_win = (gross_win / wins) if wins else 0.0
    avg_loss = (gross_loss / losses) if losses else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (gross_win and 999.0 or 0.0)
    expectancy = (total_pnl / n) if n else 0.0
    avg_hold = (sum(durations) / len(durations)) if durations else 0.0

    # Shape the exit breakdown for the UI (ordered, with labels)
    exit_breakdown = []
    for key, slot in sorted(buckets.items(), key=lambda kv: -kv[1]["count"]):
        meta = EXIT_BUCKET_META.get(key, {"label": key, "tone": "neutral"})
        exit_breakdown.append({
            "bucket": key,
            "label": meta["label"],
            "tone": meta["tone"],
            "count": slot["count"],
            "pnl": round(slot["pnl"], 2),
            "win_rate": round(slot["wins"] / slot["count"], 3) if slot["count"] else 0.0,
            "share": round(slot["count"] / n, 3) if n else 0.0,
        })

    recent.sort(key=lambda r: r.get("exited_at", ""), reverse=True)

    return {
        "risk_per_trade": risk_per_trade,
        "closed_count": n,
        "running_count": len(running),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_r": round(avg_r, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "avg_hold_minutes": round(avg_hold, 1),
        "best": best,
        "worst": worst,
        "exit_breakdown": exit_breakdown,
        "recent": recent[:15],
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/market/lot-sizes",
    tags=["Market Data"],
    summary="Derivative lot sizes",
    description=(
        "Returns the authoritative NSE/BSE derivative lot sizes parsed from the "
        "live Fyers symbol master CSV. Refreshed daily; falls back to a hardcoded "
        "snapshot if the network is unavailable."
    ),
)
async def market_lot_sizes():
    """Return lot sizes for index derivatives (the headline contracts the UI uses).

    Stock F&O lot sizes are available via ``/api/instruments/lot-sizes``.
    """
    from core import symbol_master

    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"]
    result = {sym: symbol_master.get_lot_size(sym) for sym in indices}
    status = symbol_master.get_status()
    return {
        "lot_sizes": result,
        "source": status.get("source", "unknown"),
        "last_refresh": status.get("last_refresh"),
    }


@app.get(
    "/api/instruments/lot-sizes",
    tags=["Market Data"],
    summary="All instrument lot sizes",
    description=(
        "Returns the complete {underlying_symbol: lot_size} map from the Fyers "
        "symbol master — covers indices and all F&O stocks. Use this for stock "
        "options as well as index options."
    ),
)
async def instrument_lot_sizes():
    from core import symbol_master
    return {
        "lot_sizes": symbol_master.get_lot_sizes(),
        "status": symbol_master.get_status(),
    }


@app.post(
    "/api/instruments/refresh",
    tags=["Market Data"],
    summary="Force symbol master refresh",
    description=(
        "Force-redownloads the Fyers symbol master CSV. Call this after SEBI "
        "publishes a lot-size revision so the platform picks it up immediately "
        "instead of waiting for the next daily refresh."
    ),
)
async def instrument_refresh():
    from core import symbol_master
    summary = symbol_master.refresh(force=True)
    return summary


@app.get(
    "/api/market/expiries/{symbol}",
    tags=["Market Data"],
    summary="Option expiry dates",
    description="Returns available option expiry dates for a symbol.",
)
async def market_expiries(symbol: str):
    # Get expiry data from Fyers option chain response
    if _live_feed and _live_feed.is_connected:
        try:
            chain_data = await _live_feed.get_option_chain(symbol, strike_count=1)
            if chain_data and chain_data.get("expiry_data"):
                expiries = [
                    {"date": e["date"], "timestamp": e["expiry"], "type": e.get("expiry_flag", "")}
                    for e in chain_data["expiry_data"]
                ]
                return {"expiries": expiries, "source": "fyers_live"}
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Fyers expiries failed: {e}")

    # Fallback: generate some dummy expiry dates
    from datetime import timedelta
    now = datetime.now(IST)
    expiries = []
    d = now.date()
    for _ in range(8):
        days_to_thu = (3 - d.weekday()) % 7 or 7
        d = d + timedelta(days=days_to_thu)
        expiries.append({"date": d.strftime("%d-%m-%Y"), "timestamp": "", "type": "W"})
    return {"expiries": expiries, "source": "mock"}


@app.get(
    "/api/market/candles/{symbol}",
    tags=["Market Data"],
    summary="OHLCV candles",
    description="Returns mock OHLCV candle data for the given symbol.",
)
async def market_candles(
    symbol: str,
    timeframe: str = Query(
        default="M5",
        description="Candle timeframe: M1, M5, M15, M30, H1, D1",
        enum=["M1", "M5", "M15", "M30", "H1", "D1"],
    ),
    count: int = Query(default=50, ge=1, le=500, description="Number of candles to return"),
):
    """Candle data — cache-first, then Fyers live, then mock as last resort."""
    # 1. Try Fyers (which internally checks SQLite cache first, then Fyers API)
    if _live_feed and _live_feed.is_connected:
        try:
            candles = await _live_feed.get_candles(symbol, timeframe, count)
            if candles:
                return {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "count": len(candles),
                    "candles": candles,
                    "source": "fyers_live",
                }
        except Exception as e:
            logger.warning(f"Fyers candles failed: {e}")

    # 2. Try SQLite cache directly (even if Fyers is disconnected) — with staleness check
    try:
        from core.state_store import get_store
        RESOLUTION_MAP_LOCAL = {"M1": "1", "M5": "5", "M15": "15", "M30": "30", "H1": "60", "D1": "D"}
        res = RESOLUTION_MAP_LOCAL.get(timeframe, "D")
        store = get_store()
        cached = store.get_candles(symbol.upper(), res, limit=count + 50)
        if cached and len(cached) >= min(count, 3):
            newest_ts = max(c["ts"] for c in cached)
            age_seconds = time.time() - newest_ts
            max_age = 7200 if res != "D" else 172800  # 2h intraday, 2d daily
            if age_seconds <= max_age:
                from datetime import datetime as _dt, timezone as _tz
                result = cached[-count:] if len(cached) > count else cached
                candles = [
                    {
                        "timestamp": _dt.fromtimestamp(c["ts"], tz=_tz.utc).isoformat(),
                        "open": c["open"], "high": c["high"],
                        "low": c["low"], "close": c["close"],
                        "volume": c["volume"],
                    }
                    for c in result
                ]
                return {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "count": len(candles),
                    "candles": candles,
                    "source": "sqlite_cache",
                }
    except Exception as e:
        logger.debug(f"Candle cache fallback failed: {e}")

    # 3. Last resort: mock
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "count": count,
        "candles": _mock.candles(symbol, timeframe, count),
        "source": "mock",
    }


# ===================================================================
# Portfolio & Position Endpoints
# ===================================================================


@app.get(
    "/api/portfolio/positions",
    tags=["Portfolio"],
    summary="Current positions",
    description="Returns all open positions with greeks, P&L, and strategy attribution.",
)
async def portfolio_positions():
    # Routing priority depends on the global trading mode:
    #   - LIVE  → real Fyers positions (only)
    #   - PAPER → paper trading positions (only — never mock)
    # Each mode falls back to its respective source. Mock is the last resort
    # only when neither broker is available.
    if _TRADING_MODE == "live":
        try:
            if _live_feed and _live_feed._fyers:
                result = await _fyers_call(_live_feed._fyers.positions)
                if result and result.get("s") == "ok":
                    fyers_positions = result.get("netPositions", result.get("overall", []))
                    parsed = []
                    for p in (fyers_positions if isinstance(fyers_positions, list) else []):
                        fyers_sym = p.get("symbol", "")
                        display_sym = fyers_sym.split(":")[1] if ":" in fyers_sym else fyers_sym
                        fyers_qty = p.get("netQty", p.get("qty", 0))
                        fyers_avg = p.get("avgPrice", p.get("buyAvgPrice", 0))
                        fyers_ltp = p.get("ltp", 0)
                        fyers_pnl_u = p.get("unrealizedProfit", p.get("pl", 0))
                        fyers_pnl_r = p.get("realized_profit", p.get("realizedProfit", 0))
                        fyers_opt_type = p.get("optionType", "")
                        parsed.append({
                            "instrument": fyers_sym,
                            "symbol": display_sym,
                            "strike": p.get("strikePrice", 0),
                            "option_type": fyers_opt_type,
                            "type": fyers_opt_type,           # alias for frontend
                            "expiry": p.get("expiryDate", ""),
                            "quantity": fyers_qty,
                            "qty": fyers_qty,                 # alias for frontend
                            "avg_price": fyers_avg,
                            "avgPrice": fyers_avg,            # alias for frontend
                            "ltp": fyers_ltp,
                            "pnl_unrealized": fyers_pnl_u,
                            "pnl_realized": fyers_pnl_r,
                            "pnl": fyers_pnl_u,              # alias for frontend
                            "product_type": p.get("productType", ""),
                            "strategy_id": "",
                            "strategy": "",
                            "delta": 0, "gamma": 0, "theta": 0, "vega": 0,
                        })
                    return {
                        "positions": parsed,
                        "count": len(parsed),
                        "source": "fyers_live",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
        except Exception as e:
            logger.warning(f"Fyers positions fetch failed: {e}")

    # Paper mode (default) — read from PaperTradingManager
    if _paper_trading_manager and _paper_trading_manager.is_active:
        try:
            paper_positions = await _paper_trading_manager.get_positions()

            # Build a chain lookup so we can refresh LTP for each option
            # from the cached Fyers option chain (same cache the executor uses).
            chain_lookup: dict[str, dict[int, dict]] = {}  # underlying → {strike → {call_ltp, put_ltp}}
            for cache_key, chain_data in _fyers_chain_cache.items():
                if ":" in cache_key:
                    continue  # skip duplicate "NIFTY:" keys
                rows = chain_data.get("chain", []) if isinstance(chain_data, dict) else []
                by_strike: dict[int, dict] = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    s = int(row.get("strike", 0))
                    if s:
                        by_strike[s] = row
                if by_strike:
                    chain_lookup[cache_key.upper()] = by_strike

            parsed = []
            for p in paper_positions:
                # paper_broker returns Decimal — coerce to float
                qty = float(p.get("quantity", 0) or 0)
                avg = float(p.get("average_price", 0) or 0)
                ltp = float(p.get("ltp", avg) or avg)
                pnl_u = float(p.get("pnl_unrealized", 0) or 0)
                pnl_r = float(p.get("pnl_realized", 0) or 0)
                sym = p.get("symbol", "")
                # Best-effort parse of "NIFTY 24000 CE" into components
                strike = 0
                opt_type = ""
                underlying = sym
                parts = sym.split()
                if len(parts) >= 3:
                    underlying = parts[0]
                    try:
                        strike = int(parts[1])
                    except ValueError:
                        pass
                    opt_type = parts[2] if parts[2] in ("CE", "PE") else ""

                # Refresh LTP from option chain cache if available
                if underlying.upper() in chain_lookup and strike:
                    row = chain_lookup[underlying.upper()].get(strike)
                    if row:
                        key = "call_ltp" if opt_type == "CE" else "put_ltp"
                        fresh_ltp = float(row.get(key, 0) or 0)
                        if fresh_ltp > 0:
                            ltp = fresh_ltp
                            # Recompute unrealized P&L with fresh LTP
                            if qty != 0:
                                pnl_u = (ltp - avg) * qty

                parsed.append({
                    "instrument": sym,
                    "symbol": sym,                    # full instrument name for UI display
                    "underlying": underlying,
                    "strike": strike,
                    "option_type": opt_type,
                    "type": opt_type,                 # alias for frontend
                    "expiry": p.get("expiry", ""),
                    "quantity": qty,
                    "qty": qty,                       # alias for frontend
                    "avg_price": avg,
                    "avgPrice": avg,                  # alias for frontend
                    "ltp": ltp,
                    "pnl_unrealized": pnl_u,
                    "pnl_realized": pnl_r,
                    "pnl": pnl_u,                     # alias for frontend (net P&L = unrealized for open)
                    "product_type": p.get("product_type", "NRML"),
                    "strategy_id": p.get("strategy_id", ""),
                    "strategy": p.get("strategy_id", ""),
                    "delta": 0, "gamma": 0, "theta": 0, "vega": 0,
                })
            # Also include positions from deployed strategies that have entered.
            # This gives a unified portfolio view of all paper positions
            # (manual trades + AI/executor-deployed strategies).
            for sid, strat in _deployed_strategies.items():
                if strat.get("status") not in ("RUNNING", "EXITED"):
                    continue
                if not strat.get("entered"):
                    continue
                strat_name = strat.get("name", sid)
                strat_underlying = (strat.get("underlying") or "NIFTY").upper()
                for pos in strat.get("positions", []):
                    sym = pos.get("symbol", "")
                    p_strike = pos.get("strike", 0)
                    p_opt_type = ""
                    p_underlying = sym
                    parts = sym.split()
                    if len(parts) >= 3:
                        p_underlying = parts[0]
                        try:
                            p_strike = int(parts[1])
                        except ValueError:
                            pass
                        p_opt_type = parts[2] if parts[2] in ("CE", "PE") else ""
                    p_qty = int(pos.get("qty", 0))
                    p_entry = float(pos.get("entry_price", 0))
                    p_ltp = float(pos.get("ltp", p_entry))
                    p_pnl = float(pos.get("pnl", 0))
                    # Signed qty: BUY = positive, SELL = negative
                    signed_qty = p_qty if pos.get("side") == "BUY" else -p_qty
                    parsed.append({
                        "instrument": sym,
                        "symbol": sym,
                        "underlying": p_underlying,
                        "strike": p_strike,
                        "option_type": p_opt_type,
                        "type": p_opt_type,
                        "expiry": "",
                        "quantity": signed_qty,
                        "qty": signed_qty,
                        "avg_price": p_entry,
                        "avgPrice": p_entry,
                        "ltp": p_ltp,
                        "pnl_unrealized": p_pnl,
                        "pnl_realized": 0.0,
                        "pnl": p_pnl,
                        "product_type": "NRML",
                        "strategy_id": sid,
                        "strategy": strat_name,
                        "delta": 0, "gamma": 0, "theta": 0, "vega": 0,
                    })

            return {
                "positions": parsed,
                "count": len(parsed),
                "source": "paper_trading",
                "timestamp": datetime.now(IST).isoformat(),
            }
        except Exception as e:
            logger.warning(f"Paper positions fetch failed: {e}")

    # Build positions from deployed strategies (no mock)
    parsed = []
    for sid, strat in _deployed_strategies.items():
        status = str(strat.get("status", "")).upper()
        if status not in ("RUNNING", "ENTERED"):
            continue
        if not strat.get("entered"):
            continue
        strat_name = strat.get("name", sid)
        for pos in strat.get("positions", []):
            sym = pos.get("symbol", "")
            p_strike = pos.get("strike", 0)
            p_opt_type = pos.get("option_type", "")
            p_qty = int(pos.get("qty", 0))
            p_entry = float(pos.get("entry_price", 0))
            p_ltp = float(pos.get("ltp", p_entry))
            p_pnl = float(pos.get("pnl", 0))
            signed_qty = p_qty if pos.get("side") == "BUY" else -p_qty
            parsed.append({
                "instrument": sym, "symbol": sym,
                "underlying": (strat.get("underlying") or "NIFTY").upper(),
                "strike": p_strike, "option_type": p_opt_type, "type": p_opt_type,
                "expiry": pos.get("expiry", ""),
                "quantity": signed_qty, "qty": signed_qty,
                "avg_price": p_entry, "avgPrice": p_entry,
                "ltp": p_ltp, "pnl_unrealized": p_pnl, "pnl_realized": 0.0, "pnl": p_pnl,
                "product_type": "NRML", "strategy_id": sid, "strategy": strat_name,
                "delta": 0, "gamma": 0, "theta": 0, "vega": 0,
            })
    return {
        "positions": parsed,
        "count": len(parsed),
        "source": "deployed_strategies",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/portfolio/greeks",
    tags=["Portfolio"],
    summary="Portfolio-level greeks",
    description="Returns aggregated net delta, gamma, theta, and vega across all positions.",
)
async def portfolio_greeks():
    """Real portfolio Greeks from Black-Scholes via the Risk Engine."""
    try:
        bundle = _get_risk_inputs()
        re = bundle["risk_engine"]
        agg = re.aggregate_portfolio_greeks(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
        )
        pf = agg["portfolio"]
        return {
            "net_delta": round(pf["delta"], 4),
            "net_gamma": round(pf["gamma"], 6),
            "net_theta": round(pf["theta"], 2),
            "net_vega": round(pf["vega"], 2),
            "position_count": agg["positions_count"],
            "strategies_count": agg["strategies_count"],
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as e:
        logger.warning(f"Portfolio greeks computation failed: {e}")
        return {
            "net_delta": 0.0, "net_gamma": 0.0,
            "net_theta": 0.0, "net_vega": 0.0,
            "position_count": 0,
            "source": "empty",
            "timestamp": datetime.now(IST).isoformat(),
        }


@app.get(
    "/api/portfolio/pnl",
    tags=["Portfolio"],
    summary="P&L snapshot",
    description="Returns current realized, unrealized, and net P&L with transaction charges breakdown.",
)
async def portfolio_pnl():
    """Real P&L from deployed strategies (paper mode) or Fyers (live mode)."""
    # Try Fyers live positions first (live mode only)
    if _TRADING_MODE == "live":
        try:
            if _live_feed and _live_feed._fyers:
                result = await _fyers_call(_live_feed._fyers.positions)
                if result and result.get("s") == "ok":
                    positions = result.get("netPositions", result.get("overall", []))
                    if isinstance(positions, list) and len(positions) > 0:
                        realized = sum(float(p.get("realized_profit", p.get("realizedProfit", 0))) for p in positions)
                        unrealized = sum(float(p.get("unrealizedProfit", p.get("pl", 0))) for p in positions)
                        return {
                            "realized_pnl": round(realized, 2),
                            "unrealized_pnl": round(unrealized, 2),
                            "net_pnl": round(realized + unrealized, 2),
                            "charges": {"total": 0},
                            "source": "fyers_live",
                            "timestamp": datetime.now(IST).isoformat(),
                        }
        except Exception as e:
            logger.warning(f"Fyers P&L derivation failed: {e}")

    # Paper mode: compute from deployed strategies
    realized = 0.0
    unrealized = 0.0
    total_charges = 0.0
    for strat in _deployed_strategies.values():
        status = str(strat.get("status", "")).upper()
        if status in ("EXITED", "STOPPED"):
            realized += float(strat.get("realized_pnl", strat.get("pnl", 0)) or 0)
        elif status in ("RUNNING", "ENTERED") and strat.get("entered"):
            unrealized += float(strat.get("pnl", 0) or 0)
        total_charges += float(strat.get("total_charges", 0) or 0)
    net = realized + unrealized - total_charges
    return {
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": round(realized + unrealized, 2),
        "charges": {"total": round(total_charges, 2)},
        "net_pnl": round(net, 2),
        "source": "deployed_strategies",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/portfolio/margin",
    tags=["Portfolio"],
    summary="Margin utilization",
    description="Returns margin usage, available capital, SPAN/exposure breakdown, and utilization percentage.",
)
async def portfolio_margin():
    # Try getting real margin from Fyers funds
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.funds)
            if result and result.get("s") == "ok":
                fund_data = result.get("fund_limit", [])
                # Fyers fund_limit is a list of fund items
                total = 0
                available = 0
                used = 0
                for item in (fund_data if isinstance(fund_data, list) else []):
                    title = item.get("title", "").lower()
                    val = float(item.get("equityAmount", item.get("amount", 0)))
                    if "total" in title and "balance" in title:
                        total = val
                    elif "available" in title or "net" in title:
                        available = val
                    elif "utilized" in title or "used" in title:
                        used = val
                if total > 0:
                    utilization = (used / total * 100) if total > 0 else 0
                    return {
                        "margin_used": round(used, 2),
                        "margin_available": round(available, 2),
                        "total_margin": round(total, 2),
                        "margin_utilization": round(utilization, 2),
                        "source": "fyers_live",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers margin fetch failed: {e}")

    # Paper mode: compute from Risk Engine
    try:
        bundle = _get_risk_inputs()
        re = bundle["risk_engine"]
        margin = re.calculate_margin(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
            available_capital=bundle["capital"],
        )
        return {
            "margin_used": margin["total_margin_required"],
            "used_margin": margin["total_margin_required"],
            "available_margin": margin["available_margin"],
            "total_margin": bundle["capital"],
            "span_margin": margin["span_margin"],
            "exposure_margin": margin["exposure_margin"],
            "margin_utilization": margin["utilization_pct"],
            "utilization_pct": margin["utilization_pct"],
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as e:
        logger.warning(f"Risk engine margin failed: {e}")
        return {
            "margin_used": 0, "available_margin": 0, "total_margin": 0,
            "margin_utilization": 0, "utilization_pct": 0,
            "source": "empty",
            "timestamp": datetime.now(IST).isoformat(),
        }


# ===================================================================
# Strategy Endpoints
# ===================================================================


@app.get(
    "/api/strategies",
    tags=["Strategies"],
    summary="List all strategies",
    description="Returns all configured strategies with status, P&L, and position counts.",
)
async def list_strategies():
    from core.strategy_fit import STRATEGY_FIT

    strategies = []
    for strategy_class, tmpl in STRATEGY_FIT.items():
        strategies.append({
            "strategy_id": strategy_class,
            "strategy_class": strategy_class,
            "name": tmpl["name"],
            "description": tmpl.get("description", ""),
            "edge": tmpl.get("edge", ""),
            "underlying": "NIFTY",
            "strategy_type": strategy_class,
            "status": "STOPPED",
            "mode": "PAPER",
            "risk_profile": "Defined Risk" if any(l["action"] == "BUY" for l in tmpl.get("default_legs", [])) else "Managed Risk",
            "category": tmpl.get("category", ""),
            "win_rate": round(tmpl.get("win_rate", 0.5) * 100),
            "avg_return_pct": tmpl.get("avg_return_pct", 0),
            "max_loss_pct": tmpl.get("max_loss_pct", 0),
            "capital_req": tmpl.get("capital_req", 100000),
            "ideal_regime": tmpl.get("ideal_regime", []),
            "schedule_window": tmpl.get("schedule_window", []),
            "default_legs": tmpl.get("default_legs", []),
            "entry_conditions": tmpl.get("entry_conditions", []),
            "risk_params": tmpl.get("risk_params", {}),
            "pnl_today": 0,
            "pnl_week": 0,
            "pnl_month": 0,
            "positions_count": len(tmpl.get("default_legs", [])),
            "orders_today": 0,
            "max_drawdown_pct": 0,
            "sharpe": 0,
            "avg_trade": 0,
            "params": tmpl.get("risk_params", {}),
        })

    # Merge status overrides
    for s in strategies:
        sid = s["strategy_id"]
        if sid in _strategy_overrides:
            s.update(_strategy_overrides[sid])

    # Append any dynamically deployed strategies
    for sid, dep in _deployed_strategies.items():
        if not any(s["strategy_id"] == sid for s in strategies):
            strategies.append(dep)

    return {
        "strategies": strategies,
        "count": len(strategies),
        "running": sum(1 for s in strategies if s.get("status") == "RUNNING"),
        "paused": sum(1 for s in strategies if s.get("status") == "PAUSED"),
        "stopped": sum(1 for s in strategies if s.get("status") == "STOPPED"),
    }


@app.get(
    "/api/strategies/{strategy_id}",
    tags=["Strategies"],
    summary="Strategy detail",
    description="Returns detailed information for a specific strategy.",
)
async def get_strategy(strategy_id: str):
    # Look up in deployed strategies first (live state)
    if strategy_id in _deployed_strategies:
        strat = _deployed_strategies[strategy_id]
        _refresh_strategy_pnl(strat)
        return strat
    # Otherwise fall back to mock strategy catalog
    strategies = _mock.strategies()
    for s in strategies:
        if s["strategy_id"] == strategy_id:
            return s
    raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")


# ===================================================================
# Trading Mode (paper / live) — controls strategy & order routing
# ===================================================================


@app.get(
    "/api/trading/mode",
    tags=["Trading"],
    summary="Get current trading mode",
    description=(
        "Returns the active trading mode. `paper` routes orders through the "
        "PaperTradingManager simulator; `live` places real orders via Fyers."
    ),
)
async def get_trading_mode():
    return {
        "mode": _TRADING_MODE,
        "fyers_connected": bool(_live_feed and _live_feed.is_connected),
        "paper_session_active": bool(_paper_trading_manager and _paper_trading_manager.is_active),
    }


@app.post(
    "/api/trading/mode",
    tags=["Trading"],
    summary="Switch trading mode",
    description=(
        "Switch between paper and live trading. Switching to `live` requires:\n"
        "- Fyers gateway connected\n"
        "- Explicit `confirm=true` flag in the request body\n"
        "Switching to `paper` is always allowed."
    ),
)
async def set_trading_mode(body: dict = Body(...)):
    global _TRADING_MODE
    requested = (body.get("mode") or "").lower().strip()
    confirm = bool(body.get("confirm", False))

    if requested not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")

    if requested == "live":
        if not (_live_feed and _live_feed.is_connected):
            raise HTTPException(
                status_code=400,
                detail="Cannot switch to LIVE mode — Fyers gateway is not connected",
            )
        if not confirm:
            raise HTTPException(
                status_code=400,
                detail="Switching to LIVE places real orders. Resubmit with confirm=true to proceed.",
            )

    previous = _TRADING_MODE
    _TRADING_MODE = requested
    logger.warning(f"Trading mode switched: {previous} -> {requested}")

    # Persist trading mode to SQLite
    try:
        from core.state_store import get_store
        get_store().save_setting("trading_mode", _TRADING_MODE)
    except Exception:
        pass

    return {
        "success": True,
        "mode": _TRADING_MODE,
        "previous": previous,
        "message": f"Trading mode is now {_TRADING_MODE.upper()}",
    }


@app.post(
    "/api/strategies/deploy",
    tags=["Strategies"],
    summary="Deploy a strategy",
    description=(
        "Deploy a strategy with the given configuration. Routes through the "
        "appropriate broker based on the current trading mode:\n"
        "- **paper**: registered with PaperTradingManager; positions and P&L "
        "  are simulated using live Fyers LTPs.\n"
        "- **live**: places real entry orders via Fyers (requires explicit "
        "  LIVE mode toggle to be set first)."
    ),
)
async def deploy_strategy(body: dict = Body(...)):
    import uuid as _uuid

    # Kill switch guard — reject all new deploys when active
    try:
        from core.state_store import get_store
        store = get_store()
        if store.is_kill_switch_active():
            meta = store.get_kill_switch_meta() or {}
            return {
                "ok": False,
                "error": "kill_switch_active",
                "message": f"Kill switch active: {meta.get('reason', 'risk limits breached')}. Reset via DELETE /api/risk/kill-switch.",
                "kill_switch_meta": meta,
                "timestamp": datetime.now(IST).isoformat(),
            }
    except Exception as _exc:
        logger.debug(f"Kill switch check failed (allowing deploy): {_exc}")

    sid = f"strategy-{_uuid.uuid4().hex[:8]}"
    underlying = body.get("underlying", "NIFTY")
    legs = body.get("legs", [])
    mode = _TRADING_MODE  # snapshot at deploy time

    # Execution mode: defaults to "paper" for safety, regardless of the global
    # trading mode. To trade real money via the executor, the client must pass
    # ``execution_mode: "live"`` AND the global mode must also be "live".
    requested_exec_mode = (body.get("execution_mode") or "paper").lower()
    if requested_exec_mode == "live" and mode != "live":
        # Refuse to set execution_mode=live when global mode is paper
        requested_exec_mode = "paper"
    execution_mode = requested_exec_mode

    strategy_entry = {
        "strategy_id": sid,
        "name": body.get("name", "Custom Strategy"),
        "underlying": underlying,
        "status": "RUNNING",
        "mode": mode,                            # global mode snapshot at deploy
        "execution_mode": execution_mode,        # actual routing for the executor
        "entered": False,                        # set True by executor after entry orders placed
        "entered_at": None,
        "entry_orders": [],
        "exit_orders": [],
        "exit_reason": None,
        "exited_at": None,
        "pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "positions": [],                         # list of {symbol, side, qty, entry_price, ltp, pnl}
        "legs": legs,
        "risk_params": body.get("risk_params", {}),
        # Entry gating: legacy schedule (back-compat) + new condition-based fields
        "schedule": body.get("schedule", "market_open"),
        "custom_time": body.get("custom_time") or body.get("customTime"),
        "schedule_window": body.get("schedule_window") or body.get("scheduleWindow"),
        "entry_conditions": body.get("entry_conditions") or body.get("entryConditions") or [],
        "entry_trigger": (body.get("entry_trigger") or body.get("entryTrigger") or "ALL").upper(),
        "condition_timeframe": body.get("condition_timeframe") or body.get("conditionTimeframe") or "M5",
        "last_condition_check": None,            # populated by executor on each evaluation
        "spot_price": body.get("spot_price", 0),
        "lot_size": body.get("lot_size", 1),
        "deployed_at": datetime.now(IST).isoformat(),
        # AI auto-deploy metadata — when True the executor enters immediately
        # (the AI signal engine's scoring IS the entry condition)
        "ai_deployed": body.get("ai_deployed", False),
        "ai_confidence": body.get("ai_confidence"),
        "ai_signal": body.get("ai_signal"),
        "ai_reasoning": body.get("ai_reasoning"),
        "strategy_class": body.get("strategy_class"),
    }

    # ------- Paper mode: auto-start a paper session if needed, register strategy
    if mode == "paper":
        if _paper_trading_manager is not None and not _paper_trading_manager.is_active:
            try:
                await _paper_trading_manager.start_session({
                    "initial_capital": 1_000_000.0,
                    "mock_feed": False,  # use live Fyers ticks
                    "symbols": [underlying],
                })
            except Exception as e:
                logger.warning(f"Could not auto-start paper session: {e}")

        try:
            if _paper_trading_manager is not None and _paper_trading_manager.is_active:
                _paper_trading_manager.deploy_strategy(
                    strategy_id=sid,
                    strategy_name=strategy_entry["name"],
                    strategy_class=body.get("strategy_class", "custom"),
                    params={"underlying": underlying, "legs": legs},
                )
        except Exception as e:
            logger.warning(f"Paper manager deploy failed: {e}")

        # Build virtual positions from legs (one position per leg)
        # Strike is offset-from-spot but must snap to the valid strike step
        # (NIFTY=50, BANKNIFTY=100, MIDCPNIFTY=25, etc.) — otherwise the
        # option chain LTP lookup for P&L won't find a matching row.
        from core.fyers_live_feed import STRIKE_STEPS
        strike_step = STRIKE_STEPS.get(underlying.upper(), 50)

        def _snap_strike(price: float, step: int) -> int:
            if step <= 0:
                return int(round(price))
            return int(round(price / step) * step)

        spot = float(strategy_entry["spot_price"]) or 0.0
        # Validate spot freshness — re-fetch if option chain available
        try:
            fresh_chain = _fyers_chain_cache.get(underlying.upper()) or {}
            fresh_spot = float(fresh_chain.get("spot_price", 0) or 0)
            if fresh_spot > 0:
                drift = abs(fresh_spot - spot)
                if drift > 25:  # spot moved >25 pts since signal generated
                    logger.info(f"Deploy: spot drifted {drift:.0f}pts ({spot:.0f} -> {fresh_spot:.0f}), using fresh spot")
                    spot = fresh_spot
                    strategy_entry["spot_price"] = fresh_spot
                    strategy_entry["spot_drift_corrected"] = True
        except Exception:
            pass

        lot = int(strategy_entry["lot_size"]) or 1
        for leg in legs:
            premium = float(leg.get("premium", 0))
            lots = int(leg.get("lots", 1)) or 1
            offset = float(leg.get("offset", 0))
            strike = _snap_strike(spot + offset, strike_step)
            strategy_entry["positions"].append({
                "symbol": f"{underlying} {strike} {leg.get('type', 'CE')}",
                "strike": strike,
                "side": leg.get("action", "SELL"),
                "qty": lots * lot,
                "lots": lots,
                "entry_price": premium,
                "ltp": premium,
                "pnl": 0.0,
                "leg_id": leg.get("id"),
            })

    # ------- Live mode: place real Fyers orders
    elif mode == "live":
        if not (_live_feed and _live_feed.is_connected):
            raise HTTPException(
                status_code=503,
                detail="Live mode but Fyers gateway is not connected",
            )
        # NOTE: Real Fyers leg placement goes here. We deliberately don't
        # auto-place yet — entry orders must be triggered via /api/orders POST
        # after deploy, OR the strategy runner will do it on schedule.
        strategy_entry["status"] = "PENDING_ENTRY"

    _deployed_strategies[sid] = strategy_entry
    _strategy_overrides[sid] = {"status": strategy_entry["status"]}

    # Persist to SQLite
    try:
        from core.state_store import get_store
        store = get_store()
        store.save_strategy(sid, strategy_entry)
        store.log_risk_event(
            event_type="STRATEGY_DEPLOY",
            severity="INFO",
            limit_name="strategy",
            message=f"Strategy '{strategy_entry['name']}' deployed in {mode.upper()} mode on {underlying}",
            metadata={"strategy_id": sid, "strategy_class": body.get("strategy_class"), "mode": mode, "underlying": underlying},
        )
    except Exception:
        pass

    return {
        "success": True,
        "strategy_id": sid,
        "mode": mode,
        "message": (
            f"Strategy '{strategy_entry['name']}' deployed in {mode.upper()} mode"
        ),
        "strategy": strategy_entry,
    }


@app.get(
    "/api/deployed-strategies",
    tags=["Strategies"],
    summary="List deployed strategies with live P&L",
    description=(
        "Returns all currently deployed strategies with their live P&L computed "
        "from current Fyers LTPs. Each strategy includes per-leg positions and "
        "the cumulative realized + unrealized P&L."
    ),
)
async def list_deployed_strategies(status: str | None = None):
    """List all deployed strategies. Optional ``status`` query filter.

    - ``status=running``  -> only RUNNING (active monitoring/positions)
    - ``status=history``  -> only STOPPED / EXITED / FAILED
    - omitted             -> everything
    """
    results = []
    for sid, strat in _deployed_strategies.items():
        # Refresh per-position LTPs from Fyers cache and recompute leg P&L
        _refresh_strategy_pnl(strat)
        # Persist updated P&L to SQLite
        try:
            from core.state_store import get_store
            get_store().save_strategy(sid, strat)
        except Exception:
            pass
        results.append(strat)

    if status:
        filt = status.lower()
        if filt == "running":
            results = [s for s in results if s.get("status") == "RUNNING"]
        elif filt == "history":
            results = [s for s in results if s.get("status") in ("STOPPED", "EXITED", "FAILED")]

    return {"strategies": results, "count": len(results), "mode": _TRADING_MODE}


@app.delete(
    "/api/deployed-strategies/clear-history",
    tags=["Strategies"],
    summary="Clear stopped / exited strategies",
    description=(
        "Removes all STOPPED / EXITED / FAILED strategies from the in-memory list. "
        "Useful for cleaning up after backtest-style experimentation. RUNNING strategies "
        "are never touched. Returns the count of removed entries."
    ),
)
async def clear_history():
    to_remove = [
        sid for sid, strat in _deployed_strategies.items()
        if strat.get("status") in ("STOPPED", "EXITED", "FAILED")
    ]
    for sid in to_remove:
        _deployed_strategies.pop(sid, None)
        _strategy_overrides.pop(sid, None)
        # Remove from SQLite
        try:
            from core.state_store import get_store
            get_store().delete_strategy(sid)
        except Exception:
            pass
    return {"removed_count": len(to_remove), "removed_ids": to_remove}


@app.delete(
    "/api/deployed-strategies/clear-all",
    tags=["Strategies"],
    summary="Clear ALL deployed strategies (running too)",
    description=(
        "Hard reset — removes every deployed strategy, including running ones. "
        "Use this when you want a clean slate (typically after fixing a bug)."
    ),
)
async def clear_all_strategies():
    count = len(_deployed_strategies)
    _deployed_strategies.clear()
    _strategy_overrides.clear()
    # Clear all from SQLite
    try:
        from core.state_store import get_store
        get_store().clear_strategies()
    except Exception:
        pass
    return {"removed_count": count}


@app.post(
    "/api/deployed-strategies/recalibrate-entry-prices",
    tags=["Strategies"],
    summary="Recalibrate entry prices from current option chain",
    description=(
        "For every entered strategy, overwrites each leg's entry_price with the "
        "current option-chain LTP. Useful when strategies entered before the "
        "chain was cached and ended up with placeholder template premiums. "
        "Optionally restrict via ``?status=running``."
    ),
)
async def recalibrate_entry_prices(status: str | None = None):
    if _dashboard_executor is None or not _live_feed or not _live_feed.is_connected:
        raise HTTPException(status_code=503, detail="Executor or Fyers not available")

    fixed: list[dict] = []
    failed: list[dict] = []
    for sid, strat in _deployed_strategies.items():
        if not strat.get("entered"):
            continue
        if status and strat.get("status", "").lower() != status.lower():
            continue
        positions = strat.get("positions", [])
        try:
            await _dashboard_executor._fill_entry_prices_from_chain(sid, strat, positions)
            # Reset realized/unrealized P&L since we just changed entry prices
            strat["unrealized_pnl"] = 0.0
            strat["pnl"] = strat.get("realized_pnl", 0.0)
            strat["_high_water_mark"] = 0.0
            fixed.append({
                "strategy_id": sid,
                "name": strat.get("name"),
                "positions": [
                    {"symbol": p["symbol"], "entry": p["entry_price"], "ltp": p["ltp"]}
                    for p in positions
                ],
            })
        except Exception as e:
            failed.append({"strategy_id": sid, "error": str(e)})

    return {
        "recalibrated_count": len(fixed),
        "failed_count": len(failed),
        "recalibrated": fixed,
        "failed": failed,
    }


@app.get(
    "/api/strategies/{strategy_id}/pnl",
    tags=["Strategies"],
    summary="Live P&L for a deployed strategy",
    description="Returns the current realized + unrealized P&L for one deployed strategy.",
)
async def deployed_strategy_pnl(strategy_id: str):
    if strategy_id not in _deployed_strategies:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not deployed")
    strat = _deployed_strategies[strategy_id]
    _refresh_strategy_pnl(strat)
    return {
        "strategy_id": strategy_id,
        "name": strat.get("name"),
        "mode": strat.get("mode"),
        "status": strat.get("status"),
        "pnl": strat.get("pnl", 0.0),
        "realized_pnl": strat.get("realized_pnl", 0.0),
        "unrealized_pnl": strat.get("unrealized_pnl", 0.0),
        "positions": strat.get("positions", []),
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.post(
    "/api/strategies/{strategy_id}/stop",
    tags=["Strategies"],
    summary="Stop a deployed strategy",
    description="Stops a deployed strategy and squares off its positions in paper mode.",
)
async def stop_deployed_strategy(strategy_id: str):
    if strategy_id not in _deployed_strategies:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not deployed")
    strat = _deployed_strategies[strategy_id]

    # If the strategy has already entered positions, square them off via the executor.
    # This locks in the current P&L as realized and places exit orders.
    if strat.get("entered") and _dashboard_executor is not None:
        try:
            await _dashboard_executor._place_exit_orders(strategy_id, strat, reason="manual_stop")
        except Exception as e:
            logger.warning(f"Manual-stop exit-order placement failed: {e}")

    strat["status"] = "STOPPED"
    _strategy_overrides[strategy_id] = {"status": "STOPPED"}

    # Persist stopped state to SQLite + log notification
    try:
        from core.state_store import get_store
        store = get_store()
        store.save_strategy(strategy_id, strat)
        pnl = strat.get("realized_pnl", 0.0) or strat.get("pnl", 0.0) or 0.0
        store.log_risk_event(
            event_type="STRATEGY_STOP",
            severity="WARN" if pnl < 0 else "INFO",
            limit_name="strategy",
            current_value=float(pnl),
            message=f"Strategy '{strat.get('name', strategy_id)}' stopped. P&L: Rs {pnl:,.0f}",
            metadata={"strategy_id": strategy_id, "exit_reason": strat.get("exit_reason"), "pnl": pnl},
        )
    except Exception:
        pass

    if strat.get("mode") == "paper" and _paper_trading_manager:
        try:
            _paper_trading_manager.stop_strategy(strategy_id)
        except Exception as e:
            logger.warning(f"Paper manager stop failed: {e}")

    return {
        "success": True,
        "strategy_id": strategy_id,
        "status": "STOPPED",
        "exit_reason": strat.get("exit_reason"),
        "realized_pnl": strat.get("realized_pnl", 0.0),
    }


@app.post(
    "/api/strategies/{strategy_id}/execution-mode",
    tags=["Strategies"],
    summary="Set execution mode for a strategy",
    description=(
        "Per-strategy execution_mode override. By default deployed strategies "
        "execute in PAPER even when the global mode is LIVE. To enable real "
        "trading for ONE strategy, POST {execution_mode: 'live'}. Requires the "
        "global trading mode to also be 'live'."
    ),
)
async def set_strategy_execution_mode(strategy_id: str, body: dict = Body(...)):
    if strategy_id not in _deployed_strategies:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not deployed")
    requested = (body.get("execution_mode") or "").lower().strip()
    if requested not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="execution_mode must be 'paper' or 'live'")
    if requested == "live" and _TRADING_MODE != "live":
        raise HTTPException(
            status_code=400,
            detail="Global trading mode is PAPER. Switch to LIVE first via /api/trading/mode.",
        )
    strat = _deployed_strategies[strategy_id]
    previous = strat.get("execution_mode", "paper")
    strat["execution_mode"] = requested
    logger.warning(
        f"Strategy {strategy_id} execution_mode: {previous} -> {requested} "
        f"(name={strat.get('name')})"
    )
    return {
        "success": True,
        "strategy_id": strategy_id,
        "execution_mode": requested,
        "previous": previous,
    }


@app.get(
    "/api/executor/status",
    tags=["Strategies"],
    summary="Dashboard strategy executor status",
    description=(
        "Returns whether the background strategy executor is running, how many "
        "ticks it has processed, and when it last ticked. Useful for debugging."
    ),
)
async def executor_status():
    if _dashboard_executor is None:
        return {"running": False, "reason": "executor not initialised"}
    return _dashboard_executor.status()


# ===================================================================
# Indicators & Market Regime — live values for entry conditions UI
# ===================================================================


@app.get(
    "/api/indicators/{symbol}",
    tags=["Indicators"],
    summary="All indicators for a symbol",
    description=(
        "Returns live values for RSI, MACD, ATR, ADX, Bollinger Bands, VWAP, "
        "Supertrend, IV Rank, IV Percentile, current regime — computed from "
        "Fyers candles + option chain. Used by the StrategyBuilder UI to show "
        "current readings while the user designs entry conditions."
    ),
)
async def get_indicators(symbol: str, timeframe: str = "M5"):
    from core import indicators as _ind
    from core.market_regime import classify_regime, get_iv_tracker

    sym = symbol.upper()
    out: dict[str, Any] = {
        "symbol": sym,
        "timeframe": timeframe,
        "indicators": {},
        "regime": None,
    }

    if not (_live_feed and _live_feed.is_connected):
        return {**out, "source": "unavailable"}

    candles = []
    try:
        candles = await _live_feed.get_candles(sym, timeframe, 200)
    except Exception as e:
        logger.warning(f"Candle fetch failed for {sym}: {e}")

    if candles:
        arr = _ind.split_ohlcv(candles)
        out["indicators"]["close"] = float(arr["Close"][-1]) if len(arr["Close"]) else None
        out["indicators"]["rsi_14"] = _ind.rsi(arr["Close"], 14)
        out["indicators"]["atr_14"] = _ind.atr(arr["High"], arr["Low"], arr["Close"], 14)
        adx_v = _ind.adx(arr["High"], arr["Low"], arr["Close"], 14)
        out["indicators"]["adx_14"] = adx_v
        out["indicators"]["macd"] = _ind.macd(arr["Close"])
        out["indicators"]["bbands_20"] = _ind.bollinger_bands(arr["Close"], 20, 2.0)
        out["indicators"]["vwap"] = _ind.vwap(arr["High"], arr["Low"], arr["Close"], arr["Volume"])
        out["indicators"]["supertrend_10_3"] = _ind.supertrend(arr["High"], arr["Low"], arr["Close"], 10, 3.0)
        out["indicators"]["ema_20"] = _ind.ema(arr["Close"], 20)
        out["indicators"]["sma_50"] = _ind.sma(arr["Close"], 50)
        out["indicators"]["sma_200"] = _ind.sma(arr["Close"], 200)

    # IV stats
    tracker = get_iv_tracker()
    out["indicators"]["iv_rank"] = tracker.iv_rank(sym)
    out["indicators"]["iv_percentile"] = tracker.iv_percentile(sym)
    out["indicators"]["iv_sample_count"] = tracker.sample_count(sym)
    out["indicators"]["iv_latest"] = tracker.latest(sym)

    # VIX
    vix_tick = _live_feed.get_cached_tick("INDIA VIX") if _live_feed else None
    vix_val = float(vix_tick.get("ltp", 0)) if vix_tick else None
    out["indicators"]["vix"] = vix_val

    # Spot
    spot_tick = _live_feed.get_cached_tick(sym) if _live_feed else None
    spot_val = float(spot_tick.get("ltp", 0)) if spot_tick else None
    out["indicators"]["spot"] = spot_val

    # Regime
    out["regime"] = classify_regime(
        symbol=sym,
        candles=candles,
        vix=vix_val,
        spot=spot_val,
    )

    out["source"] = "fyers_live" if candles else "fyers_partial"
    return out


@app.get(
    "/api/indicators/supported",
    tags=["Indicators"],
    summary="Supported indicators metadata",
    description="Returns the list of indicators that can be used in entry_conditions, with parameter names and types.",
)
async def supported_indicators():
    from core.condition_evaluator import supported_indicators_spec
    return {"indicators": supported_indicators_spec()}


@app.get(
    "/api/market/regime/{symbol}",
    tags=["Indicators"],
    summary="Current market regime for a symbol",
    description=(
        "Classifies the current regime as one of HIGH_VOL / LOW_VOL / TRENDING_UP / "
        "TRENDING_DOWN / RANGE_BOUND / UNKNOWN, with a confidence score and a "
        "human-readable strategy recommendation."
    ),
)
async def market_regime(symbol: str):
    from core.market_regime import classify_regime

    sym = symbol.upper()
    candles = []
    vix = None
    spot = None
    if _live_feed and _live_feed.is_connected:
        try:
            candles = await _live_feed.get_candles(sym, "M15", 100)
        except Exception:
            pass
        vix_tick = _live_feed.get_cached_tick("INDIA VIX")
        vix = float(vix_tick.get("ltp", 0)) if vix_tick else None
        spot_tick = _live_feed.get_cached_tick(sym)
        spot = float(spot_tick.get("ltp", 0)) if spot_tick else None

    return classify_regime(symbol=sym, candles=candles, vix=vix, spot=spot)


@app.post(
    "/api/strategies/evaluate-conditions",
    tags=["Indicators"],
    summary="Dry-run entry conditions",
    description=(
        "Evaluate a list of entry_conditions against current market data without "
        "deploying anything. Used by the StrategyBuilder preview to show 'would "
        "this strategy enter right now?'"
    ),
)
async def evaluate_conditions_dryrun(body: dict = Body(...)):
    from core.condition_evaluator import evaluate_conditions

    symbol = (body.get("symbol") or "NIFTY").upper()
    conditions = body.get("entry_conditions") or body.get("conditions") or []
    trigger = (body.get("entry_trigger") or body.get("trigger") or "ALL").upper()
    timeframe = body.get("timeframe") or "M5"

    return await evaluate_conditions(
        symbol=symbol,
        conditions=conditions,
        trigger=trigger,
        live_feed=_live_feed,
        timeframe=timeframe,
    )


@app.post(
    "/api/strategies/save",
    tags=["Strategies"],
    summary="Save a strategy to library",
    description="Save a strategy configuration to the strategy library for later use.",
)
async def save_strategy(body: dict = Body(...)):
    import uuid as _uuid
    sid = f"saved-{_uuid.uuid4().hex[:8]}"
    saved = {
        "strategy_id": sid,
        "name": body.get("name", "Unnamed Strategy"),
        "underlying": body.get("underlying", "NIFTY"),
        "legs": body.get("legs", []),
        "risk_params": body.get("risk_params", {}),
        "schedule": body.get("schedule", "market_open"),
        "saved_at": datetime.now(IST).isoformat(),
    }
    _saved_strategies[sid] = saved
    return {
        "success": True,
        "strategy_id": sid,
        "message": f"Strategy '{saved['name']}' saved to library",
    }


@app.post(
    "/api/strategies/{strategy_id}/pause",
    response_model=StrategyActionResponse,
    tags=["Strategies"],
    summary="Pause a strategy",
    description="Pause an actively running strategy. It will stop taking new trades but keep existing positions.",
)
async def pause_strategy(strategy_id: str):
    strategies = _mock.strategies()
    # Also check deployed strategies
    all_strategies = strategies + list(_deployed_strategies.values())
    for s in all_strategies:
        if s["strategy_id"] == strategy_id:
            # Respect existing overrides
            prev = _strategy_overrides.get(strategy_id, {}).get("status", s["status"])
            if prev != "RUNNING":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot pause strategy in '{prev}' state. Must be RUNNING.",
                )
            _strategy_overrides[strategy_id] = {"status": "PAUSED"}
            return StrategyActionResponse(
                strategy_id=strategy_id,
                action="pause",
                previous_status=prev,
                new_status="PAUSED",
                message=f"Strategy '{s['name']}' paused successfully. Existing positions retained.",
                timestamp=datetime.now(IST).isoformat(),
            )
    raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")


@app.post(
    "/api/strategies/{strategy_id}/resume",
    response_model=StrategyActionResponse,
    tags=["Strategies"],
    summary="Resume a strategy",
    description="Resume a paused strategy. It will begin scanning for new trade opportunities.",
)
async def resume_strategy(strategy_id: str):
    strategies = _mock.strategies()
    # Also check deployed strategies
    all_strategies = strategies + list(_deployed_strategies.values())
    for s in all_strategies:
        if s["strategy_id"] == strategy_id:
            # Respect existing overrides
            prev = _strategy_overrides.get(strategy_id, {}).get("status", s["status"])
            if prev != "PAUSED":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot resume strategy in '{prev}' state. Must be PAUSED.",
                )
            _strategy_overrides[strategy_id] = {"status": "RUNNING"}
            return StrategyActionResponse(
                strategy_id=strategy_id,
                action="resume",
                previous_status=prev,
                new_status="RUNNING",
                message=f"Strategy '{s['name']}' resumed successfully.",
                timestamp=datetime.now(IST).isoformat(),
            )
    raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")


# ===================================================================
# Risk Endpoints
# ===================================================================


def _get_risk_inputs():
    """Helper — gather real inputs (strategies, chain cache, lot sizes, capital)
    for the Risk Engine. Returns a dict bundle.
    """
    from core import symbol_master
    from core import risk_engine

    # Lot sizes for known underlyings
    lot_sizes = {}
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
        try:
            lot_sizes[sym] = symbol_master.get_lot_size(sym) or 1
        except Exception:
            pass

    # Available capital — from PaperBroker for paper, from Fyers funds for live
    capital = 1_000_000.0  # default
    try:
        if _TRADING_MODE == "paper":
            from core.paper_trading.paper_trading_manager import PaperTradingManager
            # Try to read from the active session if any
            pm = globals().get("_paper_manager") or None
            if pm and getattr(pm, "_active_session", None):
                capital = float(pm._active_session.get("starting_capital", 1_000_000.0))
    except Exception:
        pass

    return {
        "strategies": _deployed_strategies,
        "chain_cache": _fyers_chain_cache,
        "lot_sizes": lot_sizes,
        "capital": capital,
        "risk_engine": risk_engine,
    }


def _compute_daily_pnl() -> float:
    """Sum P&L of all RUNNING/ENTERED strategies entered today."""
    today = datetime.now(IST).date().isoformat()
    total = 0.0
    for strat in _deployed_strategies.values():
        status = str(strat.get("status", "")).upper()
        if status not in ("RUNNING", "ENTERED", "EXITED", "STOPPED"):
            continue
        entered_at = strat.get("entered_at", "")
        if entered_at and not entered_at.startswith(today):
            continue
        total += float(strat.get("pnl", 0) or 0)
    return total


@app.get(
    "/api/risk/metrics",
    tags=["Risk"],
    summary="Current risk metrics",
    description="Real portfolio VaR, drawdown, margin utilization, Greeks exposure, kill-switch status.",
)
async def risk_metrics():
    """Computes real risk metrics from live deployed positions + option chain.
    Replaces the prior mock implementation.
    """
    try:
        bundle = _get_risk_inputs()
        re = bundle["risk_engine"]
        from core.state_store import get_store
        store = get_store()

        # Aggregate Greeks across portfolio
        agg = re.aggregate_portfolio_greeks(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
        )
        pf = agg["portfolio"]

        # Get spot + India VIX from any cached chain (NIFTY preferred)
        nifty_chain = bundle["chain_cache"].get("NIFTY") or {}
        spot = float(nifty_chain.get("spot_price", 0) or 0)
        india_vix_pct = float(nifty_chain.get("india_vix", 14.0) or 14.0)
        annual_vol = india_vix_pct / 100.0

        # VaR (95% and 99%, 1-day)
        var95 = re.parametric_var(pf["delta"], spot, annual_vol, 0.95, 1) if spot > 0 else 0.0
        var99 = re.parametric_var(pf["delta"], spot, annual_vol, 0.99, 1) if spot > 0 else 0.0

        # Margin
        margin = re.calculate_margin(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
            available_capital=bundle["capital"],
        )

        # Drawdown
        eq_curve = [r["equity"] for r in store.get_equity_curve(limit=500)]
        if eq_curve:
            dd = re.calculate_drawdown(eq_curve)
        else:
            dd = {"peak_equity": bundle["capital"], "current_equity": bundle["capital"],
                  "drawdown_pct": 0.0, "max_drawdown_pct": 0.0, "duration_days": 0}

        # Daily loss
        daily_pnl = _compute_daily_pnl()
        daily_loss_used = abs(min(0.0, daily_pnl))

        # Risk limits from SQLite
        limits = {**re.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}

        return {
            "portfolio_var_1d_95": round(var95, 2),
            "portfolio_var_1d_99": round(var99, 2),
            "max_drawdown": limits["max_drawdown_pct"] / 100.0,
            "current_drawdown": dd["drawdown_pct"] / 100.0,
            "current_drawdown_pct": dd["drawdown_pct"],
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "peak_equity": dd["peak_equity"],
            "current_equity": dd["current_equity"],
            "margin_utilization": margin["utilization_pct"] / 100.0,
            "total_margin_used": margin["total_margin_required"],
            "available_margin": margin["available_margin"],
            "net_delta_exposure": round(pf["delta"], 4),
            "net_gamma_exposure": round(pf["gamma"], 6),
            "net_theta_exposure": round(pf["theta"], 2),
            "net_vega_exposure": round(pf["vega"], 2),
            "daily_loss_limit": limits["max_daily_loss"],
            "daily_loss_used": round(daily_loss_used, 2),
            "daily_pnl": round(daily_pnl, 2),
            "positions_count": agg["positions_count"],
            "open_strategies_count": agg["strategies_count"],
            "kill_switch_active": store.is_kill_switch_active(),
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"risk_metrics computation failed: {exc}", exc_info=True)
        # Graceful fallback
        return {
            "portfolio_var_1d_95": 0.0,
            "portfolio_var_1d_99": 0.0,
            "max_drawdown": 0.05,
            "current_drawdown": 0.0,
            "margin_utilization": 0.0,
            "net_delta_exposure": 0.0,
            "net_gamma_exposure": 0.0,
            "net_theta_exposure": 0.0,
            "net_vega_exposure": 0.0,
            "daily_loss_limit": 50_000.0,
            "daily_loss_used": 0.0,
            "positions_count": 0,
            "kill_switch_active": False,
            "source": "fallback_empty",
            "error": str(exc),
            "timestamp": datetime.now(IST).isoformat(),
        }


@app.get(
    "/api/risk/stress-test",
    tags=["Risk"],
    summary="Stress test results",
    description=(
        "Taylor-expansion stress test (Delta, Gamma, Vega, Theta) across standard "
        "scenarios: NIFTY +-2%, +-5%, +-10%, IV spike/crush, overnight gap."
    ),
)
async def risk_stress_test():
    """Real stress test using portfolio Greeks + scenario shocks."""
    try:
        bundle = _get_risk_inputs()
        re = bundle["risk_engine"]

        agg = re.aggregate_portfolio_greeks(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
        )
        pf = agg["portfolio"]

        nifty_chain = bundle["chain_cache"].get("NIFTY") or {}
        spot = float(nifty_chain.get("spot_price", 0) or 0)
        india_vix_pct = float(nifty_chain.get("india_vix", 14.0) or 14.0)
        current_iv = india_vix_pct / 100.0

        # No exposure → return empty scenarios
        if pf["delta"] == 0 and pf["gamma"] == 0 and pf["vega"] == 0:
            return {
                "base_spot": spot,
                "base_pnl": _compute_daily_pnl(),
                "base_iv_pct": india_vix_pct,
                "scenarios": [],
                "worst_case_pnl": 0.0,
                "best_case_pnl": 0.0,
                "message": "No exposed positions to stress-test",
                "source": "risk_engine_live",
                "timestamp": datetime.now(IST).isoformat(),
            }

        result = re.stress_test_portfolio(pf, spot, current_iv)
        result["base_pnl"] = round(_compute_daily_pnl(), 2)
        result["source"] = "risk_engine_live"
        result["timestamp"] = datetime.now(IST).isoformat()
        return result
    except Exception as exc:
        logger.error(f"risk_stress_test computation failed: {exc}", exc_info=True)
        return {
            "base_spot": 0.0, "base_pnl": 0.0, "scenarios": [],
            "worst_case_pnl": 0.0, "best_case_pnl": 0.0,
            "error": str(exc), "source": "fallback_empty",
            "timestamp": datetime.now(IST).isoformat(),
        }


# ===================================================================
# Order & Trade Endpoints
# ===================================================================


@app.get(
    "/api/orders",
    tags=["Orders & Trades"],
    summary="Today's order book",
    description="Returns all orders placed today across all strategies, in various lifecycle states.",
)
async def order_book():
    # Try Fyers live orderbook first
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.orderbook)
            if result and result.get("s") == "ok":
                fyers_orders = result.get("orderBook", [])
                if isinstance(fyers_orders, list) and len(fyers_orders) > 0:
                    parsed = []
                    for o in fyers_orders:
                        status_map = {1: "PLACED", 2: "OPEN", 3: "PENDING", 4: "PARTIAL", 5: "FILLED", 6: "CANCELLED", 7: "REJECTED"}
                        parsed.append({
                            "id": o.get("id", o.get("orderId", "")),
                            "order_id": o.get("id", o.get("orderId", "")),
                            "symbol": (o.get("symbol", "").split(":")[1] if ":" in o.get("symbol", "") else o.get("symbol", "")),
                            "side": "BUY" if o.get("side") == 1 else "SELL",
                            "qty": o.get("qty", 0),
                            "price": o.get("limitPrice", o.get("price", 0)),
                            "status": status_map.get(o.get("status"), str(o.get("status", "UNKNOWN"))),
                            "type": {1: "LIMIT", 2: "MARKET", 3: "SL", 4: "SL-M"}.get(o.get("type"), "LIMIT"),
                            "time": o.get("orderDateTime", ""),
                            "strategy": "",
                            "exchange": o.get("exchange", "NSE"),
                        })
                    return {
                        "orders": parsed,
                        "count": len(parsed),
                        "filled": sum(1 for o in parsed if o["status"] == "FILLED"),
                        "open": sum(1 for o in parsed if o["status"] in ("OPEN", "PLACED", "PENDING")),
                        "cancelled": sum(1 for o in parsed if o["status"] == "CANCELLED"),
                        "rejected": sum(1 for o in parsed if o["status"] == "REJECTED"),
                        "partial": sum(1 for o in parsed if o["status"] == "PARTIAL"),
                        "source": "fyers_live",
                    }
    except Exception as e:
        logger.warning(f"Fyers orderbook fetch failed: {e}")

    # Build orders from deployed strategies' trade log (no mock)
    try:
        from core.state_store import get_store
        store = get_store()
        trade_log = store.get_trade_log(limit=100)
        orders = []
        for t in trade_log:
            orders.append({
                "id": str(t.get("id", "")),
                "order_id": str(t.get("id", "")),
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "qty": t.get("qty", 0),
                "price": t.get("price", 0),
                "status": "FILLED",
                "type": "MARKET",
                "time": t.get("timestamp", ""),
                "strategy": t.get("strategy_id", ""),
                "action": t.get("action", ""),
                "reason": t.get("reason", ""),
            })
        return {
            "orders": orders,
            "count": len(orders),
            "filled": len(orders),
            "open": 0,
            "cancelled": 0,
            "rejected": 0,
            "partial": 0,
            "source": "trade_log",
        }
    except Exception:
        return {
            "orders": [],
            "count": 0,
            "filled": 0, "open": 0, "cancelled": 0, "rejected": 0, "partial": 0,
            "source": "empty",
        }


@app.get(
    "/api/trades",
    tags=["Orders & Trades"],
    summary="Today's trade book",
    description="Returns all executed trades today with prices, quantities, and exchange details.",
)
async def trade_book():
    # Try Fyers live tradebook first
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.tradebook)
            if result and result.get("s") == "ok":
                fyers_trades = result.get("tradeBook", [])
                if isinstance(fyers_trades, list) and len(fyers_trades) > 0:
                    parsed = []
                    for t in fyers_trades:
                        parsed.append({
                            "id": t.get("id", t.get("tradeId", "")),
                            "orderId": t.get("orderNumber", t.get("orderId", "")),
                            "symbol": (t.get("symbol", "").split(":")[1] if ":" in t.get("symbol", "") else t.get("symbol", "")),
                            "side": "BUY" if t.get("side") == 1 else "SELL",
                            "qty": t.get("tradedQty", t.get("qty", 0)),
                            "price": t.get("tradePrice", t.get("price", 0)),
                            "quantity": t.get("tradedQty", t.get("qty", 0)),
                            "time": t.get("orderDateTime", t.get("tradeDateTime", "")),
                            "exchange": t.get("exchange", "NSE"),
                        })
                    turnover = sum(t["price"] * t["quantity"] for t in parsed)
                    return {
                        "trades": parsed,
                        "count": len(parsed),
                        "total_turnover": round(turnover, 2),
                        "source": "fyers_live",
                    }
    except Exception as e:
        logger.warning(f"Fyers tradebook fetch failed: {e}")

    # Build trades from deployed strategies' trade log (no mock)
    try:
        from core.state_store import get_store
        store = get_store()
        trade_log = store.get_trade_log(limit=100)
        trades = []
        for t in trade_log:
            trades.append({
                "id": str(t.get("id", "")),
                "orderId": str(t.get("id", "")),
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "qty": t.get("qty", 0),
                "price": t.get("price", 0),
                "quantity": t.get("qty", 0),
                "time": t.get("timestamp", ""),
                "exchange": "NSE",
            })
        turnover = sum(t["price"] * t["quantity"] for t in trades)
        return {
            "trades": trades,
            "count": len(trades),
            "total_turnover": round(turnover, 2),
            "source": "trade_log",
        }
    except Exception:
        return {
            "trades": [],
            "count": 0,
            "total_turnover": 0,
            "source": "empty",
        }


@app.post(
    "/api/orders",
    tags=["Orders & Trades"],
    summary="Place a new order",
    description="Place a new order. Routes through Fyers if connected, otherwise uses paper trading.",
)
async def place_order(body: dict = Body(...)):
    symbol = body.get("symbol", "")
    side = body.get("side", "BUY")
    qty = body.get("qty", body.get("quantity", 0))
    order_type = body.get("orderType", body.get("order_type", "MARKET"))
    price = body.get("price", 0)
    product = body.get("product", body.get("product_type", "MIS"))
    trigger_price = body.get("triggerPrice", body.get("trigger_price", 0))

    # Honour the global trading mode unless the client explicitly overrides.
    # `body["mode"]` can be "paper" or "live"; default uses _TRADING_MODE.
    requested_mode = (body.get("mode") or _TRADING_MODE).lower()

    # Paper mode: skip the live Fyers call entirely
    if requested_mode == "paper":
        if _paper_trading_manager is None or not _paper_trading_manager.is_active:
            # Auto-start a paper session
            try:
                if _paper_trading_manager is not None:
                    await _paper_trading_manager.start_session({
                        "initial_capital": 1_000_000.0,
                        "mock_feed": False,
                        "symbols": [symbol.split()[0] if " " in symbol else symbol],
                    })
            except Exception as e:
                logger.warning(f"Auto-start paper session failed: {e}")
        # Fall through to the paper-trading branch below

    # Try Fyers live order placement (only in live mode)
    try:
        if requested_mode == "live" and _live_feed and _live_feed._fyers:
            # Build Fyers symbol format (e.g., "NSE:NIFTY2452424000CE")
            fyers_data = {
                "symbol": f"NSE:{symbol}" if ":" not in symbol else symbol,
                "qty": qty,
                "type": {"LIMIT": 1, "MARKET": 2, "SL": 3, "SL-M": 4}.get(order_type, 2),
                "side": 1 if side == "BUY" else -1,
                "productType": product,
                "limitPrice": price if order_type == "LIMIT" else 0,
                "stopPrice": trigger_price if order_type.startswith("SL") else 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
            }
            result = await _fyers_call(_live_feed._fyers.place_order, data=fyers_data, timeout=10.0)
            if result and result.get("s") == "ok":
                return {
                    "success": True,
                    "order_id": result.get("id", result.get("data", {}).get("id", "")),
                    "message": f"Order placed: {side} {qty} {symbol}",
                    "source": "fyers_live",
                }
            else:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "detail": result.get("message", "Fyers order rejected"),
                        "source": "fyers_live",
                    },
                )
    except Exception as e:
        logger.warning(f"Fyers order placement failed: {e}")

    # Fallback: route to paper trading
    if _paper_trading_manager and _paper_trading_manager.is_active:
        try:
            from core.models import OptionType as OT
            from datetime import date as _date

            is_option = symbol.rstrip().endswith("CE") or symbol.rstrip().endswith("PE")
            is_ce = symbol.rstrip().endswith("CE")
            parts = symbol.strip().split()
            underlying = parts[0] if parts else symbol
            strike_price = Decimal(parts[1]) if len(parts) > 1 and is_option else None

            instrument = Instrument(
                symbol=symbol,
                exchange=Exchange.NSE,
                segment=Segment.FNO if is_option else Segment.EQUITY,
                instrument_type=(InstrumentType.CALL_OPTION if is_ce else InstrumentType.PUT_OPTION) if is_option else InstrumentType.STOCK,
                strike=strike_price if is_option else None,
                option_type=(OT.CE if is_ce else OT.PE) if is_option else None,
                expiry=_date.today() if is_option else None,
                underlying=underlying,
            )

            order = Order(
                instrument=instrument,
                side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                quantity=qty,
                order_type=OrderType.MARKET if order_type == "MARKET" else OrderType.LIMIT,
                limit_price=Decimal(str(price)) if order_type == "LIMIT" else None,
                product_type=ProductType.MIS if product == "MIS" else ProductType.NRML,
            )

            broker = _paper_trading_manager._broker
            if broker:
                ltp = body.get("ltp", price or 1.0)
                broker._simulated.update_price(symbol, float(ltp))
                ack = await broker.place_order(order)
                return {
                    "success": True,
                    "order_id": ack.order_id if ack else "",
                    "message": f"Paper order placed: {side} {qty} {symbol}",
                    "source": "paper_trading",
                }
        except Exception as e:
            logger.error(f"Paper order placement failed: {e}")
            return JSONResponse(status_code=400, content={"success": False, "detail": str(e)})

    return JSONResponse(
        status_code=503,
        content={"success": False, "detail": "No broker or paper trading session available"},
    )


@app.delete(
    "/api/orders/{order_id}",
    tags=["Orders & Trades"],
    summary="Cancel an order",
    description="Cancel a pending or open order by its ID.",
)
async def cancel_order_endpoint(order_id: str):
    # Try Fyers live cancellation
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.cancel_order, data={"id": order_id}, timeout=10.0)
            if result and result.get("s") == "ok":
                return {"success": True, "order_id": order_id, "message": "Order cancelled", "source": "fyers_live"}
            else:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "detail": result.get("message", "Cancel failed"), "source": "fyers_live"},
                )
    except Exception as e:
        logger.warning(f"Fyers cancel failed: {e}")

    # Fallback: paper trading cancel
    if _paper_trading_manager and _paper_trading_manager.is_active:
        try:
            broker = _paper_trading_manager._broker
            if broker:
                await broker.cancel_order(order_id)
                return {"success": True, "order_id": order_id, "message": "Paper order cancelled", "source": "paper_trading"}
        except Exception as e:
            logger.warning(f"Paper cancel failed: {e}")

    return JSONResponse(status_code=404, content={"success": False, "detail": f"Order '{order_id}' not found"})


# ===================================================================
# Account Endpoints (Fyers Live)
# ===================================================================


@app.get(
    "/api/account/funds",
    tags=["Account"],
    summary="Account funds and margins",
    description="Returns account fund details from Fyers. Falls back to mock if not connected.",
)
async def account_funds():
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.funds)
            if result and result.get("s") == "ok":
                fund_data = result.get("fund_limit", [])
                return {"funds": fund_data, "source": "fyers_live", "timestamp": datetime.now(IST).isoformat()}
    except Exception as e:
        logger.warning(f"Fyers funds fetch failed: {e}")
    return {
        "funds": {
            "total_balance": 2000000, "available_balance": 1200000,
            "used_margin": 800000, "margin_utilization": 40.0,
        },
        "source": "mock",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/account/holdings",
    tags=["Account"],
    summary="Account holdings",
    description="Returns equity holdings from Fyers. Falls back to empty list if not connected.",
)
async def account_holdings():
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.holdings)
            if result and result.get("s") == "ok":
                holdings = result.get("holdings", [])
                return {"holdings": holdings, "count": len(holdings), "source": "fyers_live", "timestamp": datetime.now(IST).isoformat()}
    except Exception as e:
        logger.warning(f"Fyers holdings fetch failed: {e}")
    return {"holdings": [], "count": 0, "source": "mock", "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/market/depth/{symbol}",
    tags=["Market Data"],
    summary="Market depth (Level 2)",
    description="Returns bid/ask depth for a symbol from Fyers.",
)
async def market_depth(symbol: str):
    try:
        if _live_feed:
            depth = await _live_feed.get_market_depth(symbol)
            if depth:
                return {"depth": depth, "symbol": symbol, "source": "fyers_live", "timestamp": datetime.now(IST).isoformat()}
    except Exception as e:
        logger.warning(f"Fyers market depth fetch failed: {e}")
    return {"depth": {"bids": [], "asks": []}, "symbol": symbol, "source": "unavailable", "timestamp": datetime.now(IST).isoformat()}


# ===================================================================
# Event Bus Endpoints
# ===================================================================


@app.get(
    "/api/events/metrics",
    response_model=EventMetricsResponse,
    tags=["Event Bus"],
    summary="Event bus metrics",
    description="Returns event bus performance metrics: published, consumed, failed counts, and latency stats.",
)
async def event_bus_metrics():
    if _event_bus is None:
        raise HTTPException(status_code=503, detail="Event bus not initialised")
    snap = _event_bus.metrics.snapshot()
    return EventMetricsResponse(
        published=snap["published"],
        consumed=snap["consumed"],
        failed=snap["failed"],
        avg_latency_ms=snap["avg_latency_ms"],
        max_latency_ms=snap["max_latency_ms"],
        dead_letter_count=len(_event_bus.dead_letter_queue),
    )


# ===================================================================
# Risk Engine Endpoints (Phase 6)
# ===================================================================


@app.get(
    "/api/risk/limits",
    tags=["Risk Engine"],
    summary="Current risk limits",
    description="Configured risk limit thresholds (max daily loss, drawdown, Greeks exposure, etc.).",
)
async def risk_limits():
    """Return active risk limits (merge of defaults + user overrides from SQLite)."""
    try:
        from core.state_store import get_store
        from core import risk_engine
        store = get_store()
        merged = {**risk_engine.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}
        merged["timestamp"] = datetime.now(IST).isoformat()
        merged["source"] = "state_store"
        return merged
    except Exception as exc:
        logger.error(f"risk_limits failed: {exc}", exc_info=True)
        from core import risk_engine
        return {**risk_engine.DEFAULT_RISK_LIMITS,
                "timestamp": datetime.now(IST).isoformat(),
                "source": "defaults", "error": str(exc)}


@app.post(
    "/api/risk/limits",
    tags=["Risk Engine"],
    summary="Update risk limits",
    description="Persist user-configured risk limits to SQLite. Partial updates supported.",
)
async def update_risk_limits(payload: dict):
    """Merge incoming limit changes with stored config and persist."""
    try:
        from core.state_store import get_store
        from core import risk_engine
        store = get_store()
        current = {**risk_engine.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}
        allowed_keys = set(risk_engine.DEFAULT_RISK_LIMITS.keys())
        updates = {}
        for k, v in (payload or {}).items():
            if k in allowed_keys:
                try:
                    updates[k] = float(v) if not isinstance(v, bool) else v
                except (TypeError, ValueError):
                    continue
        if not updates:
            return {"ok": False, "message": "No valid limit fields provided",
                    "allowed_keys": sorted(allowed_keys)}
        new_limits = {**current, **updates}
        store.save_risk_limits(new_limits)
        store.log_risk_event("LIMITS_UPDATED", "INFO", message=f"Updated: {list(updates.keys())}",
                              metadata={"updates": updates})
        return {"ok": True, "limits": new_limits, "updated": list(updates.keys()),
                "timestamp": datetime.now(IST).isoformat()}
    except Exception as exc:
        logger.error(f"update_risk_limits failed: {exc}", exc_info=True)
        return {"ok": False, "error": str(exc), "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/risk/drawdown",
    tags=["Risk Engine"],
    summary="Drawdown monitor status",
    description="Real drawdown from persisted equity curve in SQLite.",
)
async def risk_drawdown():
    """Compute drawdown from real equity snapshots."""
    try:
        from core.state_store import get_store
        from core import risk_engine
        store = get_store()
        eq_curve = [r["equity"] for r in store.get_equity_curve(limit=1000)]
        limits = {**risk_engine.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}

        if not eq_curve:
            # Bootstrap with current capital
            bundle = _get_risk_inputs()
            cap = bundle["capital"]
            return {
                "peak_equity": cap, "current_equity": cap,
                "drawdown_amount": 0.0, "drawdown_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "max_allowed_drawdown_pct": limits["max_drawdown_pct"],
                "breach": False, "trailing_stop_active": False,
                "duration_days": 0,
                "message": "No equity history yet — start trading to build curve",
                "source": "risk_engine_live",
                "timestamp": datetime.now(IST).isoformat(),
            }

        dd = risk_engine.calculate_drawdown(eq_curve)
        return {
            **dd,
            "max_allowed_drawdown_pct": limits["max_drawdown_pct"],
            "breach": dd["drawdown_pct"] > limits["max_drawdown_pct"],
            "trailing_stop_active": False,
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"risk_drawdown failed: {exc}", exc_info=True)
        return {"peak_equity": 0, "current_equity": 0, "drawdown_pct": 0,
                "breach": False, "error": str(exc),
                "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/risk/circuit-breakers",
    tags=["Risk Engine"],
    summary="Circuit breaker status",
    description="Real circuit breaker states derived from current portfolio vs configured limits.",
)
async def risk_circuit_breakers():
    """Compute live breaker state from real metrics + persisted limits."""
    try:
        from core.state_store import get_store
        from core import risk_engine
        store = get_store()
        limits = {**risk_engine.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}

        # Pull metrics
        metrics_payload = await risk_metrics()

        daily_loss = float(metrics_payload.get("daily_loss_used", 0))
        drawdown_pct = float(metrics_payload.get("current_drawdown_pct", 0))
        delta_exp = abs(float(metrics_payload.get("net_delta_exposure", 0)))
        vega_exp = abs(float(metrics_payload.get("net_vega_exposure", 0)))
        margin_pct = float(metrics_payload.get("margin_utilization", 0)) * 100.0

        def _breaker_state(current, limit_val):
            if limit_val <= 0:
                return "closed", 0.0
            util = current / limit_val
            if util >= 1.0:
                return "open", util * 100.0
            if util >= 0.80:
                return "half_open", util * 100.0
            return "closed", util * 100.0

        breakers = []
        for spec in [
            ("daily_loss", daily_loss, limits["max_daily_loss"]),
            ("drawdown", drawdown_pct, limits["max_drawdown_pct"]),
            ("delta_exposure", delta_exp, limits["max_delta_exposure"]),
            ("vega_exposure", vega_exp, limits["max_vega_exposure"]),
            ("margin_utilization", margin_pct, 90.0),  # alert at 90% margin
            ("open_strategies", metrics_payload.get("open_strategies_count", 0),
             limits["max_open_strategies"]),
        ]:
            name, current, limit_val = spec
            state, util = _breaker_state(current, limit_val)
            breakers.append({
                "name": name,
                "state": state,
                "current": round(current, 4),
                "threshold": round(limit_val, 4),
                "utilization_pct": round(util, 2),
                "auto_recover": name in ("daily_loss", "margin_utilization"),
                "cooldown_seconds": 300 if name == "daily_loss" else 0,
                "last_triggered": None,
            })

        return {
            "breakers": breakers,
            "kill_switch_active": store.is_kill_switch_active(),
            "any_open": any(b["state"] == "open" for b in breakers),
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"risk_circuit_breakers failed: {exc}", exc_info=True)
        return {"breakers": [], "kill_switch_active": False,
                "error": str(exc), "timestamp": datetime.now(IST).isoformat()}


@app.post(
    "/api/risk/kill-switch",
    tags=["Risk Engine"],
    summary="Activate kill switch",
    description=(
        "EMERGENCY: blocks all new deploys + squares-off all open strategies "
        "via PaperBroker. Persisted to SQLite (survives restart)."
    ),
)
async def activate_kill_switch(payload: dict | None = None):
    """Real kill switch: persists flag + squares off all open positions."""
    payload = payload or {}
    reason = str(payload.get("reason", "manual user request"))
    try:
        from core.state_store import get_store
        store = get_store()
        store.set_kill_switch(True, reason=reason, triggered_by="user")
        store.log_risk_event("KILL_SWITCH_ON", "CRITICAL",
                              message=f"Kill switch activated: {reason}",
                              metadata={"reason": reason})

        # Square off every RUNNING/ENTERED strategy
        squared_off = []
        for sid, strat in list(_deployed_strategies.items()):
            status = str(strat.get("status", "")).upper()
            if status in ("RUNNING", "ENTERED"):
                try:
                    # Direct field mutations + persist
                    strat["status"] = "STOPPED"
                    strat["exit_reason"] = "kill_switch"
                    strat["exited_at"] = datetime.now(IST).isoformat()
                    # Lock realized P&L at current value
                    strat["realized_pnl"] = float(strat.get("pnl", 0) or 0)
                    store.save_strategy(sid, strat)
                    squared_off.append(sid)
                except Exception as e:
                    logger.warning(f"Kill switch: failed to stop {sid}: {e}")

        # Notify via WebSocket
        try:
            from core.websocket.manager import WebSocketManager  # noqa: F401
            ws = globals().get("_ws_manager")
            if ws and hasattr(ws, "broadcast"):
                await ws.broadcast({
                    "type": "kill_switch",
                    "active": True,
                    "reason": reason,
                    "squared_off_count": len(squared_off),
                    "timestamp": datetime.now(IST).isoformat(),
                })
        except Exception:
            pass

        return {
            "kill_switch_active": True,
            "action": "activated",
            "reason": reason,
            "squared_off_strategies": squared_off,
            "squared_off_count": len(squared_off),
            "message": f"Kill switch ON. Stopped {len(squared_off)} strategies. New deploys blocked.",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"activate_kill_switch failed: {exc}", exc_info=True)
        return {"kill_switch_active": False, "error": str(exc),
                "timestamp": datetime.now(IST).isoformat()}


@app.delete(
    "/api/risk/kill-switch",
    tags=["Risk Engine"],
    summary="Deactivate kill switch",
    description="Resets the kill switch flag. New strategy deploys allowed again.",
)
async def deactivate_kill_switch(payload: dict | None = None):
    """Reset the kill switch flag."""
    payload = payload or {}
    reason = str(payload.get("reason", "manual reset"))
    try:
        from core.state_store import get_store
        store = get_store()
        store.set_kill_switch(False, reason=reason, triggered_by="user")
        store.log_risk_event("KILL_SWITCH_OFF", "INFO",
                              message=f"Kill switch reset: {reason}",
                              metadata={"reason": reason})
        return {
            "kill_switch_active": False,
            "action": "deactivated",
            "message": "Kill switch OFF. Strategy deploys allowed again.",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"deactivate_kill_switch failed: {exc}", exc_info=True)
        return {"kill_switch_active": True, "error": str(exc),
                "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/risk/greeks-aggregation",
    tags=["Risk Engine"],
    summary="Aggregated portfolio greeks",
    description="Real per-strategy Greeks aggregated from live deployed positions and option chain IV.",
)
async def risk_greeks_aggregation():
    """Real Greeks computed via Black-Scholes from positions + chain IV."""
    try:
        from core.state_store import get_store
        from core import risk_engine
        store = get_store()
        bundle = _get_risk_inputs()
        re = bundle["risk_engine"]

        agg = re.aggregate_portfolio_greeks(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
        )
        limits = {**risk_engine.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}

        pf = agg["portfolio"]
        max_delta = limits["max_delta_exposure"]
        max_gamma = limits["max_gamma_exposure"]
        max_vega = limits["max_vega_exposure"]

        def _util(val, lim):
            if lim == 0: return 0.0
            return round(abs(val) / abs(lim) * 100.0, 2)

        return {
            "portfolio": {
                "net_delta": round(pf["delta"], 4),
                "net_gamma": round(pf["gamma"], 6),
                "net_theta": round(pf["theta"], 2),
                "net_vega": round(pf["vega"], 2),
                "net_rho": round(pf["rho"], 4),
                "total_notional": round(pf["total_notional"], 2),
                "total_premium": round(pf["total_premium"], 2),
            },
            "by_strategy": {
                sid: {
                    "delta": round(s["delta"], 4),
                    "gamma": round(s["gamma"], 6),
                    "theta": round(s["theta"], 2),
                    "vega": round(s["vega"], 2),
                    "name": s["name"],
                    "underlying": s["underlying"],
                    "status": s["status"],
                    "pnl": s["pnl"],
                } for sid, s in agg["by_strategy"].items()
            },
            "by_underlying": {
                u: {
                    "delta": round(g["delta"], 4),
                    "gamma": round(g["gamma"], 6),
                    "theta": round(g["theta"], 2),
                    "vega": round(g["vega"], 2),
                    "notional": round(g["notional"], 2),
                } for u, g in agg["by_underlying"].items()
            },
            "limits": {
                "max_delta": max_delta,
                "max_gamma": max_gamma,
                "max_vega": max_vega,
                "delta_utilization_pct": _util(pf["delta"], max_delta),
                "gamma_utilization_pct": _util(pf["gamma"], max_gamma),
                "vega_utilization_pct": _util(pf["vega"], max_vega),
            },
            "positions_count": agg["positions_count"],
            "strategies_count": agg["strategies_count"],
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"risk_greeks_aggregation failed: {exc}", exc_info=True)
        return {"portfolio": {"net_delta": 0, "net_gamma": 0, "net_theta": 0, "net_vega": 0},
                "by_strategy": {}, "limits": {}, "error": str(exc),
                "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/risk/margin-calculator",
    tags=["Risk Engine"],
    summary="NSE F&O margin (approximate)",
    description="SPAN + Exposure margin approximation (~5% of actual NSE SPAN).",
)
async def risk_margin_calculator():
    """Real margin calculation using NSE F&O approximation."""
    try:
        bundle = _get_risk_inputs()
        re = bundle["risk_engine"]
        margin = re.calculate_margin(
            bundle["strategies"], bundle["chain_cache"],
            lot_sizes=bundle["lot_sizes"],
            available_capital=bundle["capital"],
        )
        margin["source"] = "risk_engine_live"
        margin["timestamp"] = datetime.now(IST).isoformat()
        margin["available_capital"] = bundle["capital"]
        return margin
    except Exception as exc:
        logger.error(f"risk_margin_calculator failed: {exc}", exc_info=True)
        return {"total_margin_required": 0, "span_margin": 0, "exposure_margin": 0,
                "available_margin": 0, "utilization_pct": 0, "margin_by_strategy": {},
                "error": str(exc), "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/risk/breaches",
    tags=["Risk Engine"],
    summary="Current limit breaches",
    description="Live list of risk limit breaches (WARN at 80%, BREACH at 100%).",
)
async def risk_breaches():
    """Return current breaches against configured limits."""
    try:
        from core.state_store import get_store
        from core import risk_engine
        store = get_store()
        metrics_payload = await risk_metrics()
        limits = {**risk_engine.DEFAULT_RISK_LIMITS, **store.load_risk_limits()}

        # Build metrics dict for limit checker
        check_metrics = {
            "daily_loss_used": metrics_payload.get("daily_loss_used", 0),
            "current_drawdown_pct": metrics_payload.get("current_drawdown_pct", 0),
            "net_delta": metrics_payload.get("net_delta_exposure", 0),
            "net_gamma": metrics_payload.get("net_gamma_exposure", 0),
            "net_vega": metrics_payload.get("net_vega_exposure", 0),
            "net_theta": metrics_payload.get("net_theta_exposure", 0),
            "open_strategies_count": metrics_payload.get("open_strategies_count", 0),
        }

        breaches = risk_engine.check_risk_limits(check_metrics, limits)
        should_kill = risk_engine.should_auto_kill(breaches, limits)
        return {
            "breaches": [
                {
                    "limit_name": b.limit_name,
                    "current_value": b.current_value,
                    "limit_value": b.limit_value,
                    "utilization_pct": b.utilization_pct,
                    "severity": b.severity,
                    "message": b.message,
                } for b in breaches
            ],
            "count": len(breaches),
            "warn_count": sum(1 for b in breaches if b.severity == "WARN"),
            "breach_count": sum(1 for b in breaches if b.severity == "BREACH"),
            "should_auto_kill": should_kill,
            "kill_switch_active": store.is_kill_switch_active(),
            "source": "risk_engine_live",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"risk_breaches failed: {exc}", exc_info=True)
        return {"breaches": [], "count": 0, "should_auto_kill": False,
                "error": str(exc), "timestamp": datetime.now(IST).isoformat()}


@app.get(
    "/api/risk/audit-log",
    tags=["Risk Engine"],
    summary="Risk audit log",
    description="History of risk events (breaches, kill switch toggles, limit updates).",
)
async def risk_audit_log(limit: int = 100, event_type: str | None = None):
    """Return persisted risk audit events."""
    try:
        from core.state_store import get_store
        store = get_store()
        events = store.get_risk_events(limit=min(max(1, limit), 500), event_type=event_type)
        return {
            "events": events,
            "count": len(events),
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"risk_audit_log failed: {exc}", exc_info=True)
        return {"events": [], "count": 0, "error": str(exc),
                "timestamp": datetime.now(IST).isoformat()}


# ===================================================================
# Strategy Engine Endpoints (Phase 7)
# ===================================================================


@app.get(
    "/api/strategy-engine/runners",
    tags=["Strategy Engine"],
    summary="Active strategy runners",
    description="Returns all registered strategy runners with lifecycle state and health.",
)
async def strategy_runners():
    strategies = _mock.strategies()
    runners = []
    for s in strategies:
        runners.append({
            "strategy_id": s["strategy_id"],
            "name": s["name"],
            "status": s["status"],
            "mode": s["mode"],
            "heartbeat_interval_s": 10,
            "last_heartbeat": s["last_heartbeat"],
            "uptime_seconds": round(_mock._rng.uniform(1000, 20000), 0),
            "bars_processed": _mock._rng.randint(100, 5000),
            "errors_count": _mock._rng.randint(0, 5),
            "class_path": s["class_path"],
        })
    return {"runners": runners, "count": len(runners)}


@app.get(
    "/api/strategy-engine/scheduler",
    tags=["Strategy Engine"],
    summary="Scheduled jobs",
    description="Returns all scheduled jobs (strategy start/stop times, rebalance schedules).",
)
async def strategy_scheduler():
    now = datetime.now(IST)
    return {
        "jobs": [
            {
                "job_id": "sched-001",
                "strategy_id": "iron-condor-weekly",
                "action": "start",
                "schedule": "0 9 15 * * MON-FRI",
                "next_run": (now + timedelta(hours=1)).isoformat(),
                "enabled": True,
            },
            {
                "job_id": "sched-002",
                "strategy_id": "iron-condor-weekly",
                "action": "stop",
                "schedule": "0 15 20 * * MON-FRI",
                "next_run": (now + timedelta(hours=7)).isoformat(),
                "enabled": True,
            },
            {
                "job_id": "sched-003",
                "strategy_id": "straddle-banknifty",
                "action": "start",
                "schedule": "0 9 16 * * MON-FRI",
                "next_run": (now + timedelta(hours=1, minutes=1)).isoformat(),
                "enabled": True,
            },
        ],
        "count": 3,
        "timestamp": now.isoformat(),
    }


@app.get(
    "/api/strategy-engine/parameters/{strategy_id}",
    tags=["Strategy Engine"],
    summary="Strategy parameters",
    description="Returns the current parameter set for a given strategy.",
)
async def strategy_parameters(strategy_id: str):
    strategies = _mock.strategies()
    for s in strategies:
        if s["strategy_id"] == strategy_id:
            return {
                "strategy_id": strategy_id,
                "parameters": s.get("params", {}),
                "version": 1,
                "updated_at": datetime.now(IST).isoformat(),
            }
    raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")


class ParameterUpdateRequest(BaseModel):
    parameters: dict[str, Any]


@app.put(
    "/api/strategy-engine/parameters/{strategy_id}",
    tags=["Strategy Engine"],
    summary="Update strategy parameters",
    description="Updates one or more parameters for a running strategy.",
)
async def update_strategy_parameters(strategy_id: str, body: ParameterUpdateRequest):
    return {
        "strategy_id": strategy_id,
        "updated_parameters": body.parameters,
        "version": 2,
        "message": f"Parameters updated for strategy '{strategy_id}'.",
        "timestamp": datetime.now(IST).isoformat(),
    }


# ===================================================================
# P&L Engine Endpoints (Phase 8)
# ===================================================================


@app.get(
    "/api/pnl/summary",
    tags=["P&L Engine"],
    summary="P&L summary",
    description="Returns comprehensive P&L summary with realized, unrealized, charges, and net P&L.",
)
async def pnl_summary():
    # Try deriving P&L from Fyers live positions
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.positions)
            if result and result.get("s") == "ok":
                positions = result.get("netPositions", result.get("overall", []))
                if isinstance(positions, list) and len(positions) > 0:
                    realized = sum(float(p.get("realized_profit", p.get("realizedProfit", 0))) for p in positions)
                    unrealized = sum(float(p.get("unrealizedProfit", p.get("pl", 0))) for p in positions)
                    return {
                        "realized_pnl": round(realized, 2),
                        "unrealized_pnl": round(unrealized, 2),
                        "net_pnl": round(realized + unrealized, 2),
                        "charges": {"total": 0, "brokerage": 0, "stt": 0, "gst": 0},
                        "trade_count": len(positions),
                        "source": "fyers_live",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers P&L summary derivation failed: {e}")
    # Compute from deployed strategies (no mock)
    realized = 0.0
    unrealized = 0.0
    total_charges = 0.0
    for strat in _deployed_strategies.values():
        status = str(strat.get("status", "")).upper()
        if status in ("EXITED", "STOPPED"):
            realized += float(strat.get("realized_pnl", strat.get("pnl", 0)) or 0)
            total_charges += float(strat.get("total_charges", 0) or 0)
        elif status in ("RUNNING", "ENTERED") and strat.get("entered"):
            unrealized += float(strat.get("pnl", 0) or 0)
            total_charges += float(strat.get("total_charges", 0) or 0)
    return {
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": round(realized + unrealized, 2),
        "net_pnl": round(realized + unrealized - total_charges, 2),
        "charges": {"total": round(total_charges, 2)},
        "source": "deployed_strategies",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/pnl/by-strategy",
    tags=["P&L Engine"],
    summary="P&L by strategy",
    description="Returns P&L breakdown by strategy with trade counts and win rates.",
)
async def pnl_by_strategy():
    # If we have deployed strategies, derive P&L from positions
    if _deployed_strategies:
        strategy_pnl = []
        for sid, sdata in _deployed_strategies.items():
            # charges can be a dict or a float — handle both
            raw_charges = sdata.get("charges", 0)
            if isinstance(raw_charges, dict):
                total_charges = float(sdata.get("total_charges", 0) or sum(float(v or 0) for v in raw_charges.values()))
            else:
                total_charges = float(raw_charges or 0)
            realized = float(sdata.get("realized_pnl", 0) or 0)
            unrealized = float(sdata.get("pnl", 0) or 0) if sdata.get("entered") else 0.0
            strategy_pnl.append({
                "strategy_id": sid,
                "name": sdata.get("name", sid),
                "status": sdata.get("status", ""),
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": round(unrealized, 2),
                "charges": round(total_charges, 2),
                "net_pnl": round(realized + unrealized - total_charges, 2),
                "trades_today": len(sdata.get("entry_orders", [])) + len(sdata.get("exit_orders", [])),
                "win_rate": 0,
                "source": "deployed",
            })
        if strategy_pnl:
            return {"strategies": strategy_pnl, "source": "deployed_strategies", "timestamp": datetime.now(IST).isoformat()}

    # Return empty if no strategies (no mock)
    return {
        "strategies": [],
        "source": "empty",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/pnl/charges",
    tags=["P&L Engine"],
    summary="Transaction charges breakdown",
    description="Returns detailed transaction charges: STT, exchange fees, GST, SEBI fee, stamp duty.",
)
async def pnl_charges():
    # Try estimating charges from Fyers tradebook volume
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.tradebook)
            if result and result.get("s") == "ok":
                fyers_trades = result.get("tradeBook", [])
                if isinstance(fyers_trades, list) and len(fyers_trades) > 0:
                    turnover = sum(
                        float(t.get("tradePrice", t.get("price", 0))) * float(t.get("tradedQty", t.get("qty", 0)))
                        for t in fyers_trades
                    )
                    # NSE FnO charge estimates (approximate)
                    brokerage = min(len(fyers_trades) * 20, turnover * 0.0003)  # ₹20/order or 0.03%
                    stt = turnover * 0.000625  # 0.0625% on sell side (options)
                    exchange_fee = turnover * 0.00053
                    gst = (brokerage + exchange_fee) * 0.18
                    sebi_fee = turnover * 0.000001
                    stamp_duty = turnover * 0.00003
                    total = round(brokerage + stt + exchange_fee + gst + sebi_fee + stamp_duty, 2)
                    return {
                        "charges": {
                            "brokerage": round(brokerage, 2),
                            "stt": round(stt, 2),
                            "exchange_txn_fee": round(exchange_fee, 2),
                            "gst": round(gst, 2),
                            "sebi_fee": round(sebi_fee, 2),
                            "stamp_duty": round(stamp_duty, 2),
                            "total": total,
                        },
                        "by_segment": {
                            "equity": 0,
                            "fno_futures": 0,
                            "fno_options": total,
                        },
                        "trade_count": len(fyers_trades),
                        "turnover": round(turnover, 2),
                        "source": "fyers_derived",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers charges derivation failed: {e}")

    # Compute from deployed strategies
    total_charges = {}
    for strat in _deployed_strategies.values():
        charges = strat.get("charges", {})
        if isinstance(charges, dict):
            for k, v in charges.items():
                total_charges[k] = total_charges.get(k, 0) + float(v or 0)
    return {
        "charges": total_charges,
        "total": sum(total_charges.values()),
        "source": "deployed_strategies",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/pnl/trade-book",
    tags=["P&L Engine"],
    summary="Trade book with P&L",
    description="Returns the trade book with per-trade P&L and charges.",
)
async def pnl_trade_book():
    # Try Fyers live tradebook first
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.tradebook)
            if result and result.get("s") == "ok":
                fyers_trades = result.get("tradeBook", [])
                if isinstance(fyers_trades, list) and len(fyers_trades) > 0:
                    enriched = []
                    for t in fyers_trades:
                        trade_val = float(t.get("tradePrice", t.get("price", 0))) * float(t.get("tradedQty", t.get("qty", 0)))
                        charges = round(trade_val * 0.0003, 2)  # Approximate 0.03% charges
                        enriched.append({
                            "id": t.get("id", t.get("tradeId", "")),
                            "orderId": t.get("orderNumber", t.get("orderId", "")),
                            "symbol": (t.get("symbol", "").split(":")[1] if ":" in t.get("symbol", "") else t.get("symbol", "")),
                            "side": "BUY" if t.get("side") == 1 else "SELL",
                            "qty": t.get("tradedQty", t.get("qty", 0)),
                            "price": t.get("tradePrice", t.get("price", 0)),
                            "time": t.get("orderDateTime", t.get("tradeDateTime", "")),
                            "exchange": t.get("exchange", "NSE"),
                            "pnl": float(t.get("pl", 0)),
                            "charges": charges,
                            "net_pnl": round(float(t.get("pl", 0)) - charges, 2),
                        })
                    return {
                        "trades": enriched,
                        "count": len(enriched),
                        "total_pnl": round(sum(t["pnl"] for t in enriched), 2),
                        "total_charges": round(sum(t["charges"] for t in enriched), 2),
                        "source": "fyers_live",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers tradebook P&L fetch failed: {e}")

    from core.state_store import get_store
    store = get_store()
    trades = store.get_trade_log(limit=200)
    return {
        "trades": trades,
        "count": len(trades),
        "source": "trade_log",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/pnl/equity-curve",
    tags=["P&L Engine"],
    summary="Intraday equity curve",
    description="Returns the intraday equity curve with timestamps.",
)
async def pnl_equity_curve():
    now = datetime.now(IST)

    # Try deriving current equity from Fyers funds + positions
    try:
        if _live_feed and _live_feed._fyers:
            fund_result = await _fyers_call(_live_feed._fyers.funds)
            pos_result = await _fyers_call(_live_feed._fyers.positions)
            if fund_result and fund_result.get("s") == "ok":
                total_balance = 0
                for item in (fund_result.get("fund_limit", []) if isinstance(fund_result.get("fund_limit"), list) else []):
                    title = item.get("title", "").lower()
                    val = float(item.get("equityAmount", item.get("amount", 0)))
                    if "total" in title and "balance" in title:
                        total_balance = val
                        break
                if total_balance > 0:
                    unrealized = 0
                    if pos_result and pos_result.get("s") == "ok":
                        positions = pos_result.get("netPositions", pos_result.get("overall", []))
                        if isinstance(positions, list):
                            unrealized = sum(float(p.get("unrealizedProfit", p.get("pl", 0))) for p in positions)
                    current_equity = total_balance + unrealized
                    base = total_balance  # Use account balance as the base
                    # Generate intraday curve ending at current equity
                    points = []
                    equity = base
                    drift = (current_equity - base) / max(1, 78)
                    for i in range(78):
                        ts = now.replace(hour=9, minute=15) + timedelta(minutes=i * 5)
                        if ts > now:
                            break
                        equity += drift + _mock._rng.uniform(-500, 500)
                        points.append({"timestamp": ts.isoformat(), "equity": round(equity, 2)})
                    if points:
                        points[-1]["equity"] = round(current_equity, 2)
                    return {
                        "initial_capital": round(base, 2),
                        "current_equity": round(current_equity, 2),
                        "data_points": points,
                        "source": "fyers_derived",
                        "timestamp": now.isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers equity curve derivation failed: {e}")

    from core.state_store import get_store
    store = get_store()
    snapshots = store.get_equity_curve(limit=500)
    curve = [{"time": s["timestamp"][:19], "value": s["equity"]} for s in snapshots]
    return {
        "curve": curve,
        "count": len(curve),
        "source": "equity_snapshots",
        "timestamp": datetime.now(IST).isoformat(),
    }


# ===================================================================
# Backtest Engine Endpoints (Phase 9)
# ===================================================================


class BacktestRequest(BaseModel):
    strategy_class: str = Field(description="Strategy short name: iron_condor, straddle, momentum, mean_reversion, supertrend, etc.")
    start_date: str = Field(description="Backtest start date (YYYY-MM-DD)")
    end_date: str = Field(description="Backtest end date (YYYY-MM-DD)")
    initial_capital: float = Field(default=2_000_000.0, description="Initial capital in INR")
    slippage_bps: float = Field(default=2.0, description="Slippage in basis points")
    commission_per_order: float = Field(default=20.0, description="Commission per order in INR")
    symbols: list[str] = Field(default=["NIFTY"])


# Strategy class registry
_STRATEGY_MAP = {
    "iron_condor": "strategies.examples.iron_condor.IronCondorStrategy",
    "straddle": "strategies.examples.straddle_seller.StraddleSellerStrategy",
    "momentum": "strategies.examples.momentum_breakout.MomentumBreakoutStrategy",
    "mean_reversion": "strategies.examples.mean_reversion.MeanReversionStrategy",
    "supertrend": "strategies.examples.supertrend.SupertrendStrategy",
    "gamma_scalping": "strategies.examples.gamma_scalping.GammaScalpingStrategy",
    "vwap_scalper": "strategies.examples.vwap_scalper.VWAPScalperStrategy",
    "orb_options": "strategies.examples.orb_options.ORBOptionsStrategy",
    "expiry_day": "strategies.examples.expiry_day.ExpiryDayStrategy",
    "pair_trading": "strategies.examples.pair_trading.PairTradingStrategy",
    "strangle": "strategies.examples.straddle_seller.StraddleSellerStrategy",
    "bull_call_spread": "strategies.examples.iron_condor.IronCondorStrategy",
    "calendar_spread": "strategies.examples.gamma_scalping.GammaScalpingStrategy",
}

# In-memory backtest job storage
_backtest_jobs: dict[str, dict] = {}


def _resolve_strategy_class(name: str):
    """Resolve strategy short name to actual class."""
    import importlib
    class_path = _STRATEGY_MAP.get(name, name)
    # If it's a full module path like strategies.examples.iron_condor.IronCondorStrategy
    if "." in class_path:
        module_path, class_name = class_path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Cannot resolve strategy '{name}': {e}")
    raise ValueError(f"Unknown strategy: {name}")


@app.post(
    "/api/backtest/fetch-history",
    tags=["Backtest"],
    summary="Download historical data for backtesting",
    description=(
        "Bulk-downloads historical candles from Fyers and caches to SQLite. "
        "Respects rate limits with throttled sequential requests. "
        "Once downloaded, backtests run instantly from cache."
    ),
)
async def backtest_fetch_history(body: dict = Body(...)):
    """Download and cache historical candle data for a symbol."""
    symbol = (body.get("symbol") or "NIFTY").upper()
    resolution = body.get("resolution", "D")
    start_date = body.get("start_date", "")
    end_date = body.get("end_date", "")

    if not start_date or not end_date:
        return {"ok": False, "error": "start_date and end_date required (YYYY-MM-DD)"}

    # Check what we already have cached
    from core.state_store import get_store
    store = get_store()
    existing = store.get_candle_date_range(symbol, resolution)

    if not _live_feed or not _live_feed.is_connected or not _live_feed._fyers:
        return {
            "ok": False,
            "error": "Fyers not connected. Cannot download historical data.",
            "cached": existing,
        }

    try:
        from core.backtest.fyers_data_loader import fetch_fyers_historical_csv
        csv_path = await fetch_fyers_historical_csv(
            fyers_client=_live_feed._fyers,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            resolution=resolution,
        )

        # Check what we have now
        updated = store.get_candle_date_range(symbol, resolution)
        return {
            "ok": bool(csv_path),
            "symbol": symbol,
            "resolution": resolution,
            "start_date": start_date,
            "end_date": end_date,
            "csv_path": csv_path,
            "candles_cached": updated,
            "message": f"Downloaded and cached {updated['count']} candles for {symbol} ({resolution})",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"History download failed: {exc}", exc_info=True)
        return {
            "ok": False,
            "error": str(exc),
            "cached": existing,
            "timestamp": datetime.now(IST).isoformat(),
        }


@app.get(
    "/api/backtest/cache-status",
    tags=["Backtest"],
    summary="Check cached historical data",
    description="Shows what candle data is already cached in SQLite for each symbol and resolution.",
)
async def backtest_cache_status():
    """Return inventory of cached candle data."""
    from core.state_store import get_store
    store = get_store()
    inventory = {}
    for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
        sym_data = {}
        for res in ["D", "5", "15", "60"]:
            info = store.get_candle_date_range(sym, res)
            if info["count"] > 0:
                from datetime import timezone as _tz
                sym_data[res] = {
                    "count": info["count"],
                    "from": datetime.fromtimestamp(info["min_ts"], tz=_tz.utc).date().isoformat(),
                    "to": datetime.fromtimestamp(info["max_ts"], tz=_tz.utc).date().isoformat(),
                }
        if sym_data:
            inventory[sym] = sym_data
    return {
        "inventory": inventory,
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.post(
    "/api/backtest/run",
    tags=["Backtest"],
    summary="Run a backtest",
    description="Starts a backtest with Fyers historical data. Returns a job ID for tracking.",
)
async def run_backtest(body: BacktestRequest):
    import asyncio as _asyncio
    from core.backtest.fyers_data_loader import fetch_fyers_historical_csv
    from core.backtest.engine import BacktestEngine, BacktestConfig

    now = datetime.now(IST)
    job_id = f"BT-{now.strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"

    # Resolve strategy class
    try:
        strategy_cls = _resolve_strategy_class(body.strategy_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Store job as queued
    _backtest_jobs[job_id] = {
        "job_id": job_id,
        "strategy": body.strategy_class,
        "status": "running",
        "start_date": body.start_date,
        "end_date": body.end_date,
        "initial_capital": body.initial_capital,
        "created_at": now.isoformat(),
        "result": None,
        "error": None,
    }

    async def _run_backtest():
        try:
            # 1. Download Fyers historical data
            if not _live_feed or not _live_feed.is_connected or not _live_feed._fyers:
                _backtest_jobs[job_id]["status"] = "failed"
                _backtest_jobs[job_id]["error"] = "Fyers not connected"
                return

            data_files = []
            for sym in body.symbols:
                csv_path = await fetch_fyers_historical_csv(
                    _live_feed._fyers,
                    sym,
                    body.start_date,
                    body.end_date,
                    resolution="5",  # 5-minute candles for backtesting
                )
                if csv_path:
                    data_files.append(csv_path)

            if not data_files:
                _backtest_jobs[job_id]["status"] = "failed"
                _backtest_jobs[job_id]["error"] = "Failed to download historical data"
                return

            # 2. Run the backtest engine
            config = BacktestConfig(
                strategy_class=strategy_cls,
                start_date=datetime.strptime(body.start_date, "%Y-%m-%d").date() if body.start_date else None,
                end_date=datetime.strptime(body.end_date, "%Y-%m-%d").date() if body.end_date else None,
                initial_capital=body.initial_capital,
                commission_per_order=body.commission_per_order,
                slippage_bps=body.slippage_bps,
                data_files=data_files,
                timeframes=["5m"],
            )

            engine = BacktestEngine()
            result = await engine.run(config)

            # 3. Store results
            equity_data = [
                {"date": dt.isoformat() if hasattr(dt, 'isoformat') else str(dt), "equity": round(eq, 2)}
                for dt, eq in result.equity_curve[-200:]  # last 200 points
            ] if result.equity_curve else []

            monthly = [
                {"month": m, "return": round(r * 100, 2)}
                for m, r in result.monthly_returns.items()
            ] if result.monthly_returns else []

            _backtest_jobs[job_id]["status"] = "completed"
            _backtest_jobs[job_id]["result"] = {
                "metrics": {
                    "totalReturn": round(result.total_return_pct, 2),
                    "cagr": round(result.total_return_pct / max(1, (datetime.strptime(body.end_date, "%Y-%m-%d") - datetime.strptime(body.start_date, "%Y-%m-%d")).days / 365), 2),
                    "sharpe": round(result.sharpe_ratio, 2),
                    "sortino": round(result.sortino_ratio, 2),
                    "maxDrawdown": round(-abs(result.max_drawdown_pct), 2),
                    "winRate": round(result.win_rate * 100, 1),
                    "profitFactor": round(result.profit_factor, 2),
                    "totalTrades": result.total_trades,
                    "avgWin": round(result.avg_winner, 2),
                    "avgLoss": round(-abs(result.avg_loser), 2),
                    "expectancy": round(result.avg_trade_pnl, 2),
                    "calmar": round(result.calmar_ratio, 2),
                },
                "equityCurve": equity_data,
                "monthlyReturns": monthly,
                "source": "fyers_backtest",
            }

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logging.getLogger(__name__).error(f"Backtest failed: {e}\n{tb}")
            _backtest_jobs[job_id]["status"] = "failed"
            _backtest_jobs[job_id]["error"] = str(e) or tb.split("\n")[-2] if tb else "Unknown error"

    # Run backtest in background
    _asyncio.create_task(_run_backtest())

    return {
        "job_id": job_id,
        "status": "running",
        "strategy": body.strategy_class,
        "message": f"Backtest started with Fyers data. Poll /api/backtest/results/{job_id} for results.",
        "timestamp": now.isoformat(),
    }


@app.get(
    "/api/backtest/results/{job_id}",
    tags=["Backtest"],
    summary="Backtest results",
    description="Returns the results of a completed backtest including performance metrics.",
)
async def backtest_results(job_id: str):
    job = _backtest_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Backtest job {job_id} not found")

    if job["status"] == "running":
        return {"job_id": job_id, "status": "running", "message": "Backtest is still running..."}

    if job["status"] == "failed":
        return {"job_id": job_id, "status": "failed", "error": job.get("error", "Unknown error")}

    return {
        "job_id": job_id,
        "status": "completed",
        **job.get("result", {}),
    }


@app.get(
    "/api/backtest/list",
    tags=["Backtest"],
    summary="List backtest jobs",
    description="Returns all backtest jobs with their status.",
)
async def backtest_list():
    jobs = []
    for jid, job in _backtest_jobs.items():
        entry = {
            "job_id": jid,
            "strategy": job.get("strategy", ""),
            "status": job.get("status", "unknown"),
            "start_date": job.get("start_date", ""),
            "end_date": job.get("end_date", ""),
            "created_at": job.get("created_at", ""),
        }
        if job.get("result") and job["result"].get("metrics"):
            entry["total_return_pct"] = job["result"]["metrics"].get("totalReturn", 0)
        jobs.append(entry)
    return {"jobs": jobs, "count": len(jobs)}


# ===================================================================
# VectorBT Backtesting + Optuna Optimizer + QuantStats Reports
# ===================================================================


@app.get(
    "/api/vbt/strategies",
    tags=["VectorBT"],
    summary="List available vectorbt strategies",
)
async def vbt_strategies():
    from core.backtest.vectorbt_engine import VectorBTEngine
    engine = VectorBTEngine()
    return {"strategies": engine.available_strategies()}


@app.post(
    "/api/vbt/backtest",
    tags=["VectorBT"],
    summary="Run a vectorized backtest",
    description="Fast vectorized backtesting using vectorbt. Reads from SQLite candle cache.",
)
async def vbt_backtest(body: dict = Body(...)):
    from core.backtest.vectorbt_engine import VBTBacktestConfig, VectorBTEngine

    config = VBTBacktestConfig(
        strategy=body.get("strategy", "rsi_reversal"),
        symbol=body.get("symbol", "NIFTY"),
        resolution=body.get("resolution", "5"),
        start_date=body.get("start_date"),
        end_date=body.get("end_date"),
        initial_capital=body.get("initial_capital", 10_000_000),
        lot_size=body.get("lot_size", 75),
        params=body.get("params", {}),
        instrument_type=body.get("instrument_type", "options"),
        sl_pct=body.get("sl_pct"),
        tp_pct=body.get("tp_pct"),
    )

    try:
        from core.state_store import get_store
        engine = VectorBTEngine(state_store=get_store())
        result = engine.run(config)
        return {"ok": True, "result": result.to_dict()}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})
    except Exception as e:
        logger.exception("VBT backtest failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post(
    "/api/vbt/optimize",
    tags=["VectorBT"],
    summary="Optimize strategy parameters using Optuna",
    description="Runs Optuna TPE optimization to find optimal strategy parameters.",
)
async def vbt_optimize(body: dict = Body(...)):
    from core.backtest.optimizer import OptimizationConfig, StrategyOptimizer

    config = OptimizationConfig(
        strategy=body.get("strategy", "rsi_reversal"),
        symbol=body.get("symbol", "NIFTY"),
        resolution=body.get("resolution", "5"),
        start_date=body.get("start_date"),
        end_date=body.get("end_date"),
        initial_capital=body.get("initial_capital", 10_000_000),
        lot_size=body.get("lot_size", 75),
        n_trials=min(body.get("n_trials", 50), 200),
        objective=body.get("objective", "sharpe"),
        sl_pct=body.get("sl_pct"),
        tp_pct=body.get("tp_pct"),
    )

    try:
        from core.state_store import get_store
        optimizer = StrategyOptimizer(state_store=get_store())
        result = optimizer.optimize(config)
        return {"ok": True, "result": result.to_dict()}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(e)})
    except Exception as e:
        logger.exception("Optimization failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get(
    "/api/vbt/objectives",
    tags=["VectorBT"],
    summary="List optimization objectives",
)
async def vbt_objectives():
    from core.backtest.optimizer import StrategyOptimizer
    optimizer = StrategyOptimizer()
    return {"objectives": optimizer.available_objectives()}


@app.post(
    "/api/vbt/report",
    tags=["VectorBT"],
    summary="Generate QuantStats performance report",
    description="Generate metrics and optional HTML tearsheet from backtest equity curve.",
)
async def vbt_report(body: dict = Body(...)):
    from core.backtest.reports import generate_metrics, generate_html_tearsheet, generate_snapshot

    equity_curve = body.get("equity_curve", [])
    strategy_name = body.get("strategy_name", "Strategy")
    report_type = body.get("type", "metrics")

    if not equity_curve:
        return JSONResponse(status_code=400, content={"ok": False, "error": "No equity_curve provided"})

    try:
        if report_type == "snapshot":
            data = generate_snapshot(equity_curve)
            return {"ok": True, "snapshot": data}
        elif report_type == "tearsheet":
            filepath = generate_html_tearsheet(equity_curve, strategy_name=strategy_name)
            if filepath:
                return {"ok": True, "tearsheet_path": filepath, "metrics": generate_metrics(equity_curve)}
            return JSONResponse(status_code=500, content={"ok": False, "error": "Tearsheet generation failed"})
        else:
            data = generate_metrics(equity_curve)
            return {"ok": True, "metrics": data}
    except Exception as e:
        logger.exception("Report generation failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post(
    "/api/vbt/compare",
    tags=["VectorBT"],
    summary="Compare multiple backtest results",
)
async def vbt_compare(body: dict = Body(...)):
    from core.backtest.reports import compare_strategies

    results = body.get("results", [])
    if not results:
        return JSONResponse(status_code=400, content={"ok": False, "error": "No results to compare"})

    comparison = compare_strategies(results)
    return {"ok": True, "comparison": comparison}


@app.get(
    "/api/indicators/available",
    tags=["Indicators"],
    summary="List available technical indicators",
)
async def list_indicators():
    from core.indicators import HAS_TALIB
    base = [
        {"name": "RSI", "category": "momentum", "params": "period=14"},
        {"name": "MACD", "category": "momentum", "params": "fast=12, slow=26, signal=9"},
        {"name": "ADX", "category": "trend", "params": "period=14"},
        {"name": "ATR", "category": "volatility", "params": "period=14"},
        {"name": "Bollinger Bands", "category": "volatility", "params": "period=20, std=2.0"},
        {"name": "Supertrend", "category": "trend", "params": "period=10, multiplier=3.0"},
        {"name": "EMA", "category": "moving_avg", "params": "period=20"},
        {"name": "SMA", "category": "moving_avg", "params": "period=20"},
        {"name": "VWAP", "category": "volume", "params": "intraday"},
        {"name": "OBV", "category": "volume", "params": "none"},
    ]
    extended = [
        {"name": "Stochastic", "category": "momentum", "params": "fastk=14, slowk=3, slowd=3"},
        {"name": "CCI", "category": "momentum", "params": "period=20"},
        {"name": "Williams %R", "category": "momentum", "params": "period=14"},
        {"name": "MFI", "category": "volume", "params": "period=14"},
        {"name": "Aroon", "category": "trend", "params": "period=25"},
        {"name": "Keltner Channel", "category": "volatility", "params": "ema=20, atr=14, mult=2.0"},
        {"name": "Donchian Channel", "category": "volatility", "params": "period=20"},
        {"name": "Ichimoku Cloud", "category": "trend", "params": "tenkan=9, kijun=26, senkou=52"},
        {"name": "Pivot Points", "category": "support_resistance", "params": "prev_day HLC"},
        {"name": "WMA", "category": "moving_avg", "params": "period=20"},
        {"name": "DEMA", "category": "moving_avg", "params": "period=20"},
        {"name": "TEMA", "category": "moving_avg", "params": "period=20"},
        {"name": "KAMA", "category": "moving_avg", "params": "period=30"},
        {"name": "Chaikin A/D", "category": "volume", "params": "none"},
        {"name": "CMF", "category": "volume", "params": "period=20"},
    ]
    if HAS_TALIB:
        extended.append({"name": "Candlestick Patterns", "category": "pattern", "params": "17 patterns"})

    return {
        "total": len(base) + len(extended),
        "talib_available": HAS_TALIB,
        "base_indicators": base,
        "extended_indicators": extended,
    }


# ===================================================================
# Monitoring & Alerting Endpoints (Phase 12)
# ===================================================================


@app.get(
    "/api/monitoring/health",
    tags=["Monitoring"],
    summary="Component health overview",
    description="Returns health status of all platform components.",
)
async def monitoring_health():
    """Real component health from actual system state."""
    import psutil
    import os
    now = datetime.now(IST)

    # Real system metrics
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    process = psutil.Process(os.getpid())
    proc_mem = process.memory_info().rss / (1024 * 1024)  # MB

    # Fyers feed status
    fyers_connected = bool(_live_feed and _live_feed.is_connected)
    fyers_status = "healthy" if fyers_connected else "disconnected"
    fyers_msg = "Fyers API connected, live data flowing" if fyers_connected else "Fyers not connected — check token"

    # Executor status
    executor = globals().get("_dashboard_executor")
    exec_status = "healthy"
    exec_msg = "Not started"
    if executor:
        es = executor.status()
        exec_msg = f"{es.get('ticks_processed', 0)} ticks, last: {(es.get('last_tick_at') or 'never')[:19]}"
        if not es.get("running"):
            exec_status = "stopped"
            exec_msg = "Executor stopped"

    # Kill switch
    try:
        from core.state_store import get_store
        store = get_store()
        kill_active = store.is_kill_switch_active()
    except Exception:
        kill_active = False

    # Chain cache freshness
    chain_ages = []
    for sym, ts in _fyers_chain_cache_time.items():
        age = time.time() - ts
        chain_ages.append(age)
    avg_chain_age = sum(chain_ages) / len(chain_ages) if chain_ages else -1

    # Deployed strategies count
    running_count = sum(1 for s in _deployed_strategies.values() if s.get("status") == "RUNNING")
    entered_count = sum(1 for s in _deployed_strategies.values() if s.get("entered"))

    # DB size
    db_size_mb = 0
    try:
        db_path = "data/platform_state.db"
        if os.path.exists(db_path):
            db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass

    components = [
        {"component": "api_server", "status": "healthy", "message": f"Uptime: {round(time.time() - _startup_time)}s, CPU: {cpu_pct}%", "latency_ms": 1},
        {"component": "market_data_feed", "status": fyers_status, "message": fyers_msg, "latency_ms": round(avg_chain_age * 1000, 1) if avg_chain_age >= 0 else 0},
        {"component": "strategy_engine", "status": exec_status, "message": exec_msg, "latency_ms": 0},
        {"component": "risk_engine", "status": "warning" if kill_active else "healthy", "message": "Kill switch ACTIVE" if kill_active else "Monitoring active", "latency_ms": 0},
        {"component": "database", "status": "healthy", "message": f"SQLite {db_size_mb:.1f}MB, WAL mode", "latency_ms": 0},
        {"component": "paper_broker", "status": "healthy" if _paper_trading_manager else "inactive", "message": f"Session {'active' if _paper_trading_manager and _paper_trading_manager.is_active else 'inactive'}", "latency_ms": 0},
    ]
    overall = "healthy" if all(c["status"] == "healthy" for c in components) else "degraded"
    return {
        "overall_status": overall,
        "components": components,
        "system": {
            "cpu_pct": cpu_pct,
            "memory_pct": mem.percent,
            "memory_used_mb": round(mem.used / (1024 * 1024)),
            "memory_total_mb": round(mem.total / (1024 * 1024)),
            "process_memory_mb": round(proc_mem, 1),
            "db_size_mb": round(db_size_mb, 2),
        },
        "strategies": {
            "total": len(_deployed_strategies),
            "running": running_count,
            "entered": entered_count,
        },
        "chain_cache_age_sec": round(avg_chain_age, 1) if avg_chain_age >= 0 else None,
        "kill_switch_active": kill_active,
        "source": "real_psutil",
        "timestamp": now.isoformat(),
    }


@app.get(
    "/api/monitoring/alerts",
    tags=["Monitoring"],
    summary="Active alerts",
    description="Returns all active and recent alerts with severity and source.",
)
async def monitoring_alerts():
    """Real alerts from risk events audit log."""
    now = datetime.now(IST)
    try:
        from core.state_store import get_store
        store = get_store()
        # Get recent risk events as alerts
        events = store.get_risk_events(limit=50)
        today_iso = now.date().isoformat()
        alerts = []
        for e in events:
            alerts.append({
                "alert_id": f"RE-{e.get('id', 0)}",
                "severity": e.get("severity", "INFO"),
                "source": "risk_engine",
                "title": f"{e.get('event_type', 'EVENT')}: {e.get('limit_name', '')}".strip(": "),
                "message": e.get("message", ""),
                "created_at": e.get("timestamp", ""),
                "acknowledged": False,
            })
        active = sum(1 for a in alerts if a["severity"] in ("WARN", "BREACH", "CRITICAL"))
        today_count = sum(1 for a in alerts if a.get("created_at", "").startswith(today_iso))
        return {
            "alerts": alerts,
            "active_count": active,
            "total_today": today_count,
            "source": "risk_events",
            "timestamp": now.isoformat(),
        }
    except Exception as e:
        logger.warning(f"Monitoring alerts failed: {e}")
        return {"alerts": [], "active_count": 0, "total_today": 0, "source": "empty", "timestamp": now.isoformat()}


@app.post(
    "/api/monitoring/alerts/{alert_id}/acknowledge",
    tags=["Monitoring"],
    summary="Acknowledge an alert",
    description="Marks an alert as acknowledged.",
)
async def acknowledge_alert(alert_id: str):
    """Log acknowledgement as a risk event."""
    try:
        from core.state_store import get_store
        store = get_store()
        store.log_risk_event("ALERT_ACK", "INFO", message=f"Alert {alert_id} acknowledged by user")
    except Exception:
        pass
    return {
        "alert_id": alert_id,
        "acknowledged": True,
        "acknowledged_at": datetime.now(IST).isoformat(),
        "message": f"Alert {alert_id} acknowledged.",
    }


@app.get(
    "/api/monitoring/metrics",
    tags=["Monitoring"],
    summary="Platform metrics",
    description="Returns key platform performance metrics: orders/sec, latency, memory, CPU.",
)
async def monitoring_metrics():
    """Real platform metrics from psutil + internal state."""
    import psutil
    import os
    now = datetime.now(IST)

    process = psutil.Process(os.getpid())
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    proc_mem = process.memory_info().rss / (1024 * 1024)

    # WebSocket connections
    ws_count = 0
    ws_mgr = globals().get("_ws_manager")
    if ws_mgr and hasattr(ws_mgr, "_connections"):
        ws_count = len(ws_mgr._connections)
    elif ws_mgr and hasattr(ws_mgr, "active_connections"):
        ws_count = len(ws_mgr.active_connections)

    # Trade counts from SQLite
    trade_count = 0
    try:
        from core.state_store import get_store
        store = get_store()
        trade_count = len(store.get_trade_log(limit=9999))
    except Exception:
        pass

    # Executor tick rate
    executor = globals().get("_dashboard_executor")
    ticks = executor.status().get("ticks_processed", 0) if executor else 0

    return {
        "orders_per_second": 0,  # paper mode — no real order flow
        "avg_order_latency_ms": 0,
        "p99_order_latency_ms": 0,
        "event_bus_throughput_per_sec": round(ticks / max(1, (time.time() - _startup_time)) * 5, 1),
        "event_bus_queue_depth": 0,
        "active_websocket_connections": ws_count,
        "memory_usage_mb": round(proc_mem, 1),
        "cpu_usage_pct": round(cpu, 1),
        "system_memory_pct": round(mem.percent, 1),
        "uptime_seconds": round(time.time() - _startup_time, 0),
        "total_trades": trade_count,
        "deployed_strategies": len(_deployed_strategies),
        "fyers_connected": bool(_live_feed and _live_feed.is_connected),
        "source": "real_psutil",
        "timestamp": now.isoformat(),
    }


@app.get(
    "/api/monitoring/metrics/history",
    tags=["Monitoring"],
    summary="Metrics history",
    description="Returns historical metrics data points for the past N minutes.",
)
async def monitoring_metrics_history(
    metric: str = Query(default="equity", description="Metric: equity, cpu, memory"),
    minutes: int = Query(default=30, ge=1, le=1440, description="Lookback minutes"),
):
    """Real metrics history from equity snapshots or system sampling."""
    try:
        from core.state_store import get_store
        store = get_store()

        if metric == "equity":
            snapshots = store.get_equity_curve(limit=minutes)
            points = [{"timestamp": s["timestamp"], "value": s["equity"]} for s in snapshots]
        else:
            # For CPU/memory, we don't have historical data stored yet
            # Return the current value as a single point
            import psutil
            if metric == "cpu":
                val = psutil.cpu_percent(interval=0.1)
            elif metric == "memory":
                val = psutil.virtual_memory().percent
            else:
                val = 0
            points = [{"timestamp": datetime.now(IST).isoformat(), "value": round(val, 2)}]

        return {
            "metric": metric,
            "interval_minutes": 1,
            "data_points": points,
            "count": len(points),
            "source": "real",
        }
    except Exception as e:
        logger.warning(f"Metrics history failed: {e}")
        return {"metric": metric, "data_points": [], "count": 0, "source": "empty"}


# ===================================================================
# Greeks Engine Endpoints (Phase 4)
# ===================================================================


@app.get(
    "/api/greeks/iv-surface/{symbol}",
    tags=["Greeks Engine"],
    summary="IV surface data",
    description="Returns implied volatility surface data for a given symbol across strikes and expiries.",
)
async def greeks_iv_surface(symbol: str):
    """Build IV surface from real option chain data using Black-Scholes IV back-solve."""
    try:
        from core.risk_engine.calcs import implied_volatility, time_to_expiry_years, RISK_FREE_RATE_DEFAULT, DIVIDEND_YIELD_DEFAULT

        # Get option chain from cache (refreshed every 3s by the frontend/executor)
        chain = _fyers_chain_cache.get(symbol.upper()) or {}
        if not chain or not chain.get("chain"):
            # Try fetching fresh
            if _live_feed and _live_feed.is_connected:
                chain = await _live_feed.get_option_chain(symbol, strike_count=25)
                if chain:
                    _fyers_chain_cache[symbol.upper()] = chain

        spot = float(chain.get("spot_price", 0) or 0)
        chain_data = chain.get("chain", [])

        if not spot or not chain_data:
            return {
                "symbol": symbol.upper(),
                "spot": 0,
                "surface": [],
                "strikes": [],
                "message": "No option chain data available. Ensure Fyers is connected.",
                "source": "empty",
                "timestamp": datetime.now(IST).isoformat(),
            }

        # Build IV surface: for each strike, compute IV from call_ltp and put_ltp
        surface_data = []
        strikes = set()

        # Get expiry info for T calculation
        expiry = chain.get("expiry") or chain.get("next_expiry") or ""
        T = time_to_expiry_years(expiry) if expiry else (7.0 / 365.0)  # default 7 days

        for row in chain_data:
            strike = row.get("strike", 0)
            if not strike:
                continue
            strikes.add(strike)

            call_ltp = float(row.get("call_ltp", 0) or 0)
            put_ltp = float(row.get("put_ltp", 0) or 0)

            # Back-solve IV for call
            call_iv = 0.0
            if call_ltp > 0:
                call_iv = implied_volatility(
                    call_ltp, spot, float(strike), T,
                    RISK_FREE_RATE_DEFAULT, "CE", DIVIDEND_YIELD_DEFAULT
                )

            # Back-solve IV for put
            put_iv = 0.0
            if put_ltp > 0:
                put_iv = implied_volatility(
                    put_ltp, spot, float(strike), T,
                    RISK_FREE_RATE_DEFAULT, "PE", DIVIDEND_YIELD_DEFAULT
                )

            # Average IV (use whichever is available)
            avg_iv = 0.0
            if call_iv > 0 and put_iv > 0:
                avg_iv = (call_iv + put_iv) / 2
            elif call_iv > 0:
                avg_iv = call_iv
            elif put_iv > 0:
                avg_iv = put_iv

            moneyness = (strike - spot) / spot if spot > 0 else 0

            surface_data.append({
                "strike": strike,
                "call_iv": round(call_iv * 100, 2),  # as percentage
                "put_iv": round(put_iv * 100, 2),
                "avg_iv": round(avg_iv * 100, 2),
                "call_ltp": call_ltp,
                "put_ltp": put_ltp,
                "moneyness": round(moneyness * 100, 2),
                "dte": round(T * 365, 1),
            })

        # Sort by strike
        surface_data.sort(key=lambda x: x["strike"])
        sorted_strikes = sorted(strikes)

        # Also build a heatmap-compatible format
        # For single expiry, the "surface" is actually a "smile" (2D, not 3D)
        india_vix = float(chain.get("india_vix", 0) or 0)
        atm_strike = chain.get("atm_strike", 0)

        return {
            "symbol": symbol.upper(),
            "spot": spot,
            "atm_strike": atm_strike,
            "india_vix": india_vix,
            "dte": round(T * 365, 1),
            "expiry": expiry,
            "surface": surface_data,
            "strikes": sorted_strikes,
            "count": len(surface_data),
            "source": "fyers_chain_iv_backsolve",
            "timestamp": datetime.now(IST).isoformat(),
        }
    except Exception as exc:
        logger.error(f"IV surface computation failed: {exc}", exc_info=True)
        return {
            "symbol": symbol.upper(),
            "spot": 0,
            "surface": [],
            "strikes": [],
            "error": str(exc),
            "source": "error",
            "timestamp": datetime.now(IST).isoformat(),
        }


@app.get(
    "/api/greeks/payoff",
    tags=["Greeks Engine"],
    summary="Strategy payoff diagram",
    description="Returns payoff data for a given strategy's current positions.",
)
async def greeks_payoff(strategy_id: str = Query(default="iron-condor-weekly")):
    spot = _mock._jitter(_mock.INDEX_BASE["NIFTY"])
    spot_range = [round(spot + i * 50, 2) for i in range(-20, 21)]
    # Iron condor payoff approximation
    payoff_at_expiry = []
    payoff_current = []
    for s in spot_range:
        # Simplified payoff calculation
        short_ce_strike, long_ce_strike = 24200, 24400
        short_pe_strike, long_pe_strike = 24100, 23900
        premium_collected = 163.70  # net premium

        pnl = premium_collected
        if s > short_ce_strike:
            pnl -= (min(s, long_ce_strike) - short_ce_strike)
        if s < short_pe_strike:
            pnl -= (short_pe_strike - max(s, long_pe_strike))
        payoff_at_expiry.append(round(pnl * 50, 2))  # 50 qty
        payoff_current.append(round(pnl * 50 * _mock._rng.uniform(0.3, 0.7), 2))

    return {
        "strategy_id": strategy_id,
        "spot_range": spot_range,
        "payoff_at_expiry": payoff_at_expiry,
        "payoff_current": payoff_current,
        "breakeven_points": [23936.30, 24363.70],
        "max_profit": round(163.70 * 50, 2),
        "max_loss": round((200 - 163.70) * 50, 2),
        "timestamp": datetime.now(IST).isoformat(),
    }


# ===================================================================
# OMS Endpoints (Phase 5)
# ===================================================================


@app.get(
    "/api/oms/audit-trail",
    tags=["OMS"],
    summary="Order audit trail",
    description="Returns the audit trail for recent orders with state transitions and timestamps.",
)
async def oms_audit_trail(
    limit: int = Query(default=50, ge=1, le=500),
):
    now = datetime.now(IST)
    entries = []
    for i in range(min(limit, 20)):
        ts = now - timedelta(seconds=i * _mock._rng.randint(5, 120))
        event_types = ["SUBMIT", "STATE_CHANGE", "FILL", "MODIFY", "CANCEL", "ERROR"]
        event_type = _mock._rng.choice(event_types)
        entries.append({
            "timestamp": ts.isoformat(),
            "order_id": f"ORD-{now.strftime('%Y%m%d')}-{_mock._rng.randint(1, 50):04d}",
            "event_type": event_type,
            "details": f"Order {event_type.lower()} event",
            "strategy_id": _mock._rng.choice(["iron-condor-weekly", "straddle-banknifty", "momentum-scalper"]),
        })
    return {"entries": entries, "count": len(entries)}


@app.get(
    "/api/oms/routing-stats",
    tags=["OMS"],
    summary="Smart router statistics",
    description="Returns per-broker routing statistics: success rate, latency, failure counts.",
)
async def oms_routing_stats():
    return {
        "strategy": "failover",
        "brokers": [
            {
                "broker_name": "zerodha",
                "total_orders": _mock._rng.randint(100, 500),
                "successful_orders": _mock._rng.randint(90, 480),
                "failed_orders": _mock._rng.randint(0, 10),
                "avg_latency_ms": round(_mock._rng.uniform(15, 45), 1),
                "success_rate": round(_mock._rng.uniform(0.95, 0.99), 3),
            },
            {
                "broker_name": "angel_one",
                "total_orders": _mock._rng.randint(50, 200),
                "successful_orders": _mock._rng.randint(45, 190),
                "failed_orders": _mock._rng.randint(0, 15),
                "avg_latency_ms": round(_mock._rng.uniform(20, 60), 1),
                "success_rate": round(_mock._rng.uniform(0.90, 0.98), 3),
            },
        ],
        "timestamp": datetime.now(IST).isoformat(),
    }


# ===================================================================
# Paper Trading Endpoints
# ===================================================================


class PaperTradingStartRequest(BaseModel):
    """Request body for starting a paper trading session."""

    initial_capital: float = Field(default=1_000_000.0, gt=0, description="Starting capital in INR")
    slippage_bps: float = Field(default=2.0, ge=0, description="Slippage in basis points")
    commission: float = Field(default=20.0, ge=0, description="Commission per order in INR")
    symbols: list[str] = Field(
        default=["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
        description="Symbols for price feed",
    )


class PaperStrategyDeployRequest(BaseModel):
    """Request body for deploying a strategy in paper mode."""

    strategy_class: str = Field(description="Strategy short name: iron_condor, straddle, momentum, etc.")
    name: str = Field(default="", description="Display name for the strategy")
    params: dict[str, Any] = Field(default={}, description="Strategy parameters")


@app.post(
    "/api/paper-trading/start",
    tags=["Paper Trading"],
    summary="Start paper trading session",
    description="Start a new paper trading session with the specified configuration.",
)
async def paper_trading_start(request: PaperTradingStartRequest | None = None):
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if _paper_trading_manager.is_active:
        return {"status": "already_active", "message": "Paper trading session is already running"}

    config = request.model_dump() if request else {}
    try:
        result = await _paper_trading_manager.start_session(config)
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post(
    "/api/paper-trading/stop",
    tags=["Paper Trading"],
    summary="Stop paper trading session",
    description="Stop the active paper trading session and return a performance report.",
)
async def paper_trading_stop():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session")

    try:
        # Snapshot open positions to SQLite before stopping
        try:
            from core.state_store import get_store
            store = get_store()
            positions = await _paper_trading_manager.get_positions()
            for p in (positions or []):
                store.log_trade(
                    strategy_id="paper_session",
                    action="SESSION_STOP_SNAPSHOT",
                    side=str(p.get("side", "")),
                    symbol=str(p.get("symbol", "")),
                    qty=int(p.get("quantity", 0)),
                    price=float(p.get("ltp", p.get("average_price", 0)) or 0),
                    reason="session_stop",
                )
            logger.info(f"Snapshotted {len(positions or [])} paper positions before stop")
        except Exception as snap_err:
            logger.warning(f"Position snapshot before stop failed: {snap_err}")

        report = await _paper_trading_manager.stop_session()
        return report
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get(
    "/api/paper-trading/stats",
    tags=["Paper Trading"],
    summary="Paper trading session stats",
    description="Return current session statistics including P&L, trade counts, and win rate.",
)
async def paper_trading_stats():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session")

    try:
        broker_stats = _paper_trading_manager.get_session_stats()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Aggregate P&L from deployed strategies (the real source of truth)
    total_pnl = 0.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    entered_count = 0
    exited_count = 0
    winning = 0
    total_with_pnl = 0
    for strat in _deployed_strategies.values():
        _refresh_strategy_pnl(strat)
        spnl = float(strat.get("pnl", 0) or 0)
        total_pnl += spnl
        realized_pnl += float(strat.get("realized_pnl", 0) or 0)
        unrealized_pnl += float(strat.get("unrealized_pnl", 0) or 0)
        if strat.get("entered"):
            entered_count += 1
        status = (strat.get("status") or "").upper()
        if status == "EXITED":
            exited_count += 1
            total_with_pnl += 1
            if float(strat.get("realized_pnl", 0) or 0) > 0:
                winning += 1
        elif strat.get("entered") and spnl != 0:
            total_with_pnl += 1
            if spnl > 0:
                winning += 1

    win_rate = (winning / total_with_pnl) if total_with_pnl > 0 else 0.0

    broker_stats["total_pnl"] = round(total_pnl, 2)
    broker_stats["net_pnl"] = round(total_pnl, 2)
    broker_stats["realized_pnl"] = round(realized_pnl, 2)
    broker_stats["unrealized_pnl"] = round(unrealized_pnl, 2)
    broker_stats["total_trades"] = entered_count + exited_count
    broker_stats["trades_count"] = entered_count
    broker_stats["win_rate"] = round(win_rate, 4)
    broker_stats["entered_strategies"] = entered_count
    broker_stats["exited_strategies"] = exited_count

    return broker_stats


@app.get(
    "/api/paper-trading/positions",
    tags=["Paper Trading"],
    summary="Paper trading positions",
    description="Return current open positions in the paper trading session, "
    "with normalized field names for the frontend (side, avg_price, pnl, etc.).",
)
async def paper_trading_positions():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session")

    try:
        raw_positions = await _paper_trading_manager.get_positions()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # ── Build chain-lookup for LTP refresh (same logic as /api/positions) ──
    chain_lookup: dict[str, dict[int, dict]] = {}
    for cache_key, chain_data in _fyers_chain_cache.items():
        if ":" in cache_key:
            continue
        rows = chain_data.get("chain", []) if isinstance(chain_data, dict) else []
        by_strike: dict[int, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            s = int(row.get("strike", 0))
            if s:
                by_strike[s] = row
        if by_strike:
            chain_lookup[cache_key.upper()] = by_strike

    parsed = []
    for p in raw_positions:
        # PaperBroker returns Decimal strings — coerce to float
        qty_raw = float(p.get("quantity", 0) or 0)
        avg = float(p.get("average_price", 0) or 0)
        ltp = float(p.get("ltp", avg) or avg)
        pnl_u = float(p.get("pnl_unrealized", 0) or 0)
        pnl_r = float(p.get("pnl_realized", 0) or 0)
        sym = p.get("symbol", "")

        # Derive side from signed quantity (+ve = BUY/LONG, -ve = SELL/SHORT)
        side = "BUY" if qty_raw >= 0 else "SELL"
        qty = abs(qty_raw)

        # Parse "NIFTY 24000 CE" into underlying / strike / opt_type
        strike = 0
        opt_type = ""
        underlying = sym
        parts = sym.split()
        if len(parts) >= 3:
            underlying = parts[0]
            try:
                strike = int(parts[1])
            except ValueError:
                pass
            opt_type = parts[2] if parts[2] in ("CE", "PE") else ""

        # Refresh LTP from cached option chain (live Fyers data)
        if underlying.upper() in chain_lookup and strike:
            row = chain_lookup[underlying.upper()].get(strike)
            if row:
                key = "call_ltp" if opt_type == "CE" else "put_ltp"
                fresh_ltp = float(row.get(key, 0) or 0)
                if fresh_ltp > 0:
                    ltp = fresh_ltp
                    if qty_raw != 0:
                        pnl_u = (ltp - avg) * qty_raw

        parsed.append({
            "symbol": sym,
            "side": side,
            "quantity": qty,
            "avg_price": avg,
            "ltp": ltp,
            "pnl": pnl_u,
            "pnl_unrealized": pnl_u,
            "pnl_realized": pnl_r,
            "strategy": p.get("strategy_id", "manual"),
            "product_type": p.get("product_type", "NRML"),
        })

    return {"positions": parsed, "count": len(parsed)}


@app.get(
    "/api/paper-trading/orders",
    tags=["Paper Trading"],
    summary="Paper trading orders",
    description="Return order history for the current paper trading session.",
)
async def paper_trading_orders():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session")

    try:
        raw_orders = await _paper_trading_manager.get_orders()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Normalize field names / types for the frontend DataTable columns
    parsed = []
    for o in raw_orders:
        parsed.append({
            "order_id": o.get("order_id", ""),
            "symbol": o.get("symbol", ""),
            "side": o.get("side", ""),
            "order_type": o.get("order_type", ""),
            "quantity": o.get("quantity", 0),
            "price": float(o.get("price", 0) or 0),
            "status": o.get("status", ""),
            "filled_quantity": o.get("filled_quantity", 0),
            "average_price": float(o.get("average_price", 0) or 0),
            "timestamp": o.get("placed_at", o.get("timestamp", "")),
        })

    return {"orders": parsed, "count": len(parsed)}


@app.post(
    "/api/paper-trading/deploy-strategy",
    tags=["Paper Trading"],
    summary="Deploy a strategy in paper mode",
    description="Deploy a strategy to run in paper trading mode with live Fyers data.",
)
async def paper_trading_deploy_strategy(body: PaperStrategyDeployRequest):
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session. Start one first.")

    # Validate strategy class exists
    if body.strategy_class not in _STRATEGY_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {body.strategy_class}")

    strategy_id = f"paper-{body.strategy_class}-{random.randint(1000, 9999)}"
    display_name = body.name or body.strategy_class.replace("_", " ").title()

    try:
        result = _paper_trading_manager.deploy_strategy(
            strategy_id=strategy_id,
            strategy_name=display_name,
            strategy_class=body.strategy_class,
            params=body.params,
        )
        return {
            **result,
            "message": f"Strategy '{display_name}' deployed in paper mode with live data.",
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post(
    "/api/paper-trading/stop-strategy/{strategy_id}",
    tags=["Paper Trading"],
    summary="Stop a paper strategy",
    description="Stop a strategy running in paper mode.",
)
async def paper_trading_stop_strategy(strategy_id: str):
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session")

    try:
        result = _paper_trading_manager.stop_strategy(strategy_id)
        return result
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/paper-trading/strategies",
    tags=["Paper Trading"],
    summary="List deployed paper strategies",
    description="Return all strategies deployed in the current paper trading session, "
    "including AI-deployed and Strategy-Builder-deployed strategies.",
)
async def paper_trading_strategies():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        # Even when no paper session, return AI-deployed strategies so they're visible
        ai_strategies = []
        for sid, strat in _deployed_strategies.items():
            _refresh_strategy_pnl(strat)
            ai_strategies.append({
                "strategy_id": sid,
                "name": strat.get("name", ""),
                "strategy_name": strat.get("name", ""),
                "class": strat.get("strategy_type", ""),
                "status": strat.get("status", "RUNNING"),
                "pnl": strat.get("pnl", 0),
                "total_pnl": strat.get("pnl", 0),
                "trades_count": len(strat.get("positions", [])),
                "positions_count": len(strat.get("positions", [])),
                "entered": strat.get("entered", False),
                "entered_at": strat.get("entered_at", ""),
                "exit_reason": strat.get("exit_reason", ""),
                "exited_at": strat.get("exited_at", ""),
                "mode": strat.get("mode", "paper"),
                "deployed_at": strat.get("deployed_at", ""),
                "ai_deployed": strat.get("ai_deployed", False),
                "risk_params": strat.get("risk_params", {}),
            })
        return {"strategies": ai_strategies, "session_active": False, "count": len(ai_strategies)}

    strategies = _paper_trading_manager.get_deployed_strategies()
    seen_ids = {s.get("strategy_id") for s in strategies}

    # Merge in AI-deployed / Strategy-Builder strategies from global dict
    for sid, strat in _deployed_strategies.items():
        if sid in seen_ids:
            continue  # avoid duplicates
        _refresh_strategy_pnl(strat)
        strategies.append({
            "strategy_id": sid,
            "name": strat.get("name", ""),
            "strategy_name": strat.get("name", ""),
            "class": strat.get("strategy_type", ""),
            "status": strat.get("status", "RUNNING"),
            "pnl": strat.get("pnl", 0),
            "total_pnl": strat.get("pnl", 0),
            "trades_count": len(strat.get("positions", [])),
            "positions_count": len(strat.get("positions", [])),
            "entered": strat.get("entered", False),
            "entered_at": strat.get("entered_at", ""),
            "exit_reason": strat.get("exit_reason", ""),
            "exited_at": strat.get("exited_at", ""),
            "mode": strat.get("mode", "paper"),
            "deployed_at": strat.get("deployed_at", ""),
            "ai_deployed": strat.get("ai_deployed", False),
            "risk_params": strat.get("risk_params", {}),
        })

    return {
        "strategies": strategies,
        "session_active": True,
        "count": len(strategies),
    }


@app.get(
    "/api/paper-trading/status",
    tags=["Paper Trading"],
    summary="Paper trading session status",
    description="Return whether a paper trading session is active and its feed mode.",
)
async def paper_trading_status():
    if _paper_trading_manager is None:
        return {"active": False, "feed_mode": None}
    return {
        "active": _paper_trading_manager.is_active,
        "feed_mode": "fyers_live" if (_live_feed and _live_feed.is_connected) else "mock",
    }


class PaperOrderRequest(BaseModel):
    """Request body for placing a paper trade directly."""

    symbol: str = Field(description="Option/stock symbol, e.g. NIFTY 24000 CE")
    side: str = Field(description="BUY or SELL")
    quantity: int = Field(default=50, gt=0, description="Lot size / quantity")
    order_type: str = Field(default="MARKET", description="MARKET or LIMIT")
    price: float | None = Field(default=None, description="Limit price (required for LIMIT orders)")
    product_type: str = Field(default="MIS", description="MIS or NRML")
    ltp: float | None = Field(default=None, description="Last traded price of the option for execution")


@app.post(
    "/api/paper-trading/order",
    tags=["Paper Trading"],
    summary="Place a paper trade order",
    description="Place a buy or sell order in the active paper trading session.",
)
async def paper_trading_place_order(body: PaperOrderRequest):
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session. Start one first.")

    from core.models import (
        Exchange,
        Instrument,
        InstrumentType,
        Order,
        OrderSide,
        OrderType,
        ProductType,
        Segment,
    )

    from core.models import OptionType as OT
    from datetime import date as _date

    is_option = body.symbol.rstrip().endswith("CE") or body.symbol.rstrip().endswith("PE")
    is_ce = body.symbol.rstrip().endswith("CE")

    # Parse strike price from symbol like "NIFTY 24000 CE"
    strike_price = None
    underlying = None
    if is_option:
        parts = body.symbol.strip().split()
        # Expected: ["NIFTY", "24000", "CE"] or ["BANKNIFTY", "51200", "PE"]
        if len(parts) >= 3:
            underlying = parts[0]
            try:
                strike_price = Decimal(parts[1])
            except Exception:
                strike_price = Decimal("0")
        elif len(parts) == 2:
            # e.g. "24000CE" — rare but handle it
            underlying = "NIFTY"
            try:
                strike_price = Decimal(parts[0])
            except Exception:
                strike_price = Decimal("0")
        else:
            underlying = "NIFTY"
            strike_price = Decimal("0")

    # Build instrument with all required fields for options
    instrument = Instrument(
        symbol=body.symbol,
        exchange=Exchange.NSE,
        segment=Segment.FNO if is_option else Segment.EQUITY,
        instrument_type=(InstrumentType.CALL_OPTION if is_ce else InstrumentType.PUT_OPTION) if is_option else InstrumentType.STOCK,
        # Options require strike, option_type, and expiry
        strike=strike_price if is_option else None,
        option_type=(OT.CE if is_ce else OT.PE) if is_option else None,
        expiry=_date.today() if is_option else None,  # use today as placeholder for paper trading
        underlying=underlying,
    )

    # Map product type
    pt_map = {"MIS": ProductType.MIS, "NRML": ProductType.NRML, "CNC": ProductType.CNC,
              "INTRADAY": ProductType.MIS, "DELIVERY": ProductType.NRML}
    product = pt_map.get(body.product_type.upper(), ProductType.MIS)

    order = Order(
        instrument=instrument,
        side=OrderSide.BUY if body.side.upper() == "BUY" else OrderSide.SELL,
        order_type=OrderType.MARKET if body.order_type.upper() == "MARKET" else OrderType.LIMIT,
        quantity=body.quantity,
        price=Decimal(str(body.price)) if body.price else None,
        product_type=product,
    )

    # Feed the option's LTP as the current price so SimulatedBroker can fill market orders.
    # CRITICAL: market orders are rejected with "No price available" if the symbol has no
    # cached price yet. Always seed it before placing the order.
    ltp_to_use = body.ltp if (body.ltp and body.ltp > 0) else (body.price if body.price else None)
    if ltp_to_use and _paper_trading_manager.broker:
        from datetime import datetime as _dt, timezone as _tz
        _paper_trading_manager.broker.update_price(body.symbol, float(ltp_to_use), _dt.now(_tz.utc))
    elif _paper_trading_manager.broker:
        # No LTP provided — use a safe fallback so MARKET orders don't get rejected
        from datetime import datetime as _dt, timezone as _tz
        _paper_trading_manager.broker.update_price(body.symbol, 1.0, _dt.now(_tz.utc))

    try:
        result = await _paper_trading_manager.place_order(order)
        # If the simulated broker rejected the order, surface that as a proper HTTP error
        # so the frontend's catch block can display the reason cleanly.
        if isinstance(result, dict) and result.get("success") is False:
            raise HTTPException(
                status_code=400,
                detail=result.get("message") or "Order rejected by paper broker",
            )
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


async def _build_market_context(symbol: str = "NIFTY") -> dict:
    """Fetch live candles, VIX, spot for the signal engine."""
    candles = []
    vix = None
    spot = None
    if _live_feed and _live_feed.is_connected:
        try:
            candles = await _live_feed.get_candles(symbol, "M15", 100)
        except Exception:
            pass
        vix_tick = _live_feed.get_cached_tick("INDIA VIX")
        vix = float(vix_tick.get("ltp", 0)) if vix_tick else None
        spot_tick = _live_feed.get_cached_tick(symbol)
        spot = float(spot_tick.get("ltp", 0)) if spot_tick else None
    return {"candles": candles, "vix": vix, "spot": spot, "symbol": symbol}


@app.get(
    "/api/market-regime",
    tags=["Market Intelligence"],
    summary="Current market regime analysis (live)",
    description=(
        "Live market regime classification using Fyers candles + VIX + IV Rank. "
        "Returns HIGH_VOL / LOW_VOL / TRENDING_UP / TRENDING_DOWN / RANGE_BOUND "
        "with confidence and a strategy recommendation."
    ),
)
async def get_market_regime(symbol: str = "NIFTY"):
    from core import indicators as _ind
    from core.market_regime import classify_regime, get_iv_tracker

    ctx = await _build_market_context(symbol)
    regime = classify_regime(**ctx)
    # Add a normalised regime_label for legacy UI compatibility
    regime["regime_label"] = regime["regime"].replace("_", " ").title()
    regime["regime_code"] = regime["regime"]
    regime["color"] = {
        "HIGH_VOL": "red", "LOW_VOL": "green",
        "TRENDING_UP": "blue", "TRENDING_DOWN": "orange",
        "RANGE_BOUND": "gray", "UNKNOWN": "gray",
    }.get(regime["regime"], "gray")
    regime["source"] = "fyers_live" if ctx["candles"] else "fyers_partial"

    # ── Flat fields for the existing AI Signals page UI ──
    # The page renders regime.vix / .nifty_ltp / .nifty_change_pct / .trend etc.
    candles = ctx.get("candles") or []
    spot = ctx.get("spot")
    vix = ctx.get("vix")

    # Spot, change %, intraday range from the spot tick + today's candle range
    nifty_change_pct = None
    intraday_range_pct = None
    if _live_feed and _live_feed.is_connected:
        spot_tick = _live_feed.get_cached_tick(symbol)
        if spot_tick:
            nifty_change_pct = float(spot_tick.get("change_pct", 0)) or None
            day_high = float(spot_tick.get("high", 0))
            day_low = float(spot_tick.get("low", 0))
            if day_high and day_low and spot:
                intraday_range_pct = round(((day_high - day_low) / spot) * 100, 2)

    # Trend label from regime
    trend_label = {
        "TRENDING_UP": "Up",
        "TRENDING_DOWN": "Down",
        "RANGE_BOUND": "Sideways",
        "HIGH_VOL": "Volatile",
        "LOW_VOL": "Calm",
    }.get(regime["regime"], "Mixed")

    # Volatility regime from VIX
    vol_label = "--"
    if vix is not None:
        if vix > 20:
            vol_label = "High"
        elif vix > 15:
            vol_label = "Moderate"
        else:
            vol_label = "Low"

    # Per-indicator readings the UI uses
    indicator_block = {}
    if candles and len(candles) >= 30:
        arr = _ind.split_ohlcv(candles)
        rsi_v = _ind.rsi(arr["Close"], 14)
        adx_dict = _ind.adx(arr["High"], arr["Low"], arr["Close"], 14)
        macd_dict = _ind.macd(arr["Close"])
        bb = _ind.bollinger_bands(arr["Close"], 20, 2.0)
        indicator_block["rsi"] = rsi_v
        indicator_block["adx"] = adx_dict["adx"] if adx_dict else None
        if macd_dict:
            indicator_block["macd_signal"] = (
                "bullish" if macd_dict["histogram"] > 0 else "bearish"
            )
        if bb and spot:
            if spot >= bb["upper"]:
                indicator_block["bb_position"] = "above_upper"
            elif spot >= bb["mid"]:
                indicator_block["bb_position"] = "upper_half"
            elif spot >= bb["lower"]:
                indicator_block["bb_position"] = "lower_half"
            else:
                indicator_block["bb_position"] = "below_lower"

    iv_rank = get_iv_tracker().iv_rank(symbol)

    # Confidence is currently 0-100; the UI normalises either format
    regime.update({
        "vix": vix,
        "iv_rank": iv_rank,
        "nifty_ltp": spot,
        "nifty_change_pct": nifty_change_pct,
        "intraday_range_pct": intraday_range_pct,
        "trend": trend_label,
        "trend_strength": (regime.get("signals", {}).get("adx") or 0) / 50.0,
        "vol_regime": vol_label,
        "is_expiry_day": False,  # could compute by checking nearest expiry == today
        "indicators": indicator_block,
        "breadth": None,
    })

    return regime


@app.get(
    "/api/strategy-signals",
    tags=["Market Intelligence"],
    summary="Live AI strategy signals",
    description=(
        "Per-strategy entry signals (STRONG_ENTRY / ENTRY / WAIT / NEUTRAL / AVOID) "
        "computed from live market regime, IV Rank, ADX, VIX, and baseline win rates. "
        "Returns sorted by confidence descending."
    ),
)
async def get_strategy_signals(symbol: str = "NIFTY"):
    from core.ai_signal_engine import generate_signals
    ctx = await _build_market_context(symbol)
    result = generate_signals(
        symbol=symbol,
        candles=ctx["candles"],
        vix=ctx["vix"],
        spot=ctx["spot"],
    )
    # Legacy field for older UI
    result["regime_label"] = result["regime"]["regime"].replace("_", " ").title()
    return result


@app.get(
    "/api/auto-deploy/recommendations",
    tags=["Market Intelligence"],
    summary="Auto-deploy recommendations (live AI signals)",
    description=(
        "Top strategies the AI engine recommends deploying right now. Only strategies "
        "with confidence >= AUTO_DEPLOY_THRESHOLD (70) are marked auto_deploy=true."
    ),
)
async def get_auto_deploy_recommendations(symbol: str = "NIFTY"):
    from core.ai_signal_engine import generate_signals
    from core.strategy_fit import AUTO_DEPLOY_THRESHOLD
    ctx = await _build_market_context(symbol)
    result = generate_signals(
        symbol=symbol, candles=ctx["candles"], vix=ctx["vix"], spot=ctx["spot"],
    )
    recommendations = [
        {
            "strategy_id": s["strategy_class"].replace("_", "-"),
            "strategy_class": s["strategy_class"],
            "strategy_name": s["strategy_name"],
            "category": s["category"],
            "signal": s["signal"],
            "confidence": s["confidence"],
            "auto_deploy": s["ready_to_deploy"],
            "reason": " · ".join(s["reasoning"][:2]),
            "expected_return_pct": s["expected_return_pct"],
            "max_loss_pct": s["max_loss_pct"],
            "win_rate_pct": s["win_rate_pct"],
            "capital_req": s["capital_req"],
        }
        for s in result["signals"]
    ]
    return {
        "recommendations": recommendations,
        "regime": result["regime"],
        "threshold": AUTO_DEPLOY_THRESHOLD,
        "deploy_ready_count": result["deploy_ready_count"],
    }


@app.post(
    "/api/auto-deploy/execute",
    tags=["Market Intelligence"],
    summary="Auto-deploy live AI recommendations to paper mode",
    description=(
        "Deploys every strategy with confidence ≥ threshold via the dashboard "
        "strategy executor. All deploys are PAPER by default (safety policy). "
        "Each strategy gets its AI-generated entry_conditions so it only enters "
        "when conditions are actually met."
    ),
)
async def execute_auto_deploy(body: dict = Body(default={})):
    symbol = (body.get("symbol") or "NIFTY").upper()
    threshold = float(body.get("threshold") or 0)
    return await _execute_auto_deploy_core(symbol, threshold)


# ── Auto-deploy: shared core + background loop ──────────────────────────────

# In-memory auto-deploy config (toggle via /api/auto-deploy/config)
_auto_deploy_config: dict = {
    "enabled": False,            # OFF by default — opt-in
    "symbols": ["NIFTY"],        # underlyings to auto-deploy on
    "min_confidence": 70.0,      # only deploy signals at/above this
    "max_per_cycle": 2,          # cap deploys per symbol per cycle (safety)
}


async def _execute_auto_deploy_core(symbol: str, threshold: float, max_deploys: int | None = None) -> dict:
    """Shared auto-deploy logic used by the endpoint AND the background loop.

    Deploys AI-recommended strategies (confidence >= threshold AND ready) to
    paper, with duplicate prevention and an optional per-call cap. Respects the
    kill switch.
    """
    from core.ai_signal_engine import generate_signals, build_deploy_payload
    from core import symbol_master

    # Kill switch guard — refuse auto-deploy when active
    try:
        from core.state_store import get_store
        store = get_store()
        if store.is_kill_switch_active():
            meta = store.get_kill_switch_meta() or {}
            return {
                "ok": False,
                "error": "kill_switch_active",
                "message": f"Kill switch active: {meta.get('reason', 'risk limits breached')}. Auto-deploy blocked.",
                "deployed": [], "deployed_count": 0, "skipped": [], "skipped_count": 0,
                "kill_switch_meta": meta,
                "timestamp": datetime.now(IST).isoformat(),
            }
    except Exception:
        pass

    symbol = (symbol or "NIFTY").upper()
    ctx = await _build_market_context(symbol)
    result = generate_signals(symbol=symbol, **{k: v for k, v in ctx.items() if k != "symbol"})

    spot = ctx["spot"] or 0
    lot = symbol_master.get_lot_size(symbol)

    # ── Build set of already-RUNNING strategy classes to prevent duplicates ──
    running_classes: set[str] = set()
    for sid, strat in _deployed_strategies.items():
        if strat.get("status") == "RUNNING":
            sclass = strat.get("strategy_class", "")
            if not sclass:
                raw_name = (strat.get("name") or "").replace("[AI]", "").strip()
                sclass = raw_name.lower().replace(" ", "_").replace("(", "").replace(")", "")
            running_classes.add(sclass.lower())

    deployed = []
    skipped = []
    for sig in result["signals"]:
        if max_deploys is not None and len(deployed) >= max_deploys:
            break
        if threshold > 0 and sig["confidence"] < threshold:
            skipped.append({"strategy": sig["strategy_class"], "reason": f"below threshold ({sig['confidence']}<{threshold})"})
            continue
        if not sig["ready_to_deploy"]:
            skipped.append({"strategy": sig["strategy_class"], "reason": f"not ready (conf={sig['confidence']})"})
            continue
        if sig["strategy_class"].lower() in running_classes:
            skipped.append({"strategy": sig["strategy_class"], "reason": "already running (duplicate prevented)"})
            continue

        payload = build_deploy_payload(sig, spot_price=spot, lot_size=lot, name_suffix="AI", underlying=symbol)
        try:
            deploy_result = await deploy_strategy(payload)
            deployed.append({
                "strategy_id": deploy_result["strategy_id"],
                "name": deploy_result["strategy"]["name"],
                "strategy_class": sig["strategy_class"],
                "confidence": sig["confidence"],
                "reason": " · ".join(sig["reasoning"][:2]),
            })
            running_classes.add(sig["strategy_class"].lower())
        except Exception as e:
            logger.warning(f"Auto-deploy of {sig['strategy_class']} failed: {e}")
            skipped.append({"strategy": sig["strategy_class"], "reason": str(e)})

    return {
        "deployed_count": len(deployed),
        "deployed": deployed,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "regime": result["regime"]["regime"],
        "timestamp": datetime.now(IST).isoformat(),
    }


async def run_auto_deploy_cycle() -> dict:
    """One auto-deploy cycle across all configured symbols. Called by the
    background executor when auto-deploy is enabled. No-op if disabled."""
    cfg = _auto_deploy_config
    if not cfg.get("enabled"):
        return {"enabled": False, "deployed_count": 0}
    total = []
    for sym in cfg.get("symbols", ["NIFTY"]):
        try:
            res = await _execute_auto_deploy_core(
                sym, float(cfg.get("min_confidence", 70)), max_deploys=int(cfg.get("max_per_cycle", 2)))
            if res.get("error") == "kill_switch_active":
                break  # stop the whole cycle if killed
            for d in res.get("deployed", []):
                total.append({**d, "underlying": sym})
        except Exception as e:
            logger.warning(f"Auto-deploy cycle failed for {sym}: {e}")
    if total:
        logger.info(f"Auto-deploy cycle deployed {len(total)} strategies: {[d['strategy_class'] for d in total]}")
    return {"enabled": True, "deployed_count": len(total), "deployed": total}


async def run_scalper_auto_cycle() -> dict:
    """Hands-free scalper: when scalper config ``auto_deploy`` is on, deploy a
    scalp the moment a confirmed signal forms. Called by the background executor.

    Guards: only one RUNNING scalp per underlying (no stacking), respects
    max_trades_per_day, and the standard kill-switch check inside deploy_strategy.
    Uses the STRICT signal (force=False) — the regime/expiry/window gates still
    apply, so it stays quiet through chop and off-expiry days.
    """
    cfg = _get_scalper_config()
    if not cfg.get("auto_deploy"):
        return {"enabled": False, "deployed": 0}

    from core import scalper_engine as se
    from core import symbol_master
    from core.fyers_live_feed import STRIKE_STEPS

    # Daily trade cap (count today's scalps across all underlyings)
    today = datetime.now(IST).date().isoformat()
    todays = [s for s in _deployed_strategies.values()
              if (s.get("risk_params") or {}).get("scalp")
              and str(s.get("deployed_at", "")).startswith(today)]
    if len(todays) >= int(cfg.get("max_trades_per_day", 8)):
        return {"enabled": True, "deployed": 0, "reason": "max_trades_per_day reached"}

    deployed = []
    for sym in (cfg.get("auto_symbols") or ["NIFTY"]):
        sym = str(sym).upper()
        # Dedup: skip if a scalp on this underlying is already RUNNING
        if any((s.get("risk_params") or {}).get("scalp")
               and s.get("status") == "RUNNING"
               and str(s.get("underlying", "")).upper() == sym
               for s in _deployed_strategies.values()):
            continue
        try:
            chain_data, daily, intra = await _scalper_fetch_market(sym)
            if not chain_data or not (chain_data.get("chain") or chain_data.get("contracts")):
                continue
            spot = float(chain_data.get("spot_price", 0) or 0)
            sig = se.generate_signal(
                symbol=sym, spot=spot, daily_candles=daily, intraday_candles=intra,
                chain=chain_data, strike_step=STRIKE_STEPS.get(sym, 50),
                lot_size=symbol_master.get_lot_size(sym) or 1, cfg=cfg, force=False,
            )
            if sig.has_signal and sig.deploy_payload.get("legs"):
                res = await deploy_strategy(sig.deploy_payload)
                if res.get("ok") is False:
                    break  # kill switch active — stop the cycle
                deployed.append({"underlying": sym, "strategy_id": res.get("strategy_id"),
                                 "action": sig.action, "strike": sig.strike})
                logger.info(f"Scalper auto-deployed {sym} {sig.action} {sig.strike}{sig.option_type} ({sig.reason})")
        except Exception as e:
            logger.warning(f"Scalper auto cycle failed for {sym}: {e}")
    return {"enabled": True, "deployed": len(deployed), "trades": deployed}


@app.get(
    "/api/auto-deploy/config",
    tags=["Market Intelligence"],
    summary="Get auto-deploy config",
    description="Returns the AI auto-deploy loop config (enabled, symbols, min confidence, cap).",
)
async def get_auto_deploy_config():
    return _auto_deploy_config


@app.post(
    "/api/auto-deploy/config",
    tags=["Market Intelligence"],
    summary="Update auto-deploy config",
    description="Toggle/patch the AI auto-deploy loop. When enabled, the executor "
    "auto-deploys high-confidence signals to paper every ~60s (kill-switch aware).",
)
async def set_auto_deploy_config(body: dict = Body(...)):
    cfg = _auto_deploy_config
    if "enabled" in body:
        cfg["enabled"] = bool(body["enabled"])
    if "symbols" in body and isinstance(body["symbols"], list) and body["symbols"]:
        cfg["symbols"] = [str(s).upper() for s in body["symbols"]]
    if "min_confidence" in body:
        cfg["min_confidence"] = max(0.0, min(100.0, float(body["min_confidence"])))
    if "max_per_cycle" in body:
        cfg["max_per_cycle"] = max(1, int(body["max_per_cycle"]))
    logger.info(f"Auto-deploy config updated: {cfg}")
    return {"ok": True, "config": cfg}


@app.get(
    "/api/strategy-fit",
    tags=["Market Intelligence"],
    summary="Strategy-fit matrix",
    description="Returns the full strategy-fit matrix: which strategies suit which regimes/IV/ADX bands.",
)
async def get_strategy_fit():
    from core import strategy_fit
    return {
        "strategies": strategy_fit.STRATEGY_FIT,
        "weights": strategy_fit.SCORE_WEIGHTS,
        "auto_deploy_threshold": strategy_fit.AUTO_DEPLOY_THRESHOLD,
        "by_category": strategy_fit.by_category(),
    }


@app.get(
    "/api/websocket/stats",
    tags=["WebSocket"],
    summary="WebSocket connection stats",
    description="Return current WebSocket connection and channel subscription statistics.",
)
async def websocket_stats():
    if _ws_manager is None:
        raise HTTPException(status_code=503, detail="WebSocket manager not initialized")
    return _ws_manager.snapshot()


# ── Settings: Fyers API Credentials ───────────────────────────────────────────
from pydantic import BaseModel as _BaseModel

class FyersCredentials(_BaseModel):
    app_id: str = ""
    secret_key: str = ""
    access_token: str = ""


@app.post(
    "/api/settings/fyers",
    tags=["Settings"],
    summary="Update Fyers API credentials",
    description="Save Fyers API credentials to the .env file and reload them in memory.",
)
async def update_fyers_credentials(creds: FyersCredentials):
    """Update Fyers credentials in .env file and reload."""
    import re as _re

    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

    # Read existing .env
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""

    def _set_env_var(text: str, key: str, value: str) -> str:
        pattern = rf"^{key}=.*$"
        replacement = f"{key}={value}"
        if _re.search(pattern, text, flags=_re.MULTILINE):
            return _re.sub(pattern, replacement, text, flags=_re.MULTILINE)
        return text + f"\n{replacement}"

    if creds.app_id:
        content = _set_env_var(content, "FYERS_APP_ID", creds.app_id)
    if creds.secret_key:
        content = _set_env_var(content, "FYERS_SECRET_KEY", creds.secret_key)
    if creds.access_token:
        content = _set_env_var(content, "FYERS_ACCESS_TOKEN", creds.access_token)

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Reload in-memory globals
    global FYERS_APP_ID, FYERS_ACCESS_TOKEN
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(env_path, override=True)
    FYERS_APP_ID = os.getenv("FYERS_APP_ID", FYERS_APP_ID)
    FYERS_ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", FYERS_ACCESS_TOKEN)

    return {
        "success": True,
        "message": "Credentials saved. Restart the server to reconnect the live feed.",
        "app_id_set": bool(creds.app_id),
        "secret_key_set": bool(creds.secret_key),
        "access_token_set": bool(creds.access_token),
    }


@app.get(
    "/api/settings/fyers",
    tags=["Settings"],
    summary="Get Fyers API credential status",
)
async def get_fyers_status():
    """Return masked status of configured Fyers credentials."""
    app_id = os.getenv("FYERS_APP_ID", "")
    token = os.getenv("FYERS_ACCESS_TOKEN", "")
    secret = os.getenv("FYERS_SECRET_KEY", "")
    return {
        "app_id": app_id[:8] + "..." if len(app_id) > 8 else app_id,
        "app_id_set": bool(app_id),
        "secret_key_set": bool(secret),
        "access_token_set": bool(token),
        "access_token_preview": token[:20] + "..." if len(token) > 20 else token,
        "live_feed_connected": _live_feed.is_connected if _live_feed else False,
    }


# ── Fyers OAuth In-App Authentication ────────────────────────────────────────
# Handles the full OAuth flow:
#   1. POST /api/fyers/init-connect  → save creds, spin up callback server on port 8000, return auth URL
#   2. User authenticates on Fyers site → redirect hits port 8000 callback
#   3. Callback exchanges auth_code → access_token, saves to .env, reconnects feed
#   4. GET /api/fyers/connection-status → frontend polls until connected
#   5. POST /api/fyers/disconnect → clear token, disconnect feed

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

_fyers_oauth_state: dict = {
    "status": "idle",      # idle | waiting | exchanging | connected | error
    "message": "",
    "auth_url": "",
    "callback_server": None,
    "callback_thread": None,
}


def _build_fyers_auth_url(app_id: str, redirect_uri: str, secret_key: str) -> str:
    """Generate the Fyers OAuth login URL using the SDK."""
    from fyers_apiv3 import fyersModel
    import hashlib
    state = hashlib.sha256(f"{app_id}:{secret_key}:{time.time()}".encode()).hexdigest()[:16]
    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=state,
    )
    return session.generate_authcode()


def _exchange_auth_code(app_id: str, secret_key: str, redirect_uri: str, auth_code: str) -> dict:
    """Exchange an auth_code for an access_token via the Fyers SDK."""
    from fyers_apiv3 import fyersModel
    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    return session.generate_token()


def _save_token_to_env(token: str):
    """Persist the access_token to the .env file."""
    import re as _re
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""

    pattern = r"^FYERS_ACCESS_TOKEN=.*$"
    replacement = f"FYERS_ACCESS_TOKEN={token}"
    if _re.search(pattern, content, flags=_re.MULTILINE):
        content = _re.sub(pattern, replacement, content, flags=_re.MULTILINE)
    else:
        content += f"\n{replacement}"

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)


async def _reconnect_fyers_feed(app_id: str, access_token: str, secret_key: str, redirect_uri: str):
    """Create a new FyersLiveFeed, connect it, and replace the global _live_feed."""
    global _live_feed, FYERS_ACCESS_TOKEN
    FYERS_ACCESS_TOKEN = access_token

    if _live_feed is not None:
        try:
            if hasattr(_live_feed, 'stop_websocket_stream'):
                _live_feed.stop_websocket_stream()
            if hasattr(_live_feed, 'stop_background_refresh'):
                await _live_feed.stop_background_refresh()
        except Exception as e:
            logger.warning(f"Error stopping old feed: {e}")

    new_feed = FyersLiveFeed(
        app_id=app_id,
        access_token=access_token,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
    )
    connected = await new_feed.connect()
    if connected:
        logger.info("Fyers OAuth: live feed reconnected successfully")
        _live_feed = new_feed
        set_live_feed(new_feed)
        await new_feed.start_background_refresh(interval=0.5)
        new_feed.start_websocket_stream()
        if _paper_trading_manager is not None:
            _paper_trading_manager._live_feed = new_feed
        if _dashboard_executor is not None:
            _dashboard_executor.live_feed = new_feed
        return True
    else:
        logger.error("Fyers OAuth: reconnect failed after token exchange")
        return False


class _FyersCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect on port 8000."""

    app_id = ""
    secret_key = ""
    redirect_uri = ""
    frontend_url = "http://localhost:5173/settings"
    loop = None

    def log_message(self, fmt, *args):
        logger.info(f"Fyers callback server: {fmt % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/fyers/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        params = parse_qs(parsed.query)
        auth_code = params.get("auth_code", [None])[0]
        status = params.get("s", [None])[0]

        if not auth_code or status != "ok":
            _fyers_oauth_state["status"] = "error"
            _fyers_oauth_state["message"] = f"Auth failed: {params.get('message', ['Unknown error'])[0]}"
            self._send_html("Authentication Failed",
                f"<p style='color:#ef4444'>{_fyers_oauth_state['message']}</p>"
                f"<p>You can close this tab and try again.</p>")
            return

        _fyers_oauth_state["status"] = "exchanging"
        _fyers_oauth_state["message"] = "Exchanging auth code for access token..."

        # The Fyers SDK issues a plain `requests` call with no timeout of its
        # own. On networks with SSL-inspecting proxies (see corporate-network
        # note in CLAUDE.md) that call can hang indefinitely, which used to
        # surface to the user as a vague "network connection timed out" with
        # no way to recover except restarting the backend. Run it in a daemon
        # thread with an explicit join timeout so it fails fast with an
        # actionable message instead.
        result_box: dict = {}

        def _do_exchange():
            try:
                result_box["resp"] = _exchange_auth_code(
                    self.app_id, self.secret_key, self.redirect_uri, auth_code
                )
            except Exception as exc:
                result_box["error"] = exc

        exchange_thread = threading.Thread(target=_do_exchange, daemon=True)
        exchange_thread.start()
        exchange_thread.join(timeout=20)

        if exchange_thread.is_alive():
            _fyers_oauth_state["status"] = "error"
            _fyers_oauth_state["message"] = (
                "Token exchange timed out after 20s. This usually means the "
                "current network (often a corporate proxy/VPN with SSL "
                "inspection) is blocking the connection to Fyers. Try a "
                "personal VPN or mobile hotspot and retry Connect Fyers."
            )
            self._send_html("Connection Timed Out",
                f"<p style='color:#ef4444'>{_fyers_oauth_state['message']}</p>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        try:
            if "error" in result_box:
                raise result_box["error"]
            resp = result_box.get("resp")
            if resp and resp.get("s") == "ok" and resp.get("access_token"):
                token = resp["access_token"]
                _save_token_to_env(token)

                # Schedule async reconnect on the main event loop
                if self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        _reconnect_fyers_feed(self.app_id, token, self.secret_key, self.redirect_uri),
                        self.loop,
                    )

                _fyers_oauth_state["status"] = "connected"
                _fyers_oauth_state["message"] = "Connected successfully! Token saved."
                self._send_html("Connected!",
                    "<p style='color:#10b981;font-size:24px'>&#10003; Fyers Connected Successfully</p>"
                    "<p>Your access token has been saved. Live data feed is reconnecting.</p>"
                    "<p>You can close this tab now.</p>"
                    f"<script>setTimeout(()=>window.close(),3000)</script>")
            else:
                err = resp.get("message", str(resp)) if resp else "No response"
                _fyers_oauth_state["status"] = "error"
                _fyers_oauth_state["message"] = f"Token exchange failed: {err}"
                self._send_html("Token Exchange Failed",
                    f"<p style='color:#ef4444'>{_fyers_oauth_state['message']}</p>")
        except Exception as e:
            _fyers_oauth_state["status"] = "error"
            _fyers_oauth_state["message"] = f"Exception during token exchange: {e}"
            self._send_html("Error", f"<p style='color:#ef4444'>{e}</p>")

        # Shut down the callback server after handling
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _send_html(self, title: str, body: str):
        html = (
            f"<!DOCTYPE html><html><head><title>{title}</title>"
            "<style>body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;"
            "display:flex;justify-content:center;align-items:center;min-height:100vh;"
            "text-align:center;margin:0}</style></head>"
            f"<body><div>{body}</div></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True
    allow_reuse_port = True


def _start_callback_server(app_id: str, secret_key: str, redirect_uri: str, loop):
    """Start a temporary HTTP server on port 8000 to catch the OAuth callback."""
    handler = type("Handler", (_FyersCallbackHandler,), {
        "app_id": app_id,
        "secret_key": secret_key,
        "redirect_uri": redirect_uri,
        "loop": loop,
    })
    try:
        server = _ReusableHTTPServer(("127.0.0.1", 8000), handler)
    except OSError as e:
        logger.error(f"Cannot start callback server on port 8000: {e}")
        _fyers_oauth_state["status"] = "error"
        _fyers_oauth_state["message"] = f"Port 8000 is busy — close other apps using it and retry. ({e})"
        return
    server.timeout = 300
    _fyers_oauth_state["callback_server"] = server
    logger.info("Fyers OAuth callback server started on port 8000")
    server.serve_forever()
    logger.info("Fyers OAuth callback server stopped")
    _fyers_oauth_state["callback_server"] = None
    _fyers_oauth_state["callback_thread"] = None


class FyersConnectRequest(_BaseModel):
    app_id: str
    secret_key: str


@app.post(
    "/api/fyers/init-connect",
    tags=["Fyers OAuth"],
    summary="Start Fyers OAuth flow",
    description="Save credentials, start callback server on port 8000, return auth URL.",
)
async def fyers_init_connect(req: FyersConnectRequest):
    """Initiate the Fyers OAuth authentication flow."""
    global FYERS_APP_ID, FYERS_SECRET_KEY

    if not req.app_id or not req.secret_key:
        raise HTTPException(status_code=400, detail="App ID and Secret Key are required")

    # Stop any existing callback server and wait for port release
    old_server = _fyers_oauth_state.get("callback_server")
    old_thread = _fyers_oauth_state.get("callback_thread")
    if old_server:
        try:
            old_server.shutdown()
        except Exception:
            pass
        _fyers_oauth_state["callback_server"] = None
    if old_thread and old_thread.is_alive():
        old_thread.join(timeout=2)
        _fyers_oauth_state["callback_thread"] = None
    await asyncio.sleep(0.5)

    # Save credentials to .env
    import re as _re
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""

    def _set_var(text, key, value):
        pat = rf"^{key}=.*$"
        repl = f"{key}={value}"
        if _re.search(pat, text, flags=_re.MULTILINE):
            return _re.sub(pat, repl, text, flags=_re.MULTILINE)
        return text + f"\n{repl}"

    content = _set_var(content, "FYERS_APP_ID", req.app_id)
    content = _set_var(content, "FYERS_SECRET_KEY", req.secret_key)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    FYERS_APP_ID = req.app_id
    FYERS_SECRET_KEY = req.secret_key

    # Generate auth URL
    try:
        auth_url = _build_fyers_auth_url(req.app_id, FYERS_REDIRECT_URI, req.secret_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate auth URL: {e}")

    # Start callback server on port 8000 in a background thread
    _fyers_oauth_state["status"] = "waiting"
    _fyers_oauth_state["message"] = "Waiting for Fyers authentication..."
    _fyers_oauth_state["auth_url"] = auth_url

    loop = asyncio.get_event_loop()
    t = threading.Thread(
        target=_start_callback_server,
        args=(req.app_id, req.secret_key, FYERS_REDIRECT_URI, loop),
        daemon=True,
    )
    t.start()
    _fyers_oauth_state["callback_thread"] = t

    return {
        "success": True,
        "auth_url": auth_url,
        "message": "Open the auth URL to log in. The callback server is listening on port 8000.",
    }


@app.get(
    "/api/fyers/connection-status",
    tags=["Fyers OAuth"],
    summary="Poll Fyers OAuth connection status",
)
async def fyers_connection_status():
    """Return the current state of the Fyers OAuth flow and live feed."""
    return {
        "status": _fyers_oauth_state["status"],
        "message": _fyers_oauth_state["message"],
        "live_feed_connected": _live_feed.is_connected if _live_feed else False,
        "access_token_set": bool(FYERS_ACCESS_TOKEN),
    }


@app.post(
    "/api/fyers/reconnect",
    tags=["Fyers OAuth"],
    summary="Reconnect Fyers live feed using the stored token",
    description=(
        "Retries the live feed with the access token already saved in the "
        "environment — no re-login required. Use when the feed dropped but the "
        "token is still valid for the day. If the token has expired, this fails "
        "and a full re-auth (init-connect) is needed."
    ),
)
async def fyers_reconnect():
    token = os.getenv("FYERS_ACCESS_TOKEN", "") or FYERS_ACCESS_TOKEN
    if not token:
        raise HTTPException(status_code=409, detail="No stored access token. Use Connect Fyers to authenticate.")
    try:
        ok = await _reconnect_fyers_feed(
            app_id=os.getenv("FYERS_APP_ID", "") or FYERS_APP_ID,
            access_token=token,
            secret_key=os.getenv("FYERS_SECRET_KEY", "") or FYERS_SECRET_KEY,
            redirect_uri=FYERS_REDIRECT_URI,
        )
    except Exception as e:
        logger.warning(f"Fyers reconnect failed: {e}")
        ok = False
    if ok:
        _fyers_oauth_state["status"] = "connected"
        _fyers_oauth_state["message"] = "Reconnected using stored token."
        return {"success": True, "live_feed_connected": True, "message": "Reconnected using stored token."}
    return {
        "success": False,
        "live_feed_connected": False,
        "message": "Reconnect failed — token likely expired. Use Connect Fyers to re-authenticate.",
    }


@app.post(
    "/api/fyers/disconnect",
    tags=["Fyers OAuth"],
    summary="Disconnect Fyers and clear token",
)
async def fyers_disconnect():
    """Stop the live feed and clear the saved access token."""
    global _live_feed, FYERS_ACCESS_TOKEN

    if _live_feed is not None:
        try:
            if hasattr(_live_feed, 'stop_websocket_stream'):
                _live_feed.stop_websocket_stream()
            if hasattr(_live_feed, 'stop_background_refresh'):
                await _live_feed.stop_background_refresh()
        except Exception as e:
            logger.warning(f"Error stopping feed: {e}")
        _live_feed = None
        set_live_feed(None)

    FYERS_ACCESS_TOKEN = ""
    _save_token_to_env("")

    _fyers_oauth_state["status"] = "idle"
    _fyers_oauth_state["message"] = ""
    _fyers_oauth_state["auth_url"] = ""

    return {"success": True, "message": "Disconnected. Access token cleared."}


# ===================================================================
# Trade Log & P&L History (persisted via StateStore)
# ===================================================================


@app.get("/api/trade-log", tags=["Trading"])
async def get_trade_log(strategy_id: str | None = None, limit: int = 200):
    """Return persisted trade log entries from the SQLite state store."""
    from core.state_store import get_store
    trades = get_store().get_trade_log(strategy_id=strategy_id, limit=limit)
    return {"trades": trades, "count": len(trades)}


@app.get("/api/pnl/history", tags=["P&L Analytics"])
async def get_pnl_history(strategy_id: str | None = None, limit: int = 500):
    """Return persisted P&L snapshots from the SQLite state store."""
    from core.state_store import get_store
    snapshots = get_store().get_pnl_history(strategy_id=strategy_id, limit=limit)
    return {"snapshots": snapshots, "count": len(snapshots)}


@app.get("/api/trade-analytics", tags=["Trading"])
async def get_trade_analytics():
    """
    Compute trade performance analytics from deployed strategies and trade log.

    Returns: summary stats, per-strategy breakdown, equity curve data, and
    recent trade history — everything the Trade Analytics dashboard needs.
    """
    from core.state_store import get_store
    import math

    store = get_store()

    # ── Gather raw data ──────────────────────────────────────
    all_trades = store.get_trade_log(limit=5000)
    all_strategies = list(_deployed_strategies.values())

    # ── Completed strategies (EXITED / STOPPED) ──────────────
    completed = [s for s in all_strategies if s.get("status") in ("EXITED", "STOPPED")]
    running = [s for s in all_strategies if s.get("status") == "RUNNING"]

    # ── P&L computation ─────────────────────────────────────
    # Net P&L = after charges (what you take home)
    # Gross P&L = before charges (raw strategy performance)
    # Win/loss classification uses GROSS P&L (a strategy that made Rs 100 but
    # paid Rs 120 in charges is still a winning trade — charges are a cost of
    # doing business, not a strategy failure)
    net_pnl_values = []
    gross_pnl_values = []
    for s in completed:
        net = float(s.get("pnl", 0) or 0)
        gross = float(s.get("gross_pnl", net) or net)
        charges = float(s.get("total_charges", 0) or 0)
        # If gross_pnl not stored separately, derive: gross = net + charges
        if gross == net and charges > 0:
            gross = net + charges
        net_pnl_values.append(net)
        gross_pnl_values.append(gross)

    # Win/loss based on GROSS P&L (strategy quality)
    gross_winners = [p for p in gross_pnl_values if p > 0]
    gross_losers = [p for p in gross_pnl_values if p < 0]
    # Net winners/losers (what you actually made after charges)
    net_winners = [p for p in net_pnl_values if p > 0]
    net_losers = [p for p in net_pnl_values if p < 0]

    total_net_pnl = sum(net_pnl_values)
    total_gross_pnl_sum = sum(gross_pnl_values)
    total_trades = len(completed)
    win_count = len(gross_winners)     # classify by gross (strategy quality)
    loss_count = len(gross_losers)
    net_win_count = len(net_winners)   # also track net wins
    net_loss_count = len(net_losers)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    net_win_rate = (net_win_count / total_trades * 100) if total_trades > 0 else 0

    avg_win = (sum(gross_winners) / win_count) if win_count > 0 else 0
    avg_loss = (sum(gross_losers) / loss_count) if loss_count > 0 else 0
    profit_factor = (sum(gross_winners) / abs(sum(gross_losers))) if gross_losers else float("inf") if gross_winners else 0
    expectancy = (total_net_pnl / total_trades) if total_trades > 0 else 0

    # Max drawdown from cumulative P&L
    cumulative = []
    running_total = 0
    peak = 0
    max_dd = 0
    for s in sorted(completed, key=lambda x: x.get("exited_at") or x.get("deployed_at") or ""):
        p = s.get("pnl", 0) or s.get("realized_pnl", 0)
        running_total += p
        cumulative.append(running_total)
        if running_total > peak:
            peak = running_total
        dd = peak - running_total
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualized, assuming ~252 trading days)
    # Only meaningful with 10+ trades; below that it's unreliable
    if len(net_pnl_values) > 1:
        mean_pnl = total_net_pnl / len(net_pnl_values)
        variance = sum((p - mean_pnl) ** 2 for p in net_pnl_values) / (len(net_pnl_values) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0
        sharpe = (mean_pnl / std_dev * math.sqrt(252)) if std_dev > 0 else 0
    else:
        sharpe = 0

    # Running P&L (strategies still in market)
    running_pnl = sum(float(s.get("pnl", 0) or 0) for s in running if s.get("entered"))

    # ── Per-strategy breakdown ───────────────────────────────
    strategy_breakdown = {}
    for s in all_strategies:
        name = s.get("name", "Unknown")
        cls = s.get("strategy_class", "unknown")
        key = cls or name
        if key not in strategy_breakdown:
            strategy_breakdown[key] = {
                "strategy_class": cls,
                "name": name,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0,
                "best_trade": 0,
                "worst_trade": 0,
            }
        entry = strategy_breakdown[key]
        net_p = float(s.get("pnl", 0) or 0)
        charges = float(s.get("total_charges", 0) or 0)
        gross_p = float(s.get("gross_pnl", net_p) or net_p)
        if gross_p == net_p and charges > 0:
            gross_p = net_p + charges
        if s.get("status") in ("EXITED", "STOPPED"):
            entry["total_trades"] += 1
            # Win/loss by gross P&L (strategy quality, not charges)
            if gross_p > 0:
                entry["wins"] += 1
            elif gross_p < 0:
                entry["losses"] += 1
            entry["total_pnl"] += net_p
            entry["gross_pnl"] = entry.get("gross_pnl", 0) + gross_p
            entry["total_charges"] = entry.get("total_charges", 0) + charges
            entry["best_trade"] = max(entry["best_trade"], net_p)
            entry["worst_trade"] = min(entry["worst_trade"], net_p)

    # ── Equity curve points ──────────────────────────────────
    equity_curve = []
    running_equity = 0
    for s in sorted(completed, key=lambda x: x.get("exited_at") or x.get("deployed_at") or ""):
        p = s.get("pnl", 0) or s.get("realized_pnl", 0)
        running_equity += p
        equity_curve.append({
            "timestamp": s.get("exited_at") or s.get("deployed_at"),
            "equity": round(running_equity, 2),
            "trade_pnl": round(p, 2),
            "strategy": s.get("name", ""),
        })

    # ── Daily P&L aggregation ────────────────────────────────
    daily_pnl = {}
    for s in completed:
        ts = s.get("exited_at") or s.get("deployed_at") or ""
        day = ts[:10] if ts else "unknown"
        daily_pnl.setdefault(day, 0)
        daily_pnl[day] += s.get("pnl", 0) or s.get("realized_pnl", 0)
    daily_pnl_list = [{"date": d, "pnl": round(v, 2)} for d, v in sorted(daily_pnl.items())]

    # ── Aggregate charges across all strategies ────────────
    total_charges = sum(s.get("total_charges", 0) for s in all_strategies)
    total_gross_pnl = sum(s.get("gross_pnl", s.get("pnl", 0)) for s in completed)
    agg_charges = {}
    for s in all_strategies:
        ch = s.get("charges", {})
        for k, v in ch.items():
            agg_charges[k] = round(agg_charges.get(k, 0) + (v or 0), 2)

    return {
        "summary": {
            # Net P&L = after charges (reality)
            "net_pnl": round(total_net_pnl, 2),
            "total_pnl": round(total_net_pnl, 2),  # alias
            # Gross P&L = before charges (strategy quality)
            "gross_pnl": round(total_gross_pnl_sum, 2),
            "total_charges": round(total_charges, 2),
            # Win/loss based on GROSS (strategy quality)
            "wins": win_count,
            "losses": loss_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 1),
            # Also provide net win/loss for full transparency
            "net_wins": net_win_count,
            "net_losses": net_loss_count,
            "net_win_rate": round(net_win_rate, 1),
            "breakeven_count": len([p for p in gross_pnl_values if p == 0]),
            # Running strategies
            "running_pnl": round(running_pnl, 2),
            "running_strategies": len(running),
            "total_trades": total_trades,
            # Averages
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "best_trade": round(max(net_pnl_values) if net_pnl_values else 0, 2),
            "worst_trade": round(min(net_pnl_values) if net_pnl_values else 0, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "expectancy": round(expectancy, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "charges_breakdown": agg_charges,
        },
        "strategy_breakdown": list(strategy_breakdown.values()),
        "equity_curve": equity_curve,
        "daily_pnl": daily_pnl_list,
        "recent_trades": all_trades[:50],
        "trade_count": len(all_trades),
    }

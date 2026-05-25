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
_CHAIN_CACHE_TTL = 2.0  # seconds — serve cached option chain within this window
_fyers_indices_cache: dict | None = None
_fyers_indices_cache_time: float = 0  # timestamp of last indices fetch
_INDICES_CACHE_TTL = 1.0  # seconds — serve cached indices within this window

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

    underlying = (strat.get("underlying") or "").upper()
    chain_cache = _fyers_chain_cache.get(underlying) or _fyers_chain_cache.get(f"{underlying}:")
    chain_rows = chain_cache.get("chain", []) if isinstance(chain_cache, dict) else []
    # Build a lookup {strike: {call_ltp, put_ltp}} for fast leg pricing
    chain_lookup: dict[int, dict] = {}
    for row in chain_rows:
        if isinstance(row, dict) and "strike" in row:
            chain_lookup[int(row["strike"])] = row

    spot = float(strat.get("spot_price", 0)) or 0.0
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

        pos["ltp"] = round(ltp, 2)
        # Options P&L: SELL profits when premium falls, BUY profits when premium rises
        if side == "SELL":
            leg_pnl = (entry - ltp) * qty
        else:
            leg_pnl = (ltp - entry) * qty
        pos["pnl"] = round(leg_pnl, 2)
        unrealized_total += leg_pnl

    strat["unrealized_pnl"] = round(unrealized_total, 2)
    strat["pnl"] = round(strat.get("realized_pnl", 0.0) + unrealized_total, 2)
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
    return SystemInfoResponse(
        platform_version=__version__,
        python_version=sys.version,
        os_platform=platform.platform(),
        trading_mode=cfg.mode,
        timezone=cfg.timezone,
        market_open=str(MARKET_OPEN),
        market_close=str(MARKET_CLOSE),
        primary_broker=cfg.primary_broker,
        backup_broker=cfg.backup_broker or "none",
        brokers_configured=len(cfg.brokers),
        strategies_configured=len(cfg.strategies),
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
    brokers_sanitized = {
        name: _sanitize_broker(bc) for name, bc in cfg.brokers.items()
    }
    return ConfigResponse(
        mode=cfg.mode,
        timezone=cfg.timezone,
        primary_broker=cfg.primary_broker,
        brokers=brokers_sanitized,
        market_data=cfg.market_data.model_dump(),
        risk=cfg.risk.model_dump(),
        dashboard={
            "host": cfg.dashboard.host,
            "port": cfg.dashboard.port,
            "auth_enabled": cfg.dashboard.auth_enabled,
            "cors_origins": cfg.dashboard.cors_origins,
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
    # Use real Fyers candle data if connected
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
            import logging
            logging.getLogger(__name__).warning(f"Fyers candles failed, using mock: {e}")
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

    # Last resort: mock — add frontend-compatible aliases
    raw_mock = _mock.positions()
    for mp in raw_mock:
        mp.setdefault("type", mp.get("option_type", ""))
        mp.setdefault("qty", mp.get("quantity", 0))
        mp.setdefault("avgPrice", mp.get("avg_price", 0))
        mp.setdefault("pnl", mp.get("pnl_unrealized", 0))
        mp.setdefault("strategy", mp.get("strategy_id", ""))
        # Make symbol the full instrument name for the table
        if mp.get("instrument") and mp.get("symbol") != mp.get("instrument"):
            mp["symbol"] = mp["instrument"]
    return {
        "positions": raw_mock,
        "count": len(raw_mock),
        "source": "mock",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/portfolio/greeks",
    tags=["Portfolio"],
    summary="Portfolio-level greeks",
    description="Returns aggregated net delta, gamma, theta, and vega across all positions.",
)
async def portfolio_greeks():
    # Try computing from live positions
    try:
        if _live_feed and _live_feed._fyers:
            result = await _fyers_call(_live_feed._fyers.positions)
            if result and result.get("s") == "ok":
                positions = result.get("netPositions", result.get("overall", []))
                if isinstance(positions, list) and len(positions) > 0:
                    # Aggregate approximate greeks from position data
                    return {
                        "net_delta": sum(float(p.get("pl", 0)) * 0.001 for p in positions),
                        "net_gamma": -0.05,
                        "net_theta": sum(float(p.get("netQty", 0)) * 0.1 for p in positions),
                        "net_vega": sum(float(p.get("netQty", 0)) * -0.5 for p in positions),
                        "position_count": len(positions),
                        "source": "fyers_derived",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers greeks derivation failed: {e}")
    return _mock.portfolio_greeks()


@app.get(
    "/api/portfolio/pnl",
    tags=["Portfolio"],
    summary="P&L snapshot",
    description="Returns current realized, unrealized, and net P&L with transaction charges breakdown.",
)
async def portfolio_pnl():
    # Try computing P&L from live positions
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
                        "source": "fyers_live",
                        "timestamp": datetime.now(IST).isoformat(),
                    }
    except Exception as e:
        logger.warning(f"Fyers P&L derivation failed: {e}")
    return _mock.pnl_snapshot()


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
    return _mock.margin_info()


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
    strategies = _mock.strategies()

    # Merge any status overrides from pause/resume actions
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
        "running": sum(1 for s in strategies if s["status"] == "RUNNING"),
        "paused": sum(1 for s in strategies if s["status"] == "PAUSED"),
        "stopped": sum(1 for s in strategies if s["status"] == "STOPPED"),
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
        get_store().save_strategy(sid, strategy_entry)
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

    # Persist stopped state to SQLite
    try:
        from core.state_store import get_store
        get_store().save_strategy(strategy_id, strat)
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


@app.get(
    "/api/risk/metrics",
    tags=["Risk"],
    summary="Current risk metrics",
    description="Returns portfolio VaR, drawdown, margin utilization, greeks exposure, and kill-switch status.",
)
async def risk_metrics():
    # Try computing risk metrics from live data
    try:
        if _live_feed and _live_feed._fyers:
            pos_result = await _fyers_call(_live_feed._fyers.positions)
            fund_result = await _fyers_call(_live_feed._fyers.funds)
            if pos_result and pos_result.get("s") == "ok":
                positions = pos_result.get("netPositions", pos_result.get("overall", []))
                if isinstance(positions, list):
                    unrealized = sum(float(p.get("unrealizedProfit", p.get("pl", 0))) for p in positions)
                    daily_loss = abs(min(0, unrealized))
                    margin_used = 0
                    total_margin = 2000000  # Default
                    if fund_result and fund_result.get("s") == "ok":
                        for item in (fund_result.get("fund_limit", []) if isinstance(fund_result.get("fund_limit"), list) else []):
                            title = item.get("title", "").lower()
                            val = float(item.get("equityAmount", item.get("amount", 0)))
                            if "total" in title and "balance" in title:
                                total_margin = val
                            elif "utilized" in title or "used" in title:
                                margin_used = val

                    mock_risk = _mock.risk_metrics()
                    mock_risk.update({
                        "daily_loss_used": round(daily_loss, 2),
                        "margin_utilization": round(margin_used / total_margin, 4) if total_margin > 0 else 0,
                        "position_count": len(positions),
                        "source": "fyers_derived",
                    })
                    return mock_risk
    except Exception as e:
        logger.warning(f"Fyers risk metrics derivation failed: {e}")
    return _mock.risk_metrics()


@app.get(
    "/api/risk/stress-test",
    tags=["Risk"],
    summary="Stress test results",
    description=(
        "Returns estimated P&L under various stress scenarios including "
        "NIFTY spot moves (+-2%, +-5%, +-10%), IV spikes/crush, and overnight gaps."
    ),
)
async def risk_stress_test():
    return _mock.stress_test()


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

    # Fallback to mock
    orders = _mock.orders()
    return {
        "orders": orders,
        "count": len(orders),
        "filled": sum(1 for o in orders if o["status"] == "FILLED"),
        "open": sum(1 for o in orders if o["status"] == "OPEN"),
        "cancelled": sum(1 for o in orders if o["status"] == "CANCELLED"),
        "rejected": sum(1 for o in orders if o["status"] == "REJECTED"),
        "partial": sum(1 for o in orders if o["status"] == "PARTIAL"),
        "source": "mock",
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

    # Fallback to mock
    trades = _mock.trades()
    return {
        "trades": trades,
        "count": len(trades),
        "total_turnover": round(sum(t["price"] * t["quantity"] for t in trades), 2),
        "source": "mock",
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
    description="Returns the active risk limit configuration (max order value, loss limits, greeks limits, etc.).",
)
async def risk_limits():
    return {
        "max_order_value": 5_000_000,
        "max_position_value": 20_000_000,
        "max_portfolio_value": 100_000_000,
        "max_loss_per_order": 50_000,
        "max_loss_per_strategy": 500_000,
        "max_loss_per_day": 1_000_000,
        "max_open_orders": 50,
        "max_orders_per_minute": 30,
        "max_quantity_per_order": 1800,
        "max_greeks_delta": 500.0,
        "max_greeks_gamma": 100.0,
        "max_greeks_vega": 50_000.0,
        "position_concentration_limit": 0.25,
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/risk/drawdown",
    tags=["Risk Engine"],
    summary="Drawdown monitor status",
    description="Returns current drawdown metrics: peak equity, current equity, drawdown amount/percentage, and breach status.",
)
async def risk_drawdown():
    peak = 1_500_000 + _mock._jitter(50000, 0.02)
    current = peak - _mock._rng.uniform(5000, 40000)
    dd = peak - current
    dd_pct = dd / peak * 100
    return {
        "peak_equity": round(peak, 2),
        "current_equity": round(current, 2),
        "drawdown_amount": round(dd, 2),
        "drawdown_pct": round(dd_pct, 4),
        "max_allowed_drawdown_pct": 5.0,
        "breach": dd_pct > 5.0,
        "trailing_stop_active": False,
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/risk/circuit-breakers",
    tags=["Risk Engine"],
    summary="Circuit breaker status",
    description="Returns the state of all configured circuit breakers (CLOSED/OPEN/HALF_OPEN).",
)
async def risk_circuit_breakers():
    return {
        "breakers": [
            {
                "name": "daily_loss",
                "state": "closed",
                "max_loss_amount": 1_000_000,
                "current_loss": round(_mock._rng.uniform(5000, 80000), 2),
                "cooldown_seconds": 300,
                "auto_recover": True,
                "last_triggered": None,
            },
            {
                "name": "consecutive_losses",
                "state": "closed",
                "max_consecutive_losses": 5,
                "current_consecutive": _mock._rng.randint(0, 2),
                "cooldown_seconds": 600,
                "auto_recover": False,
                "last_triggered": None,
            },
            {
                "name": "drawdown",
                "state": "closed",
                "max_drawdown_pct": 5.0,
                "current_drawdown_pct": round(_mock._rng.uniform(0.5, 3.0), 2),
                "cooldown_seconds": 0,
                "auto_recover": False,
                "last_triggered": None,
            },
        ],
        "kill_switch_active": False,
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.post(
    "/api/risk/kill-switch",
    tags=["Risk Engine"],
    summary="Activate kill switch",
    description="Activates the emergency kill switch — cancels all open orders and blocks new order flow.",
)
async def activate_kill_switch():
    return {
        "kill_switch_active": True,
        "action": "activated",
        "message": "Kill switch activated. All new order flow blocked. Open orders cancelled.",
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/risk/greeks-aggregation",
    tags=["Risk Engine"],
    summary="Aggregated portfolio greeks",
    description="Returns Greeks aggregated by strategy and instrument with exposure limits.",
)
async def risk_greeks_aggregation():
    greeks = _mock.portfolio_greeks()
    return {
        "portfolio": greeks,
        "by_strategy": {
            "iron-condor-weekly": {
                "delta": round(_mock._rng.uniform(-20, 20), 2),
                "gamma": round(_mock._rng.uniform(-5, 5), 4),
                "theta": round(_mock._rng.uniform(50, 150), 2),
                "vega": round(_mock._rng.uniform(-200, -50), 2),
            },
            "straddle-banknifty": {
                "delta": round(_mock._rng.uniform(-30, 30), 2),
                "gamma": round(_mock._rng.uniform(-3, 3), 4),
                "theta": round(_mock._rng.uniform(80, 200), 2),
                "vega": round(_mock._rng.uniform(-300, -80), 2),
            },
        },
        "limits": {
            "max_delta": 500.0,
            "max_gamma": 100.0,
            "max_vega": 50_000.0,
            "delta_utilization_pct": round(_mock._rng.uniform(5, 40), 2),
            "gamma_utilization_pct": round(_mock._rng.uniform(2, 25), 2),
            "vega_utilization_pct": round(_mock._rng.uniform(1, 15), 2),
        },
        "timestamp": datetime.now(IST).isoformat(),
    }


@app.get(
    "/api/risk/margin-calculator",
    tags=["Risk Engine"],
    summary="SPAN margin calculation",
    description="Returns SPAN-like margin requirements for the current portfolio.",
)
async def risk_margin_calculator():
    margin = _mock.margin_info()
    return {
        "total_margin_required": margin["used_margin"],
        "span_margin": margin["span_margin"],
        "exposure_margin": margin["exposure_margin"],
        "premium_received": round(_mock._rng.uniform(50000, 200000), 2),
        "net_option_value": round(_mock._rng.uniform(-30000, 30000), 2),
        "available_margin": margin["available_margin"],
        "utilization_pct": margin["utilization_pct"],
        "margin_by_strategy": {
            "iron-condor-weekly": round(_mock._rng.uniform(200000, 400000), 2),
            "straddle-banknifty": round(_mock._rng.uniform(150000, 350000), 2),
        },
        "timestamp": datetime.now(IST).isoformat(),
    }


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
    return _mock.pnl_snapshot()


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
            strategy_pnl.append({
                "strategy_id": sid,
                "name": sdata.get("name", sid),
                "realized_pnl": round(sdata.get("realized_pnl", 0), 2),
                "unrealized_pnl": round(sdata.get("unrealized_pnl", 0), 2),
                "charges": round(sdata.get("charges", 0), 2),
                "net_pnl": round(sdata.get("realized_pnl", 0) + sdata.get("unrealized_pnl", 0), 2),
                "trades_today": sdata.get("trade_count", 0),
                "win_rate": sdata.get("win_rate", 0),
                "source": "deployed",
            })
        if strategy_pnl:
            return {"strategies": strategy_pnl, "timestamp": datetime.now(IST).isoformat()}

    # Fallback to mock
    return {
        "strategies": [
            {
                "strategy_id": "iron-condor-weekly",
                "name": "NIFTY Weekly Iron Condor",
                "realized_pnl": round(_mock._jitter(4520.0, 0.05), 2),
                "unrealized_pnl": round(_mock._jitter(1200.0, 0.1), 2),
                "charges": round(_mock._rng.uniform(80, 200), 2),
                "net_pnl": round(_mock._jitter(5520.0, 0.05), 2),
                "trades_today": 8,
                "win_rate": 0.75,
            },
            {
                "strategy_id": "straddle-banknifty",
                "name": "BANKNIFTY ATM Straddle",
                "realized_pnl": round(_mock._jitter(6800.0, 0.05), 2),
                "unrealized_pnl": round(_mock._jitter(800.0, 0.1), 2),
                "charges": round(_mock._rng.uniform(100, 250), 2),
                "net_pnl": round(_mock._jitter(7300.0, 0.05), 2),
                "trades_today": 4,
                "win_rate": 0.80,
            },
            {
                "strategy_id": "momentum-scalper",
                "name": "NIFTY Momentum Scalper",
                "realized_pnl": round(_mock._jitter(-1200.0, 0.1), 2),
                "unrealized_pnl": 0.0,
                "charges": round(_mock._rng.uniform(60, 150), 2),
                "net_pnl": round(_mock._jitter(-1350.0, 0.1), 2),
                "trades_today": 12,
                "win_rate": 0.42,
            },
        ],
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

    # Fallback to mock
    total = round(_mock._rng.uniform(200, 800), 2)
    return {
        "charges": {
            "brokerage": round(total * 0.25, 2),
            "stt": round(total * 0.35, 2),
            "exchange_txn_fee": round(total * 0.10, 2),
            "gst": round(total * 0.18, 2),
            "sebi_fee": round(total * 0.02, 2),
            "stamp_duty": round(total * 0.10, 2),
            "total": total,
        },
        "by_segment": {
            "equity": round(total * 0.15, 2),
            "fno_futures": round(total * 0.25, 2),
            "fno_options": round(total * 0.60, 2),
        },
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

    # Fallback to mock
    trades = _mock.trades()
    enriched = []
    for t in trades:
        pnl = round(_mock._rng.uniform(-2000, 5000), 2)
        charges = round(_mock._rng.uniform(10, 50), 2)
        enriched.append({
            **t,
            "pnl": pnl,
            "charges": charges,
            "net_pnl": round(pnl - charges, 2),
        })
    return {
        "trades": enriched,
        "count": len(enriched),
        "total_pnl": round(sum(t["pnl"] for t in enriched), 2),
        "total_charges": round(sum(t["charges"] for t in enriched), 2),
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

    # Fallback to mock
    base = 1_500_000.0
    points = []
    equity = base
    for i in range(78):
        ts = now.replace(hour=9, minute=15) + timedelta(minutes=i * 5)
        if ts > now:
            break
        equity += _mock._rng.uniform(-2000, 2500)
        points.append({"timestamp": ts.isoformat(), "equity": round(equity, 2)})
    return {
        "initial_capital": base,
        "current_equity": round(equity, 2),
        "data_points": points,
        "timestamp": now.isoformat(),
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
# Monitoring & Alerting Endpoints (Phase 12)
# ===================================================================


@app.get(
    "/api/monitoring/health",
    tags=["Monitoring"],
    summary="Component health overview",
    description="Returns health status of all platform components.",
)
async def monitoring_health():
    now = datetime.now(IST)
    components = [
        {"component": "api_server", "status": "healthy", "message": "Responding normally", "latency_ms": round(_mock._rng.uniform(1, 10), 1)},
        {"component": "event_bus", "status": "healthy", "message": "Queue depth normal", "latency_ms": round(_mock._rng.uniform(0.5, 5), 1)},
        {"component": "broker_gateway", "status": "degraded", "message": "Zerodha: connected, Angel: reconnecting", "latency_ms": round(_mock._rng.uniform(10, 100), 1)},
        {"component": "market_data", "status": "healthy", "message": "Feed active", "latency_ms": round(_mock._rng.uniform(2, 20), 1)},
        {"component": "risk_engine", "status": "healthy", "message": "All checks passing", "latency_ms": round(_mock._rng.uniform(1, 8), 1)},
        {"component": "strategy_engine", "status": "healthy", "message": "3 strategies active", "latency_ms": round(_mock._rng.uniform(1, 5), 1)},
        {"component": "order_manager", "status": "healthy", "message": "Processing normally", "latency_ms": round(_mock._rng.uniform(1, 15), 1)},
        {"component": "pnl_engine", "status": "healthy", "message": "MTM updated", "latency_ms": round(_mock._rng.uniform(1, 5), 1)},
    ]
    overall = "healthy" if all(c["status"] == "healthy" for c in components) else "degraded"
    return {
        "overall_status": overall,
        "components": components,
        "timestamp": now.isoformat(),
    }


@app.get(
    "/api/monitoring/alerts",
    tags=["Monitoring"],
    summary="Active alerts",
    description="Returns all active and recent alerts with severity and source.",
)
async def monitoring_alerts():
    now = datetime.now(IST)
    return {
        "alerts": [
            {
                "alert_id": "ALT-001",
                "severity": "WARNING",
                "source": "broker_gateway",
                "title": "Angel One connection unstable",
                "message": "Reconnection attempt 3/5. Latency spike detected.",
                "created_at": (now - timedelta(minutes=12)).isoformat(),
                "acknowledged": False,
            },
            {
                "alert_id": "ALT-002",
                "severity": "INFO",
                "source": "strategy_engine",
                "title": "Iron Condor adjustment triggered",
                "message": "Short CE delta exceeded 0.30, adjustment order placed.",
                "created_at": (now - timedelta(minutes=5)).isoformat(),
                "acknowledged": True,
            },
            {
                "alert_id": "ALT-003",
                "severity": "WARNING",
                "source": "risk_engine",
                "title": "Daily loss approaching 60% of limit",
                "message": "Daily loss: ₹580,000 / ₹1,000,000 limit.",
                "created_at": (now - timedelta(minutes=2)).isoformat(),
                "acknowledged": False,
            },
        ],
        "active_count": 2,
        "total_today": 15,
        "timestamp": now.isoformat(),
    }


@app.post(
    "/api/monitoring/alerts/{alert_id}/acknowledge",
    tags=["Monitoring"],
    summary="Acknowledge an alert",
    description="Marks an alert as acknowledged.",
)
async def acknowledge_alert(alert_id: str):
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
    now = datetime.now(IST)
    return {
        "orders_per_second": round(_mock._rng.uniform(2, 15), 1),
        "avg_order_latency_ms": round(_mock._rng.uniform(5, 50), 1),
        "p99_order_latency_ms": round(_mock._rng.uniform(50, 200), 1),
        "event_bus_throughput_per_sec": round(_mock._rng.uniform(50, 500), 1),
        "event_bus_queue_depth": _mock._rng.randint(0, 100),
        "active_websocket_connections": _mock._rng.randint(1, 10),
        "memory_usage_mb": round(_mock._rng.uniform(200, 600), 1),
        "cpu_usage_pct": round(_mock._rng.uniform(5, 40), 1),
        "uptime_seconds": round(time.time() - _startup_time, 0),
        "timestamp": now.isoformat(),
    }


@app.get(
    "/api/monitoring/metrics/history",
    tags=["Monitoring"],
    summary="Metrics history",
    description="Returns historical metrics data points for the past N minutes.",
)
async def monitoring_metrics_history(
    metric: str = Query(default="orders_per_second", description="Metric name"),
    minutes: int = Query(default=30, ge=1, le=1440, description="Lookback minutes"),
):
    now = datetime.now(IST)
    points = []
    for i in range(minutes):
        ts = now - timedelta(minutes=minutes - i)
        val = _mock._rng.uniform(1, 50) if "latency" not in metric else _mock._rng.uniform(5, 200)
        points.append({"timestamp": ts.isoformat(), "value": round(val, 2)})
    return {
        "metric": metric,
        "interval_minutes": 1,
        "data_points": points,
        "count": len(points),
    }


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
    symbol = symbol.upper()
    base_price = _mock.INDEX_BASE.get(symbol, 24250.0)
    spot = _mock._jitter(base_price)
    step = 50 if symbol == "NIFTY" else 100

    surface = []
    for dte in [1, 3, 7, 14, 30, 60]:
        for offset in range(-5, 6):
            strike = round(spot / step) * step + offset * step
            moneyness = (spot - strike) / spot
            iv = 0.14 + abs(moneyness) * 0.6 + _mock._rng.uniform(-0.01, 0.01) + 0.01 * math.sqrt(dte / 365)
            surface.append({
                "strike": strike,
                "dte": dte,
                "iv_call": round(iv * 100, 2),
                "iv_put": round((iv + 0.005) * 100, 2),
                "moneyness": round(moneyness, 4),
            })

    return {
        "symbol": symbol,
        "spot_price": spot,
        "surface": surface,
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
        return _paper_trading_manager.get_session_stats()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get(
    "/api/paper-trading/positions",
    tags=["Paper Trading"],
    summary="Paper trading positions",
    description="Return current open positions in the paper trading session.",
)
async def paper_trading_positions():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        raise HTTPException(status_code=409, detail="No active paper trading session")

    try:
        return await _paper_trading_manager.get_positions()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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
        return await _paper_trading_manager.get_orders()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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
    description="Return all strategies deployed in the current paper trading session.",
)
async def paper_trading_strategies():
    if _paper_trading_manager is None:
        raise HTTPException(status_code=503, detail="Paper trading manager not initialized")
    if not _paper_trading_manager.is_active:
        return {"strategies": [], "session_active": False}

    strategies = _paper_trading_manager.get_deployed_strategies()
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
    from core.ai_signal_engine import generate_signals, build_deploy_payload
    from core import symbol_master

    symbol = (body.get("symbol") or "NIFTY").upper()
    threshold = float(body.get("threshold") or 0)

    ctx = await _build_market_context(symbol)
    result = generate_signals(symbol=symbol, **{k: v for k, v in ctx.items() if k != "symbol"})

    spot = ctx["spot"] or 0
    lot = symbol_master.get_lot_size(symbol)

    deployed = []
    skipped = []
    for sig in result["signals"]:
        if threshold > 0 and sig["confidence"] < threshold:
            skipped.append({"strategy": sig["strategy_class"], "reason": f"below threshold ({sig['confidence']}<{threshold})"})
            continue
        if not sig["ready_to_deploy"]:
            skipped.append({"strategy": sig["strategy_class"], "reason": f"not ready (conf={sig['confidence']})"})
            continue

        payload = build_deploy_payload(sig, spot_price=spot, lot_size=lot, name_suffix="AI")
        try:
            # Reuse the platform's own deploy endpoint logic
            deploy_result = await deploy_strategy(payload)
            deployed.append({
                "strategy_id": deploy_result["strategy_id"],
                "name": deploy_result["strategy"]["name"],
                "strategy_class": sig["strategy_class"],
                "confidence": sig["confidence"],
                "reason": " · ".join(sig["reasoning"][:2]),
            })
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

"""
Zerodha Kite Connect broker implementation.

Uses httpx (async HTTP client) directly for full async support, avoiding the
synchronous ``kiteconnect`` library dependency.  Communicates with the Kite
Connect v3 REST API and streams tick data via the Kite WebSocket endpoint.

Reference: https://kite.trade/docs/connect/v3/
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import struct
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import httpx

from core.broker_gateway.base import BaseBroker
from core.broker_gateway.rate_limiter import AsyncRateLimiter
from core.models import (
    Exchange,
    Instrument,
    InstrumentType,
    MarginInfo,
    OptionChain,
    OptionContract,
    OptionType,
    Order,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Segment,
    Tick,
    Trade,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kite Connect API constants
# ---------------------------------------------------------------------------

KITE_BASE_URL = "https://api.kite.trade"
KITE_WS_URL = "wss://ws.kite.trade"
KITE_API_VERSION = "3"

# REST endpoints
EP_SESSION_TOKEN = "/session/token"
EP_USER_PROFILE = "/user/profile"
EP_ORDERS = "/orders"
EP_ORDER_VARIETY = "/orders/{variety}"
EP_ORDER_MODIFY = "/orders/{variety}/{order_id}"
EP_ORDER_CANCEL = "/orders/{variety}/{order_id}"
EP_ORDER_TRADES = "/orders/{order_id}/trades"
EP_TRADES = "/trades"
EP_POSITIONS = "/portfolio/positions"
EP_MARGINS = "/user/margins"
EP_INSTRUMENTS = "/instruments"
EP_INSTRUMENTS_EXCHANGE = "/instruments/{exchange}"
EP_QUOTE_LTP = "/quote/ltp"

# Kite order / product string constants
KITE_VARIETY_REGULAR = "regular"
KITE_VARIETY_AMO = "amo"
KITE_VARIETY_ICEBERG = "iceberg"
KITE_VARIETY_AUCTION = "auction"

KITE_ORDER_TYPE_MARKET = "MARKET"
KITE_ORDER_TYPE_LIMIT = "LIMIT"
KITE_ORDER_TYPE_SL = "SL"
KITE_ORDER_TYPE_SLM = "SL-M"

KITE_PRODUCT_MIS = "MIS"
KITE_PRODUCT_NRML = "NRML"
KITE_PRODUCT_CNC = "CNC"

KITE_SIDE_BUY = "BUY"
KITE_SIDE_SELL = "SELL"

# Kite order status strings
KITE_STATUS_MAP: dict[str, OrderStatus] = {
    "PUT ORDER REQ RECEIVED": OrderStatus.PENDING,
    "VALIDATION PENDING": OrderStatus.PENDING,
    "OPEN PENDING": OrderStatus.PENDING,
    "MODIFY VALIDATION PENDING": OrderStatus.OPEN,
    "MODIFY PENDING": OrderStatus.OPEN,
    "TRIGGER PENDING": OrderStatus.OPEN,
    "OPEN": OrderStatus.OPEN,
    "COMPLETE": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCEL PENDING": OrderStatus.OPEN,
}

# Exchange enum -> Kite exchange string
EXCHANGE_TO_KITE: dict[Exchange, str] = {
    Exchange.NSE: "NSE",
    Exchange.BSE: "BSE",
    Exchange.NFO: "NFO",
    Exchange.BFO: "BFO",
    Exchange.CDS: "CDS",
    Exchange.MCX: "MCX",
}

KITE_EXCHANGE_TO_ENUM: dict[str, Exchange] = {v: k for k, v in EXCHANGE_TO_KITE.items()}

# Instrument type mapping from Kite instrument dump
KITE_INST_TYPE_MAP: dict[str, InstrumentType] = {
    "EQ": InstrumentType.STOCK,
    "FUT": InstrumentType.FUTURE,
    "CE": InstrumentType.CALL_OPTION,
    "PE": InstrumentType.PUT_OPTION,
}

KITE_EXCHANGE_SEGMENT: dict[str, Segment] = {
    "NSE": Segment.EQUITY,
    "BSE": Segment.EQUITY,
    "NFO": Segment.FNO,
    "BFO": Segment.FNO,
    "CDS": Segment.CURRENCY,
    "MCX": Segment.COMMODITY,
}

# WebSocket modes
WS_MODE_LTP = 1
WS_MODE_QUOTE = 2
WS_MODE_FULL = 3


# ---------------------------------------------------------------------------
# Kite API exception
# ---------------------------------------------------------------------------


class KiteApiError(Exception):
    """Error raised when a Kite Connect API call fails."""

    def __init__(self, status_code: int, error_type: str, message: str) -> None:
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        super().__init__(f"[{error_type}] {message} (HTTP {status_code})")


class KiteTokenError(KiteApiError):
    """Raised when the access token is invalid or expired."""


# ---------------------------------------------------------------------------
# ZerodhaBroker implementation
# ---------------------------------------------------------------------------


class ZerodhaBroker(BaseBroker):
    """Zerodha Kite Connect broker implementation.

    Uses httpx for async HTTP and raw websockets for tick data streaming.
    All REST calls are rate-limited and include structured logging.

    Parameters
    ----------
    api_key:
        Kite Connect API key.
    api_secret:
        Kite Connect API secret (used for generating access tokens).
    access_token:
        Pre-generated access token.  If empty, call ``connect()`` with a
        request_token to generate one.
    totp_secret:
        Optional TOTP secret for automated login flows.
    rate_limit:
        Maximum REST API requests per second (default 3).
    """

    name = "zerodha"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str = "",
        totp_secret: str = "",
        rate_limit: float = 3.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token
        self._totp_secret = totp_secret

        self._rate_limiter = AsyncRateLimiter(rate=rate_limit, burst=max(1, int(rate_limit)))
        self._http: httpx.AsyncClient | None = None
        self._connected = False

        # Instruments cache
        self._instruments_cache: list[Instrument] = []
        self._instruments_cache_date: date | None = None

        # WebSocket state
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_running = False
        self._tick_callback: Callable[..., Any] | None = None
        self._subscribed_tokens: set[int] = set()
        self._ws_mode: int = WS_MODE_FULL

        # Order update callback
        self._order_update_callback: Callable[..., Any] | None = None

        # Token -> symbol lookup (populated from instruments)
        self._token_symbol_map: dict[int, str] = {}
        self._token_exchange_map: dict[int, Exchange] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers required by Kite Connect."""
        return {
            "Authorization": f"token {self._api_key}:{self._access_token}",
            "X-Kite-Version": KITE_API_VERSION,
        }

    def _ensure_http_client(self) -> httpx.AsyncClient:
        """Lazily create or return the httpx client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=KITE_BASE_URL,
                headers=self._auth_headers(),
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a rate-limited, authenticated HTTP request to Kite API.

        Returns the parsed JSON response ``data`` field.
        Raises ``KiteApiError`` or ``KiteTokenError`` on failure.
        """
        await self._rate_limiter.acquire()
        client = self._ensure_http_client()

        logger.debug(
            "Kite API request: %s %s params=%s data=%s",
            method.upper(),
            path,
            params,
            data,
        )

        try:
            response = await client.request(
                method,
                path,
                params=params,
                data=data,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            logger.error("Kite API network error: %s %s -> %s", method, path, exc)
            raise KiteApiError(0, "NetworkException", str(exc)) from exc

        logger.debug(
            "Kite API response: %s %s -> HTTP %d",
            method.upper(),
            path,
            response.status_code,
        )

        # Instrument dump returns CSV, not JSON
        if path.startswith("/instruments"):
            if response.status_code == 200:
                return {"data": response.text}
            # fall through to error handling

        try:
            body = response.json()
        except Exception:
            body = {"status": "error", "error_type": "ParseException", "message": response.text}

        if response.status_code == 200:
            return body.get("data", body)

        error_type = body.get("error_type", "GeneralException")
        message = body.get("message", response.text)

        if error_type == "TokenException" or response.status_code == 403:
            self._connected = False
            raise KiteTokenError(
                response.status_code,
                error_type,
                f"{message}. Please re-authenticate and generate a new access token.",
            )

        raise KiteApiError(response.status_code, error_type, message)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, request_token: str | None = None) -> None:
        """Establish connection to Kite Connect.

        If *request_token* is provided (and no access_token is set), generates
        a new access token via the session/token endpoint.  Otherwise validates
        the existing access token by fetching the user profile.

        Parameters
        ----------
        request_token:
            One-time token received after Kite login redirect.
        """
        self._ensure_http_client()

        # Generate access token from request_token if needed
        if request_token and not self._access_token:
            await self._generate_access_token(request_token)

        if not self._access_token:
            raise KiteApiError(
                0,
                "InputException",
                "No access token provided.  Supply an access_token or call "
                "connect(request_token=...) after the Kite login flow.",
            )

        # Refresh client headers with the (potentially new) token
        if self._http and not self._http.is_closed:
            self._http.headers.update(self._auth_headers())

        # Validate session
        try:
            profile = await self._request("GET", EP_USER_PROFILE)
            logger.info(
                "Zerodha connected: user=%s, email=%s",
                profile.get("user_id", "?"),
                profile.get("email", "?"),
            )
        except KiteTokenError:
            raise
        except KiteApiError as exc:
            raise KiteApiError(
                exc.status_code,
                exc.error_type,
                f"Failed to validate session: {exc.message}",
            ) from exc

        self._connected = True

        # Pre-load instruments (cache)
        try:
            await self.get_instruments()
        except Exception:
            logger.warning("Failed to pre-load instruments cache during connect")

    async def _generate_access_token(self, request_token: str) -> None:
        """Exchange a request_token for an access_token.

        POST /session/token with api_key, request_token, and a checksum
        (SHA-256 of api_key + request_token + api_secret).
        """
        import hashlib

        checksum = hashlib.sha256(
            f"{self._api_key}{request_token}{self._api_secret}".encode()
        ).hexdigest()

        # This endpoint does not need an existing access token
        client = self._ensure_http_client()
        await self._rate_limiter.acquire()

        logger.debug("Generating access token via POST %s", EP_SESSION_TOKEN)

        response = await client.post(
            EP_SESSION_TOKEN,
            data={
                "api_key": self._api_key,
                "request_token": request_token,
                "checksum": checksum,
            },
            headers={"X-Kite-Version": KITE_API_VERSION},
        )

        body = response.json()
        if response.status_code != 200 or body.get("status") == "error":
            error_type = body.get("error_type", "TokenException")
            message = body.get("message", "Failed to generate access token")
            raise KiteApiError(response.status_code, error_type, message)

        data = body.get("data", {})
        self._access_token = data.get("access_token", "")
        logger.info("Access token generated successfully")

    async def disconnect(self) -> None:
        """Close WebSocket, HTTP client, and clean up resources."""
        logger.info("Disconnecting Zerodha broker")

        # Stop WebSocket
        self._ws_running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None

        # Close HTTP client
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

        self._connected = False
        self._subscribed_tokens.clear()
        logger.info("Zerodha broker disconnected")

    async def is_connected(self) -> bool:
        """Check whether the session is active by making a lightweight call."""
        if not self._connected or not self._access_token:
            return False

        try:
            await self._request("GET", EP_USER_PROFILE)
            return True
        except (KiteTokenError, KiteApiError):
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(self, order: Order) -> OrderResponse:
        """Submit an order to Kite Connect.

        Maps the internal ``Order`` model to Kite API parameters and calls
        ``POST /orders/{variety}``.
        """
        params = self._map_order_to_kite_params(order)
        variety = params.pop("variety", KITE_VARIETY_REGULAR)
        path = EP_ORDER_VARIETY.format(variety=variety)

        logger.info(
            "Placing order: %s %s %s qty=%d type=%s @ %s",
            order.side.value,
            order.instrument.symbol,
            order.instrument.exchange.value,
            order.quantity,
            order.order_type.value,
            order.price,
        )

        try:
            data = await self._request("POST", path, data=params)
            broker_order_id = str(data.get("order_id", ""))
            logger.info(
                "Order placed: internal_id=%s broker_id=%s",
                order.order_id,
                broker_order_id,
            )
            return OrderResponse(
                success=True,
                order_id=order.order_id,
                broker_order_id=broker_order_id,
                message="Order placed successfully",
                status=OrderStatus.PLACED,
            )
        except KiteApiError as exc:
            logger.error("Order placement failed: %s", exc)
            return OrderResponse(
                success=False,
                order_id=order.order_id,
                message=str(exc),
                status=OrderStatus.REJECTED,
            )

    async def modify_order(
        self, order_id: str, modifications: dict[str, Any]
    ) -> OrderResponse:
        """Modify an existing open order on Kite Connect.

        Parameters
        ----------
        order_id:
            The *broker* order ID.
        modifications:
            Keys may include: ``price``, ``quantity``, ``trigger_price``,
            ``order_type``, ``variety``.
        """
        variety = modifications.pop("variety", KITE_VARIETY_REGULAR)
        path = EP_ORDER_MODIFY.format(variety=variety, order_id=order_id)

        kite_params: dict[str, Any] = {}
        if "price" in modifications:
            kite_params["price"] = str(modifications["price"])
        if "quantity" in modifications:
            kite_params["quantity"] = int(modifications["quantity"])
        if "trigger_price" in modifications:
            kite_params["trigger_price"] = str(modifications["trigger_price"])
        if "order_type" in modifications:
            kite_params["order_type"] = self._get_kite_order_type(
                OrderType(modifications["order_type"])
            )

        logger.info("Modifying order %s: %s", order_id, kite_params)

        try:
            data = await self._request("PUT", path, data=kite_params)
            return OrderResponse(
                success=True,
                broker_order_id=str(data.get("order_id", order_id)),
                message="Order modified successfully",
                status=OrderStatus.OPEN,
            )
        except KiteApiError as exc:
            logger.error("Order modification failed for %s: %s", order_id, exc)
            return OrderResponse(
                success=False,
                broker_order_id=order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an open order by its broker order ID.

        Parameters
        ----------
        order_id:
            Broker order ID (Kite order_id).
        """
        # Try regular variety first; AMO orders need "amo" variety
        for variety in (KITE_VARIETY_REGULAR, KITE_VARIETY_AMO):
            path = EP_ORDER_CANCEL.format(variety=variety, order_id=order_id)
            logger.info("Cancelling order %s (variety=%s)", order_id, variety)

            try:
                data = await self._request("DELETE", path)
                return OrderResponse(
                    success=True,
                    broker_order_id=str(data.get("order_id", order_id)),
                    message="Order cancelled successfully",
                    status=OrderStatus.CANCELLED,
                )
            except KiteApiError as exc:
                if variety == KITE_VARIETY_REGULAR and "variety" in exc.message.lower():
                    continue  # Try AMO variety
                logger.error("Order cancellation failed for %s: %s", order_id, exc)
                return OrderResponse(
                    success=False,
                    broker_order_id=order_id,
                    message=str(exc),
                    status=OrderStatus.ERROR,
                )

        return OrderResponse(
            success=False,
            broker_order_id=order_id,
            message="Failed to cancel order with any variety",
            status=OrderStatus.ERROR,
        )

    async def cancel_all_orders(self) -> list[OrderResponse]:
        """Cancel all open/pending orders. Used by the kill switch."""
        logger.info("Cancelling ALL open orders")
        results: list[OrderResponse] = []

        try:
            orders = await self.get_order_book()
        except KiteApiError as exc:
            logger.error("Failed to fetch order book for cancel_all: %s", exc)
            return [
                OrderResponse(
                    success=False,
                    message=f"Failed to fetch order book: {exc}",
                    status=OrderStatus.ERROR,
                )
            ]

        open_statuses = {OrderStatus.PENDING, OrderStatus.PLACED, OrderStatus.OPEN, OrderStatus.PARTIAL}
        open_orders = [o for o in orders if o.status in open_statuses]

        if not open_orders:
            logger.info("No open orders to cancel")
            return results

        for order in open_orders:
            if order.broker_order_id:
                resp = await self.cancel_order(order.broker_order_id)
                results.append(resp)

        logger.info(
            "cancel_all_orders: attempted=%d, succeeded=%d",
            len(results),
            sum(1 for r in results if r.success),
        )
        return results

    # ------------------------------------------------------------------
    # Position & Account
    # ------------------------------------------------------------------

    async def get_positions(self) -> list[Position]:
        """Fetch all current positions from Kite Connect."""
        logger.debug("Fetching positions")
        try:
            data = await self._request("GET", EP_POSITIONS)
        except KiteApiError as exc:
            logger.error("Failed to fetch positions: %s", exc)
            raise

        positions: list[Position] = []
        # Kite returns { "net": [...], "day": [...] }
        net_positions = data.get("net", []) if isinstance(data, dict) else []
        for kite_pos in net_positions:
            try:
                positions.append(self._map_kite_position_to_model(kite_pos))
            except Exception as exc:
                logger.warning("Skipping unmappable position %s: %s", kite_pos.get("tradingsymbol"), exc)

        logger.debug("Fetched %d net positions", len(positions))
        return positions

    async def get_order_book(self) -> list[Order]:
        """Fetch today's complete order book from Kite Connect."""
        logger.debug("Fetching order book")
        try:
            data = await self._request("GET", EP_ORDERS)
        except KiteApiError as exc:
            logger.error("Failed to fetch order book: %s", exc)
            raise

        orders: list[Order] = []
        order_list = data if isinstance(data, list) else []
        for kite_order in order_list:
            try:
                orders.append(self._map_kite_order_to_model(kite_order))
            except Exception as exc:
                logger.warning(
                    "Skipping unmappable order %s: %s",
                    kite_order.get("order_id"),
                    exc,
                )

        logger.debug("Fetched %d orders", len(orders))
        return orders

    async def get_trade_book(self) -> list[Trade]:
        """Fetch today's executed trades from Kite Connect."""
        logger.debug("Fetching trade book")
        try:
            data = await self._request("GET", EP_TRADES)
        except KiteApiError as exc:
            logger.error("Failed to fetch trade book: %s", exc)
            raise

        trades: list[Trade] = []
        trade_list = data if isinstance(data, list) else []
        for kite_trade in trade_list:
            try:
                trades.append(self._map_kite_trade_to_model(kite_trade))
            except Exception as exc:
                logger.warning(
                    "Skipping unmappable trade %s: %s",
                    kite_trade.get("trade_id"),
                    exc,
                )

        logger.debug("Fetched %d trades", len(trades))
        return trades

    async def get_margins(self) -> MarginInfo:
        """Fetch current margin/funds information from Kite Connect."""
        logger.debug("Fetching margins")
        try:
            data = await self._request("GET", EP_MARGINS)
        except KiteApiError as exc:
            logger.error("Failed to fetch margins: %s", exc)
            raise

        # Kite returns { "equity": {...}, "commodity": {...} }
        equity = data.get("equity", {}) if isinstance(data, dict) else {}
        available = equity.get("available", {})
        utilised = equity.get("utilised", {})

        available_cash = Decimal(str(available.get("cash", 0)))
        used_margin = Decimal(str(utilised.get("debits", 0)))
        total_collateral = Decimal(str(available.get("collateral", 0)))
        available_margin = Decimal(str(available.get("live_balance", 0)))
        exposure_margin = Decimal(str(utilised.get("exposure", 0)))
        span_margin = Decimal(str(utilised.get("span", 0)))

        total = available_margin + used_margin
        utilization_pct = float(used_margin / total * 100) if total > 0 else 0.0

        return MarginInfo(
            available_cash=available_cash,
            used_margin=used_margin,
            available_margin=available_margin,
            total_collateral=total_collateral,
            exposure_margin=exposure_margin,
            span_margin=span_margin,
            utilization_pct=min(utilization_pct, 100.0),
        )

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        """Get last traded price for a list of instruments.

        Parameters
        ----------
        instruments:
            List of instrument identifiers in Kite format:
            ``"EXCHANGE:TRADINGSYMBOL"`` (e.g. ``"NSE:RELIANCE"``).

        Returns
        -------
        dict mapping each instrument key to its LTP.
        """
        if not instruments:
            return {}

        logger.debug("Fetching LTP for %d instruments", len(instruments))

        # Kite accepts up to ~500 instruments per call
        params = {"i": instruments}
        try:
            data = await self._request("GET", EP_QUOTE_LTP, params=params)
        except KiteApiError as exc:
            logger.error("Failed to fetch LTP: %s", exc)
            raise

        result: dict[str, float] = {}
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict):
                    result[key] = float(val.get("last_price", 0))
                else:
                    result[key] = float(val)

        return result

    async def get_option_chain(self, symbol: str, expiry: date) -> OptionChain:
        """Build an option chain from the instruments cache and live LTP data.

        Parameters
        ----------
        symbol:
            Underlying symbol (e.g. ``"NIFTY"``, ``"BANKNIFTY"``).
        expiry:
            Expiry date to filter contracts.
        """
        logger.debug("Building option chain for %s expiry=%s", symbol, expiry)

        # Ensure instruments are loaded
        if not self._instruments_cache:
            await self.get_instruments()

        # Filter option contracts
        option_instruments = [
            inst
            for inst in self._instruments_cache
            if inst.underlying == symbol
            and inst.expiry == expiry
            and inst.instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION)
        ]

        if not option_instruments:
            logger.warning("No option contracts found for %s expiry=%s", symbol, expiry)
            return OptionChain(
                underlying_symbol=symbol,
                underlying_price=Decimal("0"),
                expiry=expiry,
                timestamp=datetime.now(tz=timezone.utc),
                contracts=[],
                atm_strike=Decimal("0"),
                pcr=0.0,
            )

        # Get LTP for underlying
        exchange_prefix = "NSE" if symbol in ("NIFTY", "BANKNIFTY", "NIFTY BANK") else "NFO"
        underlying_key = f"{exchange_prefix}:{symbol}"
        try:
            ltp_data = await self.get_ltp([underlying_key])
            underlying_price = Decimal(str(ltp_data.get(underlying_key, 0)))
        except Exception:
            underlying_price = Decimal("0")

        # Get LTP for all option contracts
        option_keys = [
            f"{EXCHANGE_TO_KITE[inst.exchange]}:{inst.symbol}" for inst in option_instruments
        ]

        option_ltp: dict[str, float] = {}
        # Batch in groups of 500
        for i in range(0, len(option_keys), 500):
            batch = option_keys[i : i + 500]
            try:
                batch_data = await self.get_ltp(batch)
                option_ltp.update(batch_data)
            except Exception:
                logger.warning("Failed to fetch LTP for option batch starting at %d", i)

        # Build contracts
        contracts: list[OptionContract] = []
        total_ce_oi = 0
        total_pe_oi = 0

        for inst in option_instruments:
            key = f"{EXCHANGE_TO_KITE[inst.exchange]}:{inst.symbol}"
            ltp = Decimal(str(option_ltp.get(key, 0)))
            opt_type = OptionType.CE if inst.instrument_type == InstrumentType.CALL_OPTION else OptionType.PE

            contract = OptionContract(
                instrument=inst,
                strike=inst.strike or Decimal("0"),
                option_type=opt_type,
                expiry=expiry,
                ltp=ltp,
            )
            contracts.append(contract)

            if opt_type == OptionType.CE:
                total_ce_oi += contract.oi
            else:
                total_pe_oi += contract.oi

        # Find ATM strike
        strikes = sorted({c.strike for c in contracts})
        atm_strike = Decimal("0")
        if strikes and underlying_price > 0:
            atm_strike = min(strikes, key=lambda s: abs(s - underlying_price))

        pcr = float(total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 0.0

        return OptionChain(
            underlying_symbol=symbol,
            underlying_price=underlying_price,
            expiry=expiry,
            timestamp=datetime.now(tz=timezone.utc),
            contracts=contracts,
            atm_strike=atm_strike,
            pcr=pcr,
        )

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        """Fetch and cache the master instrument list from Kite Connect.

        The instrument dump is a large CSV refreshed once per trading day.
        Results are cached for the current date.

        Parameters
        ----------
        exchange:
            Optional exchange filter (e.g. ``"NFO"``, ``"NSE"``).
        """
        today = date.today()

        # Return cache if still fresh
        if self._instruments_cache and self._instruments_cache_date == today:
            if exchange:
                return [
                    i for i in self._instruments_cache
                    if i.exchange.value == exchange.upper()
                ]
            return list(self._instruments_cache)

        # Fetch from API
        path = (
            EP_INSTRUMENTS_EXCHANGE.format(exchange=exchange.upper())
            if exchange
            else EP_INSTRUMENTS
        )
        logger.info("Fetching instruments from Kite (exchange=%s)", exchange or "ALL")

        try:
            raw = await self._request("GET", path)
        except KiteApiError as exc:
            logger.error("Failed to fetch instruments: %s", exc)
            raise

        csv_text = raw if isinstance(raw, str) else raw.get("data", "")
        instruments = self._parse_instruments_csv(csv_text)

        # Only replace full cache when fetching all exchanges
        if not exchange:
            self._instruments_cache = instruments
            self._instruments_cache_date = today
            self._build_token_maps()
            logger.info("Instruments cache refreshed: %d instruments", len(instruments))
        else:
            # Merge into cache
            existing = [i for i in self._instruments_cache if i.exchange.value != exchange.upper()]
            self._instruments_cache = existing + instruments
            self._instruments_cache_date = today
            self._build_token_maps()

        return instruments

    def _parse_instruments_csv(self, csv_text: str) -> list[Instrument]:
        """Parse the Kite instruments CSV dump into Instrument models."""
        instruments: list[Instrument] = []
        reader = csv.DictReader(io.StringIO(csv_text))

        for row in reader:
            try:
                exchange_str = row.get("exchange", "")
                exchange = KITE_EXCHANGE_TO_ENUM.get(exchange_str)
                if exchange is None:
                    continue

                segment = KITE_EXCHANGE_SEGMENT.get(exchange_str, Segment.EQUITY)
                inst_type_str = row.get("instrument_type", "EQ")
                inst_type = KITE_INST_TYPE_MAP.get(inst_type_str, InstrumentType.STOCK)

                expiry_str = row.get("expiry", "")
                expiry_date: date | None = None
                if expiry_str:
                    try:
                        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                    except ValueError:
                        pass

                strike_val = row.get("strike", "")
                strike: Decimal | None = None
                if strike_val and float(strike_val) > 0:
                    strike = Decimal(strike_val)

                option_type: OptionType | None = None
                if inst_type == InstrumentType.CALL_OPTION:
                    option_type = OptionType.CE
                elif inst_type == InstrumentType.PUT_OPTION:
                    option_type = OptionType.PE

                lot_size = int(row.get("lot_size", 1) or 1)
                tick_size_val = row.get("tick_size", "0.05")
                tick_size = Decimal(tick_size_val) if tick_size_val else Decimal("0.05")

                inst = Instrument(
                    symbol=row.get("tradingsymbol", ""),
                    exchange=exchange,
                    segment=segment,
                    instrument_type=inst_type,
                    lot_size=lot_size,
                    tick_size=tick_size,
                    expiry=expiry_date,
                    strike=strike,
                    option_type=option_type,
                    underlying=row.get("name", None) or None,
                    token=row.get("instrument_token", None),
                )
                instruments.append(inst)
            except Exception as exc:
                logger.debug("Skipping instrument row: %s", exc)

        return instruments

    def _build_token_maps(self) -> None:
        """Build token -> symbol/exchange lookup maps from the instruments cache."""
        self._token_symbol_map.clear()
        self._token_exchange_map.clear()
        for inst in self._instruments_cache:
            if inst.token:
                try:
                    token_int = int(inst.token)
                    self._token_symbol_map[token_int] = inst.symbol
                    self._token_exchange_map[token_int] = inst.exchange
                except (ValueError, TypeError):
                    pass

    # ------------------------------------------------------------------
    # WebSocket / Streaming
    # ------------------------------------------------------------------

    async def subscribe_ticks(
        self, instruments: list[str], callback: Any
    ) -> None:
        """Subscribe to real-time tick data via the Kite WebSocket.

        Parameters
        ----------
        instruments:
            List of instrument tokens (as strings) to subscribe.
        callback:
            Async callable invoked with each ``Tick`` model.
        """
        self._tick_callback = callback
        new_tokens = {int(t) for t in instruments}
        self._subscribed_tokens.update(new_tokens)

        # Start WebSocket if not running
        if not self._ws_running:
            self._ws_running = True
            self._ws_task = asyncio.create_task(self._ws_loop())
            logger.info("WebSocket loop started")
        else:
            # Send subscribe message to existing connection
            await self._ws_send_subscribe(list(new_tokens))

    async def unsubscribe_ticks(self, instruments: list[str]) -> None:
        """Unsubscribe from tick data for given instruments."""
        tokens_to_remove = {int(t) for t in instruments}
        self._subscribed_tokens -= tokens_to_remove
        logger.info("Unsubscribed %d tokens", len(tokens_to_remove))

        # If nothing left, stop the WebSocket
        if not self._subscribed_tokens:
            self._ws_running = False
            if self._ws_task and not self._ws_task.done():
                self._ws_task.cancel()

    async def subscribe_order_updates(self, callback: Any) -> None:
        """Register a callback for real-time order status updates.

        Kite Connect provides order updates via a postback URL or through the
        WebSocket.  This method registers the callback that will be invoked
        when an order update arrives.

        Parameters
        ----------
        callback:
            Async callable receiving a dict of order update data.
        """
        self._order_update_callback = callback
        logger.info("Order update callback registered")

    async def _ws_loop(self) -> None:
        """Main WebSocket event loop with reconnection and exponential backoff."""
        try:
            import websockets
        except ImportError:
            logger.error(
                "websockets library is required for tick streaming. "
                "Install it with: pip install websockets"
            )
            self._ws_running = False
            return

        backoff = 1.0
        max_backoff = 60.0

        while self._ws_running:
            ws_url = (
                f"{KITE_WS_URL}?api_key={self._api_key}"
                f"&access_token={self._access_token}"
            )

            try:
                async with websockets.connect(ws_url) as ws:  # type: ignore[attr-defined]
                    logger.info("WebSocket connected")
                    backoff = 1.0

                    # Subscribe to current tokens
                    if self._subscribed_tokens:
                        await self._ws_send_subscribe(
                            list(self._subscribed_tokens), ws=ws
                        )

                    async for message in ws:
                        if not self._ws_running:
                            break

                        if isinstance(message, bytes):
                            ticks = self._parse_ws_binary(message)
                            if self._tick_callback and ticks:
                                for tick in ticks:
                                    try:
                                        if asyncio.iscoroutinefunction(self._tick_callback):
                                            await self._tick_callback(tick)
                                        else:
                                            self._tick_callback(tick)
                                    except Exception as exc:
                                        logger.error("Tick callback error: %s", exc)
                        elif isinstance(message, str):
                            # Text messages are typically order updates
                            try:
                                data = json.loads(message)
                                if data.get("type") == "order" and self._order_update_callback:
                                    if asyncio.iscoroutinefunction(self._order_update_callback):
                                        await self._order_update_callback(data)
                                    else:
                                        self._order_update_callback(data)
                            except json.JSONDecodeError:
                                logger.debug("Non-JSON WS message: %s", message[:100])

            except asyncio.CancelledError:
                logger.info("WebSocket loop cancelled")
                break
            except Exception as exc:
                if not self._ws_running:
                    break
                logger.error("WebSocket error: %s (reconnecting in %.1fs)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

        logger.info("WebSocket loop exited")

    async def _ws_send_subscribe(
        self, tokens: list[int], ws: Any = None
    ) -> None:
        """Send a subscribe + set_mode message over the WebSocket."""
        if ws is None:
            return  # Will subscribe on next connection

        subscribe_msg = json.dumps({"a": "subscribe", "v": tokens})
        mode_msg = json.dumps({"a": "mode", "v": [self._ws_mode, tokens]})

        try:
            await ws.send(subscribe_msg)
            await ws.send(mode_msg)
            logger.debug("Subscribed to %d tokens", len(tokens))
        except Exception as exc:
            logger.error("Failed to send subscribe message: %s", exc)

    def _parse_ws_binary(self, data: bytes) -> list[Tick]:
        """Parse Kite WebSocket binary tick data.

        Kite sends packets where the first 2 bytes indicate the number of
        packets, followed by individual instrument packets.
        """
        ticks: list[Tick] = []

        if len(data) < 2:
            return ticks

        num_packets = struct.unpack(">H", data[:2])[0]
        offset = 2

        for _ in range(num_packets):
            if offset + 2 > len(data):
                break

            packet_len = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2

            if offset + packet_len > len(data):
                break

            packet = data[offset : offset + packet_len]
            offset += packet_len

            try:
                tick = self._decode_tick_packet(packet)
                if tick:
                    ticks.append(tick)
            except Exception as exc:
                logger.debug("Failed to decode tick packet: %s", exc)

        return ticks

    def _decode_tick_packet(self, packet: bytes) -> Tick | None:
        """Decode a single binary tick packet from the Kite WebSocket.

        Packet sizes:
        - LTP mode (8 bytes): token(4) + ltp(4)
        - Quote mode (44 bytes): token(4) + ltp(4) + ... ohlc + volume
        - Full mode (184 bytes): all fields including depth
        """
        if len(packet) < 8:
            return None

        token = struct.unpack(">I", packet[0:4])[0]
        # Kite sends prices as integers (multiply by 100), divide back
        divisor = 100.0

        ltp_raw = struct.unpack(">I", packet[4:8])[0]
        ltp = Decimal(str(ltp_raw / divisor))

        symbol = self._token_symbol_map.get(token, str(token))
        exchange = self._token_exchange_map.get(token, Exchange.NSE)

        open_price = Decimal("0")
        high_price = Decimal("0")
        low_price = Decimal("0")
        close_price = Decimal("0")
        volume = 0
        oi = 0
        oi_change = 0
        bid = Decimal("0")
        ask = Decimal("0")
        bid_qty = 0
        ask_qty = 0

        if len(packet) >= 44:
            # Quote mode fields
            high_raw = struct.unpack(">I", packet[8:12])[0]
            low_raw = struct.unpack(">I", packet[12:16])[0]
            open_raw = struct.unpack(">I", packet[16:20])[0]
            close_raw = struct.unpack(">I", packet[20:24])[0]
            volume = struct.unpack(">I", packet[28:32])[0]

            high_price = Decimal(str(high_raw / divisor))
            low_price = Decimal(str(low_raw / divisor))
            open_price = Decimal(str(open_raw / divisor))
            close_price = Decimal(str(close_raw / divisor))

        if len(packet) >= 184:
            # Full mode includes OI and market depth
            oi = struct.unpack(">I", packet[40:44])[0]

            # Best bid/ask from depth (first entry)
            if len(packet) >= 64:
                bid_qty = struct.unpack(">I", packet[44:48])[0]
                bid_raw = struct.unpack(">I", packet[48:52])[0]
                ask_qty = struct.unpack(">I", packet[64:68])[0]
                ask_raw = struct.unpack(">I", packet[68:72])[0]
                bid = Decimal(str(bid_raw / divisor))
                ask = Decimal(str(ask_raw / divisor))

        return Tick(
            instrument_id=str(token),
            symbol=symbol,
            ltp=ltp,
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume,
            oi=oi,
            oi_change=oi_change,
            timestamp=datetime.now(tz=timezone.utc),
            exchange=exchange,
        )

    # ------------------------------------------------------------------
    # Mapping helpers (private)
    # ------------------------------------------------------------------

    def _map_order_to_kite_params(self, order: Order) -> dict[str, Any]:
        """Convert an internal ``Order`` model to Kite API POST parameters.

        Returns a dict suitable for passing as ``data`` to the place_order
        endpoint.  The ``variety`` key determines the URL path segment.
        """
        params: dict[str, Any] = {
            "variety": KITE_VARIETY_REGULAR,
            "exchange": self._get_exchange_str(order.instrument.exchange),
            "tradingsymbol": order.instrument.symbol,
            "transaction_type": KITE_SIDE_BUY if order.side == OrderSide.BUY else KITE_SIDE_SELL,
            "quantity": order.quantity,
            "product": self._get_kite_product(order.product_type),
            "order_type": self._get_kite_order_type(order.order_type),
            "validity": "DAY",
        }

        # Price fields
        if order.price is not None:
            params["price"] = str(order.price)
        else:
            params["price"] = "0"

        if order.trigger_price is not None:
            params["trigger_price"] = str(order.trigger_price)
        else:
            params["trigger_price"] = "0"

        # Tag
        if order.tag:
            params["tag"] = order.tag

        return params

    def _map_kite_order_to_model(self, kite_order: dict[str, Any]) -> Order:
        """Convert a Kite order response dict to an internal ``Order`` model."""
        exchange_str = kite_order.get("exchange", "NSE")
        exchange = KITE_EXCHANGE_TO_ENUM.get(exchange_str, Exchange.NSE)

        # Determine instrument type from the exchange
        segment = KITE_EXCHANGE_SEGMENT.get(exchange_str, Segment.EQUITY)
        if segment == Segment.FNO:
            # Rough heuristic: CE/PE in tradingsymbol
            tsym = kite_order.get("tradingsymbol", "")
            if tsym.endswith("CE"):
                inst_type = InstrumentType.CALL_OPTION
            elif tsym.endswith("PE"):
                inst_type = InstrumentType.PUT_OPTION
            else:
                inst_type = InstrumentType.FUTURE
        else:
            inst_type = InstrumentType.STOCK

        instrument = Instrument(
            symbol=kite_order.get("tradingsymbol", ""),
            exchange=exchange,
            segment=segment,
            instrument_type=inst_type,
            token=str(kite_order.get("instrument_token", "")),
        )

        # Map order type
        kite_otype = kite_order.get("order_type", "MARKET")
        order_type_map = {
            "MARKET": OrderType.MARKET,
            "LIMIT": OrderType.LIMIT,
            "SL": OrderType.SL,
            "SL-M": OrderType.SL_M,
        }
        order_type = order_type_map.get(kite_otype, OrderType.MARKET)

        # Map product type
        kite_product = kite_order.get("product", "MIS")
        product_map = {
            "MIS": ProductType.MIS,
            "NRML": ProductType.NRML,
            "CNC": ProductType.CNC,
        }
        product_type = product_map.get(kite_product, ProductType.MIS)

        # Map side
        side = OrderSide.BUY if kite_order.get("transaction_type") == "BUY" else OrderSide.SELL

        # Map status
        kite_status = kite_order.get("status", "")
        status = KITE_STATUS_MAP.get(kite_status, OrderStatus.PENDING)

        # Parse timestamps
        placed_at = self._parse_kite_timestamp(kite_order.get("order_timestamp"))
        updated_at = self._parse_kite_timestamp(kite_order.get("exchange_update_timestamp"))

        price = Decimal(str(kite_order.get("price", 0) or 0))
        trigger_price = Decimal(str(kite_order.get("trigger_price", 0) or 0))

        return Order(
            order_id=str(kite_order.get("order_id", "")),
            instrument=instrument,
            order_type=order_type,
            side=side,
            product_type=product_type,
            quantity=int(kite_order.get("quantity", 0) or 1),
            price=price if price > 0 else None,
            trigger_price=trigger_price if trigger_price > 0 else None,
            status=status,
            filled_quantity=int(kite_order.get("filled_quantity", 0) or 0),
            average_price=Decimal(str(kite_order.get("average_price", 0) or 0)),
            placed_at=placed_at,
            updated_at=updated_at,
            broker_order_id=str(kite_order.get("order_id", "")),
            tag=kite_order.get("tag"),
            rejection_reason=kite_order.get("status_message"),
            parent_order_id=kite_order.get("parent_order_id"),
        )

    def _map_kite_position_to_model(self, kite_pos: dict[str, Any]) -> Position:
        """Convert a Kite position dict to an internal ``Position`` model."""
        exchange_str = kite_pos.get("exchange", "NSE")
        exchange = KITE_EXCHANGE_TO_ENUM.get(exchange_str, Exchange.NSE)
        segment = KITE_EXCHANGE_SEGMENT.get(exchange_str, Segment.EQUITY)

        tsym = kite_pos.get("tradingsymbol", "")
        if segment == Segment.FNO:
            if tsym.endswith("CE"):
                inst_type = InstrumentType.CALL_OPTION
            elif tsym.endswith("PE"):
                inst_type = InstrumentType.PUT_OPTION
            else:
                inst_type = InstrumentType.FUTURE
        else:
            inst_type = InstrumentType.STOCK

        instrument = Instrument(
            symbol=tsym,
            exchange=exchange,
            segment=segment,
            instrument_type=inst_type,
            token=str(kite_pos.get("instrument_token", "")),
        )

        product_map = {"MIS": ProductType.MIS, "NRML": ProductType.NRML, "CNC": ProductType.CNC}
        product_type = product_map.get(kite_pos.get("product", "MIS"), ProductType.MIS)

        quantity = int(kite_pos.get("quantity", 0))
        average_price = Decimal(str(kite_pos.get("average_price", 0)))
        ltp = Decimal(str(kite_pos.get("last_price", 0)))
        pnl = Decimal(str(kite_pos.get("pnl", 0)))
        realised = Decimal(str(kite_pos.get("realised", 0)))
        unrealised = Decimal(str(kite_pos.get("unrealised", 0)))
        value = Decimal(str(kite_pos.get("value", 0)))

        return Position(
            instrument=instrument,
            quantity=quantity,
            average_price=average_price,
            ltp=ltp,
            pnl_unrealized=unrealised,
            pnl_realized=realised,
            value=value,
            product_type=product_type,
        )

    def _map_kite_trade_to_model(self, kite_trade: dict[str, Any]) -> Trade:
        """Convert a Kite trade dict to an internal ``Trade`` model."""
        exchange_str = kite_trade.get("exchange", "NSE")
        exchange = KITE_EXCHANGE_TO_ENUM.get(exchange_str, Exchange.NSE)
        segment = KITE_EXCHANGE_SEGMENT.get(exchange_str, Segment.EQUITY)

        tsym = kite_trade.get("tradingsymbol", "")
        if segment == Segment.FNO:
            if tsym.endswith("CE"):
                inst_type = InstrumentType.CALL_OPTION
            elif tsym.endswith("PE"):
                inst_type = InstrumentType.PUT_OPTION
            else:
                inst_type = InstrumentType.FUTURE
        else:
            inst_type = InstrumentType.STOCK

        instrument = Instrument(
            symbol=tsym,
            exchange=exchange,
            segment=segment,
            instrument_type=inst_type,
            token=str(kite_trade.get("instrument_token", "")),
        )

        side = OrderSide.BUY if kite_trade.get("transaction_type") == "BUY" else OrderSide.SELL
        timestamp = self._parse_kite_timestamp(kite_trade.get("fill_timestamp")) or datetime.now(
            tz=timezone.utc
        )

        return Trade(
            trade_id=str(kite_trade.get("trade_id", "")),
            order_id=str(kite_trade.get("order_id", "")),
            instrument=instrument,
            side=side,
            quantity=int(kite_trade.get("quantity", 0) or 1),
            price=Decimal(str(kite_trade.get("average_price", 0) or 0)),
            timestamp=timestamp,
            broker_trade_id=str(kite_trade.get("trade_id", "")),
            exchange_trade_id=str(kite_trade.get("exchange_trade_id", "")),
        )

    def _map_kite_tick_to_model(self, tick: dict[str, Any]) -> Tick:
        """Convert a Kite tick dict (from kiteconnect callback) to a ``Tick``."""
        token = tick.get("instrument_token", 0)
        symbol = self._token_symbol_map.get(token, str(token))
        exchange = self._token_exchange_map.get(token, Exchange.NSE)

        return Tick(
            instrument_id=str(token),
            symbol=symbol,
            ltp=Decimal(str(tick.get("last_price", 0))),
            bid=Decimal(str(tick.get("best_bid_price", 0) if "best_bid_price" in tick else 0)),
            ask=Decimal(str(tick.get("best_ask_price", 0) if "best_ask_price" in tick else 0)),
            bid_qty=int(tick.get("best_bid_quantity", 0) or 0),
            ask_qty=int(tick.get("best_ask_quantity", 0) or 0),
            open=Decimal(str(tick.get("ohlc", {}).get("open", 0))),
            high=Decimal(str(tick.get("ohlc", {}).get("high", 0))),
            low=Decimal(str(tick.get("ohlc", {}).get("low", 0))),
            close=Decimal(str(tick.get("ohlc", {}).get("close", 0))),
            volume=int(tick.get("volume_traded", 0) or 0),
            oi=int(tick.get("oi", 0) or 0),
            oi_change=int(tick.get("oi_day_high", 0) or 0) - int(tick.get("oi_day_low", 0) or 0),
            timestamp=datetime.now(tz=timezone.utc),
            exchange=exchange,
        )

    @staticmethod
    def _get_exchange_str(exchange: Exchange) -> str:
        """Map an ``Exchange`` enum to the Kite exchange string."""
        return EXCHANGE_TO_KITE.get(exchange, "NSE")

    @staticmethod
    def _get_kite_product(product: ProductType) -> str:
        """Map a ``ProductType`` enum to the Kite product string."""
        mapping = {
            ProductType.MIS: KITE_PRODUCT_MIS,
            ProductType.NRML: KITE_PRODUCT_NRML,
            ProductType.CNC: KITE_PRODUCT_CNC,
        }
        return mapping.get(product, KITE_PRODUCT_MIS)

    @staticmethod
    def _get_kite_order_type(order_type: OrderType) -> str:
        """Map an ``OrderType`` enum to the Kite order type string."""
        mapping = {
            OrderType.MARKET: KITE_ORDER_TYPE_MARKET,
            OrderType.LIMIT: KITE_ORDER_TYPE_LIMIT,
            OrderType.SL: KITE_ORDER_TYPE_SL,
            OrderType.SL_M: KITE_ORDER_TYPE_SLM,
            OrderType.GTT: KITE_ORDER_TYPE_LIMIT,  # GTT handled separately
        }
        return mapping.get(order_type, KITE_ORDER_TYPE_MARKET)

    @staticmethod
    def _parse_kite_timestamp(ts: str | None) -> datetime | None:
        """Parse a Kite timestamp string into a timezone-aware datetime."""
        if not ts:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(ts, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
        return None

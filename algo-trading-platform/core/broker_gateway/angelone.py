"""
Angel One SmartAPI broker implementation.

Implements the ``BaseBroker`` interface for Angel One's SmartAPI, providing
authentication (API key + client ID + password + TOTP), JWT token management
with automatic refresh, order management, position/margin queries, market-data
access, and WebSocket streaming.

REST API base : https://apiconnect.angelone.in
WebSocket     : wss://smartapisocket.angelone.in/smart-stream
Rate limit    : 10 requests / second (configurable)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import struct
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

import httpx

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
    Trade,
)

from .base import BaseBroker
from .rate_limiter import AsyncRateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://apiconnect.angelone.in"
_WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
_INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Default timeout for HTTP requests (seconds).
_HTTP_TIMEOUT = 30.0

# Token refresh buffer -- refresh this many seconds before actual expiry.
_TOKEN_REFRESH_BUFFER_SECS = 60


# ---------------------------------------------------------------------------
# Mapping helpers -- Angel One API <-> platform models
# ---------------------------------------------------------------------------


class _AngelOrderVariety(str, Enum):
    """Angel One order varieties."""

    NORMAL = "NORMAL"
    STOPLOSS = "STOPLOSS"
    AMO = "AMO"
    ROBO = "ROBO"


class _AngelOrderType(str, Enum):
    """Angel One order types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOPLOSS_LIMIT = "STOPLOSS_LIMIT"
    STOPLOSS_MARKET = "STOPLOSS_MARKET"


class _AngelProductType(str, Enum):
    """Angel One product types."""

    INTRADAY = "INTRADAY"
    CARRYFORWARD = "CARRYFORWARD"
    DELIVERY = "DELIVERY"


# --- Mapping: platform OrderType -> Angel One variety + ordertype -----------

_ORDER_TYPE_MAP: dict[OrderType, tuple[_AngelOrderVariety, _AngelOrderType]] = {
    OrderType.MARKET: (_AngelOrderVariety.NORMAL, _AngelOrderType.MARKET),
    OrderType.LIMIT: (_AngelOrderVariety.NORMAL, _AngelOrderType.LIMIT),
    OrderType.SL: (_AngelOrderVariety.STOPLOSS, _AngelOrderType.STOPLOSS_LIMIT),
    OrderType.SL_M: (_AngelOrderVariety.STOPLOSS, _AngelOrderType.STOPLOSS_MARKET),
}

_ANGEL_ORDER_TYPE_REVERSE: dict[tuple[str, str], OrderType] = {
    ("NORMAL", "MARKET"): OrderType.MARKET,
    ("NORMAL", "LIMIT"): OrderType.LIMIT,
    ("STOPLOSS", "STOPLOSS_LIMIT"): OrderType.SL,
    ("STOPLOSS", "STOPLOSS_MARKET"): OrderType.SL_M,
}

# --- Mapping: platform ProductType <-> Angel One producttype ----------------

_PRODUCT_TYPE_MAP: dict[ProductType, _AngelProductType] = {
    ProductType.MIS: _AngelProductType.INTRADAY,
    ProductType.NRML: _AngelProductType.CARRYFORWARD,
    ProductType.CNC: _AngelProductType.DELIVERY,
}

_PRODUCT_TYPE_REVERSE: dict[str, ProductType] = {
    "INTRADAY": ProductType.MIS,
    "CARRYFORWARD": ProductType.NRML,
    "DELIVERY": ProductType.CNC,
}

# --- Mapping: Angel One order status -> platform OrderStatus ----------------

_ORDER_STATUS_MAP: dict[str, OrderStatus] = {
    "open": OrderStatus.OPEN,
    "pending": OrderStatus.PENDING,
    "trigger pending": OrderStatus.PENDING,
    "open pending": OrderStatus.PENDING,
    "validation pending": OrderStatus.PENDING,
    "put order req received": OrderStatus.PENDING,
    "modify pending": OrderStatus.PENDING,
    "cancel pending": OrderStatus.PENDING,
    "modify validation pending": OrderStatus.PENDING,
    "after market order req received": OrderStatus.PENDING,
    "complete": OrderStatus.FILLED,
    "traded": OrderStatus.FILLED,
    "rejected": OrderStatus.REJECTED,
    "cancelled": OrderStatus.CANCELLED,
}

# --- Exchange mapping -------------------------------------------------------

_EXCHANGE_MAP: dict[str, Exchange] = {
    "NSE": Exchange.NSE,
    "BSE": Exchange.BSE,
    "NFO": Exchange.NFO,
    "BFO": Exchange.BFO,
    "CDS": Exchange.CDS,
    "MCX": Exchange.MCX,
}

_EXCHANGE_REVERSE: dict[Exchange, str] = {v: k for k, v in _EXCHANGE_MAP.items()}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _get_local_ip() -> str:
    """Best-effort retrieval of the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_mac_address() -> str:
    """Return MAC address as a colon-separated hex string."""
    mac_int = uuid.getnode()
    return ":".join(f"{(mac_int >> (8 * i)) & 0xFF:02x}" for i in range(5, -1, -1))


def _decimal_or_zero(value: Any) -> Decimal:
    """Safely convert a value to Decimal, defaulting to zero."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _parse_angel_timestamp(ts_str: str | None) -> datetime | None:
    """Parse Angel One timestamp strings into datetime objects."""
    if not ts_str:
        return None
    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str, fmt).replace(
                tzinfo=timezone(timedelta(hours=5, minutes=30))
            )
        except ValueError:
            continue
    logger.warning("Could not parse Angel One timestamp: %s", ts_str)
    return None


# ---------------------------------------------------------------------------
# AngelOneBroker
# ---------------------------------------------------------------------------


class AngelOneBroker(BaseBroker):
    """Angel One SmartAPI broker gateway.

    Provides full ``BaseBroker`` interface implementation using Angel One's
    REST API for orders, positions, margins, and market data, plus WebSocket
    streaming for real-time ticks and order updates.

    Args:
        api_key: Angel One SmartAPI API key.
        client_id: Angel One client / user ID.
        password: Trading password.
        totp_secret: TOTP secret for two-factor authentication.
        rate_limit: Maximum requests per second (default 10).
    """

    name: str = "angelone"

    def __init__(
        self,
        api_key: str,
        client_id: str = "",
        password: str = "",
        totp_secret: str = "",
        rate_limit: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._client_id = client_id
        self._password = password
        self._totp_secret = totp_secret

        # Auth state
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._feed_token: str | None = None
        self._token_expiry: datetime | None = None

        # HTTP client -- created lazily in connect()
        self._client: httpx.AsyncClient | None = None

        # Rate limiter
        self._rate_limiter = AsyncRateLimiter(rate=rate_limit, burst=int(rate_limit))

        # Instrument cache: token -> Instrument
        self._instrument_cache: dict[str, Instrument] = {}
        self._instrument_cache_loaded = False

        # Network identity (cached once)
        self._local_ip = _get_local_ip()
        self._public_ip = ""  # populated on first request or left empty
        self._mac_address = _get_mac_address()

        # WebSocket state
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_running = False
        self._tick_callbacks: list[Callable[..., Any]] = []
        self._order_callbacks: list[Callable[..., Any]] = []
        self._subscribed_tokens: set[str] = set()

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build the standard Angel One request headers."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": self._local_ip,
            "X-ClientPublicIP": self._public_ip or self._local_ip,
            "X-MACAddress": self._mac_address,
            "X-PrivateKey": self._api_key,
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return the httpx client, creating one if necessary."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                timeout=httpx.Timeout(_HTTP_TIMEOUT),
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        """Execute a rate-limited HTTP request to the Angel One API.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API endpoint path (e.g., ``/rest/auth/...``).
            json_body: Optional JSON body for POST requests.
            authenticated: Whether to include the auth header and
                auto-refresh the token if expired.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
            RuntimeError: If the API returns a non-success status.
        """
        if authenticated:
            await self._ensure_token_valid()

        await self._rate_limiter.acquire()
        client = await self._ensure_client()

        try:
            response = await client.request(
                method,
                path,
                headers=self._headers(),
                json=json_body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Angel One API error: %s %s -> %d %s",
                method,
                path,
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise

        data: dict[str, Any] = response.json()

        # Angel One wraps responses in {"status": true/false, "message": ..., "data": ...}
        if data.get("status") is False:
            error_msg = data.get("message", "Unknown Angel One API error")
            error_code = data.get("errorcode", "")
            logger.error(
                "Angel One API logical error on %s %s: [%s] %s",
                method,
                path,
                error_code,
                error_msg,
            )
            raise RuntimeError(
                f"Angel One API error [{error_code}]: {error_msg}"
            )

        return data

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _generate_totp(self) -> str:
        """Generate a TOTP code from the stored secret.

        Uses a pure-Python HMAC-SHA1 implementation so that the ``pyotp``
        dependency is optional.  If ``pyotp`` is installed it will be
        preferred for correctness.
        """
        try:
            import pyotp  # type: ignore[import-untyped]

            return pyotp.TOTP(self._totp_secret).now()
        except ImportError:
            pass

        # Fallback: manual TOTP (RFC 6238 / HOTP with SHA-1, 30s window)
        import base64
        import hmac

        key = base64.b32decode(self._totp_secret.upper() + "=" * (-len(self._totp_secret) % 8))
        counter = int(time.time()) // 30
        counter_bytes = struct.pack(">Q", counter)
        mac = hmac.new(key, counter_bytes, hashlib.sha1).digest()
        offset = mac[-1] & 0x0F
        code = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(code % 10**6).zfill(6)

    async def _login(self) -> None:
        """Authenticate with Angel One and store JWT tokens."""
        totp = self._generate_totp() if self._totp_secret else ""

        payload = {
            "clientcode": self._client_id,
            "password": self._password,
            "totp": totp,
        }

        data = await self._request(
            "POST",
            "/rest/auth/angelbroking/user/v1/loginByPassword",
            json_body=payload,
            authenticated=False,
        )

        token_data = data.get("data", {})
        self._access_token = token_data.get("jwtToken", "")
        self._refresh_token = token_data.get("refreshToken", "")
        self._feed_token = token_data.get("feedToken", "")

        # Angel One JWTs typically expire after ~24 hours; we assume 23 hours
        # to be safe and rely on auto-refresh.
        self._token_expiry = datetime.now(timezone.utc) + timedelta(hours=23)

        logger.info("Angel One login successful for client %s", self._client_id)

    async def _refresh_access_token(self) -> None:
        """Refresh the JWT access token using the refresh token."""
        if not self._refresh_token:
            logger.warning("No refresh token available; performing full login")
            await self._login()
            return

        payload = {"refreshToken": self._refresh_token}

        try:
            data = await self._request(
                "POST",
                "/rest/auth/angelbroking/jwt/v1/generateTokens",
                json_body=payload,
                authenticated=False,
            )
            token_data = data.get("data", {})
            self._access_token = token_data.get("jwtToken", self._access_token)
            self._refresh_token = token_data.get("refreshToken", self._refresh_token)
            self._feed_token = token_data.get("feedToken", self._feed_token)
            self._token_expiry = datetime.now(timezone.utc) + timedelta(hours=23)
            logger.info("Angel One token refreshed for client %s", self._client_id)
        except Exception:
            logger.warning("Token refresh failed; falling back to full login", exc_info=True)
            await self._login()

    async def _ensure_token_valid(self) -> None:
        """Ensure the access token is present and not about to expire."""
        if not self._access_token:
            await self._login()
            return

        if self._token_expiry is None:
            await self._login()
            return

        now = datetime.now(timezone.utc)
        if now >= self._token_expiry - timedelta(seconds=_TOKEN_REFRESH_BUFFER_SECS):
            logger.info("Access token nearing expiry; refreshing")
            await self._refresh_access_token()

    # ------------------------------------------------------------------
    # Instrument mapping helpers
    # ------------------------------------------------------------------

    async def _load_instrument_master(self) -> None:
        """Download the Angel One instrument master file and populate cache."""
        if self._instrument_cache_loaded:
            return

        logger.info("Downloading Angel One instrument master...")
        client = await self._ensure_client()
        try:
            resp = await client.get(_INSTRUMENT_MASTER_URL, timeout=60.0)
            resp.raise_for_status()
            instruments_raw: list[dict[str, Any]] = resp.json()
        except Exception:
            logger.error("Failed to download instrument master", exc_info=True)
            return

        for raw in instruments_raw:
            try:
                inst = self._parse_master_instrument(raw)
                if inst and inst.token:
                    self._instrument_cache[inst.token] = inst
            except Exception:
                continue  # skip malformed entries silently

        self._instrument_cache_loaded = True
        logger.info(
            "Angel One instrument master loaded: %d instruments cached",
            len(self._instrument_cache),
        )

    @staticmethod
    def _parse_master_instrument(raw: dict[str, Any]) -> Instrument | None:
        """Convert a single Angel One instrument master record to an ``Instrument``."""
        token = raw.get("token", "")
        symbol = raw.get("symbol", "")
        exchange_str = raw.get("exch_seg", "")
        inst_type_str = raw.get("instrumenttype", "")
        name = raw.get("name", "")

        if not token or not symbol:
            return None

        exchange = _EXCHANGE_MAP.get(exchange_str)
        if exchange is None:
            return None

        # Determine segment
        if exchange in (Exchange.NFO, Exchange.BFO):
            segment = Segment.FNO
        elif exchange == Exchange.CDS:
            segment = Segment.CURRENCY
        elif exchange == Exchange.MCX:
            segment = Segment.COMMODITY
        else:
            segment = Segment.EQUITY

        # Determine instrument type
        if inst_type_str in ("OPTIDX", "OPTSTK"):
            option_suffix = symbol[-2:] if len(symbol) >= 2 else ""
            if option_suffix == "CE":
                instrument_type = InstrumentType.CALL_OPTION
                option_type: OptionType | None = OptionType.CE
            elif option_suffix == "PE":
                instrument_type = InstrumentType.PUT_OPTION
                option_type = OptionType.PE
            else:
                instrument_type = InstrumentType.CALL_OPTION
                option_type = OptionType.CE
        elif inst_type_str in ("FUTIDX", "FUTSTK"):
            instrument_type = InstrumentType.FUTURE
            option_type = None
        else:
            instrument_type = InstrumentType.STOCK
            option_type = None

        # Parse expiry
        expiry: date | None = None
        expiry_str = raw.get("expiry", "")
        if expiry_str:
            for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
                try:
                    expiry = datetime.strptime(expiry_str, fmt).date()
                    break
                except ValueError:
                    continue

        # Parse strike
        strike: Decimal | None = None
        strike_val = raw.get("strike", "")
        if strike_val and instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            try:
                strike_dec = Decimal(str(strike_val))
                # Angel One stores strikes * 100 for some instruments
                if strike_dec > Decimal("100000"):
                    strike_dec = strike_dec / Decimal("100")
                strike = strike_dec
            except Exception:
                strike = None

        lot_size = int(raw.get("lotsize", "1") or "1")
        tick_size_val = raw.get("tick_size", "0.05")
        tick_size = Decimal(str(tick_size_val)) if tick_size_val else Decimal("0.05")

        return Instrument(
            symbol=symbol,
            exchange=exchange,
            segment=segment,
            instrument_type=instrument_type,
            lot_size=max(lot_size, 1),
            tick_size=tick_size,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
            underlying=name or None,
            token=str(token),
        )

    def _find_instrument_by_symbol(
        self, symbol: str, exchange: str
    ) -> Instrument | None:
        """Look up an instrument from the cache by symbol and exchange."""
        for inst in self._instrument_cache.values():
            if inst.symbol == symbol and inst.exchange.value == exchange:
                return inst
        return None

    # ------------------------------------------------------------------
    # Order-model mapping
    # ------------------------------------------------------------------

    def _order_to_angel_payload(self, order: Order) -> dict[str, Any]:
        """Convert a platform ``Order`` to an Angel One place-order payload."""
        variety, angel_order_type = _ORDER_TYPE_MAP.get(
            order.order_type, (_AngelOrderVariety.NORMAL, _AngelOrderType.MARKET)
        )
        angel_product = _PRODUCT_TYPE_MAP.get(
            order.product_type, _AngelProductType.INTRADAY
        )
        exchange_str = _EXCHANGE_REVERSE.get(order.instrument.exchange, "NSE")
        symbol_token = order.instrument.token or ""

        return {
            "variety": variety.value,
            "tradingsymbol": order.instrument.symbol,
            "symboltoken": symbol_token,
            "transactiontype": order.side.value,
            "exchange": exchange_str,
            "ordertype": angel_order_type.value,
            "producttype": angel_product.value,
            "duration": "DAY",
            "price": str(order.price or 0),
            "triggerprice": str(order.trigger_price or 0),
            "quantity": str(order.quantity),
        }

    def _angel_order_to_model(self, raw: dict[str, Any]) -> Order:
        """Convert an Angel One order-book entry to a platform ``Order``."""
        exchange_str = raw.get("exchange", "NSE")
        exchange = _EXCHANGE_MAP.get(exchange_str, Exchange.NSE)
        symbol = raw.get("tradingsymbol", "")
        token = raw.get("symboltoken", "")

        # Try to find instrument from cache; fall back to minimal construction
        instrument = self._instrument_cache.get(token)
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                exchange=exchange,
                segment=Segment.EQUITY,
                instrument_type=InstrumentType.STOCK,
                token=token,
            )

        variety = raw.get("variety", "NORMAL")
        angel_otype = raw.get("ordertype", "MARKET")
        order_type = _ANGEL_ORDER_TYPE_REVERSE.get(
            (variety, angel_otype), OrderType.MARKET
        )

        product_str = raw.get("producttype", "INTRADAY")
        product_type = _PRODUCT_TYPE_REVERSE.get(product_str, ProductType.MIS)

        side = OrderSide.BUY if raw.get("transactiontype") == "BUY" else OrderSide.SELL

        status_str = (raw.get("orderstatus") or "pending").lower()
        status = _ORDER_STATUS_MAP.get(status_str, OrderStatus.PENDING)

        return Order(
            order_id=raw.get("orderid", uuid.uuid4().hex),
            instrument=instrument,
            order_type=order_type,
            side=side,
            product_type=product_type,
            quantity=int(raw.get("quantity", 0) or 0),
            price=_decimal_or_zero(raw.get("price")),
            trigger_price=_decimal_or_zero(raw.get("triggerprice")),
            status=status,
            filled_quantity=int(raw.get("filledshares", 0) or 0),
            average_price=_decimal_or_zero(raw.get("averageprice")),
            placed_at=_parse_angel_timestamp(raw.get("orderentrydate")),
            updated_at=_parse_angel_timestamp(raw.get("lastupdatedtime")),
            broker_order_id=raw.get("orderid"),
            rejection_reason=raw.get("text"),
        )

    def _angel_trade_to_model(self, raw: dict[str, Any]) -> Trade:
        """Convert an Angel One trade-book entry to a platform ``Trade``."""
        exchange_str = raw.get("exchange", "NSE")
        exchange = _EXCHANGE_MAP.get(exchange_str, Exchange.NSE)
        symbol = raw.get("tradingsymbol", "")
        token = raw.get("symboltoken", "")

        instrument = self._instrument_cache.get(token)
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                exchange=exchange,
                segment=Segment.EQUITY,
                instrument_type=InstrumentType.STOCK,
                token=token,
            )

        side = OrderSide.BUY if raw.get("transactiontype") == "BUY" else OrderSide.SELL
        timestamp = _parse_angel_timestamp(raw.get("filltime")) or datetime.now(
            timezone(timedelta(hours=5, minutes=30))
        )

        return Trade(
            trade_id=raw.get("tradeid", uuid.uuid4().hex),
            order_id=raw.get("orderid", ""),
            instrument=instrument,
            side=side,
            quantity=int(raw.get("fillsize", 0) or 0),
            price=_decimal_or_zero(raw.get("fillprice")),
            timestamp=timestamp,
            broker_trade_id=raw.get("tradeid"),
            exchange_trade_id=raw.get("exchangetradeid"),
        )

    def _angel_position_to_model(self, raw: dict[str, Any]) -> Position:
        """Convert an Angel One position entry to a platform ``Position``."""
        exchange_str = raw.get("exchange", "NSE")
        exchange = _EXCHANGE_MAP.get(exchange_str, Exchange.NSE)
        symbol = raw.get("tradingsymbol", "")
        token = raw.get("symboltoken", "")

        instrument = self._instrument_cache.get(token)
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                exchange=exchange,
                segment=Segment.EQUITY,
                instrument_type=InstrumentType.STOCK,
                token=token,
            )

        product_str = raw.get("producttype", "INTRADAY")
        product_type = _PRODUCT_TYPE_REVERSE.get(product_str, ProductType.MIS)

        buy_qty = int(raw.get("buyqty", 0) or 0)
        sell_qty = int(raw.get("sellqty", 0) or 0)
        net_qty = buy_qty - sell_qty

        buy_avg = _decimal_or_zero(raw.get("buyavgprice"))
        sell_avg = _decimal_or_zero(raw.get("sellavgprice"))
        avg_price = buy_avg if net_qty > 0 else sell_avg

        ltp = _decimal_or_zero(raw.get("ltp"))
        pnl = _decimal_or_zero(raw.get("pnl"))
        realised = _decimal_or_zero(raw.get("realised"))
        unrealised = _decimal_or_zero(raw.get("unrealised"))

        return Position(
            instrument=instrument,
            quantity=net_qty,
            average_price=avg_price,
            ltp=ltp,
            pnl_unrealized=unrealised,
            pnl_realized=realised,
            value=ltp * Decimal(str(abs(net_qty))),
            product_type=product_type,
        )

    # ==================================================================
    # BaseBroker interface implementation
    # ==================================================================

    # ── Connection Lifecycle ─────────────────────────────────────────

    async def connect(self) -> None:
        """Authenticate with Angel One SmartAPI and initialise the session."""
        await self._ensure_client()
        await self._login()
        logger.info("AngelOneBroker connected for client %s", self._client_id)

    async def disconnect(self) -> None:
        """Close the HTTP client and cancel any running WebSocket tasks."""
        self._ws_running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

        self._access_token = None
        self._refresh_token = None
        self._feed_token = None
        self._token_expiry = None
        logger.info("AngelOneBroker disconnected")

    async def is_connected(self) -> bool:
        """Return True if we hold a valid (non-expired) access token."""
        if not self._access_token:
            return False
        if self._token_expiry is None:
            return False
        return datetime.now(timezone.utc) < self._token_expiry

    # ── Order Management ─────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResponse:
        """Place an order via Angel One SmartAPI.

        Args:
            order: Platform ``Order`` model.

        Returns:
            ``OrderResponse`` with success flag and broker order ID.
        """
        payload = self._order_to_angel_payload(order)

        try:
            data = await self._request(
                "POST",
                "/rest/secure/angelbroking/order/v1/placeOrder",
                json_body=payload,
            )
            resp_data = data.get("data", {})
            broker_order_id = resp_data.get("orderid", "")
            logger.info(
                "Order placed: %s %s %s qty=%d -> broker_id=%s",
                order.side.value,
                order.instrument.symbol,
                order.order_type.value,
                order.quantity,
                broker_order_id,
            )
            return OrderResponse(
                success=True,
                order_id=order.order_id,
                broker_order_id=broker_order_id,
                message=data.get("message", "Order placed successfully"),
                status=OrderStatus.PLACED,
            )
        except Exception as exc:
            logger.error("Failed to place order: %s", exc, exc_info=True)
            return OrderResponse(
                success=False,
                order_id=order.order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def modify_order(
        self, order_id: str, modifications: dict[str, Any]
    ) -> OrderResponse:
        """Modify an existing open order.

        Args:
            order_id: Broker order ID to modify.
            modifications: Dict of fields to modify. Supported keys:
                ``quantity``, ``price``, ``trigger_price``, ``order_type``.

        Returns:
            ``OrderResponse`` indicating success or failure.
        """
        payload: dict[str, Any] = {"orderid": order_id}

        if "quantity" in modifications:
            payload["quantity"] = str(modifications["quantity"])
        if "price" in modifications:
            payload["price"] = str(modifications["price"])
        if "trigger_price" in modifications:
            payload["triggerprice"] = str(modifications["trigger_price"])
        if "order_type" in modifications:
            ot = modifications["order_type"]
            if isinstance(ot, OrderType):
                _, angel_ot = _ORDER_TYPE_MAP.get(
                    ot, (_AngelOrderVariety.NORMAL, _AngelOrderType.MARKET)
                )
                payload["ordertype"] = angel_ot.value

        # Angel One requires variety for modify; default to NORMAL
        payload.setdefault("variety", "NORMAL")

        try:
            data = await self._request(
                "POST",
                "/rest/secure/angelbroking/order/v1/modifyOrder",
                json_body=payload,
            )
            resp_data = data.get("data", {})
            return OrderResponse(
                success=True,
                broker_order_id=resp_data.get("orderid", order_id),
                message=data.get("message", "Order modified"),
                status=OrderStatus.OPEN,
            )
        except Exception as exc:
            logger.error("Failed to modify order %s: %s", order_id, exc)
            return OrderResponse(
                success=False,
                broker_order_id=order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an open order.

        Args:
            order_id: Broker order ID to cancel.
        """
        payload = {
            "variety": "NORMAL",
            "orderid": order_id,
        }

        try:
            data = await self._request(
                "POST",
                "/rest/secure/angelbroking/order/v1/cancelOrder",
                json_body=payload,
            )
            return OrderResponse(
                success=True,
                broker_order_id=order_id,
                message=data.get("message", "Order cancelled"),
                status=OrderStatus.CANCELLED,
            )
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return OrderResponse(
                success=False,
                broker_order_id=order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def cancel_all_orders(self) -> list[OrderResponse]:
        """Cancel all open orders. Used by the kill switch.

        Fetches the order book, identifies open/pending orders, and cancels
        each one individually.
        """
        orders = await self.get_order_book()
        open_orders = [
            o
            for o in orders
            if o.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.PARTIAL)
        ]

        if not open_orders:
            logger.info("No open orders to cancel")
            return []

        results: list[OrderResponse] = []
        for order in open_orders:
            bid = order.broker_order_id or order.order_id
            resp = await self.cancel_order(bid)
            results.append(resp)

        logger.info("Cancel-all: %d orders processed", len(results))
        return results

    # ── Position & Account ───────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        """Fetch all current positions from Angel One."""
        data = await self._request(
            "GET",
            "/rest/secure/angelbroking/order/v1/getPosition",
        )
        positions_raw = data.get("data", [])
        if not positions_raw:
            return []

        return [self._angel_position_to_model(p) for p in positions_raw]

    async def get_order_book(self) -> list[Order]:
        """Fetch today's complete order book."""
        data = await self._request(
            "GET",
            "/rest/secure/angelbroking/order/v1/getOrderBook",
        )
        orders_raw = data.get("data", [])
        if not orders_raw:
            return []

        return [self._angel_order_to_model(o) for o in orders_raw]

    async def get_trade_book(self) -> list[Trade]:
        """Fetch today's executed trades."""
        data = await self._request(
            "GET",
            "/rest/secure/angelbroking/order/v1/getTradeBook",
        )
        trades_raw = data.get("data", [])
        if not trades_raw:
            return []

        return [self._angel_trade_to_model(t) for t in trades_raw]

    async def get_margins(self) -> MarginInfo:
        """Fetch current margin/funds information from Angel One RMS."""
        data = await self._request(
            "GET",
            "/rest/secure/angelbroking/user/v1/getRMS",
        )
        rms = data.get("data", {})
        if not rms:
            return MarginInfo()

        available_cash = _decimal_or_zero(rms.get("availablecash"))
        used_margin = _decimal_or_zero(rms.get("utiliseddebits"))
        available_margin = _decimal_or_zero(rms.get("net"))
        total_collateral = _decimal_or_zero(rms.get("collateral"))
        exposure_margin = _decimal_or_zero(rms.get("exposuremargin"))
        span_margin = _decimal_or_zero(rms.get("spanmargin"))

        total = available_cash + used_margin
        utilization = float(used_margin / total * 100) if total > 0 else 0.0

        return MarginInfo(
            available_cash=available_cash,
            used_margin=used_margin,
            available_margin=available_margin,
            total_collateral=total_collateral,
            exposure_margin=exposure_margin,
            span_margin=span_margin,
            utilization_pct=min(utilization, 100.0),
        )

    # ── Market Data ──────────────────────────────────────────────────

    async def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        """Get last traded price for a list of instrument tokens.

        Args:
            instruments: List of instrument tokens (Angel One symbol tokens).

        Returns:
            Mapping of instrument token to LTP as float.
        """
        result: dict[str, float] = {}

        # Angel One getLtpData expects exchange + tradingsymbol + symboltoken
        for token in instruments:
            cached = self._instrument_cache.get(token)
            if cached is None:
                logger.warning("Instrument token %s not in cache; skipping LTP", token)
                continue

            exchange_str = _EXCHANGE_REVERSE.get(cached.exchange, "NSE")
            payload = {
                "exchange": exchange_str,
                "tradingsymbol": cached.symbol,
                "symboltoken": token,
            }

            try:
                data = await self._request(
                    "POST",
                    "/rest/secure/angelbroking/order/v1/getLtpData",
                    json_body=payload,
                )
                ltp_data = data.get("data", {})
                ltp_val = ltp_data.get("ltp", 0)
                result[token] = float(ltp_val)
            except Exception:
                logger.error("Failed to fetch LTP for token %s", token, exc_info=True)

        return result

    async def get_option_chain(self, symbol: str, expiry: date) -> OptionChain:
        """Build an option chain for a symbol and expiry from the instrument cache.

        Angel One does not expose a dedicated option-chain endpoint; we
        construct one from the instrument master and LTP queries.

        Args:
            symbol: Underlying symbol (e.g., ``NIFTY``, ``BANKNIFTY``).
            expiry: Expiry date.

        Returns:
            ``OptionChain`` with contracts populated from the instrument cache.
        """
        await self._load_instrument_master()

        # Find matching option instruments
        matching: list[Instrument] = []
        for inst in self._instrument_cache.values():
            if (
                inst.instrument_type
                in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION)
                and inst.underlying
                and inst.underlying.upper() == symbol.upper()
                and inst.expiry == expiry
            ):
                matching.append(inst)

        if not matching:
            # Also try matching by symbol prefix
            for inst in self._instrument_cache.values():
                if (
                    inst.instrument_type
                    in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION)
                    and inst.symbol.startswith(symbol.upper())
                    and inst.expiry == expiry
                ):
                    matching.append(inst)

        # Fetch LTPs for matching instruments
        tokens = [inst.token for inst in matching if inst.token]
        ltp_map: dict[str, float] = {}
        if tokens:
            # Batch LTP in chunks of 50 to avoid overloading
            for i in range(0, len(tokens), 50):
                chunk = tokens[i : i + 50]
                chunk_ltp = await self.get_ltp(chunk)
                ltp_map.update(chunk_ltp)

        # Get underlying price
        underlying_price = Decimal("0")
        underlying_inst = self._find_instrument_by_symbol(symbol, "NSE")
        if underlying_inst and underlying_inst.token:
            ul_ltp = await self.get_ltp([underlying_inst.token])
            if ul_ltp:
                underlying_price = Decimal(str(list(ul_ltp.values())[0]))

        # Build option contracts
        contracts: list[OptionContract] = []
        for inst in matching:
            if inst.strike is None or inst.option_type is None or inst.expiry is None:
                continue
            ltp = Decimal(str(ltp_map.get(inst.token or "", 0)))
            contracts.append(
                OptionContract(
                    instrument=inst,
                    strike=inst.strike,
                    option_type=inst.option_type,
                    expiry=inst.expiry,
                    ltp=ltp,
                )
            )

        # ATM strike
        atm_strike = Decimal("0")
        if contracts and underlying_price > 0:
            atm_strike = min(
                (c.strike for c in contracts),
                key=lambda s: abs(s - underlying_price),
            )

        # PCR (OI-based) -- we don't have OI from LTP alone, so default to 0
        now = datetime.now(timezone(timedelta(hours=5, minutes=30)))

        return OptionChain(
            underlying_symbol=symbol,
            underlying_price=underlying_price,
            expiry=expiry,
            timestamp=now,
            contracts=contracts,
            atm_strike=atm_strike,
        )

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        """Fetch the instrument list, optionally filtered by exchange.

        Args:
            exchange: Exchange code to filter (e.g., ``"NFO"``, ``"NSE"``).
                If ``None``, returns all cached instruments.
        """
        await self._load_instrument_master()

        if exchange is None:
            return list(self._instrument_cache.values())

        return [
            inst
            for inst in self._instrument_cache.values()
            if inst.exchange.value == exchange.upper()
        ]

    # ── WebSocket / Streaming ────────────────────────────────────────

    async def subscribe_ticks(
        self, instruments: list[str], callback: Any
    ) -> None:
        """Subscribe to real-time tick data via Angel One SmartStream WebSocket.

        Args:
            instruments: List of instrument tokens to subscribe.
            callback: Async callable invoked with each tick dict.
        """
        self._tick_callbacks.append(callback)
        self._subscribed_tokens.update(instruments)

        if not self._ws_running:
            self._ws_running = True
            self._ws_task = asyncio.create_task(self._ws_loop())

        # If WS is already running, send subscribe message
        logger.info("Subscribed to ticks for %d instruments", len(instruments))

    async def unsubscribe_ticks(self, instruments: list[str]) -> None:
        """Unsubscribe from tick data for given instruments."""
        for token in instruments:
            self._subscribed_tokens.discard(token)

        if not self._subscribed_tokens:
            self._ws_running = False
            if self._ws_task and not self._ws_task.done():
                self._ws_task.cancel()
                try:
                    await self._ws_task
                except asyncio.CancelledError:
                    pass
                self._ws_task = None

        logger.info("Unsubscribed from %d instruments", len(instruments))

    async def subscribe_order_updates(self, callback: Any) -> None:
        """Subscribe to real-time order status updates.

        Args:
            callback: Async callable invoked on each order update.
        """
        self._order_callbacks.append(callback)
        logger.info("Registered order update callback")

    async def _ws_loop(self) -> None:
        """Internal WebSocket event loop with reconnection and backoff.

        Connects to the Angel One SmartStream WebSocket, subscribes to
        requested tokens, and dispatches ticks to registered callbacks.
        """
        backoff = 1.0
        max_backoff = 60.0

        while self._ws_running:
            try:
                import websockets  # type: ignore[import-untyped]

                headers = {
                    "Authorization": f"Bearer {self._access_token}",
                    "x-api-key": self._api_key,
                    "x-client-code": self._client_id,
                    "x-feed-token": self._feed_token or "",
                }

                async with websockets.connect(
                    _WS_URL,
                    extra_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    logger.info("WebSocket connected to Angel One SmartStream")
                    backoff = 1.0

                    # Subscribe to tokens
                    if self._subscribed_tokens:
                        # Angel One SmartStream subscribe format
                        # Mode 1 = LTP, 2 = Quote, 3 = Snap Quote
                        token_list = [
                            {
                                "exchangeType": 1,  # NSE
                                "tokens": list(self._subscribed_tokens),
                            }
                        ]
                        subscribe_msg = {
                            "correlationID": "ws_sub_1",
                            "action": 1,  # Subscribe
                            "params": {
                                "mode": 2,  # Quote mode
                                "tokenList": token_list,
                            },
                        }
                        await ws.send(json.dumps(subscribe_msg))

                    async for message in ws:
                        if not self._ws_running:
                            break

                        try:
                            if isinstance(message, bytes):
                                tick_data = self._parse_binary_tick(message)
                            else:
                                tick_data = json.loads(message)

                            for cb in self._tick_callbacks:
                                try:
                                    if asyncio.iscoroutinefunction(cb):
                                        await cb(tick_data)
                                    else:
                                        cb(tick_data)
                                except Exception:
                                    logger.error(
                                        "Tick callback error", exc_info=True
                                    )
                        except Exception:
                            logger.error(
                                "Failed to parse WebSocket message",
                                exc_info=True,
                            )

            except asyncio.CancelledError:
                logger.info("WebSocket task cancelled")
                break
            except ImportError:
                logger.error(
                    "websockets package not installed; WebSocket streaming unavailable"
                )
                break
            except Exception:
                logger.error(
                    "WebSocket connection error; reconnecting in %.1fs",
                    backoff,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    @staticmethod
    def _parse_binary_tick(data: bytes) -> dict[str, Any]:
        """Parse Angel One SmartStream binary tick data.

        The binary format varies by subscription mode. This provides a
        best-effort parse for the common Quote mode fields.
        """
        result: dict[str, Any] = {"raw_bytes_length": len(data)}

        if len(data) < 8:
            return result

        try:
            # First byte: subscription mode
            # Bytes 1-2: exchange type
            # Bytes 2-27: token (padded)
            # Remaining: price fields in little-endian int32 (prices * 100)
            result["subscription_mode"] = data[0]
            result["exchange_type"] = struct.unpack("<B", data[1:2])[0]
            token_bytes = data[2:27]
            result["token"] = token_bytes.split(b"\x00")[0].decode("ascii", errors="ignore")

            if len(data) >= 35:
                result["sequence_number"] = struct.unpack("<q", data[27:35])[0]
            if len(data) >= 43:
                result["exchange_timestamp"] = struct.unpack("<q", data[35:43])[0]
            if len(data) >= 47:
                result["ltp"] = struct.unpack("<i", data[43:47])[0] / 100.0
            if len(data) >= 51:
                result["qty_traded"] = struct.unpack("<i", data[47:51])[0]
            if len(data) >= 55:
                result["avg_traded_price"] = struct.unpack("<i", data[51:55])[0] / 100.0
            if len(data) >= 59:
                result["volume"] = struct.unpack("<i", data[55:59])[0]
            if len(data) >= 67:
                result["total_buy_qty"] = struct.unpack("<d", data[59:67])[0]
            if len(data) >= 75:
                result["total_sell_qty"] = struct.unpack("<d", data[67:75])[0]
            if len(data) >= 79:
                result["open"] = struct.unpack("<i", data[75:79])[0] / 100.0
            if len(data) >= 83:
                result["high"] = struct.unpack("<i", data[79:83])[0] / 100.0
            if len(data) >= 87:
                result["low"] = struct.unpack("<i", data[83:87])[0] / 100.0
            if len(data) >= 91:
                result["close"] = struct.unpack("<i", data[87:91])[0] / 100.0
        except Exception:
            logger.debug("Binary tick parse incomplete", exc_info=True)

        return result

    # ── Profile (utility, not in BaseBroker) ─────────────────────────

    async def get_profile(self) -> dict[str, Any]:
        """Fetch the user profile from Angel One.

        Returns:
            Dict with profile fields (name, email, exchanges, etc.).
        """
        data = await self._request(
            "GET",
            "/rest/secure/angelbroking/user/v1/getProfile",
        )
        return data.get("data", {})

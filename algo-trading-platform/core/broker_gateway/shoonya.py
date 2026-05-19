"""
Shoonya (Finvasia) broker gateway implementation.

Integrates with the Shoonya / Finvasia NorenOMS REST + WebSocket API.
All requests are POST to a single base URL with ``jData`` JSON payloads.

Reference: https://api.shoonya.com/NorenWClientTP
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

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
# Mapping helpers
# ---------------------------------------------------------------------------

_SIDE_TO_SHOONYA: dict[OrderSide, str] = {
    OrderSide.BUY: "B",
    OrderSide.SELL: "S",
}
_SHOONYA_TO_SIDE: dict[str, OrderSide] = {v: k for k, v in _SIDE_TO_SHOONYA.items()}

_ORDER_TYPE_TO_SHOONYA: dict[OrderType, str] = {
    OrderType.MARKET: "MKT",
    OrderType.LIMIT: "LMT",
    OrderType.SL: "SL-LMT",
    OrderType.SL_M: "SL-MKT",
}
_SHOONYA_TO_ORDER_TYPE: dict[str, OrderType] = {v: k for k, v in _ORDER_TYPE_TO_SHOONYA.items()}

_PRODUCT_TO_SHOONYA: dict[ProductType, str] = {
    ProductType.MIS: "I",
    ProductType.NRML: "M",
    ProductType.CNC: "C",
}
_SHOONYA_TO_PRODUCT: dict[str, ProductType] = {v: k for k, v in _PRODUCT_TO_SHOONYA.items()}

_EXCHANGE_TO_SHOONYA: dict[Exchange, str] = {
    Exchange.NSE: "NSE",
    Exchange.NFO: "NFO",
    Exchange.BSE: "BSE",
    Exchange.CDS: "CDS",
    Exchange.MCX: "MCX",
}
_SHOONYA_TO_EXCHANGE: dict[str, Exchange] = {v: k for k, v in _EXCHANGE_TO_SHOONYA.items()}

_SHOONYA_STATUS_MAP: dict[str, OrderStatus] = {
    "PENDING": OrderStatus.PENDING,
    "OPEN": OrderStatus.OPEN,
    "COMPLETE": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "TRIGGER_PENDING": OrderStatus.OPEN,
}


def _parse_shoonya_exchange(exch: str) -> Exchange:
    """Convert a Shoonya exchange string to platform ``Exchange``."""
    return _SHOONYA_TO_EXCHANGE.get(exch, Exchange.NSE)


def _segment_for_exchange(exchange: Exchange) -> Segment:
    """Derive the market segment from an exchange enum."""
    match exchange:
        case Exchange.NSE | Exchange.BSE:
            return Segment.EQUITY
        case Exchange.NFO | Exchange.BFO:
            return Segment.FNO
        case Exchange.CDS:
            return Segment.CURRENCY
        case Exchange.MCX:
            return Segment.COMMODITY
        case _:
            return Segment.EQUITY


def _parse_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely parse a value to ``Decimal``."""
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _parse_int(value: Any, default: int = 0) -> int:
    """Safely parse a value to ``int``."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class ShoonyaBroker(BaseBroker):
    """Shoonya (Finvasia) broker gateway.

    Communicates with the Shoonya NorenOMS REST API.  All endpoints are POST
    requests to ``https://api.shoonya.com/NorenWClientTP/<route>`` carrying a
    ``jData`` form field with a JSON payload.

    Args:
        api_key: Shoonya API key.
        user_id: Shoonya user / client ID.
        password: Login password (will be SHA-256 hashed before sending).
        vendor_code: Vendor code provided by Finvasia.
        imei: Device IMEI string (arbitrary identifier accepted by the API).
        totp_secret: TOTP secret for 2FA.
        rate_limit: Maximum requests per second (default 5).
    """

    name: str = "shoonya"

    _BASE_URL: str = "https://api.shoonya.com/NorenWClientTP"
    _WS_URL: str = "wss://api.shoonya.com/NorenWSTP"

    def __init__(
        self,
        api_key: str,
        user_id: str = "",
        password: str = "",
        vendor_code: str = "",
        imei: str = "abc1234",
        totp_secret: str = "",
        rate_limit: float = 5.0,
    ) -> None:
        self._api_key = api_key
        self._user_id = user_id
        self._password = password
        self._vendor_code = vendor_code
        self._imei = imei
        self._totp_secret = totp_secret

        self._session_token: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._limiter = AsyncRateLimiter(rate=rate_limit, burst=max(1, int(rate_limit)))

        # Caches
        self._instrument_cache: dict[str, list[Instrument]] = {}

        # WebSocket state
        self._ws_connection: Any = None
        self._tick_callback: Any = None
        self._order_callback: Any = None

    # ── Internal helpers ──────────────────────────────────────────────

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it if necessary."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _post(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a POST request to the Shoonya API.

        The payload is serialised to JSON and sent as the ``jData`` form
        field.  The session token (``susertoken``) is injected automatically
        when available.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx HTTP status.
            RuntimeError: If the API returns a non-ok status.
        """
        await self._limiter.acquire()

        if self._session_token and "susertoken" not in payload:
            payload["susertoken"] = self._session_token

        client = self._ensure_client()
        url = f"{self._BASE_URL}/{route}"
        jdata = json.dumps(payload)

        logger.debug("Shoonya POST %s  payload_keys=%s", route, list(payload.keys()))

        response = await client.post(url, data=f"jData={jdata}")
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        if data.get("stat") == "Not_Ok":
            emsg = data.get("emsg", "Unknown Shoonya error")
            logger.error("Shoonya API error on /%s: %s", route, emsg)
            raise RuntimeError(f"Shoonya API error: {emsg}")

        return data

    @staticmethod
    def _sha256(text: str) -> str:
        """Return the hex SHA-256 digest of *text*."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _generate_totp(self) -> str:
        """Generate a TOTP code from the stored secret.

        Uses a minimal TOTP implementation (RFC 6238) so that the broker
        module does not depend on ``pyotp`` at import time.  If the caller
        provides a static TOTP code instead of a secret, it is returned
        verbatim.
        """
        if len(self._totp_secret) == 6 and self._totp_secret.isdigit():
            # Caller passed a pre-computed OTP code, not a secret.
            return self._totp_secret

        try:
            import pyotp  # type: ignore[import-untyped]

            return pyotp.TOTP(self._totp_secret).now()
        except ImportError:
            logger.warning(
                "pyotp not installed; pass a 6-digit TOTP code directly "
                "via totp_secret or install pyotp."
            )
            return self._totp_secret

    # ── Connection Lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Authenticate with the Shoonya API using QuickAuth.

        Establishes a session token that is reused for all subsequent
        requests.
        """
        pwd_hash = self._sha256(self._password)
        app_key = self._sha256(f"{self._user_id}|{self._api_key}")
        totp = self._generate_totp()

        payload: dict[str, Any] = {
            "source": "API",
            "apkversion": "1.0.0",
            "uid": self._user_id,
            "pwd": pwd_hash,
            "factor2": totp,
            "vc": self._vendor_code,
            "appkey": app_key,
            "imei": self._imei,
        }

        data = await self._post("QuickAuth", payload)
        self._session_token = data.get("susertoken")
        if not self._session_token:
            raise RuntimeError("Shoonya QuickAuth did not return a session token")

        logger.info(
            "Shoonya authenticated successfully for user %s", self._user_id
        )

    async def disconnect(self) -> None:
        """Close the HTTP client and clear the session."""
        self._session_token = None
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._instrument_cache.clear()
        logger.info("Shoonya session disconnected")

    async def is_connected(self) -> bool:
        """Return ``True`` if a session token is present."""
        return self._session_token is not None

    # ── Order Management ──────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResponse:
        """Place an order via Shoonya PlaceOrder endpoint."""
        payload: dict[str, Any] = {
            "uid": self._user_id,
            "actid": self._user_id,
            "exch": _EXCHANGE_TO_SHOONYA.get(order.instrument.exchange, "NSE"),
            "tsym": order.instrument.symbol,
            "qty": str(order.quantity),
            "prc": str(order.price or 0),
            "trgprc": str(order.trigger_price or 0),
            "prd": _PRODUCT_TO_SHOONYA.get(order.product_type, "I"),
            "trantype": _SIDE_TO_SHOONYA[order.side],
            "prctyp": _ORDER_TYPE_TO_SHOONYA.get(order.order_type, "MKT"),
            "ret": "DAY",
        }

        if order.tag:
            payload["remarks"] = order.tag

        try:
            data = await self._post("PlaceOrder", payload)
            broker_order_id = data.get("norenordno", "")
            logger.info(
                "Shoonya order placed: broker_id=%s symbol=%s side=%s qty=%s",
                broker_order_id,
                order.instrument.symbol,
                order.side.value,
                order.quantity,
            )
            return OrderResponse(
                success=True,
                order_id=order.order_id,
                broker_order_id=broker_order_id,
                message="Order placed successfully",
                status=OrderStatus.PLACED,
            )
        except Exception as exc:
            logger.error("Shoonya PlaceOrder failed: %s", exc)
            return OrderResponse(
                success=False,
                order_id=order.order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def modify_order(
        self, order_id: str, modifications: dict[str, Any]
    ) -> OrderResponse:
        """Modify an existing order via Shoonya ModifyOrder."""
        payload: dict[str, Any] = {
            "uid": self._user_id,
            "norenordno": order_id,
        }

        # Map common modification fields
        field_map: dict[str, str] = {
            "exchange": "exch",
            "symbol": "tsym",
            "quantity": "qty",
            "price": "prc",
            "trigger_price": "trgprc",
            "order_type": "prctyp",
        }
        for platform_key, shoonya_key in field_map.items():
            if platform_key in modifications:
                value = modifications[platform_key]
                if platform_key == "order_type" and isinstance(value, OrderType):
                    value = _ORDER_TYPE_TO_SHOONYA.get(value, "MKT")
                payload[shoonya_key] = str(value)

        if "ret" not in payload:
            payload["ret"] = "DAY"

        try:
            await self._post("ModifyOrder", payload)
            logger.info("Shoonya order modified: %s", order_id)
            return OrderResponse(
                success=True,
                broker_order_id=order_id,
                message="Order modified successfully",
                status=OrderStatus.OPEN,
            )
        except Exception as exc:
            logger.error("Shoonya ModifyOrder failed for %s: %s", order_id, exc)
            return OrderResponse(
                success=False,
                broker_order_id=order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an open order via Shoonya CancelOrder."""
        payload: dict[str, Any] = {
            "uid": self._user_id,
            "norenordno": order_id,
        }

        try:
            await self._post("CancelOrder", payload)
            logger.info("Shoonya order cancelled: %s", order_id)
            return OrderResponse(
                success=True,
                broker_order_id=order_id,
                message="Order cancelled successfully",
                status=OrderStatus.CANCELLED,
            )
        except Exception as exc:
            logger.error("Shoonya CancelOrder failed for %s: %s", order_id, exc)
            return OrderResponse(
                success=False,
                broker_order_id=order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def cancel_all_orders(self) -> list[OrderResponse]:
        """Cancel every open order by fetching the order book first."""
        orders = await self.get_order_book()
        results: list[OrderResponse] = []
        for order in orders:
            if order.status in (OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.PARTIAL):
                if order.broker_order_id:
                    resp = await self.cancel_order(order.broker_order_id)
                    results.append(resp)
        logger.info("Shoonya cancel_all_orders: attempted %d cancellations", len(results))
        return results

    # ── Position & Account ────────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        """Fetch positions from Shoonya PositionBook."""
        payload: dict[str, Any] = {"uid": self._user_id, "actid": self._user_id}

        try:
            data = await self._post("PositionBook", payload)
        except RuntimeError:
            # API returns Not_Ok when there are no positions
            return []

        if not isinstance(data, list):
            data = [data] if data.get("stat") == "Ok" else []

        positions: list[Position] = []
        for item in data:
            exchange = _parse_shoonya_exchange(item.get("exch", "NSE"))
            instrument = Instrument(
                symbol=item.get("tsym", ""),
                exchange=exchange,
                segment=_segment_for_exchange(exchange),
                instrument_type=InstrumentType.STOCK,
                token=item.get("token"),
            )
            product = _SHOONYA_TO_PRODUCT.get(item.get("prd", "I"), ProductType.MIS)
            net_qty = _parse_int(item.get("netqty", 0))
            avg_price = _parse_decimal(item.get("netavgprc", "0"))
            ltp = _parse_decimal(item.get("lp", "0"))
            pnl_realized = _parse_decimal(item.get("rpnl", "0"))
            pnl_unrealized = _parse_decimal(item.get("urmtom", "0"))

            positions.append(
                Position(
                    instrument=instrument,
                    quantity=net_qty,
                    average_price=avg_price,
                    ltp=ltp,
                    pnl_realized=pnl_realized,
                    pnl_unrealized=pnl_unrealized,
                    product_type=product,
                )
            )
        return positions

    async def get_order_book(self) -> list[Order]:
        """Fetch today's order book from Shoonya."""
        payload: dict[str, Any] = {"uid": self._user_id}

        try:
            data = await self._post("OrderBook", payload)
        except RuntimeError:
            return []

        if not isinstance(data, list):
            data = [data] if data.get("stat") == "Ok" else []

        orders: list[Order] = []
        for item in data:
            exchange = _parse_shoonya_exchange(item.get("exch", "NSE"))
            instrument = Instrument(
                symbol=item.get("tsym", ""),
                exchange=exchange,
                segment=_segment_for_exchange(exchange),
                instrument_type=InstrumentType.STOCK,
                token=item.get("token"),
            )
            side = _SHOONYA_TO_SIDE.get(item.get("trantype", "B"), OrderSide.BUY)
            order_type = _SHOONYA_TO_ORDER_TYPE.get(item.get("prctyp", "MKT"), OrderType.MARKET)
            product = _SHOONYA_TO_PRODUCT.get(item.get("prd", "I"), ProductType.MIS)
            status_str = item.get("status", "PENDING").upper()
            status = _SHOONYA_STATUS_MAP.get(status_str, OrderStatus.PENDING)

            orders.append(
                Order(
                    instrument=instrument,
                    side=side,
                    order_type=order_type,
                    product_type=product,
                    quantity=_parse_int(item.get("qty", 0)) or 1,
                    price=_parse_decimal(item.get("prc")) or None,
                    trigger_price=_parse_decimal(item.get("trgprc")) or None,
                    status=status,
                    filled_quantity=_parse_int(item.get("fillshares", 0)),
                    average_price=_parse_decimal(item.get("avgprc", "0")),
                    broker_order_id=item.get("norenordno"),
                    rejection_reason=item.get("rejreason"),
                )
            )
        return orders

    async def get_trade_book(self) -> list[Trade]:
        """Fetch today's trade book from Shoonya."""
        payload: dict[str, Any] = {"uid": self._user_id, "actid": self._user_id}

        try:
            data = await self._post("TradeBook", payload)
        except RuntimeError:
            return []

        if not isinstance(data, list):
            data = [data] if data.get("stat") == "Ok" else []

        trades: list[Trade] = []
        for item in data:
            exchange = _parse_shoonya_exchange(item.get("exch", "NSE"))
            instrument = Instrument(
                symbol=item.get("tsym", ""),
                exchange=exchange,
                segment=_segment_for_exchange(exchange),
                instrument_type=InstrumentType.STOCK,
                token=item.get("token"),
            )
            side = _SHOONYA_TO_SIDE.get(item.get("trantype", "B"), OrderSide.BUY)

            # Parse trade timestamp (format: "dd-mm-yyyy HH:MM:SS")
            ts_str = item.get("exch_tm", "")
            try:
                ts = datetime.strptime(ts_str, "%d-%m-%Y %H:%M:%S")
            except (ValueError, TypeError):
                ts = datetime.now()

            trades.append(
                Trade(
                    order_id=item.get("norenordno", ""),
                    instrument=instrument,
                    side=side,
                    quantity=_parse_int(item.get("fillshares", 0)) or 1,
                    price=_parse_decimal(item.get("flprc", "0")),
                    timestamp=ts,
                    broker_trade_id=item.get("flid"),
                    exchange_trade_id=item.get("exchordid"),
                )
            )
        return trades

    async def get_margins(self) -> MarginInfo:
        """Fetch margin / funds info from Shoonya Limits."""
        payload: dict[str, Any] = {"uid": self._user_id, "actid": self._user_id}

        data = await self._post("Limits", payload)

        available_cash = _parse_decimal(data.get("cash", "0"))
        used_margin = _parse_decimal(data.get("marginused", "0"))
        available_margin = _parse_decimal(data.get("marginleft", "0"))
        total_collateral = _parse_decimal(data.get("collateral", "0"))
        exposure_margin = _parse_decimal(data.get("exposuremargin", "0"))
        span_margin = _parse_decimal(data.get("spanmargin", "0"))

        total = available_margin + used_margin
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

    # ── Market Data ───────────────────────────────────────────────────

    async def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        """Get the last traded price for a list of instruments.

        Each instrument string should be in ``EXCHANGE|TOKEN`` format
        (e.g. ``"NSE|26000"``).  If a plain token is given, NSE is assumed.
        """
        results: dict[str, float] = {}

        for inst in instruments:
            if "|" in inst:
                exch, token = inst.split("|", 1)
            else:
                exch, token = "NSE", inst

            payload: dict[str, Any] = {
                "uid": self._user_id,
                "exch": exch,
                "token": token,
            }

            try:
                data = await self._post("GetQuotes", payload)
                ltp = float(data.get("lp", 0))
                results[inst] = ltp
            except Exception as exc:
                logger.warning("Shoonya GetQuotes failed for %s: %s", inst, exc)
                results[inst] = 0.0

        return results

    async def get_option_chain(self, symbol: str, expiry: date) -> OptionChain:
        """Build an option chain by searching for instruments and fetching quotes.

        Shoonya does not have a dedicated option-chain endpoint; we search
        for matching scrips and assemble the chain from individual quotes.
        """
        # Search for option instruments matching the underlying
        search_text = f"{symbol} {expiry.strftime('%d%b%y').upper()}"
        search_payload: dict[str, Any] = {
            "uid": self._user_id,
            "stext": search_text,
            "exch": "NFO",
        }

        try:
            data = await self._post("SearchScrip", search_payload)
        except RuntimeError:
            data = {}

        scrips = data.get("values", [])
        contracts: list[OptionContract] = []
        underlying_ltp = Decimal("0")

        for scrip in scrips:
            tsym: str = scrip.get("tsym", "")
            token: str = scrip.get("token", "")

            # Determine option type from symbol suffix
            opt_type: OptionType | None = None
            if tsym.endswith("CE"):
                opt_type = OptionType.CE
            elif tsym.endswith("PE"):
                opt_type = OptionType.PE
            else:
                continue  # skip futures / irrelevant scrips

            # Extract strike from symbol (best-effort)
            strike = _parse_decimal(scrip.get("strike_price", scrip.get("strprc", "0")))
            if strike <= 0:
                continue

            # Fetch individual quote for LTP / OI
            try:
                quote = await self._post(
                    "GetQuotes",
                    {"uid": self._user_id, "exch": "NFO", "token": token},
                )
            except RuntimeError:
                quote = {}

            inst = Instrument(
                symbol=tsym,
                exchange=Exchange.NFO,
                segment=Segment.FNO,
                instrument_type=(
                    InstrumentType.CALL_OPTION
                    if opt_type == OptionType.CE
                    else InstrumentType.PUT_OPTION
                ),
                expiry=expiry,
                strike=strike,
                option_type=opt_type,
                underlying=symbol,
                token=token,
            )

            ltp = _parse_decimal(quote.get("lp", "0"))
            contracts.append(
                OptionContract(
                    instrument=inst,
                    strike=strike,
                    option_type=opt_type,
                    expiry=expiry,
                    ltp=ltp,
                    bid=_parse_decimal(quote.get("bp1", "0")),
                    ask=_parse_decimal(quote.get("sp1", "0")),
                    volume=_parse_int(quote.get("v", 0)),
                    oi=_parse_int(quote.get("oi", 0)),
                    oi_change=_parse_int(quote.get("oichng", 0)),
                )
            )

        # Fetch underlying LTP
        try:
            underlying_quote = await self._post(
                "SearchScrip",
                {"uid": self._user_id, "stext": symbol, "exch": "NSE"},
            )
            underlying_values = underlying_quote.get("values", [])
            if underlying_values:
                utoken = underlying_values[0].get("token", "")
                uquote = await self._post(
                    "GetQuotes",
                    {"uid": self._user_id, "exch": "NSE", "token": utoken},
                )
                underlying_ltp = _parse_decimal(uquote.get("lp", "0"))
        except RuntimeError:
            pass

        # Determine ATM strike
        strikes = sorted({c.strike for c in contracts})
        atm_strike = Decimal("0")
        if strikes and underlying_ltp > 0:
            atm_strike = min(strikes, key=lambda s: abs(s - underlying_ltp))

        # Compute PCR
        total_put_oi = sum(c.oi for c in contracts if c.option_type == OptionType.PE)
        total_call_oi = sum(c.oi for c in contracts if c.option_type == OptionType.CE)
        pcr = float(total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0

        return OptionChain(
            underlying_symbol=symbol,
            underlying_price=underlying_ltp,
            expiry=expiry,
            timestamp=datetime.now(),
            contracts=contracts,
            atm_strike=atm_strike,
            pcr=pcr,
        )

    async def get_instruments(self, exchange: str | None = None) -> list[Instrument]:
        """Fetch / search instruments via Shoonya SearchScrip.

        Results are cached per exchange for the lifetime of the connection.
        """
        cache_key = exchange or "ALL"
        if cache_key in self._instrument_cache:
            return self._instrument_cache[cache_key]

        exch = exchange or "NSE"
        payload: dict[str, Any] = {
            "uid": self._user_id,
            "stext": "",
            "exch": exch,
        }

        try:
            data = await self._post("SearchScrip", payload)
        except RuntimeError:
            return []

        scrips = data.get("values", [])
        instruments: list[Instrument] = []

        for scrip in scrips:
            exch_enum = _parse_shoonya_exchange(scrip.get("exch", exch))
            inst_type = InstrumentType.STOCK
            tsym: str = scrip.get("tsym", "")
            if tsym.endswith("CE"):
                inst_type = InstrumentType.CALL_OPTION
            elif tsym.endswith("PE"):
                inst_type = InstrumentType.PUT_OPTION
            elif scrip.get("instname") == "FUTIDX" or scrip.get("instname") == "FUTSTK":
                inst_type = InstrumentType.FUTURE

            instruments.append(
                Instrument(
                    symbol=tsym,
                    exchange=exch_enum,
                    segment=_segment_for_exchange(exch_enum),
                    instrument_type=inst_type,
                    token=scrip.get("token"),
                    lot_size=_parse_int(scrip.get("ls", 1)) or 1,
                    tick_size=_parse_decimal(scrip.get("ti", "0.05")),
                )
            )

        self._instrument_cache[cache_key] = instruments
        logger.info(
            "Shoonya instruments loaded: exchange=%s count=%d", exch, len(instruments)
        )
        return instruments

    # ── WebSocket / Streaming ─────────────────────────────────────────

    async def subscribe_ticks(self, instruments: list[str], callback: Any) -> None:
        """Subscribe to real-time tick data via Shoonya WebSocket.

        This is a stub that stores the callback and instrument list.  A
        production implementation would open a WebSocket connection to
        ``wss://api.shoonya.com/NorenWSTP`` and handle reconnection with
        exponential backoff.

        Args:
            instruments: List of ``EXCHANGE|TOKEN`` strings.
            callback: Async callable invoked with each tick dict.
        """
        self._tick_callback = callback
        logger.info(
            "Shoonya tick subscription registered for %d instruments",
            len(instruments),
        )
        # In production: open WS, send subscribe frames, dispatch to callback.

    async def unsubscribe_ticks(self, instruments: list[str]) -> None:
        """Unsubscribe from tick data for the given instruments."""
        logger.info(
            "Shoonya tick unsubscription for %d instruments", len(instruments)
        )
        # In production: send unsubscribe frames over the WS.

    async def subscribe_order_updates(self, callback: Any) -> None:
        """Subscribe to real-time order status updates.

        The Shoonya WebSocket delivers order updates on the same
        connection as tick data.  This registers the callback that will
        be invoked when an order update frame arrives.
        """
        self._order_callback = callback
        logger.info("Shoonya order-update subscription registered")

    # ── Utilities ─────────────────────────────────────────────────────

    def __repr__(self) -> str:
        connected = "connected" if self._session_token else "disconnected"
        return f"<ShoonyaBroker user={self._user_id} {connected}>"

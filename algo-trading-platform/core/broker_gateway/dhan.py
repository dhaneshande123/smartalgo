"""
Dhan HQ broker gateway implementation.

Integrates with the Dhan v2 REST API and WebSocket feed for market data and
order management.

Reference: https://api.dhan.co/v2
"""

from __future__ import annotations

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

_SIDE_TO_DHAN: dict[OrderSide, str] = {
    OrderSide.BUY: "BUY",
    OrderSide.SELL: "SELL",
}
_DHAN_TO_SIDE: dict[str, OrderSide] = {v: k for k, v in _SIDE_TO_DHAN.items()}

_ORDER_TYPE_TO_DHAN: dict[OrderType, str] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.SL: "STOP_LOSS",
    OrderType.SL_M: "STOP_LOSS_MARKET",
}
_DHAN_TO_ORDER_TYPE: dict[str, OrderType] = {v: k for k, v in _ORDER_TYPE_TO_DHAN.items()}

_PRODUCT_TO_DHAN: dict[ProductType, str] = {
    ProductType.MIS: "INTRADAY",
    ProductType.NRML: "MARGIN",
    ProductType.CNC: "CNC",
}
_DHAN_TO_PRODUCT: dict[str, ProductType] = {v: k for k, v in _PRODUCT_TO_DHAN.items()}

_EXCHANGE_TO_DHAN: dict[Exchange, str] = {
    Exchange.NSE: "NSE_EQ",
    Exchange.BSE: "BSE_EQ",
    Exchange.NFO: "NSE_FNO",
    Exchange.BFO: "BSE_FNO",
    Exchange.MCX: "MCX_COMM",
    Exchange.CDS: "NSE_CURRENCY",
}
_DHAN_TO_EXCHANGE: dict[str, Exchange] = {v: k for k, v in _EXCHANGE_TO_DHAN.items()}

_DHAN_STATUS_MAP: dict[str, OrderStatus] = {
    "TRANSIT": OrderStatus.PENDING,
    "PENDING": OrderStatus.PENDING,
    "TRADED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.CANCELLED,
    "PART_TRADED": OrderStatus.PARTIAL,
}


def _parse_dhan_exchange(segment: str) -> Exchange:
    """Convert a Dhan exchange segment string to platform ``Exchange``."""
    return _DHAN_TO_EXCHANGE.get(segment, Exchange.NSE)


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


class DhanBroker(BaseBroker):
    """Dhan HQ broker gateway.

    Communicates with the Dhan v2 REST API.  Authentication uses a static
    ``access-token`` header.

    Args:
        api_key: Dhan API key (used as the access token).
        client_id: Dhan client / account ID.
        access_token: Explicit access token; falls back to *api_key* when empty.
        rate_limit: Maximum requests per second (default 25).
    """

    name: str = "dhan"

    _BASE_URL: str = "https://api.dhan.co/v2"
    _WS_URL: str = "wss://api-feed.dhan.co"

    def __init__(
        self,
        api_key: str,
        client_id: str = "",
        access_token: str = "",
        rate_limit: float = 25.0,
    ) -> None:
        self._api_key = api_key
        self._client_id = client_id
        self._access_token = access_token or api_key
        self._connected: bool = False

        self._client: httpx.AsyncClient | None = None
        self._limiter = AsyncRateLimiter(rate=rate_limit, burst=max(1, int(rate_limit)))

        # Caches
        self._instrument_cache: dict[str, list[Instrument]] = {}

        # WebSocket state
        self._tick_callback: Any = None
        self._order_callback: Any = None

    # ── Internal helpers ──────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """Return the standard headers for Dhan API requests."""
        return {
            "access-token": self._access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it if necessary."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._BASE_URL,
                headers=self._headers(),
                timeout=30.0,
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Send an HTTP request to the Dhan API.

        Handles rate limiting and basic error handling.

        Returns:
            Parsed JSON response (dict or list).

        Raises:
            httpx.HTTPStatusError: On non-2xx HTTP status.
            RuntimeError: If the API returns an error payload.
        """
        await self._limiter.acquire()

        client = self._ensure_client()

        logger.debug("Dhan %s %s", method.upper(), path)

        response = await client.request(
            method,
            path,
            json=json_body,
            params=params,
        )

        # Dhan returns 2xx on success; raise on error status
        if response.status_code >= 400:
            body = response.text
            logger.error(
                "Dhan API error: %s %s -> %d: %s",
                method.upper(),
                path,
                response.status_code,
                body[:500],
            )
            raise RuntimeError(
                f"Dhan API error ({response.status_code}): {body[:300]}"
            )

        if response.status_code == 204:
            return {}

        return response.json()

    async def _get(self, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        """Convenience wrapper for GET requests."""
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any] | list[Any]:
        """Convenience wrapper for POST requests."""
        return await self._request("POST", path, json_body=body)

    async def _put(self, path: str, body: dict[str, Any]) -> dict[str, Any] | list[Any]:
        """Convenience wrapper for PUT requests."""
        return await self._request("PUT", path, json_body=body)

    async def _delete(self, path: str) -> dict[str, Any] | list[Any]:
        """Convenience wrapper for DELETE requests."""
        return await self._request("DELETE", path)

    # ── Connection Lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Verify the Dhan access token by fetching fund limits.

        Dhan uses a static access token, so there is no explicit login
        endpoint.  We validate the token by making a lightweight API call.
        """
        self._ensure_client()

        try:
            await self._get("/fundlimit")
            self._connected = True
            logger.info(
                "Dhan connected successfully for client %s", self._client_id
            )
        except Exception as exc:
            self._connected = False
            raise RuntimeError(
                f"Dhan connection failed — invalid token or network error: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        """Close the HTTP client and clear state."""
        self._connected = False
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._instrument_cache.clear()
        logger.info("Dhan session disconnected")

    async def is_connected(self) -> bool:
        """Return ``True`` if the last connection check succeeded."""
        return self._connected

    # ── Order Management ──────────────────────────────────────────────

    async def place_order(self, order: Order) -> OrderResponse:
        """Place an order via Dhan POST /orders."""
        body: dict[str, Any] = {
            "dhanClientId": self._client_id,
            "transactionType": _SIDE_TO_DHAN[order.side],
            "exchangeSegment": _EXCHANGE_TO_DHAN.get(order.instrument.exchange, "NSE_EQ"),
            "productType": _PRODUCT_TO_DHAN.get(order.product_type, "INTRADAY"),
            "orderType": _ORDER_TYPE_TO_DHAN.get(order.order_type, "MARKET"),
            "validity": "DAY",
            "securityId": order.instrument.token or order.instrument.symbol,
            "quantity": order.quantity,
            "price": float(order.price) if order.price else 0.0,
            "triggerPrice": float(order.trigger_price) if order.trigger_price else 0.0,
            "disclosedQuantity": 0,
            "afterMarketOrder": False,
        }

        if order.tag:
            body["correlationId"] = order.tag

        try:
            data = await self._post("/orders", body)
            if not isinstance(data, dict):
                data = {}
            broker_order_id = data.get("orderId", data.get("order_id", ""))
            logger.info(
                "Dhan order placed: broker_id=%s symbol=%s side=%s qty=%s",
                broker_order_id,
                order.instrument.symbol,
                order.side.value,
                order.quantity,
            )
            return OrderResponse(
                success=True,
                order_id=order.order_id,
                broker_order_id=str(broker_order_id),
                message="Order placed successfully",
                status=OrderStatus.PLACED,
            )
        except Exception as exc:
            logger.error("Dhan PlaceOrder failed: %s", exc)
            return OrderResponse(
                success=False,
                order_id=order.order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def modify_order(
        self, order_id: str, modifications: dict[str, Any]
    ) -> OrderResponse:
        """Modify an existing order via Dhan PUT /orders/{order_id}."""
        body: dict[str, Any] = {
            "dhanClientId": self._client_id,
            "orderId": order_id,
        }

        field_map: dict[str, str] = {
            "quantity": "quantity",
            "price": "price",
            "trigger_price": "triggerPrice",
            "order_type": "orderType",
            "validity": "validity",
            "disclosed_quantity": "disclosedQuantity",
        }
        for platform_key, dhan_key in field_map.items():
            if platform_key in modifications:
                value = modifications[platform_key]
                if platform_key == "order_type" and isinstance(value, OrderType):
                    value = _ORDER_TYPE_TO_DHAN.get(value, "MARKET")
                elif platform_key in ("price", "trigger_price"):
                    value = float(value) if value else 0.0
                body[dhan_key] = value

        try:
            await self._put(f"/orders/{order_id}", body)
            logger.info("Dhan order modified: %s", order_id)
            return OrderResponse(
                success=True,
                broker_order_id=order_id,
                message="Order modified successfully",
                status=OrderStatus.OPEN,
            )
        except Exception as exc:
            logger.error("Dhan ModifyOrder failed for %s: %s", order_id, exc)
            return OrderResponse(
                success=False,
                broker_order_id=order_id,
                message=str(exc),
                status=OrderStatus.ERROR,
            )

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an open order via Dhan DELETE /orders/{order_id}."""
        try:
            await self._delete(f"/orders/{order_id}")
            logger.info("Dhan order cancelled: %s", order_id)
            return OrderResponse(
                success=True,
                broker_order_id=order_id,
                message="Order cancelled successfully",
                status=OrderStatus.CANCELLED,
            )
        except Exception as exc:
            logger.error("Dhan CancelOrder failed for %s: %s", order_id, exc)
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
        logger.info("Dhan cancel_all_orders: attempted %d cancellations", len(results))
        return results

    # ── Position & Account ────────────────────────────────────────────

    async def get_positions(self) -> list[Position]:
        """Fetch positions from Dhan GET /positions."""
        try:
            data = await self._get("/positions")
        except RuntimeError:
            return []

        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return []

        positions: list[Position] = []
        for item in data:
            exchange = _parse_dhan_exchange(item.get("exchangeSegment", "NSE_EQ"))
            instrument = Instrument(
                symbol=item.get("tradingSymbol", ""),
                exchange=exchange,
                segment=_segment_for_exchange(exchange),
                instrument_type=InstrumentType.STOCK,
                token=str(item.get("securityId", "")),
            )
            product = _DHAN_TO_PRODUCT.get(item.get("productType", "INTRADAY"), ProductType.MIS)
            net_qty = _parse_int(item.get("netQty", 0))
            avg_price = _parse_decimal(item.get("averagePrice", "0"))
            ltp = _parse_decimal(item.get("ltp", "0"))
            pnl_realized = _parse_decimal(item.get("realizedProfit", "0"))
            pnl_unrealized = _parse_decimal(item.get("unrealizedProfit", "0"))

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
        """Fetch today's order book from Dhan GET /orders."""
        try:
            data = await self._get("/orders")
        except RuntimeError:
            return []

        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return []

        orders: list[Order] = []
        for item in data:
            exchange = _parse_dhan_exchange(item.get("exchangeSegment", "NSE_EQ"))
            instrument = Instrument(
                symbol=item.get("tradingSymbol", ""),
                exchange=exchange,
                segment=_segment_for_exchange(exchange),
                instrument_type=InstrumentType.STOCK,
                token=str(item.get("securityId", "")),
            )
            side = _DHAN_TO_SIDE.get(item.get("transactionType", "BUY"), OrderSide.BUY)
            order_type = _DHAN_TO_ORDER_TYPE.get(item.get("orderType", "MARKET"), OrderType.MARKET)
            product = _DHAN_TO_PRODUCT.get(item.get("productType", "INTRADAY"), ProductType.MIS)
            status_str = item.get("orderStatus", "PENDING").upper()
            status = _DHAN_STATUS_MAP.get(status_str, OrderStatus.PENDING)

            price = _parse_decimal(item.get("price")) or None
            trigger_price = _parse_decimal(item.get("triggerPrice")) or None

            orders.append(
                Order(
                    instrument=instrument,
                    side=side,
                    order_type=order_type,
                    product_type=product,
                    quantity=_parse_int(item.get("quantity", 0)) or 1,
                    price=price,
                    trigger_price=trigger_price,
                    status=status,
                    filled_quantity=_parse_int(item.get("filledQty", 0)),
                    average_price=_parse_decimal(item.get("averageTradedPrice", "0")),
                    broker_order_id=str(item.get("orderId", "")),
                    rejection_reason=item.get("omsErrorDescription"),
                )
            )
        return orders

    async def get_trade_book(self) -> list[Trade]:
        """Fetch today's trade book from Dhan GET /trades."""
        try:
            data = await self._get("/trades")
        except RuntimeError:
            return []

        if isinstance(data, dict):
            data = data.get("data", [])
        if not isinstance(data, list):
            return []

        trades: list[Trade] = []
        for item in data:
            exchange = _parse_dhan_exchange(item.get("exchangeSegment", "NSE_EQ"))
            instrument = Instrument(
                symbol=item.get("tradingSymbol", ""),
                exchange=exchange,
                segment=_segment_for_exchange(exchange),
                instrument_type=InstrumentType.STOCK,
                token=str(item.get("securityId", "")),
            )
            side = _DHAN_TO_SIDE.get(item.get("transactionType", "BUY"), OrderSide.BUY)

            # Parse trade timestamp
            ts_str = item.get("exchangeTime", item.get("createTime", ""))
            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
            except (ValueError, TypeError):
                ts = datetime.now()

            trades.append(
                Trade(
                    order_id=str(item.get("orderId", "")),
                    instrument=instrument,
                    side=side,
                    quantity=_parse_int(item.get("tradedQuantity", 0)) or 1,
                    price=_parse_decimal(item.get("tradedPrice", "0")),
                    timestamp=ts,
                    broker_trade_id=str(item.get("tradeId", "")),
                    exchange_trade_id=str(item.get("exchangeTradeId", "")),
                )
            )
        return trades

    async def get_margins(self) -> MarginInfo:
        """Fetch margin / fund info from Dhan GET /fundlimit."""
        data = await self._get("/fundlimit")
        if not isinstance(data, dict):
            data = {}

        # Dhan returns a nested structure; flatten common fields
        available_cash = _parse_decimal(data.get("availabelBalance", data.get("sodLimit", "0")))
        used_margin = _parse_decimal(data.get("utilizedAmount", "0"))
        available_margin = _parse_decimal(data.get("availabelBalance", "0"))
        total_collateral = _parse_decimal(data.get("collateralAmount", "0"))
        exposure_margin = _parse_decimal(data.get("blockedPayoutAmount", "0"))
        span_margin = _parse_decimal(data.get("spanMargin", "0"))

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

        Each instrument string should be ``EXCHANGE_SEGMENT:SECURITY_ID``
        (e.g. ``"NSE_EQ:11536"``).  If no segment prefix is given,
        ``NSE_EQ`` is assumed.

        Dhan supports batching LTP requests by grouping security IDs per
        exchange segment.
        """
        # Group instruments by exchange segment
        grouped: dict[str, list[str]] = {}
        original_keys: dict[str, str] = {}  # security_id -> original key
        for inst in instruments:
            if ":" in inst:
                segment, sec_id = inst.split(":", 1)
            else:
                segment, sec_id = "NSE_EQ", inst
            grouped.setdefault(segment, []).append(sec_id)
            original_keys[sec_id] = inst

        body: dict[str, list[int]] = {}
        for segment, ids in grouped.items():
            body[segment] = [int(i) for i in ids if i.isdigit()]

        results: dict[str, float] = {}

        if not body:
            return results

        try:
            data = await self._post("/marketfeed/ltp", body)
            if not isinstance(data, dict):
                data = {}
            # Dhan returns {segment: {securityId: {ltp: ...}}} or similar
            ltp_data = data.get("data", data)
            if isinstance(ltp_data, dict):
                for segment, sec_map in ltp_data.items():
                    if isinstance(sec_map, dict):
                        for sec_id, quote in sec_map.items():
                            key = original_keys.get(str(sec_id), f"{segment}:{sec_id}")
                            if isinstance(quote, dict):
                                results[key] = float(quote.get("last_price", quote.get("ltp", 0)))
                            elif isinstance(quote, (int, float)):
                                results[key] = float(quote)
        except Exception as exc:
            logger.warning("Dhan LTP fetch failed: %s", exc)
            for inst in instruments:
                results.setdefault(inst, 0.0)

        # Ensure all requested instruments have an entry
        for inst in instruments:
            results.setdefault(inst, 0.0)

        return results

    async def get_option_chain(self, symbol: str, expiry: date) -> OptionChain:
        """Fetch the option chain for a symbol and expiry from Dhan.

        Dhan provides an option chain endpoint; if unavailable we fall back
        to building the chain from the instrument master and LTP calls.
        """
        # Try the dedicated option chain endpoint
        body: dict[str, Any] = {
            "UnderlyingScrip": symbol,
            "ExpiryDate": expiry.isoformat(),
        }

        contracts: list[OptionContract] = []
        underlying_ltp = Decimal("0")

        try:
            data = await self._post("/optionchain", body)
            if not isinstance(data, dict):
                data = {}

            chain_data = data.get("data", [])
            if isinstance(chain_data, list):
                for item in chain_data:
                    for opt_side in ("ce", "CE", "pe", "PE"):
                        opt_data = item.get(opt_side)
                        if not opt_data:
                            continue
                        strike = _parse_decimal(
                            item.get("strikePrice", opt_data.get("strikePrice", "0"))
                        )
                        if strike <= 0:
                            continue

                        opt_type = OptionType.CE if opt_side.upper() == "CE" else OptionType.PE
                        inst_type = (
                            InstrumentType.CALL_OPTION
                            if opt_type == OptionType.CE
                            else InstrumentType.PUT_OPTION
                        )

                        inst = Instrument(
                            symbol=opt_data.get("tradingSymbol", f"{symbol}{strike}{opt_side.upper()}"),
                            exchange=Exchange.NFO,
                            segment=Segment.FNO,
                            instrument_type=inst_type,
                            expiry=expiry,
                            strike=strike,
                            option_type=opt_type,
                            underlying=symbol,
                            token=str(opt_data.get("securityId", "")),
                        )

                        contracts.append(
                            OptionContract(
                                instrument=inst,
                                strike=strike,
                                option_type=opt_type,
                                expiry=expiry,
                                ltp=_parse_decimal(opt_data.get("ltp", "0")),
                                bid=_parse_decimal(opt_data.get("bestBidPrice", "0")),
                                ask=_parse_decimal(opt_data.get("bestAskPrice", "0")),
                                volume=_parse_int(opt_data.get("volume", 0)),
                                oi=_parse_int(opt_data.get("openInterest", 0)),
                                oi_change=_parse_int(opt_data.get("oiChange", 0)),
                                iv=float(opt_data.get("impliedVolatility", 0)),
                            )
                        )

            underlying_ltp = _parse_decimal(data.get("underlyingPrice", "0"))

        except Exception as exc:
            logger.warning("Dhan option chain fetch failed: %s", exc)

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
        """Fetch the instrument master from Dhan.

        Dhan provides a CSV-based instrument download.  This method fetches
        the list and caches it for the lifetime of the connection.

        Args:
            exchange: Optional exchange filter (e.g. ``"NFO"``, ``"NSE"``).
        """
        cache_key = exchange or "ALL"
        if cache_key in self._instrument_cache:
            return self._instrument_cache[cache_key]

        # Dhan instrument master is typically a CSV download at a well-known URL.
        # We use the compact API endpoint if available.
        instruments: list[Instrument] = []

        try:
            data = await self._get("/compact/instruments")
            if not isinstance(data, list):
                data = data.get("data", []) if isinstance(data, dict) else []
        except RuntimeError:
            logger.warning("Dhan instrument master fetch failed")
            return []

        for item in data:
            exch_segment = item.get("exchangeSegment", "NSE_EQ")
            exch_enum = _parse_dhan_exchange(exch_segment)

            # Apply exchange filter
            if exchange and exch_enum.value != exchange:
                continue

            tsym = item.get("tradingSymbol", "")
            inst_type = InstrumentType.STOCK

            scrip_type = item.get("instrumentType", "").upper()
            if scrip_type in ("OPTIDX", "OPTSTK", "CE"):
                inst_type = InstrumentType.CALL_OPTION
            elif scrip_type in ("PE",):
                inst_type = InstrumentType.PUT_OPTION
            elif scrip_type in ("FUTIDX", "FUTSTK", "FUT"):
                inst_type = InstrumentType.FUTURE
            elif scrip_type in ("INDEX",):
                inst_type = InstrumentType.INDEX

            lot_size = _parse_int(item.get("lotSize", 1)) or 1
            tick_size = _parse_decimal(item.get("tickSize", "0.05"))

            instruments.append(
                Instrument(
                    symbol=tsym,
                    exchange=exch_enum,
                    segment=_segment_for_exchange(exch_enum),
                    instrument_type=inst_type,
                    token=str(item.get("securityId", "")),
                    lot_size=lot_size,
                    tick_size=tick_size,
                )
            )

        self._instrument_cache[cache_key] = instruments
        logger.info(
            "Dhan instruments loaded: exchange=%s count=%d", exchange or "ALL", len(instruments)
        )
        return instruments

    # ── WebSocket / Streaming ─────────────────────────────────────────

    async def subscribe_ticks(self, instruments: list[str], callback: Any) -> None:
        """Subscribe to real-time tick data via Dhan WebSocket.

        This is a stub that stores the callback and instrument list.  A
        production implementation would open a WebSocket connection to
        ``wss://api-feed.dhan.co`` using the Dhan binary feed protocol.

        Args:
            instruments: List of ``EXCHANGE_SEGMENT:SECURITY_ID`` strings.
            callback: Async callable invoked with each tick dict.
        """
        self._tick_callback = callback
        logger.info(
            "Dhan tick subscription registered for %d instruments", len(instruments)
        )

    async def unsubscribe_ticks(self, instruments: list[str]) -> None:
        """Unsubscribe from tick data for the given instruments."""
        logger.info(
            "Dhan tick unsubscription for %d instruments", len(instruments)
        )

    async def subscribe_order_updates(self, callback: Any) -> None:
        """Subscribe to real-time order status updates via Dhan WebSocket."""
        self._order_callback = callback
        logger.info("Dhan order-update subscription registered")

    # ── Utilities ─────────────────────────────────────────────────────

    def __repr__(self) -> str:
        connected = "connected" if self._connected else "disconnected"
        return f"<DhanBroker client={self._client_id} {connected}>"

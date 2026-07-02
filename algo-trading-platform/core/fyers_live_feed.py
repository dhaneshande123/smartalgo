"""
Fyers API v3 Live Data Feed.

Connects to Fyers API to fetch real market data (quotes, option chains,
historical candles) and stream live ticks via WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Symbol mappings: internal name -> Fyers symbol format
# ---------------------------------------------------------------------------

INDEX_SYMBOLS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "INDIA VIX": "NSE:INDIAVIX-INDEX",
}

# Reverse map: Fyers symbol -> internal name
_FYERS_TO_INTERNAL = {v: k for k, v in INDEX_SYMBOLS.items()}

# Option chain underlying symbols
OPTION_CHAIN_SYMBOLS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "BANKEX": "BSE:BANKEX-INDEX",
}

# Strike steps (per-index price granularity for option strikes).
# Lot sizes are NOT hardcoded — they come from ``core.symbol_master`` which
# parses the live Fyers symbol master CSV. See ``get_lot_size()`` calls below.
STRIKE_STEPS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
}

# Resolution mapping
RESOLUTION_MAP = {
    "M1": "1", "M5": "5", "M15": "15", "M30": "30",
    "H1": "60", "D1": "D",
}


class FyersLiveFeed:
    """Real-time market data feed using Fyers API v3."""

    def __init__(
        self,
        app_id: str,
        access_token: str,
        secret_key: str = "",
        redirect_uri: str = "",
    ):
        self.app_id = app_id
        self.access_token = access_token
        self.secret_key = secret_key
        self.redirect_uri = redirect_uri

        self._fyers = None
        self._connected = False
        self._last_ticks: dict[str, dict] = {}
        self._refresh_task: asyncio.Task | None = None
        self._ws = None
        self._ws_connected = False

    async def connect(self) -> bool:
        """Initialize Fyers API client and verify connectivity."""
        try:
            from fyers_apiv3 import fyersModel

            # SDK auto-prepends client_id: — pass raw access_token only
            self._fyers = fyersModel.FyersModel(
                client_id=self.app_id,
                token=self.access_token,
                is_async=False,
                log_path="",
            )

            # Verify connection by fetching a profile or a simple quote
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._fyers.quotes(
                    data={"symbols": "NSE:NIFTY50-INDEX"}
                ),
            )

            if response and response.get("s") == "ok" and response.get("d"):
                self._connected = True
                nifty_ltp = response["d"][0]["v"]["lp"]
                logger.info(f"Fyers API connected! NIFTY LTP: {nifty_ltp}")
                return True
            else:
                error_msg = response.get("message", str(response)) if response else "No response"
                logger.error(f"Fyers connection failed: {error_msg}")
                return False

        except Exception as e:
            logger.error(f"Fyers connection error: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def ws_connected(self) -> bool:
        """Whether the WebSocket tick stream is currently alive.

        Distinct from ``is_connected`` (REST session validity) — the WS can
        silently drop while the REST session stays valid.
        """
        return self._ws_connected

    # ------------------------------------------------------------------
    # Quotes / LTP
    # ------------------------------------------------------------------

    async def get_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Fetch quotes for multiple Fyers symbols.

        Args:
            symbols: list of Fyers-format symbols (e.g. ["NSE:NIFTY50-INDEX"])

        Returns:
            list of quote dicts with normalized fields.
        """
        if not self._connected or not self._fyers:
            return []

        try:
            symbols_str = ",".join(symbols)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._fyers.quotes(data={"symbols": symbols_str}),
            )

            if response and response.get("s") == "ok" and response.get("d"):
                results = []
                for item in response["d"]:
                    v = item.get("v", {})
                    n = item.get("n", "")  # Fyers symbol
                    internal_name = _FYERS_TO_INTERNAL.get(n, n)

                    tick = {
                        "symbol": internal_name,
                        "fyers_symbol": n,
                        "ltp": float(v.get("lp", 0)),
                        "open": float(v.get("open_price", 0)),
                        "high": float(v.get("high_price", 0)),
                        "low": float(v.get("low_price", 0)),
                        "prev_close": float(v.get("prev_close_price", 0)),
                        "change": float(v.get("ch", 0)),
                        "change_pct": float(v.get("chp", 0)),
                        "volume": int(v.get("volume", 0)),
                        "bid": float(v.get("bid", 0)),
                        "ask": float(v.get("ask", 0)),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self._last_ticks[internal_name] = tick
                    results.append(tick)
                return results
            else:
                logger.warning(f"Fyers quotes failed: {response}")
                return []

        except Exception as e:
            logger.error(f"Fyers quotes error: {e}")
            return []

    async def get_all_indices(self) -> list[dict[str, Any]]:
        """Fetch all index quotes in a single API call."""
        fyers_symbols = list(INDEX_SYMBOLS.values())
        return await self.get_quotes(fyers_symbols)

    async def get_ltp(self, symbol: str) -> dict[str, Any] | None:
        """Get LTP for a single symbol (by internal name)."""
        fyers_sym = INDEX_SYMBOLS.get(symbol.upper())
        if not fyers_sym:
            return None
        results = await self.get_quotes([fyers_sym])
        return results[0] if results else None

    def get_cached_tick(self, symbol: str) -> dict[str, Any] | None:
        """Return the last cached tick for a symbol (no API call)."""
        return self._last_ticks.get(symbol.upper())

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------

    async def get_option_chain(
        self, symbol: str, strike_count: int = 25, expiry_timestamp: str = ""
    ) -> dict[str, Any]:
        """Fetch option chain for a symbol.

        Args:
            symbol: Internal name (NIFTY, BANKNIFTY, etc.)
            strike_count: Number of strikes around ATM
            expiry_timestamp: epoch timestamp string for specific expiry, or "" for nearest

        Returns:
            dict with spot, expiry, and list of option contracts.
        """
        if not self._connected or not self._fyers:
            return {}

        fyers_sym = OPTION_CHAIN_SYMBOLS.get(symbol.upper())
        if not fyers_sym:
            return {}

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._fyers.optionchain(
                    data={
                        "symbol": fyers_sym,
                        "strikecount": strike_count,
                        "timestamp": expiry_timestamp,
                    }
                ),
            )

            if response and response.get("code") == 200 and response.get("data"):
                raw_chain = response["data"].get("optionsChain", [])
                expiry_data = response["data"].get("expiryData", [])
                vix_data = response["data"].get("indiavixData", {})

                step = STRIKE_STEPS.get(symbol.upper(), 50)
                from core.symbol_master import get_lot_size
                lot = get_lot_size(symbol)

                # First entry (strike_price=-1) is the underlying spot
                spot = 0
                contracts = []
                for opt in raw_chain:
                    strike = opt.get("strike_price", 0)
                    if strike == -1:
                        # This is the spot/underlying entry
                        spot = float(opt.get("ltp", 0))
                        continue

                    opt_type = opt.get("option_type", "")
                    contracts.append({
                        "strike": strike,
                        "option_type": opt_type,
                        "fyers_symbol": opt.get("symbol", ""),
                        "ltp": float(opt.get("ltp", 0)),
                        "bid": float(opt.get("bid", 0)),
                        "ask": float(opt.get("ask", 0)),
                        "volume": int(opt.get("volume", 0)),
                        "oi": int(opt.get("oi", 0)),
                        "prev_oi": int(opt.get("prev_oi", 0)),
                        "oi_change": int(opt.get("oich", 0)),
                        "oi_change_pct": float(opt.get("oichp", 0)),
                        "change": float(opt.get("ltpch", 0)),
                        "change_pct": float(opt.get("ltpchp", 0)),
                    })

                atm_strike = round(spot / step) * step if spot else 0

                return {
                    "symbol": symbol.upper(),
                    "fyers_symbol": fyers_sym,
                    "spot_price": spot,
                    "atm_strike": atm_strike,
                    "lot_size": lot,
                    "expiry_data": expiry_data,
                    "total_call_oi": response["data"].get("callOi", 0),
                    "total_put_oi": response["data"].get("putOi", 0),
                    "india_vix": float(vix_data.get("ltp", 0)),
                    "chain": contracts,
                    "source": "fyers_live",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            else:
                logger.warning(f"Fyers option chain failed: {response}")
                return {}

        except Exception as e:
            logger.error(f"Fyers option chain error: {e}")
            return {}

    # ------------------------------------------------------------------
    # Historical / Candle Data
    # ------------------------------------------------------------------

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "D1",
        count: int = 50,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch historical candle data — cache-first with Fyers fill.

        Strategy:
        1. Check SQLite candle cache for the requested range
        2. If cache has enough data, return it (zero API calls)
        3. If cache is empty/stale, fetch from Fyers with retry on 429
        4. Cache the result for next time

        Args:
            symbol: Internal name (NIFTY, BANKNIFTY, etc.)
            timeframe: M1, M5, M15, M30, H1, D1
            count: Number of candles to return
            use_cache: If True (default), check SQLite first

        Returns:
            list of candle dicts with timestamp, open, high, low, close, volume.
        """
        resolution = RESOLUTION_MAP.get(timeframe, "D")

        # ── 1. Try SQLite cache first (with staleness check) ─────────
        if use_cache:
            try:
                from core.state_store import get_store
                store = get_store()
                cached = store.get_candles(symbol.upper(), resolution, limit=count + 50)
                if cached and len(cached) >= min(count, 3):
                    newest_ts = max(c["ts"] for c in cached)
                    age_seconds = datetime.now(timezone.utc).timestamp() - newest_ts
                    max_age = 7200 if resolution != "D" else 172800  # 2h intraday, 2d daily
                    if age_seconds <= max_age:
                        result = cached[-count:] if len(cached) > count else cached
                        return [
                            {
                                "timestamp": datetime.fromtimestamp(c["ts"], tz=timezone.utc).isoformat(),
                                "open": c["open"],
                                "high": c["high"],
                                "low": c["low"],
                                "close": c["close"],
                                "volume": c["volume"],
                            }
                            for c in result
                        ]
                    else:
                        logger.debug(f"Candle cache stale for {symbol}/{resolution} (age={age_seconds:.0f}s), fetching fresh")
            except Exception as e:
                logger.debug(f"Candle cache read failed: {e}")

        # ── 2. Fetch from Fyers (with retry on 429) ────────────────
        if not self._connected or not self._fyers:
            return []

        fyers_sym = INDEX_SYMBOLS.get(symbol.upper())
        if not fyers_sym:
            return []

        now = datetime.now()
        if resolution == "D":
            days_back = count * 2
        else:
            minutes = int(resolution) if resolution.isdigit() else 60
            trading_mins_per_day = 375  # 09:15-15:30 IST
            days_back = max(10, (count * minutes) // trading_mins_per_day + 5)

        range_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        range_to = now.strftime("%Y-%m-%d")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._fyers.history(
                        data={
                            "symbol": fyers_sym,
                            "resolution": resolution,
                            "date_format": "1",
                            "range_from": range_from,
                            "range_to": range_to,
                            "cont_flag": "1",
                        }
                    ),
                )

                # Rate limited — wait and retry
                if response and response.get("code") == 429:
                    wait = (attempt + 1) * 2  # 2s, 4s, 6s
                    logger.warning(f"Fyers rate limited (429), retry in {wait}s (attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(wait)
                    continue

                if response and response.get("s") == "ok" and response.get("candles"):
                    raw = response["candles"]
                    raw = raw[-count:] if len(raw) > count else raw

                    candles = []
                    cache_rows = []
                    for c in raw:
                        ts = c[0]
                        if isinstance(ts, (int, float)):
                            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                        else:
                            dt = datetime.now(timezone.utc)

                        candles.append({
                            "timestamp": dt.isoformat(),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]),
                        })
                        cache_rows.append({
                            "ts": int(ts) if isinstance(ts, (int, float)) else int(dt.timestamp()),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]),
                        })

                    # ── 3. Cache to SQLite ──────────────────────────
                    if cache_rows:
                        try:
                            from core.state_store import get_store
                            store = get_store()
                            store.save_candles(symbol.upper(), resolution, cache_rows)
                            logger.info(f"Cached {len(cache_rows)} {resolution} candles for {symbol}")
                        except Exception as e:
                            logger.debug(f"Candle cache write failed: {e}")

                    return candles
                else:
                    msg = response.get("message", "") if response else "no response"
                    logger.warning(f"Fyers candles failed: code={response.get('code', '?')}, msg={msg}")
                    return []

            except Exception as e:
                logger.error(f"Fyers candles error (attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return []

        return []

    async def fetch_history_range(
        self,
        symbol: str,
        resolution: str,
        from_date: str,
        to_date: str,
    ) -> list[dict]:
        """Fetch candles for a specific date range (for backtest bulk download).

        Args:
            symbol: NIFTY, BANKNIFTY, etc.
            resolution: D, 5, 15, 30, 60, etc. (Fyers format)
            from_date: YYYY-MM-DD
            to_date: YYYY-MM-DD

        Returns:
            list of raw candle dicts [{ts, open, high, low, close, volume}]
        """
        if not self._connected or not self._fyers:
            return []

        fyers_sym = INDEX_SYMBOLS.get(symbol.upper())
        if not fyers_sym:
            return []

        max_retries = 3
        for attempt in range(max_retries):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._fyers.history(
                        data={
                            "symbol": fyers_sym,
                            "resolution": resolution,
                            "date_format": "1",
                            "range_from": from_date,
                            "range_to": to_date,
                            "cont_flag": "1",
                        }
                    ),
                )

                if response and response.get("code") == 429:
                    wait = (attempt + 1) * 3
                    logger.warning(f"History rate limited, retry in {wait}s")
                    await asyncio.sleep(wait)
                    continue

                if response and response.get("s") == "ok" and response.get("candles"):
                    return [
                        {
                            "ts": int(c[0]),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": int(c[5]),
                        }
                        for c in response["candles"]
                    ]
                else:
                    msg = response.get("message", "") if response else "no response"
                    logger.warning(f"History fetch failed: {msg}")
                    return []

            except Exception as e:
                logger.error(f"History fetch error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return []

        return []

    # ------------------------------------------------------------------
    # Market Depth
    # ------------------------------------------------------------------

    async def get_market_depth(self, symbol: str) -> dict[str, Any] | None:
        """Fetch market depth (order book) for a symbol."""
        if not self._connected or not self._fyers:
            return None

        fyers_sym = INDEX_SYMBOLS.get(symbol.upper(), symbol)

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._fyers.depth(
                    data={"symbol": fyers_sym, "ohlcv_flag": "1"}
                ),
            )

            if response and response.get("s") == "ok":
                return response.get("d", {})
            return None

        except Exception as e:
            logger.error(f"Fyers depth error: {e}")
            return None

    # ------------------------------------------------------------------
    # Background Refresh
    # ------------------------------------------------------------------

    async def start_background_refresh(self, interval: float = 3.0) -> None:
        """Start a background task that refreshes all index quotes periodically."""
        if self._refresh_task and not self._refresh_task.done():
            return

        async def _refresh_loop():
            while self._connected:
                try:
                    await self.get_all_indices()
                except Exception as e:
                    logger.warning(f"Background refresh error: {e}")
                await asyncio.sleep(interval)
            logger.info("Fyers background refresh loop stopped")

        self._refresh_task = asyncio.create_task(_refresh_loop())
        logger.info(f"Started Fyers background index refresh every {interval}s")

    async def stop_background_refresh(self) -> None:
        """Stop the background refresh task."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    # ------------------------------------------------------------------
    # WebSocket Streaming
    # ------------------------------------------------------------------

    def start_websocket_stream(
        self,
        symbols: list[str] | None = None,
        on_tick: Callable[[dict], None] | None = None,
    ) -> None:
        """Start WebSocket streaming for live ticks (runs in background thread)."""
        import threading

        if symbols is None:
            symbols = list(INDEX_SYMBOLS.values())

        ws_access_token = f"{self.app_id}:{self.access_token}"

        def _on_message(message):
            if isinstance(message, dict):
                sym = message.get("symbol", "")
                internal = _FYERS_TO_INTERNAL.get(sym, sym)
                tick = {
                    "symbol": internal,
                    "fyers_symbol": sym,
                    "ltp": float(message.get("ltp", 0)),
                    "open": float(message.get("open_price", 0)),
                    "high": float(message.get("high_price", 0)),
                    "low": float(message.get("low_price", 0)),
                    "prev_close": float(message.get("prev_close_price", 0)),
                    "change": float(message.get("ch", 0)),
                    "change_pct": float(message.get("chp", 0)),
                    "volume": int(message.get("volume", 0)),
                    "bid": float(message.get("bid", 0)),
                    "ask": float(message.get("ask", 0)),
                    "oi": int(message.get("oi", 0)),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._last_ticks[internal] = tick
                if on_tick:
                    on_tick(tick)

        def _on_open():
            from fyers_apiv3.FyersWebsocket import data_ws
            logger.info(f"Fyers WebSocket connected, subscribing to {symbols}")
            self._ws.subscribe(symbols=symbols, data_type="SymbolUpdate")
            self._ws.keep_running()
            self._ws_connected = True

        def _on_error(msg):
            logger.error(f"Fyers WS error: {msg}")

        def _on_close(msg):
            logger.info(f"Fyers WS closed: {msg}")
            self._ws_connected = False

        try:
            from fyers_apiv3.FyersWebsocket import data_ws

            self._ws = data_ws.FyersDataSocket(
                access_token=ws_access_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=_on_open,
                on_close=_on_close,
                on_error=_on_error,
                on_message=_on_message,
            )

            ws_thread = threading.Thread(target=self._ws.connect, daemon=True)
            ws_thread.start()
            logger.info("Fyers WebSocket stream started in background thread")

        except Exception as e:
            logger.error(f"Failed to start Fyers WS: {e}")

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        """Disconnect from Fyers API."""
        await self.stop_background_refresh()
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass
            self._ws = None
        self._connected = False
        self._ws_connected = False
        logger.info("Fyers feed disconnected")

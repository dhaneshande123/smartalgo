"""
Angel One SmartAPI Live Data Feed.

Connects to Angel One SmartAPI to fetch real market data and stream it
through the platform's WebSocket channels.

Usage:
    feed = AngelLiveFeed(api_key, client_id, password, totp_secret)
    await feed.connect()
    ltp = await feed.get_ltp("NIFTY")
    await feed.start_streaming(["NIFTY", "BANKNIFTY"])
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import pyotp

logger = logging.getLogger(__name__)

# Index symbol tokens for Angel One SmartAPI
INDEX_TOKENS = {
    "NIFTY": {"token": "99926000", "exchange": "NSE", "name": "NIFTY 50"},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE", "name": "BANK NIFTY"},
    "FINNIFTY": {"token": "99926037", "exchange": "NSE", "name": "FINNIFTY"},
    "MIDCPNIFTY": {"token": "99926074", "exchange": "NSE", "name": "MIDCAP NIFTY"},
    "SENSEX": {"token": "99919000", "exchange": "BSE", "name": "SENSEX"},
    "INDIA VIX": {"token": "99926004", "exchange": "NSE", "name": "INDIA VIX"},
}

# Exchange type codes for WebSocket
EXCHANGE_TYPES = {
    "NSE": 1,   # NSE Cash
    "NFO": 2,   # NSE F&O
    "BSE": 3,   # BSE Cash
    "BFO": 4,   # BSE F&O
    "MCX": 5,   # MCX
}


class AngelLiveFeed:
    """Real-time market data feed using Angel One SmartAPI."""

    def __init__(
        self,
        api_key: str,
        client_id: str,
        password: str,
        totp_secret: str,
    ):
        self.api_key = api_key
        self.client_id = client_id
        self.password = password
        self.totp_secret = totp_secret

        self._smart_api = None
        self._auth_token = None
        self._feed_token = None
        self._refresh_token = None
        self._ws = None
        self._connected = False
        self._callbacks: list[Callable] = []
        self._last_ticks: dict[str, dict] = {}
        self._refresh_task: asyncio.Task | None = None

    async def connect(self) -> bool:
        """Authenticate with Angel One and establish session."""
        try:
            from SmartApi import SmartConnect

            self._smart_api = SmartConnect(api_key=self.api_key)

            # Generate TOTP
            totp = pyotp.TOTP(self.totp_secret).now()
            logger.info(f"Authenticating with Angel One as {self.client_id}...")

            # Login — this is a blocking call, run in executor
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: self._smart_api.generateSession(
                    self.client_id,
                    self.password,
                    totp,
                ),
            )

            if data and data.get("status"):
                self._auth_token = data["data"]["jwtToken"]
                self._refresh_token = data["data"]["refreshToken"]
                self._feed_token = self._smart_api.getfeedToken()
                self._connected = True
                logger.info("Angel One authentication successful!")
                return True
            else:
                error_msg = data.get("message", "Unknown error") if data else "No response"
                logger.error(f"Angel One login failed: {error_msg}")
                return False

        except Exception as e:
            logger.error(f"Angel One connection error: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def get_ltp(self, symbol: str) -> dict[str, Any] | None:
        """Get Last Traded Price for a symbol."""
        if not self._connected or not self._smart_api:
            return None

        token_info = INDEX_TOKENS.get(symbol.upper())
        if not token_info:
            logger.warning(f"Unknown symbol: {symbol}")
            return None

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: self._smart_api.ltpData(
                    token_info["exchange"],
                    token_info["name"],
                    token_info["token"],
                ),
            )

            if data and data.get("status"):
                ltp_data = data["data"]
                ltp = float(ltp_data.get("ltp", 0))
                open_ = float(ltp_data.get("open", 0))
                high = float(ltp_data.get("high", 0))
                low = float(ltp_data.get("low", 0))
                close = float(ltp_data.get("close", 0))

                # Angel One returns INDIA VIX scaled by 1000x
                if symbol.upper() == "INDIA VIX":
                    ltp = round(ltp / 1000, 2)
                    open_ = round(open_ / 1000, 2)
                    high = round(high / 1000, 2)
                    low = round(low / 1000, 2)
                    close = round(close / 1000, 2)

                result = {
                    "symbol": symbol,
                    "ltp": ltp,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": int(ltp_data.get("volume", 0) or 0),
                    "exchange": token_info["exchange"],
                    "token": token_info["token"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._last_ticks[symbol] = result
                return result
            else:
                logger.warning(f"LTP fetch failed for {symbol}: {data}")
                return None

        except Exception as e:
            logger.error(f"Error fetching LTP for {symbol}: {e}")
            return None

    async def get_all_indices(self) -> list[dict[str, Any]]:
        """Fetch LTP for all configured index symbols."""
        results = []
        for symbol in INDEX_TOKENS:
            data = await self.get_ltp(symbol)
            if data:
                results.append(data)
        return results

    def get_cached_tick(self, symbol: str) -> dict[str, Any] | None:
        """Return the last cached tick for a symbol (no API call)."""
        return self._last_ticks.get(symbol.upper())

    async def start_background_refresh(self, interval: float = 3.0) -> None:
        """Start a background task that refreshes all index LTPs periodically."""
        if self._refresh_task and not self._refresh_task.done():
            return  # already running

        async def _refresh_loop():
            while self._connected:
                try:
                    for symbol in INDEX_TOKENS:
                        if not self._connected:
                            break
                        await self.get_ltp(symbol)
                        await asyncio.sleep(0.3)  # small delay between calls to avoid rate limits
                except Exception as e:
                    logger.warning(f"Background refresh error: {e}")
                await asyncio.sleep(interval)
            logger.info("Background refresh loop stopped")

        self._refresh_task = asyncio.create_task(_refresh_loop())
        logger.info(f"Started background index refresh every {interval}s")

    async def stop_background_refresh(self) -> None:
        """Stop the background refresh task."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    async def get_market_quote(self, exchange: str, symbol_name: str, token: str) -> dict | None:
        """Get full market quote for any instrument."""
        if not self._connected:
            return None

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: self._smart_api.ltpData(exchange, symbol_name, token),
            )
            if data and data.get("status"):
                return data["data"]
            return None
        except Exception as e:
            logger.error(f"Quote error: {e}")
            return None

    async def get_candle_data(
        self,
        exchange: str,
        token: str,
        interval: str = "ONE_DAY",
        from_date: str = "",
        to_date: str = "",
    ) -> list[dict] | None:
        """Fetch historical candle data."""
        if not self._connected:
            return None

        if not from_date:
            from datetime import timedelta
            end = datetime.now()
            start = end - timedelta(days=365)
            from_date = start.strftime("%Y-%m-%d 09:15")
            to_date = end.strftime("%Y-%m-%d 15:30")

        try:
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date,
            }
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: self._smart_api.getCandleData(params),
            )
            if data and data.get("status"):
                return data["data"]
            return None
        except Exception as e:
            logger.error(f"Candle data error: {e}")
            return None

    def start_websocket_stream(
        self,
        symbols: list[str],
        on_tick: Callable[[dict], None] | None = None,
    ):
        """
        Start WebSocket streaming for given symbols.
        This runs in a background thread (SmartWebSocketV2 is synchronous).
        """
        if not self._connected:
            logger.error("Not connected. Call connect() first.")
            return

        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2

            # Build token list
            token_list = []
            for sym in symbols:
                info = INDEX_TOKENS.get(sym.upper())
                if info:
                    exchange_type = EXCHANGE_TYPES.get(info["exchange"], 1)
                    token_list.append({
                        "exchangeType": exchange_type,
                        "tokens": [info["token"]],
                    })

            if not token_list:
                logger.warning("No valid symbols for WebSocket")
                return

            self._ws = SmartWebSocketV2(
                self._auth_token,
                self.api_key,
                self.client_id,
                self._feed_token,
            )

            # Reverse lookup: token -> symbol
            token_to_symbol = {}
            for sym, info in INDEX_TOKENS.items():
                token_to_symbol[info["token"]] = sym

            def on_data(wsapp, message):
                """Parse binary WebSocket message into tick data."""
                try:
                    if isinstance(message, dict):
                        token = str(message.get("token", ""))
                        symbol = token_to_symbol.get(token, token)
                        tick = {
                            "symbol": symbol,
                            "ltp": message.get("last_traded_price", 0) / 100,
                            "open": message.get("open_price_of_the_day", 0) / 100,
                            "high": message.get("high_price_of_the_day", 0) / 100,
                            "low": message.get("low_price_of_the_day", 0) / 100,
                            "close": message.get("closed_price", 0) / 100,
                            "volume": message.get("volume_trade_for_the_day", 0),
                            "bid": message.get("best_5_buy_data", [{}])[0].get("price", 0) / 100 if message.get("best_5_buy_data") else 0,
                            "ask": message.get("best_5_sell_data", [{}])[0].get("price", 0) / 100 if message.get("best_5_sell_data") else 0,
                            "oi": message.get("open_interest", 0),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        self._last_ticks[symbol] = tick
                        if on_tick:
                            on_tick(tick)
                except Exception as e:
                    logger.error(f"WS tick parse error: {e}")

            def on_open(wsapp):
                logger.info("Angel One WebSocket connected, subscribing...")
                # Mode 2 = Quote (full data), Mode 1 = LTP only
                self._ws.subscribe("smartalgo01", 2, token_list)

            def on_error(wsapp, error):
                logger.error(f"Angel One WS error: {error}")

            def on_close(wsapp):
                logger.info("Angel One WebSocket closed")

            self._ws.on_open = on_open
            self._ws.on_data = on_data
            self._ws.on_error = on_error
            self._ws.on_close = on_close

            import threading
            ws_thread = threading.Thread(
                target=self._ws.connect,
                daemon=True,
                name="angel-ws-feed",
            )
            ws_thread.start()
            logger.info(f"Angel One WebSocket streaming started for {symbols}")

        except Exception as e:
            logger.error(f"WebSocket start error: {e}")

    async def disconnect(self):
        """Logout and cleanup."""
        try:
            if self._ws:
                self._ws.close_connection()
            if self._smart_api:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self._smart_api.terminateSession(self.client_id),
                )
            self._connected = False
            logger.info("Angel One disconnected")
        except Exception as e:
            logger.error(f"Disconnect error: {e}")

    def get_last_tick(self, symbol: str) -> dict | None:
        """Get the last received tick for a symbol."""
        return self._last_ticks.get(symbol.upper())

    def get_all_last_ticks(self) -> dict[str, dict]:
        """Get all last received ticks."""
        return dict(self._last_ticks)

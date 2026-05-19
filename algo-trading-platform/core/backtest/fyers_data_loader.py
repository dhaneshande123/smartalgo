"""
Fyers historical data loader for backtesting.

Downloads historical candle data from Fyers API and saves as CSV files
compatible with BacktestEngine.
"""

from __future__ import annotations

import csv
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Fyers symbol mapping
FYERS_SYMBOLS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}

RESOLUTION_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "D": "D",
    "1D": "D",
}


async def fetch_fyers_historical_csv(
    fyers_client,
    symbol: str,
    start_date: str,
    end_date: str,
    resolution: str = "5",
) -> str | None:
    """Fetch historical data from Fyers and save as CSV.

    Args:
        fyers_client: Initialized FyersModel instance.
        symbol: Internal symbol name (e.g. "NIFTY").
        start_date: Start date as "YYYY-MM-DD".
        end_date: End date as "YYYY-MM-DD".
        resolution: Candle resolution ("1", "5", "15", "30", "60", "D").

    Returns:
        Path to the generated CSV file, or None on failure.
    """
    import asyncio

    fyers_sym = FYERS_SYMBOLS.get(symbol.upper(), symbol)
    res = RESOLUTION_MAP.get(resolution, resolution)

    logger.info(f"Fetching Fyers history: {fyers_sym}, {start_date} to {end_date}, res={res}")

    # Fyers limits: intraday = 100 days/request, daily = 366 days/request
    all_candles = []
    chunk_days = 90 if res != "D" else 350
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    current = start_dt
    while current < end_dt:
        chunk_end = min(current + timedelta(days=chunk_days), end_dt)
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda c=current, ce=chunk_end: fyers_client.history(
                    data={
                        "symbol": fyers_sym,
                        "resolution": res,
                        "date_format": "1",
                        "range_from": c.strftime("%Y-%m-%d"),
                        "range_to": ce.strftime("%Y-%m-%d"),
                        "cont_flag": "1",
                    }
                ),
            )

            if response and response.get("s") == "ok" and response.get("candles"):
                all_candles.extend(response["candles"])
                logger.info(
                    f"  Chunk {current.date()} to {chunk_end.date()}: "
                    f"{len(response['candles'])} candles"
                )
            else:
                logger.warning(f"  Chunk failed: {response}")

        except Exception as e:
            logger.error(f"  Chunk error: {e}")

        current = chunk_end + timedelta(days=1)

    if not all_candles:
        logger.error("No candles fetched from Fyers")
        return None

    # Deduplicate by timestamp
    seen = set()
    unique = []
    for c in all_candles:
        ts = c[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(c)
    unique.sort(key=lambda x: x[0])

    # Write to temp CSV
    # Format: Date,Open,High,Low,Close,Volume
    tmp_dir = os.path.join(tempfile.gettempdir(), "smartalgo_backtest")
    os.makedirs(tmp_dir, exist_ok=True)
    csv_path = os.path.join(
        tmp_dir,
        f"{symbol}_{res}_{start_date}_{end_date}.csv",
    )

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        for candle in unique:
            # candle = [timestamp, open, high, low, close, volume]
            ts = candle[0]
            if isinstance(ts, (int, float)):
                dt = datetime.utcfromtimestamp(ts)
                date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                date_str = str(ts)
            writer.writerow([
                date_str,
                candle[1],
                candle[2],
                candle[3],
                candle[4],
                candle[5],
            ])

    logger.info(f"Saved {len(unique)} candles to {csv_path}")
    return csv_path

"""
Fyers symbol master loader.

Downloads the public Fyers symbol master CSVs (NSE F&O, BSE F&O) and exposes
authoritative lot sizes and tick sizes for all derivative contracts.

The CSVs are refreshed once per trading day and cached on disk under
``data/cache/`` so the platform doesn't repeat the download on every restart.

Public API
----------
- ``refresh(force=False) -> dict``    : ensure caches are loaded, return summary
- ``get_lot_size(symbol) -> int``     : authoritative lot size for an underlying
- ``get_lot_sizes() -> dict``         : full {symbol: lot_size} mapping
- ``get_tick_size(symbol) -> float``  : tick size (defaults to 0.05 for FNO)

Source
------
- NSE F&O : https://public.fyers.in/sym_details/NSE_FO.csv
- BSE F&O : https://public.fyers.in/sym_details/BSE_FO.csv

The CSVs are pipe-free comma-separated with no header. The column layout
(verified against live Fyers v3 data as of May 2026):

    0  fy_token (numeric)
    1  symbol_description     (e.g. "BANKNIFTY 26 May 26 FUT")
    2  exchange_token
    3  lot_size
    4  tick_size
    ...
    13 underlying_symbol      (e.g. "BANKNIFTY", "RELIANCE")
    ...
"""

from __future__ import annotations

import csv
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_SOURCES = {
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "BSE_FO": "https://public.fyers.in/sym_details/BSE_FO.csv",
}

# Re-download if the cached file is older than this many seconds (24h)
_REFRESH_INTERVAL = 24 * 3600

# Sensible fallback values used when network is unavailable on a fresh install.
# These are sourced from Fyers symbol master as of May 2026 — they are NOT
# the authoritative source. The CSV download is. Keep them updated whenever
# you sync a known-good snapshot.
_FALLBACK_LOT_SIZES: dict[str, int] = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
    "NIFTYNXT50": 25,
    "SENSEX": 20,
    "BANKEX": 30,
}


# ---------------------------------------------------------------------------
# Module state (caches)
# ---------------------------------------------------------------------------

_lot_sizes: dict[str, int] = {}
_tick_sizes: dict[str, float] = {}
_last_refresh_ts: float = 0.0
_last_refresh_source: str = "uninitialised"  # "cache", "network", "fallback"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cache_file_for(key: str) -> Path:
    return _CACHE_DIR / f"fyers_{key.lower()}.csv"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < _REFRESH_INTERVAL


def _download_csv(url: str, target: Path, timeout: int = 30) -> bool:
    """Download a CSV to ``target``. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "smartalgo/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        target.write_bytes(data)
        logger.info(f"Downloaded {url} -> {target} ({len(data)} bytes)")
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning(f"Failed to download {url}: {e}")
        return False


def _parse_csv(path: Path) -> tuple[dict[str, int], dict[str, float]]:
    """Parse a Fyers symbol master CSV. Returns (lot_sizes, tick_sizes)."""
    lots: dict[str, int] = {}
    ticks: dict[str, float] = {}

    if not path.exists():
        return lots, ticks

    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 14:
                continue
            try:
                lot_size = int(row[3])
                tick_size = float(row[4])
                underlying = row[13].strip().upper()
            except (ValueError, IndexError):
                continue

            if not underlying or lot_size <= 0:
                continue

            # Keep the first (smallest) lot size we encounter per underlying.
            # Different expiries of the same underlying always share the same
            # lot size, so first-wins is correct.
            if underlying not in lots:
                lots[underlying] = lot_size
                ticks[underlying] = tick_size

    return lots, ticks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh(force: bool = False) -> dict[str, Any]:
    """Ensure the symbol master cache is loaded.

    Behaviour:
        * If a fresh cached file (< 24h old) exists and ``force`` is False,
          parse it without any network call.
        * Otherwise attempt to (re)download the public CSV.
        * If the download fails AND there's no cached file, fall back to the
          hardcoded ``_FALLBACK_LOT_SIZES`` so the platform stays usable
          offline.

    Returns a small summary dict suitable for logging / health checks.
    """
    global _lot_sizes, _tick_sizes, _last_refresh_ts, _last_refresh_source

    aggregated_lots: dict[str, int] = {}
    aggregated_ticks: dict[str, float] = {}
    network_used = False
    files_ok = 0

    for key, url in _SOURCES.items():
        cache_path = _cache_file_for(key)
        need_download = force or not _is_cache_fresh(cache_path)

        if need_download:
            if _download_csv(url, cache_path):
                network_used = True
            elif not cache_path.exists():
                # Network failed AND no stale copy on disk — skip this source
                logger.warning(f"No cached {key} CSV and download failed")
                continue

        lots, ticks = _parse_csv(cache_path)
        if lots:
            files_ok += 1
            aggregated_lots.update(lots)
            aggregated_ticks.update(ticks)

    if aggregated_lots:
        _lot_sizes = aggregated_lots
        _tick_sizes = aggregated_ticks
        _last_refresh_source = "network" if network_used else "cache"
    else:
        # Total failure — populate from hardcoded fallback so callers don't crash.
        _lot_sizes = dict(_FALLBACK_LOT_SIZES)
        _tick_sizes = {sym: 0.05 for sym in _FALLBACK_LOT_SIZES}
        _last_refresh_source = "fallback"
        logger.error(
            "Symbol master refresh fell back to hardcoded values — "
            "no cached CSV and network unreachable"
        )

    _last_refresh_ts = time.time()

    return {
        "source": _last_refresh_source,
        "symbols_loaded": len(_lot_sizes),
        "files_parsed": files_ok,
        "refreshed_at": datetime.utcnow().isoformat() + "Z",
    }


def get_lot_size(symbol: str, default: int | None = None) -> int:
    """Return the lot size for an underlying symbol (e.g. ``"NIFTY"``).

    Loads the cache on first call. Falls back to ``default`` (or the hardcoded
    map, or 1) if the symbol is unknown.
    """
    if not _lot_sizes:
        refresh()
    sym = (symbol or "").upper().strip()
    if sym in _lot_sizes:
        return _lot_sizes[sym]
    if default is not None:
        return default
    return _FALLBACK_LOT_SIZES.get(sym, 1)


def get_tick_size(symbol: str, default: float = 0.05) -> float:
    """Return the tick size for an underlying symbol."""
    if not _tick_sizes:
        refresh()
    return _tick_sizes.get((symbol or "").upper().strip(), default)


def get_lot_sizes() -> dict[str, int]:
    """Return a copy of all known lot sizes (loads cache on first call)."""
    if not _lot_sizes:
        refresh()
    return dict(_lot_sizes)


def get_status() -> dict[str, Any]:
    """Status info for /api/health and /api/instruments/lot-sizes."""
    return {
        "loaded": bool(_lot_sizes),
        "source": _last_refresh_source,
        "symbols_count": len(_lot_sizes),
        "last_refresh": (
            datetime.fromtimestamp(_last_refresh_ts).isoformat() + "Z"
            if _last_refresh_ts
            else None
        ),
    }

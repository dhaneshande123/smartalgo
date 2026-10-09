"""
Fly-High Strategy Engine — VWAP Crossover + SuperTrend Confluence on 5-minute candles.

Entry: 5-min candle crosses VWAP AND SuperTrend direction confirms (confluence filter)
Gate:  ADX >= 10, skip first candle (9:15-9:20), max 2 trades/day, no entry after 15:15
Strike: 1-ITM (higher delta, lower theta drag than ATM)
SL:    Previous candle low (BUY) / high (SELL), capped at max_sl_points
Exit:  Book 50% at 1:1 R:R, trail rest, max hold 45 min, EOD 15:15
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, time as dtime
from typing import Any, Optional

import numpy as np

from core import indicators as ind
from core.constants import IST
from core.scalper_engine import normalize_chain_rows

logger = logging.getLogger(__name__)

STRIKE_STEPS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
}

DEFAULT_CONFIG = {
    "enabled": True,
    "risk_per_trade": 2000,
    "adx_min": 10,
    "max_sl_points": 30,
    "max_lots": 10,
    "max_trades_per_day": 2,
    "entry_start": "09:20",
    "entry_cutoff": "15:15",
    "max_hold_minutes": 45,
    "supertrend_period": 10,
    "supertrend_multiplier": 3.0,
    "book_partial_pct": 50,
    "trail_giveback_pct": 30,
    "premium_floor_pct": -35,
    "min_oi": 50000,
    "min_volume": 500,
    "max_spread_pct": 3.0,
    "auto_deploy": False,
    "auto_symbols": ["NIFTY"],
}

_flyhigh_config: dict[str, Any] = {**DEFAULT_CONFIG}


def get_config() -> dict[str, Any]:
    return _flyhigh_config


def update_config(patch: dict) -> dict[str, Any]:
    allowed = set(DEFAULT_CONFIG.keys())
    for k, v in patch.items():
        if k in allowed:
            _flyhigh_config[k] = v
    return _flyhigh_config


@dataclass
class FlyHighSignal:
    has_signal: bool = False
    direction: str = ""
    reason: str = ""
    spot: float = 0.0
    vwap: float = 0.0
    adx: float = 0.0
    strike: int = 0
    option_type: str = ""
    premium: float = 0.0
    lots: int = 0
    qty: int = 0
    sl_price: float = 0.0
    sl_points: float = 0.0
    target_price: float = 0.0
    blockers: list = field(default_factory=list)
    deploy_payload: dict = field(default_factory=dict)
    candle_info: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_vwap_series(candles: list[dict]) -> list[float]:
    """Compute cumulative VWAP for each candle in the series."""
    if not candles:
        return []
    highs = np.array([float(c.get("high", 0)) for c in candles])
    lows = np.array([float(c.get("low", 0)) for c in candles])
    closes = np.array([float(c.get("close", 0)) for c in candles])
    volumes = np.array([float(c.get("volume", 0)) for c in candles])

    typical = (highs + lows + closes) / 3.0
    cum_pv = np.cumsum(typical * volumes)
    cum_v = np.cumsum(volumes)
    cum_v[cum_v == 0] = 1
    return (cum_pv / cum_v).tolist()


def detect_crossover(candles: list[dict], vwap_series: list[float]) -> dict:
    """Detect VWAP crossover on the last closed candle.

    Returns dict with: crossed (bool), direction (BULLISH/BEARISH),
    close, prev_close, vwap, prev_vwap, prev_candle_low, prev_candle_high.
    """
    if len(candles) < 3 or len(vwap_series) < 3:
        return {"crossed": False, "reason": "insufficient candles"}

    # Use candles[-2] as the "just closed" candle, candles[-3] as the one before
    curr = candles[-2]
    prev = candles[-3]
    curr_close = float(curr.get("close", 0))
    prev_close = float(prev.get("close", 0))
    curr_vwap = vwap_series[-2]
    prev_vwap = vwap_series[-3]

    result = {
        "crossed": False,
        "direction": "",
        "close": curr_close,
        "prev_close": prev_close,
        "vwap": curr_vwap,
        "prev_vwap": prev_vwap,
        "prev_candle_low": float(prev.get("low", 0)),
        "prev_candle_high": float(prev.get("high", 0)),
        "curr_candle_low": float(curr.get("low", 0)),
        "curr_candle_high": float(curr.get("high", 0)),
    }

    if prev_close <= prev_vwap and curr_close > curr_vwap:
        result["crossed"] = True
        result["direction"] = "BULLISH"
    elif prev_close >= prev_vwap and curr_close < curr_vwap:
        result["crossed"] = True
        result["direction"] = "BEARISH"

    return result


def select_itm_strike(
    spot: float, direction: str, chain: dict, strike_step: int, cfg: dict,
) -> Optional[dict]:
    """Select 1-ITM strike (higher delta than ATM).

    For BULLISH (CE): one strike below ATM.
    For BEARISH (PE): one strike above ATM.
    """
    rows = normalize_chain_rows(chain)
    if not rows:
        return None

    atm = round(spot / strike_step) * strike_step
    option_type = "CE" if direction == "BULLISH" else "PE"

    if direction == "BULLISH":
        target = atm - strike_step  # 1-ITM for CE
    else:
        target = atm + strike_step  # 1-ITM for PE

    candidates = [target, atm, target - strike_step, atm + strike_step]
    if direction == "BEARISH":
        candidates = [target, atm, target + strike_step, atm - strike_step]

    side = "call" if option_type == "CE" else "put"
    for strike in candidates:
        row = rows.get(strike)
        if not row:
            continue
        premium = float(row.get(f"{side}_ltp", 0) or 0)
        oi = int(row.get(f"{side}_oi", 0) or 0)
        vol = int(row.get(f"{side}_volume", 0) or 0)
        bid = float(row.get(f"{side}_bid", 0) or 0)
        ask = float(row.get(f"{side}_ask", 0) or 0)
        spread_pct = ((ask - bid) / premium * 100) if premium > 0 else 999

        if premium <= 0:
            continue
        if oi < cfg.get("min_oi", 50000):
            continue
        if vol < cfg.get("min_volume", 500):
            continue
        if spread_pct > cfg.get("max_spread_pct", 3.0):
            continue

        return {
            "strike": strike,
            "option_type": option_type,
            "premium": premium,
            "oi": oi,
            "volume": vol,
            "spread_pct": round(spread_pct, 2),
        }

    return None


def generate_signal(
    symbol: str,
    spot: float,
    intraday_candles: list[dict],
    chain: dict,
    strike_step: int,
    lot_size: int,
    cfg: dict,
    now: Optional[datetime] = None,
    trades_today: int = 0,
) -> FlyHighSignal:
    """Generate a Fly-High VWAP crossover signal."""
    if now is None:
        now = datetime.now(IST)
    now_t = now.time()

    sig = FlyHighSignal(spot=spot)
    blockers = []

    # Time window check
    entry_start = dtime.fromisoformat(cfg.get("entry_start", "09:20"))
    entry_cutoff = dtime.fromisoformat(cfg.get("entry_cutoff", "14:30"))
    if now_t < entry_start:
        blockers.append(f"before entry window ({cfg['entry_start']})")
    if now_t > entry_cutoff:
        blockers.append(f"past entry cutoff ({cfg['entry_cutoff']})")

    # Max trades per day
    max_trades = cfg.get("max_trades_per_day", 2)
    if trades_today >= max_trades:
        blockers.append(f"max trades reached ({trades_today}/{max_trades})")

    # Need enough candles (at least 3 for crossover + some for ADX)
    if len(intraday_candles) < 15:
        blockers.append("insufficient candle data")
        sig.blockers = blockers
        sig.reason = "; ".join(blockers)
        return sig

    # Compute VWAP series
    vwap_series = compute_vwap_series(intraday_candles)
    if not vwap_series:
        blockers.append("VWAP computation failed")
        sig.blockers = blockers
        sig.reason = "; ".join(blockers)
        return sig

    sig.vwap = round(vwap_series[-2], 2) if len(vwap_series) >= 2 else 0.0

    # ADX regime gate
    highs = [float(c.get("high", 0)) for c in intraday_candles]
    lows = [float(c.get("low", 0)) for c in intraday_candles]
    closes = [float(c.get("close", 0)) for c in intraday_candles]
    adx_result = ind.adx(highs, lows, closes, period=14)
    adx_val = adx_result.get("adx", 0) if adx_result else 0
    sig.adx = round(adx_val, 1)

    adx_min = cfg.get("adx_min", 10)
    if adx_val < adx_min:
        blockers.append(f"ADX {adx_val:.0f} < {adx_min} (ranging market)")

    # Detect VWAP crossover
    cross = detect_crossover(intraday_candles, vwap_series)
    sig.candle_info = cross

    if not cross.get("crossed"):
        blockers.append("no VWAP crossover on last candle")

    # SuperTrend confluence check
    st_period = cfg.get("supertrend_period", 10)
    st_mult = cfg.get("supertrend_multiplier", 3.0)
    st_result = ind.supertrend(
        highs,
        lows,
        closes,
        period=st_period,
        multiplier=st_mult,
    )
    st_dir = st_result.get("direction", "") if st_result else ""
    sig.candle_info["supertrend_dir"] = st_dir

    if cross.get("crossed") and st_result:
        vwap_dir = cross["direction"]
        if vwap_dir == "BULLISH" and st_dir != "UP":
            blockers.append(f"SuperTrend DOWN — no confluence with BULLISH VWAP cross")
        elif vwap_dir == "BEARISH" and st_dir != "DOWN":
            blockers.append(f"SuperTrend UP — no confluence with BEARISH VWAP cross")
    elif not st_result:
        blockers.append("SuperTrend computation failed (insufficient data)")

    if blockers:
        sig.blockers = blockers
        sig.reason = "; ".join(blockers)
        return sig

    direction = cross["direction"]
    sig.direction = direction

    # Compute stop loss
    if direction == "BULLISH":
        sl_level = cross["curr_candle_low"]
        sl_points = spot - sl_level
    else:
        sl_level = cross["curr_candle_high"]
        sl_points = sl_level - spot

    max_sl = cfg.get("max_sl_points", 30)
    if sl_points > max_sl:
        sig.reason = f"SL too wide ({sl_points:.0f} pts > {max_sl} cap)"
        sig.blockers = [sig.reason]
        return sig

    if sl_points <= 0:
        sig.reason = "invalid SL (zero or negative distance)"
        sig.blockers = [sig.reason]
        return sig

    sig.sl_price = round(sl_level, 2)
    sig.sl_points = round(sl_points, 2)

    # Select 1-ITM strike
    strike_info = select_itm_strike(spot, direction, chain, strike_step, cfg)
    if not strike_info:
        sig.reason = "no liquid 1-ITM strike found"
        sig.blockers = [sig.reason]
        return sig

    sig.strike = strike_info["strike"]
    sig.option_type = strike_info["option_type"]
    sig.premium = strike_info["premium"]

    # Position sizing (risk-based, capped at max_lots)
    risk_per_trade = cfg.get("risk_per_trade", 2000)
    max_lots = cfg.get("max_lots", 10)
    premium_sl_estimate = sig.premium * (sl_points / spot) * 2
    if premium_sl_estimate <= 0:
        premium_sl_estimate = sig.premium * 0.20
    lots = max(1, min(max_lots, int(risk_per_trade / (premium_sl_estimate * lot_size))))
    sig.lots = lots
    sig.qty = lots * lot_size

    # Target (1:1 R:R for first partial)
    if direction == "BULLISH":
        sig.target_price = round(spot + sl_points, 2)
    else:
        sig.target_price = round(spot - sl_points, 2)

    sig.has_signal = True
    sig.reason = f"VWAP + SuperTrend confluence {direction} — candle closed {'above' if direction == 'BULLISH' else 'below'} VWAP, SuperTrend confirms"

    # Build deploy payload
    risk_params = {
        "stop_loss_price": sig.sl_price,
        "stop_loss_points": sig.sl_points,
        "book_partial_pct": cfg.get("book_partial_pct", 50),
        "trail_giveback_pct": cfg.get("trail_giveback_pct", 30),
        "premium_floor_pct": cfg.get("premium_floor_pct", -35),
        "max_hold_minutes": cfg.get("max_hold_minutes", 45),
        "strategy_type": "flyhigh_vwap",
    }

    fyers_sym = _build_fyers_option_symbol(symbol, sig.strike, sig.option_type)

    atm = round(spot / strike_step) * strike_step
    sig.deploy_payload = {
        "name": f"FlyHigh {direction[:4]} {symbol} {sig.strike}{sig.option_type}",
        "strategy_type": "flyhigh_vwap",
        "underlying": symbol,
        "mode": "paper",
        "lot_size": lot_size,
        "legs": [{
            "type": sig.option_type,
            "action": "BUY",
            "lots": sig.lots,
            "offset": sig.strike - atm,
            "premium": sig.premium,
        }],
        "risk_params": risk_params,
        "entry_conditions": [],
        "ai_deployed": True,
    }

    return sig


def _parse_candle_ist(candle: dict) -> Optional[datetime]:
    """Parse a candle's timestamp and return it in IST."""
    ts_str = candle.get("timestamp") or candle.get("ts") or candle.get("date", "")
    try:
        if isinstance(ts_str, (int, float)):
            return datetime.fromtimestamp(ts_str, tz=IST)
        elif isinstance(ts_str, str) and ts_str:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)
    except (ValueError, TypeError):
        pass
    return None


def scan_missed_signals(
    symbol: str,
    intraday_candles: list[dict],
    cfg: dict,
) -> list[dict]:
    """Replay today's candles and find all VWAP+SuperTrend crossover signals.

    Returns a list of missed signals with their timestamps and details.
    Does NOT need chain data — just identifies signal times and directions.
    """
    if len(intraday_candles) < 3:
        return []

    entry_start = dtime.fromisoformat(cfg.get("entry_start", "09:20"))
    entry_cutoff = dtime.fromisoformat(cfg.get("entry_cutoff", "15:15"))
    adx_min = cfg.get("adx_min", 10)
    st_period = cfg.get("supertrend_period", 10)
    st_mult = cfg.get("supertrend_multiplier", 3.0)

    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    # Filter to today's candles only, but keep yesterday's for indicator warmup
    today_start_idx = 0
    for idx, c in enumerate(intraday_candles):
        dt = _parse_candle_ist(c)
        if dt and dt.strftime("%Y-%m-%d") == today_str:
            today_start_idx = idx
            break

    # Need at least 3 today candles for crossover detection
    today_count = len(intraday_candles) - today_start_idx
    if today_count < 3:
        return []

    signals = []

    # Iterate over today's candles; use ALL prior candles as indicator warmup
    for i in range(max(today_start_idx + 2, 3), len(intraday_candles)):
        candle = intraday_candles[i - 1]  # the "just closed" candle ([-2] in the window)
        candle_dt = _parse_candle_ist(candle)
        if not candle_dt or candle_dt.strftime("%Y-%m-%d") != today_str:
            continue

        candle_t = candle_dt.time()
        if candle_t < entry_start or candle_t > entry_cutoff:
            continue

        # Use all candles up to index i as the window (includes yesterday for warmup)
        window = intraday_candles[:i + 1]

        # Compute VWAP only on today's candles (VWAP resets daily)
        today_window = [c for c in window if _parse_candle_ist(c) and _parse_candle_ist(c).strftime("%Y-%m-%d") == today_str]
        if len(today_window) < 3:
            continue

        vwap_series = compute_vwap_series(today_window)
        if len(vwap_series) < 3:
            continue

        cross = detect_crossover(today_window, vwap_series)
        if not cross.get("crossed"):
            continue

        # ADX and SuperTrend use the full window (need history for accuracy)
        highs = [float(c.get("high", 0)) for c in window]
        lows = [float(c.get("low", 0)) for c in window]
        closes = [float(c.get("close", 0)) for c in window]

        adx_result = ind.adx(highs, lows, closes, period=14)
        adx_val = adx_result.get("adx", 0) if adx_result else 0

        st_result = ind.supertrend(highs, lows, closes, period=st_period, multiplier=st_mult)
        st_dir = st_result.get("direction", "") if st_result else ""

        vwap_dir = cross["direction"]
        spot_at_signal = float(candle.get("close", 0))
        sl_price = cross["curr_candle_low"] if vwap_dir == "BULLISH" else cross["curr_candle_high"]
        sl_points = abs(spot_at_signal - sl_price)
        max_sl = cfg.get("max_sl_points", 30)

        blocked_reason = None
        if adx_val < adx_min:
            blocked_reason = f"ADX {adx_val:.1f} < {adx_min}"
        elif vwap_dir == "BULLISH" and st_dir != "UP":
            blocked_reason = f"SuperTrend {st_dir} ≠ UP (no confluence)"
        elif vwap_dir == "BEARISH" and st_dir != "DOWN":
            blocked_reason = f"SuperTrend {st_dir} ≠ DOWN (no confluence)"
        elif sl_points > max_sl:
            blocked_reason = f"SL {sl_points:.1f}pts > {max_sl}pt cap"
        elif sl_points <= 0:
            blocked_reason = "SL width zero"

        signals.append({
            "time": candle_dt.strftime("%H:%M"),
            "timestamp": candle_dt.isoformat(),
            "direction": vwap_dir,
            "spot_at_signal": round(spot_at_signal, 2),
            "vwap_at_signal": round(vwap_series[-2], 2),
            "adx": round(adx_val, 1),
            "supertrend_dir": st_dir,
            "sl_price": round(sl_price, 2),
            "sl_points": round(sl_points, 2),
            "option_type": "CE" if vwap_dir == "BULLISH" else "PE",
            "blocked": blocked_reason is not None,
            "blocked_reason": blocked_reason,
        })

    return signals


def _build_fyers_option_symbol(symbol: str, strike: int, opt_type: str) -> str:
    """Build a Fyers-format option symbol (best effort)."""
    prefix_map = {
        "NIFTY": "NSE:NIFTY", "BANKNIFTY": "NSE:BANKNIFTY",
        "FINNIFTY": "NSE:FINNIFTY", "MIDCPNIFTY": "NSE:MIDCPNIFTY",
        "SENSEX": "BSE:SENSEX", "BANKEX": "BSE:BANKEX",
    }
    prefix = prefix_map.get(symbol.upper(), f"NSE:{symbol}")
    return f"{prefix}{strike}{opt_type}"

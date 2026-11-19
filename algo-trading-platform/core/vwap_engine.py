"""
Fly-High Strategy Engine — VWAP Crossover + SuperTrend on the OPTION charts.

The strategy reads the 5-minute charts of the 1-ITM CE and 1-ITM PE contracts
directly (not the NIFTY spot chart, which lags the option premium):

Entry: last CLOSED 5-min candle of the CE (or PE) chart crosses ABOVE its own
       VWAP (inter- or intra-candle), SuperTrend(10,3) on that chart is UP and
       ADX(14) >= adx_min. Both CE and PE triggering together = conflict, skip.
Gate:  entry window 09:20-15:15, max trades/day. No NIFTY filter.
SL:    signal candle low on the option chart.
Exit:  1:1 target on premium -> book 50%, runner trails the option SuperTrend
       line (never below entry). Max hold 45 min, EOD 15:15.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, time as dtime
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

CANDLE_MINUTES = 5
MIN_HISTORY_CANDLES = 20  # ADX(14) / SuperTrend(10) warmup

DEFAULT_CONFIG = {
    "enabled": True,
    "risk_per_trade": 2000,
    "adx_min": 10,
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
    direction: str = ""          # BULLISH (CE entry) / BEARISH (PE entry)
    reason: str = ""
    spot: float = 0.0
    vwap: float = 0.0            # option VWAP of the triggered side
    adx: float = 0.0             # option ADX of the triggered side
    strike: int = 0
    option_type: str = ""
    option_symbol: str = ""
    premium: float = 0.0
    lots: int = 0
    qty: int = 0
    sl_premium: float = 0.0      # signal candle low on the option chart
    sl_points: float = 0.0       # premium points at risk
    target_premium: float = 0.0  # 1:1 on premium
    signal_candle_ts: str = ""
    blockers: list = field(default_factory=list)
    deploy_payload: dict = field(default_factory=dict)
    ce: dict = field(default_factory=dict)  # CE chart panel
    pe: dict = field(default_factory=dict)  # PE chart panel

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
    if volumes.sum() <= 0:
        volumes = np.ones(len(candles))  # no volume data: equal-weight typical price

    typical = (highs + lows + closes) / 3.0
    cum_pv = np.cumsum(typical * volumes)
    cum_v = np.cumsum(volumes)
    cum_v[cum_v == 0] = 1
    return (cum_pv / cum_v).tolist()


def detect_crossover(candles: list[dict], vwap_series: list[float], offset: int = 2) -> dict:
    """Detect a VWAP crossover on ``candles[-offset]`` (vs the candle before it).

    ``offset=2`` when the list still ends with the forming candle, ``offset=1``
    when it holds closed candles only.

    Two detection modes (either triggers a signal):
    1. Inter-candle: previous candle closed on one side, this candle closed on the other.
    2. Intra-candle: this candle's body crosses VWAP (open on one side, close on the other).
       Catches early-session signals where VWAP has too little history.
    """
    if len(candles) < offset + 1 or len(vwap_series) < offset + 1:
        return {"crossed": False, "reason": "insufficient candles"}

    curr = candles[-offset]
    prev = candles[-offset - 1]
    curr_close = float(curr.get("close", 0))
    curr_open = float(curr.get("open", 0))
    prev_close = float(prev.get("close", 0))
    curr_vwap = vwap_series[-offset]
    prev_vwap = vwap_series[-offset - 1]

    result = {
        "crossed": False,
        "direction": "",
        "cross_type": "",
        "close": curr_close,
        "prev_close": prev_close,
        "vwap": curr_vwap,
        "prev_vwap": prev_vwap,
        "curr_candle_low": float(curr.get("low", 0)),
        "curr_candle_high": float(curr.get("high", 0)),
    }

    if prev_close <= prev_vwap and curr_close > curr_vwap:
        result.update(crossed=True, direction="BULLISH", cross_type="inter_candle")
    elif prev_close >= prev_vwap and curr_close < curr_vwap:
        result.update(crossed=True, direction="BEARISH", cross_type="inter_candle")

    if not result["crossed"] and curr_open > 0:
        if curr_open <= curr_vwap and curr_close > curr_vwap:
            result.update(crossed=True, direction="BULLISH", cross_type="intra_candle")
        elif curr_open >= curr_vwap and curr_close < curr_vwap:
            result.update(crossed=True, direction="BEARISH", cross_type="intra_candle")

    return result


def select_itm_strike(
    spot: float, direction: str, chain: dict, strike_step: int, cfg: dict,
) -> Optional[dict]:
    """Select the 1-ITM contract (higher delta than ATM), falling back to
    nearby strikes when it fails the liquidity filters.

    BULLISH -> CE one strike below ATM; BEARISH -> PE one strike above ATM.
    """
    rows = normalize_chain_rows(chain)
    if not rows:
        return None

    atm = round(spot / strike_step) * strike_step
    option_type = "CE" if direction == "BULLISH" else "PE"

    if direction == "BULLISH":
        target = atm - strike_step
        candidates = [target, atm, target - strike_step, atm + strike_step]
    else:
        target = atm + strike_step
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
            "fyers_symbol": str(row.get(f"{side}_symbol", "") or ""),
            "oi": oi,
            "volume": vol,
            "spread_pct": round(spread_pct, 2),
        }

    return None


def select_contracts(spot: float, chain: dict, strike_step: int, cfg: dict) -> dict:
    """The two charts Fly-High watches: 1-ITM CE and 1-ITM PE (None if illiquid)."""
    return {
        "CE": select_itm_strike(spot, "BULLISH", chain, strike_step, cfg),
        "PE": select_itm_strike(spot, "BEARISH", chain, strike_step, cfg),
    }


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


def closed_candles(candles: list[dict], now: datetime) -> list[dict]:
    """Drop the still-forming candle (start + 5 min > now) and unparseable rows."""
    period = timedelta(minutes=CANDLE_MINUTES)
    out = []
    for c in candles:
        dt = _parse_candle_ist(c)
        if dt is not None and dt + period <= now:
            out.append(c)
    return out


def option_supertrend(candles: list[dict], cfg: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """SuperTrend on an option's closed 5-min candles (used for the runner trail)."""
    now = now or datetime.now(IST)
    closed = closed_candles(candles, now)
    if len(closed) < MIN_HISTORY_CANDLES:
        return None
    return ind.supertrend(
        [float(c.get("high", 0)) for c in closed],
        [float(c.get("low", 0)) for c in closed],
        [float(c.get("close", 0)) for c in closed],
        period=cfg.get("supertrend_period", 10),
        multiplier=cfg.get("supertrend_multiplier", 3.0),
    )


def analyze_option_chart(candles: list[dict], cfg: dict, now: Optional[datetime] = None) -> dict:
    """Evaluate the Fly-High entry rules on one option's 5-min chart.

    VWAP resets daily (today's closed candles only); ADX/SuperTrend use all
    closed candles, so yesterday's session serves as warmup. ``ready`` means
    the last closed candle is a valid entry candle.
    """
    now = now or datetime.now(IST)
    today = now.date()
    closed = closed_candles(candles, now)
    today_c = [c for c in closed if _parse_candle_ist(c).date() == today]

    panel: dict[str, Any] = {
        "ready": False, "blockers": [], "candles": len(closed),
        "close": 0.0, "vwap": 0.0, "above_vwap": False,
        "adx": 0.0, "st_dir": "", "st_value": 0.0,
        "cross": "", "cross_type": "", "signal_candle": None,
    }
    if len(today_c) < 2:
        panel["blockers"].append("waiting for 2 closed 5-min candles today")
        return panel

    vwap_series = compute_vwap_series(today_c)
    last = today_c[-1]
    last_dt = _parse_candle_ist(last)
    panel["close"] = round(float(last.get("close", 0)), 2)
    panel["vwap"] = round(vwap_series[-1], 2)
    panel["above_vwap"] = panel["close"] > panel["vwap"]
    panel["signal_candle"] = {
        "time": last_dt.strftime("%H:%M"),
        "ts": last_dt.isoformat(),
        "open": round(float(last.get("open", 0)), 2),
        "high": round(float(last.get("high", 0)), 2),
        "low": round(float(last.get("low", 0)), 2),
        "close": panel["close"],
    }

    cross = detect_crossover(today_c, vwap_series, offset=1)
    panel["cross"] = cross.get("direction", "")
    panel["cross_type"] = cross.get("cross_type", "")

    if len(closed) < MIN_HISTORY_CANDLES:
        panel["blockers"].append(f"insufficient history ({len(closed)}/{MIN_HISTORY_CANDLES} candles)")
        return panel

    highs = [float(c.get("high", 0)) for c in closed]
    lows = [float(c.get("low", 0)) for c in closed]
    closes = [float(c.get("close", 0)) for c in closed]
    adx_result = ind.adx(highs, lows, closes, period=14)
    adx_val = float(adx_result.get("adx", 0) or 0) if adx_result else 0.0
    st = ind.supertrend(
        highs, lows, closes,
        period=cfg.get("supertrend_period", 10),
        multiplier=cfg.get("supertrend_multiplier", 3.0),
    )
    panel["adx"] = round(adx_val, 1)
    panel["st_dir"] = st.get("direction", "") if st else ""
    panel["st_value"] = round(float(st.get("value", 0)), 2) if st else 0.0

    adx_min = cfg.get("adx_min", 10)
    if panel["cross"] != "BULLISH":
        panel["blockers"].append("no VWAP cross-up on last candle")
    if panel["st_dir"] != "UP":
        panel["blockers"].append(f"SuperTrend {panel['st_dir'] or 'n/a'}")
    if adx_val < adx_min:
        panel["blockers"].append(f"ADX {adx_val:.0f} < {adx_min}")

    panel["ready"] = not panel["blockers"]
    return panel


def _time_blockers(cfg: dict, now_t: dtime, trades_today: int) -> list[str]:
    blockers = []
    entry_start = dtime.fromisoformat(cfg.get("entry_start", "09:20"))
    entry_cutoff = dtime.fromisoformat(cfg.get("entry_cutoff", "15:15"))
    if now_t < entry_start:
        blockers.append(f"before entry window ({cfg.get('entry_start', '09:20')})")
    if now_t > entry_cutoff:
        blockers.append(f"past entry cutoff ({cfg.get('entry_cutoff', '15:15')})")
    max_trades = cfg.get("max_trades_per_day", 2)
    if trades_today >= max_trades:
        blockers.append(f"max trades reached ({trades_today}/{max_trades})")
    return blockers


def generate_signal(
    symbol: str,
    spot: float,
    contracts: dict,
    option_candles: dict,
    strike_step: int,
    lot_size: int,
    cfg: dict,
    now: Optional[datetime] = None,
    trades_today: int = 0,
) -> FlyHighSignal:
    """Generate a Fly-High signal from the 1-ITM CE / PE option charts.

    ``contracts``: {"CE": select_itm_strike(...) | None, "PE": ...}
    ``option_candles``: {"CE": [5-min candles], "PE": [...]}
    """
    now = now or datetime.now(IST)
    sig = FlyHighSignal(spot=spot)

    panels: dict[str, dict] = {}
    for side in ("CE", "PE"):
        info = contracts.get(side)
        if not info:
            panels[side] = {"side": side, "ready": False, "blockers": ["no liquid 1-ITM contract"]}
            continue
        panel = analyze_option_chart(option_candles.get(side) or [], cfg, now)
        if not info.get("fyers_symbol"):
            panel["ready"] = False
            panel["blockers"].append("contract symbol unavailable (no chart)")
        panel.update(side=side, strike=info["strike"], fyers_symbol=info.get("fyers_symbol", ""),
                     ltp=info["premium"])
        panels[side] = panel
    sig.ce, sig.pe = panels["CE"], panels["PE"]

    blockers = _time_blockers(cfg, now.time(), trades_today)
    ready = [s for s in ("CE", "PE") if panels[s].get("ready")]
    if len(ready) == 2:
        blockers.append("CE and PE both triggered on the same candle (conflict) — skipped")
    elif not ready:
        blockers.append("no entry setup on CE or PE chart")

    if blockers:
        sig.blockers = blockers
        sig.reason = "; ".join(blockers)
        return sig

    side = ready[0]
    panel = panels[side]
    info = contracts[side]
    premium = float(info["premium"])
    sl = float(panel["signal_candle"]["low"])
    if premium <= sl:
        sig.blockers = [f"{side} LTP {premium:.1f} <= signal-candle low {sl:.1f} — setup failed"]
        sig.reason = sig.blockers[0]
        return sig

    risk_pts = premium - sl
    sig.direction = "BULLISH" if side == "CE" else "BEARISH"
    sig.strike = int(info["strike"])
    sig.option_type = side
    sig.option_symbol = info["fyers_symbol"]
    sig.premium = round(premium, 2)
    sig.vwap = panel["vwap"]
    sig.adx = panel["adx"]
    sig.sl_premium = round(sl, 2)
    sig.sl_points = round(risk_pts, 2)
    sig.target_premium = round(premium + risk_pts, 2)
    sig.signal_candle_ts = panel["signal_candle"]["ts"]

    risk_per_trade = cfg.get("risk_per_trade", 2000)
    max_lots = cfg.get("max_lots", 10)
    sig.lots = max(1, min(max_lots, int(risk_per_trade / (risk_pts * lot_size))))
    sig.qty = sig.lots * lot_size

    sig.has_signal = True
    sig.reason = (
        f"{side} {sig.strike} crossed above its VWAP on the {panel['signal_candle']['time']} candle, "
        f"SuperTrend UP, ADX {panel['adx']:.0f}"
    )

    risk_params = {
        "premium_sl": sig.sl_premium,
        "target_premium": sig.target_premium,
        "option_symbol": sig.option_symbol,
        "signal_candle_ts": sig.signal_candle_ts,
        "book_partial_pct": cfg.get("book_partial_pct", 50),
        "trail_giveback_pct": cfg.get("trail_giveback_pct", 30),
        "premium_floor_pct": cfg.get("premium_floor_pct", -35),
        "max_hold_minutes": cfg.get("max_hold_minutes", 45),
        "strategy_type": "flyhigh_vwap",
    }

    atm = round(spot / strike_step) * strike_step
    sig.deploy_payload = {
        "name": f"FlyHigh {symbol} {sig.strike}{side}",
        "strategy_type": "flyhigh_vwap",
        "underlying": symbol,
        "mode": "paper",
        "lot_size": lot_size,
        "spot_price": spot,
        "legs": [{
            "type": side,
            "action": "BUY",
            "lots": sig.lots,
            "strike": sig.strike,
            "offset": sig.strike - atm,
            "premium": sig.premium,
        }],
        "risk_params": risk_params,
        "entry_conditions": [],
        "ai_deployed": True,
    }
    return sig


def scan_signal_log(
    contracts: dict,
    option_candles: dict,
    cfg: dict,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Replay today's closed candles of the current 1-ITM CE/PE charts and list
    every VWAP cross-up, with the gate result and a simple outcome (SL = signal
    candle low, target = 1:1, whichever the later candles touch first).

    Read-only log: it uses the contracts that are 1-ITM *now*, so early
    signals may refer to a strike that was not 1-ITM at the time.
    """
    now = now or datetime.now(IST)
    today = now.date()
    entry_start = dtime.fromisoformat(cfg.get("entry_start", "09:20"))
    entry_cutoff = dtime.fromisoformat(cfg.get("entry_cutoff", "15:15"))
    period = timedelta(minutes=CANDLE_MINUTES)

    rows: list[dict] = []
    for side in ("CE", "PE"):
        info = contracts.get(side)
        if not info:
            continue
        closed = closed_candles(option_candles.get(side) or [], now)
        for i, c in enumerate(closed):
            c_dt = _parse_candle_ist(c)
            if c_dt.date() != today:
                continue
            end = c_dt + period
            if not (entry_start <= end.time() <= entry_cutoff):
                continue
            panel = analyze_option_chart(closed[: i + 1], cfg, end)
            if panel.get("cross") != "BULLISH":
                continue

            entry = float(c.get("close", 0))
            sl = float(c.get("low", 0))
            risk_pts = entry - sl
            target = entry + risk_pts
            outcome, exit_time, exit_px = "open", "", 0.0
            if risk_pts > 0:
                for fc in closed[i + 1:]:
                    if float(fc.get("low", 0)) <= sl:
                        outcome, exit_px = "SL Hit", sl
                    elif float(fc.get("high", 0)) >= target:
                        outcome, exit_px = "Target Hit", target
                    else:
                        continue
                    exit_time = _parse_candle_ist(fc).strftime("%H:%M")
                    break
                if outcome == "open" and len(closed) > i + 1:
                    exit_px = float(closed[-1].get("close", 0))

            rows.append({
                "time": end.strftime("%H:%M"),
                "candle_time": c_dt.strftime("%H:%M"),
                "signal_candle_ts": c_dt.isoformat(),
                "option_type": side,
                "direction": "BULLISH" if side == "CE" else "BEARISH",
                "strike": int(info["strike"]),
                "entry_premium": round(entry, 2),
                "sl_premium": round(sl, 2),
                "target_premium": round(target, 2),
                "vwap": panel.get("vwap", 0.0),
                "adx": panel.get("adx", 0.0),
                "supertrend_dir": panel.get("st_dir", ""),
                "blocked": not panel.get("ready") or risk_pts <= 0,
                "blocked_reason": "; ".join(panel.get("blockers") or [])
                    or ("SL width zero" if risk_pts <= 0 else ""),
                "outcome": outcome,
                "exit_time": exit_time,
                "exit_premium": round(exit_px, 2),
                "pnl_points": round(exit_px - entry, 2) if exit_px else 0.0,
            })

    # Both sides valid on the same candle = conflict, the live engine skips it
    valid_by_ts: dict[str, int] = {}
    for r in rows:
        if not r["blocked"]:
            valid_by_ts[r["signal_candle_ts"]] = valid_by_ts.get(r["signal_candle_ts"], 0) + 1
    for r in rows:
        if not r["blocked"] and valid_by_ts.get(r["signal_candle_ts"], 0) > 1:
            r["blocked"] = True
            r["blocked_reason"] = "CE and PE both triggered (conflict)"

    rows.sort(key=lambda r: r["signal_candle_ts"])
    return rows

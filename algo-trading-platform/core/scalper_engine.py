"""
Expiry-Day Scalper Engine — support/resistance-driven OTM option buying.

The thesis: on expiry day, gamma is huge. A cheap OTM option (1-10 Rs) can
explode 5-20x if the spot makes a clean directional move *through* a level.
But theta is brutal and chop kills option buyers — so the engine only fires on
high-conviction, confirmed breakouts in a trending regime, with tight risk.

Pipeline (per tick):
  1. Build S/R levels: CPR (pivot/TC/BC), prev-day high/low, day VWAP,
     opening-range (first 15m) high/low, round numbers, OI walls.
  2. Detect the nearest level to spot and whether it's being broken.
  3. Regime gate (STRICT): ADX >= threshold, breakout candle CLOSES beyond the
     level (not just a wick), and volume confirms. No chop = no trade.
  4. Strike selection by time-of-day: OTM early (max gamma), ATM late.
     Liquidity filter: min OI/volume, max bid-ask spread %.
  5. Size by fixed rupee-risk (default Rs 2000 / trade).
  6. Emit a deploy-ready payload with the exit plan:
       - book 50% at +50%, move stop to breakeven, trail the rest
       - structural stop: spot reclaims the broken level
       - premium floor backstop: -35%

This module is intentionally stateless — the API layer fetches candles +
option chain and calls these functions. Stability/state lives in the caller.

Author: SmartAlgo Scalper Engine
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, time as dtime
from typing import Any, Optional

from core import indicators as ind
from core.constants import IST

logger = logging.getLogger(__name__)

# ── Configuration (defaults; overridable via API config) ─────────────────────

DEFAULT_CONFIG = {
    "enabled": True,
    "expiry_only": True,            # only arm on expiry days
    "risk_per_trade": 2000.0,       # Rs max loss per scalp (drives sizing)
    "regime": "strict",             # "strict" | "balanced"
    "adx_min_strict": 20.0,
    "adx_min_balanced": 15.0,
    "entry_start": "09:20",
    "entry_cutoff": "15:10",        # no new entries after this — leaves a buffer
                                     # before the 15:15 EOD square-off while still
                                     # catching the ~15:00 late-session spike window
    "tighten_after": "14:00",       # tighten trail after this
    "book_partial_pct": 50.0,       # book half at +50%
    "trail_giveback_pct": 30.0,     # exit runner if it gives back 30% from peak
    "premium_floor_pct": 35.0,      # hard premium stop (backstop to structural)
    "max_trades_per_day": 8,
    "min_oi": 50000,                # liquidity floor on the strike
    "min_volume": 10000,
    "max_spread_pct": 8.0,          # max bid-ask spread as % of premium
    "max_lots": 20,                 # cap size on ultra-cheap options
    "level_proximity_pct": 0.12,    # spot within this % of a level = "at the wall"
    "breakout_pct": 0.04,           # close past level by this % = breaking
    "auto_deploy": False,           # hands-free: auto-deploy a scalp when a confirmed signal forms
    "auto_symbols": ["NIFTY"],      # which underlyings the auto loop watches
}

# Strike offset (in strike-steps) by time-of-day phase
PHASE_OFFSETS = {
    "morning": 2,   # 2 strikes OTM — cheap, max gamma
    "midday": 1,    # 1 strike OTM
    "afternoon": 0, # ATM — survives theta, better delta
}


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SRLevel:
    name: str           # e.g. "CPR_TC", "PDH", "VWAP", "ORB_HIGH", "OI_RES"
    price: float
    kind: str           # "support" | "resistance" | "pivot"
    source: str         # "cpr" | "prev_day" | "vwap" | "orb" | "round" | "oi"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScalpSignal:
    has_signal: bool
    direction: str = "NEUTRAL"          # "BULLISH" | "BEARISH" | "NEUTRAL"
    action: Optional[str] = None        # "BUY_CE" | "BUY_PE"
    reason: str = ""
    level_name: str = ""
    level_price: float = 0.0
    spot: float = 0.0
    adx: float = 0.0
    strike: int = 0
    option_type: str = ""               # "CE" | "PE"
    premium: float = 0.0
    lots: int = 0
    qty: int = 0
    stop_level: float = 0.0             # structural stop (spot reclaims level)
    blockers: list = field(default_factory=list)
    deploy_payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_hhmm(s: str) -> dtime:
    h, m = s.strip().split(":")
    return dtime(int(h), int(m))


def _phase(now_t: dtime) -> str:
    if now_t < dtime(11, 0):
        return "morning"
    if now_t < dtime(13, 0):
        return "midday"
    return "afternoon"


# Weekly expiry weekday per index (Mon=0 .. Sun=6).
# Post-rationalisation (2024-25): NSE NIFTY weekly = Tuesday, BSE SENSEX = Thursday.
WEEKLY_EXPIRY_WEEKDAY = {
    "NIFTY": 1,     # Tuesday
    "SENSEX": 3,    # Thursday
}


def is_expiry_day(chain: dict, symbol: str | None = None) -> bool:
    """True if today (IST) is the weekly expiry for this symbol.

    Primary: compare the option chain's nearest expiry date to today (most
    accurate — it already accounts for holiday shifts). Fallback: the per-symbol
    weekly expiry weekday (NIFTY=Tue, SENSEX=Thu) when no chain date is present.
    """
    today = datetime.now(IST).date()
    try:
        exp = (chain or {}).get("expiry") or (chain or {}).get("expiry_date") or ""
        if exp:
            for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(str(exp)[:11].strip(), fmt).date() == today
                except ValueError:
                    continue
        ts = (chain or {}).get("expiry_timestamp")
        if ts:
            return datetime.fromtimestamp(int(ts), tz=IST).date() == today
    except Exception as e:
        logger.debug(f"is_expiry_day chain parse failed: {e}")

    # Fallback: weekday rule for the symbol
    sym = (symbol or (chain or {}).get("symbol") or "").upper()
    wd = WEEKLY_EXPIRY_WEEKDAY.get(sym)
    if wd is not None:
        return datetime.now(IST).weekday() == wd
    return False


def normalize_chain_rows(chain: dict) -> dict[int, dict]:
    """Return {strike: {call_ltp, put_ltp, call_oi, put_oi, call_volume,
    put_volume, call_bid, put_bid, call_ask, put_ask}}.

    Handles BOTH chain formats found in the cache:
      - flat: one row per option with ``option_type`` + ``ltp``/``oi``/...
        (as written by FyersLiveFeed.get_option_chain)
      - paired: one row per strike with ``call_ltp``/``put_ltp``/... keys
        (as re-paired by the dashboard executor)
    """
    # Rows may live under "chain" (live) or "contracts" (mock)
    rows = []
    if isinstance(chain, dict):
        rows = chain.get("chain") or chain.get("contracts") or []
    out: dict[int, dict] = {}

    def _fold_nested(slot: dict, side: str, obj: dict) -> None:
        slot[f"{side}_ltp"] = float(obj.get("ltp", 0) or 0)
        slot[f"{side}_oi"] = float(obj.get("oi", 0) or 0)
        slot[f"{side}_volume"] = float(obj.get("volume", 0) or 0)
        slot[f"{side}_bid"] = float(obj.get("bid", 0) or 0)
        slot[f"{side}_ask"] = float(obj.get("ask", 0) or 0)

    for r in rows:
        if not isinstance(r, dict):
            continue
        strike = int(float(r.get("strike", 0) or 0))
        if not strike:
            continue
        slot = out.setdefault(strike, {"strike": strike})
        if isinstance(r.get("call"), dict) or isinstance(r.get("put"), dict):
            # nested format (mock): {strike, call:{...}, put:{...}}
            if isinstance(r.get("call"), dict):
                _fold_nested(slot, "call", r["call"])
            if isinstance(r.get("put"), dict):
                _fold_nested(slot, "put", r["put"])
        elif "option_type" in r:
            # flat row → fold into the right side
            side = "call" if str(r.get("option_type", "")).upper() in ("CE", "CALL") else "put"
            _fold_nested(slot, side, r)
        else:
            # already paired — copy the known keys through
            for k in ("call_ltp", "put_ltp", "call_oi", "put_oi",
                      "call_volume", "put_volume", "call_bid", "put_bid",
                      "call_ask", "put_ask"):
                if k in r:
                    slot[k] = float(r.get(k, 0) or 0)
    return out


def round_number_levels(spot: float, step: int, n: int = 2) -> list[float]:
    """Psychological round numbers near spot (multiples of the strike step's
    'big round' — e.g. for NIFTY use 100s, BANKNIFTY 500s)."""
    big = 100 if step <= 50 else 500
    base = round(spot / big) * big
    return [base + i * big for i in range(-n, n + 1)]


def compute_sr_levels(
    daily_candles: list[dict],
    intraday_candles: list[dict],
    chain: dict,
    spot: float,
    strike_step: int,
) -> list[SRLevel]:
    """Build the full S/R level set from daily + intraday candles + OI chain."""
    levels: list[SRLevel] = []

    # ── CPR + classic pivots from previous day ──
    if len(daily_candles) >= 2:
        prev = daily_candles[-2]
        ph = float(prev.get("high", prev.get("High", 0)))
        pl = float(prev.get("low", prev.get("Low", 0)))
        pc = float(prev.get("close", prev.get("Close", 0)))
        if ph and pl and pc:
            pivot = (ph + pl + pc) / 3.0
            bc = (ph + pl) / 2.0
            tc = pivot + (pivot - bc)
            levels.append(SRLevel("CPR_TC", round(tc, 2), "resistance", "cpr"))
            levels.append(SRLevel("CPR_Pivot", round(pivot, 2), "pivot", "cpr"))
            levels.append(SRLevel("CPR_BC", round(bc, 2), "support", "cpr"))
            pp = ind.pivot_points(ph, pl, pc)
            levels.append(SRLevel("R1", round(pp["R1"], 2), "resistance", "cpr"))
            levels.append(SRLevel("S1", round(pp["S1"], 2), "support", "cpr"))
            # Prev-day high/low
            levels.append(SRLevel("PDH", round(ph, 2), "resistance", "prev_day"))
            levels.append(SRLevel("PDL", round(pl, 2), "support", "prev_day"))

    # ── Day VWAP ──
    if intraday_candles:
        arr = ind.split_ohlcv(intraday_candles)
        vw = ind.vwap(arr["High"], arr["Low"], arr["Close"], arr["Volume"])
        if vw:
            kind = "support" if spot >= vw else "resistance"
            levels.append(SRLevel("VWAP", round(float(vw), 2), kind, "vwap"))

        # ── Opening-range (first 15 min = 3 x 5m candles) ──
        orb = intraday_candles[:3]
        if len(orb) >= 1:
            orb_h = max(float(c.get("high", c.get("High", 0))) for c in orb)
            orb_l = min(float(c.get("low", c.get("Low", 1e9))) for c in orb)
            if orb_h:
                levels.append(SRLevel("ORB_High", round(orb_h, 2), "resistance", "orb"))
            if orb_l and orb_l < 1e9:
                levels.append(SRLevel("ORB_Low", round(orb_l, 2), "support", "orb"))

    # ── Round numbers ──
    for rn in round_number_levels(spot, strike_step):
        kind = "support" if rn <= spot else "resistance"
        levels.append(SRLevel(f"Round_{int(rn)}", float(rn), kind, "round"))

    # ── OI walls from chain ──
    try:
        paired = normalize_chain_rows(chain)
        best_call = None  # highest call OI above spot (resistance)
        best_put = None   # highest put OI below spot (support)
        for strike, r in paired.items():
            call_oi = float(r.get("call_oi", 0) or 0)
            put_oi = float(r.get("put_oi", 0) or 0)
            if strike >= spot and (best_call is None or call_oi > best_call[1]):
                best_call = (strike, call_oi)
            if strike <= spot and (best_put is None or put_oi > best_put[1]):
                best_put = (strike, put_oi)
        if best_call:
            levels.append(SRLevel("OI_Resistance", float(best_call[0]), "resistance", "oi"))
        if best_put:
            levels.append(SRLevel("OI_Support", float(best_put[0]), "support", "oi"))
    except Exception as e:
        logger.debug(f"OI wall computation failed: {e}")

    return levels


def regime_gate(intraday_candles: list[dict], cfg: dict) -> dict:
    """STRICT trend filter. Returns {ok, adx, reason}.

    Requires ADX above threshold (trend present). The breakout-close + volume
    check is applied per-level in generate_signal.
    """
    if len(intraday_candles) < 15:
        return {"ok": False, "adx": 0.0, "reason": "insufficient intraday data"}

    arr = ind.split_ohlcv(intraday_candles)
    adx_res = ind.adx(arr["High"], arr["Low"], arr["Close"], period=14)
    adx_val = float(adx_res.get("adx", 0)) if adx_res else 0.0

    threshold = cfg["adx_min_strict"] if cfg.get("regime") == "strict" else cfg["adx_min_balanced"]
    if adx_val < threshold:
        return {"ok": False, "adx": adx_val, "reason": f"no trend (ADX {adx_val:.0f} < {threshold:.0f}) - chop kills option buyers"}

    return {"ok": True, "adx": adx_val, "reason": f"trending (ADX {adx_val:.0f})"}


def _nearest_breaking_level(
    levels: list[SRLevel], spot: float, last_candle: dict, cfg: dict
) -> Optional[tuple[SRLevel, str]]:
    """Find a level the spot is breaking THROUGH with a candle close.

    Returns (level, direction) where direction is "BULLISH" (broke resistance up)
    or "BEARISH" (broke support down), or None.
    """
    close = float(last_candle.get("close", last_candle.get("Close", spot)))
    open_ = float(last_candle.get("open", last_candle.get("Open", close)))
    break_amt = cfg["breakout_pct"] / 100.0 * spot

    best = None
    best_dist = 1e18
    for lvl in levels:
        dist = abs(close - lvl.price)
        # Bullish: candle opened below level, closed above it by break_amt
        if open_ <= lvl.price and close >= lvl.price + break_amt and lvl.kind in ("resistance", "pivot"):
            if dist < best_dist:
                best, best_dist = (lvl, "BULLISH"), dist
        # Bearish: candle opened above level, closed below it by break_amt
        elif open_ >= lvl.price and close <= lvl.price - break_amt and lvl.kind in ("support", "pivot"):
            if dist < best_dist:
                best, best_dist = (lvl, "BEARISH"), dist
    return best


def _volume_confirms(intraday_candles: list[dict]) -> bool:
    """Last candle volume above the recent average = real participation."""
    if len(intraday_candles) < 6:
        return True  # not enough data, don't block
    vols = [float(c.get("volume", c.get("Volume", 0)) or 0) for c in intraday_candles[-6:]]
    last = vols[-1]
    avg = sum(vols[:-1]) / max(1, len(vols) - 1)
    return last >= avg * 0.9  # allow slight slack


def select_strike(
    spot: float, direction: str, now_t: dtime, chain: dict, strike_step: int, cfg: dict
) -> Optional[dict]:
    """Pick the strike + premium for the scalp, with a liquidity filter.

    Returns {strike, option_type, premium, oi, volume, spread_pct} or None if
    no liquid strike is available.
    """
    opt_type = "CE" if direction == "BULLISH" else "PE"
    phase = _phase(now_t)
    offset_strikes = PHASE_OFFSETS[phase]
    # OTM direction: calls -> higher strike, puts -> lower strike
    atm = round(spot / strike_step) * strike_step
    target = atm + offset_strikes * strike_step if opt_type == "CE" else atm - offset_strikes * strike_step

    by_strike = normalize_chain_rows(chain)
    side = "call" if opt_type == "CE" else "put"

    # Walk outward from target to find a liquid strike
    candidates = [target, target + strike_step, target - strike_step,
                  target + 2 * strike_step, target - 2 * strike_step]
    for strike in candidates:
        row = by_strike.get(int(strike))
        if not row:
            continue
        premium = float(row.get(f"{side}_ltp", 0) or 0)
        oi = float(row.get(f"{side}_oi", 0) or 0)
        vol = float(row.get(f"{side}_volume", 0) or 0)
        bid = float(row.get(f"{side}_bid", 0) or 0)
        ask = float(row.get(f"{side}_ask", 0) or 0)
        spread_pct = ((ask - bid) / premium * 100) if (premium > 0 and ask > bid) else 0.0

        if premium <= 0:
            continue
        if oi < cfg["min_oi"] or vol < cfg["min_volume"]:
            continue
        if spread_pct > cfg["max_spread_pct"]:
            continue
        return {
            "strike": int(strike),
            "option_type": opt_type,
            "premium": round(premium, 2),
            "oi": int(oi),
            "volume": int(vol),
            "spread_pct": round(spread_pct, 2),
        }
    return None


def _size_position(premium: float, lot_size: int, cfg: dict) -> tuple[int, int]:
    """Size by fixed rupee-risk. Worst-case loss ~= premium * stopFrac * qty.
    Returns (lots, qty)."""
    stop_frac = cfg["premium_floor_pct"] / 100.0
    risk = cfg["risk_per_trade"]
    per_lot_risk = premium * stop_frac * lot_size
    if per_lot_risk <= 0:
        return 1, lot_size
    lots = int(risk // per_lot_risk)
    lots = max(1, min(lots, cfg["max_lots"]))
    return lots, lots * lot_size


def generate_signal(
    symbol: str,
    spot: float,
    daily_candles: list[dict],
    intraday_candles: list[dict],
    chain: dict,
    strike_step: int,
    lot_size: int,
    cfg: dict,
    now: Optional[datetime] = None,
    force: bool = False,
) -> ScalpSignal:
    """Full pipeline → a (possibly empty) scalp signal with deploy payload.

    When ``force`` is True (manual override), the arming gates (expiry, time
    window, regime) and the breakout requirement are bypassed: direction is
    derived from spot vs VWAP and a protective stop is taken from the nearest
    level on the losing side. Liquidity filtering still applies — we won't buy
    an illiquid strike even on a forced deploy.
    """
    now = now or datetime.now(IST)
    now_t = now.time()
    blockers: list[str] = []

    levels = compute_sr_levels(daily_candles, intraday_candles, chain, spot, strike_step)

    # ── Arming gates ──
    if not cfg.get("enabled", True):
        blockers.append("scalper disabled")
    if cfg.get("expiry_only", True) and not is_expiry_day(chain, symbol):
        blockers.append("not an expiry day (arm only on expiry, or disable expiry-only)")
    if now_t < _parse_hhmm(cfg["entry_start"]):
        blockers.append(f"before entry window ({cfg['entry_start']})")
    if now_t > _parse_hhmm(cfg["entry_cutoff"]):
        blockers.append(f"past entry cutoff ({cfg['entry_cutoff']}) - theta crush zone")

    # ── Regime gate ──
    regime = regime_gate(intraday_candles, cfg)
    if not regime["ok"]:
        blockers.append(regime["reason"])

    # ── Breakout detection ──
    breaking = None
    if intraday_candles:
        breaking = _nearest_breaking_level(levels, spot, intraday_candles[-1], cfg)
    if not breaking:
        blockers.append("no confirmed level break (need candle CLOSE beyond a level)")
    elif not _volume_confirms(intraday_candles):
        blockers.append("breakout not confirmed by volume")

    forced = False
    if force and not breaking:
        # Manual override: derive a direction from spot vs VWAP (or CPR pivot),
        # and take a protective stop from the nearest level on the losing side.
        forced = True
        ref = next((l for l in levels if l.source == "vwap"), None) \
            or next((l for l in levels if l.name == "CPR_Pivot"), None)
        direction = "BULLISH" if (ref is None or spot >= ref.price) else "BEARISH"
        if direction == "BULLISH":
            below = [l for l in levels if l.price < spot]
            synth = max(below, key=lambda l: l.price) if below else \
                SRLevel("Spot-1%", round(spot * 0.99, 2), "support", "round")
        else:
            above = [l for l in levels if l.price > spot]
            synth = min(above, key=lambda l: l.price) if above else \
                SRLevel("Spot+1%", round(spot * 1.01, 2), "resistance", "round")
        breaking = (synth, direction)

    # If anything blocks (and not forced), return a no-signal with the level map
    if (blockers and not force) or not breaking:
        return ScalpSignal(
            has_signal=False,
            spot=spot,
            adx=regime.get("adx", 0.0),
            reason="; ".join(blockers) if blockers else "waiting for setup",
            blockers=blockers,
            deploy_payload={"levels": [l.to_dict() for l in levels]},
        )

    level, direction = breaking

    # ── Strike + liquidity ──
    pick = select_strike(spot, direction, now_t, chain, strike_step, cfg)
    if not pick:
        return ScalpSignal(
            has_signal=False, spot=spot, adx=regime["adx"],
            reason="no liquid strike (OI/volume/spread filter) - would slip in live",
            blockers=["liquidity filter: no tradeable strike"],
            level_name=level.name, level_price=level.price, direction=direction,
            deploy_payload={"levels": [l.to_dict() for l in levels]},
        )

    lots, qty = _size_position(pick["premium"], lot_size, cfg)
    action = "BUY_CE" if direction == "BULLISH" else "BUY_PE"
    stop_level = level.price  # structural stop: spot reclaims the broken level

    deploy_payload = {
        "name": f"Scalp {symbol} {pick['strike']}{pick['option_type']} ({level.name})",
        "underlying": symbol,
        "strategy_class": "scalper",
        "ai_deployed": True,            # executor enters immediately within window
        "ai_signal": action,
        "ai_confidence": (50 if forced else min(95, 55 + regime["adx"])),
        "ai_reasoning": [
            (f"FORCED {direction} entry (manual override, stop @ {level.name} {level.price})"
             if forced else f"{direction} break of {level.name} @ {level.price}"),
            f"ADX {regime['adx']:.0f}" + ("" if forced else " (trend confirmed)"),
            f"strike {pick['strike']}{pick['option_type']} @ Rs{pick['premium']} (OI {pick['oi']:,}, spread {pick['spread_pct']}%)",
        ],
        "spot_price": spot,
        "lot_size": lot_size,
        "legs": [{
            "type": pick["option_type"],
            "action": "BUY",
            "lots": lots,
            "offset": pick["strike"] - round(spot / strike_step) * strike_step,
            "premium": pick["premium"],
        }],
        "risk_params": {
            "maxLossPerTrade": cfg["risk_per_trade"],
            "scalp": True,
            "book_partial_pct": cfg["book_partial_pct"],
            "trail_giveback_pct": cfg["trail_giveback_pct"],
            "premium_floor_pct": cfg["premium_floor_pct"],
            "stop_level": stop_level,
            "stop_dir": direction,        # BULLISH → exit if spot < stop_level
            "tighten_after": cfg["tighten_after"],
        },
        "schedule_window": [cfg["entry_start"], cfg["entry_cutoff"]],
        "levels": [l.to_dict() for l in levels],
    }

    return ScalpSignal(
        has_signal=True,
        direction=direction,
        action=action,
        reason=(f"FORCED {direction} entry (manual override) - stop @ {level.name} {level.price}"
                if forced else f"{direction} break of {level.name} @ {level.price} with ADX {regime['adx']:.0f}"),
        level_name=level.name,
        level_price=level.price,
        spot=spot,
        adx=regime["adx"],
        strike=pick["strike"],
        option_type=pick["option_type"],
        premium=pick["premium"],
        lots=lots,
        qty=qty,
        stop_level=stop_level,
        blockers=[],
        deploy_payload=deploy_payload,
    )

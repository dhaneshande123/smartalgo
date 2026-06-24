"""
OI Signal Engine -4-Factor signal generation from option chain Open Interest data.

Generates BUY CE / BUY PE signals with confidence scoring based on:
  1. Buildup Analysis (35%) -OI change + price change = Long/Short/Unwinding/Covering
  2. PCR Extreme (25%)     -Put-Call Ratio at extremes signals mean reversion
  3. Max Pain Gravity (20%) -Spot vs max-pain distance signals gravitational pull
  4. S/R Breach (20%)      -Spot crossing support/resistance from OI walls

Each signal includes:
  - direction: "BULLISH" | "BEARISH" | "NEUTRAL"
  - action: "BUY_CE" | "BUY_PE" | None
  - confidence: 0-100%
  - recommended strike + reasoning
  - stability_count: how many consecutive refreshes signal persisted (noise filter)

Stability filter: Signal must persist >= STABILITY_THRESHOLD consecutive ticks
before being shown as "CONFIRMED". Below that, it's "FORMING".

Author: SmartAlgo OI Signal Engine
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

STABILITY_THRESHOLD = 2          # Signal must persist N refreshes to be CONFIRMED
CONFIDENCE_DISPLAY_MIN = 40      # Below this, don't even show the signal
CONFIDENCE_DEPLOY_MIN = 60       # Below this, disable deploy button

# Factor weights (must sum to 1.0)
W_BUILDUP = 0.35
W_PCR     = 0.25
W_MAXPAIN = 0.20
W_SR      = 0.20

# PCR thresholds
PCR_EXTREME_BULLISH = 1.3   # PCR above this = oversold = bullish
PCR_EXTREME_BEARISH = 0.7   # PCR below this = overbought = bearish
PCR_STRONG_BULLISH  = 1.5
PCR_STRONG_BEARISH  = 0.5

# Max pain distance thresholds (as % of spot)
MAXPAIN_STRONG_PULL_PCT = 1.5   # >1.5% away = strong gravitational pull
MAXPAIN_MILD_PULL_PCT   = 0.5   # >0.5% away = mild pull

# S/R breach thresholds (as % of spot)
SR_BREACH_PCT   = 0.3    # Spot within 0.3% of S/R = at the wall
SR_BREAK_PCT    = 0.1    # Spot past S/R by 0.1% = breaking through

# Strike selection: how many strikes OTM for recommendation
STRIKE_ATM_OFFSET_AGGRESSIVE = 0   # ATM
STRIKE_ATM_OFFSET_MODERATE   = 1   # 1 strike OTM
STRIKE_ATM_OFFSET_SAFE       = 2   # 2 strikes OTM


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class FactorResult:
    """Result from a single signal factor."""
    direction: str          # "BULLISH" | "BEARISH" | "NEUTRAL"
    score: float            # 0.0 to 1.0 (factor-local confidence)
    reasoning: str          # Human-readable explanation
    raw_value: float = 0.0  # The underlying metric (PCR value, distance %, etc.)


@dataclass
class OISignal:
    """Complete signal with all factors combined."""
    direction: str               # "BULLISH" | "BEARISH" | "NEUTRAL"
    action: Optional[str]        # "BUY_CE" | "BUY_PE" | None
    confidence: float            # 0-100%
    status: str                  # "CONFIRMED" | "FORMING" | "WEAK"
    stability_count: int         # consecutive ticks with same direction
    recommended_strike: Optional[int]
    recommended_ltp: Optional[float]
    recommended_type: Optional[str]  # "CE" | "PE"
    factors: dict                # factor_name -> FactorResult
    reasoning: list[str]         # combined reasoning bullets
    timestamp: float
    symbol: str
    spot: float
    # For deploy integration
    deploy_payload: Optional[dict] = None


@dataclass
class StabilityTracker:
    """Tracks signal persistence across refreshes."""
    last_direction: str = "NEUTRAL"
    consecutive_count: int = 0
    last_confidence: float = 0.0
    last_timestamp: float = 0.0


# In-memory stability state per symbol
_stability: dict[str, StabilityTracker] = {}


# ── Factor 1: Buildup Analysis (35%) ────────────────────────────────────────

def _analyze_buildup(chain: list[dict], spot: float) -> FactorResult:
    """
    Classify overall market buildup from aggregate OI change + price change.

    Logic:
    - Sum call/put OI changes across all strikes
    - Weight ATM strikes 3x (they carry the most gamma risk for MMs)
    - Classify: long buildup, short buildup, unwinding, covering
    - Net interpretation gives direction
    """
    if not chain:
        return FactorResult("NEUTRAL", 0.0, "No chain data")

    weighted_call_oi_chg = 0.0
    weighted_put_oi_chg = 0.0
    total_call_price_chg = 0.0
    total_put_price_chg = 0.0
    total_weight = 0.0

    for row in chain:
        strike = row.get("strike", 0)
        distance = abs(strike - spot) if spot > 0 else 0
        # ATM weight 3.0, nearby 2.0, far OTM 1.0
        if spot > 0:
            pct_dist = distance / spot * 100
            weight = 3.0 if pct_dist < 0.3 else (2.0 if pct_dist < 1.0 else 1.0)
        else:
            weight = 1.0

        call_oi_chg = float(row.get("call_oi_change", 0) or 0)
        put_oi_chg = float(row.get("put_oi_change", 0) or 0)
        call_price_chg = float(row.get("call_change_pct", 0) or 0)
        put_price_chg = float(row.get("put_change_pct", 0) or 0)

        weighted_call_oi_chg += call_oi_chg * weight
        weighted_put_oi_chg += put_oi_chg * weight
        total_call_price_chg += call_price_chg * weight
        total_put_price_chg += put_price_chg * weight
        total_weight += weight

    if total_weight == 0:
        return FactorResult("NEUTRAL", 0.0, "No weighted data")

    # Normalize
    avg_call_oi_chg = weighted_call_oi_chg / total_weight
    avg_put_oi_chg = weighted_put_oi_chg / total_weight
    avg_call_price_chg = total_call_price_chg / total_weight
    avg_put_price_chg = total_put_price_chg / total_weight

    # Classify call side
    # Call OI up + Call price down = Call writing (bearish -sellers expect price stays below)
    # Call OI up + Call price up = Long call buildup (bullish)
    # Call OI down + Call price up = Short covering on calls (mildly bullish)
    # Call OI down + Call price down = Long unwinding on calls (neutral/bearish)
    call_bullish_score = 0.0
    call_reasoning = ""
    if avg_call_oi_chg > 0 and avg_call_price_chg <= 0:
        # Call writing -BEARISH for market (sellers confident price won't go up)
        call_bullish_score = -0.7
        call_reasoning = "Call writing (OI up, price down) -sellers expect ceiling"
    elif avg_call_oi_chg > 0 and avg_call_price_chg > 0:
        # Fresh call buying -BULLISH
        call_bullish_score = 0.5
        call_reasoning = "Fresh call buying (OI up, price up) -bullish bets"
    elif avg_call_oi_chg < 0 and avg_call_price_chg > 0:
        # Short covering on calls -MILDLY BULLISH
        call_bullish_score = 0.3
        call_reasoning = "Call short covering (OI down, price up) -sellers exiting"
    elif avg_call_oi_chg < 0 and avg_call_price_chg <= 0:
        # Long unwinding -NEUTRAL/BEARISH
        call_bullish_score = -0.2
        call_reasoning = "Call long unwinding (OI down, price down)"
    else:
        call_reasoning = "Call side neutral"

    # Classify put side
    # Put OI up + Put price down = Put writing (BULLISH -sellers expect price stays above)
    # Put OI up + Put price up = Long put buildup (bearish)
    # Put OI down + Put price up = Long unwinding on puts (neutral/bullish)
    # Put OI down + Put price down = Short covering on puts (mildly bearish)
    put_bullish_score = 0.0
    put_reasoning = ""
    if avg_put_oi_chg > 0 and avg_put_price_chg <= 0:
        # Put writing -BULLISH for market (sellers confident price won't fall)
        put_bullish_score = 0.7
        put_reasoning = "Put writing (OI up, price down) -sellers defend support"
    elif avg_put_oi_chg > 0 and avg_put_price_chg > 0:
        # Fresh put buying -BEARISH
        put_bullish_score = -0.5
        put_reasoning = "Fresh put buying (OI up, price up) -bearish bets"
    elif avg_put_oi_chg < 0 and avg_put_price_chg <= 0:
        # Short covering on puts -MILDLY BEARISH
        put_bullish_score = -0.3
        put_reasoning = "Put short covering (OI down, price down) -put sellers exiting"
    elif avg_put_oi_chg < 0 and avg_put_price_chg > 0:
        # Long unwinding -neutral/bullish
        put_bullish_score = 0.2
        put_reasoning = "Put long unwinding (OI down, price up)"
    else:
        put_reasoning = "Put side neutral"

    # Combine: average of call and put bullish scores
    net_score = (call_bullish_score + put_bullish_score) / 2.0  # -1.0 to +1.0

    if net_score > 0.15:
        direction = "BULLISH"
        confidence = min(1.0, abs(net_score))
    elif net_score < -0.15:
        direction = "BEARISH"
        confidence = min(1.0, abs(net_score))
    else:
        direction = "NEUTRAL"
        confidence = 0.2

    reasoning = f"{call_reasoning} + {put_reasoning}"
    return FactorResult(direction, confidence, reasoning, raw_value=net_score)


# ── Factor 2: PCR Extreme (25%) ─────────────────────────────────────────────

def _analyze_pcr(chain: list[dict]) -> FactorResult:
    """
    Detect PCR at extreme levels signaling mean reversion.

    PCR > 1.3 = too many puts = market oversold = BULLISH (contrarian)
    PCR < 0.7 = too many calls = market overbought = BEARISH (contrarian)
    """
    if not chain:
        return FactorResult("NEUTRAL", 0.0, "No chain data", 0.0)

    total_call_oi = sum(float(r.get("call_oi", 0) or 0) for r in chain)
    total_put_oi = sum(float(r.get("put_oi", 0) or 0) for r in chain)

    if total_call_oi == 0:
        return FactorResult("NEUTRAL", 0.0, "Zero call OI", 0.0)

    pcr = total_put_oi / total_call_oi

    if pcr >= PCR_STRONG_BULLISH:
        return FactorResult(
            "BULLISH", 0.95,
            f"PCR {pcr:.2f} extremely high -heavy put writing = strong support",
            pcr
        )
    elif pcr >= PCR_EXTREME_BULLISH:
        return FactorResult(
            "BULLISH", 0.7,
            f"PCR {pcr:.2f} above 1.3 -put writers confident = bullish",
            pcr
        )
    elif pcr <= PCR_STRONG_BEARISH:
        return FactorResult(
            "BEARISH", 0.95,
            f"PCR {pcr:.2f} extremely low -heavy call writing = strong resistance",
            pcr
        )
    elif pcr <= PCR_EXTREME_BEARISH:
        return FactorResult(
            "BEARISH", 0.7,
            f"PCR {pcr:.2f} below 0.7 -call sellers dominate = bearish",
            pcr
        )
    elif 0.9 <= pcr <= 1.1:
        return FactorResult(
            "NEUTRAL", 0.3,
            f"PCR {pcr:.2f} balanced -no edge from OI ratio",
            pcr
        )
    else:
        # Mild lean
        direction = "BULLISH" if pcr > 1.0 else "BEARISH"
        score = 0.4 + abs(pcr - 1.0) * 0.5
        return FactorResult(
            direction, min(0.6, score),
            f"PCR {pcr:.2f} mildly {'bullish' if pcr > 1.0 else 'bearish'}",
            pcr
        )


# ── Factor 3: Max Pain Gravity (20%) ────────────────────────────────────────

def _analyze_max_pain(chain: list[dict], spot: float) -> FactorResult:
    """
    Max pain theory: spot tends to gravitate toward the strike with highest combined OI.

    Spot far above max pain = bearish pull
    Spot far below max pain = bullish pull
    Spot near max pain = neutral (already at equilibrium)
    """
    if not chain or spot <= 0:
        return FactorResult("NEUTRAL", 0.0, "No data for max pain", 0.0)

    # Find max pain strike (highest combined OI)
    max_combined = 0
    max_pain_strike = 0
    for row in chain:
        combined = float(row.get("call_oi", 0) or 0) + float(row.get("put_oi", 0) or 0)
        if combined > max_combined:
            max_combined = combined
            max_pain_strike = row.get("strike", 0)

    if max_pain_strike == 0:
        return FactorResult("NEUTRAL", 0.0, "Could not determine max pain", 0.0)

    distance_pct = (spot - max_pain_strike) / spot * 100  # +ve = spot above MP

    if distance_pct > MAXPAIN_STRONG_PULL_PCT:
        return FactorResult(
            "BEARISH", 0.8,
            f"Spot {distance_pct:+.1f}% above max pain {max_pain_strike} -strong downward gravity",
            distance_pct
        )
    elif distance_pct > MAXPAIN_MILD_PULL_PCT:
        return FactorResult(
            "BEARISH", 0.5,
            f"Spot {distance_pct:+.1f}% above max pain {max_pain_strike} -mild downward pull",
            distance_pct
        )
    elif distance_pct < -MAXPAIN_STRONG_PULL_PCT:
        return FactorResult(
            "BULLISH", 0.8,
            f"Spot {distance_pct:+.1f}% below max pain {max_pain_strike} -strong upward gravity",
            distance_pct
        )
    elif distance_pct < -MAXPAIN_MILD_PULL_PCT:
        return FactorResult(
            "BULLISH", 0.5,
            f"Spot {distance_pct:+.1f}% below max pain {max_pain_strike} -mild upward pull",
            distance_pct
        )
    else:
        return FactorResult(
            "NEUTRAL", 0.3,
            f"Spot near max pain {max_pain_strike} ({distance_pct:+.1f}%) -equilibrium zone",
            distance_pct
        )


# ── Factor 4: Support/Resistance Breach (20%) ───────────────────────────────

def _analyze_sr_breach(chain: list[dict], spot: float) -> FactorResult:
    """
    Check if spot is approaching or breaking through OI-based support/resistance.

    Resistance = strike with highest call OI (sellers defend this level)
    Support = strike with highest put OI (sellers defend this level)

    Spot breaking above resistance = bullish breakout
    Spot breaking below support = bearish breakdown
    Spot between S/R = range-bound
    """
    if not chain or spot <= 0:
        return FactorResult("NEUTRAL", 0.0, "No data for S/R analysis", 0.0)

    # Find resistance (highest call OI)
    max_call_oi = 0
    resistance = 0
    for row in chain:
        call_oi = float(row.get("call_oi", 0) or 0)
        if call_oi > max_call_oi:
            max_call_oi = call_oi
            resistance = row.get("strike", 0)

    # Find support (highest put OI)
    max_put_oi = 0
    support = 0
    for row in chain:
        put_oi = float(row.get("put_oi", 0) or 0)
        if put_oi > max_put_oi:
            max_put_oi = put_oi
            support = row.get("strike", 0)

    if resistance == 0 or support == 0:
        return FactorResult("NEUTRAL", 0.0, "Could not determine S/R", 0.0)

    res_dist_pct = (spot - resistance) / spot * 100  # +ve = above resistance
    sup_dist_pct = (spot - support) / spot * 100      # -ve = below support

    # Check resistance breach
    if res_dist_pct > SR_BREAK_PCT:
        return FactorResult(
            "BULLISH", 0.85,
            f"Spot ABOVE resistance {resistance} -breakout! Call sellers may cover",
            res_dist_pct
        )
    elif abs(res_dist_pct) < SR_BREACH_PCT:
        return FactorResult(
            "BEARISH", 0.5,
            f"Spot AT resistance {resistance} -likely to face selling pressure",
            res_dist_pct
        )

    # Check support breach
    if sup_dist_pct < -SR_BREAK_PCT:
        return FactorResult(
            "BEARISH", 0.85,
            f"Spot BELOW support {support} -breakdown! Put sellers may cover",
            sup_dist_pct
        )
    elif abs(sup_dist_pct) < SR_BREACH_PCT:
        return FactorResult(
            "BULLISH", 0.5,
            f"Spot AT support {support} -likely to find buying interest",
            sup_dist_pct
        )

    # Between S/R
    range_size = resistance - support
    if range_size > 0:
        position = (spot - support) / range_size  # 0 = at support, 1 = at resistance
        if position > 0.7:
            return FactorResult(
                "BEARISH", 0.4,
                f"Spot in upper range (S:{support} / R:{resistance}) -closer to resistance",
                position
            )
        elif position < 0.3:
            return FactorResult(
                "BULLISH", 0.4,
                f"Spot in lower range (S:{support} / R:{resistance}) -closer to support",
                position
            )
        else:
            return FactorResult(
                "NEUTRAL", 0.2,
                f"Spot mid-range (S:{support} / R:{resistance}) -no directional edge",
                position
            )

    return FactorResult("NEUTRAL", 0.2, "S/R analysis inconclusive", 0.0)


# ── Strike Recommender ───────────────────────────────────────────────────────

def _recommend_strike(
    chain: list[dict],
    spot: float,
    direction: str,
    confidence: float,
    symbol: str = "NIFTY",
) -> tuple[Optional[int], Optional[float], Optional[str], str]:
    """
    Recommend the best strike to trade based on signal direction.

    Returns: (strike, ltp, option_type, reasoning)

    Strategy:
    - High confidence (>80%): ATM for max delta capture
    - Medium confidence (60-80%): 1 strike OTM for better risk/reward
    - Lower confidence: 2 strikes OTM (cheaper, defined risk)

    Also looks for the strike with highest OI change in the signal direction
    (smart money indicator).
    """
    if not chain or spot <= 0 or direction == "NEUTRAL":
        return None, None, None, "No recommendation -neutral signal"

    option_type = "CE" if direction == "BULLISH" else "PE"

    # Find ATM strike
    atm_strike = None
    min_dist = float("inf")
    for row in chain:
        strike = row.get("strike", 0)
        dist = abs(strike - spot)
        if dist < min_dist:
            min_dist = dist
            atm_strike = strike

    if atm_strike is None:
        return None, None, None, "Could not find ATM strike"

    # Determine strike step from chain
    strikes = sorted(set(r.get("strike", 0) for r in chain))
    strike_step = 50  # default
    if len(strikes) >= 2:
        diffs = [strikes[i+1] - strikes[i] for i in range(len(strikes)-1)]
        if diffs:
            strike_step = min(d for d in diffs if d > 0)

    # Choose offset based on confidence
    if confidence >= 80:
        offset = 0
        risk_label = "ATM (max delta)"
    elif confidence >= 60:
        offset = 1
        risk_label = "1 OTM (balanced risk/reward)"
    else:
        offset = 2
        risk_label = "2 OTM (defined risk)"

    # Calculate recommended strike
    if direction == "BULLISH":
        rec_strike = atm_strike + (offset * strike_step)
    else:  # BEARISH
        rec_strike = atm_strike - (offset * strike_step)

    # Find the row for this strike to get LTP
    ltp_key = f"{'call' if option_type == 'CE' else 'put'}_ltp"
    oi_chg_key = f"{'call' if option_type == 'CE' else 'put'}_oi_change"

    rec_ltp = None
    for row in chain:
        if row.get("strike") == rec_strike:
            rec_ltp = float(row.get(ltp_key, 0) or 0)
            break

    # Also check: is there a strike with unusually high OI change (smart money)?
    best_smart_strike = None
    best_smart_oi_chg = 0
    for row in chain:
        strike = row.get("strike", 0)
        oi_chg = float(row.get(oi_chg_key, 0) or 0)
        # Only consider strikes within 5 strikes of ATM
        if abs(strike - atm_strike) <= 5 * strike_step and oi_chg > best_smart_oi_chg:
            best_smart_oi_chg = oi_chg
            best_smart_strike = strike

    reasoning = f"{option_type} {rec_strike} -{risk_label}"
    if best_smart_strike and best_smart_strike != rec_strike and best_smart_oi_chg > 0:
        reasoning += f" | Smart money active at {best_smart_strike} (OI +{int(best_smart_oi_chg):,})"

    return rec_strike, rec_ltp, option_type, reasoning


# ── Build Deploy Payload ─────────────────────────────────────────────────────

def _build_deploy_payload(
    signal: OISignal,
    lot_size: int = 75,
) -> Optional[dict]:
    """Build a strategy deploy payload from the signal for one-click paper deploy."""
    if not signal.recommended_strike or not signal.recommended_type:
        return None

    option_type = signal.recommended_type  # "CE" or "PE"
    strike = signal.recommended_strike
    underlying = signal.symbol

    return {
        "name": f"OI Signal -{'Bull' if signal.direction == 'BULLISH' else 'Bear'} {option_type} {strike}",
        "underlying": underlying,
        "execution_mode": "paper",
        "strategy_class": "oi_signal_directional",
        "legs": [
            {
                "option_type": option_type,
                "strike": strike,
                "action": "BUY",
                "lots": 1,
                "quantity": lot_size,
            }
        ],
        "entry_conditions": [],
        "exit_conditions": {
            "stop_loss_pct": 30,     # 30% SL on premium
            "target_pct": 50,        # 50% target on premium
            "max_hold_minutes": 120, # 2hr max hold
        },
        "notes": f"Auto-generated from OI Signal Engine. "
                 f"Confidence: {signal.confidence:.0f}%. "
                 f"Direction: {signal.direction}. "
                 f"Factors: {', '.join(signal.reasoning[:3])}",
        "ai_deployed": True,
        "ai_confidence": signal.confidence / 100.0,
        "ai_signal": f"OI-{signal.direction}",
        "ai_reasoning": signal.reasoning[:3],
    }


# ── Stability Tracker ────────────────────────────────────────────────────────

def _update_stability(symbol: str, direction: str, confidence: float) -> tuple[int, str]:
    """
    Track signal persistence. Returns (consecutive_count, status).

    Status:
    - "CONFIRMED": direction persisted >= STABILITY_THRESHOLD ticks
    - "FORMING": direction is new or recently changed
    - "WEAK": confidence too low
    """
    global _stability

    if symbol not in _stability:
        _stability[symbol] = StabilityTracker()

    tracker = _stability[symbol]

    if confidence < CONFIDENCE_DISPLAY_MIN:
        tracker.last_direction = "NEUTRAL"
        tracker.consecutive_count = 0
        return 0, "WEAK"

    if direction == tracker.last_direction and direction != "NEUTRAL":
        tracker.consecutive_count += 1
    else:
        tracker.consecutive_count = 1

    tracker.last_direction = direction
    tracker.last_confidence = confidence
    tracker.last_timestamp = time.time()

    if tracker.consecutive_count >= STABILITY_THRESHOLD:
        return tracker.consecutive_count, "CONFIRMED"
    else:
        return tracker.consecutive_count, "FORMING"


# ── Main Signal Generator ────────────────────────────────────────────────────

def generate_oi_signals(
    chain: list[dict],
    spot: float,
    symbol: str = "NIFTY",
    lot_size: int = 75,
    vix: float = 0.0,
) -> dict:
    """
    Generate OI-based trading signals from option chain data.

    Args:
        chain: List of option chain rows with call_oi, put_oi, call_oi_change, etc.
        spot: Current spot price of the underlying
        symbol: Underlying symbol (NIFTY, BANKNIFTY, etc.)
        lot_size: Contract lot size for deploy payload
        vix: India VIX value (optional, for context)

    Returns:
        dict with:
            - signal: OISignal object serialized
            - factors: Individual factor results
            - meta: Computation metadata
    """
    now = time.time()

    # ── Run all 4 factors ──
    f_buildup = _analyze_buildup(chain, spot)
    f_pcr = _analyze_pcr(chain)
    f_maxpain = _analyze_max_pain(chain, spot)
    f_sr = _analyze_sr_breach(chain, spot)

    factors = {
        "buildup": f_buildup,
        "pcr": f_pcr,
        "max_pain": f_maxpain,
        "support_resistance": f_sr,
    }

    # ── Weighted confidence scoring ──
    # Direction voting: each factor votes bullish/bearish with its score
    bullish_score = 0.0
    bearish_score = 0.0
    weights = {
        "buildup": W_BUILDUP,
        "pcr": W_PCR,
        "max_pain": W_MAXPAIN,
        "support_resistance": W_SR,
    }

    for name, factor in factors.items():
        w = weights[name]
        if factor.direction == "BULLISH":
            bullish_score += factor.score * w
        elif factor.direction == "BEARISH":
            bearish_score += factor.score * w
        # NEUTRAL contributes nothing

    # Net direction
    net = bullish_score - bearish_score
    if net > 0.05:
        direction = "BULLISH"
        raw_confidence = bullish_score * 100  # scale to 0-100
    elif net < -0.05:
        direction = "BEARISH"
        raw_confidence = bearish_score * 100
    else:
        direction = "NEUTRAL"
        raw_confidence = max(bullish_score, bearish_score) * 30  # low confidence for neutral

    # Boost confidence if multiple factors agree
    agreeing_factors = sum(
        1 for f in factors.values()
        if f.direction == direction and f.score > 0.3
    )
    if agreeing_factors >= 3:
        raw_confidence = min(100, raw_confidence * 1.25)
    elif agreeing_factors >= 4:
        raw_confidence = min(100, raw_confidence * 1.4)

    # VIX adjustment: high VIX = reduce confidence (volatile markets are unpredictable)
    if vix > 20:
        raw_confidence *= 0.85
    elif vix > 25:
        raw_confidence *= 0.7

    confidence = round(min(100, max(0, raw_confidence)), 1)

    # ── Stability tracking ──
    stability_count, status = _update_stability(symbol, direction, confidence)

    # ── Action ──
    action = None
    if direction == "BULLISH":
        action = "BUY_CE"
    elif direction == "BEARISH":
        action = "BUY_PE"

    # ── Strike recommendation ──
    rec_strike, rec_ltp, rec_type, strike_reasoning = _recommend_strike(
        chain, spot, direction, confidence, symbol
    )

    # ── Combined reasoning ──
    reasoning = []
    for name, factor in factors.items():
        if factor.direction != "NEUTRAL" or factor.score > 0.3:
            label = name.replace("_", " ").title()
            reasoning.append(f"{label}: {factor.reasoning}")
    if strike_reasoning:
        reasoning.append(f"Recommended: {strike_reasoning}")

    # ── Build signal ──
    signal = OISignal(
        direction=direction,
        action=action,
        confidence=confidence,
        status=status,
        stability_count=stability_count,
        recommended_strike=rec_strike,
        recommended_ltp=rec_ltp,
        recommended_type=rec_type,
        factors={
            name: {
                "direction": f.direction,
                "score": round(f.score * 100, 1),
                "reasoning": f.reasoning,
                "raw_value": round(f.raw_value, 4) if f.raw_value else 0,
            }
            for name, f in factors.items()
        },
        reasoning=reasoning,
        timestamp=now,
        symbol=symbol,
        spot=spot,
    )

    # ── Deploy payload (only if confidence high enough) ──
    deploy_payload = None
    if confidence >= CONFIDENCE_DEPLOY_MIN and action and status == "CONFIRMED":
        deploy_payload = _build_deploy_payload(signal, lot_size)
        signal.deploy_payload = deploy_payload

    # ── Serialize ──
    return {
        "signal": {
            "direction": signal.direction,
            "action": signal.action,
            "confidence": signal.confidence,
            "status": signal.status,
            "stability_count": signal.stability_count,
            "recommended_strike": signal.recommended_strike,
            "recommended_ltp": signal.recommended_ltp,
            "recommended_type": signal.recommended_type,
            "reasoning": signal.reasoning,
            "deploy_payload": signal.deploy_payload,
            "can_deploy": deploy_payload is not None,
        },
        "factors": signal.factors,
        "meta": {
            "symbol": symbol,
            "spot": spot,
            "vix": vix,
            "lot_size": lot_size,
            "timestamp": now,
            "bullish_score": round(bullish_score, 4),
            "bearish_score": round(bearish_score, 4),
            "agreeing_factors": agreeing_factors,
            "chain_strikes": len(chain),
        },
    }


# ── Self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick validation with synthetic data
    test_chain = []
    spot = 23200
    for i in range(25):
        strike = 22400 + i * 50
        dist = abs(strike - spot)
        is_atm = dist < 25

        test_chain.append({
            "strike": strike,
            "isATM": is_atm,
            "call_oi": max(0, 500000 - dist * 200 + (100000 if strike == 23300 else 0)),
            "put_oi": max(0, 600000 - dist * 250 + (150000 if strike == 23100 else 0)),
            "call_oi_change": 5000 if strike >= spot else -2000,
            "put_oi_change": -3000 if strike >= spot else 8000,
            "call_change_pct": -2.5 if strike >= spot else 1.5,
            "put_change_pct": 3.0 if strike <= spot else -1.8,
            "call_ltp": max(1, (spot - strike + 200) * 0.8),
            "put_ltp": max(1, (strike - spot + 200) * 0.8),
        })

    result = generate_oi_signals(test_chain, spot, "NIFTY", 75, 13.5)
    sig = result["signal"]
    print(f"\n=== OI Signal Engine Self-Test ===")
    print(f"Direction: {sig['direction']}")
    print(f"Action:    {sig['action']}")
    print(f"Confidence: {sig['confidence']}%")
    print(f"Status:    {sig['status']}")
    print(f"Strike:    {sig['recommended_strike']} {sig['recommended_type']}")
    print(f"LTP:       {sig['recommended_ltp']}")
    print(f"\nFactors:")
    for name, f in result["factors"].items():
        print(f"  {name}: {f['direction']} ({f['score']}%) -{f['reasoning']}")
    print(f"\nReasoning:")
    for r in sig["reasoning"]:
        print(f"  - {r}")
    print(f"\nCan Deploy: {sig['can_deploy']}")
    print(f"Meta: bullish={result['meta']['bullish_score']}, bearish={result['meta']['bearish_score']}, agreeing={result['meta']['agreeing_factors']}")

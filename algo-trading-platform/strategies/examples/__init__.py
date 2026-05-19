"""
Example trading strategies for the algo trading platform.

All strategies inherit from BaseStrategy and are ready to run in
live, paper, or backtest mode.
"""

from strategies.examples.iron_condor import IronCondorStrategy
from strategies.examples.straddle_seller import StraddleSellerStrategy
from strategies.examples.momentum_breakout import MomentumBreakoutStrategy
from strategies.examples.mean_reversion import MeanReversionStrategy
from strategies.examples.vwap_scalper import VWAPScalperStrategy
from strategies.examples.expiry_day import ExpiryDayStrategy
from strategies.examples.pair_trading import PairTradingStrategy
from strategies.examples.gamma_scalping import GammaScalpingStrategy
from strategies.examples.orb_options import ORBOptionsStrategy
from strategies.examples.supertrend import SupertrendStrategy

__all__: list[str] = [
    "IronCondorStrategy",
    "StraddleSellerStrategy",
    "MomentumBreakoutStrategy",
    "MeanReversionStrategy",
    "VWAPScalperStrategy",
    "ExpiryDayStrategy",
    "PairTradingStrategy",
    "GammaScalpingStrategy",
    "ORBOptionsStrategy",
    "SupertrendStrategy",
]

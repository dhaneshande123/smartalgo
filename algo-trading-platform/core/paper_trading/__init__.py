"""
Paper Trading module for the algo trading platform.

Provides a simulated broker that uses live (or mock) market data for order
matching, allowing strategies to be tested in real-time without risking
real capital.
"""

from .paper_broker import PaperBroker
from .paper_trading_manager import PaperTradingManager

__all__ = [
    "PaperBroker",
    "PaperTradingManager",
]

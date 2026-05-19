"""NSE/BSE market constants and reference data for the Indian stock market."""

from datetime import time, date
from decimal import Decimal
from zoneinfo import ZoneInfo

# Timezone
IST = ZoneInfo("Asia/Kolkata")

# Market timing
PRE_OPEN_START = time(9, 0)
PRE_OPEN_END = time(9, 8)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
POST_CLOSE_START = time(15, 40)
POST_CLOSE_END = time(16, 0)
BROKER_AUTO_SQUARE_OFF = time(15, 15)

# Tick sizes
TICK_SIZE_OPTIONS = Decimal("0.05")
TICK_SIZE_FUTURES = Decimal("0.05")
TICK_SIZE_EQUITY = Decimal("0.05")

# F&O lot sizes (as of 2024 — should be auto-updated from NSE)
LOT_SIZES: dict[str, int] = {
    "NIFTY": 25,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
    "BANKEX": 15,
}

# Weekly expiry days (0=Monday, 6=Sunday)
WEEKLY_EXPIRY_DAY: dict[str, int] = {
    "NIFTY": 3,        # Thursday
    "BANKNIFTY": 2,     # Wednesday
    "FINNIFTY": 1,      # Tuesday
    "MIDCPNIFTY": 0,    # Monday
    "SENSEX": 4,         # Friday
    "BANKEX": 0,         # Monday
}

# Monthly expiry: last Thursday of the month
MONTHLY_EXPIRY_DAY = 3  # Thursday

# MWPL ban threshold
MWPL_BAN_THRESHOLD = Decimal("0.95")

# STT rates (per side)
STT_RATES = {
    "equity_delivery_buy": Decimal("0.001"),     # 0.1% on buy
    "equity_delivery_sell": Decimal("0.001"),     # 0.1% on sell
    "equity_intraday_sell": Decimal("0.00025"),   # 0.025% on sell side only
    "futures_sell": Decimal("0.000125"),           # 0.0125% on sell side
    "options_sell": Decimal("0.000625"),           # 0.0625% on sell side (on premium)
    "options_exercise": Decimal("0.00125"),        # 0.125% on exercise (on intrinsic value)
}

# Exchange transaction charges (NSE)
EXCHANGE_TXN_CHARGES = {
    "equity": Decimal("0.0000297"),
    "futures": Decimal("0.0000188"),
    "options": Decimal("0.0000495"),
}

# GST rate on (brokerage + exchange txn charges)
GST_RATE = Decimal("0.18")

# SEBI turnover fee
SEBI_FEE = Decimal("0.000001")  # Rs 10 per crore

# Stamp duty (buy side only)
STAMP_DUTY = {
    "equity_delivery": Decimal("0.00015"),    # 0.015%
    "equity_intraday": Decimal("0.00003"),    # 0.003%
    "futures": Decimal("0.00002"),             # 0.002%
    "options": Decimal("0.00003"),             # 0.003%
}

# NSE holidays 2024-2025 (should be auto-fetched, this is fallback)
NSE_HOLIDAYS_2025: list[date] = [
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan Eid)
    date(2025, 4, 10),   # Sri Ram Navami
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 6, 7),    # Bakri Eid
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 16),   # Parsi New Year
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali-Laxmi Pujan
    date(2025, 10, 22),  # Diwali-Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurab Sri Guru Nanak Dev
    date(2025, 12, 25),  # Christmas
]

# Strike step sizes for option chain generation
STRIKE_STEP: dict[str, int] = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
}

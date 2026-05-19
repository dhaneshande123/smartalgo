"""Shared test fixtures for the algo trading platform."""

import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `core.*` imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.models import (
    Exchange,
    Instrument,
    InstrumentType,
    OptionType,
    Segment,
)


@pytest.fixture
def nifty_index() -> Instrument:
    """NIFTY 50 index instrument."""
    return Instrument(
        symbol="NIFTY 50",
        exchange=Exchange.NSE,
        segment=Segment.EQUITY,
        instrument_type=InstrumentType.INDEX,
        lot_size=1,
        token="256265",
    )


@pytest.fixture
def nifty_future() -> Instrument:
    """NIFTY near-month future."""
    return Instrument(
        symbol="NIFTY24APRFUT",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.FUTURE,
        lot_size=25,
        expiry=date(2025, 4, 24),
        underlying="NIFTY",
        token="12345678",
    )


@pytest.fixture
def nifty_ce_option() -> Instrument:
    """NIFTY 24000 CE option."""
    return Instrument(
        symbol="NIFTY24APR24000CE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.CALL_OPTION,
        lot_size=25,
        expiry=date(2025, 4, 24),
        strike=Decimal("24000"),
        option_type=OptionType.CE,
        underlying="NIFTY",
        token="87654321",
    )


@pytest.fixture
def nifty_pe_option() -> Instrument:
    """NIFTY 23000 PE option."""
    return Instrument(
        symbol="NIFTY24APR23000PE",
        exchange=Exchange.NFO,
        segment=Segment.FNO,
        instrument_type=InstrumentType.PUT_OPTION,
        lot_size=25,
        expiry=date(2025, 4, 24),
        strike=Decimal("23000"),
        option_type=OptionType.PE,
        underlying="NIFTY",
        token="87654322",
    )


@pytest.fixture
def sample_timestamp() -> datetime:
    """A fixed timestamp for deterministic tests."""
    return datetime(2025, 4, 1, 9, 30, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    """Reset the config singleton between tests."""
    import core.config as cfg
    yield
    cfg._config = None
    cfg._config_path = None
    cfg._config_env = None


@pytest.fixture
def minimal_config_yaml(tmp_path: Path) -> Path:
    """Create a minimal valid config YAML for testing."""
    config = tmp_path / "platform.yaml"
    config.write_text(
        """\
mode: "paper"
timezone: "Asia/Kolkata"
log_level: "DEBUG"
""",
        encoding="utf-8",
    )
    return config

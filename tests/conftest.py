"""
SmartTrade AI Bot — Shared Test Fixtures
Mock exchange, mock DB session, and sample data for all test files.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
import pytest_asyncio

from app.config import Settings


# ======================================================================
# Event loop fixture (required for async tests)
# ======================================================================
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ======================================================================
# Test settings (never touches real APIs)
# ======================================================================
@pytest.fixture
def test_settings():
    """Settings override for testing — no real API keys needed."""
    return Settings(
        BINANCE_API_KEY="test_key",
        BINANCE_SECRET="test_secret",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_CHAT_ID="",
        DB_URL="sqlite+aiosqlite:///test.db",
        TRADING_PAIR="BNB/USDT",
        CAPITAL_USDT=100.0,
        MAX_TRADE_PCT=0.20,
        DAILY_LOSS_LIMIT_PCT=0.05,
        GRID_LEVELS=16,
        GRID_RANGE_PCT=0.08,
        STOP_LOSS_PCT=0.02,
        TAKE_PROFIT_PCT=0.03,
        CONSECUTIVE_LOSS_LIMIT=3,
        COOLDOWN_SECONDS=5,
        ENVIRONMENT="development",
        LOG_LEVEL="DEBUG",
        FASTAPI_PORT=8000,
    )


# ======================================================================
# Mock ccxt exchange
# ======================================================================
@pytest.fixture
def mock_exchange():
    """Mock ccxt.binance exchange for testing without API calls."""
    exchange = AsyncMock()
    exchange.markets = {"BNB/USDT": {"symbol": "BNB/USDT", "active": True}}
    exchange.load_markets = AsyncMock(return_value=exchange.markets)

    # Default ticker
    exchange.fetch_ticker = AsyncMock(
        return_value={"last": 600.0, "symbol": "BNB/USDT"},
    )

    # Default balance
    exchange.fetch_balance = AsyncMock(
        return_value={"free": {"USDT": 100.0, "BNB": 0.0}},
    )

    # Default order response
    exchange.create_limit_order = AsyncMock(
        return_value={
            "id": "test_order_001",
            "status": "open",
            "symbol": "BNB/USDT",
            "side": "buy",
            "price": 600.0,
            "amount": 0.01,
            "filled": 0.01,
            "average": 600.0,
            "fee": {"cost": 0.006, "currency": "USDT"},
        },
    )
    exchange.create_market_order = AsyncMock(
        return_value={
            "id": "test_order_002",
            "status": "closed",
            "symbol": "BNB/USDT",
            "side": "buy",
            "price": 600.0,
            "amount": 0.01,
            "filled": 0.01,
            "average": 600.5,
            "fee": {"cost": 0.006, "currency": "USDT"},
        },
    )
    exchange.cancel_order = AsyncMock(return_value={"status": "canceled"})
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    exchange.fetch_order = AsyncMock(
        return_value={
            "id": "test_order_001",
            "status": "closed",
            "filled": 0.01,
            "average": 600.0,
            "price": 600.0,
            "fee": {"cost": 0.006},
        },
    )
    exchange.close = AsyncMock()
    return exchange


# ======================================================================
# Mock DB session
# ======================================================================
@pytest_asyncio.fixture
async def mock_db_session():
    """Mock async SQLAlchemy session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


# ======================================================================
# Sample OHLCV data
# ======================================================================
@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate 200 rows of realistic OHLCV data for testing."""
    import numpy as np

    np.random.seed(42)
    n = 200
    timestamps = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    close = 600 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.2
    volume = np.abs(np.random.randn(n) * 1000) + 100

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=timestamps)
    return df


@pytest.fixture
def sample_prices() -> pd.Series:
    """Simple price series for strategy tests."""
    import numpy as np

    np.random.seed(42)
    return pd.Series(
        600 + np.cumsum(np.random.randn(200) * 0.5),
        name="close",
    )

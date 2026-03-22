"""
SmartTrade AI Bot — Grid Bot Unit Tests
Tests grid level calculation, order placement logic, and rebalance triggers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.execution.signal_bus import SignalBus
from app.strategy.grid_bot import GridBot


class TestGridInitialization:
    """Test grid level calculation."""

    def setup_method(self):
        self.bus = SignalBus(cooldown_seconds=1)
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.capital_per_grid = 6.25
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 16
            self.bot = GridBot(self.bus)

    def test_grid_levels_created(self):
        """Grid should create N+1 levels."""
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 16
            ms.capital_per_grid = 6.25
            self.bot.initialize_grid("BNB/USDT", 600.0)

        assert len(self.bot.state.levels) == 17  # 16 intervals = 17 levels
        assert self.bot.state.active is True

    def test_grid_range_correct(self):
        """Grid low/high should be ±8% from center."""
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 16
            ms.capital_per_grid = 6.25
            self.bot.initialize_grid("BNB/USDT", 600.0)

        assert self.bot.state.grid_low == pytest.approx(552.0, rel=0.01)
        assert self.bot.state.grid_high == pytest.approx(648.0, rel=0.01)

    def test_grid_levels_ascending(self):
        """Grid prices should be in ascending order."""
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 16
            ms.capital_per_grid = 6.25
            self.bot.initialize_grid("BNB/USDT", 600.0)

        prices = [lvl.price for lvl in self.bot.state.levels]
        assert prices == sorted(prices)

    def test_grid_symbol_set(self):
        """Grid state should record the symbol."""
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 10
            ms.capital_per_grid = 10.0
            self.bot.initialize_grid("ETH/USDT", 3500.0)

        assert self.bot.state.symbol == "ETH/USDT"


class TestGridOrders:
    """Test order generation logic."""

    def setup_method(self):
        self.bus = SignalBus(cooldown_seconds=0)  # No cooldown for tests
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.capital_per_grid = 6.25
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 10
            self.bot = GridBot(self.bus)

    @pytest.mark.asyncio
    async def test_initial_orders_below_price(self):
        """Initial orders should only be buy orders below current price."""
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 10
            ms.capital_per_grid = 10.0
            self.bot.initialize_grid("BNB/USDT", 600.0)
            count = await self.bot.generate_initial_orders(600.0)

        assert count > 0  # Should have placed some orders
        # All orders should be for levels below 600
        buy_levels = [lvl for lvl in self.bot.state.levels if lvl.has_buy_order]
        assert all(lvl.price < 600.0 for lvl in buy_levels)

    @pytest.mark.asyncio
    async def test_no_orders_without_init(self):
        """Should return 0 if grid not initialized."""
        count = await self.bot.generate_initial_orders(600.0)
        assert count == 0


class TestGridStatus:
    """Test status reporting."""

    def setup_method(self):
        self.bus = SignalBus(cooldown_seconds=0)
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.capital_per_grid = 6.25
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 16
            self.bot = GridBot(self.bus)

    def test_status_when_inactive(self):
        status = self.bot.get_status()
        assert status["active"] is False
        assert status["completed_cycles"] == 0

    def test_status_when_active(self):
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_LEVELS = 16
            ms.capital_per_grid = 6.25
            self.bot.initialize_grid("BNB/USDT", 600.0)

        status = self.bot.get_status()
        assert status["active"] is True
        assert status["symbol"] == "BNB/USDT"
        assert status["total_levels"] == 17

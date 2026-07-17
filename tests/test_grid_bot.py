"""
SmartTrade AI Bot — Grid Bot Unit Tests
Tests grid level calculation, order placement logic, and rebalance triggers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.execution.signal_bus import SignalBus, TradeSignal, SignalPriority
from app.strategy.grid_bot import GridBot


class TestGridInitialization:
    """Test grid level calculation."""

    def setup_method(self):
        self.bus = SignalBus(cooldown_seconds=1)
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.capital_per_grid = 6.25
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_MIN_RANGE_PCT = 0.03
            ms.GRID_MAX_RANGE_PCT = 0.15
            ms.TREND_ADX_THRESHOLD = 25.0
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
            ms.GRID_MIN_RANGE_PCT = 0.03
            ms.GRID_MAX_RANGE_PCT = 0.15
            ms.TREND_ADX_THRESHOLD = 25.0
            ms.GRID_LEVELS = 10
            self.bot = GridBot(self.bus)

    @pytest.mark.asyncio
    async def test_initial_orders_below_price(self):
        """Initial orders should only be buy orders below current price."""
        with patch("app.strategy.grid_bot.settings") as ms:
            ms.GRID_RANGE_PCT = 0.08
            ms.GRID_MIN_RANGE_PCT = 0.03
            ms.GRID_MAX_RANGE_PCT = 0.15
            ms.TREND_ADX_THRESHOLD = 25.0
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

    @pytest.mark.asyncio
    async def test_on_sell_filled_unmatched_price(self):
        """Should return 0.0 and not crash or use loop variable if price matches no level."""
        self.bot.initialize_grid("BNB/USDT", 600.0)
        pnl = await self.bot.on_sell_filled({"price": 9999.0, "quantity": 1.0})
        assert pnl == 0.0


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

    @pytest.mark.asyncio
    async def test_rebalance_cancels_old_orders(self):
        """Rebalance should cancel existing orders via the trader."""
        from unittest.mock import AsyncMock
        mock_trader = AsyncMock()
        self.bot.state.active = True
        self.bot.state.symbol = "BNB/USDT"
        
        await self.bot.rebalance(300.0, trader=mock_trader)
        
        mock_trader.cancel_all_orders.assert_called_once_with("BNB/USDT")
        assert self.bot.state.active is True

class TestSignalBus:
    """Test SignalBus deduplication."""

    @pytest.mark.asyncio
    async def test_dedup_with_price(self):
        bus = SignalBus(cooldown_seconds=1)
        # Same symbol, side, strategy but DIFFERENT price -> should NOT dedup
        sig1 = TradeSignal(priority=SignalPriority.GRID_ORDER, timestamp=1, symbol="A", side="buy", order_type="limit", price=100.0, quantity=1, strategy="grid_bot")
        sig2 = TradeSignal(priority=SignalPriority.GRID_ORDER, timestamp=2, symbol="A", side="buy", order_type="limit", price=101.0, quantity=1, strategy="grid_bot")
        
        assert await bus.push(sig1) is True
        assert await bus.push(sig2) is True
        assert bus.pending_count == 2

class TestGridDynamicSizing:
    """Test ATR adaptive sizing and ADX trend filters."""

    def setup_method(self):
        self.bus = SignalBus(cooldown_seconds=0)

    @patch("app.strategy.grid_bot.settings")
    def test_atr_adaptive_sizing_within_bounds(self, ms):
        ms.capital_per_grid = 10.0
        ms.GRID_RANGE_PCT = 0.08
        ms.GRID_MIN_RANGE_PCT = 0.03
        ms.GRID_MAX_RANGE_PCT = 0.15
        ms.TREND_ADX_THRESHOLD = 25.0
        ms.GRID_LEVELS = 10
        self.bot = GridBot(self.bus)
        
        # ATR = 30.0, Price = 600.0 => ATR% = 0.05. 2x ATR = 0.10.
        # Should be between 0.03 and 0.15, so 0.10 is used.
        self.bot.update_indicators(atr=30.0, adx=15.0)
        self.bot.initialize_grid("BNB/USDT", 600.0)
        
        # Grid range should be 0.10
        assert self.bot.state.grid_low == pytest.approx(540.0, rel=0.01)
        assert self.bot.state.grid_high == pytest.approx(660.0, rel=0.01)

    @patch("app.strategy.grid_bot.settings")
    def test_atr_adaptive_sizing_clamped_min(self, ms):
        ms.capital_per_grid = 10.0
        ms.GRID_RANGE_PCT = 0.08
        ms.GRID_MIN_RANGE_PCT = 0.03
        ms.GRID_MAX_RANGE_PCT = 0.15
        ms.TREND_ADX_THRESHOLD = 25.0
        ms.GRID_LEVELS = 10
        self.bot = GridBot(self.bus)
        
        # ATR = 3.0, Price = 600.0 => ATR% = 0.005. 2x ATR = 0.01
        # Should be clamped to min 0.03.
        self.bot.update_indicators(atr=3.0, adx=15.0)
        self.bot.initialize_grid("BNB/USDT", 600.0)
        
        # Grid range should be 0.03
        assert self.bot.state.grid_low == pytest.approx(582.0, rel=0.01)
        assert self.bot.state.grid_high == pytest.approx(618.0, rel=0.01)

    @patch("app.strategy.grid_bot.settings")
    def test_atr_adaptive_sizing_clamped_max(self, ms):
        ms.capital_per_grid = 10.0
        ms.GRID_RANGE_PCT = 0.08
        ms.GRID_MIN_RANGE_PCT = 0.03
        ms.GRID_MAX_RANGE_PCT = 0.15
        ms.TREND_ADX_THRESHOLD = 25.0
        ms.GRID_LEVELS = 10
        self.bot = GridBot(self.bus)
        
        # ATR = 90.0, Price = 600.0 => ATR% = 0.15. 2x ATR = 0.30
        # Should be clamped to max 0.15.
        self.bot.update_indicators(atr=90.0, adx=15.0)
        self.bot.initialize_grid("BNB/USDT", 600.0)
        
        assert self.bot.state.grid_low == pytest.approx(510.0, rel=0.01)

    @pytest.mark.asyncio
    @patch("app.strategy.grid_bot.settings")
    async def test_adx_trend_filter_blocks_initial_orders(self, ms):
        ms.capital_per_grid = 10.0
        ms.GRID_RANGE_PCT = 0.08
        ms.GRID_MIN_RANGE_PCT = 0.03
        ms.GRID_MAX_RANGE_PCT = 0.15
        ms.TREND_ADX_THRESHOLD = 25.0
        ms.GRID_LEVELS = 10
        self.bot = GridBot(self.bus)
        
        # ADX = 30 (above 25 threshold)
        self.bot.update_indicators(atr=30.0, adx=30.0)
        self.bot.initialize_grid("BNB/USDT", 600.0)
        
        count = await self.bot.generate_initial_orders(600.0)
        assert count == 0  # Should be blocked


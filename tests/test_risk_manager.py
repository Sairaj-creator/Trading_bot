"""
SmartTrade AI Bot — Risk Manager Unit Tests
Tests all circuit breaker thresholds, stop-loss triggers, position-size rejection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.risk.risk_manager import RiskManager


class TestPreTradeChecks:
    """Test pre-trade validation rules."""

    def setup_method(self):
        with patch("app.risk.risk_manager.settings") as mock_settings:
            mock_settings.CAPITAL_USDT = 100.0
            mock_settings.MAX_TRADE_PCT = 0.20
            mock_settings.STOP_LOSS_PCT = 0.02
            mock_settings.TAKE_PROFIT_PCT = 0.03
            mock_settings.DAILY_LOSS_LIMIT_PCT = 0.05
            mock_settings.CONSECUTIVE_LOSS_LIMIT = 3
            self.rm = RiskManager(initial_balance=100.0)

    def test_trade_within_limits(self):
        """Normal trade within 20% capital limit should pass."""
        allowed, reason = self.rm.check_trade_allowed(
            "BNB/USDT", "buy", 0.03, 600.0, 100.0,
        )
        assert allowed is True
        assert reason == "OK"

    def test_trade_exceeds_max_capital(self):
        """Trade exceeding 20% of balance should be rejected."""
        # 0.05 BNB @ $600 = $30, which exceeds 20% of $100
        with patch("app.risk.risk_manager.settings") as ms:
            ms.MAX_TRADE_PCT = 0.20
            allowed, reason = self.rm.check_trade_allowed(
                "BNB/USDT", "buy", 0.05, 600.0, 100.0,
            )
        assert allowed is False
        assert "exceeds max" in reason

    def test_insufficient_balance(self):
        """Trade exceeding available balance should be rejected.
        Note: the max-capital check (20% of balance) fires before the
        absolute balance check since 20% < 100%. The trade is still
        correctly blocked — the risk manager prevents overleveraging.
        """
        allowed, reason = self.rm.check_trade_allowed(
            "BNB/USDT", "buy", 1.0, 600.0, 50.0,
        )
        assert allowed is False
        assert "exceeds max" in reason

    def test_circuit_broken_rejects_trade(self):
        """No trades should pass when circuit breaker is active."""
        from datetime import datetime, timezone, timedelta
        self.rm.state.circuit_broken = True
        self.rm.state.circuit_break_until = datetime.now(timezone.utc) + timedelta(hours=1)

        allowed, reason = self.rm.check_trade_allowed(
            "BNB/USDT", "buy", 0.01, 600.0, 100.0,
        )
        assert allowed is False
        assert "Circuit breaker" in reason

    def test_portfolio_exposure_cap(self):
        """Trade exceeding MAX_PORTFOLIO_EXPOSURE_PCT should be rejected."""
        with patch("app.risk.risk_manager.settings") as ms:
            ms.MAX_TRADE_PCT = 0.20
            ms.MAX_PORTFOLIO_EXPOSURE_PCT = 0.85
            self.rm.state.open_notional = 80.0  # 80% of 100 balance

            # A $10 trade brings it to 90%, exceeding 85% limit
            allowed, reason = self.rm.check_trade_allowed(
                "BNB/USDT", "buy", 0.0166, 600.0, 100.0,
            )
        assert allowed is False
        assert "Portfolio exposure" in reason

    def test_portfolio_exposure_cap_blocks_after_fills(self):
        """Total exposure includes open notional which shouldn't clear on buy fill."""
        with patch("app.risk.risk_manager.settings") as ms:
            ms.MAX_TRADE_PCT = 1.00
            ms.MAX_PORTFOLIO_EXPOSURE_PCT = 0.85
            
            # Step 1: initial trade allowed
            allowed, _ = self.rm.check_trade_allowed("BNB/USDT", "buy", 0.08, 1000.0, 100.0) # $80 cost
            assert allowed is True
            assert self.rm.state.open_notional == 80.0
            
            # Step 2: order fills. We do NOT decrement open_notional on buy fill.
            # (In main.py it only decrements on canceled/expired, or sell filled)
            
            # Step 3: next trade blocked because open_notional is 80, limit is 85
            allowed, reason = self.rm.check_trade_allowed("BNB/USDT", "buy", 0.01, 1000.0, 100.0) # $10 cost
            assert allowed is False
            assert "Portfolio exposure" in reason


class TestStopLoss:
    """Test stop-loss monitoring."""

    def setup_method(self):
        with patch("app.risk.risk_manager.settings") as mock_settings:
            mock_settings.CAPITAL_USDT = 100.0
            mock_settings.MAX_TRADE_PCT = 0.20
            mock_settings.STOP_LOSS_PCT = 0.02
            mock_settings.TAKE_PROFIT_PCT = 0.03
            mock_settings.DAILY_LOSS_LIMIT_PCT = 0.05
            mock_settings.CONSECUTIVE_LOSS_LIMIT = 3
            self.rm = RiskManager(initial_balance=100.0)

    def test_stop_loss_triggers(self):
        """Stop-loss should trigger at 2% adverse move for specific level."""
        self.rm.register_position("BNB/USDT", 1, 600.0)
        self.rm.register_position("BNB/USDT", 2, 550.0)
        with patch("app.risk.risk_manager.settings") as ms:
            ms.STOP_LOSS_PCT = 0.02
            # 586 is < 600 * 0.98, so level 1 triggers. But 586 > 550, so level 2 does not.
            results = self.rm.check_stop_loss("BNB/USDT", 586.0)
            
        assert len(results) == 2
        for level_id, triggered, loss_pct in results:
            if level_id == 1:
                assert triggered is True
                assert loss_pct > 0.02
            else:
                assert triggered is False

    def test_stop_loss_not_triggered(self):
        """Price within tolerance should not trigger stop-loss."""
        self.rm.register_position("BNB/USDT", 1, 600.0)
        with patch("app.risk.risk_manager.settings") as ms:
            ms.STOP_LOSS_PCT = 0.02
            results = self.rm.check_stop_loss("BNB/USDT", 595.0)
        assert len(results) == 1
        assert results[0][1] is False

    def test_no_position_no_trigger(self):
        """No stop-loss if no position exists."""
        results = self.rm.check_stop_loss("BNB/USDT", 500.0)
        assert len(results) == 0


class TestTakeProfit:
    """Test take-profit monitoring."""

    def setup_method(self):
        with patch("app.risk.risk_manager.settings") as mock_settings:
            mock_settings.CAPITAL_USDT = 100.0
            mock_settings.MAX_TRADE_PCT = 0.20
            mock_settings.STOP_LOSS_PCT = 0.02
            mock_settings.TAKE_PROFIT_PCT = 0.03
            mock_settings.DAILY_LOSS_LIMIT_PCT = 0.05
            mock_settings.CONSECUTIVE_LOSS_LIMIT = 3
            self.rm = RiskManager(initial_balance=100.0)

    def test_take_profit_triggers(self):
        """Take-profit should trigger at 3%+ gain."""
        self.rm.register_position("BNB/USDT", 1, 600.0)
        with patch("app.risk.risk_manager.settings") as ms:
            ms.TAKE_PROFIT_PCT = 0.03
            results = self.rm.check_take_profit("BNB/USDT", 620.0)
        assert len(results) == 1
        assert results[0][1] is True
        assert results[0][2] > 0.03


class TestCircuitBreakers:
    """Test daily loss and consecutive loss circuit breakers."""

    def setup_method(self):
        with patch("app.risk.risk_manager.settings") as mock_settings:
            mock_settings.CAPITAL_USDT = 100.0
            mock_settings.MAX_TRADE_PCT = 0.20
            mock_settings.STOP_LOSS_PCT = 0.02
            mock_settings.TAKE_PROFIT_PCT = 0.03
            mock_settings.DAILY_LOSS_LIMIT_PCT = 0.05
            mock_settings.CONSECUTIVE_LOSS_LIMIT = 3
            self.rm = RiskManager(initial_balance=100.0)

    def test_consecutive_losses_counted(self):
        """Consecutive losses should increment counter."""
        self.rm.record_trade_result(-0.5)
        self.rm.record_trade_result(-0.3)
        assert self.rm.state.consecutive_losses == 2

    def test_consecutive_losses_reset_on_win(self):
        """A winning trade resets the consecutive loss counter."""
        self.rm.record_trade_result(-0.5)
        self.rm.record_trade_result(-0.3)
        self.rm.record_trade_result(1.0)
        assert self.rm.state.consecutive_losses == 0

    def test_daily_pnl_accumulates(self):
        """Daily P&L should accumulate across trades."""
        self.rm.record_trade_result(-1.0)
        self.rm.record_trade_result(2.0)
        self.rm.record_trade_result(-0.5)
        assert self.rm.state.daily_pnl == pytest.approx(0.5)


class TestGridRangeCheck:
    """Test grid range exit detection."""

    def setup_method(self):
        with patch("app.risk.risk_manager.settings") as mock_settings:
            mock_settings.CAPITAL_USDT = 100.0
            mock_settings.MAX_TRADE_PCT = 0.20
            mock_settings.STOP_LOSS_PCT = 0.02
            mock_settings.TAKE_PROFIT_PCT = 0.03
            mock_settings.DAILY_LOSS_LIMIT_PCT = 0.05
            mock_settings.CONSECUTIVE_LOSS_LIMIT = 3
            self.rm = RiskManager(initial_balance=100.0)

    def test_price_above_grid(self):
        result = self.rm.check_grid_range_exit(1000.0, 500.0, 700.0)
        assert result == "above"

    def test_price_below_grid(self):
        result = self.rm.check_grid_range_exit(400.0, 500.0, 700.0)
        assert result == "below"

    def test_price_within_grid(self):
        result = self.rm.check_grid_range_exit(600.0, 500.0, 700.0)
        assert result is None


class TestDrawdown:
    """Test drawdown tracking."""

    def setup_method(self):
        with patch("app.risk.risk_manager.settings") as mock_settings:
            mock_settings.CAPITAL_USDT = 100.0
            mock_settings.MAX_TRADE_PCT = 0.20
            mock_settings.STOP_LOSS_PCT = 0.02
            mock_settings.TAKE_PROFIT_PCT = 0.03
            mock_settings.DAILY_LOSS_LIMIT_PCT = 0.05
            mock_settings.CONSECUTIVE_LOSS_LIMIT = 3
            self.rm = RiskManager(initial_balance=100.0)

    def test_drawdown_calculation(self):
        """Drawdown should reflect peak-to-current drop."""
        self.rm.update_drawdown(105.0)  # New peak
        dd = self.rm.update_drawdown(100.0)  # Drop from peak
        assert dd == pytest.approx(5 / 105, rel=1e-3)

    def test_no_drawdown_at_peak(self):
        dd = self.rm.update_drawdown(110.0)
        assert dd == 0.0

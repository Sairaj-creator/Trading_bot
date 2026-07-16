"""
SmartTrade AI Bot — Trader Unit Tests
Mock Binance API, test retry logic, slippage validation, fee calculation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import ccxt.async_support as ccxt

from app.execution.trader import Trader, MAX_RETRIES


@pytest.fixture
def trader_instance(mock_exchange):
    """Create a Trader with the mock exchange."""
    return Trader(mock_exchange)


class TestFeeCalculation:
    """Test fee-aware order sizing."""

    def test_fee_adjusted_quantity(self):
        qty = Trader.fee_adjusted_quantity(1.0, fee_pct=0.001)
        assert qty == pytest.approx(0.999, rel=1e-4)

    def test_fee_adjusted_cost(self):
        cost = Trader.fee_adjusted_cost(100.0, fee_pct=0.001)
        assert cost == pytest.approx(100.1, rel=1e-4)

    def test_calculate_order_quantity(self):
        trader = Trader(AsyncMock())
        qty = trader.calculate_order_quantity(price=600.0, capital_usdt=100.0)
        # 100 / 1.001 / 600 ≈ 0.1665
        assert qty == pytest.approx(100 / 1.001 / 600, rel=1e-3)

    def test_zero_price_returns_zero(self):
        trader = Trader(AsyncMock())
        qty = trader.calculate_order_quantity(price=0.0, capital_usdt=100.0)
        assert qty == 0.0


class TestOrderPlacement:
    """Test order creation with mock exchange."""

    @pytest.mark.asyncio
    async def test_limit_order_success(self, trader_instance, mock_exchange):
        """Successful limit order should return order dict."""
        with patch("app.execution.trader.notify_trade", new_callable=AsyncMock):
            result = await trader_instance.create_order(
                "BNB/USDT", "buy", "limit", 0.01, price=600.0,
            )
        assert result is not None
        assert result["id"] == "test_order_001"
        mock_exchange.create_limit_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_market_order_success(self, trader_instance, mock_exchange):
        """Successful market order should return order dict."""
        with patch("app.execution.trader.notify_trade", new_callable=AsyncMock):
            result = await trader_instance.create_order(
                "BNB/USDT", "buy", "market", 0.01,
            )
        assert result is not None
        mock_exchange.create_market_order.assert_called_once()


class TestRetryLogic:
    """Test retryable vs fatal error handling."""

    @pytest.mark.asyncio
    async def test_fatal_error_no_retry(self, mock_exchange):
        """InsufficientFunds should fail immediately — no retry."""
        mock_exchange.create_market_order = AsyncMock(
            side_effect=ccxt.InsufficientFunds("Not enough USDT"),
        )
        trader = Trader(mock_exchange)

        with patch("app.execution.trader.notify_error", new_callable=AsyncMock):
            result = await trader.create_order(
                "BNB/USDT", "buy", "market", 0.01,
            )

        assert result is None
        assert mock_exchange.create_market_order.call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_invalid_order_no_retry(self, mock_exchange):
        """InvalidOrder should fail immediately — no retry."""
        mock_exchange.create_limit_order = AsyncMock(
            side_effect=ccxt.InvalidOrder("Invalid quantity"),
        )
        trader = Trader(mock_exchange)

        with patch("app.execution.trader.notify_error", new_callable=AsyncMock):
            result = await trader.create_order(
                "BNB/USDT", "buy", "limit", 0.01, price=600.0,
            )

        assert result is None
        assert mock_exchange.create_limit_order.call_count == 1

    @pytest.mark.asyncio
    async def test_network_error_retries(self, mock_exchange):
        """NetworkError should retry up to MAX_RETRIES then fail."""
        mock_exchange.create_market_order = AsyncMock(
            side_effect=ccxt.NetworkError("Connection lost"),
        )
        trader = Trader(mock_exchange)

        with patch("app.execution.trader.notify_error", new_callable=AsyncMock):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await trader.create_order(
                    "BNB/USDT", "buy", "market", 0.01,
                )

        assert result is None
        assert mock_exchange.create_market_order.call_count == MAX_RETRIES + 1

    @pytest.mark.asyncio
    async def test_network_error_succeeds_on_retry(self, mock_exchange):
        """Should succeed if retry resolves the network issue."""
        mock_exchange.create_market_order = AsyncMock(
            side_effect=[
                ccxt.NetworkError("Timeout"),
                {
                    "id": "retry_success",
                    "status": "closed",
                    "filled": 0.01,
                    "average": 600.0,
                    "price": 600.0,
                    "fee": {"cost": 0.006, "currency": "USDT"},
                },
            ],
        )
        trader = Trader(mock_exchange)

        with patch("app.execution.trader.notify_trade", new_callable=AsyncMock):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await trader.create_order(
                    "BNB/USDT", "buy", "market", 0.01,
                )

        assert result is not None
        assert result["id"] == "retry_success"
        assert mock_exchange.create_market_order.call_count == 2


class TestCancelOrder:
    """Test order cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_success(self, trader_instance, mock_exchange):
        result = await trader_instance.cancel_order("order_001", "BNB/USDT")
        assert result is True
        mock_exchange.cancel_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_all_empty(self, trader_instance, mock_exchange):
        count = await trader_instance.cancel_all_orders("BNB/USDT")
        assert count == 0

    @pytest.mark.asyncio
    async def test_cancel_order_not_found_returns_true(self, trader_instance, mock_exchange):
        mock_exchange.cancel_order = AsyncMock(side_effect=ccxt.OrderNotFound("Already done"))
        result = await trader_instance.cancel_order("order_001", "BNB/USDT")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_all_counts_not_found_as_success(self, trader_instance, mock_exchange):
        mock_exchange.fetch_open_orders = AsyncMock(return_value=[{"id": "1"}, {"id": "2"}])
        mock_exchange.cancel_order = AsyncMock(side_effect=[None, ccxt.OrderNotFound("Already done")])
        count = await trader_instance.cancel_all_orders("BNB/USDT")
        assert count == 2

import sys
from unittest.mock import AsyncMock, patch, MagicMock

# Mock out the missing database module
sys.modules['database'] = MagicMock()
sys.modules['database.models'] = MagicMock()

import pytest
from app.main import _signal_consumer_loop, _handle_cancelled_order
from app.execution.signal_bus import TradeSignal, SignalPriority

@pytest.mark.asyncio
async def test_failed_placement_rollback():
    """Test that open_notional is rolled back if create_order returns None."""
    with patch("app.main.signal_bus") as mock_sb, \
         patch("app.main.risk_manager") as mock_rm, \
         patch("app.main.trader") as mock_trader, \
         patch("app.main.fetcher") as mock_fetcher:
        
        # Setup mocks
        signal = TradeSignal(
            priority=SignalPriority.GRID_ORDER,
            timestamp=1,
            symbol="BNB/USDT",
            side="buy",
            order_type="limit",
            price=100.0,
            quantity=1.0,
            strategy="grid_bot"
        )
        # return signal then block forever
        async def mock_pop():
            if not getattr(mock_pop, "called", False):
                mock_pop.called = True
                return signal
            else:
                import asyncio
                await asyncio.sleep(9999)
                return signal
        mock_sb.pop = mock_pop
        
        mock_fetcher.get_balance = AsyncMock(return_value=1000.0)
        mock_rm.check_trade_allowed.return_value = (True, "OK")
        mock_trader.create_order = AsyncMock(return_value=None)
        
        import asyncio
        task = asyncio.create_task(_signal_consumer_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        
        # Verify rollback
        mock_rm.decrement_open_notional.assert_called_once_with(100.0)

@pytest.mark.asyncio
async def test_handle_cancelled_order():
    """Test that cancelling an order explicitly releases notional."""
    with patch("app.main.order_tracker") as mock_ot, \
         patch("app.main.risk_manager") as mock_rm:
        
        mock_ot._tracked_orders = {
            "order_123": {
                "side": "buy",
                "price": 100.0,
                "quantity": 2.0
            }
        }
        
        _handle_cancelled_order("order_123")
        
        mock_rm.decrement_open_notional.assert_called_once_with(200.0)
        assert "order_123" not in mock_ot._tracked_orders

@pytest.mark.asyncio
async def test_stranded_inventory_liquidated_on_rebalance():
    """Test that stranded inventory is liquidated via market sell when rebalance triggers."""
    with patch("app.main.signal_bus") as mock_sb, \
         patch("app.main.risk_manager") as mock_rm, \
         patch("app.main.grid_bot") as mock_grid, \
         patch("app.main.trader") as mock_trader, \
         patch("app.main.fetcher") as mock_fetcher:
        
        mock_fetcher.get_current_price = AsyncMock(return_value=120.0)
        mock_grid.state.active = True
        mock_grid.state.grid_low = 80.0
        mock_grid.state.grid_high = 110.0
        mock_grid.capital_per_grid = 100.0
        
        # Simulate price exiting grid
        mock_rm.check_grid_range_exit.return_value = "above"
        
        # Simulate 1 open position
        mock_rm.state.open_positions = {("BNB/USDT", 2): 100.0}
        mock_rm.check_stop_loss.return_value = []
        
        from app.main import _candle_update_job
        import app.config as settings
        settings.TRADING_PAIR = "BNB/USDT"
        
        await _candle_update_job()
        
        # Verify market sell signal was pushed
        assert mock_sb.push.call_count == 1
        signal = mock_sb.push.call_args[0][0]
        assert signal.side == "sell"
        assert signal.order_type == "market"
        assert signal.price == 120.0
        assert signal.quantity == 1.0  # 100 capital / 100 entry_price
        
        # Verify synchronous cleanup
        mock_rm.decrement_open_notional.assert_called_once_with(100.0 * 1.0)
        mock_rm.close_position.assert_called_once_with("BNB/USDT", 2)
        mock_grid.rebalance.assert_called_once_with(120.0, mock_trader)


"""
SmartTrade AI Bot — Grid Trading Strategy
Divides a price range into intervals, buys at lower grid levels,
sells one level above. Profits from completed buy-sell cycles.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

from app.config import settings
from app.execution.signal_bus import SignalBus, TradeSignal, SignalPriority


@dataclass
class GridLevel:
    """Represents a single grid price level."""
    index: int
    price: float
    has_buy_order: bool = False
    has_sell_order: bool = False
    buy_order_id: str | None = None
    sell_order_id: str | None = None
    buy_filled: bool = False


@dataclass
class GridState:
    """Persistent state for the grid bot."""
    symbol: str = ""
    grid_low: float = 0.0
    grid_high: float = 0.0
    levels: list[GridLevel] = field(default_factory=list)
    completed_cycles: int = 0
    total_pnl: float = 0.0
    active: bool = False


class GridBot:
    """
    Grid trading engine.
    Places buy orders below spot and sell orders above.
    Each completed buy-sell cycle earns the grid interval minus fees.
    """

    def __init__(self, signal_bus: SignalBus) -> None:
        self.signal_bus = signal_bus
        self.state = GridState()
        self.capital_per_grid = settings.capital_per_grid

    # ------------------------------------------------------------------
    # Grid Initialization
    # ------------------------------------------------------------------

    def initialize_grid(
        self,
        symbol: str,
        current_price: float,
        grid_range_pct: float | None = None,
        num_levels: int | None = None,
    ) -> None:
        """
        Calculate grid levels centered on current price.
        Grid range is ±grid_range_pct from current price.
        """
        grid_range = grid_range_pct or settings.GRID_RANGE_PCT
        levels = num_levels or settings.GRID_LEVELS

        grid_low = current_price * (1 - grid_range)
        grid_high = current_price * (1 + grid_range)
        interval = (grid_high - grid_low) / levels

        # Validate minimum grid spacing (must clear fees: >0.2% round-trip)
        spacing_pct = interval / current_price
        if spacing_pct < 0.01:  # 1% minimum to clear 0.2% fees with margin
            logger.warning(
                "Grid spacing {:.3%} is below 1% minimum. "
                "Reducing grid levels or widening range recommended.",
                spacing_pct,
            )

        grid_levels = []
        for i in range(levels + 1):
            price = grid_low + (i * interval)
            grid_levels.append(GridLevel(index=i, price=round(price, 4)))

        self.state = GridState(
            symbol=symbol,
            grid_low=round(grid_low, 4),
            grid_high=round(grid_high, 4),
            levels=grid_levels,
            active=True,
        )

        logger.info(
            "Grid initialised for {}: range ${:.4f}–${:.4f}, "
            "{} levels, ${:.2f}/grid, spacing={:.3%}",
            symbol, grid_low, grid_high,
            levels, self.capital_per_grid, spacing_pct,
        )

    # ------------------------------------------------------------------
    # Order Generation
    # ------------------------------------------------------------------

    async def generate_initial_orders(self, current_price: float) -> int:
        """
        Generate buy orders below current price and sell orders above.
        Returns the number of signals pushed.
        """
        if not self.state.active:
            logger.warning("Grid not active — call initialize_grid() first.")
            return 0

        signals_pushed = 0
        for level in self.state.levels:
            if level.price < current_price and not level.has_buy_order:
                # Place buy order at this level
                quantity = self.capital_per_grid / level.price
                signal = TradeSignal(
                    priority=SignalPriority.GRID_ORDER,
                    timestamp=time.time(),
                    symbol=self.state.symbol,
                    side="buy",
                    order_type="limit",
                    price=level.price,
                    quantity=round(quantity, 6),
                    strategy="grid_bot",
                )
                if await self.signal_bus.push(signal):
                    level.has_buy_order = True
                    signals_pushed += 1

        logger.info("Generated {} initial grid buy orders.", signals_pushed)
        return signals_pushed

    async def on_buy_filled(self, filled_order: dict) -> None:
        """
        Callback when a grid buy order fills.
        Places a sell order one grid level above.
        """
        fill_price = filled_order["price"]

        # Find the matching grid level
        target_level = None
        for level in self.state.levels:
            if abs(level.price - fill_price) / fill_price < 0.005:  # 0.5% tolerance
                target_level = level
                break

        if target_level is None:
            logger.warning("Filled buy at {:.4f} doesn't match any grid level.", fill_price)
            return

        target_level.buy_filled = True
        target_level.buy_order_id = filled_order.get("order_id")

        # Place sell one level above
        sell_level_idx = target_level.index + 1
        if sell_level_idx >= len(self.state.levels):
            logger.info("Buy filled at top grid level — no sell level above.")
            return

        sell_level = self.state.levels[sell_level_idx]
        quantity = filled_order["quantity"]

        signal = TradeSignal(
            priority=SignalPriority.GRID_ORDER,
            timestamp=time.time(),
            symbol=self.state.symbol,
            side="sell",
            order_type="limit",
            price=sell_level.price,
            quantity=round(quantity, 6),
            strategy="grid_bot",
        )

        if await self.signal_bus.push(signal):
            sell_level.has_sell_order = True
            logger.info(
                "Grid sell placed at ${:.4f} (level {}) for qty {:.6f}",
                sell_level.price, sell_level_idx, quantity,
            )

    async def on_sell_filled(self, filled_order: dict) -> None:
        """
        Callback when a grid sell order fills.
        Records the completed cycle profit and replaces the buy order.
        """
        fill_price = filled_order["price"]
        quantity = filled_order["quantity"]

        # Find the grid level
        for level in self.state.levels:
            if abs(level.price - fill_price) / fill_price < 0.005:
                level.has_sell_order = False
                break

        # Calculate cycle profit (approximate — one grid interval minus fees)
        grid_interval = 0.0
        if len(self.state.levels) > 1:
            grid_interval = self.state.levels[1].price - self.state.levels[0].price
        fee_cost = fill_price * quantity * 0.002  # 0.1% buy + 0.1% sell
        cycle_pnl = (grid_interval * quantity) - fee_cost

        self.state.completed_cycles += 1
        self.state.total_pnl += cycle_pnl

        logger.info(
            "Grid cycle #{} complete: P&L ${:.4f} (total ${:.4f})",
            self.state.completed_cycles, cycle_pnl, self.state.total_pnl,
        )

        # Replace the buy order at the lower level
        buy_level_idx = max(0, self.state.levels.index(level) - 1) if level else 0
        if buy_level_idx < len(self.state.levels):
            buy_level = self.state.levels[buy_level_idx]
            if not buy_level.has_buy_order:
                qty = self.capital_per_grid / buy_level.price
                signal = TradeSignal(
                    priority=SignalPriority.GRID_ORDER,
                    timestamp=time.time(),
                    symbol=self.state.symbol,
                    side="buy",
                    order_type="limit",
                    price=buy_level.price,
                    quantity=round(qty, 6),
                    strategy="grid_bot",
                )
                await self.signal_bus.push(signal)
                buy_level.has_buy_order = True

    # ------------------------------------------------------------------
    # Grid Rebalancing
    # ------------------------------------------------------------------

    async def rebalance(self, current_price: float) -> None:
        """
        Rebalance the grid when price has drifted significantly.
        Cancels existing orders and creates a new grid centered on current price.
        """
        logger.info("Rebalancing grid around ${:.4f}...", current_price)
        self.state.active = False
        # Reset all order flags
        for level in self.state.levels:
            level.has_buy_order = False
            level.has_sell_order = False
            level.buy_filled = False
        self.initialize_grid(self.state.symbol, current_price)
        await self.generate_initial_orders(current_price)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current grid bot status for the health API."""
        active_buys = sum(1 for lvl in self.state.levels if lvl.has_buy_order)
        active_sells = sum(1 for lvl in self.state.levels if lvl.has_sell_order)
        return {
            "symbol": self.state.symbol,
            "active": self.state.active,
            "grid_range": f"${self.state.grid_low}–${self.state.grid_high}",
            "total_levels": len(self.state.levels),
            "active_buys": active_buys,
            "active_sells": active_sells,
            "completed_cycles": self.state.completed_cycles,
            "total_pnl": round(self.state.total_pnl, 4),
        }

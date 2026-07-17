"""
SmartTrade AI Bot — Risk Management Engine
Pre-trade checks, stop-loss monitoring, circuit breakers, position limits.
Pair-trading checks are gated behind a config flag (no circular dependency).
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field

from loguru import logger

from app.config import settings


@dataclass
class RiskState:
    """Mutable runtime risk state — reset daily."""
    daily_pnl: float = 0.0
    daily_peak_balance: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    circuit_broken: bool = False
    circuit_break_until: datetime | None = None
    open_positions: dict = field(default_factory=dict)  # (symbol, grid_level) -> {"entry_price": float, "quantity": float}
    open_notional: float = 0.0


class RiskManager:
    """
    All pre-trade and runtime risk checks.
    No trade reaches the exchange without passing through this layer.
    """

    def __init__(self, initial_balance: float | None = None) -> None:
        self.initial_balance = initial_balance or settings.CAPITAL_USDT
        self.state = RiskState(daily_peak_balance=self.initial_balance)
        self.pair_trading_enabled = False  # Gated — set True when pair strategy loads

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def check_trade_allowed(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        current_balance: float,
    ) -> tuple[bool, str]:
        """
        Run all pre-trade validations.
        Returns (allowed: bool, reason: str).
        """
        # Circuit breaker check
        if self.state.circuit_broken:
            if self.state.circuit_break_until:
                now = datetime.now(timezone.utc)
                if now < self.state.circuit_break_until:
                    remaining = (self.state.circuit_break_until - now).seconds
                    return False, f"Circuit breaker active ({remaining}s remaining)"
                # Circuit breaker expired — auto-reset
                self.state.circuit_broken = False
                self.state.circuit_break_until = None
                logger.info("Circuit breaker expired — trading resumed.")
            else:
                return False, "Circuit breaker active (no expiry set)"

        # Max capital per trade (20% default) 
        # Note: We intentionally only block `buy` orders here. Sells are inherently risk-reducing.
        # Blocking a sell would strand inventory. Tradeoff: any future bug generating an oversized
        # sell quantity will not be caught by this check before reaching the exchange.
        trade_cost = quantity * price
        max_allowed = current_balance * settings.MAX_TRADE_PCT
        if side == "buy" and trade_cost > max_allowed:
            return (
                False,
                f"Trade cost ${trade_cost:.2f} exceeds max "
                f"${max_allowed:.2f} ({settings.MAX_TRADE_PCT:.0%} of balance)",
            )

        # Sell sanity check: Ensure sell quantity doesn't significantly exceed total tracked inventory.
        # Note: This check only protects standard grid buy->sell flows via signal_bus. 
        # SL/TP/rebalance liquidations in main.py call trader directly and bypass this.
        if side == "sell":
            tracked_inventory = sum(
                pos["quantity"] for (sym, _), pos in self.state.open_positions.items() if sym == symbol
            )
            # Add a 1% tolerance for fee adjustments and float rounding
            if quantity > (tracked_inventory * 1.01):
                return False, f"Sell quantity {quantity} exceeds tracked inventory {tracked_inventory} (with 1% tolerance)"
            
        # Max portfolio exposure (default 85%)
        if side == "buy" and hasattr(settings, "MAX_PORTFOLIO_EXPOSURE_PCT"):
            max_portfolio_allowed = current_balance * settings.MAX_PORTFOLIO_EXPOSURE_PCT
            if self.state.open_notional + trade_cost > max_portfolio_allowed:
                return (
                    False,
                    f"Portfolio exposure ${self.state.open_notional + trade_cost:.2f} exceeds max "
                    f"${max_portfolio_allowed:.2f} ({settings.MAX_PORTFOLIO_EXPOSURE_PCT:.0%} of balance)",
                )

        # Balance sufficiency
        if side == "buy" and trade_cost > current_balance:
            return False, f"Insufficient balance: need ${trade_cost:.2f}, have ${current_balance:.2f}"
            
        # Increment open notional for the allowed trade
        if side == "buy":
            self.state.open_notional += trade_cost

        return True, "OK"
        
    def decrement_open_notional(self, cost: float) -> None:
        """Decrement open notional when an order fills or is cancelled."""
        self.state.open_notional = max(0.0, self.state.open_notional - cost)

    # ------------------------------------------------------------------
    # Post-trade updates
    # ------------------------------------------------------------------

    def record_trade_result(self, pnl: float) -> None:
        """Update risk state after a completed trade."""
        self.state.daily_pnl += pnl
        self.state.daily_trades += 1

        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

    def evaluate_circuit_breakers(self) -> tuple[bool, str, int]:
        """
        Evaluate all circuit breaker conditions synchronously.
        Returns (triggered, reason, duration_hours).
        """
        # Daily loss limit
        daily_loss_pct = abs(self.state.daily_pnl) / self.initial_balance
        if self.state.daily_pnl < 0 and daily_loss_pct >= settings.DAILY_LOSS_LIMIT_PCT:
            return True, f"Daily loss limit hit: {daily_loss_pct:.2%} (limit: {settings.DAILY_LOSS_LIMIT_PCT:.0%})", 24

        # Consecutive losses
        if self.state.consecutive_losses >= settings.CONSECUTIVE_LOSS_LIMIT:
            return True, f"{self.state.consecutive_losses} consecutive losses", 2

        return False, "", 0

    def trigger_circuit_breaker(self, reason: str, duration_hours: int = 24) -> None:
        """Activate the circuit breaker."""
        self.state.circuit_broken = True
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        self.state.circuit_break_until = now + timedelta(hours=duration_hours)
        logger.critical("CIRCUIT BREAKER: {} — halting for {}h", reason, duration_hours)

    # ------------------------------------------------------------------
    # Stop-loss / Take-profit monitoring
    # ------------------------------------------------------------------

    def check_stop_loss(
        self, symbol: str, current_price: float,
    ) -> list[tuple[int, bool, float]]:
        """
        Check if any open position for the symbol has breached stop-loss.
        Returns a list of (grid_level, triggered, loss_pct).
        """
        results = []
        for (sym, level_id), pos_data in self.state.open_positions.items():
            if sym == symbol:
                entry_price = pos_data["entry_price"]
                loss_pct = (entry_price - current_price) / entry_price
                if loss_pct >= settings.STOP_LOSS_PCT:
                    logger.warning(
                        "STOP-LOSS triggered for {} level {} at {:.2%} (limit {:.2%})",
                        symbol, level_id, loss_pct, settings.STOP_LOSS_PCT,
                    )
                    results.append((level_id, True, loss_pct))
                else:
                    results.append((level_id, False, loss_pct))
        return results

    def check_take_profit(
        self, symbol: str, current_price: float,
    ) -> list[tuple[int, bool, float]]:
        """
        Check if any open position for the symbol has reached take-profit.
        Returns a list of (grid_level, triggered, gain_pct).
        """
        results = []
        for (sym, level_id), pos_data in self.state.open_positions.items():
            if sym == symbol:
                entry_price = pos_data["entry_price"]
                gain_pct = (current_price - entry_price) / entry_price
                if gain_pct >= settings.TAKE_PROFIT_PCT:
                    logger.info("TAKE-PROFIT for {} level {} at {:.2%}", symbol, level_id, gain_pct)
                    results.append((level_id, True, gain_pct))
                else:
                    results.append((level_id, False, gain_pct))
        return results

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def register_position(self, symbol: str, grid_level: int, entry_price: float, quantity: float) -> None:
        """Track a new open position per grid level."""
        self.state.open_positions[(symbol, grid_level)] = {
            "entry_price": entry_price,
            "quantity": quantity
        }

    def close_position(self, symbol: str, grid_level: int) -> None:
        """Remove a closed position from tracking."""
        self.state.open_positions.pop((symbol, grid_level), None)

    # ------------------------------------------------------------------
    # Pair trading risk (gated)
    # ------------------------------------------------------------------

    def check_correlation_decay(self, current_correlation: float) -> bool:
        """
        Check if pair correlation has decayed below threshold.
        Only runs if pair_trading_enabled is True.
        Returns True if the pair trade should be closed.
        """
        if not self.pair_trading_enabled:
            return False
        threshold = 0.75
        if current_correlation < threshold:
            logger.warning(
                "Correlation decay: r={:.3f} < {:.2f} — close pair trade.",
                current_correlation, threshold,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Grid-specific checks
    # ------------------------------------------------------------------

    def check_grid_range_exit(
        self, current_price: float, grid_low: float, grid_high: float,
    ) -> str | None:
        """
        Check if price has exited the grid range.
        Returns 'above', 'below', or None.
        """
        if current_price > grid_high:
            logger.warning(
                "Price {:.4f} exited grid range above {:.4f}.",
                current_price, grid_high,
            )
            return "above"
        if current_price < grid_low:
            logger.warning(
                "Price {:.4f} exited grid range below {:.4f}.",
                current_price, grid_low,
            )
            return "below"
        return None

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def reset_daily(self, new_balance: float) -> None:
        """Reset daily counters — call at midnight UTC."""
        self.state.daily_pnl = 0.0
        self.state.daily_peak_balance = new_balance
        self.state.daily_trades = 0
        # Do NOT reset consecutive_losses — carries across days
        logger.info("Risk state reset for new day. Balance: ${:.2f}", new_balance)

    # ------------------------------------------------------------------
    # Max drawdown tracking
    # ------------------------------------------------------------------

    def update_drawdown(self, current_balance: float) -> float:
        """Update peak balance and return current drawdown percentage."""
        if current_balance > self.state.daily_peak_balance:
            self.state.daily_peak_balance = current_balance
        if self.state.daily_peak_balance > 0:
            drawdown = (
                self.state.daily_peak_balance - current_balance
            ) / self.state.daily_peak_balance
            return drawdown
        return 0.0

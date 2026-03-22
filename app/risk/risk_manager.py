"""
SmartTrade AI Bot — Risk Management Engine
Pre-trade checks, stop-loss monitoring, circuit breakers, position limits.
Pair-trading checks are gated behind a config flag (no circular dependency).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field

from loguru import logger

from app.config import settings
from app.utils.notifier import notify_circuit_breaker, notify_stop_loss


@dataclass
class RiskState:
    """Mutable runtime risk state — reset daily."""
    daily_pnl: float = 0.0
    daily_peak_balance: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    circuit_broken: bool = False
    circuit_break_until: datetime | None = None
    open_positions: dict = field(default_factory=dict)  # symbol -> entry_price


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

        # Max capital per trade (20% default)
        trade_cost = quantity * price
        max_allowed = current_balance * settings.MAX_TRADE_PCT
        if trade_cost > max_allowed:
            return (
                False,
                f"Trade cost ${trade_cost:.2f} exceeds max "
                f"${max_allowed:.2f} ({settings.MAX_TRADE_PCT:.0%} of balance)",
            )

        # Balance sufficiency
        if side == "buy" and trade_cost > current_balance:
            return False, f"Insufficient balance: need ${trade_cost:.2f}, have ${current_balance:.2f}"

        return True, "OK"

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

        # Check circuit breakers after update
        asyncio.ensure_future(self._check_circuit_breakers())

    async def _check_circuit_breakers(self) -> None:
        """Evaluate all circuit breaker conditions."""
        # Daily loss limit
        daily_loss_pct = abs(self.state.daily_pnl) / self.initial_balance
        if self.state.daily_pnl < 0 and daily_loss_pct >= settings.DAILY_LOSS_LIMIT_PCT:
            await self._trigger_circuit_breaker(
                f"Daily loss limit hit: {daily_loss_pct:.2%} "
                f"(limit: {settings.DAILY_LOSS_LIMIT_PCT:.0%})",
                duration_hours=24,
            )
            return

        # Consecutive losses
        if self.state.consecutive_losses >= settings.CONSECUTIVE_LOSS_LIMIT:
            await self._trigger_circuit_breaker(
                f"{self.state.consecutive_losses} consecutive losses",
                duration_hours=2,
            )
            return

    async def _trigger_circuit_breaker(
        self, reason: str, duration_hours: int = 24,
    ) -> None:
        """Activate the circuit breaker and send alert."""
        self.state.circuit_broken = True
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        self.state.circuit_break_until = now + timedelta(hours=duration_hours)
        logger.critical("CIRCUIT BREAKER: {} — halting for {}h", reason, duration_hours)
        await notify_circuit_breaker(reason)

    # ------------------------------------------------------------------
    # Stop-loss / Take-profit monitoring
    # ------------------------------------------------------------------

    def check_stop_loss(
        self, symbol: str, current_price: float,
    ) -> tuple[bool, float]:
        """
        Check if a position has breached stop-loss.
        Returns (triggered: bool, loss_pct: float).
        """
        entry_price = self.state.open_positions.get(symbol)
        if entry_price is None:
            return False, 0.0

        loss_pct = (entry_price - current_price) / entry_price
        if loss_pct >= settings.STOP_LOSS_PCT:
            logger.warning(
                "STOP-LOSS triggered for {} at {:.2%} (limit {:.2%})",
                symbol, loss_pct, settings.STOP_LOSS_PCT,
            )
            asyncio.ensure_future(notify_stop_loss(symbol, loss_pct))
            return True, loss_pct
        return False, loss_pct

    def check_take_profit(
        self, symbol: str, current_price: float,
    ) -> tuple[bool, float]:
        """
        Check if a position has reached take-profit.
        Returns (triggered: bool, gain_pct: float).
        """
        entry_price = self.state.open_positions.get(symbol)
        if entry_price is None:
            return False, 0.0

        gain_pct = (current_price - entry_price) / entry_price
        if gain_pct >= settings.TAKE_PROFIT_PCT:
            logger.info("TAKE-PROFIT for {} at {:.2%}", symbol, gain_pct)
            return True, gain_pct
        return False, gain_pct

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def register_position(self, symbol: str, entry_price: float) -> None:
        """Track a new open position."""
        self.state.open_positions[symbol] = entry_price

    def close_position(self, symbol: str) -> None:
        """Remove a closed position from tracking."""
        self.state.open_positions.pop(symbol, None)

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

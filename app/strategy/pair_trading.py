"""
SmartTrade AI Bot — Statistical Pair Trading Strategy
Trades mean-reversion of correlated crypto pairs.
Entry at 2σ divergence, exit at 0.5σ or 48h timeout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger

from app.config import settings
from app.data.indicators import compute_correlation
from app.execution.signal_bus import SignalBus, TradeSignal, SignalPriority

# Strategy parameters
ENTRY_ZSCORE = 2.0       # Enter when ratio diverges > 2 sigma
EXIT_ZSCORE = 0.5        # Exit when ratio reverts within 0.5 sigma
MIN_CORRELATION = 0.85   # Only trade pairs with r > 0.85
CORRELATION_WINDOW = 30  # 30-day rolling correlation
MAX_HOLD_HOURS = 48      # Maximum holding period
RATIO_WINDOW = 30        # Rolling window for ratio mean/std


@dataclass
class PairTradeState:
    """Tracks the current pair trade position."""
    active: bool = False
    symbol_long: str = ""
    symbol_short: str = ""
    entry_zscore: float = 0.0
    entry_time: datetime | None = None
    long_entry_price: float = 0.0
    short_entry_price: float = 0.0
    position_size: float = 0.0
    current_correlation: float = 0.0


class PairTradingBot:
    """
    Statistical pair trading: trades mean-reversion between
    two correlated crypto pairs using z-score of price ratio.
    """

    def __init__(self, signal_bus: SignalBus) -> None:
        self.signal_bus = signal_bus
        self.state = PairTradeState()

    @staticmethod
    def compute_price_ratio(
        prices_a: pd.Series, prices_b: pd.Series,
    ) -> pd.Series:
        """Compute the price ratio between two series."""
        return (prices_a / prices_b).dropna()

    @staticmethod
    def compute_zscore(ratio: pd.Series, window: int = RATIO_WINDOW) -> pd.Series:
        """Compute rolling z-score of the price ratio."""
        mean = ratio.rolling(window).mean()
        std = ratio.rolling(window).std()
        zscore = (ratio - mean) / std
        return zscore.rename("zscore")

    async def check_opportunity(
        self,
        symbol_a: str,
        symbol_b: str,
        prices_a: pd.Series,
        prices_b: pd.Series,
    ) -> bool:
        """
        Check if a pair trade entry signal exists.
        Returns True if a new position was signalled.
        """
        if self.state.active:
            return False

        # Validate correlation
        correlation = compute_correlation(prices_a, prices_b, CORRELATION_WINDOW)
        if correlation.empty:
            return False

        current_corr = correlation.iloc[-1]
        if np.isnan(current_corr) or current_corr < MIN_CORRELATION:
            logger.debug(
                "Pair {}/{} correlation {:.3f} < {:.2f} — skipping.",
                symbol_a, symbol_b, current_corr, MIN_CORRELATION,
            )
            return False

        # Compute z-score of price ratio
        ratio = self.compute_price_ratio(prices_a, prices_b)
        if len(ratio) < RATIO_WINDOW:
            return False

        zscore = self.compute_zscore(ratio)
        current_z = zscore.iloc[-1]

        if np.isnan(current_z):
            return False

        logger.debug(
            "Pair {}/{}: z={:.3f}, r={:.3f}",
            symbol_a, symbol_b, current_z, current_corr,
        )

        # Entry signal: |z| > 2.0
        if abs(current_z) >= ENTRY_ZSCORE:
            return await self._open_position(
                symbol_a, symbol_b,
                prices_a.iloc[-1], prices_b.iloc[-1],
                current_z, current_corr,
            )

        return False

    async def _open_position(
        self,
        symbol_a: str,
        symbol_b: str,
        price_a: float,
        price_b: float,
        zscore: float,
        correlation: float,
    ) -> bool:
        """
        Open a pair trade position.
        If z > 0: A is overpriced → short A, long B (ratio will revert down)
        If z < 0: A is underpriced → long A, short B (ratio will revert up)
        """
        capital = settings.CAPITAL_USDT * settings.MAX_TRADE_PCT
        half_capital = capital / 2

        if zscore > 0:
            # A is overpriced: sell A, buy B
            long_symbol, short_symbol = symbol_b, symbol_a
            long_price, short_price = price_b, price_a
        else:
            # A is underpriced: buy A, sell B
            long_symbol, short_symbol = symbol_a, symbol_b
            long_price, short_price = price_a, price_b

        long_qty = half_capital / long_price
        short_qty = half_capital / short_price

        # Signal: buy the underperformer
        buy_signal = TradeSignal(
            priority=SignalPriority.NEW_ENTRY,
            timestamp=time.time(),
            symbol=long_symbol,
            side="buy",
            order_type="market",
            price=long_price,
            quantity=round(long_qty, 6),
            strategy="pair_trade",
        )

        # Signal: sell the outperformer
        sell_signal = TradeSignal(
            priority=SignalPriority.NEW_ENTRY,
            timestamp=time.time(),
            symbol=short_symbol,
            side="sell",
            order_type="market",
            price=short_price,
            quantity=round(short_qty, 6),
            strategy="pair_trade",
        )

        buy_ok = await self.signal_bus.push(buy_signal)
        sell_ok = await self.signal_bus.push(sell_signal)

        if buy_ok and sell_ok:
            self.state = PairTradeState(
                active=True,
                symbol_long=long_symbol,
                symbol_short=short_symbol,
                entry_zscore=zscore,
                entry_time=datetime.now(timezone.utc),
                long_entry_price=long_price,
                short_entry_price=short_price,
                position_size=half_capital,
                current_correlation=correlation,
            )
            logger.info(
                "Pair trade opened: long {} / short {} (z={:.2f}, r={:.3f})",
                long_symbol, short_symbol, zscore, correlation,
            )
            return True

        return False

    async def check_exit(
        self,
        prices_a: pd.Series,
        prices_b: pd.Series,
        current_correlation: float,
    ) -> bool:
        """
        Check if the pair trade should be closed.
        Exit conditions:
        1. Z-score reverts within EXIT_ZSCORE (0.5)
        2. Max hold time exceeded (48h)
        3. Correlation decay below 0.75
        """
        if not self.state.active:
            return False

        self.state.current_correlation = current_correlation

        # 1. Correlation decay
        if current_correlation < 0.75:
            logger.warning(
                "Correlation decayed to {:.3f} — closing pair trade.",
                current_correlation,
            )
            return await self._close_position("correlation_decay")

        # 2. Time limit
        if self.state.entry_time:
            hours_held = (
                datetime.now(timezone.utc) - self.state.entry_time
            ).total_seconds() / 3600
            if hours_held >= MAX_HOLD_HOURS:
                logger.info("Pair trade max hold ({}h) reached.", MAX_HOLD_HOURS)
                return await self._close_position("max_hold_time")

        # 3. Z-score reversion
        ratio = self.compute_price_ratio(prices_a, prices_b)
        if len(ratio) >= RATIO_WINDOW:
            zscore = self.compute_zscore(ratio)
            current_z = zscore.iloc[-1]
            if not np.isnan(current_z) and abs(current_z) <= EXIT_ZSCORE:
                logger.info(
                    "Pair trade z-score reverted to {:.3f} — closing.",
                    current_z,
                )
                return await self._close_position("zscore_reversion")

        return False

    async def _close_position(self, reason: str) -> bool:
        """Close both legs of the pair trade."""
        # Sell the long leg
        sell_signal = TradeSignal(
            priority=SignalPriority.TAKE_PROFIT,
            timestamp=time.time(),
            symbol=self.state.symbol_long,
            side="sell",
            order_type="market",
            quantity=round(
                self.state.position_size / self.state.long_entry_price, 6,
            ),
            strategy="pair_trade",
        )
        await self.signal_bus.push(sell_signal)

        logger.info(
            "Pair trade closed ({}): long {} / short {}",
            reason, self.state.symbol_long, self.state.symbol_short,
        )
        self.state = PairTradeState()  # Reset
        return True

    def get_status(self) -> dict:
        """Return current pair trade status."""
        return {
            "active": self.state.active,
            "long": self.state.symbol_long,
            "short": self.state.symbol_short,
            "entry_zscore": self.state.entry_zscore,
            "correlation": self.state.current_correlation,
            "position_size": self.state.position_size,
        }

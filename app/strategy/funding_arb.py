"""
SmartTrade AI Bot — Funding Rate Arbitrage Strategy
Delta-neutral position: long spot + short perp futures.
Collects funding rate payments when rate is strongly positive.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from loguru import logger

from app.config import settings
from app.execution.signal_bus import SignalBus, TradeSignal, SignalPriority
from app.data.fetcher import DataFetcher

# Minimum annualized funding rate to trigger entry (15%)
MIN_ENTRY_RATE_ANNUAL = 0.15
# Rate below which to close the position (5%)
MIN_EXIT_RATE_ANNUAL = 0.05
# Funding is paid every 8 hours (3x/day)
FUNDING_PERIODS_PER_DAY = 3
DAYS_PER_YEAR = 365


@dataclass
class FundingArbState:
    """Tracks the current delta-neutral position."""
    active: bool = False
    spot_symbol: str = ""
    futures_symbol: str = ""
    entry_rate: float = 0.0
    spot_entry_price: float = 0.0
    futures_entry_price: float = 0.0
    position_size: float = 0.0
    funding_collected: float = 0.0
    periods_held: int = 0
    max_periods: int = 9  # 3 days max hold


def annualize_funding_rate(rate_per_period: float) -> float:
    """Convert a single-period funding rate to annualized percentage."""
    return rate_per_period * FUNDING_PERIODS_PER_DAY * DAYS_PER_YEAR


class FundingArbBot:
    """
    Funding rate arbitrage strategy.
    When funding rate is strongly positive, shorts collect payment.
    Delta-neutral: long spot offsets short futures exposure.
    """

    def __init__(
        self, signal_bus: SignalBus, fetcher: DataFetcher,
    ) -> None:
        self.signal_bus = signal_bus
        self.fetcher = fetcher
        self.state = FundingArbState()

    async def check_opportunity(self, symbol: str) -> bool:
        """
        Check if funding rate justifies opening a position.
        Returns True if a new position was signalled.
        Uses MOCK_FUNDING_RATE from config if set (for testnet).
        """
        if self.state.active:
            return False  # Already in a position

        # Use mock rate for testnet (Binance Testnet returns zero rates)
        if settings.MOCK_FUNDING_RATE is not None:
            funding_rate = settings.MOCK_FUNDING_RATE
            logger.info("Using mock funding rate: {:.4%}", funding_rate)
        else:
            rate_info = await self.fetcher.fetch_funding_rate(symbol)
            if not rate_info:
                return False
            funding_rate = rate_info.get("fundingRate", 0)
            if funding_rate is None:
                return False
            funding_rate = float(funding_rate)

        annual_rate = annualize_funding_rate(funding_rate)
        logger.debug(
            "Funding rate for {}: {:.4%} per period ({:.2%} annualized)",
            symbol, funding_rate, annual_rate,
        )

        if annual_rate >= MIN_ENTRY_RATE_ANNUAL:
            logger.info(
                "Funding arb opportunity: {} at {:.2%} annualized",
                symbol, annual_rate,
            )
            return await self._open_position(symbol, funding_rate)

        return False

    async def _open_position(
        self, symbol: str, current_rate: float,
    ) -> bool:
        """Open delta-neutral position: buy spot, short futures."""
        price = await self.fetcher.get_current_price(symbol)
        if not price:
            return False

        # Use max 20% of capital for funding arb
        capital = settings.CAPITAL_USDT * settings.MAX_TRADE_PCT
        quantity = capital / price

        # Signal: buy spot
        buy_signal = TradeSignal(
            priority=SignalPriority.NEW_ENTRY,
            timestamp=time.time(),
            symbol=symbol,
            side="buy",
            order_type="market",
            price=price,
            quantity=round(quantity, 6),
            strategy="funding_arb",
        )

        pushed = await self.signal_bus.push(buy_signal)
        if not pushed:
            return False

        self.state = FundingArbState(
            active=True,
            spot_symbol=symbol,
            futures_symbol=symbol,
            entry_rate=current_rate,
            spot_entry_price=price,
            position_size=quantity,
        )

        logger.info(
            "Funding arb position opened: {} qty={:.6f} rate={:.4%}",
            symbol, quantity, current_rate,
        )
        return True

    async def check_exit(self, symbol: str) -> bool:
        """
        Check if the position should be closed.
        Exit conditions:
        1. Funding rate drops below MIN_EXIT_RATE_ANNUAL
        2. Max holding periods exceeded
        """
        if not self.state.active:
            return False

        self.state.periods_held += 1

        # Check max hold
        if self.state.periods_held >= self.state.max_periods:
            logger.info("Funding arb max hold reached — closing.")
            return await self._close_position("max_periods_reached")

        # Check rate decay
        rate_info = await self.fetcher.fetch_funding_rate(symbol)
        if rate_info:
            current_rate = float(rate_info.get("fundingRate", 0) or 0)
            annual_rate = annualize_funding_rate(current_rate)
            if annual_rate < MIN_EXIT_RATE_ANNUAL:
                logger.info(
                    "Funding rate decayed to {:.2%} — closing position.",
                    annual_rate,
                )
                return await self._close_position("rate_decay")

        return False

    async def _close_position(self, reason: str) -> bool:
        """Close the delta-neutral position."""
        # Signal: sell spot to close
        sell_signal = TradeSignal(
            priority=SignalPriority.TAKE_PROFIT,
            timestamp=time.time(),
            symbol=self.state.spot_symbol,
            side="sell",
            order_type="market",
            quantity=round(self.state.position_size, 6),
            strategy="funding_arb",
        )

        pushed = await self.signal_bus.push(sell_signal)

        logger.info(
            "Funding arb closed ({}): {} periods, collected ${:.4f}",
            reason, self.state.periods_held, self.state.funding_collected,
        )

        self.state = FundingArbState()  # Reset
        return pushed

    def get_status(self) -> dict:
        """Return current funding arb status."""
        return {
            "active": self.state.active,
            "symbol": self.state.spot_symbol,
            "entry_rate": self.state.entry_rate,
            "periods_held": self.state.periods_held,
            "funding_collected": self.state.funding_collected,
            "position_size": self.state.position_size,
        }

"""
SmartTrade AI Bot — Signal Bus
Async priority queue for trade signals with deduplication and cooldown.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum

from loguru import logger


class SignalPriority(IntEnum):
    """Lower number = higher priority."""
    STOP_LOSS = 0
    TAKE_PROFIT = 1
    GRID_ORDER = 2
    NEW_ENTRY = 3


@dataclass(order=True)
class TradeSignal:
    """
    A validated trade signal ready for execution.
    Ordering is by priority (ascending), then by timestamp (FIFO).
    """
    priority: SignalPriority
    timestamp: float = field(compare=True)
    symbol: str = field(compare=False)
    side: str = field(compare=False)         # "buy" or "sell"
    order_type: str = field(compare=False)   # "market" or "limit"
    price: float | None = field(compare=False, default=None)
    quantity: float = field(compare=False, default=0.0)
    strategy: str = field(compare=False, default="grid_bot")
    signal_id: str = field(compare=False, default="")
    grid_level: int | None = field(compare=False, default=None)

    def __post_init__(self) -> None:
        if not self.signal_id:
            self.signal_id = f"{self.symbol}_{self.side}_{self.timestamp}"


class SignalBus:
    """
    Async signal queue with deduplication and cooldown enforcement.
    Producers (strategies) push signals; consumer (execution engine) pops them.
    """

    def __init__(self, cooldown_seconds: int = 60) -> None:
        self._queue: asyncio.PriorityQueue[TradeSignal] = asyncio.PriorityQueue()
        self._recent_signals: dict[str, float] = {}
        self._cooldown = cooldown_seconds
        self._running = True

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def _dedup_key(self, signal: TradeSignal) -> str:
        """Create a deduplication key from symbol + side + strategy + price."""
        return f"{signal.symbol}:{signal.side}:{signal.strategy}:{signal.price}"

    def _is_duplicate(self, signal: TradeSignal) -> bool:
        """Check if an identical signal was sent within the cooldown window."""
        key = self._dedup_key(signal)
        last_time = self._recent_signals.get(key)
        if last_time is None:
            return False
        elapsed = signal.timestamp - last_time
        if elapsed < self._cooldown:
            logger.debug(
                "Duplicate signal suppressed: {} ({}s since last)",
                key, elapsed,
            )
            return True
        return False

    async def push(self, signal: TradeSignal) -> bool:
        """
        Add a signal to the queue.
        Returns False if the signal is a duplicate within the cooldown window.
        """
        if not self._running:
            logger.warning("Signal bus is stopped — rejecting signal.")
            return False

        if self._is_duplicate(signal):
            return False

        # Record for dedup tracking
        key = self._dedup_key(signal)
        self._recent_signals[key] = signal.timestamp
        await self._queue.put(signal)
        logger.info(
            "Signal queued: {} {} {} [priority={}]",
            signal.symbol, signal.side, signal.strategy,
            signal.priority.name,
        )
        return True

    async def pop(self) -> TradeSignal:
        """Block until a signal is available, then return the highest priority."""
        return await self._queue.get()

    def stop(self) -> None:
        """Stop accepting new signals."""
        self._running = False
        logger.info("Signal bus stopped.")

    def start(self) -> None:
        """Resume accepting signals."""
        self._running = True
        logger.info("Signal bus started.")

    def cleanup_stale_dedup(self, max_age: int = 3600) -> None:
        """Remove dedup entries older than max_age seconds."""
        now = time.time()
        stale_keys = [
            k for k, t in self._recent_signals.items()
            if now - t > max_age
        ]
        for k in stale_keys:
            del self._recent_signals[k]

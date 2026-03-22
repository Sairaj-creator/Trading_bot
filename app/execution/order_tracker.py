"""
SmartTrade AI Bot — Order Tracker
Monitors open orders, detects fills and partial fills,
updates trade records in the database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import ccxt.async_support as ccxt
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.notifier import notify_trade
from database.models import Trade, TradeSide, TradeStatus, OrderType, StrategyName


class OrderTracker:
    """
    Polls open orders and reconciles fill status.
    Triggers callback on fill events (e.g., place sell after grid buy fills).
    """

    def __init__(
        self,
        exchange: ccxt.binance,
        poll_interval: float = 5.0,
    ) -> None:
        self.exchange = exchange
        self.poll_interval = poll_interval
        self._tracked_orders: dict[str, dict] = {}  # order_id -> order_info
        self._on_fill_callbacks: list = []
        self._running = False

    def on_fill(self, callback) -> None:
        """Register a callback to be invoked when an order is filled."""
        self._on_fill_callbacks.append(callback)

    def track(
        self,
        order_id: str,
        symbol: str,
        side: str,
        strategy: str = "grid_bot",
        grid_level: int | None = None,
    ) -> None:
        """Start tracking an open order."""
        self._tracked_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "strategy": strategy,
            "grid_level": grid_level,
            "tracked_at": datetime.now(timezone.utc),
        }
        logger.debug("Now tracking order {} ({} {}).", order_id, symbol, side)

    async def check_orders(self) -> list[dict]:
        """
        Poll all tracked orders and detect fills.
        Returns list of newly filled orders.
        """
        filled = []
        to_remove = []

        for order_id, info in self._tracked_orders.items():
            try:
                order = await self.exchange.fetch_order(order_id, info["symbol"])
            except ccxt.BaseError as exc:
                logger.warning("Failed to fetch order {}: {}", order_id, exc)
                continue

            status = order.get("status", "")

            if status == "closed":
                # Fully filled
                filled_order = {
                    "order_id": order_id,
                    "symbol": info["symbol"],
                    "side": info["side"],
                    "strategy": info["strategy"],
                    "grid_level": info.get("grid_level"),
                    "price": float(order.get("average", 0) or order.get("price", 0)),
                    "quantity": float(order.get("filled", 0)),
                    "fee": float(
                        order.get("fee", {}).get("cost", 0)
                        if order.get("fee") else 0,
                    ),
                    "status": "filled",
                }
                filled.append(filled_order)
                to_remove.append(order_id)
                logger.info(
                    "Order FILLED: {} {} {} @ {:.4f}",
                    info["symbol"], info["side"], order_id,
                    filled_order["price"],
                )

                # Send Telegram alert for the filled limit order
                asyncio.create_task(
                    notify_trade(
                        info["symbol"], info["side"], 
                        filled_order["price"], filled_order["quantity"]
                    )
                )

                # Invoke callbacks
                for cb in self._on_fill_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(filled_order)
                        else:
                            cb(filled_order)
                    except Exception as exc:
                        logger.error("Fill callback error: {}", exc)

            elif status == "canceled" or status == "expired":
                to_remove.append(order_id)
                logger.info("Order {} status: {}", order_id, status)

        # Cleanup completed orders
        for oid in to_remove:
            self._tracked_orders.pop(oid, None)

        return filled

    async def record_trade(
        self, session: AsyncSession, filled_order: dict,
    ) -> Trade:
        """Persist a filled order to the database."""
        trade = Trade(
            symbol=filled_order["symbol"],
            side=TradeSide(filled_order["side"]),
            strategy=StrategyName(filled_order["strategy"]),
            order_type=OrderType.limit,
            quantity=filled_order["quantity"],
            price=filled_order["price"],
            fee_usdt=filled_order.get("fee", 0),
            pnl_usdt=filled_order.get("pnl"),
            status=TradeStatus(filled_order.get("status", "filled")),
            binance_order_id=filled_order["order_id"],
        )
        session.add(trade)
        await session.flush()
        logger.debug("Trade recorded: {}", trade)
        return trade

    async def run(self) -> None:
        """Continuous polling loop — run as an asyncio task."""
        self._running = True
        logger.info("Order tracker started (poll={}s).", self.poll_interval)
        while self._running:
            if self._tracked_orders:
                await self.check_orders()
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info("Order tracker stopped.")

    @property
    def active_orders(self) -> int:
        return len(self._tracked_orders)

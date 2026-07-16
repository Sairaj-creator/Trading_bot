"""
SmartTrade AI Bot — Trade Executor
Order placement, retry logic with retryable/fatal error distinction,
fee-aware sizing, and slippage validation.
"""

from __future__ import annotations

import asyncio

import ccxt.async_support as ccxt
from loguru import logger

from app.utils.notifier import notify_error, notify_trade


# Errors that should NEVER be retried — immediate failure
_FATAL_ERRORS = (
    ccxt.InsufficientFunds,
    ccxt.InvalidOrder,
    ccxt.AuthenticationError,
    ccxt.PermissionDenied,
    ccxt.AccountNotEnabled,
    ccxt.ArgumentsRequired,
    ccxt.BadSymbol,
)

# Errors that are safe to retry with backoff
_RETRYABLE_ERRORS = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.ExchangeNotAvailable,
    ccxt.RateLimitExceeded,
    ccxt.DDoSProtection,
)

MAX_RETRIES = 3
MAX_SLIPPAGE_PCT = 0.005  # 0.5%
BINANCE_FEE_PCT = 0.001   # 0.1%


class Trader:
    """
    Executes orders on Binance via ccxt.
    Distinguishes between retryable (network) and fatal (logic) errors.
    """

    def __init__(self, exchange: ccxt.Exchange, on_cancel_cb=None) -> None:
        self.exchange = exchange
        self.on_cancel_cb = on_cancel_cb

    # ------------------------------------------------------------------
    # Fee-aware order sizing
    # ------------------------------------------------------------------

    @staticmethod
    def fee_adjusted_quantity(
        quantity: float, fee_pct: float = BINANCE_FEE_PCT,
    ) -> float:
        """Reduce quantity to account for trading fees."""
        return quantity * (1 - fee_pct)

    @staticmethod
    def fee_adjusted_cost(
        cost_usdt: float, fee_pct: float = BINANCE_FEE_PCT,
    ) -> float:
        """Calculate the effective cost after fees."""
        return cost_usdt * (1 + fee_pct)

    def calculate_order_quantity(
        self, price: float, capital_usdt: float,
    ) -> float:
        """Calculate the max quantity purchasable after accounting for fees."""
        effective_capital = capital_usdt / (1 + BINANCE_FEE_PCT)
        return effective_capital / price if price > 0 else 0.0

    # ------------------------------------------------------------------
    # Order Placement
    # ------------------------------------------------------------------

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        strategy: str = "grid_bot",
    ) -> dict | None:
        """
        Place an order on Binance with retry logic.

        Retryable errors (network, timeout): retry up to MAX_RETRIES with backoff.
        Fatal errors (insufficient funds, invalid order): fail immediately.

        Returns the order response dict or None on failure.
        """
        attempt = 0

        while attempt <= MAX_RETRIES:
            try:
                if order_type == "limit" and price is not None:
                    order = await self.exchange.create_limit_order(
                        symbol, side, quantity, price,
                    )
                else:
                    order = await self.exchange.create_market_order(
                        symbol, side, quantity,
                    )

                fill_price = float(order.get("average", order.get("price", 0)) or 0)
                fill_qty = float(order.get("filled", quantity))

                logger.info(
                    "Order placed: {} {} {} qty={:.6f} price={:.4f}",
                    symbol, side, order_type, quantity, price or fill_price,
                )

                # Slippage check for market orders
                if order_type == "market" and price is not None and fill_price > 0:
                    slippage = abs(fill_price - price) / price
                    if slippage > MAX_SLIPPAGE_PCT:
                        logger.warning(
                            "High slippage detected: {:.2%} (limit {:.2%})",
                            slippage, MAX_SLIPPAGE_PCT,
                        )

                # Fire-and-forget notification ONLY for market orders (instant fills).
                # Limit orders are notified by order_tracker.py when they actually close.
                if order_type == "market":
                    asyncio.create_task(
                        notify_trade(symbol, side, fill_price, fill_qty)
                    )

                return order

            except _FATAL_ERRORS as exc:
                # These errors will never succeed on retry — fail immediately
                logger.error(
                    "FATAL order error (no retry): {} {} {} — {}",
                    symbol, side, order_type, exc,
                )
                asyncio.create_task(
                    notify_error(
                        f"Fatal order error: {symbol} {side} — {type(exc).__name__}: {exc}"
                    )
                )
                return None

            except _RETRYABLE_ERRORS as exc:
                attempt += 1
                wait = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(
                    "Retryable error (attempt {}/{}): {} — retrying in {}s",
                    attempt, MAX_RETRIES, exc, wait,
                )
                if attempt > MAX_RETRIES:
                    logger.error(
                        "Max retries exceeded for {} {} {}.",
                        symbol, side, order_type,
                    )
                    asyncio.create_task(
                        notify_error(
                            f"Order failed after {MAX_RETRIES} retries: "
                            f"{symbol} {side} — {exc}"
                        )
                    )
                    return None
                await asyncio.sleep(wait)

            except Exception as exc:
                # Unexpected error — treat as fatal
                logger.exception("Unexpected error placing order: {}", exc)
                asyncio.create_task(
                    notify_error(f"Unexpected order error: {exc}")
                )
                return None

        return None

    # ------------------------------------------------------------------
    # Order Cancellation
    # ------------------------------------------------------------------

    async def cancel_order(
        self, order_id: str, symbol: str,
    ) -> bool:
        """Cancel an open order. Returns True if successful."""
        try:
            await self.exchange.cancel_order(order_id, symbol)
            logger.info("Cancelled order {} for {}.", order_id, symbol)
            if self.on_cancel_cb:
                if asyncio.iscoroutinefunction(self.on_cancel_cb):
                    await self.on_cancel_cb(order_id)
                else:
                    self.on_cancel_cb(order_id)
            return True
        except ccxt.OrderNotFound:
            logger.info("Order {} already closed/cancelled.", order_id)
            if self.on_cancel_cb:
                if asyncio.iscoroutinefunction(self.on_cancel_cb):
                    await self.on_cancel_cb(order_id)
                else:
                    self.on_cancel_cb(order_id)
            return True
        except _FATAL_ERRORS as exc:
            logger.error("Cannot cancel order {}: {}", order_id, exc)
            return False
        except _RETRYABLE_ERRORS as exc:
            logger.warning("Retry cancel for order {}: {}", order_id, exc)
            try:
                await asyncio.sleep(2)
                await self.exchange.cancel_order(order_id, symbol)
                return True
            except ccxt.BaseError:
                return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all open orders for a symbol. Returns count cancelled."""
        try:
            orders = await self.exchange.fetch_open_orders(symbol)
        except ccxt.BaseError as exc:
            logger.error("Failed to fetch open orders for {}: {}", symbol, exc)
            return 0
            
        cancelled = 0
        for order in orders:
            try:
                if await self.cancel_order(order["id"], symbol):
                    cancelled += 1
            except ccxt.BaseError as exc:
                logger.error("Error cancelling order {}: {}", order["id"], exc)
        logger.info("Cancelled {}/{} orders for {}.", cancelled, len(orders), symbol)
        return cancelled

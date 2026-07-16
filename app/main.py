"""
SmartTrade AI Bot — Main Entry Point
FastAPI application with lifespan handler, APScheduler tasks,
and the core trading event loop.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from app.config import settings
from app.utils.logger import setup_logger
from app.utils.notifier import notify_daily_summary, notify_error, send_alert
from app.data.fetcher import DataFetcher
from app.data.indicators import compute_all_indicators
from app.data.validator import validate_candles
from app.execution.signal_bus import SignalBus, TradeSignal, SignalPriority
from app.execution.trader import Trader
from app.execution.order_tracker import OrderTracker
from app.risk.risk_manager import RiskManager
from app.strategy.grid_bot import GridBot

# ======================================================================
# Global instances — created in lifespan, used by endpoints
# ======================================================================
fetcher: DataFetcher | None = None
grid_bot: GridBot | None = None
risk_manager: RiskManager | None = None
order_tracker: OrderTracker | None = None
trader: Trader | None = None
signal_bus: SignalBus | None = None
scheduler: AsyncIOScheduler | None = None
_start_time: datetime | None = None
_balance_cache = {"value": 0.0, "time": 0.0}

def _handle_cancelled_order(order_id: str) -> None:
    """Release notional for a canceled order tracked by order_tracker."""
    if order_tracker and risk_manager:
        info = order_tracker._tracked_orders.get(order_id)
        if info:
            if info["side"] == "buy":
                risk_manager.decrement_open_notional(info.get("price", 0.0) * info.get("quantity", 0.0))
            order_tracker._tracked_orders.pop(order_id, None)


# ======================================================================
# Lifespan — startup & shutdown
# ======================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all components on startup, clean up on shutdown."""
    global fetcher, grid_bot, risk_manager, order_tracker
    global trader, signal_bus, scheduler, _start_time

    setup_logger()
    logger.info("SmartTrade AI Bot starting (env={})...", settings.ENVIRONMENT)
    _start_time = datetime.now(timezone.utc)

    # Data layer
    fetcher = DataFetcher()
    await fetcher.load_markets()

    # Execution layer
    signal_bus = SignalBus(cooldown_seconds=settings.COOLDOWN_SECONDS)
    trader = Trader(fetcher.exchange, on_cancel_cb=_handle_cancelled_order)
    order_tracker = OrderTracker(fetcher.exchange, poll_interval=30.0, on_cancel_cb=_handle_cancelled_order)
    risk_manager = RiskManager()

    # Strategy
    grid_bot = GridBot(signal_bus)

    # Register grid fill callback
    order_tracker.on_fill(_on_order_filled)

    # Load initial candles
    symbol = settings.TRADING_PAIR
    candles = await fetcher.fetch_historical_candles(symbol)
    if not candles.empty:
        clean = validate_candles(candles)
        indicators = compute_all_indicators(clean)
        logger.info("Initial indicators computed ({} rows).", len(indicators))

        # Initialize grid around current price
        price = await fetcher.get_current_price(symbol)
        if price:
            grid_bot.initialize_grid(symbol, price)
            await grid_bot.generate_initial_orders(price)

    # Start background tasks
    asyncio.create_task(order_tracker.run())
    asyncio.create_task(_signal_consumer_loop())

    # Scheduler — periodic tasks
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_candle_update_job, "interval", seconds=60, id="candle_update")
    scheduler.add_job(_stale_check_job, "interval", seconds=180, id="stale_check")
    scheduler.add_job(_daily_summary_job, "cron", hour=0, minute=30, id="daily_summary")
    scheduler.start()
    logger.info("Scheduler started with {} jobs.", len(scheduler.get_jobs()))

    # Startup Telegram ping — confirm alerts are live before first trade
    ping_ok = await send_alert(
        "\U00002705 <b>SmartTrade AI Bot started</b>\n"
        f"  Env: {settings.ENVIRONMENT}\n"
        f"  Pair: {settings.TRADING_PAIR}\n"
        f"  Capital: ${settings.CAPITAL_USDT}"
    )
    if ping_ok:
        logger.info("Telegram startup ping sent successfully.")
    else:
        logger.warning("Telegram startup ping FAILED — check TELEGRAM_BOT_TOKEN and CHAT_ID.")

    yield  # App is running

    # Shutdown
    logger.info("Shutting down SmartTrade AI Bot...")
    scheduler.shutdown(wait=False)
    order_tracker.stop()
    signal_bus.stop()
    await fetcher.close()
    logger.info("Shutdown complete.")


# ======================================================================
# Background loops
# ======================================================================
async def _signal_consumer_loop() -> None:
    """Process signals from the bus → risk check → execute."""
    global _balance_cache
    while True:
        signal = await signal_bus.pop()
        
        now = time.time()
        if now - _balance_cache["time"] > 5.0:
            _balance_cache["value"] = await fetcher.get_balance()
            _balance_cache["time"] = now
        balance = _balance_cache["value"]

        allowed, reason = risk_manager.check_trade_allowed(
            signal.symbol, signal.side, signal.quantity, signal.price or 0, balance,
        )
        if not allowed:
            logger.warning("Signal rejected: {}", reason)
            continue

        order = await trader.create_order(
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.order_type,
            quantity=signal.quantity,
            price=signal.price,
            strategy=signal.strategy,
        )

        if order and order.get("id"):
            order_tracker.track(
                order_id=order["id"],
                symbol=signal.symbol,
                side=signal.side,
                strategy=signal.strategy,
                grid_level=signal.grid_level,
                price=signal.price or 0.0,
                quantity=signal.quantity,
            )
        else:
            # Order placement failed (None returned)
            if signal.side == "buy":
                risk_manager.decrement_open_notional((signal.price or 0.0) * signal.quantity)


async def _on_order_filled(filled_order: dict) -> None:
    """Route fill events to the correct strategy callback."""
    global _balance_cache
    _balance_cache["time"] = 0.0  # Invalidate cache on any fill/cancel
    
    status = filled_order.get("status", "filled")
    side = filled_order.get("side", "")
    
    # Status canceled/expired are now handled directly via _handle_cancelled_order hook from order_tracker polling
    if status != "filled":
        return
        
    strategy = filled_order.get("strategy", "grid_bot")
    pnl = filled_order.get("pnl", 0)

    if strategy == "grid_bot" and grid_bot:
        if side == "buy":
            await grid_bot.on_buy_filled(filled_order)
            if filled_order.get("grid_level") is not None:
                risk_manager.register_position(filled_order["symbol"], filled_order["grid_level"], filled_order["price"])
        elif side == "sell":
            cycle_pnl = await grid_bot.on_sell_filled(filled_order)
            if cycle_pnl:
                risk_manager.record_trade_result(cycle_pnl)
            if filled_order.get("grid_level") is not None:
                # Decrement open notional based on entry price since position is now closed
                entry_price = risk_manager.state.open_positions.get((filled_order["symbol"], filled_order["grid_level"]))
                if entry_price:
                    risk_manager.decrement_open_notional(entry_price * filled_order.get("quantity", 0.0))
                risk_manager.close_position(filled_order["symbol"], filled_order["grid_level"])


# ======================================================================
# Scheduled jobs
# ======================================================================
async def _candle_update_job() -> None:
    """Fetch latest candle and check grid/risk state."""
    symbol = settings.TRADING_PAIR
    await fetcher.fetch_latest_candle(symbol)
    price = await fetcher.get_current_price(symbol)

    if price and grid_bot and grid_bot.state.active:
        # Check if price exited grid range
        exit_dir = risk_manager.check_grid_range_exit(
            price, grid_bot.state.grid_low, grid_bot.state.grid_high,
        )
        if exit_dir:
            logger.warning("Price exited grid {} — rebalancing.", exit_dir)
            
            # Close stranded inventory before rebalance
            for (sym, level_id), entry_price in list(risk_manager.state.open_positions.items()):
                if sym == symbol:
                    # Calculate original quantity
                    qty = grid_bot.capital_per_grid / entry_price
                    logger.info("Closing stranded position for {} level {} (entry: {:.4f}) at market.", sym, level_id, entry_price)
                    signal = TradeSignal(
                        priority=SignalPriority.STOP_LOSS,
                        timestamp=time.time(),
                        symbol=sym,
                        side="sell",
                        order_type="market",
                        price=price,
                        quantity=round(qty, 6),
                        strategy="grid_bot",
                        grid_level=level_id,
                    )
                    await signal_bus.push(signal)
                    
                    # Synchronously close position and release notional to prevent leaks
                    risk_manager.decrement_open_notional(entry_price * round(qty, 6))
                    risk_manager.close_position(sym, level_id)

            await grid_bot.rebalance(price, trader)

        # Check stop-loss on open positions
        stop_results = risk_manager.check_stop_loss(symbol, price)
        for level_id, triggered, pct in stop_results:
            if triggered and level_id < len(grid_bot.state.levels):
                level = grid_bot.state.levels[level_id]
                qty = grid_bot.capital_per_grid / level.price
                signal = TradeSignal(
                    priority=SignalPriority.STOP_LOSS,
                    timestamp=time.time(),
                    symbol=symbol,
                    side="sell",
                    order_type="market",
                    price=price,
                    quantity=round(qty, 6),
                    strategy="grid_bot",
                    grid_level=level_id,
                )
                await signal_bus.push(signal)
                
                # Synchronously close position and release notional
                entry_price = risk_manager.state.open_positions.get((symbol, level_id))
                if entry_price:
                    risk_manager.decrement_open_notional(entry_price * round(qty, 6))
                risk_manager.close_position(symbol, level_id)


async def _stale_check_job() -> None:
    """Detect stale candle data (silent WebSocket disconnection)."""
    symbol = settings.TRADING_PAIR
    if fetcher.is_stale(symbol):
        logger.warning("Stale data detected — re-fetching candles.")
        await fetcher.fetch_historical_candles(symbol)
        await notify_error(f"Stale data detected for {symbol} — reconnected.")


async def _daily_summary_job() -> None:
    """Send daily P&L summary via Telegram at 00:30 UTC."""
    today = date.today().isoformat()
    balance = await fetcher.get_balance()
    drawdown = risk_manager.update_drawdown(balance)

    await notify_daily_summary(
        date=today,
        trades=risk_manager.state.daily_trades,
        pnl=risk_manager.state.daily_pnl,
        fees=0.0,  # TODO: aggregate from DB
        drawdown=drawdown,
    )

    # Reset daily counters
    risk_manager.reset_daily(balance)


# ======================================================================
# FastAPI App
# ======================================================================
app = FastAPI(
    title="SmartTrade AI Bot",
    version="2.0.0",
    description="Automated crypto trading system — Binance Spot",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Bot health check endpoint."""
    uptime = None
    if _start_time:
        uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()
    return {
        "status": "running",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": uptime,
        "pair": settings.TRADING_PAIR,
        "circuit_breaker": risk_manager.state.circuit_broken if risk_manager else None,
    }


@app.get("/positions")
async def positions():
    """Return all open positions and grid status."""
    grid_status = grid_bot.get_status() if grid_bot else {}
    tracked = order_tracker.active_orders if order_tracker else 0
    return {
        "grid": grid_status,
        "tracked_orders": tracked,
        "open_positions": risk_manager.state.open_positions if risk_manager else {},
    }


@app.get("/daily")
async def daily():
    """Return today's performance metrics."""
    balance = 0.0
    if fetcher:
        balance = await fetcher.get_balance()
    return {
        "date": date.today().isoformat(),
        "trades": risk_manager.state.daily_trades if risk_manager else 0,
        "pnl": risk_manager.state.daily_pnl if risk_manager else 0,
        "balance": balance,
        "circuit_broken": risk_manager.state.circuit_broken if risk_manager else False,
        "consecutive_losses": risk_manager.state.consecutive_losses if risk_manager else 0,
    }


@app.post("/stop")
async def emergency_stop():
    """Emergency halt — stop all trading."""
    logger.critical("EMERGENCY STOP triggered via API.")
    if signal_bus:
        signal_bus.stop()
    if order_tracker:
        order_tracker.stop()
    if trader and grid_bot and grid_bot.state.symbol:
        cancelled = await trader.cancel_all_orders(grid_bot.state.symbol)
        logger.critical("Emergency stop cancelled {} open orders.", cancelled)
    if grid_bot:
        grid_bot.state.active = False
    return {"status": "stopped", "message": "All trading halted."}


# ======================================================================
# Direct execution
# ======================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.FASTAPI_PORT,
        reload=settings.ENVIRONMENT == "development",
    )

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
from app.utils.notifier import notify_daily_summary, notify_error, send_alert, notify_circuit_breaker, notify_stop_loss
from database.session import async_session_factory
import hmac
from fastapi import Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func
from database.models import Trade, TradeStatus
from app.data.fetcher import DataFetcher
from app.data.indicators import compute_all_indicators
from app.data.validator import validate_candles
from app.execution.signal_bus import SignalBus
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

async def _check_and_trigger_circuit_breakers():
    if risk_manager:
        triggered, reason, hours = risk_manager.evaluate_circuit_breakers()
        if triggered:
            risk_manager.trigger_circuit_breaker(reason, hours)
            await notify_circuit_breaker(reason)


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
        
        if not indicators.empty:
            latest = indicators.iloc[-1]
            grid_bot.update_indicators(latest.get("atr"), latest.get("adx"))

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

    if strategy == "grid_bot" and grid_bot:
        if side == "buy":
            await grid_bot.on_buy_filled(filled_order)
            if filled_order.get("grid_level") is not None:
                risk_manager.register_position(
                    filled_order["symbol"], 
                    filled_order["grid_level"], 
                    filled_order["price"], 
                    filled_order["quantity"]
                )
        elif side == "sell":
            cycle_pnl = await grid_bot.on_sell_filled(filled_order)
            if cycle_pnl:
                risk_manager.record_trade_result(cycle_pnl)
                await _check_and_trigger_circuit_breakers()
                filled_order["pnl"] = cycle_pnl
            if filled_order.get("grid_level") is not None:
                # Decrement open notional based on entry price since position is now closed
                pos_data = risk_manager.state.open_positions.get((filled_order["symbol"], filled_order["grid_level"]))
                if pos_data:
                    risk_manager.decrement_open_notional(pos_data["entry_price"] * pos_data["quantity"])
                risk_manager.close_position(filled_order["symbol"], filled_order["grid_level"])

    # Persist to database
    try:
        async with async_session_factory() as session:
            await order_tracker.record_trade(session, filled_order)
            await session.commit()
    except Exception as e:
        logger.error("Database persistence failed for order {}: {}", filled_order.get("order_id"), e)



# ======================================================================
# Scheduled jobs
# ======================================================================
async def _candle_update_job() -> None:
    """Fetch latest candle and check grid/risk state."""
    symbol = settings.TRADING_PAIR
    candles = await fetcher.fetch_latest_candle(symbol)
    price = await fetcher.get_current_price(symbol)
    
    if not candles.empty and grid_bot:
        clean = validate_candles(candles)
        inds = compute_all_indicators(clean)
        if not inds.empty:
            latest = inds.iloc[-1]
            grid_bot.update_indicators(latest.get("atr"), latest.get("adx"))

    if price and grid_bot and grid_bot.state.active:
        # Check if price exited grid range
        exit_dir = risk_manager.check_grid_range_exit(
            price, grid_bot.state.grid_low, grid_bot.state.grid_high,
        )
        if exit_dir:
            logger.warning("Price exited grid {} — rebalancing.", exit_dir)
            
            # Close stranded inventory before rebalance using concurrent awaits
            liquidation_tasks = []
            positions_to_close = []
            
            for (sym, level_id), pos_data in list(risk_manager.state.open_positions.items()):
                if sym == symbol:
                    entry_price = pos_data["entry_price"]
                    qty = pos_data["quantity"]
                    logger.info("Closing stranded position for {} level {} (entry: {:.4f}) at market.", sym, level_id, entry_price)
                    
                    positions_to_close.append((sym, level_id, entry_price, qty))
                    liquidation_tasks.append(trader.create_order(
                        symbol=sym,
                        side="sell",
                        order_type="market",
                        quantity=round(qty, 6),
                        price=price
                    ))
            
            if liquidation_tasks:
                results = await asyncio.gather(*liquidation_tasks)
                liquidation_failed = False
                
                for i, order in enumerate(results):
                    sym, level_id, entry_price, qty = positions_to_close[i]
                    
                    if order and order.get("id"):
                        # Calculate and record PnL
                        fill_price = float(order.get("average") or order.get("price") or price)
                        filled_qty = float(order.get("filled") or qty)
                        fee = float(order.get("fee", {}).get("cost", 0.0) if order.get("fee") else 0.0)
                        
                        pnl = (fill_price - entry_price) * filled_qty - fee
                        risk_manager.record_trade_result(pnl)
                        await _check_and_trigger_circuit_breakers()
                        logger.info("Stranded position {} level {} liquidated. PnL: ${:.4f}", sym, level_id, pnl)
                        
                        filled_order_db = {
                            "order_id": order.get("id", ""),
                            "symbol": sym,
                            "side": "sell",
                            "strategy": "grid_bot",
                            "grid_level": level_id,
                            "price": fill_price,
                            "quantity": filled_qty,
                            "fee": fee,
                            "pnl": pnl,
                            "status": "filled"
                        }
                        try:
                            async with async_session_factory() as session:
                                await order_tracker.record_trade(session, filled_order_db)
                                await session.commit()
                        except Exception as e:
                            logger.error("DB fail on rebalance: {}", e)
                        
                        # Synchronously close position and release notional
                        risk_manager.decrement_open_notional(entry_price * round(qty, 6))
                        risk_manager.close_position(sym, level_id)
                    else:
                        logger.error("Failed to liquidate stranded position for {} level {}", sym, level_id)
                        liquidation_failed = True
                        
                if liquidation_failed:
                    logger.warning("Rebalance aborted due to liquidation failure. Will retry on next update.")
                    return  # Abort rebalance so positions aren't stranded without sell orders
            
            await grid_bot.rebalance(price, trader)

        # Stop-loss check
        sl_results = risk_manager.check_stop_loss(symbol, price)
        sl_positions = [(symbol, level_id) for level_id, triggered, _pct in sl_results if triggered]
        if sl_positions:
            sl_tasks = []
            sl_meta = []
            
            for (sym, level_id) in sl_positions:
                pos_data = risk_manager.state.open_positions.get((sym, level_id))
                if pos_data:
                    entry_price = pos_data["entry_price"]
                    qty = pos_data["quantity"]
                    logger.warning("Stop-loss triggered for {} level {} (entry: {:.4f})", sym, level_id, entry_price)
                    
                    sl_meta.append((sym, level_id, entry_price, qty))
                    sl_tasks.append(trader.create_order(
                        symbol=sym,
                        side="sell",
                        order_type="market",
                        quantity=round(qty, 6),
                        price=price
                    ))
            
            if sl_tasks:
                results = await asyncio.gather(*sl_tasks)
                for i, order in enumerate(results):
                    sym, level_id, entry_price, qty = sl_meta[i]
                    if order and order.get("id"):
                        # Calculate and record PnL
                        fill_price = float(order.get("average") or order.get("price") or price)
                        filled_qty = float(order.get("filled") or qty)
                        fee = float(order.get("fee", {}).get("cost", 0.0) if order.get("fee") else 0.0)
                        
                        pnl = (fill_price - entry_price) * filled_qty - fee
                        risk_manager.record_trade_result(pnl)
                        await _check_and_trigger_circuit_breakers()
                        await notify_stop_loss(f"{sym} level {level_id}", (entry_price - fill_price) / entry_price)
                        
                        filled_order_db = {
                            "order_id": order.get("id", ""),
                            "symbol": sym,
                            "side": "sell",
                            "strategy": "grid_bot",
                            "grid_level": level_id,
                            "price": fill_price,
                            "quantity": filled_qty,
                            "fee": fee,
                            "pnl": pnl,
                            "status": "filled"
                        }
                        try:
                            async with async_session_factory() as session:
                                await order_tracker.record_trade(session, filled_order_db)
                                await session.commit()
                        except Exception as e:
                            logger.error("DB fail on SL: {}", e)
                        
                        risk_manager.decrement_open_notional(entry_price * round(qty, 6))
                        risk_manager.close_position(sym, level_id)
                    else:
                        logger.error("Failed to execute stop-loss for {} level {}", symbol, level_id)

        # Take-profit check
        tp_results = risk_manager.check_take_profit(symbol, price)
        tp_positions = [(symbol, level_id) for level_id, triggered, _pct in tp_results if triggered]
        if tp_positions:
            tp_tasks = []
            tp_meta = []
            
            for (sym, level_id) in tp_positions:
                pos_data = risk_manager.state.open_positions.get((sym, level_id))
                if pos_data:
                    entry_price = pos_data["entry_price"]
                    qty = pos_data["quantity"]
                    
                    # CANCEL existing limit sell for this grid level to prevent double-execution
                    if order_tracker:
                        for oid, info in list(order_tracker._tracked_orders.items()):
                            if info["symbol"] == sym and info["side"] == "sell" and info.get("grid_level") == level_id:
                                try:
                                    await trader.cancel_order(oid, sym)
                                except Exception as e:
                                    logger.error("Failed to cancel TP conflicting limit {}: {}", oid, e)
                                break
                    
                    logger.info("Take-profit triggered for {} level {} (entry: {:.4f})", sym, level_id, entry_price)
                    
                    tp_meta.append((sym, level_id, entry_price, qty))
                    tp_tasks.append(trader.create_order(
                        symbol=sym,
                        side="sell",
                        order_type="market",
                        quantity=round(qty, 6),
                        price=price
                    ))
            
            if tp_tasks:
                results = await asyncio.gather(*tp_tasks)
                for i, order in enumerate(results):
                    sym, level_id, entry_price, qty = tp_meta[i]
                    if order and order.get("id"):
                        fill_price = float(order.get("average") or order.get("price") or price)
                        filled_qty = float(order.get("filled") or qty)
                        fee = float(order.get("fee", {}).get("cost", 0.0) if order.get("fee") else 0.0)
                        
                        pnl = (fill_price - entry_price) * filled_qty - fee
                        risk_manager.record_trade_result(pnl)
                        await _check_and_trigger_circuit_breakers()
                        
                        filled_order_db = {
                            "order_id": order.get("id", ""),
                            "symbol": sym,
                            "side": "sell",
                            "strategy": "grid_bot",
                            "grid_level": level_id,
                            "price": fill_price,
                            "quantity": filled_qty,
                            "fee": fee,
                            "pnl": pnl,
                            "status": "filled"
                        }
                        try:
                            async with async_session_factory() as session:
                                await order_tracker.record_trade(session, filled_order_db)
                                await session.commit()
                        except Exception as e:
                            logger.error("DB fail on TP: {}", e)
                        
                        risk_manager.decrement_open_notional(entry_price * round(qty, 6))
                        risk_manager.close_position(sym, level_id)
                    else:
                        logger.error("Failed to execute take-profit for {} level {}", sym, level_id)


async def _stale_check_job() -> None:
    """Detect stale candle data (silent WebSocket disconnection)."""
    symbol = settings.TRADING_PAIR
    if fetcher.is_stale(symbol):
        logger.warning("Stale data detected — re-fetching candles.")
        await fetcher.fetch_historical_candles(symbol)
        await notify_error(f"Stale data detected for {symbol} — reconnected.")


async def _daily_summary_job() -> None:
    """Send daily P&L summary via Telegram at 00:30 UTC."""
    from datetime import timedelta
    now_utc = datetime.now(timezone.utc)
    start_of_window = now_utc - timedelta(hours=24)
    today = now_utc.date().isoformat()
    
    total_fees = 0.0
    try:
        async with async_session_factory() as session:
            stmt = select(func.sum(Trade.fee_usdt)).where(
                Trade.timestamp >= start_of_window,
                Trade.timestamp < now_utc,
                Trade.status == TradeStatus.filled
            )
            result = await session.execute(stmt)
            total_fees = float(result.scalar() or 0.0)
    except Exception as e:
        logger.error("Failed to aggregate daily fees: {}", e)

    balance = 0.0
    if fetcher:
        balance = await fetcher.get_balance()
        
    drawdown = 0.0
    if risk_manager:
        drawdown = risk_manager.update_drawdown(balance)

    await notify_daily_summary(
        date=today,
        trades=risk_manager.state.daily_trades if risk_manager else 0,
        pnl=risk_manager.state.daily_pnl if risk_manager else 0.0,
        fees=total_fees,
        drawdown=drawdown,
    )

    # Reset daily counters
    if risk_manager:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def verify_dashboard_key(x_api_key: str = Header(..., alias="X-API-Key")):
    if not getattr(settings, "DASHBOARD_API_KEY", None):
        raise HTTPException(status_code=500, detail="DASHBOARD_API_KEY not configured on server")
    if not hmac.compare_digest(x_api_key, settings.DASHBOARD_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return True


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
    open_pos = {}
    if risk_manager:
        for k, v in risk_manager.state.open_positions.items():
            open_pos[str(k)] = v
    return {
        "grid": grid_status,
        "tracked_orders": tracked,
        "open_positions": open_pos,
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


@app.get("/trades")
async def get_trades(limit: int = 50, offset: int = 0):
    """Return paginated trade history."""
    try:
        async with async_session_factory() as session:
            stmt = select(Trade).order_by(Trade.timestamp.desc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            trades = result.scalars().all()
            return {"trades": [{"id": t.id, "order_id": t.order_id, "symbol": t.symbol, "side": t.side, "strategy": t.strategy, "grid_level": t.grid_level, "price": t.price, "quantity": t.quantity, "fee": t.fee, "pnl": t.pnl, "status": t.status, "timestamp": t.timestamp.isoformat()} for t in trades]}
    except Exception as e:
        logger.error("Failed to fetch trades: {}", e)
        return {"trades": []}


@app.get("/grid")
async def get_grid():
    """Detailed grid status for visualization."""
    return {
        "status": grid_bot.get_status() if grid_bot else {},
        "open_positions": risk_manager.state.open_positions if risk_manager else {},
    }


@app.post("/stop")
async def emergency_stop(authorized: bool = Depends(verify_dashboard_key)):
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

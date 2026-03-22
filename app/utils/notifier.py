"""
SmartTrade AI Bot — Telegram Notification Service
Async wrapper for push alerts: trades, stops, errors, daily P&L.
"""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from app.config import settings

# Telegram Bot API base URL
_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def send_alert(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a Telegram message to the configured chat.
    Returns True if sent successfully, False otherwise.
    Silently logs errors — never crashes the bot over a notification failure.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping alert.")
        return False

    url = _BASE_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.debug("Telegram alert sent.")
                return True
            logger.error("Telegram API error: {} {}", resp.status_code, resp.text)
            return False
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.error("Telegram send failed: {}", exc)
        return False


async def notify_trade(
    symbol: str, side: str, price: float, qty: float, pnl: float | None = None,
) -> bool:
    """Format and send a trade execution alert."""
    pnl_str = f"  |  P&L: <b>${pnl:+.4f}</b>" if pnl is not None else ""
    msg = (
        f"🔔 <b>Trade Executed</b>\n"
        f"  {symbol} {side.upper()}\n"
        f"  Price: ${price:.4f}  |  Qty: {qty:.6f}\n"
        f"{pnl_str}"
    )
    return await send_alert(msg)


async def notify_stop_loss(symbol: str, loss_pct: float) -> bool:
    """Alert when a stop-loss is triggered."""
    msg = (
        f"🛑 <b>Stop-Loss Triggered</b>\n"
        f"  {symbol}  |  Loss: {loss_pct:.2%}"
    )
    return await send_alert(msg)


async def notify_circuit_breaker(reason: str) -> bool:
    """Alert when a circuit breaker fires."""
    msg = f"🚨 <b>Circuit Breaker Activated</b>\n  Reason: {reason}"
    return await send_alert(msg)


async def notify_daily_summary(
    date: str, trades: int, pnl: float, fees: float, drawdown: float,
) -> bool:
    """Send the daily P&L summary."""
    msg = (
        f"📊 <b>Daily Summary — {date}</b>\n"
        f"  Trades: {trades}  |  Net P&L: ${pnl:+.4f}\n"
        f"  Fees: ${fees:.4f}  |  Max Drawdown: {drawdown:.2%}"
    )
    return await send_alert(msg)


async def notify_error(error: str) -> bool:
    """Alert on critical system errors."""
    msg = f"❌ <b>System Error</b>\n  {error}"
    return await send_alert(msg)

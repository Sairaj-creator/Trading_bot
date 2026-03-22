"""
SmartTrade AI Bot — Binance Data Fetcher
OHLCV candle fetching (REST + WebSocket), funding rate polling.
Maintains a rolling 200-candle DataFrame in memory.
"""

from __future__ import annotations

from datetime import datetime, timezone

import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger

from app.config import settings

# Rolling window size — enough for EMA-200
ROLLING_WINDOW = 200


class DataFetcher:
    """Fetches and manages market data from Binance via ccxt."""

    def __init__(self) -> None:
        sandbox = settings.ENVIRONMENT == "testnet"
        self.exchange = ccxt.binance({
            "apiKey": settings.BINANCE_API_KEY,
            "secret": settings.BINANCE_SECRET,
            "sandbox": sandbox,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
            },
        })
        self.candles: dict[str, pd.DataFrame] = {}
        self.last_candle_time: dict[str, datetime] = {}
        self._running = False

    async def close(self) -> None:
        """Clean shutdown of the exchange connection."""
        await self.exchange.close()

    async def load_markets(self) -> None:
        """Validate trading pairs on startup."""
        await self.exchange.load_markets()
        logger.info("Markets loaded — {} pairs available.", len(self.exchange.markets))

    # ------------------------------------------------------------------
    # OHLCV Candle Fetching
    # ------------------------------------------------------------------

    async def fetch_historical_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: int = ROLLING_WINDOW,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV candles via REST API.
        Returns a DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        try:
            raw = await self.exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit,
            )
        except ccxt.BaseError as exc:
            logger.error("Failed to fetch candles for {}: {}", symbol, exc)
            return pd.DataFrame()

        if not raw:
            logger.warning("No candle data returned for {}.", symbol)
            return pd.DataFrame()

        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        df = df.astype({"open": float, "high": float, "low": float,
                        "close": float, "volume": float})

        # Store in rolling window cache
        self.candles[symbol] = df.tail(ROLLING_WINDOW)
        self.last_candle_time[symbol] = datetime.now(timezone.utc)
        logger.info(
            "Loaded {} historical candles for {} ({})",
            len(df), symbol, timeframe,
        )
        return self.candles[symbol]

    async def fetch_latest_candle(
        self, symbol: str, timeframe: str = "5m",
    ) -> pd.DataFrame:
        """Fetch the most recent candle and append to rolling window."""
        try:
            raw = await self.exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=2,
            )
        except ccxt.BaseError as exc:
            logger.error("Failed to fetch latest candle for {}: {}", symbol, exc)
            return self.candles.get(symbol, pd.DataFrame())

        if not raw:
            return self.candles.get(symbol, pd.DataFrame())

        new_df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], unit="ms", utc=True)
        new_df = new_df.set_index("timestamp")
        new_df = new_df.astype({"open": float, "high": float, "low": float,
                                "close": float, "volume": float})

        if symbol in self.candles:
            combined = pd.concat([self.candles[symbol], new_df])
            combined = combined[~combined.index.duplicated(keep="last")]
            self.candles[symbol] = combined.tail(ROLLING_WINDOW)
        else:
            self.candles[symbol] = new_df.tail(ROLLING_WINDOW)

        self.last_candle_time[symbol] = datetime.now(timezone.utc)
        return self.candles[symbol]

    def is_stale(
        self, symbol: str, max_age_seconds: int | None = None,
    ) -> bool:
        """
        Check if candle data is stale (no update in max_age_seconds).
        Uses STALE_CANDLE_THRESHOLD_SECONDS from config (default 180s).
        Bump to 300 for testnet where candle gaps are longer.
        """
        if max_age_seconds is None:
            max_age_seconds = settings.STALE_CANDLE_THRESHOLD_SECONDS
        last = self.last_candle_time.get(symbol)
        if last is None:
            return True
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age > max_age_seconds:
            logger.warning(
                "Stale data for {} — last update {:.0f}s ago.", symbol, age,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Ticker / Price
    # ------------------------------------------------------------------

    async def get_current_price(self, symbol: str) -> float | None:
        """Fetch the latest ticker price for a symbol."""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except ccxt.BaseError as exc:
            logger.error("Failed to fetch price for {}: {}", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Funding Rate (Futures)
    # ------------------------------------------------------------------

    async def fetch_funding_rate(self, symbol: str) -> dict | None:
        """
        Fetch current funding rate for a perpetual futures symbol.
        Returns dict with keys: symbol, fundingRate, fundingTimestamp, datetime.
        Returns None if the exchange doesn't support futures or on error.
        """
        try:
            rates = await self.exchange.fetch_funding_rate(symbol)
            logger.debug("Funding rate for {}: {}", symbol, rates.get("fundingRate"))
            return rates
        except (ccxt.BaseError, AttributeError) as exc:
            logger.warning("Funding rate fetch failed for {}: {}", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_balance(self, currency: str = "USDT") -> float:
        """Fetch available balance for a given currency."""
        try:
            balance = await self.exchange.fetch_balance()
            free = balance.get("free", {}).get(currency, 0)
            return float(free)
        except ccxt.BaseError as exc:
            logger.error("Failed to fetch balance: {}", exc)
            return 0.0

"""
SmartTrade AI Bot — Technical Indicator Calculations
RSI, EMA, Bollinger Bands, ATR, rolling correlation.
Uses pandas-ta for computation, pure Python (no C deps).
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from loguru import logger


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Relative Strength Index.
    Returns a Series aligned to the DataFrame index.
    """
    if len(df) < period + 1:
        logger.warning("Not enough data for RSI-{} ({} rows).", period, len(df))
        return pd.Series(dtype=float, index=df.index)
    result = ta.rsi(df["close"], length=period)
    return result.rename("rsi")


def compute_ema(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Compute Exponential Moving Average for a given period."""
    if len(df) < period:
        logger.warning("Not enough data for EMA-{} ({} rows).", period, len(df))
        return pd.Series(dtype=float, index=df.index)
    result = ta.ema(df["close"], length=period)
    return result.rename(f"ema_{period}")


def compute_all_emas(
    df: pd.DataFrame, periods: list[int] | None = None,
) -> pd.DataFrame:
    """Compute multiple EMAs and return as a DataFrame."""
    if periods is None:
        periods = [20, 50, 200]
    emas = {}
    for p in periods:
        emas[f"ema_{p}"] = compute_ema(df, p)
    return pd.DataFrame(emas, index=df.index)


def compute_bollinger(
    df: pd.DataFrame, period: int = 20, std_dev: float = 2.0,
) -> pd.DataFrame:
    """
    Compute Bollinger Bands.
    Returns DataFrame with columns: bb_lower, bb_mid, bb_upper, bb_width.
    """
    if len(df) < period:
        logger.warning("Not enough data for Bollinger-{} ({} rows).", period, len(df))
        return pd.DataFrame(
            columns=["bb_lower", "bb_mid", "bb_upper", "bb_width"],
            index=df.index,
        )
    bbands = ta.bbands(df["close"], length=period, std=std_dev)
    if bbands is None or bbands.empty:
        return pd.DataFrame(
            columns=["bb_lower", "bb_mid", "bb_upper", "bb_width"],
            index=df.index,
        )
    result = pd.DataFrame(index=df.index)
    cols = bbands.columns.tolist()
    result["bb_lower"] = bbands[cols[0]]
    result["bb_mid"] = bbands[cols[1]]
    result["bb_upper"] = bbands[cols[2]]
    # Band width as percentage of mid
    result["bb_width"] = (result["bb_upper"] - result["bb_lower"]) / result["bb_mid"]
    return result


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range — used for volatility-adjusted stop-loss."""
    if len(df) < period + 1:
        logger.warning("Not enough data for ATR-{} ({} rows).", period, len(df))
        return pd.Series(dtype=float, index=df.index)
    result = ta.atr(df["high"], df["low"], df["close"], length=period)
    return result.rename("atr")


def compute_correlation(
    series_a: pd.Series,
    series_b: pd.Series,
    window: int = 30,
) -> pd.Series:
    """
    Compute rolling Pearson correlation between two price series.
    Used by pair trading to validate pair co-movement.
    """
    if len(series_a) < window or len(series_b) < window:
        logger.warning(
            "Not enough data for correlation (need {}, got {}/{}).",
            window, len(series_a), len(series_b),
        )
        return pd.Series(dtype=float)
    return series_a.rolling(window).corr(series_b).rename("correlation")


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convenience: compute RSI-14, EMA-20/50/200, Bollinger, ATR-14
    and merge into a single DataFrame.
    """
    result = df.copy()
    result["rsi"] = compute_rsi(df, 14)
    emas = compute_all_emas(df, [20, 50, 200])
    result = pd.concat([result, emas], axis=1)
    bb = compute_bollinger(df, 20, 2.0)
    result = pd.concat([result, bb], axis=1)
    result["atr"] = compute_atr(df, 14)
    return result

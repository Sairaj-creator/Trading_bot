"""
SmartTrade AI Bot — Candle Data Validator
Rejects anomalous candles: zero volume, price spikes, timestamp gaps.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger


def validate_candles(df: pd.DataFrame, max_spike_pct: float = 0.10) -> pd.DataFrame:
    """
    Validate OHLCV candle data and remove anomalous rows.

    Rules:
    1. Reject candles with zero volume.
    2. Flag candles where close-to-close change exceeds max_spike_pct (10%).
    3. Drop rows with any NaN in OHLCV columns.

    Returns a cleaned DataFrame with invalid candles removed.
    """
    if df.empty:
        return df

    original_len = len(df)
    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(df.columns):
        logger.error(
            "Missing required columns: {}",
            required_cols - set(df.columns),
        )
        return df

    # 1. Drop NaN rows
    clean = df.dropna(subset=list(required_cols))
    nan_dropped = original_len - len(clean)
    if nan_dropped:
        logger.warning("Dropped {} candles with NaN values.", nan_dropped)

    # 2. Drop zero-volume candles
    zero_vol_mask = clean["volume"] <= 0
    zero_vol_count = zero_vol_mask.sum()
    if zero_vol_count:
        logger.warning("Dropped {} zero-volume candles.", zero_vol_count)
        clean = clean[~zero_vol_mask]

    # 3. Flag and drop price spikes
    if len(clean) > 1:
        pct_change = clean["close"].pct_change().abs()
        spike_mask = pct_change > max_spike_pct
        spike_count = spike_mask.sum()
        if spike_count:
            spike_times = clean.index[spike_mask].tolist()
            logger.warning(
                "Dropped {} candles with >{:.0%} price spike at: {}",
                spike_count,
                max_spike_pct,
                spike_times[:5],  # Log first 5
            )
            clean = clean[~spike_mask]

    total_dropped = original_len - len(clean)
    if total_dropped:
        logger.info(
            "Validation complete: {}/{} candles retained ({} dropped).",
            len(clean), original_len, total_dropped,
        )
    return clean


def check_timestamp_continuity(
    df: pd.DataFrame, expected_interval_minutes: int = 5,
) -> list[pd.Timestamp]:
    """
    Check for gaps in candle timestamps.
    Returns a list of timestamps where gaps were detected.
    """
    if len(df) < 2:
        return []

    expected_delta = pd.Timedelta(minutes=expected_interval_minutes)
    time_diffs = df.index.to_series().diff()
    gaps = time_diffs[time_diffs > expected_delta * 1.5]  # 50% tolerance

    if not gaps.empty:
        gap_times = gaps.index.tolist()
        logger.warning(
            "Detected {} timestamp gaps (expected {}min interval): {}",
            len(gap_times),
            expected_interval_minutes,
            gap_times[:5],
        )
        return gap_times
    return []

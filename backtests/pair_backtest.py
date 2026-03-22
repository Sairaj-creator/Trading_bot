"""
SmartTrade AI Bot — Pair Trading Backtest
Statistical pair trading walkforward test with correlation stability analysis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def simulate_pair_trading(
    prices_a: pd.Series,
    prices_b: pd.Series,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    min_correlation: float = 0.85,
    corr_window: int = 30,
    ratio_window: int = 30,
    capital: float = 100.0,
    fee_pct: float = 0.001,
) -> dict:
    """
    Simulate pair trading on two price series.

    Entry: |z-score of price ratio| > entry_z AND correlation > min_correlation
    Exit:  |z-score| < exit_z OR correlation drops below 0.75
    """
    if len(prices_a) != len(prices_b):
        min_len = min(len(prices_a), len(prices_b))
        prices_a = prices_a.iloc[:min_len]
        prices_b = prices_b.iloc[:min_len]

    ratio = prices_a / prices_b
    ratio_mean = ratio.rolling(ratio_window).mean()
    ratio_std = ratio.rolling(ratio_window).std()
    zscore = (ratio - ratio_mean) / ratio_std
    correlation = prices_a.rolling(corr_window).corr(prices_b)

    balance = capital
    position_active = False
    long_symbol = ""
    entry_idx = 0
    trades = []

    for i in range(ratio_window, len(prices_a)):
        z = zscore.iloc[i]
        corr = correlation.iloc[i]

        if np.isnan(z) or np.isnan(corr):
            continue

        if not position_active:
            # Entry check
            if abs(z) >= entry_z and corr >= min_correlation:
                position_active = True
                entry_idx = i
                half = balance * 0.2 / 2  # Use 20% of capital

                if z > 0:  # A overvalued
                    long_symbol = "B"
                    long_price = prices_b.iloc[i]
                    short_price = prices_a.iloc[i]
                else:  # A undervalued
                    long_symbol = "A"
                    long_price = prices_a.iloc[i]
                    short_price = prices_b.iloc[i]

                long_qty = (half / long_price) * (1 - fee_pct)
                short_qty = (half / short_price) * (1 - fee_pct)

        elif position_active:
            # Exit check
            should_exit = (
                abs(z) <= exit_z
                or corr < 0.75
                or (i - entry_idx) > 48 * 12  # 48h in 5-min candles
            )

            if should_exit:
                # Calculate P&L
                if long_symbol == "A":
                    long_exit = prices_a.iloc[i]
                    short_exit = prices_b.iloc[i]
                else:
                    long_exit = prices_b.iloc[i]
                    short_exit = prices_a.iloc[i]

                long_pnl = long_qty * (long_exit - long_price) * (1 - fee_pct)
                short_pnl = short_qty * (short_price - short_exit) * (1 - fee_pct)
                total_pnl = long_pnl + short_pnl

                balance += total_pnl
                trades.append({
                    "entry_idx": entry_idx,
                    "exit_idx": i,
                    "hold_bars": i - entry_idx,
                    "entry_z": zscore.iloc[entry_idx],
                    "exit_z": z,
                    "exit_corr": corr,
                    "pnl": round(total_pnl, 4),
                    "exit_reason": (
                        "zscore_revert" if abs(z) <= exit_z
                        else "corr_decay" if corr < 0.75
                        else "timeout"
                    ),
                })
                position_active = False

    # Metrics
    wins = [t for t in trades if t["pnl"] > 0]
    exit_reasons = {}
    for t in trades:
        r = t["exit_reason"]
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "initial_capital": capital,
        "final_balance": round(balance, 4),
        "total_return_pct": round((balance - capital) / capital * 100, 2),
        "total_trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "total_pnl": round(sum(t["pnl"] for t in trades), 4),
        "avg_pnl": round(
            sum(t["pnl"] for t in trades) / len(trades), 4,
        ) if trades else 0,
        "avg_hold_bars": round(
            sum(t["hold_bars"] for t in trades) / len(trades), 1,
        ) if trades else 0,
        "exit_reasons": exit_reasons,
        "correlation_range": f"{correlation.min():.3f}–{correlation.max():.3f}",
    }


def run_walkforward(
    prices_a: pd.Series, prices_b: pd.Series, train_ratio: float = 0.7,
) -> dict:
    """70/30 walkforward backtest."""
    split = int(len(prices_a) * train_ratio)

    train = simulate_pair_trading(
        prices_a.iloc[:split], prices_b.iloc[:split],
    )
    test = simulate_pair_trading(
        prices_a.iloc[split:], prices_b.iloc[split:],
    )

    return {
        "training": train,
        "validation": test,
        "overfit_check": {
            "train_return": train["total_return_pct"],
            "test_return": test["total_return_pct"],
            "delta": round(train["total_return_pct"] - test["total_return_pct"], 2),
        },
    }


if __name__ == "__main__":
    np.random.seed(42)
    n = 90 * 24 * 12  # 90 days of 5-min candles

    # Simulate two correlated assets
    common = np.random.normal(0, 0.0008, n)
    noise_a = np.random.normal(0, 0.0003, n)
    noise_b = np.random.normal(0, 0.0003, n)

    prices_a = pd.Series(600 * np.exp(np.cumsum(common + noise_a)), name="A")
    prices_b = pd.Series(3500 * np.exp(np.cumsum(common + noise_b)), name="B")

    print("=" * 60)
    print("SmartTrade Pair Trading — 90-Day Backtest")
    print("=" * 60)

    result = run_walkforward(prices_a, prices_b)

    for phase in ["training", "validation"]:
        print(f"\n--- {phase.upper()} ---")
        for k, v in result[phase].items():
            print(f"  {k}: {v}")

    print("\n--- OVERFIT CHECK ---")
    for k, v in result["overfit_check"].items():
        print(f"  {k}: {v}%")

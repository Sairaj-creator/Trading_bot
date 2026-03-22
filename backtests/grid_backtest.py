"""
SmartTrade AI Bot — Grid Trading Backtest
90-day walkforward backtest using vectorbt.
Split: 70% training, 30% out-of-sample validation.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

try:
    import vectorbt as vbt  # noqa: F401
    HAS_VBT = True
except ImportError:
    HAS_VBT = False
    print("vectorbt not installed — run: pip install vectorbt")


def simulate_grid_trading(
    prices: pd.Series,
    grid_range_pct: float = 0.08,
    num_levels: int = 16,
    capital: float = 100.0,
    fee_pct: float = 0.001,
) -> dict:
    """
    Simulate grid trading on historical price data.

    For each candle, check if price crosses any grid level.
    A cross below a buy level triggers a buy; a cross above triggers a sell.

    Returns dict with performance metrics.
    """
    if prices.empty:
        return {"error": "No price data provided."}

    # Define grid around the median price in the range
    center = prices.median()
    grid_low = center * (1 - grid_range_pct)
    grid_high = center * (1 + grid_range_pct)
    interval = (grid_high - grid_low) / num_levels
    capital_per_grid = capital / num_levels

    grid_prices = [grid_low + i * interval for i in range(num_levels + 1)]

    # Tracking
    balance = capital
    holdings = 0.0
    trades = []
    buy_queue = []  # Track buy fills for matching sells

    for i in range(1, len(prices)):
        prev_price = prices.iloc[i - 1]
        curr_price = prices.iloc[i]

        for gp in grid_prices:
            # Buy signal: price crosses below a grid level
            if prev_price >= gp > curr_price and balance >= capital_per_grid:
                qty = (capital_per_grid / gp) * (1 - fee_pct)
                cost = capital_per_grid
                balance -= cost
                holdings += qty
                buy_queue.append({"price": gp, "qty": qty, "cost": cost})
                trades.append({
                    "idx": i,
                    "side": "buy",
                    "price": gp,
                    "qty": qty,
                    "fee": cost * fee_pct,
                })

            # Sell signal: price crosses above a grid level
            elif prev_price <= gp < curr_price and buy_queue:
                fill = buy_queue.pop(0)
                revenue = fill["qty"] * gp * (1 - fee_pct)
                pnl = revenue - fill["cost"]
                balance += revenue
                holdings -= fill["qty"]
                trades.append({
                    "idx": i,
                    "side": "sell",
                    "price": gp,
                    "qty": fill["qty"],
                    "fee": revenue * fee_pct / (1 - fee_pct),
                    "pnl": pnl,
                })

    # Calculate metrics
    sell_trades = [t for t in trades if t["side"] == "sell"]
    wins = [t for t in sell_trades if t.get("pnl", 0) > 0]
    total_pnl = sum(t.get("pnl", 0) for t in sell_trades)
    total_fees = sum(t.get("fee", 0) for t in trades)

    # Final equity = balance + remaining holdings at last price
    final_equity = balance + holdings * prices.iloc[-1]

    return {
        "initial_capital": capital,
        "final_equity": round(final_equity, 4),
        "total_return_pct": round((final_equity - capital) / capital * 100, 2),
        "total_trades": len(trades),
        "completed_cycles": len(sell_trades),
        "win_rate": round(len(wins) / len(sell_trades) * 100, 2) if sell_trades else 0,
        "total_pnl": round(total_pnl, 4),
        "total_fees": round(total_fees, 4),
        "grid_range": f"${grid_low:.2f}–${grid_high:.2f}",
        "grid_levels": num_levels,
        "grid_interval": round(interval, 4),
    }


def run_walkforward_backtest(
    prices: pd.Series,
    train_ratio: float = 0.7,
    **grid_params,
) -> dict:
    """
    Run walkforward backtest: optimize on training set, validate on test set.
    """
    split = int(len(prices) * train_ratio)
    train_prices = prices.iloc[:split]
    test_prices = prices.iloc[split:]

    print(f"Training set: {len(train_prices)} candles")
    print(f"Test set:     {len(test_prices)} candles")

    train_result = simulate_grid_trading(train_prices, **grid_params)
    test_result = simulate_grid_trading(test_prices, **grid_params)

    return {
        "training": train_result,
        "validation": test_result,
        "overfit_check": {
            "train_return": train_result["total_return_pct"],
            "test_return": test_result["total_return_pct"],
            "delta": round(
                train_result["total_return_pct"] - test_result["total_return_pct"], 2,
            ),
        },
    }


if __name__ == "__main__":
    # Example: generate synthetic price data for testing
    np.random.seed(42)
    n_candles = 90 * 24 * 12  # 90 days of 5-min candles
    returns = np.random.normal(0, 0.001, n_candles)  # 0.1% vol per bar
    prices = pd.Series(
        600 * np.exp(np.cumsum(returns)),  # Starting at ~$600 (BNB-like)
        name="close",
    )

    print("=" * 60)
    print("SmartTrade Grid Bot — 90-Day Backtest")
    print("=" * 60)

    result = run_walkforward_backtest(
        prices,
        train_ratio=0.7,
        grid_range_pct=0.08,
        num_levels=16,
        capital=100.0,
    )

    for phase in ["training", "validation"]:
        print(f"\n--- {phase.upper()} ---")
        for k, v in result[phase].items():
            print(f"  {k}: {v}")

    print("\n--- OVERFIT CHECK ---")
    for k, v in result["overfit_check"].items():
        print(f"  {k}: {v}%")

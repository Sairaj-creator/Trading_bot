# SmartTrade AI Bot

Production-grade automated cryptocurrency trading system for **Binance Spot** with **$100 starting capital**.

## Strategies

| Strategy | Edge | Best Market |
|----------|------|-------------|
| **Grid Trading** (Primary) | Mathematical range profit | Sideways / Ranging |
| **Funding Rate Arb** | Structural rate harvest | High funding periods |
| **Stat Pair Trading** | Statistical mean reversion | Correlated pair divergence |

## Quick Start

```bash
# 1. Clone and set up
git clone https://github.com/youruser/smarttrade-bot.git
cd smarttrade-bot
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your Binance API keys and Telegram bot token

# 4. Database setup (requires PostgreSQL)
createdb smarttrade
alembic upgrade head

# 5. Run
python -m app.main
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Bot status, uptime, circuit breaker state |
| `/positions` | GET | Open positions and grid status |
| `/daily` | GET | Today's trades, P&L, balance |
| `/stop` | POST | Emergency halt — closes all trading |

## Architecture

```
Data Layer (ccxt) → Indicators (pandas-ta) → Strategy Engine
    → Signal Bus (asyncio) → Risk Manager → Execution Engine → Binance
```

**Key principle:** The Risk Manager sits between signals and execution. No trade reaches the exchange without passing risk checks.

## Backtesting

```bash
# Grid trading (90-day walkforward)
python backtests/grid_backtest.py

# Pair trading
python backtests/pair_backtest.py
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Deployment (Ubuntu VPS)

```bash
sudo cp smarttrade.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smarttrade
sudo systemctl start smarttrade
journalctl -u smarttrade -f   # Follow logs
```

## Risk Management

- **Max 20%** of capital per trade
- **2% stop-loss** on directional trades
- **5% daily loss** circuit breaker (24h halt)
- **3 consecutive losses** pause (2h)
- Grid auto-rebalance on range exit

## Project Structure

```
app/
├── main.py              # FastAPI + scheduler + event loop
├── config.py            # Pydantic settings from .env
├── strategy/            # Grid bot, funding arb, pair trading
├── execution/           # Signal bus, trader, order tracker
├── risk/                # Circuit breakers, stop-loss
├── data/                # Fetcher, indicators, validator
└── utils/               # Logger, Telegram notifier
database/                # SQLAlchemy models + session
backtests/               # vectorbt walkforward tests
tests/                   # pytest with mocked exchange
```

## License

Private — not for redistribution.

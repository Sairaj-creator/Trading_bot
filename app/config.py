"""
SmartTrade AI Bot — Configuration
Loads all settings from .env with validation.
Fails fast with clear errors if required keys are missing.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Binance API
    BINANCE_API_KEY: str = ""
    BINANCE_SECRET: str = ""

    # Telegram Alerts
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Database
    DB_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/smarttrade"

    # Trading Configuration
    TRADING_PAIR: str = "BNB/USDT"
    CAPITAL_USDT: float = 100.0
    MAX_TRADE_PCT: float = 0.20
    MAX_PORTFOLIO_EXPOSURE_PCT: float = 0.85
    DAILY_LOSS_LIMIT_PCT: float = 0.05
    GRID_LEVELS: int = 16
    GRID_RANGE_PCT: float = 0.08
    STOP_LOSS_PCT: float = 0.02
    TAKE_PROFIT_PCT: float = 0.03
    CONSECUTIVE_LOSS_LIMIT: int = 3
    COOLDOWN_SECONDS: int = 60

    # Testing / Testnet
    MOCK_FUNDING_RATE: float | None = None  # Override for testnet (e.g. 0.0003)
    STALE_CANDLE_THRESHOLD_SECONDS: int = 180  # Bump to 300 for testnet

    # Runtime
    ENVIRONMENT: str = "development"  # development | testnet | production
    LOG_LEVEL: str = "INFO"
    FASTAPI_PORT: int = 8000
    DASHBOARD_API_KEY: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def validate_required_keys(self) -> "Settings":
        """Fail fast if required keys are missing in production/testnet."""
        if self.ENVIRONMENT in ("production", "testnet"):
            missing = []
            if not self.BINANCE_API_KEY:
                missing.append("BINANCE_API_KEY")
            if not self.BINANCE_SECRET:
                missing.append("BINANCE_SECRET")
            if not self.TELEGRAM_BOT_TOKEN:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not self.TELEGRAM_CHAT_ID:
                missing.append("TELEGRAM_CHAT_ID")
            if missing:
                raise ValueError(
                    f"Missing required env vars for {self.ENVIRONMENT}: "
                    f"{', '.join(missing)}. "
                    f"Set them in .env or switch ENVIRONMENT=development."
                )
        if self.CAPITAL_USDT <= 0:
            raise ValueError("CAPITAL_USDT must be positive.")
        if not 0 < self.MAX_TRADE_PCT <= 1:
            raise ValueError("MAX_TRADE_PCT must be between 0 and 1.")
        if not 0 < self.MAX_PORTFOLIO_EXPOSURE_PCT <= 1:
            raise ValueError("MAX_PORTFOLIO_EXPOSURE_PCT must be between 0 and 1.")
        if self.MAX_PORTFOLIO_EXPOSURE_PCT <= self.MAX_TRADE_PCT:
            raise ValueError(f"MAX_PORTFOLIO_EXPOSURE_PCT ({self.MAX_PORTFOLIO_EXPOSURE_PCT}) must be strictly greater than MAX_TRADE_PCT ({self.MAX_TRADE_PCT}).")
        if not 0 < self.STOP_LOSS_PCT < 1:
            raise ValueError("STOP_LOSS_PCT must be between 0 and 1.")
        if not 0 < self.TAKE_PROFIT_PCT < 1:
            raise ValueError("TAKE_PROFIT_PCT must be between 0 and 1.")
        if not 0 < self.GRID_RANGE_PCT < 1:
            raise ValueError("GRID_RANGE_PCT must be between 0 and 1.")
        if not 0 < self.DAILY_LOSS_LIMIT_PCT < 1:
            raise ValueError("DAILY_LOSS_LIMIT_PCT must be between 0 and 1.")
        if self.GRID_LEVELS < 2:
            raise ValueError("GRID_LEVELS must be at least 2.")
        return self

    @property
    def is_live(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testnet(self) -> bool:
        return self.ENVIRONMENT == "testnet"

    @property
    def capital_per_grid(self) -> float:
        return self.CAPITAL_USDT / self.GRID_LEVELS


settings = Settings()

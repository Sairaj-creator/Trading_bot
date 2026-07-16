"""
SmartTrade AI Bot — SQLAlchemy ORM Models
Trade and DailyStat tables for persistent trade history.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all models."""
    pass


class TradeSide(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class OrderType(str, enum.Enum):
    market = "market"
    limit = "limit"


class TradeStatus(str, enum.Enum):
    filled = "filled"
    partial = "partial"
    failed = "failed"


class StrategyName(str, enum.Enum):
    grid_bot = "grid_bot"
    funding_arb = "funding_arb"
    pair_trade = "pair_trade"


class Trade(Base):
    """Every executed order on the exchange."""

    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide), nullable=False)
    strategy: Mapped[StrategyName] = mapped_column(
        Enum(StrategyName), nullable=False,
    )
    order_type: Mapped[OrderType] = mapped_column(
        Enum(OrderType), nullable=False,
    )
    quantity: Mapped[float] = mapped_column(
        Numeric(18, 8), nullable=False,
    )
    price: Mapped[float] = mapped_column(
        Numeric(18, 8), nullable=False,
    )
    fee_usdt: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, default=0,
    )
    pnl_usdt: Mapped[float | None] = mapped_column(
        Numeric(10, 4), nullable=True,
    )
    status: Mapped[TradeStatus] = mapped_column(
        Enum(TradeStatus), nullable=False,
    )
    binance_order_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Trade {self.symbol} {self.side.value} "
            f"{self.quantity}@{self.price} [{self.status.value}]>"
        )


class DailyStat(Base):
    """Aggregated daily performance metrics."""

    __tablename__ = "daily_stats"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl_usdt: Mapped[float] = mapped_column(
        Numeric(10, 4), default=0,
    )
    total_fees_usdt: Mapped[float] = mapped_column(
        Numeric(10, 4), default=0,
    )
    max_drawdown_pct: Mapped[float] = mapped_column(
        Numeric(6, 4), default=0,
    )
    circuit_broken: Mapped[bool] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<DailyStat {self.date} pnl={self.total_pnl_usdt}>"

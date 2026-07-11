from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taiwan_stock_screener.database.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Stock(Base, TimestampMixin):
    __tablename__ = "stocks"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    prices: Mapped[list[DailyPrice]] = relationship(back_populates="stock", cascade="all, delete-orphan")


class DailyPrice(Base, TimestampMixin):
    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("symbol", "trade_date", name="uq_daily_price_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("stocks.symbol"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)

    stock: Mapped[Stock] = relationship(back_populates="prices")


class InstitutionalTrade(Base, TimestampMixin):
    __tablename__ = "institutional_trades"
    __table_args__ = (UniqueConstraint("symbol", "trade_date", name="uq_inst_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("stocks.symbol"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    foreign_buy_sell: Mapped[float] = mapped_column(Float, default=0)
    investment_trust_buy_sell: Mapped[float] = mapped_column(Float, default=0)
    dealer_buy_sell: Mapped[float] = mapped_column(Float, default=0)


class MonthlyRevenue(Base, TimestampMixin):
    __tablename__ = "monthly_revenues"
    __table_args__ = (UniqueConstraint("symbol", "year", "month", name="uq_revenue_symbol_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("stocks.symbol"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    revenue: Mapped[float] = mapped_column(Float)
    revenue_yoy_pct: Mapped[float | None] = mapped_column(Float)


class FinancialStatement(Base, TimestampMixin):
    __tablename__ = "financial_statements"
    __table_args__ = (UniqueConstraint("symbol", "year", "quarter", name="uq_financial_symbol_quarter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("stocks.symbol"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)
    eps: Mapped[float | None] = mapped_column(Float)
    roe_pct: Mapped[float | None] = mapped_column(Float)
    gross_margin_pct: Mapped[float | None] = mapped_column(Float)
    operating_margin_pct: Mapped[float | None] = mapped_column(Float)
    net_margin_pct: Mapped[float | None] = mapped_column(Float)


class ScoreResult(Base, TimestampMixin):
    __tablename__ = "score_results"
    __table_args__ = (UniqueConstraint("symbol", "score_date", name="uq_score_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("stocks.symbol"), index=True)
    score_date: Mapped[date] = mapped_column(Date, index=True)
    total_score: Mapped[float] = mapped_column(Float)
    trend_score: Mapped[float] = mapped_column(Float)
    volume_score: Mapped[float] = mapped_column(Float)
    institutional_score: Mapped[float] = mapped_column(Float)
    chip_score: Mapped[float] = mapped_column(Float)
    fundamental_score: Mapped[float] = mapped_column(Float)
    industry_score: Mapped[float] = mapped_column(Float)
    risk_reward_score: Mapped[float] = mapped_column(Float)
    is_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[str] = mapped_column(Text, default="")
    entry_price: Mapped[float | None] = mapped_column(Float)
    alternate_entry_price: Mapped[float | None] = mapped_column(Float)
    stop_loss_price: Mapped[float | None] = mapped_column(Float)
    target_price_1: Mapped[float | None] = mapped_column(Float)
    target_price_2: Mapped[float | None] = mapped_column(Float)
    risk_reward_ratio: Mapped[float | None] = mapped_column(Float)
    suggested_position_pct: Mapped[float | None] = mapped_column(Float)


class WatchlistItem(Base, TimestampMixin):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    note: Mapped[str | None] = mapped_column(Text)

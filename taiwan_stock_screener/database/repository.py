from __future__ import annotations

from datetime import date

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from taiwan_stock_screener.database.models import (
    DailyPrice,
    FinancialStatement,
    InstitutionalTrade,
    MonthlyRevenue,
    ScoreResult,
    Stock,
    WatchlistItem,
)


class StockRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_stock(self, stock: Stock) -> None:
        existing = self.db.get(Stock, stock.symbol)
        if existing:
            existing.name = stock.name
            existing.market = stock.market
            existing.industry = stock.industry
            existing.is_active = stock.is_active
        else:
            self.db.add(stock)

    def upsert_daily_price(self, price: DailyPrice) -> None:
        existing = self.db.execute(
            select(DailyPrice).where(
                DailyPrice.symbol == price.symbol,
                DailyPrice.trade_date == price.trade_date,
            )
        ).scalar_one_or_none()
        if existing:
            for field in ("open", "high", "low", "close", "volume", "turnover"):
                setattr(existing, field, getattr(price, field))
        else:
            self.db.add(price)

    def upsert_institutional_trade(self, item: InstitutionalTrade) -> None:
        existing = self.db.execute(
            select(InstitutionalTrade).where(
                InstitutionalTrade.symbol == item.symbol,
                InstitutionalTrade.trade_date == item.trade_date,
            )
        ).scalar_one_or_none()
        if existing:
            existing.foreign_buy_sell = item.foreign_buy_sell
            existing.investment_trust_buy_sell = item.investment_trust_buy_sell
            existing.dealer_buy_sell = item.dealer_buy_sell
        else:
            self.db.add(item)

    def upsert_monthly_revenue(self, item: MonthlyRevenue) -> None:
        existing = self.db.execute(
            select(MonthlyRevenue).where(
                MonthlyRevenue.symbol == item.symbol,
                MonthlyRevenue.year == item.year,
                MonthlyRevenue.month == item.month,
            )
        ).scalar_one_or_none()
        if existing:
            existing.revenue = item.revenue
            existing.revenue_yoy_pct = item.revenue_yoy_pct
        else:
            self.db.add(item)

    def upsert_financial_statement(self, item: FinancialStatement) -> None:
        existing = self.db.execute(
            select(FinancialStatement).where(
                FinancialStatement.symbol == item.symbol,
                FinancialStatement.year == item.year,
                FinancialStatement.quarter == item.quarter,
            )
        ).scalar_one_or_none()
        if existing:
            existing.eps = item.eps
            existing.roe_pct = item.roe_pct
            existing.gross_margin_pct = item.gross_margin_pct
            existing.operating_margin_pct = item.operating_margin_pct
            existing.net_margin_pct = item.net_margin_pct
        else:
            self.db.add(item)

    def upsert_score_result(self, item: ScoreResult) -> None:
        existing = self.db.execute(
            select(ScoreResult).where(
                ScoreResult.symbol == item.symbol,
                ScoreResult.score_date == item.score_date,
            )
        ).scalar_one_or_none()
        if existing:
            for field in (
                "total_score",
                "trend_score",
                "volume_score",
                "institutional_score",
                "chip_score",
                "fundamental_score",
                "industry_score",
                "risk_reward_score",
                "is_candidate",
                "reasons",
                "entry_price",
                "alternate_entry_price",
                "stop_loss_price",
                "target_price_1",
                "target_price_2",
                "risk_reward_ratio",
                "suggested_position_pct",
            ):
                setattr(existing, field, getattr(item, field))
        else:
            self.db.add(item)

    def list_stocks(self) -> list[Stock]:
        return list(self.db.execute(select(Stock).order_by(Stock.symbol)).scalars())

    def search_stocks(self, query: str) -> list[Stock]:
        like = f"%{query}%"
        return list(
            self.db.execute(
                select(Stock).where((Stock.symbol.like(like)) | (Stock.name.like(like))).order_by(Stock.symbol)
            ).scalars()
        )

    def latest_prices(self, symbol: str, limit: int = 260) -> list[DailyPrice]:
        return list(
            self.db.execute(
                select(DailyPrice)
                .where(DailyPrice.symbol == symbol)
                .order_by(desc(DailyPrice.trade_date))
                .limit(limit)
            ).scalars()
        )[::-1]

    def latest_score_date(self) -> date | None:
        return self.db.execute(select(ScoreResult.score_date).order_by(desc(ScoreResult.score_date)).limit(1)).scalar()

    def list_candidates(self, limit: int = 20) -> list[ScoreResult]:
        latest = self.latest_score_date()
        if latest is None:
            return []
        return list(
            self.db.execute(
                select(ScoreResult)
                .where(ScoreResult.score_date == latest, ScoreResult.is_candidate.is_(True))
                .order_by(desc(ScoreResult.total_score))
                .limit(limit)
            ).scalars()
        )

    def get_stock(self, symbol: str) -> Stock | None:
        return self.db.get(Stock, symbol)

    def set_watchlist_item(self, user_id: str, symbol: str, note: str | None = None) -> WatchlistItem:
        existing = self.db.execute(
            select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol)
        ).scalar_one_or_none()
        if existing:
            existing.note = note
            return existing
        item = WatchlistItem(user_id=user_id, symbol=symbol, note=note)
        self.db.add(item)
        return item

    def delete_watchlist_item(self, user_id: str, symbol: str) -> None:
        self.db.execute(delete(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol))

    def list_watchlist(self, user_id: str) -> list[WatchlistItem]:
        return list(
            self.db.execute(select(WatchlistItem).where(WatchlistItem.user_id == user_id).order_by(WatchlistItem.symbol))
            .scalars()
        )

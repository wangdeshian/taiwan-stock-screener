from __future__ import annotations

from datetime import date
import logging

import pandas as pd
from sqlalchemy.orm import Session

from taiwan_stock_screener.collectors.sample import (
    sample_daily_prices,
    sample_financials,
    sample_institutional_trades,
    sample_monthly_revenue,
    sample_stocks,
)
from taiwan_stock_screener.config import get_settings
from taiwan_stock_screener.database.models import (
    DailyPrice,
    FinancialStatement,
    InstitutionalTrade,
    MonthlyRevenue,
    ScoreResult,
    Stock,
)
from taiwan_stock_screener.database.repository import StockRepository
from taiwan_stock_screener.database.session import init_db
from taiwan_stock_screener.indicators.technical import add_technical_indicators
from taiwan_stock_screener.scoring.engine import ScoringEngine

logger = logging.getLogger(__name__)


class DailyUpdateService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = StockRepository(db)
        self.scoring = ScoringEngine()
        self.settings = get_settings()

    def run(self) -> dict[str, int | str]:
        init_db()
        logger.info("Starting daily update")
        if self.settings.sample_mode:
            stocks = sample_stocks()
            prices = sample_daily_prices()
            institutional = sample_institutional_trades()
            revenues = sample_monthly_revenue()
            financials = sample_financials()
        else:
            raise NotImplementedError("Set app.sample_mode=true until production collectors are scheduled")

        self._store_reference_data(stocks, prices, institutional, revenues, financials)
        scores = self._calculate_scores(prices, institutional, revenues, financials)
        self.db.commit()
        logger.info("Daily update completed")
        return {
            "status": "ok",
            "stocks": int(stocks["symbol"].nunique()),
            "prices": len(prices),
            "scores": scores,
        }

    def _store_reference_data(
        self,
        stocks: pd.DataFrame,
        prices: pd.DataFrame,
        institutional: pd.DataFrame,
        revenues: pd.DataFrame,
        financials: pd.DataFrame,
    ) -> None:
        for row in stocks.to_dict("records"):
            self.repo.upsert_stock(
                Stock(
                    symbol=str(row["symbol"]),
                    name=str(row["name"]),
                    market=str(row["market"]),
                    industry=row.get("industry"),
                    is_active=True,
                )
            )
        self.db.flush()

        for row in prices.to_dict("records"):
            self.repo.upsert_daily_price(DailyPrice(**row))
        for row in institutional.to_dict("records"):
            self.repo.upsert_institutional_trade(InstitutionalTrade(**row))
        for row in revenues.to_dict("records"):
            self.repo.upsert_monthly_revenue(MonthlyRevenue(**row))
        for row in financials.to_dict("records"):
            self.repo.upsert_financial_statement(FinancialStatement(**row))

    def _calculate_scores(
        self,
        prices: pd.DataFrame,
        institutional: pd.DataFrame,
        revenues: pd.DataFrame,
        financials: pd.DataFrame,
    ) -> int:
        score_date = date.today()
        count = 0
        industry_strength = self._industry_strength(prices)
        for symbol, price_rows in prices.groupby("symbol"):
            indicators = add_technical_indicators(price_rows)
            institutional_rows = institutional[institutional["symbol"] == symbol]
            revenue_rows = revenues[revenues["symbol"] == symbol]
            financial_rows = financials[financials["symbol"] == symbol]
            revenue_row = revenue_rows.iloc[-1] if not revenue_rows.empty else None
            financial_row = financial_rows.iloc[-1] if not financial_rows.empty else None
            industry_rank = industry_strength.get(str(symbol))
            result = self.scoring.score(
                symbol=str(symbol),
                indicators=indicators,
                institutional_rows=institutional_rows,
                revenue_row=revenue_row,
                financial_row=financial_row,
                industry_rank_pct=industry_rank,
            )
            plan = result.trade_plan
            self.repo.upsert_score_result(
                ScoreResult(
                    symbol=str(symbol),
                    score_date=score_date,
                    total_score=result.total_score,
                    trend_score=result.trend_score,
                    volume_score=result.volume_score,
                    institutional_score=result.institutional_score,
                    chip_score=result.chip_score,
                    fundamental_score=result.fundamental_score,
                    industry_score=result.industry_score,
                    risk_reward_score=result.risk_reward_score,
                    is_candidate=result.is_candidate,
                    reasons=",".join(result.reasons),
                    entry_price=plan.entry_price if plan else None,
                    alternate_entry_price=plan.alternate_entry_price if plan else None,
                    stop_loss_price=plan.stop_loss_price if plan else None,
                    target_price_1=plan.target_price_1 if plan else None,
                    target_price_2=plan.target_price_2 if plan else None,
                    risk_reward_ratio=plan.risk_reward_ratio if plan else None,
                    suggested_position_pct=plan.suggested_position_pct if plan else None,
                )
            )
            count += 1
        return count

    def _industry_strength(self, prices: pd.DataFrame) -> dict[str, float]:
        momentum: list[tuple[str, float]] = []
        for symbol, rows in prices.groupby("symbol"):
            sorted_rows = rows.sort_values("trade_date")
            if len(sorted_rows) < 20:
                continue
            latest = float(sorted_rows.iloc[-1]["close"])
            previous = float(sorted_rows.iloc[-20]["close"])
            momentum.append((str(symbol), (latest - previous) / previous if previous else 0))
        if not momentum:
            return {}
        momentum_sorted = sorted(momentum, key=lambda item: item[1])
        denominator = max(len(momentum_sorted) - 1, 1)
        return {symbol: idx / denominator for idx, (symbol, _) in enumerate(momentum_sorted)}

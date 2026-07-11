from __future__ import annotations

from taiwan_stock_screener.collectors.sample import (
    sample_daily_prices,
    sample_financials,
    sample_institutional_trades,
    sample_monthly_revenue,
)
from taiwan_stock_screener.indicators.technical import add_technical_indicators
from taiwan_stock_screener.scoring.engine import ScoringEngine


def test_scoring_engine_returns_candidate_shape() -> None:
    symbol = "2330"
    prices = sample_daily_prices(days=120)
    indicators = add_technical_indicators(prices[prices["symbol"] == symbol])
    institutions = sample_institutional_trades()
    revenues = sample_monthly_revenue()
    financials = sample_financials()
    result = ScoringEngine().score(
        symbol=symbol,
        indicators=indicators,
        institutional_rows=institutions[institutions["symbol"] == symbol],
        revenue_row=revenues[revenues["symbol"] == symbol].iloc[-1],
        financial_row=financials[financials["symbol"] == symbol].iloc[-1],
        industry_rank_pct=1,
    )
    assert 0 <= result.total_score <= 100
    assert result.trade_plan is not None
    assert result.trade_plan.risk_reward_ratio >= 0
    assert result.reasons

from __future__ import annotations

from dataclasses import dataclass, field

from taiwan_stock_screener.strategy.trade_plan import TradePlan


@dataclass(frozen=True)
class ScoreBreakdown:
    symbol: str
    total_score: float
    trend_score: float
    volume_score: float
    institutional_score: float
    chip_score: float
    fundamental_score: float
    industry_score: float
    risk_reward_score: float
    is_candidate: bool
    reasons: list[str] = field(default_factory=list)
    trade_plan: TradePlan | None = None

from __future__ import annotations

import pandas as pd

from taiwan_stock_screener.config import get_settings
from taiwan_stock_screener.scoring.models import ScoreBreakdown
from taiwan_stock_screener.strategy.trade_plan import build_trade_plan


class ScoringEngine:
    def __init__(self) -> None:
        settings = get_settings()
        self.thresholds = settings.raw["thresholds"]
        self.weights = settings.raw["scoring"]

    def score(
        self,
        symbol: str,
        indicators: pd.DataFrame,
        institutional_rows: pd.DataFrame | None = None,
        revenue_row: pd.Series | None = None,
        financial_row: pd.Series | None = None,
        industry_rank_pct: float | None = None,
    ) -> ScoreBreakdown:
        if indicators.empty:
            raise ValueError("Cannot score empty indicator frame")

        latest = indicators.iloc[-1]
        reasons: list[str] = []
        trend = self._trend_score(indicators, reasons)
        volume = self._volume_score(latest, reasons)
        institutional = self._institutional_score(latest, institutional_rows, reasons)
        chip = self._chip_score(indicators, institutional_rows, reasons)
        fundamental = self._fundamental_score(revenue_row, financial_row, reasons)
        industry = self._industry_score(industry_rank_pct, reasons)

        provisional = trend + volume + institutional + chip + fundamental + industry
        plan = build_trade_plan(float(latest["close"]), float(latest.get("atr14", 0) or 0), provisional)
        risk_reward = self._risk_reward_score(plan.risk_reward_ratio, reasons)
        total = min(100.0, trend + volume + institutional + chip + fundamental + industry + risk_reward)
        is_candidate = total >= float(self.thresholds["candidate_score"])
        return ScoreBreakdown(
            symbol=symbol,
            total_score=round(total, 2),
            trend_score=round(trend, 2),
            volume_score=round(volume, 2),
            institutional_score=round(institutional, 2),
            chip_score=round(chip, 2),
            fundamental_score=round(fundamental, 2),
            industry_score=round(industry, 2),
            risk_reward_score=round(risk_reward, 2),
            is_candidate=is_candidate,
            reasons=reasons,
            trade_plan=build_trade_plan(float(latest["close"]), float(latest.get("atr14", 0) or 0), total),
        )

    def _trend_score(self, indicators: pd.DataFrame, reasons: list[str]) -> float:
        latest = indicators.iloc[-1]
        score = 0.0
        weight = float(self.weights["trend"])
        close = float(latest["close"])
        if close > float(latest.get("ma20", 0)):
            score += weight * 0.25
            reasons.append("close_above_ma20")
        if float(latest.get("ma5", 0)) > float(latest.get("ma10", 0)) > float(latest.get("ma20", 0)):
            score += weight * 0.25
            reasons.append("ma_bullish_alignment")
        slope_days = int(self.thresholds["ma20_slope_days"])
        if len(indicators) > slope_days and float(latest.get("ma20", 0)) > float(indicators.iloc[-slope_days]["ma20"]):
            score += weight * 0.2
            reasons.append("ma20_uptrend")
        ma60_slope_days = int(self.thresholds["ma60_slope_days"])
        if len(indicators) > ma60_slope_days and float(latest.get("ma60", 0)) > float(indicators.iloc[-ma60_slope_days]["ma60"]):
            score += weight * 0.15
            reasons.append("ma60_uptrend")
        if float(latest.get("distance_from_60d_high_pct", 100)) < float(self.thresholds["near_high_pct"]):
            score += weight * 0.15
            reasons.append("near_60d_high")
        return min(weight, score)

    def _volume_score(self, latest: pd.Series, reasons: list[str]) -> float:
        score = 0.0
        weight = float(self.weights["volume"])
        if float(latest.get("volume_ratio", 0)) >= float(self.thresholds["volume_ratio"]):
            score += weight * 0.55
            reasons.append("volume_expansion")
        if float(latest.get("turnover", 0)) >= float(self.thresholds["turnover_min_twd"]):
            score += weight * 0.45
            reasons.append("high_turnover")
        return min(weight, score)

    def _institutional_score(
        self,
        latest: pd.Series,
        institutional_rows: pd.DataFrame | None,
        reasons: list[str],
    ) -> float:
        if institutional_rows is None or institutional_rows.empty:
            return 0.0
        recent = institutional_rows.sort_values("trade_date").tail(int(self.thresholds["institutional_days"]))
        foreign_sum = float(recent["foreign_buy_sell"].sum())
        trust_sum = float(recent["investment_trust_buy_sell"].sum())
        net_sum = foreign_sum + trust_sum + float(recent.get("dealer_buy_sell", pd.Series(dtype=float)).sum())
        latest_volume = float(latest.get("volume", 0))
        buy_ratio = net_sum / latest_volume * 100 if latest_volume else 0
        score = 0.0
        weight = float(self.weights["institutional"])
        if foreign_sum > 0 or trust_sum > 0:
            score += weight * 0.55
            reasons.append("institutional_net_buying")
        if buy_ratio >= float(self.thresholds["institutional_buy_ratio_pct"]):
            score += weight * 0.45
            reasons.append("institutional_buy_ratio")
        return min(weight, score)

    def _chip_score(
        self,
        indicators: pd.DataFrame,
        institutional_rows: pd.DataFrame | None,
        reasons: list[str],
    ) -> float:
        score = 0.0
        weight = float(self.weights["chip"])
        latest = indicators.iloc[-1]
        if float(latest.get("obv", 0)) > float(indicators["obv"].tail(20).mean()):
            score += weight * 0.5
            reasons.append("obv_accumulation")
        if institutional_rows is not None and not institutional_rows.empty:
            recent = institutional_rows.sort_values("trade_date").tail(5)
            if float(recent["foreign_buy_sell"].sum() + recent["investment_trust_buy_sell"].sum()) > 0:
                score += weight * 0.5
                reasons.append("chip_support")
        return min(weight, score)

    def _fundamental_score(
        self,
        revenue_row: pd.Series | None,
        financial_row: pd.Series | None,
        reasons: list[str],
    ) -> float:
        score = 0.0
        weight = float(self.weights["fundamental"])
        if revenue_row is not None and float(revenue_row.get("revenue_yoy_pct") or 0) > float(self.thresholds["revenue_yoy_pct"]):
            score += weight * 0.35
            reasons.append("revenue_yoy_growth")
        if financial_row is not None and float(financial_row.get("eps") or 0) > float(self.thresholds["eps_min"]):
            score += weight * 0.3
            reasons.append("positive_eps")
        if financial_row is not None and float(financial_row.get("roe_pct") or 0) > float(self.thresholds["roe_min_pct"]):
            score += weight * 0.35
            reasons.append("healthy_roe")
        return min(weight, score)

    def _industry_score(self, industry_rank_pct: float | None, reasons: list[str]) -> float:
        weight = float(self.weights["industry"])
        if industry_rank_pct is None:
            return weight * 0.5
        if industry_rank_pct >= 0.7:
            reasons.append("strong_industry_rotation")
            return weight
        if industry_rank_pct >= 0.4:
            return weight * 0.5
        return 0.0

    def _risk_reward_score(self, rr: float, reasons: list[str]) -> float:
        weight = float(self.weights["risk_reward"])
        if rr >= float(self.thresholds["risk_reward_min"]):
            reasons.append("risk_reward_above_min")
            return weight
        return max(0.0, weight * rr / float(self.thresholds["risk_reward_min"]))

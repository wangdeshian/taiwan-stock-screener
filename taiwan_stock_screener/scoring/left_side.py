from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from taiwan_stock_screener.config import get_settings
from taiwan_stock_screener.strategy.trade_plan import TradePlan, build_trade_plan


@dataclass(frozen=True)
class LeftSideScoreBreakdown:
    symbol: str
    total_score: float
    base_structure_score: float
    short_covering_score: float
    retail_capitulation_score: float
    smart_money_score: float
    fundamental_safety_score: float
    catalyst_score: float
    sector_resonance_score: float
    sentiment_score: float
    ignition_score: float
    is_candidate: bool
    reasons: list[str] = field(default_factory=list)
    trade_plan: TradePlan | None = None
    bb_bandwidth_percentile: float | None = None


class LeftSideScoringEngine:
    """左側潛伏策略評分。

    與右側動能策略相反：尋找股價處於低基期、波動收斂、空單回補、
    散戶絕望（融資大減、當沖冷清、量能萎縮）、而大戶/內部人默默吸籌的股票。
    """

    def __init__(self) -> None:
        config = get_settings().raw["left_side"]
        self.thresholds = config["thresholds"]
        self.weights = config["weights"]
        self.candidate_score = float(config["candidate_score"])

    def score(
        self,
        symbol: str,
        indicators: pd.DataFrame,
        chip_rows: pd.DataFrame | None = None,
        holder_rows: pd.DataFrame | None = None,
        institutional_rows: pd.DataFrame | None = None,
        revenue_row: pd.Series | None = None,
        financial_row: pd.Series | None = None,
        catalyst_row: dict | pd.Series | None = None,
        sector_row: dict | pd.Series | None = None,
        broker_row: dict | pd.Series | None = None,
        sentiment_ratio: float | None = None,
    ) -> LeftSideScoreBreakdown:
        if indicators.empty:
            raise ValueError("Cannot score empty indicator frame")

        indicators = indicators.sort_values("trade_date")
        latest = indicators.iloc[-1]
        reasons: list[str] = []

        base_structure = self._base_structure_score(indicators, reasons)
        short_covering = self._short_covering_score(chip_rows, reasons)
        retail_capitulation = self._retail_capitulation_score(latest, chip_rows, reasons)
        smart_money = self._smart_money_score(holder_rows, institutional_rows, latest, reasons, broker_row)
        fundamental_safety = self._fundamental_safety_score(revenue_row, financial_row, reasons)
        catalyst = self._catalyst_score(catalyst_row, reasons)
        sector_resonance = self._sector_resonance_score(sector_row, reasons)
        sentiment = self._sentiment_score(sentiment_ratio, reasons)
        bandwidth_percentile = self._bandwidth_percentile(indicators)
        ignition = self._ignition_score(indicators, bandwidth_percentile, reasons)

        total = min(
            100.0,
            base_structure
            + short_covering
            + retail_capitulation
            + smart_money
            + fundamental_safety
            + catalyst
            + sector_resonance
            + ignition,
        )
        plan = build_trade_plan(float(latest["close"]), float(latest.get("atr14", 0) or 0), total)
        return LeftSideScoreBreakdown(
            symbol=symbol,
            total_score=round(total, 2),
            base_structure_score=round(base_structure, 2),
            short_covering_score=round(short_covering, 2),
            retail_capitulation_score=round(retail_capitulation, 2),
            smart_money_score=round(smart_money, 2),
            fundamental_safety_score=round(fundamental_safety, 2),
            catalyst_score=round(catalyst, 2),
            sector_resonance_score=round(sector_resonance, 2),
            sentiment_score=round(sentiment, 2),
            ignition_score=round(ignition, 2),
            is_candidate=total >= self.candidate_score,
            reasons=reasons,
            trade_plan=plan,
            bb_bandwidth_percentile=None if bandwidth_percentile is None else round(bandwidth_percentile, 1),
        )

    def _base_structure_score(self, indicators: pd.DataFrame, reasons: list[str]) -> float:
        weight = float(self.weights["base_structure"])
        latest = indicators.iloc[-1]
        score = 0.0

        close = float(latest["close"])
        high_window = indicators["high"].astype(float).tail(240)
        year_high = float(high_window.max())
        if year_high > 0:
            distance_pct = (year_high - close) / year_high * 100
            if distance_pct >= float(self.thresholds["low_base_from_high_pct"]):
                score += weight * 0.4
                reasons.append("low_base")

        percentile = self._bandwidth_percentile(indicators)
        if percentile is not None and percentile <= float(self.thresholds["bb_squeeze_percentile"]):
            score += weight * 0.35
            reasons.append("bollinger_squeeze")

        ma_days = int(self.thresholds["stabilize_ma_days"])
        ma20 = float(latest.get("ma20", 0) or 0)
        if len(indicators) > ma_days and ma20 > 0:
            past_ma20 = float(indicators.iloc[-ma_days].get("ma20", 0) or 0)
            if close >= ma20 or (past_ma20 > 0 and ma20 >= past_ma20 * 0.995):
                score += weight * 0.25
                reasons.append("price_stabilizing")

        return min(weight, score)

    def _short_covering_score(self, chip_rows: pd.DataFrame | None, reasons: list[str]) -> float:
        weight = float(self.weights["short_covering"])
        series = self._chip_series(chip_rows, "short_balance")
        if series is None:
            return 0.0
        lookback = int(self.thresholds["short_balance_lookback_days"])
        window = series.tail(lookback)
        if len(window) < 2:
            return 0.0
        start = float(window.iloc[0])
        end = float(window.iloc[-1])
        if start <= 0:
            return 0.0
        drop_pct = (start - end) / start * 100
        required = float(self.thresholds["short_balance_drop_pct"])
        score = 0.0
        if drop_pct >= required:
            reasons.append("short_covering")
            score = weight
        elif drop_pct > 0:
            score = weight * min(1.0, drop_pct / required) * 0.5

        # 券資比軋空訊號：融券/融資 ≥ 門檻且空單正在回補 → 軋空燃料充足
        if drop_pct > 0:
            ratio = self._short_margin_ratio(chip_rows)
            if ratio is not None and ratio >= float(self.thresholds["short_margin_ratio_squeeze_pct"]):
                reasons.append("short_squeeze_setup")
                score += weight * 0.3
        return min(weight, score)

    def _short_margin_ratio(self, chip_rows: pd.DataFrame | None) -> float | None:
        """券資比（%）＝最新融券餘額 / 最新融資餘額。"""
        shorts = self._chip_series(chip_rows, "margin_short_balance")
        margins = self._chip_series(chip_rows, "margin_balance")
        if shorts is None or margins is None:
            return None
        margin = float(margins.iloc[-1])
        if margin <= 0:
            return None
        return float(shorts.iloc[-1]) / margin * 100

    def _retail_capitulation_score(
        self,
        latest: pd.Series,
        chip_rows: pd.DataFrame | None,
        reasons: list[str],
    ) -> float:
        weight = float(self.weights["retail_capitulation"])
        score = 0.0

        margin = self._chip_series(chip_rows, "margin_balance")
        if margin is not None:
            lookback = int(self.thresholds["margin_lookback_days"])
            window = margin.tail(lookback)
            if len(window) >= 2 and float(window.iloc[0]) > 0:
                drop_pct = (float(window.iloc[0]) - float(window.iloc[-1])) / float(window.iloc[0]) * 100
                if drop_pct >= float(self.thresholds["margin_drop_pct"]):
                    score += weight * 0.45
                    reasons.append("margin_flush")

        day_trade = self._chip_series(chip_rows, "day_trade_ratio_pct")
        if day_trade is not None and len(day_trade) > 0:
            recent_ratio = float(day_trade.tail(5).mean())
            if recent_ratio <= float(self.thresholds["day_trade_ratio_max_pct"]):
                score += weight * 0.3
                reasons.append("day_trade_freeze")

        if float(latest.get("volume_ratio", 1) or 1) <= float(self.thresholds["volume_dryup_ratio"]):
            score += weight * 0.25
            reasons.append("volume_dryup")

        return min(weight, score)

    def _smart_money_score(
        self,
        holder_rows: pd.DataFrame | None,
        institutional_rows: pd.DataFrame | None,
        latest: pd.Series,
        reasons: list[str],
        broker_row: dict | pd.Series | None = None,
    ) -> float:
        weight = float(self.weights["smart_money"])
        score = 0.0

        # 分點資金流（FinMind Sponsor）：主力吸貨補位加分、隔日沖紊亂只警示不給分
        if broker_row is not None:
            stage = str(self._get_value(broker_row, "chip_stage") or "")
            if stage == "accumulation":
                score += weight * 0.3
                reasons.append("branch_concentration")
            elif stage == "churn":
                reasons.append("day_trade_branch_churn")

        if holder_rows is not None and not holder_rows.empty and "big_holder_ratio_pct" in holder_rows.columns:
            sorted_rows = holder_rows.sort_values("date")
            window = sorted_rows.tail(int(self.thresholds["holder_lookback_weeks"]))
            if len(window) >= 2:
                gain = float(window.iloc[-1]["big_holder_ratio_pct"]) - float(window.iloc[0]["big_holder_ratio_pct"])
                if gain >= float(self.thresholds["holder_ratio_gain_pp"]):
                    score += weight * 0.6
                    reasons.append("big_holder_accumulation")

        if institutional_rows is not None and not institutional_rows.empty:
            recent = institutional_rows.sort_values("trade_date").tail(int(self.thresholds["trust_days"]))
            trust_sum = float(recent["investment_trust_buy_sell"].sum())
            volume = float(latest.get("volume", 0) or 0)
            # 投信「微幅」買超：有買但未引起市場注意（佔量比低）
            if trust_sum > 0 and (volume <= 0 or trust_sum / (volume * len(recent)) * 100 < 1):
                score += weight * 0.25
                reasons.append("trust_light_buying")

            # 投信近 N 日內出現連續 M 日買超（默默連續吸籌）
            window = recent.tail(int(self.thresholds["trust_streak_window"]))
            streak_target = int(self.thresholds["trust_streak_days"])
            streak = 0
            has_streak = False
            for value in window["investment_trust_buy_sell"]:
                streak = streak + 1 if float(value) > 0 else 0
                if streak >= streak_target:
                    has_streak = True
                    break
            if has_streak:
                score += weight * 0.15
                reasons.append("trust_streak_buying")

        return min(weight, score)

    def _fundamental_safety_score(
        self,
        revenue_row: pd.Series | None,
        financial_row: pd.Series | None,
        reasons: list[str],
    ) -> float:
        weight = float(self.weights["fundamental_safety"])
        score = 0.0
        if financial_row is not None and float(financial_row.get("eps") or 0) > 0:
            score += weight * 0.6
            reasons.append("still_profitable")
        if revenue_row is not None:
            yoy = revenue_row.get("revenue_yoy_pct")
            if yoy is not None and float(yoy) >= float(self.thresholds["revenue_yoy_floor_pct"]):
                score += weight * 0.4
                reasons.append("revenue_not_collapsing")
        return min(weight, score)

    def _catalyst_score(self, catalyst_row: dict | pd.Series | None, reasons: list[str]) -> float:
        weight = float(self.weights["catalyst"])
        if catalyst_row is None:
            return 0.0
        days_left = self._get_value(catalyst_row, "catalyst_days_left")
        in_window = bool(self._get_value(catalyst_row, "catalyst_in_window"))
        if days_left is None or not in_window:
            return 0.0

        lookahead = max(1.0, float(self.thresholds["catalyst_lookahead_trading_days"]))
        days = max(0.0, float(days_left))
        score = weight * max(0.0, (lookahead - days + 1) / (lookahead + 1))
        reasons.append("near_catalyst")
        return min(weight, score)

    def _sector_resonance_score(self, sector_row: dict | pd.Series | None, reasons: list[str]) -> float:
        weight = float(self.weights["sector_resonance"])
        if sector_row is None:
            return 0.0
        score = 0.0
        rank_pct = self._get_value(sector_row, "sector_turnover_rank_pct")
        jump_pct = self._get_value(sector_row, "sector_turnover_jump_pct")
        if rank_pct is not None and float(rank_pct) <= float(self.thresholds["sector_rank_threshold_pct"]):
            score += weight * 0.6
            reasons.append("sector_turnover_leader")
        if jump_pct is not None and float(jump_pct) >= float(self.thresholds["sector_turnover_jump_pct"]):
            score += weight * 0.4
            reasons.append("sector_turnover_jump")
        return min(weight, score)

    def _bandwidth_percentile(self, indicators: pd.DataFrame) -> float | None:
        """今日布林帶寬在近 N 日中的百分位（越低代表壓縮越極端）。"""
        bandwidth = self._bollinger_bandwidth(indicators)
        if bandwidth is None:
            return None
        lookback = int(self.thresholds["bb_squeeze_lookback_days"])
        recent = bandwidth.tail(lookback).dropna()
        if len(recent) < 20:
            return None
        return float((recent <= recent.iloc[-1]).mean() * 100)

    def _ignition_score(
        self,
        indicators: pd.DataFrame,
        bandwidth_percentile: float | None,
        reasons: list[str],
    ) -> float:
        """壓縮點火構面：極度壓縮＋溫和放量＋收紅站上月線（起漲點訊號）。"""
        weight = float(self.weights["ignition"])
        latest = indicators.iloc[-1]
        score = 0.0

        if bandwidth_percentile is not None and bandwidth_percentile <= float(
            self.thresholds["bb_squeeze_extreme_percentile"]
        ):
            score += weight * 0.4
            reasons.append("bollinger_squeeze_extreme")

        # 溫和點火：今日量是前 N 日均量的 1.5~3 倍（排除已噴出的爆量股）
        avg_days = int(self.thresholds["ignition_volume_avg_days"])
        volumes = indicators["volume"].astype(float)
        if len(volumes) > avg_days:
            prior_avg = float(volumes.iloc[-(avg_days + 1):-1].mean())
            if prior_avg > 0:
                ratio = float(volumes.iloc[-1]) / prior_avg
                min_ratio = float(self.thresholds["ignition_volume_min_ratio"])
                max_ratio = float(self.thresholds["ignition_volume_max_ratio"])
                if min_ratio <= ratio < max_ratio:
                    score += weight * 0.35
                    reasons.append("mild_ignition")

        close = float(latest["close"])
        if close > float(latest.get("ma20", 0) or 0) and close > float(latest.get("open", close) or close):
            score += weight * 0.25
            reasons.append("bullish_red_candle")

        return min(weight, score)

    def _sentiment_score(self, sentiment_ratio: float | None, reasons: list[str]) -> float:
        # 網路聲量情緒為預留欄位：sentiment_ratio 介於 0（聲量冰點）到 1（過熱）。
        # 尚未串接 PTT/論壇爬蟲時傳入 None，此構面得 0 分。
        weight = float(self.weights["sentiment"])
        if sentiment_ratio is None:
            return 0.0
        ratio = min(1.0, max(0.0, float(sentiment_ratio)))
        score = weight * (1.0 - ratio)
        if ratio <= 0.2:
            reasons.append("sentiment_freeze")
        return score

    @staticmethod
    def _get_value(row: dict | pd.Series, key: str) -> object:
        if isinstance(row, pd.Series):
            return row.get(key)
        return row.get(key)

    @staticmethod
    def _chip_series(chip_rows: pd.DataFrame | None, column: str) -> pd.Series | None:
        if chip_rows is None or chip_rows.empty or column not in chip_rows.columns:
            return None
        series = chip_rows.sort_values("trade_date")[column].dropna()
        return series if not series.empty else None

    @staticmethod
    def _bollinger_bandwidth(indicators: pd.DataFrame) -> pd.Series | None:
        required = {"bb_upper", "bb_lower", "bb_middle"}
        if not required.issubset(indicators.columns):
            return None
        middle = indicators["bb_middle"].astype(float)
        middle = middle.where(middle != 0)
        return (indicators["bb_upper"].astype(float) - indicators["bb_lower"].astype(float)) / middle

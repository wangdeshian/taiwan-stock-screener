from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from taiwan_stock_screener.indicators.technical import add_technical_indicators
from taiwan_stock_screener.scoring.left_side import LeftSideScoringEngine


def _trading_dates(days: int) -> list[date]:
    start = date.today() - timedelta(days=days * 2)
    dates = [start + timedelta(days=i) for i in range(days * 2) if (start + timedelta(days=i)).weekday() < 5]
    return dates[-days:]


def _bottoming_prices(days: int = 260) -> pd.DataFrame:
    """跌深後打底：股價從 100 跌到 60 後橫盤，量能持續萎縮。"""
    rng = np.random.default_rng(3)
    dates = _trading_dates(days)
    decline_len = days - 60
    closes = np.concatenate(
        [
            np.linspace(100, 60, decline_len),
            60 + rng.normal(0, 0.15, 60),
        ]
    )
    volumes = np.concatenate(
        [
            np.full(days - 20, 10_000_000.0),
            np.linspace(6_000_000, 2_000_000, 20),
        ]
    )
    records = []
    for idx, trade_date in enumerate(dates):
        close = float(closes[idx])
        records.append(
            {
                "trade_date": trade_date,
                "open": close,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": float(volumes[idx]),
                "turnover": float(volumes[idx]) * close,
            }
        )
    return pd.DataFrame(records)


def _momentum_prices(days: int = 260) -> pd.DataFrame:
    """強勢上攻股：創高、爆量，不應被左側策略選中。"""
    dates = _trading_dates(days)
    closes = np.linspace(60, 100, days)
    records = []
    for idx, trade_date in enumerate(dates):
        close = float(closes[idx])
        volume = 10_000_000.0 * (3 if idx >= days - 5 else 1)
        records.append(
            {
                "trade_date": trade_date,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": volume,
                "turnover": volume * close,
            }
        )
    return pd.DataFrame(records)


def _chip_rows(dates: list[date], short_drop: bool, margin_drop: bool, day_trade_pct: float) -> pd.DataFrame:
    count = len(dates)
    short = np.linspace(1_000_000, 700_000 if short_drop else 1_300_000, count)
    margin = np.linspace(500_000, 420_000 if margin_drop else 620_000, count)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "short_balance": short,
            "margin_balance": margin,
            "day_trade_ratio_pct": day_trade_pct,
        }
    )


def _holder_rows(gain_pp: float, weeks: int = 10) -> pd.DataFrame:
    end = date.today()
    dates = [end - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    ratios = np.linspace(50.0, 50.0 + gain_pp, weeks)
    return pd.DataFrame({"date": dates, "big_holder_ratio_pct": ratios})


def _light_trust_rows(dates: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": dates,
            "foreign_buy_sell": 0.0,
            "investment_trust_buy_sell": 1_000.0,
            "dealer_buy_sell": 0.0,
        }
    )


def test_bottoming_stock_is_left_side_candidate() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)
    recent_dates = list(prices["trade_date"].tail(30))

    result = LeftSideScoringEngine().score(
        symbol="TEST",
        indicators=indicators,
        chip_rows=_chip_rows(recent_dates, short_drop=True, margin_drop=True, day_trade_pct=5.0),
        holder_rows=_holder_rows(gain_pp=1.5),
        institutional_rows=_light_trust_rows(recent_dates[-10:]),
        revenue_row=pd.Series({"revenue_yoy_pct": -5.0}),
        financial_row=pd.Series({"eps": 1.2}),
    )

    assert result.is_candidate
    assert result.total_score >= 70
    for reason in (
        "low_base",
        "short_covering",
        "margin_flush",
        "day_trade_freeze",
        "volume_dryup",
        "big_holder_accumulation",
        "trust_light_buying",
        "still_profitable",
    ):
        assert reason in result.reasons
    assert result.trade_plan is not None


def test_momentum_stock_is_not_left_side_candidate() -> None:
    prices = _momentum_prices()
    indicators = add_technical_indicators(prices)
    recent_dates = list(prices["trade_date"].tail(30))

    result = LeftSideScoringEngine().score(
        symbol="HOT",
        indicators=indicators,
        chip_rows=_chip_rows(recent_dates, short_drop=False, margin_drop=False, day_trade_pct=20.0),
        holder_rows=_holder_rows(gain_pp=0.0),
        institutional_rows=None,
        revenue_row=pd.Series({"revenue_yoy_pct": 30.0}),
        financial_row=pd.Series({"eps": 5.0}),
    )

    assert not result.is_candidate
    assert "low_base" not in result.reasons
    assert "short_covering" not in result.reasons
    assert "margin_flush" not in result.reasons


def test_left_side_score_without_chip_data_is_limited() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)

    result = LeftSideScoringEngine().score(symbol="NOCHIP", indicators=indicators)

    # 沒有籌碼與內部人資料時，只剩底部結構與量能構面，不應成為候選
    assert result.short_covering_score == 0
    assert result.smart_money_score == 0
    assert not result.is_candidate


def _washout_chip_rows(dates: list[date], short_end: float, margin_end: float) -> pd.DataFrame:
    count = len(dates)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "short_balance": np.linspace(1_000_000, short_end, count),
            "margin_balance": np.linspace(500_000, margin_end, count),
            "day_trade_ratio_pct": 5.0,
        }
    )


def test_deep_washout_bonus_requires_both_signals_deep() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)
    recent = list(prices["trade_date"].tail(30))
    engine = LeftSideScoringEngine()

    # 空單回補 -40%、融資 -20% 同時深跌 → 觸發深度洗盤加分與標記
    deep = engine.score(
        symbol="DEEP",
        indicators=indicators,
        chip_rows=_washout_chip_rows(recent, short_end=600_000, margin_end=400_000),
    )
    assert "deep_washout" in deep.reasons

    # 只有空單深跌、融資幾乎沒動（-2%）→ 不觸發組合
    short_only = engine.score(
        symbol="SHORT_ONLY",
        indicators=indicators,
        chip_rows=_washout_chip_rows(recent, short_end=600_000, margin_end=490_000),
    )
    assert "deep_washout" not in short_only.reasons
    # 組合加分讓同底部結構下的總分更高
    assert deep.total_score > short_only.total_score


def test_sentiment_dimension_is_reserved_placeholder() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)
    engine = LeftSideScoringEngine()

    without_sentiment = engine.score(symbol="TEST", indicators=indicators)
    frozen_sentiment = engine.score(symbol="TEST", indicators=indicators, sentiment_ratio=0.05)

    assert without_sentiment.sentiment_score == 0
    assert frozen_sentiment.sentiment_score == 0
    assert frozen_sentiment.total_score == without_sentiment.total_score
    assert "sentiment_freeze" in frozen_sentiment.reasons


def test_catalyst_and_sector_resonance_are_scored() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)

    result = LeftSideScoringEngine().score(
        symbol="CAT",
        indicators=indicators,
        catalyst_row={"catalyst_days_left": 3, "catalyst_in_window": True},
        sector_row={
            "sector_turnover_rank_pct": 10,
            "sector_turnover_jump_pct": 60,
        },
    )

    assert result.catalyst_score > 0
    assert result.sector_resonance_score == 10
    assert "near_catalyst" in result.reasons
    assert "sector_turnover_leader" in result.reasons
    assert "sector_turnover_jump" in result.reasons


def test_v4_microstructure_strategies_are_scored() -> None:
    prices = _squeeze_ignition_prices()
    indicators = add_technical_indicators(prices)

    result = LeftSideScoringEngine().score(
        symbol="V4",
        indicators=indicators,
        microstructure_row={
            "days_to_quarter_end": 12,
            "trust_holding_ratio_pct": 5,
            "trust_net_buy_5d": 1200,
            "disposition_days_to_end": 1,
            "disposition_range_pct": 8,
            "big_holder_ratio_change_pp": 0.2,
            "has_convertible_bond": True,
            "cb_price": 106,
            "cb_volume_ratio": 3.5,
            "same_city_branch_buy_streak_days": 5,
            "same_city_branch_buy_volume_pct": 12,
        },
    )

    assert result.microstructure_score == 15
    assert result.window_dressing_score == 15
    assert result.jailbreak_score == 15
    assert result.cb_signal_score == 15
    assert result.geographic_broker_score == 15
    for reason in (
        "window_dressing_setup",
        "jailbreak_setup",
        "cb_abnormal_signal",
        "geographic_broker_accumulation",
    ):
        assert reason in result.reasons


def _squeeze_ignition_prices(days: int = 260) -> pd.DataFrame:
    """打底末端出現壓縮點火：波動遞減至今日最低、今日溫和放量收紅。"""
    dates = _trading_dates(days)
    decline = np.linspace(100, 60.4, days - 60)
    offsets = np.linspace(0.4, 0.0, 60)  # 波動逐日收斂，今日帶寬為期間最低
    closes = np.concatenate([decline, 60.0 - offsets])
    records = []
    for idx, trade_date in enumerate(dates):
        close = float(closes[idx])
        is_today = idx == days - 1
        volume = 4_000_000.0 if is_today else 2_000_000.0  # 今日量 = 前 5 日均量 2 倍
        records.append(
            {
                "trade_date": trade_date,
                "open": close * 0.995 if is_today else close,  # 今日收紅
                "high": close * 1.002,
                "low": close * 0.993,
                "close": close,
                "volume": volume,
                "turnover": volume * close,
            }
        )
    return pd.DataFrame(records)


def test_ignition_dimension_fires_on_squeeze_breakout() -> None:
    prices = _squeeze_ignition_prices()
    indicators = add_technical_indicators(prices)

    result = LeftSideScoringEngine().score(symbol="SQZ", indicators=indicators)

    assert result.bb_bandwidth_percentile is not None
    assert result.bb_bandwidth_percentile <= 5
    for reason in ("bollinger_squeeze_extreme", "mild_ignition", "bullish_red_candle"):
        assert reason in result.reasons
    assert result.ignition_score == 10


def test_trust_streak_buying_rewarded() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)
    recent_dates = list(prices["trade_date"].tail(10))
    institutional = pd.DataFrame(
        {
            "trade_date": recent_dates,
            "foreign_buy_sell": 0.0,
            # 近 5 日內連續 3 日買超
            "investment_trust_buy_sell": [0, 0, 0, 0, 0, 800, 900, 700, -100, 200],
            "dealer_buy_sell": 0.0,
        }
    )

    result = LeftSideScoringEngine().score(
        symbol="TRUST",
        indicators=indicators,
        institutional_rows=institutional,
    )

    assert "trust_streak_buying" in result.reasons
    assert "trust_light_buying" in result.reasons


def test_bollinger_squeeze_signal_intersection() -> None:
    from taiwan_stock_screener.indicators.technical import bollinger_squeeze_signal

    trigger = bollinger_squeeze_signal(_squeeze_ignition_prices())
    assert trigger is not None
    assert trigger["is_extreme_squeeze"]
    assert trigger["is_mild_ignition"]
    assert trigger["is_bullish_confirmation"]
    assert trigger["is_squeeze_trigger"]

    hot = bollinger_squeeze_signal(_momentum_prices())
    assert hot is not None
    assert not hot["is_squeeze_trigger"]


def test_demo_output_includes_left_side_candidates() -> None:
    from scripts.run_screener import demo_output

    output = demo_output()

    assert output["left_side_candidates"], "demo output should include left-side candidates"
    assert output["left_side_threshold"] > 0
    top = output["left_side_candidates"][0]
    assert top["strategy"] == "left_side"
    for key in (
        "base_structure_score",
        "short_covering_score",
        "retail_capitulation_score",
        "smart_money_score",
        "short_balance_change_pct",
        "margin_balance_change_pct",
    ):
        assert key in top


def test_left_observation_shortlist_keeps_dashboard_populated() -> None:
    from scripts.run_screener import build_left_observation_shortlist

    quotes = pd.DataFrame(
        [
            {"symbol": "1111", "close": 20, "turnover": 30_000_000},
            {"symbol": "2222", "close": 8, "turnover": 500_000_000},
            {"symbol": "3333", "close": 50, "turnover": 120_000_000},
            {"symbol": "4444", "close": 3, "turnover": 900_000_000},  # 低於 5 元下限
        ]
    )

    result = build_left_observation_shortlist(quotes, limit=2)

    assert list(result["symbol"]) == ["2222", "3333"]
    assert result["signal_score"].tolist() == [0.0, 0.0]
    assert "short_balance_change_pct" in result.columns


def test_left_observation_shortlist_excludes_momentum_symbols() -> None:
    from scripts.run_screener import build_left_observation_shortlist

    quotes = pd.DataFrame(
        [
            {"symbol": "1111", "close": 20, "turnover": 30_000_000},
            {"symbol": "2222", "close": 8, "turnover": 500_000_000},
            {"symbol": "3333", "close": 50, "turnover": 120_000_000},
        ]
    )

    result = build_left_observation_shortlist(quotes, limit=3, exclude_symbols={"2222", "3333"})

    assert list(result["symbol"]) == ["1111"]


def test_left_signal_shortlist_excludes_momentum_symbols() -> None:
    from scripts.run_screener import exclude_symbols_from_shortlist

    shortlist = pd.DataFrame(
        [
            {"symbol": "2059", "signal_score": 80.0},
            {"symbol": "4551", "signal_score": 66.0},
            {"symbol": "8046", "signal_score": 54.0},
        ]
    )

    result = exclude_symbols_from_shortlist(shortlist, {"2059", "8046"})

    assert list(result["symbol"]) == ["4551"]


def test_chip_store_summary_handles_missing_optional_columns() -> None:
    from scripts.run_screener import chip_store_summary

    summary = chip_store_summary(pd.DataFrame({"date": ["2026-07-13"], "symbol": ["2330"]}))

    assert summary["date_count"] == 1
    assert summary["margin_rows"] == 0
    assert summary["short_rows"] == 0
    assert summary["day_trade_rows"] == 0

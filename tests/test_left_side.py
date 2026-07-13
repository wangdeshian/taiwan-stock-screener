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


def test_sentiment_dimension_is_reserved_placeholder() -> None:
    prices = _bottoming_prices()
    indicators = add_technical_indicators(prices)
    engine = LeftSideScoringEngine()

    without_sentiment = engine.score(symbol="TEST", indicators=indicators)
    frozen_sentiment = engine.score(symbol="TEST", indicators=indicators, sentiment_ratio=0.05)

    assert without_sentiment.sentiment_score == 0
    assert frozen_sentiment.sentiment_score > 0
    assert "sentiment_freeze" in frozen_sentiment.reasons


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
        ]
    )

    result = build_left_observation_shortlist(quotes, limit=2)

    assert list(result["symbol"]) == ["3333", "1111"]
    assert result["signal_score"].tolist() == [0.0, 0.0]
    assert "short_balance_change_pct" in result.columns


def test_chip_store_summary_handles_missing_optional_columns() -> None:
    from scripts.run_screener import chip_store_summary

    summary = chip_store_summary(pd.DataFrame({"date": ["2026-07-13"], "symbol": ["2330"]}))

    assert summary["date_count"] == 1
    assert summary["margin_rows"] == 0
    assert summary["short_rows"] == 0
    assert summary["day_trade_rows"] == 0

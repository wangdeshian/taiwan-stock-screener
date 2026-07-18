from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from taiwan_stock_screener.collectors.broker_flow import analyze_broker_flow, is_blacklisted


def _dates(days: int) -> list[date]:
    start = date(2026, 7, 1)
    return [start + timedelta(days=i) for i in range(days)]


def _accumulation_frame(days: int = 5) -> pd.DataFrame:
    """主力吸貨：兩個大分點每天大買，其餘小分點小賣。"""
    records = []
    for day in _dates(days):
        records.append({"trade_date": day, "securities_trader": "永豐金-台北", "buy": 6000, "sell": 500, "price": 50.0})
        records.append({"trade_date": day, "securities_trader": "國泰-敦南", "buy": 4000, "sell": 300, "price": 51.0})
        for i in range(5):
            records.append({"trade_date": day, "securities_trader": f"散戶券商{i}", "buy": 200, "sell": 800, "price": 50.5})
    return pd.DataFrame(records)


def test_is_blacklisted_normalizes_names() -> None:
    blacklist = ["凱基-松山", "元大-土城永寧"]
    assert is_blacklisted("凱基松山", blacklist)
    assert is_blacklisted("凱基-松山", blacklist)
    assert is_blacklisted("元大 土城永寧", blacklist)
    assert not is_blacklisted("凱基-台北", blacklist)


def test_accumulation_stage_detected() -> None:
    result = analyze_broker_flow(_accumulation_frame(), day_trade_blacklist=["凱基-松山"])

    assert result is not None
    assert result["chip_stage"] == "accumulation"
    assert result["branch_concentration_streak"] >= 3
    assert result["branch_concentration_pct"] >= 50
    assert result["day_trade_branch_ratio_pct"] == 0
    # 主力成本線 = 兩大買超分點的加權平均買價 (6000*50 + 4000*51) / 10000 = 50.4
    assert abs(result["main_cost_line"] - 50.4) < 0.01


def test_churn_stage_when_day_trade_branches_dominate() -> None:
    records = []
    for day in _dates(5):
        records.append({"trade_date": day, "securities_trader": "凱基-松山", "buy": 5000, "sell": 4900, "price": 50.0})
        records.append({"trade_date": day, "securities_trader": "元大-土城永寧", "buy": 3000, "sell": 3100, "price": 50.0})
        records.append({"trade_date": day, "securities_trader": "永豐金-台北", "buy": 1000, "sell": 200, "price": 50.0})
    frame = pd.DataFrame(records)

    result = analyze_broker_flow(frame, day_trade_blacklist=["凱基-松山", "元大-土城永寧"])

    assert result is not None
    assert result["chip_stage"] == "churn"
    assert result["day_trade_branch_ratio_pct"] > 50
    # 成本線不受隔日沖分點影響
    assert result["main_cost_line"] == 50.0


def test_analyze_broker_flow_handles_missing_data() -> None:
    assert analyze_broker_flow(pd.DataFrame()) is None
    assert analyze_broker_flow(pd.DataFrame({"trade_date": [], "securities_trader": [], "buy": [], "sell": []})) is None


def test_engine_scores_branch_accumulation_and_warns_on_churn() -> None:
    from tests.test_left_side import _bottoming_prices
    from taiwan_stock_screener.indicators.technical import add_technical_indicators
    from taiwan_stock_screener.scoring.left_side import LeftSideScoringEngine

    indicators = add_technical_indicators(_bottoming_prices())
    engine = LeftSideScoringEngine()

    accumulation = engine.score(symbol="ACC", indicators=indicators, broker_row={"chip_stage": "accumulation"})
    churn = engine.score(symbol="CHN", indicators=indicators, broker_row={"chip_stage": "churn"})
    baseline = engine.score(symbol="BASE", indicators=indicators)

    assert accumulation.smart_money_score > baseline.smart_money_score
    assert "branch_concentration" in accumulation.reasons
    assert churn.smart_money_score == baseline.smart_money_score
    assert "day_trade_branch_churn" in churn.reasons


def test_engine_short_squeeze_setup_signal() -> None:
    from tests.test_left_side import _bottoming_prices
    from taiwan_stock_screener.indicators.technical import add_technical_indicators
    from taiwan_stock_screener.scoring.left_side import LeftSideScoringEngine

    indicators = add_technical_indicators(_bottoming_prices())
    days = _dates(20)
    chip_rows = pd.DataFrame(
        {
            "trade_date": days,
            "short_balance": [1000 - i * 20 for i in range(20)],  # 空單回補中
            "margin_balance": 1000.0,
            "margin_short_balance": 400.0,  # 券資比 40% ≥ 30%
            "day_trade_ratio_pct": 5.0,
        }
    )

    result = LeftSideScoringEngine().score(symbol="SQZ", indicators=indicators, chip_rows=chip_rows)

    assert "short_squeeze_setup" in result.reasons

from __future__ import annotations

import pandas as pd

from scripts.run_screener import (
    calculate_peg_ratio,
    extract_financial_metrics,
    industry_for_symbol,
    industry_label_from_code,
    relative_strength_metrics,
    trailing_return_pct,
)


def test_trailing_return_pct_uses_requested_window() -> None:
    history = pd.DataFrame({"close": [100, 105, 110, 120]})

    assert trailing_return_pct(history, 2) == 14.29


def test_relative_strength_metrics_compare_stock_to_benchmark() -> None:
    stock = pd.DataFrame({"close": [100, *([100] * 19), 130]})
    benchmark = pd.DataFrame({"close": [100, *([100] * 19), 110]})

    result = relative_strength_metrics(stock, benchmark)

    assert result["stock_return_20d_pct"] == 30
    assert result["benchmark_return_20d_pct"] == 10
    assert result["relative_strength_20d_pct"] == 20


def test_calculate_peg_ratio_prefers_official_pe() -> None:
    assert calculate_peg_ratio(close=100, pe_ratio=20, revenue_yoy_pct=25, eps=2) == 0.8


def test_calculate_peg_ratio_can_estimate_pe_from_eps() -> None:
    assert calculate_peg_ratio(close=100, pe_ratio=None, revenue_yoy_pct=25, eps=2) == 0.5


def test_industry_helpers_map_official_codes_and_etfs() -> None:
    assert industry_label_from_code("24") == "半導體"
    assert industry_for_symbol("0050", "元大台灣50", {}) == "ETF/ETN"


def test_extract_financial_metrics_can_estimate_annualized_roe() -> None:
    statements = pd.DataFrame(
        [
            {"date": "2026-03-31", "type": "EPS", "value": "2"},
            {
                "date": "2026-03-31",
                "type": "ProfitLossAttributableToOwnersOfParent",
                "value": "100",
            },
            # 損益表裡的「綜合損益歸屬母公司」與權益餘額同名，不得當分母
            {
                "date": "2026-03-31",
                "type": "EquityAttributableToOwnersOfParent",
                "value": "120",
            },
        ]
    )
    balance = pd.DataFrame(
        [{"date": "2026-03-31", "type": "EquityAttributableToOwnersOfParent", "value": "1000"}]
    )

    # 沒有資產負債表時不產生 ROE（避免誤用損益表的同名科目）
    assert extract_financial_metrics(statements) == {"eps": 2.0}

    result = extract_financial_metrics(statements, balance)

    assert result == {"eps": 2.0, "roe_pct": 40.0}


def test_extract_financial_metrics_rejects_absurd_roe() -> None:
    # 抓錯科目（權益值過小）導致年化 ROE 超出 ±100% 時，寧缺勿錯
    frame = pd.DataFrame(
        [
            {"date": "2026-03-31", "type": "EPS", "value": "2"},
            {"date": "2026-03-31", "type": "ProfitLoss", "value": "1000"},
            {"date": "2026-03-31", "type": "Equity", "value": "600"},
        ]
    )

    result = extract_financial_metrics(frame)

    assert result == {"eps": 2.0}


def test_extract_financial_metrics_ignores_fuzzy_lookalike_types() -> None:
    # 不得誤抓 ProfitLossBeforeTax / OtherEquityInterest 這類相似科目
    frame = pd.DataFrame(
        [
            {"date": "2026-03-31", "type": "ProfitLossFromOperatingActivities", "value": "-500"},
            {"date": "2026-03-31", "type": "OtherEquityInterest", "value": "5"},
        ]
    )

    assert extract_financial_metrics(frame) is None


def test_extract_financial_metrics_reads_equity_from_balance_sheet() -> None:
    statements = pd.DataFrame(
        [
            {"date": "2026-03-31", "type": "EPS", "value": "2"},
            {"date": "2026-03-31", "type": "IncomeAfterTaxes", "value": "50"},
        ]
    )
    balance = pd.DataFrame(
        [
            {"date": "2026-03-31", "type": "Equity", "value": "1000"},
            {"date": "2026-03-31", "type": "CapitalStock", "value": "500"},
        ]
    )

    result = extract_financial_metrics(statements, balance)

    assert result == {"eps": 2.0, "roe_pct": 20.0, "share_capital_twd": 500.0}


def test_previous_left_scores_prefers_full_score_map() -> None:
    from scripts.run_screener import previous_left_scores

    entries = [
        {
            "date": "2026-07-20",
            "left_side_candidates": [{"symbol": "2493", "total_score": 63.5}],
            "left_side_scores": {"2493": 63.5, "9999": 21.0},
        },
        {
            "date": "2026-07-16",
            "left_side_candidates": [{"symbol": "2493", "total_score": 23.5}],
        },
        # 超出視窗的舊資料要被忽略
        {
            "date": "2026-07-01",
            "left_side_candidates": [{"symbol": "8888", "total_score": 80.0}],
        },
    ]

    result = previous_left_scores(entries, "2026-07-21", window_days=5)
    assert result["2493"] == {"date": "2026-07-20", "score": 63.5}
    # 未進榜低分股也要有基準（來自 left_side_scores 全表）
    assert result["9999"]["score"] == 21.0
    assert "8888" not in result
    # 今天（含）之後的資料不能當基準
    entries.insert(0, {"date": "2026-07-21", "left_side_scores": {"2493": 99.0}})
    assert previous_left_scores(entries, "2026-07-21", window_days=5)["2493"]["score"] == 63.5
    assert previous_left_scores([], "2026-07-21", window_days=5) == {}

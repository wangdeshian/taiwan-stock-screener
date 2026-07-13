from __future__ import annotations

import pandas as pd

from scripts.run_screener import calculate_peg_ratio, relative_strength_metrics, trailing_return_pct


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

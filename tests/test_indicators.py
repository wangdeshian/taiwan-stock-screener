from __future__ import annotations

from taiwan_stock_screener.collectors.sample import sample_daily_prices
from taiwan_stock_screener.indicators.technical import add_technical_indicators


def test_add_technical_indicators_has_expected_columns() -> None:
    prices = sample_daily_prices(days=80)
    frame = prices[prices["symbol"] == "2330"]
    result = add_technical_indicators(frame)
    for column in ["ma5", "ma20", "rsi14", "macd", "atr14", "volume_ratio", "distance_from_60d_high_pct"]:
        assert column in result.columns
    assert len(result) == len(frame)
    assert result["ma20"].notna().all()

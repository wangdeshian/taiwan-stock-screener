from __future__ import annotations

import pandas as pd

from taiwan_stock_screener.indicators.technical import add_technical_indicators
from taiwan_stock_screener.strategy.technical_templates import (
    evaluate_technical_templates,
    technical_reason_ids,
)


def price_frame(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    volumes = volumes or [1_000_000] * len(closes)
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": [close - 0.5 for close in closes],
            "high": [close + 1.0 for close in closes],
            "low": [close - 1.0 for close in closes],
            "close": closes,
            "volume": volumes,
        }
    )


def ids(signals: list[dict]) -> set[str]:
    return {str(signal["id"]) for signal in signals}


def test_new_high_momentum_template_triggers() -> None:
    closes = [float(50 + i) for i in range(70)]
    volumes = [1_000_000] * 69 + [2_500_000]
    indicators = add_technical_indicators(price_frame(closes, volumes))

    signals = evaluate_technical_templates(indicators)

    assert "new_high_momentum" in ids(signals)
    assert "tech_new_high_momentum" in technical_reason_ids(signals)


def test_ma_cluster_breakout_template_triggers() -> None:
    closes = [100.0] * 29 + [104.0]
    volumes = [1_000_000] * 29 + [2_500_000]
    indicators = add_technical_indicators(price_frame(closes, volumes))

    signals = evaluate_technical_templates(indicators)

    assert "ma_cluster_breakout" in ids(signals)

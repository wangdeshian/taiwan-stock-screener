from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from taiwan_stock_screener.strategy.chip_templates import (
    chip_reason_ids,
    evaluate_chip_templates,
)


def price_frame(days: int = 10, volume: float = 1_500_000) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=days, freq="B")
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": [50.0] * days,
            "high": [51.0] * days,
            "low": [49.0] * days,
            "close": [50.0] * days,
            "volume": [volume] * days,
        }
    )


def institutional_frame(
    foreign: list[float],
    trust: list[float],
    dealer: list[float],
) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(foreign), freq="B")
    return pd.DataFrame(
        {
            "trade_date": dates,
            "foreign_buy_sell": foreign,
            "investment_trust_buy_sell": trust,
            "dealer_buy_sell": dealer,
        }
    )


def ids(signals: list[dict]) -> set[str]:
    return {str(signal["id"]) for signal in signals}


def test_institutional_accumulation_template_accepts_share_units() -> None:
    rows = institutional_frame(
        foreign=[120_000, 130_000, 140_000, 150_000, 160_000],
        trust=[80_000, 90_000, 95_000, 100_000, 110_000],
        dealer=[70_000, 80_000, 90_000, 100_000, 120_000],
    )

    signals = evaluate_chip_templates(institutional_rows=rows)

    assert "institutional_accumulation_watch" in ids(signals)
    assert "chip_institutional_accumulation_watch" in chip_reason_ids(signals)


def test_large_holder_accumulation_retail_exit_template_triggers() -> None:
    today = date(2026, 1, 31)
    holders = pd.DataFrame(
        {
            "date": [today - timedelta(weeks=3 - i) for i in range(4)],
            "holder_1000_plus_pct": [30.0, 30.2, 30.5, 30.9],
            "holder_200_minus_pct": [40.0, 39.7, 39.5, 39.1],
        }
    )

    signals = evaluate_chip_templates(indicators=price_frame(), holder_rows=holders)

    assert "large_holder_accumulation_retail_exit" in ids(signals)


def test_small_cap_foreign_template_does_not_trigger_without_capital() -> None:
    rows = institutional_frame(
        foreign=[200_000, 220_000, 230_000, 240_000, 350_000],
        trust=[0, 0, 0, 0, 0],
        dealer=[0, 0, 0, 0, 0],
    )

    signals = evaluate_chip_templates(indicators=price_frame(), institutional_rows=rows)

    assert "small_cap_foreign_accumulation" not in ids(signals)

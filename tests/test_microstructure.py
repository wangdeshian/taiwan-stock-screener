from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from taiwan_stock_screener.collectors.microstructure import (
    build_microstructure_row,
    cb_fields,
    city_of_address,
    days_to_quarter_end,
    disposition_fields,
    geographic_fields,
    quarter_end_of,
    trust_net_buy_recent,
)


def test_quarter_end_math() -> None:
    assert quarter_end_of(date(2026, 7, 19)) == date(2026, 9, 30)
    assert quarter_end_of(date(2026, 12, 5)) == date(2026, 12, 31)
    assert quarter_end_of(date(2026, 1, 2)) == date(2026, 3, 31)
    assert days_to_quarter_end(date(2026, 9, 20)) == 10


def test_city_of_address_normalizes_taiwan_variants() -> None:
    assert city_of_address("臺北市信義區市府路45號") == "台北市"
    assert city_of_address("新竹縣寶山鄉創新一路") == "新竹縣"
    assert city_of_address("科學園區") is None
    assert city_of_address(None) is None


def test_trust_net_buy_recent() -> None:
    rows = pd.DataFrame(
        {
            "trade_date": [date(2026, 7, 1) + timedelta(days=i) for i in range(8)],
            "investment_trust_buy_sell": [0, 0, 0, 100, 200, 300, -100, 50],
        }
    )
    assert trust_net_buy_recent(rows, days=5) == 550
    assert trust_net_buy_recent(None) is None


def test_disposition_fields_active_period() -> None:
    today = date(2026, 7, 19)
    disposition = pd.DataFrame(
        [
            {"stock_id": "1234", "start_date": "2026-07-08", "end_date": "2026-07-21"},
            {"stock_id": "9999", "start_date": "2026-06-01", "end_date": "2026-06-10"},
        ]
    )
    days = [date(2026, 7, 8) + timedelta(days=i) for i in range(10)]
    history = pd.DataFrame(
        {
            "trade_date": days,
            "open": 100.0,
            "high": [104.0] * 10,
            "low": [98.0] * 10,
            "close": [100.0] * 10,
            "volume": 1_000_000.0,
            "turnover": 100_000_000.0,
        }
    )
    holders = pd.DataFrame(
        {
            "date": [date(2026, 7, 8), date(2026, 7, 15)],
            "big_holder_ratio_pct": [50.0, 50.5],
        }
    )

    result = disposition_fields("1234", disposition, history, holders, today)

    assert result["disposition_days_to_end"] == 2
    assert result["disposition_range_pct"] == 6.0  # (104-98)/100
    assert result["big_holder_ratio_change_pp"] == 0.5

    # 已出關（結束日過了）不再視為處置中
    assert disposition_fields("9999", disposition, history, holders, today)["disposition_days_to_end"] is None


def test_cb_fields_price_and_volume_ratio() -> None:
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(25)]
    cb_daily = pd.DataFrame(
        {
            "date": days,
            "close": [101.0] * 24 + [106.5],
            "volume": [100.0] * 24 + [350.0],
        }
    )

    result = cb_fields(cb_daily, volume_avg_days=20)

    assert result["cb_price"] == 106.5
    assert result["cb_volume_ratio"] == 3.5
    assert cb_fields(pd.DataFrame()) == {"cb_price": None, "cb_volume_ratio": None}


def _geo_frame(days: int, same_city_net_positive: bool) -> pd.DataFrame:
    records = []
    for i in range(days):
        day = date(2026, 7, 10) + timedelta(days=i)
        records.append(
            {
                "trade_date": day,
                "securities_trader": "元大-新竹",
                "buy": 6000 if same_city_net_positive else 1000,
                "sell": 1000 if same_city_net_positive else 6000,
            }
        )
        records.append({"trade_date": day, "securities_trader": "凱基-台北", "buy": 20000, "sell": 20000})
    return pd.DataFrame(records)


def test_geographic_fields_same_city_streak() -> None:
    trader_map = {"元大-新竹": "新竹市", "凱基-台北": "台北市"}

    result = geographic_fields(_geo_frame(5, True), trader_map, "新竹市")
    assert result["same_city_branch_buy_streak_days"] == 5
    # 最新一日：同城淨買 5000 / 當日成交量 (6000+1000+20000+20000)/2=23500 ≈ 21.28%
    assert result["same_city_branch_buy_volume_pct"] == 21.28

    selling = geographic_fields(_geo_frame(5, False), trader_map, "新竹市")
    assert selling["same_city_branch_buy_streak_days"] == 0

    no_city = geographic_fields(_geo_frame(5, True), trader_map, None)
    assert no_city["same_city_branch_buy_streak_days"] is None


def test_build_microstructure_row_requires_some_signal() -> None:
    today = date(2026, 7, 19)
    assert build_microstructure_row(today=today) is None

    rows = pd.DataFrame(
        {
            "trade_date": [today - timedelta(days=i) for i in range(5)],
            "investment_trust_buy_sell": [100.0] * 5,
        }
    )
    row = build_microstructure_row(today=today, institutional_rows=rows, has_convertible_bond=False)
    assert row is not None
    assert row["trust_net_buy_5d"] == 500
    assert row["days_to_quarter_end"] == days_to_quarter_end(today)
    assert row["has_convertible_bond"] is False

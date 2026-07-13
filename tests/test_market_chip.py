from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from taiwan_stock_screener.collectors.market_chip import (
    chip_rows_for,
    load_chip_store,
    parse_snapshot,
    prefilter_left_symbols,
    save_chip_store,
)


def test_parse_snapshot_with_english_openapi_keys() -> None:
    rows = [
        {"Code": "2330", "Name": "台積電", "MarginPurchaseTodayBalance": "12,345", "MarginPurchaseBuy": "10"},
        {"Code": "2317", "Name": "鴻海", "MarginPurchaseTodayBalance": "--", "MarginPurchaseBuy": "5"},
    ]
    frame = parse_snapshot(rows, ["margin_balance"])
    assert list(frame["symbol"]) == ["2330"]
    assert float(frame.iloc[0]["margin_balance"]) == 12345


def test_parse_snapshot_with_chinese_rwd_keys() -> None:
    rows = [
        {"證券代號": "2303", "證券名稱": "聯電", "借券賣出當日餘額": "8,000", "融券今日餘額": "999"},
    ]
    frame = parse_snapshot(rows, ["short_balance"])
    assert list(frame["symbol"]) == ["2303"]
    assert float(frame.iloc[0]["short_balance"]) == 8000


def test_parse_snapshot_skips_ratio_columns_for_volume() -> None:
    rows = [
        {"Code": "2330", "當沖成交股數佔比率": "12.3", "當沖成交股數": "5,000", "總成交股數": "50,000"},
    ]
    frame = parse_snapshot(rows, ["day_trade_volume", "total_volume"])
    assert float(frame.iloc[0]["day_trade_volume"]) == 5000
    assert float(frame.iloc[0]["total_volume"]) == 50000


def test_store_roundtrip_and_trim(tmp_path: Path) -> None:
    path = tmp_path / "chip_history.csv"
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(60)]
    store = pd.DataFrame(
        {
            "date": [d.isoformat() for d in days],
            "symbol": ["2330"] * 60,
            "margin_balance": range(60),
            "short_balance": range(60),
            "day_trade_volume": range(60),
            "total_volume": [100] * 60,
        }
    )
    trimmed = save_chip_store(store, path, keep_days=45)
    assert len(trimmed["date"].unique()) == 45

    loaded = load_chip_store(path)
    assert len(loaded["date"].unique()) == 45
    assert all(isinstance(value, str) for value in loaded["symbol"])  # 代號不能被讀成數字


def _synthetic_store(days: int = 25) -> pd.DataFrame:
    dates = [(date.today() - timedelta(days=days - i)).isoformat() for i in range(days)]
    frames = []
    # 潛伏標的：借券 -30%、融資 -15%、當沖率 4%
    frames.append(
        pd.DataFrame(
            {
                "date": dates,
                "symbol": "1111",
                "margin_balance": np.linspace(1000, 850, days),
                "short_balance": np.linspace(500, 350, days),
                "day_trade_volume": 4,
                "total_volume": 100,
            }
        )
    )
    # 熱門標的：借券 +40%、融資 +20%、當沖率 30%
    frames.append(
        pd.DataFrame(
            {
                "date": dates,
                "symbol": "2222",
                "margin_balance": np.linspace(1000, 1200, days),
                "short_balance": np.linspace(500, 700, days),
                "day_trade_volume": 30,
                "total_volume": 100,
            }
        )
    )
    return pd.concat(frames, ignore_index=True)


def test_prefilter_ranks_accumulation_signals_first() -> None:
    store = _synthetic_store()
    result = prefilter_left_symbols(store, ["1111", "2222"], limit=10)

    assert list(result["symbol"]) == ["1111"]  # 熱門股沒有任何起手式訊號，直接被過濾
    row = result.iloc[0]
    assert row["short_balance_change_pct"] < 0
    assert row["margin_balance_change_pct"] < 0
    assert row["day_trade_ratio_pct"] == 4
    assert row["signal_score"] == 1.0  # 三個訊號全滿


def test_prefilter_respects_universe_and_limit() -> None:
    store = _synthetic_store()
    assert prefilter_left_symbols(store, ["9999"], limit=10).empty
    assert len(prefilter_left_symbols(store, ["1111", "2222"], limit=0)) == 0


def test_chip_rows_for_builds_engine_input() -> None:
    store = _synthetic_store()
    rows = chip_rows_for(store, "1111")
    assert list(rows.columns) == ["trade_date", "margin_balance", "short_balance", "day_trade_ratio_pct"]
    assert rows["trade_date"].is_monotonic_increasing
    assert float(rows.iloc[-1]["day_trade_ratio_pct"]) == 4.0

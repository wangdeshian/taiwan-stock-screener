from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from taiwan_stock_screener.collectors.market_chip import (
    chip_rows_for,
    fetch_finmind_chip_snapshot,
    load_chip_store,
    parse_snapshot,
    prefilter_left_symbols,
    refresh_chip_store,
    save_chip_store,
)


def _fake_finmind_fetch(dataset: str, snapshot_date: date) -> list[dict]:
    """模擬 FinMind 日期模式回傳（週五 2026-07-10 有資料、其他日為假日）。"""
    if snapshot_date.weekday() >= 5:
        return []
    base = {"date": snapshot_date.isoformat()}
    if dataset == "TaiwanStockMarginPurchaseShortSale":
        return [
            {**base, "stock_id": "2330", "MarginPurchaseTodayBalance": 1000, "ShortSaleTodayBalance": 50},
            {**base, "stock_id": "6188", "MarginPurchaseTodayBalance": 800, "ShortSaleTodayBalance": 20},
        ]
    if dataset == "TaiwanDailyShortSaleBalances":
        return [{**base, "stock_id": "2330", "SBLShortSalesCurrentDayBalance": 700}]
    if dataset == "TaiwanStockDayTrading":
        return [{**base, "stock_id": "2330", "Volume": 5000}]
    if dataset == "TaiwanStockPrice":
        return [
            {**base, "stock_id": "2330", "Trading_Volume": 50000},
            {**base, "stock_id": "6188", "Trading_Volume": 9000},
        ]
    return []


def test_fetch_finmind_chip_snapshot_merges_datasets() -> None:
    frame = fetch_finmind_chip_snapshot(_fake_finmind_fetch, date(2026, 7, 10))

    row = frame[frame["symbol"] == "2330"].iloc[0]
    assert float(row["margin_balance"]) == 1000
    assert float(row["short_balance"]) == 700  # SBL 優先於融券
    assert float(row["day_trade_volume"]) == 5000
    assert float(row["total_volume"]) == 50000
    # 上櫃股（無 SBL 資料）退回融券餘額
    tpex_row = frame[frame["symbol"] == "6188"].iloc[0]
    assert float(tpex_row["margin_balance"]) == 800


def test_refresh_chip_store_falls_back_to_twse_when_finmind_gated(tmp_path, monkeypatch) -> None:
    import taiwan_stock_screener.collectors.market_chip as market_chip

    def gated_fetch(dataset: str, snapshot_date: date) -> list[dict]:
        raise RuntimeError("FinMind API failed status=400 msg=Your level is register.")

    fake_twse = pd.DataFrame({"symbol": ["2330"], "margin_balance": [1000.0]})
    monkeypatch.setattr(market_chip, "fetch_twse_snapshot_today", lambda: fake_twse.copy())

    path = tmp_path / "chip_history.csv"
    store, sources = refresh_chip_store(path, finmind_fetch=gated_fetch, backfill_days=7, max_backfill_dates=5)

    assert sources == ["TWSE"]
    assert list(store["symbol"]) == ["2330"]


def test_refresh_chip_store_backfills_recent_trading_days(tmp_path) -> None:
    path = tmp_path / "chip_history.csv"

    store, sources = refresh_chip_store(path, finmind_fetch=_fake_finmind_fetch, backfill_days=7, max_backfill_dates=5)

    assert sources and sources[0].startswith("FinMind×")
    assert not store.empty
    assert store["date"].nunique() >= 3  # 一週內至少 3 個平日
    # 再跑一次不會重複抓已存在的日期
    store2, sources2 = refresh_chip_store(path, finmind_fetch=_fake_finmind_fetch, backfill_days=7, max_backfill_dates=5)
    assert store2["date"].nunique() == store["date"].nunique()


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

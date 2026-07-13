from __future__ import annotations

import pandas as pd

from scripts.run_screener import merge_quote_into_history, parse_twse_rwd_quotes, roc_date_to_iso


def test_roc_date_to_iso_supports_twse_formats() -> None:
    assert roc_date_to_iso("1150713") == "2026-07-13"
    assert roc_date_to_iso("115/07/13") == "2026-07-13"
    assert roc_date_to_iso("20260713") == "2026-07-13"


def test_parse_twse_rwd_quotes_uses_final_close_for_target_date() -> None:
    payload = {
        "stat": "OK",
        "date": "20260713",
        "tables": [
            {
                "title": "115年07月13日 每日收盤行情",
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "成交股數",
                    "成交筆數",
                    "成交金額",
                    "開盤價",
                    "最高價",
                    "最低價",
                    "收盤價",
                    "漲跌(+/-)",
                    "漲跌價差",
                    "最後揭示買價",
                    "最後揭示買量",
                    "最後揭示賣價",
                    "最後揭示賣量",
                    "本益比",
                ],
                "data": [
                    [
                        "3026",
                        "禾伸堂",
                        "13,719,015",
                        "26,109",
                        "12,970,798,249",
                        "1,025.00",
                        "1,030.00",
                        "905.00",
                        "905.00",
                        "-",
                        "100.00",
                        "--",
                        "0",
                        "905.00",
                        "579",
                        "116.62",
                    ],
                ],
            }
        ],
    }

    frame = parse_twse_rwd_quotes(payload)
    row = frame.iloc[0]

    assert row["symbol"] == "3026"
    assert row["quote_date"] == "2026-07-13"
    assert row["close"] == 905
    assert row["pe_ratio"] == 116.62


def test_merge_quote_into_history_appends_newer_official_close() -> None:
    history = pd.DataFrame(
        [
            {
                "trade_date": "2026-07-09",
                "open": 966,
                "high": 1045,
                "low": 948,
                "close": 1005,
                "volume": 19_004_107,
                "turnover": 19_023_371_084,
            }
        ]
    )
    quote = {
        "quote_date": "2026-07-13",
        "open": 1025,
        "high": 1030,
        "low": 905,
        "close": 905,
        "volume": 13_719_015,
        "turnover": 12_970_798_249,
    }

    merged = merge_quote_into_history(history, quote)

    assert len(merged) == 2
    latest = merged.iloc[-1]
    assert str(latest["trade_date"].date()) == "2026-07-13"
    assert latest["close"] == 905


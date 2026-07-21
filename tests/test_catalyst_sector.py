from __future__ import annotations

# 註：ETF/ETN 排除在產業共振之外的過濾發生在 run_live_screener 組
# symbol_industries 時；compute_sector_resonance 本身只處理收到的產業對應。

from datetime import date

import pandas as pd

from taiwan_stock_screener.catalysts.events import (
    CatalystEvent,
    events_from_conference_rows,
    nearest_catalyst_payload,
    parse_roc_or_iso_date,
    trading_days_between,
)
from taiwan_stock_screener.sector.resonance import compute_sector_resonance


def test_trading_days_between_uses_weekdays() -> None:
    assert trading_days_between(date(2026, 7, 17), date(2026, 7, 20)) == 1


def test_nearest_catalyst_payload_marks_window() -> None:
    payload = nearest_catalyst_payload(
        symbol="2330",
        events=[
            CatalystEvent("2330", "法說會", date(2026, 7, 24)),
            CatalystEvent("2330", "除權息", date(2026, 8, 20)),
        ],
        today=date(2026, 7, 16),
        lookahead_trading_days=10,
    )

    assert payload["nearest_catalyst_type"] == "法說會"
    assert payload["catalyst_available"]
    assert payload["catalyst_in_window"]


def test_sector_resonance_flags_top_rank_and_jump() -> None:
    quotes = pd.DataFrame(
        [
            {"symbol": "2330", "turnover": 900},
            {"symbol": "2303", "turnover": 100},
            {"symbol": "2881", "turnover": 100},
            {"symbol": "1303", "turnover": 100},
        ]
    )
    previous = [
        {
            "date": "2026-07-15",
            "sectors": [
                {"industry": "半導體", "sector_turnover_share_pct": 40},
                {"industry": "金融保險", "sector_turnover_share_pct": 30},
            ],
        }
    ]

    payloads, snapshot = compute_sector_resonance(
        quotes,
        {
            "2330": "半導體",
            "2303": "半導體",
            "2881": "金融保險",
            "1303": "塑膠工業",
        },
        previous_entries=previous,
        rank_threshold_pct=40,
        jump_threshold_pct=50,
    )

    assert snapshot
    assert payloads["2330"]["sector_turnover_rank_pct"] <= 40
    assert payloads["2330"]["sector_turnover_jump_pct"] >= 50
    assert payloads["2330"]["sector_resonance_active"]



def test_parse_roc_or_iso_date_variants() -> None:
    assert parse_roc_or_iso_date("2026-07-25") == date(2026, 7, 25)
    assert parse_roc_or_iso_date("115/07/25") == date(2026, 7, 25)  # 民國年
    assert parse_roc_or_iso_date("1150725") == date(2026, 7, 25)  # 民國 YYYMMDD
    assert parse_roc_or_iso_date("20260725") == date(2026, 7, 25)
    assert parse_roc_or_iso_date("") is None
    assert parse_roc_or_iso_date("不適用") is None


def test_events_from_conference_rows_picks_symbol_and_date() -> None:
    today = date(2026, 7, 21)
    rows = [
        {"出表日期": "1150721", "公司代號": "2330", "公司名稱": "台積電", "召開法人說明會日期": "1150725"},
        # 已過期的事件要被濾掉
        {"出表日期": "1150721", "公司代號": "2317", "公司名稱": "鴻海", "召開法人說明會日期": "1150701"},
        # 超過 60 天的未來事件也濾掉
        {"出表日期": "1150721", "公司代號": "2454", "公司名稱": "聯發科", "召開法人說明會日期": "1151101"},
        # 同一檔同日期去重
        {"出表日期": "1150721", "公司代號": "2330", "公司名稱": "台積電", "召開法人說明會日期": "1150725"},
    ]
    events = events_from_conference_rows(rows, today=today)
    assert len(events) == 1
    assert events[0].symbol == "2330"
    assert events[0].event_type == "法說會"
    assert events[0].event_date == date(2026, 7, 25)
    assert events_from_conference_rows("not-a-list", today=today) == []

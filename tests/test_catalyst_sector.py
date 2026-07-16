from __future__ import annotations

from datetime import date

import pandas as pd

from taiwan_stock_screener.catalysts.events import (
    CatalystEvent,
    nearest_catalyst_payload,
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


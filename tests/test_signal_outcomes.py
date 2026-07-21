from __future__ import annotations

from scripts.analyze_signal_outcomes import analyze_outcomes, extract_signal_events


def test_signal_outcomes_use_quote_date_and_reasons() -> None:
    history = [
        {
            "date": "2026-07-18",  # Saturday workflow run
            "candidates": [
                {
                    "symbol": "1111",
                    "name": "Alpha",
                    "quote_date": "2026-07-17",
                    "entry_price": 100,
                    "total_score": 82,
                    "reasons": ["relative_strength_20d"],
                },
                {
                    "symbol": "2222",
                    "name": "Weekend stale",
                    "entry_price": 50,
                    "total_score": 75,
                },
            ],
        },
        {
            "date": "2026-07-20",
            "candidates": [
                {
                    "symbol": "1111",
                    "name": "Alpha",
                    "quote_date": "2026-07-20",
                    "entry_price": 110,
                    "total_score": 86,
                    "reasons": ["relative_strength_20d"],
                }
            ],
        },
    ]

    events = extract_signal_events(history)

    assert [(event.symbol, event.event_date) for event in events] == [
        ("1111", "2026-07-17"),
        ("1111", "2026-07-20"),
    ]
    assert "reason:relative_strength_20d" in events[0].tags
    assert "score>=80" in events[0].tags


def test_signal_outcome_summary_counts_future_returns() -> None:
    history = [
        {
            "date": "2026-07-13",
            "left_side_candidates": [
                {
                    "symbol": "1111",
                    "name": "Alpha",
                    "entry_price": 100,
                    "total_score": 55,
                    "score_delta": 22,
                    "short_balance_change_pct": -30,
                    "margin_balance_change_pct": -20,
                }
            ],
        },
        {
            "date": "2026-07-14",
            "left_side_candidates": [
                {
                    "symbol": "1111",
                    "name": "Alpha",
                    "entry_price": 112,
                    "total_score": 70,
                    "score_delta": 15,
                    "short_balance_change_pct": -10,
                    "margin_balance_change_pct": -5,
                }
            ],
        },
    ]

    events = extract_signal_events(history)
    report = analyze_outcomes(events, horizons=(1,), min_samples=1)
    summaries = {row["tag"]: row for row in report["feature_summaries"]}

    assert summaries["strategy:left_side"]["sample_count"] == 1
    assert summaries["strategy:left_side"]["avg_return_pct"] == 12.0
    assert summaries["score_delta>=20"]["avg_return_pct"] == 12.0
    assert summaries["deep_washout_proxy"]["win_rate_pct"] == 100.0

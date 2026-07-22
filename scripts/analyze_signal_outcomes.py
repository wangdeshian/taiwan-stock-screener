from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY = ROOT / "frontend" / "data" / "history.json"
DEFAULT_OUTPUT = ROOT / "frontend" / "data" / "signal_backtest.json"
DEFAULT_HORIZONS = (1, 2, 3, 5)


@dataclass(frozen=True)
class SignalEvent:
    run_date: str
    event_date: str
    symbol: str
    name: str
    strategy: str
    price: float
    score: float | None
    tags: tuple[str, ...]
    raw: dict[str, Any]


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def is_weekday(date_text: str) -> bool:
    parsed = parse_date(date_text)
    return bool(parsed and parsed.weekday() < 5)


def safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def candidate_price(candidate: dict[str, Any]) -> float | None:
    return safe_float(candidate.get("entry_price") or candidate.get("close_price"))


def candidate_event_date(run_date: str, candidate: dict[str, Any]) -> str:
    quote_date = candidate.get("quote_date")
    parsed = parse_date(quote_date)
    return parsed.isoformat() if parsed else run_date


def score_bucket(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 80:
        return "score>=80"
    if score >= 70:
        return "score70-80"
    if score >= 60:
        return "score60-70"
    if score >= 50:
        return "score50-60"
    return "score<50"


def signal_tags(candidate: dict[str, Any], strategy: str) -> tuple[str, ...]:
    tags: list[str] = [f"strategy:{strategy}"]
    score = safe_float(candidate.get("total_score"))
    bucket = score_bucket(score)
    if bucket:
        tags.append(bucket)

    for reason in candidate.get("reasons") or []:
        tags.append(f"reason:{reason}")
    for signal in candidate.get("technical_signals") or []:
        signal_id = signal.get("id") if isinstance(signal, dict) else None
        if signal_id:
            tags.append(f"tech:{signal_id}")
            direction = signal.get("direction")
            if direction:
                tags.append(f"tech_direction:{direction}")
    for signal in candidate.get("chip_signals") or []:
        signal_id = signal.get("id") if isinstance(signal, dict) else None
        if signal_id:
            tags.append(f"chip:{signal_id}")
            direction = signal.get("direction")
            if direction:
                tags.append(f"chip_direction:{direction}")

    score_delta = safe_float(candidate.get("score_delta"))
    if score_delta is not None:
        if score_delta >= 20:
            tags.append("score_delta>=20")
        elif score_delta >= 10:
            tags.append("score_delta10-20")

    short_change = safe_float(candidate.get("short_balance_change_pct"))
    margin_change = safe_float(candidate.get("margin_balance_change_pct"))
    if short_change is not None:
        if short_change <= -20:
            tags.append("short_drop>=20")
        if short_change <= -15:
            tags.append("short_covering_proxy")
    if margin_change is not None:
        if margin_change <= -12:
            tags.append("margin_drop>=12")
        if margin_change <= -10:
            tags.append("margin_flush_proxy")
    if short_change is not None and margin_change is not None:
        if short_change <= -20 and margin_change <= -12:
            tags.append("deep_washout_proxy")

    if safe_float(candidate.get("sector_resonance_score")) and safe_float(candidate.get("sector_resonance_score")) > 0:
        tags.append("sector_resonance>0")
    if safe_float(candidate.get("microstructure_score")) and safe_float(candidate.get("microstructure_score")) > 0:
        tags.append("microstructure>0")
    if safe_float(candidate.get("relative_strength_20d_pct")) and safe_float(candidate.get("relative_strength_20d_pct")) > 0:
        tags.append("rs20>0")
    if safe_float(candidate.get("relative_strength_60d_pct")) and safe_float(candidate.get("relative_strength_60d_pct")) > 0:
        tags.append("rs60>0")
    peg = safe_float(candidate.get("peg_ratio"))
    if peg is not None and peg < 1:
        tags.append("peg<1")
    roe = safe_float(candidate.get("roe_pct"))
    if roe is not None and roe >= 10:
        tags.append("roe>=10")
    return tuple(dict.fromkeys(tags))


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def extract_signal_events(entries: list[dict[str, Any]]) -> list[SignalEvent]:
    events: list[SignalEvent] = []
    for entry in entries:
        run_date = str(entry.get("date") or str(entry.get("updated_at") or "")[:10])
        if not parse_date(run_date):
            continue
        for strategy, key in (("momentum", "candidates"), ("momentum", "top_candidates"), ("left_side", "left_side_candidates")):
            for candidate in entry.get(key) or []:
                symbol = str(candidate.get("symbol") or "").strip()
                price = candidate_price(candidate)
                if not symbol or price is None or price <= 0:
                    continue
                event_date = candidate_event_date(run_date, candidate)
                if candidate.get("quote_date") is None and not is_weekday(event_date):
                    continue
                events.append(
                    SignalEvent(
                        run_date=run_date,
                        event_date=event_date,
                        symbol=symbol,
                        name=str(candidate.get("name") or symbol),
                        strategy=strategy,
                        price=price,
                        score=safe_float(candidate.get("total_score")),
                        tags=signal_tags(candidate, strategy),
                        raw=candidate,
                    )
                )
    unique: dict[tuple[str, str, str], SignalEvent] = {}
    for event in events:
        key = (event.event_date, event.symbol, event.strategy)
        current = unique.get(key)
        if current is None or (event.score or -1) > (current.score or -1):
            unique[key] = event
    return sorted(unique.values(), key=lambda item: (item.event_date, item.symbol, item.strategy))


def build_price_observations(events: list[SignalEvent]) -> dict[str, dict[str, float]]:
    prices: dict[str, dict[str, float]] = defaultdict(dict)
    for event in events:
        prices[event.symbol][event.event_date] = event.price
    return prices


def summarize(values: list[float]) -> dict[str, float | int]:
    return {
        "sample_count": len(values),
        "avg_return_pct": round(sum(values) / len(values), 2),
        "median_return_pct": round(statistics.median(values), 2),
        "win_rate_pct": round(sum(1 for value in values if value > 0) / len(values) * 100, 1),
        "best_return_pct": round(max(values), 2),
        "worst_return_pct": round(min(values), 2),
    }


def analyze_outcomes(
    events: list[SignalEvent],
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    min_samples: int = 3,
) -> dict[str, Any]:
    dates = sorted({event.event_date for event in events if is_weekday(event.event_date)})
    date_index = {event_date: index for index, event_date in enumerate(dates)}
    prices = build_price_observations(events)
    grouped_returns: dict[tuple[str, int], list[float]] = defaultdict(list)
    eligible_counts: dict[tuple[str, int], int] = defaultdict(int)
    examples: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)

    for event in events:
        if event.event_date not in date_index:
            continue
        index = date_index[event.event_date]
        for horizon in horizons:
            if index + horizon >= len(dates):
                continue
            target_date = dates[index + horizon]
            for tag in event.tags:
                eligible_counts[(tag, horizon)] += 1
            future_price = prices.get(event.symbol, {}).get(target_date)
            if future_price is None:
                continue
            return_pct = (future_price / event.price - 1) * 100
            for tag in event.tags:
                key = (tag, horizon)
                grouped_returns[key].append(return_pct)
                if len(examples[key]) < 5:
                    examples[key].append(
                        {
                            "date": event.event_date,
                            "target_date": target_date,
                            "symbol": event.symbol,
                            "name": event.name,
                            "return_pct": round(return_pct, 2),
                        }
                    )

    summaries: list[dict[str, Any]] = []
    for (tag, horizon), returns in grouped_returns.items():
        if len(returns) < min_samples:
            continue
        eligible = eligible_counts[(tag, horizon)]
        row = {
            "tag": tag,
            "horizon_observed_days": horizon,
            "eligible_event_count": eligible,
            "coverage_pct": round(len(returns) / eligible * 100, 1) if eligible else 0.0,
            **summarize(returns),
            "examples": examples[(tag, horizon)],
        }
        summaries.append(row)

    summaries.sort(
        key=lambda row: (
            row["horizon_observed_days"],
            -float(row["win_rate_pct"]),
            -float(row["avg_return_pct"]),
            -int(row["sample_count"]),
        )
    )
    return {
        "observed_dates": dates,
        "event_count": len(events),
        "unique_symbol_count": len({event.symbol for event in events}),
        "horizons": list(horizons),
        "min_samples": min_samples,
        "feature_summaries": summaries,
    }


def current_candidate_matches(events: list[SignalEvent], summaries: list[dict[str, Any]], horizon: int = 3) -> list[dict[str, Any]]:
    if not events:
        return []
    latest_date = max(event.event_date for event in events)
    by_tag = {
        row["tag"]: row
        for row in summaries
        if int(row["horizon_observed_days"]) == horizon and int(row["sample_count"]) >= 3
    }
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.event_date != latest_date:
            continue
        matched = [by_tag[tag] for tag in event.tags if tag in by_tag]
        matched.sort(key=lambda row: (float(row["win_rate_pct"]), float(row["avg_return_pct"])), reverse=True)
        rows.append(
            {
                "symbol": event.symbol,
                "name": event.name,
                "strategy": event.strategy,
                "total_score": event.score,
                "matched_evidence": [
                    {
                        "tag": row["tag"],
                        "sample_count": row["sample_count"],
                        "avg_return_pct": row["avg_return_pct"],
                        "median_return_pct": row["median_return_pct"],
                        "win_rate_pct": row["win_rate_pct"],
                    }
                    for row in matched[:5]
                ],
            }
        )
    rows.sort(
        key=lambda row: (
            max((item["win_rate_pct"] for item in row["matched_evidence"]), default=0),
            max((item["avg_return_pct"] for item in row["matched_evidence"]), default=-999),
        ),
        reverse=True,
    )
    return rows


def build_report(history_path: Path, min_samples: int) -> dict[str, Any]:
    entries = load_history(history_path)
    events = extract_signal_events(entries)
    analysis = analyze_outcomes(events, min_samples=min_samples)
    latest_matches = current_candidate_matches(events, analysis["feature_summaries"])
    date_count = len({entry.get("date") for entry in entries if entry.get("date")})
    confidence = "low" if date_count < 20 or analysis["event_count"] < 500 else "medium"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_path": str(history_path.relative_to(ROOT) if history_path.is_relative_to(ROOT) else history_path),
        "confidence": confidence,
        "data_window": {
            "history_entry_count": len(entries),
            "history_date_count": date_count,
            "observed_trade_dates": analysis["observed_dates"],
            "event_count": analysis["event_count"],
            "unique_symbol_count": analysis["unique_symbol_count"],
        },
        "methodology": {
            "price_field": "entry_price as close proxy; quote_date is used when available",
            "horizons": analysis["horizons"],
            "min_samples": min_samples,
            "coverage": "A future return is counted only when the same symbol has an observed price on the target observed date.",
        },
        "limitations": [
            "Current history is a candidate-only event log, not a full-market price panel.",
            "Older history rows may not include quote_date or reasons, so signal attribution is incomplete.",
            "Weekend or stale workflow rows are skipped when no quote_date is available.",
            "Treat early results as calibration hints, not a trading prediction model.",
        ],
        "feature_summaries": analysis["feature_summaries"],
        "latest_candidate_matches": latest_matches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze future returns after screener signals.")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-samples", type=int, default=3)
    args = parser.parse_args()

    report = build_report(args.history, args.min_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Signal outcome report written: {args.output}")
    print(
        "Window:",
        report["data_window"]["history_date_count"],
        "history dates,",
        report["data_window"]["event_count"],
        "events, confidence",
        report["confidence"],
    )
    for row in report["feature_summaries"][:12]:
        print(
            f"H{row['horizon_observed_days']} {row['tag']}: "
            f"n={row['sample_count']} avg={row['avg_return_pct']}% "
            f"median={row['median_return_pct']}% win={row['win_rate_pct']}%"
        )


if __name__ == "__main__":
    main()

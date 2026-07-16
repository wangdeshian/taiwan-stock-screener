from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CatalystEvent:
    symbol: str
    event_type: str
    event_date: date


def load_catalyst_events(path: Path) -> list[CatalystEvent]:
    """讀取本機催化事件清單。

    檔案不存在或格式不完整時回傳空清單，避免每日 workflow 因外部資料缺口失敗。
    預期 JSON 格式：
    [{"symbol": "2330", "event_type": "法說會", "event_date": "2026-07-25"}]
    """
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        return []

    events: list[CatalystEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).strip()
        event_type = str(row.get("event_type", "")).strip()
        event_date = parse_event_date(row.get("event_date"))
        if symbol and event_type and event_date:
            events.append(CatalystEvent(symbol=symbol, event_type=event_type, event_date=event_date))
    return events


def parse_event_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def trading_days_between(start: date, end: date) -> int:
    """用週一至週五估算交易日距離；台灣休市日後續可再接交易所行事曆修正。"""
    if end < start:
        return -1
    days = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def nearest_catalyst_payload(
    symbol: str,
    events: list[CatalystEvent],
    today: date,
    lookahead_trading_days: int,
) -> dict[str, Any]:
    """挑出指定股票最近的未來催化事件，並輸出前端可直接使用的欄位。"""
    future_events: list[tuple[int, CatalystEvent]] = []
    for event in events:
        if event.symbol != str(symbol):
            continue
        days_left = trading_days_between(today, event.event_date)
        if days_left < 0:
            continue
        future_events.append((days_left, event))

    if not future_events:
        return {
            "nearest_catalyst_type": None,
            "nearest_catalyst_date": None,
            "catalyst_days_left": None,
            "catalyst_available": False,
            "catalyst_in_window": False,
        }

    days_left, event = min(future_events, key=lambda item: item[0])
    return {
        "nearest_catalyst_type": event.event_type,
        "nearest_catalyst_date": event.event_date.isoformat(),
        "catalyst_days_left": days_left,
        "catalyst_available": True,
        "catalyst_in_window": days_left <= lookahead_trading_days,
    }


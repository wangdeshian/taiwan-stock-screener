"""資料模型與顯示格式化。純標準函式庫，不碰 GUI，方便測試。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class QuotaWindow:
    """單一額度區間，例如 Codex 的 5 小時窗、Gemini 的每日請求數。"""

    label: str
    used_percent: float | None = None
    detail: str | None = None
    resets_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.used_percent is not None:
            self.used_percent = min(max(float(self.used_percent), 0.0), 100.0)

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is None:
            return None
        return 100.0 - self.used_percent


@dataclass
class ProviderSnapshot:
    """一家供應商的即時狀態。抓不到資料時 available=False，不填任何數字。"""

    id: str
    display_name: str
    short_name: str
    available: bool
    windows: list[QuotaWindow] = field(default_factory=list)
    note: str | None = None

    @property
    def primary_window(self) -> QuotaWindow | None:
        for window in self.windows:
            if window.used_percent is not None:
                return window
        return self.windows[0] if self.windows else None

    @property
    def remaining_percent(self) -> float | None:
        window = self.primary_window
        return window.remaining_percent if window else None


def unavailable(
    provider_id: str, display_name: str, short_name: str, reason: str
) -> ProviderSnapshot:
    return ProviderSnapshot(
        id=provider_id,
        display_name=display_name,
        short_name=short_name,
        available=False,
        windows=[],
        note=reason,
    )


def format_tokens(count: int) -> str:
    value = float(count)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(count)


def format_countdown(target: datetime, now: datetime) -> str:
    seconds = int(round((target - now).total_seconds()))
    if seconds <= 0:
        return "即將重置"
    if seconds < 60:
        return f"{seconds} 秒後重置"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分後重置"
    hours = minutes // 60
    if hours < 24:
        remainder = minutes % 60
        return f"{hours} 小時後重置" if remainder == 0 else f"{hours} 小時 {remainder} 分後重置"
    days = hours // 24
    return f"{days} 天 {hours % 24} 小時後重置"


def format_window_label(minutes: int) -> str:
    """由分鐘數推回窗口名稱，避免各家用不同單位時標籤不一致。"""
    if minutes < 60:
        return f"{minutes} 分鐘"
    if minutes == 10_080:
        return "每週"
    if minutes == 1_440:
        return "每日"
    if minutes % 1_440 == 0:
        return f"{minutes // 1_440} 天"
    if minutes % 60 == 0:
        return f"{minutes // 60} 小時"
    return f"{minutes} 分鐘"


def parse_iso(text: str | None) -> datetime | None:
    """解析 ISO8601。三家寫出來的格式不完全一樣，統一轉成帶時區的 datetime。"""
    if not text or not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # 沒帶時區的一律當本機時間，這是各家 CLI 寫本機 log 時的慣例。
        return parsed.astimezone()
    return parsed


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def seconds_from_now(now: datetime, seconds: float) -> datetime:
    return now + timedelta(seconds=seconds)

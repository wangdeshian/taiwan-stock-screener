"""三家 CLI 的本機用量讀取。

共同規則：**抓不到資料就回報不可用，絕不補一個看起來合理的數字。**
額度顯示器最糟的失敗方式是明明沒讀到卻顯示 100%，你信了它然後撞到限制。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import AppConfig, ClaudeConfig, CodexConfig, GeminiConfig, resolve_dirs
from .models import (
    ProviderSnapshot,
    QuotaWindow,
    format_countdown,  # noqa: F401  (re-export 給 probe 用)
    format_tokens,
    format_window_label,
    parse_iso,
    unavailable,
)


@dataclass
class ScannedFile:
    path: Path
    size: int
    modified: float


def scan_files(
    roots: list[Path],
    suffix: str | None = None,
    name: str | None = None,
    modified_after: float | None = None,
) -> list[ScannedFile]:
    """遞迴列出檔案，依修改時間由新到舊排序。

    用 os.walk 而不是 Path.rglob：權限錯誤或 WSL 路徑斷線時要能跳過而不是整個炸掉。
    """
    found: list[ScannedFile] = []
    for root in roots:
        try:
            walker = os.walk(root, onerror=lambda _err: None)
            for dirpath, _dirnames, filenames in walker:
                for filename in filenames:
                    if suffix and not filename.endswith(suffix):
                        continue
                    if name and filename != name:
                        continue
                    full = Path(dirpath) / filename
                    try:
                        stat = full.stat()
                    except OSError:
                        continue
                    if modified_after is not None and stat.st_mtime < modified_after:
                        continue
                    found.append(ScannedFile(full, stat.st_size, stat.st_mtime))
        except OSError:
            continue

    found.sort(key=lambda item: item.modified, reverse=True)
    return found


def read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def find_nested(value: object, keys: tuple[str, ...], depth: int = 12) -> dict | None:
    """在巢狀 JSON 裡深度優先找第一個叫這些名字的字典。

    Codex 的 `rate_limits` 有時在頂層、有時包在 `payload` 底下，靠這個吸收版本差異，
    而不是寫死路徑等它改版壞掉。
    """
    if depth <= 0:
        return None
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, dict):
                return nested
        for nested in value.values():
            hit = find_nested(nested, keys, depth - 1)
            if hit is not None:
                return hit
        return None
    if isinstance(value, list):
        for element in value:
            hit = find_nested(element, keys, depth - 1)
            if hit is not None:
                return hit
    return None


def pick(mapping: dict, *keys: str):
    """取第一個存在的 key，同時支援 snake_case 與 camelCase。"""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_int(value: object) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


class CodexProvider:
    """讀 Codex CLI 寫在 `~/.codex/sessions/**/*.jsonl` 的 rate_limits。

    這是伺服器回傳的官方數字，準確。缺點是「最後一次呼叫模型時的快照」——
    你多久沒用 Codex，數字就多久沒更新，這是本機讀取的先天限制。
    """

    id = "codex"
    display_name = "Codex"
    short_name = "CX"

    def __init__(self, config: CodexConfig, roots: list[Path]) -> None:
        self.config = config
        self.roots = roots

    @property
    def directories(self) -> list[Path]:
        return resolve_dirs(self.config.sessions_dir, ".codex/sessions", self.roots)

    def snapshot(self, now: datetime) -> ProviderSnapshot:
        directories = self.directories
        if not directories:
            where = self.config.sessions_dir or "、".join(
                str(root / ".codex" / "sessions") for root in self.roots
            )
            return unavailable(self.id, self.display_name, self.short_name, f"找不到目錄：{where}")

        files = scan_files(directories, suffix=".jsonl")
        if not files:
            return unavailable(
                self.id, self.display_name, self.short_name, "目錄裡沒有 session 記錄"
            )

        found = self._latest_rate_limits(files)
        if found is None:
            return unavailable(
                self.id,
                self.display_name,
                self.short_name,
                "session 裡還沒有 rate_limits 事件（先跑一次 codex 再看）",
            )

        limits, source = found
        windows = self._windows(limits, now)
        if not windows:
            return unavailable(
                self.id,
                self.display_name,
                self.short_name,
                "讀到 rate_limits 但沒有可用欄位，請跑 --probe 檢查格式",
            )

        return ProviderSnapshot(
            id=self.id,
            display_name=self.display_name,
            short_name=self.short_name,
            available=True,
            windows=windows,
            note=f"來源：{source.name}",
        )

    def _latest_rate_limits(self, files: list[ScannedFile]) -> tuple[dict, Path] | None:
        found = self._latest_rate_limits_raw(files)
        if found is None:
            return None
        limits, path, _raw = found
        return limits, path

    def _latest_rate_limits_raw(
        self, files: list[ScannedFile]
    ) -> tuple[dict, Path, str] | None:
        for item in files[: max(1, self.config.max_files_to_scan)]:
            for line in reversed(read_lines(item.path)):
                # 先做便宜的字串比對再解析 JSON；兩種命名都要接受。
                if "rate_limit" not in line and "rateLimit" not in line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                limits = find_nested(payload, ("rate_limits", "rateLimits"))
                if limits:
                    return limits, item.path, line
        return None

    def diagnostics(self) -> dict:
        """給 --probe 用：把實際找到的 rate_limits 原始 JSON 交出來。

        Codex 的欄位會隨版本改，只印「最後一行」通常抓不到那筆事件，
        必須把真正命中的那一行印出來才看得出格式對不對。
        """
        files = scan_files(self.directories, suffix=".jsonl")
        found = self._latest_rate_limits_raw(files)
        if found is None:
            return {"files": len(files), "matched": False}
        limits, path, raw = found
        return {
            "files": len(files),
            "matched": True,
            "file": path.name,
            "keys": sorted(limits),
            "limits_json": json.dumps(limits, ensure_ascii=False, sort_keys=True),
            "raw_head": raw[:300],
        }

    @staticmethod
    def _window(entry: dict, now: datetime, fallback_label: str) -> QuotaWindow | None:
        used = as_float(pick(entry, "used_percent", "usedPercent", "percent_used", "percentUsed"))
        if used is None:
            return None

        minutes = as_int(pick(entry, "window_minutes", "windowMinutes"))
        label = format_window_label(minutes) if minutes else fallback_label

        resets_at = None
        seconds = as_float(pick(entry, "resets_in_seconds", "resetsInSeconds"))
        if seconds is not None:
            resets_at = now + timedelta(seconds=seconds)
        else:
            resets_at = parse_iso(pick(entry, "resets_at", "resetsAt"))

        return QuotaWindow(
            label=label,
            used_percent=used,
            detail=f"已用 {used:.1f}%",
            resets_at=resets_at,
        )

    @classmethod
    def _windows(cls, limits: dict, now: datetime) -> list[QuotaWindow]:
        windows: list[QuotaWindow] = []
        # 順序固定，讓 5 小時窗永遠排在週窗前面。
        for key in ("primary", "secondary"):
            entry = limits.get(key)
            if isinstance(entry, dict):
                window = cls._window(entry, now, key)
                if window:
                    windows.append(window)

        # 未來若改名，把剩下的字典也撈進來，至少不會整個空掉。
        if not windows:
            for key in sorted(limits):
                entry = limits[key]
                if isinstance(entry, dict):
                    window = cls._window(entry, now, key)
                    if window:
                        windows.append(window)
        return windows


@dataclass
class UsageEntry:
    when: datetime
    key: str
    total: int


class ClaudeProvider:
    """由 Claude Code 的對話記錄統計 token 用量。

    重要前提：Claude Code **沒有**把官方額度百分比寫在本機檔案裡，`/usage` 的數字來自
    伺服器。所以這裡算的是「本機記錄的 token 用量」，不是官方剩餘額度。沒設 token 預算時
    只顯示 token 數，不換算百分比。
    """

    id = "claude"
    display_name = "Claude"
    short_name = "CL"

    def __init__(self, config: ClaudeConfig, roots: list[Path]) -> None:
        self.config = config
        self.roots = roots
        # 以 (大小, mtime) 當快取鍵：jsonl 只會往後追加，沒變動就不重解析。
        self._cache: dict[str, tuple[int, float, list[UsageEntry]]] = {}

    @property
    def directories(self) -> list[Path]:
        return resolve_dirs(self.config.projects_dir, ".claude/projects", self.roots)

    def snapshot(self, now: datetime) -> ProviderSnapshot:
        directories = self.directories
        if not directories:
            where = self.config.projects_dir or "、".join(
                str(root / ".claude" / "projects") for root in self.roots
            )
            return unavailable(self.id, self.display_name, self.short_name, f"找不到目錄：{where}")

        # 只看最近 8 天改過的檔案：週窗需要 7 天，多留一天緩衝。
        cutoff = (now - timedelta(days=8)).timestamp()
        files = scan_files(directories, suffix=".jsonl", modified_after=cutoff)
        if not files:
            return unavailable(
                self.id, self.display_name, self.short_name, "最近 8 天沒有對話記錄"
            )

        entries = self._load_entries(files)
        if not entries:
            return unavailable(
                self.id,
                self.display_name,
                self.short_name,
                "讀到記錄檔但沒有 usage 欄位，請跑 --probe 檢查格式",
            )

        window_hours = max(0.5, float(self.config.session_window_hours))
        session_start = now - timedelta(hours=window_hours)
        week_start = now - timedelta(days=7)

        session_entries = [entry for entry in entries if entry.when >= session_start]
        week_entries = [entry for entry in entries if entry.when >= week_start]
        session_tokens = sum(entry.total for entry in session_entries)
        week_tokens = sum(entry.total for entry in week_entries)

        # 滾動窗的重置點 = 最舊那筆滑出窗口的時間。
        session_resets = None
        if session_entries:
            session_resets = min(entry.when for entry in session_entries) + timedelta(
                hours=window_hours
            )

        windows = [
            QuotaWindow(
                label=format_window_label(int(window_hours * 60)),
                used_percent=_percent(session_tokens, self.config.session_token_budget),
                detail=f"{format_tokens(session_tokens)} tokens · {len(session_entries)} 則回應",
                resets_at=session_resets,
            ),
            QuotaWindow(
                label="最近 7 天",
                used_percent=_percent(week_tokens, self.config.weekly_token_budget),
                detail=f"{format_tokens(week_tokens)} tokens · {len(week_entries)} 則回應",
            ),
        ]

        if self.config.session_token_budget is None:
            note = "本機 token 統計（非官方額度）；設定 session_token_budget 才會顯示百分比"
        else:
            note = "本機 token 統計（非官方額度），百分比以自訂預算換算"

        return ProviderSnapshot(
            id=self.id,
            display_name=self.display_name,
            short_name=self.short_name,
            available=True,
            windows=windows,
            note=note,
        )

    def _load_entries(self, files: list[ScannedFile]) -> list[UsageEntry]:
        seen: set[str] = set()
        merged: list[UsageEntry] = []
        next_cache: dict[str, tuple[int, float, list[UsageEntry]]] = {}

        for item in files:
            key = str(item.path)
            cached = self._cache.get(key)
            if cached and cached[0] == item.size and cached[1] == item.modified:
                entries = cached[2]
            else:
                entries = self.parse_entries(read_lines(item.path))
            next_cache[key] = (item.size, item.modified, entries)

            for entry in entries:
                if entry.key in seen:
                    continue
                seen.add(entry.key)
                merged.append(entry)

        self._cache = next_cache
        return merged

    @staticmethod
    def parse_entries(lines: list[str]) -> list[UsageEntry]:
        return ClaudeProvider.parse_entries_with_stats(lines)[0]

    @staticmethod
    def parse_entries_with_stats(lines: list[str]) -> tuple[list[UsageEntry], dict[str, int]]:
        """解析並回報每一層丟掉了幾行。

        `stats` 是給 `--probe` 用的：記錄數字看起來不對時，可以直接指出是哪一關過濾掉的，
        而不用猜。
        """
        stats = {
            "lines": len(lines),
            "with_usage_marker": 0,
            "json_error": 0,
            "no_message_usage": 0,
            "zero_tokens": 0,
            "no_timestamp": 0,
            "accepted": 0,
        }
        entries: list[UsageEntry] = []
        for line in lines:
            # 先做便宜的字串檢查，避免對每一行都做 JSON 解析。
            if '"usage"' not in line:
                continue
            stats["with_usage_marker"] += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                stats["json_error"] += 1
                continue
            if not isinstance(payload, dict):
                stats["json_error"] += 1
                continue

            message = payload.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                stats["no_message_usage"] += 1
                continue

            total = 0
            for field_names in (
                ("input_tokens", "inputTokens"),
                ("output_tokens", "outputTokens"),
                ("cache_creation_input_tokens", "cacheCreationInputTokens"),
                ("cache_read_input_tokens", "cacheReadInputTokens"),
            ):
                total += as_int(pick(usage, *field_names)) or 0
            if total == 0:
                stats["zero_tokens"] += 1
                continue

            when = parse_iso(payload.get("timestamp"))
            if when is None:
                stats["no_timestamp"] += 1
                continue

            # 同一則回應可能因為 resume 或複製專案而出現在多個檔案，用 id + requestId 去重。
            message_id = message.get("id") or ""
            request_id = pick(payload, "requestId", "request_id") or ""
            if message_id or request_id:
                dedupe_key = f"{message_id}|{request_id}"
            else:
                dedupe_key = f"{when.timestamp()}|{total}"

            stats["accepted"] += 1
            entries.append(UsageEntry(when=when, key=dedupe_key, total=total))
        return entries, stats

    def diagnostics(self, now: datetime) -> dict:
        """給 --probe 用：逐檔統計，指出記錄在哪一層被過濾掉。"""
        directories = self.directories
        cutoff = (now - timedelta(days=8)).timestamp()
        files = scan_files(directories, suffix=".jsonl", modified_after=cutoff)
        all_files = scan_files(directories, suffix=".jsonl")

        totals = {
            "lines": 0,
            "with_usage_marker": 0,
            "json_error": 0,
            "no_message_usage": 0,
            "zero_tokens": 0,
            "no_timestamp": 0,
            "accepted": 0,
        }
        entries: list[UsageEntry] = []
        for item in files:
            parsed, stats = self.parse_entries_with_stats(read_lines(item.path))
            for key, value in stats.items():
                totals[key] += value
            entries.extend(parsed)

        unique: dict[str, UsageEntry] = {}
        for entry in entries:
            unique.setdefault(entry.key, entry)
        deduped = list(unique.values())

        return {
            "files_total": len(all_files),
            "files_recent": len(files),
            "files_skipped_by_age": len(all_files) - len(files),
            "stats": totals,
            "unique_entries": len(deduped),
            "duplicates_removed": len(entries) - len(deduped),
            "oldest": min((e.when for e in deduped), default=None),
            "newest": max((e.when for e in deduped), default=None),
        }


class GeminiProvider:
    """由 Gemini CLI 的 `~/.gemini/tmp/<hash>/logs.json` 統計請求數。

    免費層是「每分鐘 / 每日請求數」制，所以這裡算請求筆數而不是 token。
    """

    id = "gemini"
    display_name = "Gemini"
    short_name = "GM"

    def __init__(self, config: GeminiConfig, roots: list[Path]) -> None:
        self.config = config
        self.roots = roots

    @property
    def directories(self) -> list[Path]:
        return resolve_dirs(self.config.logs_root, ".gemini/tmp", self.roots)

    def snapshot(self, now: datetime) -> ProviderSnapshot:
        directories = self.directories
        if not directories:
            where = self.config.logs_root or "、".join(
                str(root / ".gemini" / "tmp") for root in self.roots
            )
            return unavailable(self.id, self.display_name, self.short_name, f"找不到目錄：{where}")

        files = scan_files(directories, name="logs.json")
        if not files:
            return unavailable(
                self.id, self.display_name, self.short_name, "目錄下沒有 logs.json"
            )

        stamps: list[datetime] = []
        for item in files:
            stamps.extend(self.user_message_times(item.path))
        if not stamps:
            return unavailable(
                self.id, self.display_name, self.short_name, "logs.json 裡沒有可用的請求記錄"
            )

        zone, zone_note = _load_zone(self.config.daily_reset_timezone)
        local_now = now.astimezone(zone)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)

        day_count = sum(1 for stamp in stamps if day_start <= stamp.astimezone(zone) <= local_now)
        minute_count = sum(1 for stamp in stamps if now - timedelta(seconds=60) <= stamp <= now)

        windows = [
            QuotaWindow(
                label="今日請求",
                used_percent=_percent(day_count, self.config.daily_request_limit),
                detail=f"{day_count} / {self.config.daily_request_limit} 次",
                resets_at=next_day,
            ),
            QuotaWindow(
                label="每分鐘",
                used_percent=_percent(minute_count, self.config.minute_request_limit),
                detail=f"{minute_count} / {self.config.minute_request_limit} 次",
            ),
        ]

        note = f"來源：Gemini CLI logs.json（換日時區 {zone_note}）"
        return ProviderSnapshot(
            id=self.id,
            display_name=self.display_name,
            short_name=self.short_name,
            available=True,
            windows=windows,
            note=note,
        )

    def diagnostics(self) -> dict:
        """給 --probe 用：`.gemini/tmp` 不存在時，看看 `.gemini` 本身在不在。

        兩者的意義完全不同：`.gemini` 不存在＝沒裝 Gemini CLI；
        `.gemini` 在但沒有 `tmp`＝裝了但還沒在任何專案跑過。
        """
        bases = []
        for root in self.roots:
            base = root / ".gemini"
            info: dict = {"path": str(base), "exists": base.is_dir(), "children": []}
            if info["exists"]:
                try:
                    info["children"] = sorted(child.name for child in base.iterdir())[:20]
                except OSError as exc:
                    info["children"] = [f"(無法列出：{exc})"]
            bases.append(info)
        return {"bases": bases}

    @staticmethod
    def user_message_times(path: Path) -> list[datetime]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []

        stamps: list[datetime] = []
        for element in payload:
            if not isinstance(element, dict):
                continue
            # 只算使用者送出的請求；模型回覆不佔請求額度。
            if element.get("type", "user") != "user":
                continue
            when = parse_iso(element.get("timestamp"))
            if when is not None:
                stamps.append(when)
        return stamps


def _percent(used: int, budget: int | None) -> float | None:
    if not budget or budget <= 0:
        return None
    return used / budget * 100


def _load_zone(name: str):
    """載入時區。

    Windows 沒有內建時區資料庫，沒裝 `tzdata` 套件時 ZoneInfo 會直接失敗，
    所以這裡退回本機時區並在備註寫明，而不是讓整個 Gemini 卡片消失。
    """
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        local = datetime.now().astimezone().tzinfo
        return local, f"{name} 不可用，改用本機時區（pip install tzdata 可修正）"


def build_providers(config: AppConfig, roots: list[Path]) -> list:
    providers = []
    if config.codex.enabled:
        providers.append(CodexProvider(config.codex, roots))
    if config.claude.enabled:
        providers.append(ClaudeProvider(config.claude, roots))
    if config.gemini.enabled:
        providers.append(GeminiProvider(config.gemini, roots))
    return providers


def collect(config: AppConfig, roots: list[Path], now: datetime) -> list[ProviderSnapshot]:
    return [provider.snapshot(now) for provider in build_providers(config, roots)]

"""`--probe` 診斷模式。

這個工具最大的風險是「各家記錄格式跟預期不一樣」，而那只有在你自己的機器上才看得到。
Probe 把找到的家目錄、每家的目錄、檔案數、最新一行原始 JSON、以及實際解析結果都印出來，
格式對不上時可以直接看出是哪一層斷掉。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from .config import AppConfig, default_config_path, home_roots
from .models import format_countdown, now_utc
from .providers import CodexProvider, GeminiProvider, ClaudeProvider, scan_files
from .service import QuotaService

RULE = "=" * 60
THIN = "-" * 60


def report(config: AppConfig, now: datetime | None = None) -> str:
    moment = now or now_utc()
    roots = home_roots(config)
    lines: list[str] = [
        "QuotaTray 診斷報告",
        f"時間：{moment.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"平台：{sys.platform}    Python：{sys.version.split()[0]}",
        f"設定檔：{default_config_path()}",
        RULE,
        "",
        "搜尋的家目錄",
        THIN,
    ]
    for root in roots:
        mark = "存在" if root.is_dir() else "不存在"
        lines.append(f"  {root}  [{mark}]")
    if len(roots) == 1 and sys.platform == "win32":
        lines.append("  （沒找到 WSL 家目錄。若 CLI 裝在 WSL 裡，請確認 WSL 有啟動）")

    codex = CodexProvider(config.codex, roots)
    claude = ClaudeProvider(config.claude, roots)
    gemini = GeminiProvider(config.gemini, roots)

    lines += _section("Codex", codex.directories, suffix=".jsonl")
    lines += _section("Claude", claude.directories, suffix=".jsonl")
    lines += _section("Gemini", gemini.directories, name="logs.json")

    lines += ["", "解析結果", THIN]
    service = QuotaService(config, roots)
    for snapshot in service.snapshots(moment):
        lines.append(f"[{snapshot.display_name}] {'可用' if snapshot.available else '不可用'}")
        if snapshot.note:
            lines.append(f"  {snapshot.note}")
        for window in snapshot.windows:
            percent = (
                f"已用 {window.used_percent:.1f}%"
                if window.used_percent is not None
                else "無百分比"
            )
            parts = [percent]
            # Codex 的 detail 就是「已用 x%」，跟上一段重複時不要印兩次。
            if window.detail and window.detail != percent:
                parts.append(window.detail)
            if window.resets_at:
                parts.append(format_countdown(window.resets_at, moment))
            lines.append(f"  - {window.label}：" + " · ".join(parts))
        lines.append("")

    return "\n".join(lines)


def _section(
    title: str,
    directories: list[Path],
    suffix: str | None = None,
    name: str | None = None,
) -> list[str]:
    lines = ["", title, THIN]
    if not directories:
        lines.append("目錄：找不到")
        return lines

    for directory in directories:
        lines.append(f"目錄：{directory}")

    files = scan_files(directories, suffix=suffix, name=name)
    lines.append(f"檔案數：{len(files)}")
    for item in files[:3]:
        stamp = datetime.fromtimestamp(item.modified).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"  {item.path.name}  {item.size} bytes  {stamp}")

    if files:
        newest = files[0]
        try:
            content = newest.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            lines.append(f"最新檔讀取失敗：{exc}")
            return lines
        rows = [row for row in content.splitlines() if row.strip()]
        lines.append(f"最新檔行數：{len(rows)}")
        if rows:
            lines.append("最後一行（前 400 字）：")
            lines.append("  " + rows[-1][:400])

    return lines

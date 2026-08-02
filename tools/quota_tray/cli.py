"""命令列進入點。

Windows 的 cmd.exe 預設是 cp950，直接 print 繁體中文會炸 UnicodeEncodeError，
所以進來第一件事是把 stdout 轉成 UTF-8。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import AppConfig, default_config_path, load_config, write_config
from .models import ProviderSnapshot, format_countdown, now_utc
from .service import QuotaService


def _force_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def describe(snapshot: ProviderSnapshot, now) -> str:
    if not snapshot.available:
        return f"{snapshot.display_name}：—  （{snapshot.note or '無資料'}）"

    parts = []
    for window in snapshot.windows:
        if window.remaining_percent is not None:
            value = f"剩 {window.remaining_percent:.0f}%"
        else:
            value = window.detail or "-"
        if window.resets_at:
            value += f"（{format_countdown(window.resets_at, now)}）"
        parts.append(f"{window.label} {value}")
    return f"{snapshot.display_name}：" + " · ".join(parts)


def to_json(snapshots: list[ProviderSnapshot]) -> str:
    payload = [
        {
            "id": snapshot.id,
            "display_name": snapshot.display_name,
            "available": snapshot.available,
            "remaining_percent": snapshot.remaining_percent,
            "note": snapshot.note,
            "windows": [
                {
                    "label": window.label,
                    "used_percent": window.used_percent,
                    "remaining_percent": window.remaining_percent,
                    "detail": window.detail,
                    "resets_at": window.resets_at.isoformat() if window.resets_at else None,
                }
                for window in snapshot.windows
            ],
        }
        for snapshot in snapshots
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quota-tray",
        description="在 Windows 系統匣顯示 Codex / Claude / Gemini 的本機用量",
    )
    parser.add_argument("--once", action="store_true", help="讀一次印出結果後結束")
    parser.add_argument("--watch", action="store_true", help="在終端機持續刷新（不需要系統匣）")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出，方便接其他工具")
    parser.add_argument("--probe", action="store_true", help="印出資料來源診斷報告")
    parser.add_argument("--config", action="store_true", help="印出設定檔路徑與內容")
    parser.add_argument("--write-config", action="store_true", help="把預設設定寫成檔案再結束")
    parser.add_argument("--config-path", type=Path, default=None, help="指定設定檔位置")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)

    config, error = load_config(args.config_path)
    if error:
        print(error, file=sys.stderr)

    if args.write_config:
        path = write_config(config, args.config_path)
        print(f"已寫入預設設定：{path}")
        return 0

    if args.config:
        path = args.config_path or default_config_path()
        print(f"設定檔路徑：{path}")
        if path.exists():
            print(path.read_text(encoding="utf-8"))
        else:
            print("（檔案不存在，使用內建預設值。用 --write-config 產生一份）")
        return 0

    if args.probe:
        from .probe import report

        print(report(config))
        return 0

    service = QuotaService(config)

    if args.json:
        print(to_json(service.snapshots()))
        return 0

    if args.once:
        _print_once(service)
        return 0

    if args.watch:
        return _watch(service, config)

    return _run_tray(service, config)


def _print_once(service: QuotaService) -> None:
    now = now_utc()
    snapshots = service.snapshots(now)
    for snapshot in snapshots:
        print(describe(snapshot, now))


def _watch(service: QuotaService, config: AppConfig) -> int:
    interval = max(1.0, float(config.refresh_seconds))
    try:
        while True:
            now = now_utc()
            snapshots = service.snapshots(now)
            stamp = now.astimezone().strftime("%H:%M:%S")
            print(f"\n[{stamp}]  {service.summary_line(snapshots)}")
            for snapshot in snapshots:
                print("  " + describe(snapshot, now))
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _run_tray(service: QuotaService, config: AppConfig) -> int:
    try:
        from .tray import run_tray
    except ImportError as exc:
        if exc.name == "tkinter":
            # tkinter 不是 pip 套件，是 Python 本身要帶 Tcl/Tk。
            hint = (
                "這個 Python 沒有 tkinter（詳細面板需要它）。\n"
                "Windows 官方安裝檔預設就有；若是精簡版或 Microsoft Store 版，\n"
                "請到 python.org 重裝並勾選 tcl/tk。"
            )
        else:
            hint = "系統匣模式需要額外套件：\n    pip install pystray pillow tzdata\n" + (
                f"（缺少：{exc.name}）"
            )
        print(
            f"{hint}\n不想裝的話，可以改用 --watch 在終端機看，或 --once 讀一次。",
            file=sys.stderr,
        )
        return 1

    return run_tray(service, config)

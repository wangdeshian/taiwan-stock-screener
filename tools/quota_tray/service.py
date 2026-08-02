"""把三家包成一個服務物件。

存在的理由：ClaudeProvider 內部有解析快取，每次刷新都重建 provider 會讓快取失效，
在有幾百個對話記錄檔時差別很明顯。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import AppConfig, home_roots
from .models import ProviderSnapshot, now_utc
from .providers import build_providers


class QuotaService:
    def __init__(self, config: AppConfig, roots: list[Path] | None = None) -> None:
        self.config = config
        self.roots = roots if roots is not None else home_roots(config)
        self.providers = build_providers(config, self.roots)

    def snapshots(self, now: datetime | None = None) -> list[ProviderSnapshot]:
        moment = now or now_utc()
        results: list[ProviderSnapshot] = []
        for provider in self.providers:
            try:
                results.append(provider.snapshot(moment))
            except Exception as exc:  # noqa: BLE001 - 一家壞掉不該拖垮其他兩家
                results.append(
                    ProviderSnapshot(
                        id=provider.id,
                        display_name=provider.display_name,
                        short_name=provider.short_name,
                        available=False,
                        windows=[],
                        note=f"讀取時發生錯誤：{exc}",
                    )
                )
        return results

    def summary_line(self, snapshots: list[ProviderSnapshot]) -> str:
        """系統匣提示文字，例如 `CX 91%  CL 62%  GM 100%`。"""
        visible = [s for s in snapshots if s.available] if self.config.hide_unavailable else snapshots
        parts = [
            f"{s.short_name} {round(s.remaining_percent)}%"
            for s in visible
            if s.remaining_percent is not None
        ]
        return "  ".join(parts) if parts else "無可用額度資料"

    @staticmethod
    def safety_level(snapshots: list[ProviderSnapshot]) -> float | None:
        """所有供應商中最吃緊的剩餘百分比。"""
        values = [s.remaining_percent for s in snapshots if s.remaining_percent is not None]
        return min(values) if values else None

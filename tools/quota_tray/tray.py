"""Windows 系統匣圖示與詳細面板。

執行緒安排：tkinter 的 mainloop 必須待在主執行緒，pystray 在 Windows 可以跑在次執行緒，
所以主執行緒跑 tkinter、背景執行緒跑系統匣與刷新迴圈，所有畫面更新都用 `root.after`
丟回主執行緒執行。
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime

import pystray

from .config import AppConfig
from .icon import color_for, make_icon_image
from .models import ProviderSnapshot, format_countdown, now_utc
from .service import QuotaService

BG = "#1b1b1f"
CARD_BG = "#26262b"
TEXT = "#f2f2f7"
MUTED = "#9a9aa4"
TRACK = "#3a3a41"


class TrayApp:
    def __init__(self, service: QuotaService, config: AppConfig) -> None:
        self.service = service
        self.config = config
        self.interval = max(1.0, float(config.refresh_seconds))

        self.snapshots: list[ProviderSnapshot] = []
        self.updated_at: datetime | None = None
        self._stop = threading.Event()

        self.root = tk.Tk()
        self.root.withdraw()
        self.panel: tk.Toplevel | None = None

        self.icon = pystray.Icon(
            "quota-tray",
            icon=make_icon_image(None),
            title="QuotaTray",
            menu=pystray.Menu(self._menu_items),
        )

    # ---------- 系統匣 ----------

    def _menu_items(self):
        """每次開選單時重新產生，這樣文字才會是最新的。"""
        yield pystray.MenuItem(self.service.summary_line(self.snapshots), None, enabled=False)
        yield pystray.Menu.SEPARATOR
        for snapshot in self.snapshots:
            yield pystray.MenuItem(self._snapshot_line(snapshot), None, enabled=False)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("顯示詳細面板", self._on_show_panel, default=True)
        yield pystray.MenuItem("立即重新讀取", self._on_refresh_now)
        yield pystray.MenuItem("結束", self._on_quit)

    @staticmethod
    def _snapshot_line(snapshot: ProviderSnapshot) -> str:
        if not snapshot.available:
            return f"{snapshot.display_name}：—"
        window = snapshot.primary_window
        if window is None:
            return f"{snapshot.display_name}：—"
        if window.remaining_percent is not None:
            return f"{snapshot.display_name}：剩 {window.remaining_percent:.0f}%（{window.label}）"
        return f"{snapshot.display_name}：{window.detail or '-'}"

    def _on_show_panel(self, _icon=None, _item=None) -> None:
        self.root.after(0, self.show_panel)

    def _on_refresh_now(self, _icon=None, _item=None) -> None:
        threading.Thread(target=self.refresh, daemon=True).start()

    def _on_quit(self, _icon=None, _item=None) -> None:
        self._stop.set()
        self.icon.visible = False
        self.icon.stop()
        self.root.after(0, self.root.quit)

    # ---------- 刷新 ----------

    def refresh(self) -> None:
        now = now_utc()
        snapshots = self.service.snapshots(now)
        self.snapshots = snapshots
        self.updated_at = now

        remaining = self.service.safety_level(snapshots)
        try:
            self.icon.icon = make_icon_image(remaining)
            self.icon.title = f"QuotaTray  {self.service.summary_line(snapshots)}"
        except Exception:  # noqa: BLE001 - 圖示更新失敗不該讓整個程式停掉
            pass

        self.root.after(0, self._redraw_panel)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - 讀取失敗就等下一輪
                pass
            self._stop.wait(self.interval)

    # ---------- 詳細面板 ----------

    def show_panel(self) -> None:
        if self.panel is not None and self.panel.winfo_exists():
            self.panel.deiconify()
            self.panel.lift()
            self._redraw_panel()
            return

        panel = tk.Toplevel(self.root)
        panel.title("用量額度")
        panel.configure(bg=BG)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.bind("<Escape>", lambda _event: self.hide_panel())
        self.panel = panel
        self._redraw_panel()

    def hide_panel(self) -> None:
        if self.panel is not None and self.panel.winfo_exists():
            self.panel.withdraw()

    def _redraw_panel(self) -> None:
        panel = self.panel
        if panel is None or not panel.winfo_exists():
            return

        for child in panel.winfo_children():
            child.destroy()

        now = now_utc()
        header = tk.Frame(panel, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 6))

        tk.Label(
            header, text="用量額度", bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 12, "bold")
        ).pack(side="left")

        stamp = self.updated_at.astimezone().strftime("%H:%M:%S") if self.updated_at else "—"
        tk.Label(
            header, text=f"更新於 {stamp}", bg=BG, fg=MUTED, font=("Microsoft JhengHei UI", 8)
        ).pack(side="right")

        for snapshot in self.snapshots:
            self._draw_card(panel, snapshot, now)

        footer = tk.Frame(panel, bg=BG)
        footer.pack(fill="x", padx=14, pady=(4, 12))
        tk.Label(
            footer,
            text="資料只在本機讀取，不會上傳",
            bg=BG,
            fg=MUTED,
            font=("Microsoft JhengHei UI", 8),
        ).pack(side="left")
        tk.Button(
            footer,
            text="關閉",
            command=self.hide_panel,
            bg=CARD_BG,
            fg=TEXT,
            relief="flat",
            font=("Microsoft JhengHei UI", 8),
        ).pack(side="right")
        tk.Button(
            footer,
            text="重新讀取",
            command=self._on_refresh_now,
            bg=CARD_BG,
            fg=TEXT,
            relief="flat",
            font=("Microsoft JhengHei UI", 8),
        ).pack(side="right", padx=(0, 6))

        panel.update_idletasks()
        self._place_bottom_right(panel)

    def _draw_card(self, parent: tk.Widget, snapshot: ProviderSnapshot, now: datetime) -> None:
        card = tk.Frame(parent, bg=CARD_BG)
        card.pack(fill="x", padx=14, pady=4)

        top = tk.Frame(card, bg=CARD_BG)
        top.pack(fill="x", padx=10, pady=(8, 2))

        tk.Label(
            top,
            text=snapshot.display_name,
            bg=CARD_BG,
            fg=TEXT,
            font=("Microsoft JhengHei UI", 10, "bold"),
        ).pack(side="left")

        remaining = snapshot.remaining_percent
        tk.Label(
            top,
            text=f"{remaining:.0f}%" if remaining is not None else "—",
            bg=CARD_BG,
            fg=color_for(remaining),
            font=("Consolas", 15, "bold"),
        ).pack(side="right")

        if snapshot.available:
            for window in snapshot.windows:
                self._draw_window(card, window, now)

        if snapshot.note:
            tk.Label(
                card,
                text=snapshot.note,
                bg=CARD_BG,
                fg=MUTED,
                font=("Microsoft JhengHei UI", 7),
                wraplength=300,
                justify="left",
            ).pack(anchor="w", padx=10, pady=(2, 8))

    def _draw_window(self, parent: tk.Widget, window, now: datetime) -> None:
        row = tk.Frame(parent, bg=CARD_BG)
        row.pack(fill="x", padx=10, pady=(2, 0))

        line = tk.Frame(row, bg=CARD_BG)
        line.pack(fill="x")
        tk.Label(
            line, text=window.label, bg=CARD_BG, fg=MUTED, font=("Microsoft JhengHei UI", 8)
        ).pack(side="left")
        if window.resets_at:
            tk.Label(
                line,
                text=format_countdown(window.resets_at, now),
                bg=CARD_BG,
                fg=MUTED,
                font=("Microsoft JhengHei UI", 8),
            ).pack(side="right")

        if window.used_percent is not None:
            remaining = window.remaining_percent or 0.0
            width, height = 300, 6
            canvas = tk.Canvas(
                row, width=width, height=height, bg=CARD_BG, highlightthickness=0, bd=0
            )
            canvas.pack(fill="x", pady=(2, 0))
            canvas.create_rectangle(0, 0, width, height, fill=TRACK, outline="")
            filled = max(2, int(width * min(max(remaining, 0.0), 100.0) / 100))
            canvas.create_rectangle(0, 0, filled, height, fill=color_for(remaining), outline="")

        if window.detail:
            tk.Label(
                row, text=window.detail, bg=CARD_BG, fg=MUTED, font=("Microsoft JhengHei UI", 8)
            ).pack(anchor="w", pady=(1, 2))

    @staticmethod
    def _place_bottom_right(panel: tk.Toplevel) -> None:
        width = max(panel.winfo_reqwidth(), 340)
        height = panel.winfo_reqheight()
        screen_w = panel.winfo_screenwidth()
        screen_h = panel.winfo_screenheight()
        # 留出工作列的高度，讓面板浮在系統匣正上方。
        x = max(0, screen_w - width - 16)
        y = max(0, screen_h - height - 56)
        panel.geometry(f"{width}x{height}+{x}+{y}")

    # ---------- 生命週期 ----------

    def run(self) -> int:
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self.icon.run, daemon=True).start()
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
        return 0


def run_tray(service: QuotaService, config: AppConfig) -> int:
    return TrayApp(service, config).run()

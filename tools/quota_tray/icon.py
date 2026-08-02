"""系統匣圖示繪製與配色。

刻意跟 `tray.py` 分開：tray 會 import pystray，而 pystray 在沒有桌面環境時光是 import
就會失敗，抽出來才測得到。這裡只依賴 Pillow。
"""

from __future__ import annotations

GREEN = "#34c759"
ORANGE = "#ff9f0a"
RED = "#ff453a"
GREY = "#8e8e93"
TRACK = "#55555c"


def color_for(remaining: float | None) -> str:
    """剩餘越少越紅。20% 以下才轉紅，避免一整天都在示警。"""
    if remaining is None:
        return GREY
    if remaining < 20:
        return RED
    if remaining < 50:
        return ORANGE
    return GREEN


def make_icon_image(remaining: float | None, size: int = 64):
    """畫一個環形圖示。

    系統匣只有 16x16，畫百分比數字看不清楚，所以用環的缺口長度表示剩餘量，
    顏色表示嚴重程度。沒有資料時畫一條灰色橫槓，跟「剩 0%」明確區分。
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = max(4, size // 8)
    box = (width // 2, width // 2, size - width // 2 - 1, size - width // 2 - 1)

    draw.ellipse(box, outline=TRACK, width=width)

    if remaining is None:
        draw.line(
            (size * 0.32, size * 0.5, size * 0.68, size * 0.5),
            fill=GREY,
            width=max(3, width // 2),
        )
        return image

    extent = 360 * min(max(remaining, 0.0), 100.0) / 100
    if extent > 0:
        # 從 12 點鐘方向順時針畫。
        draw.arc(box, start=-90, end=-90 + extent, fill=color_for(remaining), width=width)
    return image

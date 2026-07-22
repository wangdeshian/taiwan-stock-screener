from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from taiwan_stock_screener.indicators.technical import kd


LOT_SIZE = 1000


@dataclass(frozen=True)
class Condition:
    id: str
    label: str
    met: bool
    available: bool = True


@dataclass(frozen=True)
class TechnicalTemplate:
    id: str
    name: str
    direction: str
    conditions: tuple[Condition, ...]

    @property
    def is_triggered(self) -> bool:
        return all(condition.available and condition.met for condition in self.conditions)

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "direction": self.direction,
            "condition_count": len(self.conditions),
            "met_count": sum(1 for condition in self.conditions if condition.met),
            "conditions": [
                {
                    "id": condition.id,
                    "label": condition.label,
                    "met": condition.met,
                    "available": condition.available,
                }
                for condition in self.conditions
            ],
        }


def evaluate_technical_templates(
    indicators: pd.DataFrame,
    chip_rows: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Evaluate user-provided technical strategy templates on a daily-K frame.

    The mobile screenshots use some intraday wording such as "盤中創高". Until
    the project has an intraday feed, daily high/low are used as the closest
    available proxy. Triggered templates are emitted as tags for display and
    later signal-outcome backtesting; they do not change the score by themselves.
    """

    if indicators.empty or len(indicators) < 20:
        return []

    df = indicators.sort_values("trade_date").copy() if "trade_date" in indicators.columns else indicators.copy()
    for column in ("open", "high", "low", "close", "volume"):
        if column not in df.columns:
            return []
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(df) < 20:
        return []

    templates = [
        _new_high_momentum(df),
        _ma_cluster_breakout(df),
        _mtm_60d_breakout(df),
        _dual_kd_reversal(df),
        _ma20_kd_reversal(df),
        _volume_kd_low_reversal(df),
        _high_close_three_soldiers(df),
        _low_close_three_crows(df),
        _kd_overheat_reversal(df),
        _new_low_kd_margin_reversal(df, chip_rows),
    ]

    return [template.as_payload() for template in templates if template.is_triggered]


def technical_reason_ids(signals: list[dict[str, Any]]) -> list[str]:
    return [f"tech_{signal['id']}" for signal in signals if signal.get("id")]


def _last(df: pd.DataFrame, column: str) -> float:
    return float(df[column].iloc[-1])


def _prev(df: pd.DataFrame, column: str, offset: int = 1) -> float:
    return float(df[column].iloc[-(offset + 1)])


def _volume_lots(value: float) -> float:
    return value / LOT_SIZE


def _volume_gt_lots(df: pd.DataFrame, lots: float) -> bool:
    return _volume_lots(_last(df, "volume")) >= lots


def _volume_ma(df: pd.DataFrame, window: int, offset: int = 0) -> float:
    series = df["volume"].astype(float)
    end = len(series) - offset
    if end <= 0:
        return 0.0
    return float(series.iloc[max(0, end - window):end].mean())


def _volume_gt_prev_multiple(df: pd.DataFrame, multiple: float) -> bool:
    if len(df) < 2:
        return False
    prev_volume = _prev(df, "volume")
    return prev_volume > 0 and _last(df, "volume") >= prev_volume * multiple


def _volume_gt_ma_multiple(df: pd.DataFrame, window: int, multiple: float, exclude_today: bool = True) -> bool:
    avg = _volume_ma(df, window, offset=1 if exclude_today else 0)
    return avg > 0 and _last(df, "volume") >= avg * multiple


def _new_high(df: pd.DataFrame, window: int) -> bool:
    if len(df) < window:
        return False
    return _last(df, "high") >= float(df["high"].tail(window).max())


def _new_low(df: pd.DataFrame, window: int) -> bool:
    if len(df) < window:
        return False
    return _last(df, "low") <= float(df["low"].tail(window).min())


def _ma(df: pd.DataFrame, window: int) -> float:
    column = f"ma{window}"
    if column in df.columns:
        return _last(df, column)
    return float(df["close"].tail(window).mean())


def _prev_ma(df: pd.DataFrame, window: int) -> float:
    column = f"ma{window}"
    if column in df.columns and len(df) >= 2:
        return _prev(df, column)
    return float(df["close"].iloc[:-1].tail(window).mean())


def _kd_cross(df: pd.DataFrame, direction: str, max_level: float | None = None, min_level: float | None = None) -> bool:
    if len(df) < 2 or "kd_k" not in df.columns or "kd_d" not in df.columns:
        return False
    k_now = _last(df, "kd_k")
    d_now = _last(df, "kd_d")
    k_prev = _prev(df, "kd_k")
    d_prev = _prev(df, "kd_d")
    if direction == "golden":
        crossed = k_prev <= d_prev and k_now > d_now
    else:
        crossed = k_prev >= d_prev and k_now < d_now
    if not crossed:
        return False
    if max_level is not None and max(k_now, d_now) >= max_level:
        return False
    if min_level is not None and min(k_now, d_now) <= min_level:
        return False
    return True


def _weekly_kd_golden(df: pd.DataFrame) -> bool:
    if "trade_date" not in df.columns or len(df) < 45:
        return False
    weekly = (
        df.assign(trade_date=pd.to_datetime(df["trade_date"]))
        .set_index("trade_date")
        .resample("W-FRI")
        .agg({"high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    if len(weekly) < 2:
        return False
    k_series, d_series = kd(weekly["high"], weekly["low"], weekly["close"])
    return float(k_series.iloc[-2]) <= float(d_series.iloc[-2]) and float(k_series.iloc[-1]) > float(d_series.iloc[-1])


def _close_break_ma20(df: pd.DataFrame) -> bool:
    return len(df) >= 2 and _prev(df, "close") <= _prev_ma(df, 20) and _last(df, "close") > _ma(df, 20)


def _ma_cluster_break(df: pd.DataFrame) -> bool:
    ma_values = [_ma(df, window) for window in (5, 10, 20)]
    if min(ma_values) <= 0:
        return False
    cluster_width_pct = (max(ma_values) - min(ma_values)) / _last(df, "close") * 100
    prev_max = max(_prev_ma(df, window) for window in (5, 10, 20))
    return cluster_width_pct <= 3 and _prev(df, "close") <= prev_max and _last(df, "close") > max(ma_values)


def _gain_pct(df: pd.DataFrame) -> float:
    if len(df) < 2 or _prev(df, "close") <= 0:
        return 0.0
    return (_last(df, "close") / _prev(df, "close") - 1) * 100


def _mtm_turn_positive(df: pd.DataFrame, window: int = 6) -> bool:
    if len(df) <= window + 1:
        return False
    mtm_now = _last(df, "close") - float(df["close"].iloc[-(window + 1)])
    mtm_prev = _prev(df, "close") - float(df["close"].iloc[-(window + 2)])
    return mtm_prev <= 0 < mtm_now


def _close_near_high(df: pd.DataFrame) -> bool:
    high = _last(df, "high")
    return high > 0 and _last(df, "close") >= high * 0.995


def _close_near_low(df: pd.DataFrame) -> bool:
    low = _last(df, "low")
    return low > 0 and _last(df, "close") <= low * 1.005


def _red_three_soldiers(df: pd.DataFrame) -> bool:
    if len(df) < 3:
        return False
    last3 = df.tail(3)
    return bool((last3["close"] > last3["open"]).all() and last3["close"].is_monotonic_increasing)


def _black_three_crows(df: pd.DataFrame) -> bool:
    if len(df) < 3:
        return False
    last3 = df.tail(3)
    return bool((last3["close"] < last3["open"]).all() and last3["close"].is_monotonic_decreasing)


def _margin_three_day_drop(chip_rows: pd.DataFrame | None) -> Condition:
    if chip_rows is None or chip_rows.empty or "margin_balance" not in chip_rows.columns:
        return Condition("margin_3d_drop", "融資連續 3 日減少", False, available=False)
    rows = chip_rows.sort_values("trade_date").copy() if "trade_date" in chip_rows.columns else chip_rows.copy()
    margins = pd.to_numeric(rows["margin_balance"], errors="coerce").dropna().tail(4)
    if len(margins) < 4:
        return Condition("margin_3d_drop", "融資連續 3 日減少", False, available=False)
    diffs = margins.diff().dropna()
    return Condition("margin_3d_drop", "融資連續 3 日減少", bool((diffs < 0).all()))


def _template(id_: str, name: str, direction: str, conditions: list[Condition]) -> TechnicalTemplate:
    return TechnicalTemplate(id_, name, direction, tuple(conditions))


def _new_high_momentum(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "new_high_momentum",
        "創新高動能股",
        "bullish",
        [
            Condition("new_high_5d", "股價盤中創 5 日新高（日 K high 近似）", _new_high(df, 5)),
            Condition("ma5_gt_ma10", "短均線高於長均線（5 日 > 10 日）", _ma(df, 5) > _ma(df, 10)),
            Condition("ma10_gt_ma20", "短均線高於長均線（10 日 > 20 日）", _ma(df, 10) > _ma(df, 20)),
            Condition("ma20_gt_ma60", "短均線高於長均線（20 日 > 60 日）", len(df) >= 60 and _ma(df, 20) > _ma(df, 60)),
            Condition("volume_gt_2x_prev", "成交爆大量（大於 2 倍昨量）", _volume_gt_prev_multiple(df, 2)),
        ],
    )


def _ma_cluster_breakout(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "ma_cluster_breakout",
        "突破均線糾結",
        "bullish",
        [
            Condition("ma_cluster_break", "突破 5/10/20 日均線糾結", _ma_cluster_break(df)),
            Condition("volume_gt_2000_lots", "成交量大於 2000 張", _volume_gt_lots(df, 2000)),
            Condition("volume_gt_2x_ma5", "成交爆大量（大於 2 倍 5 日均量）", _volume_gt_ma_multiple(df, 5, 2)),
        ],
    )


def _mtm_60d_breakout(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "mtm_60d_breakout",
        "MTM 創高動能",
        "bullish",
        [
            Condition("gain_gt_3pct", "漲幅大於 3%", _gain_pct(df) > 3),
            Condition("new_high_60d", "股價盤中創 60 日新高（日 K high 近似）", _new_high(df, 60)),
            Condition("mtm6_turn_positive", "6 日 MTM 由負轉正", _mtm_turn_positive(df, 6)),
        ],
    )


def _dual_kd_reversal(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "dual_kd_reversal",
        "雙 KD 向上",
        "bullish",
        [
            Condition("weekly_kd_golden", "週 KD 黃金交叉", _weekly_kd_golden(df)),
            Condition("daily_kd_golden_lt30", "日 KD 黃金交叉（KD 小於 30）", _kd_cross(df, "golden", max_level=30)),
            Condition("volume_ma5_gt_1000_lots", "5 日均量大於 1000 張", _volume_ma(df, 5) >= 1000 * LOT_SIZE),
        ],
    )


def _ma20_kd_reversal(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "ma20_kd_reversal",
        "抄底求翻身",
        "bullish",
        [
            Condition("daily_kd_golden_lt30", "日 KD 黃金交叉（KD 小於 30）", _kd_cross(df, "golden", max_level=30)),
            Condition("close_break_ma20", "股價突破月線壓力", _close_break_ma20(df)),
            Condition("volume_ma5_gt_1000_lots", "5 日均量大於 1000 張", _volume_ma(df, 5) >= 1000 * LOT_SIZE),
        ],
    )


def _volume_kd_low_reversal(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "volume_kd_low_reversal",
        "一線間",
        "bullish",
        [
            Condition("volume_gt_2x_prev", "成交爆大量（大於 2 倍昨量）", _volume_gt_prev_multiple(df, 2)),
            Condition("daily_kd_golden_lt20", "日 KD 黃金交叉（KD 小於 20）", _kd_cross(df, "golden", max_level=20)),
            Condition("volume_gt_1000_lots", "成交量大於 1000 張", _volume_gt_lots(df, 1000)),
        ],
    )


def _high_close_three_soldiers(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "high_close_three_soldiers",
        "創高出貨潮",
        "caution",
        [
            Condition("close_near_high", "收在最高", _close_near_high(df)),
            Condition("red_three_soldiers", "紅三兵", _red_three_soldiers(df)),
            Condition("volume_gt_2x_prev", "成交爆大量（大於 2 倍昨量）", _volume_gt_prev_multiple(df, 2)),
        ],
    )


def _low_close_three_crows(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "low_close_three_crows",
        "黑三鴉弱勢",
        "bearish",
        [
            Condition("close_near_low", "收在最低", _close_near_low(df)),
            Condition("black_three_crows", "黑三鴉", _black_three_crows(df)),
            Condition("volume_gt_3000_lots", "成交量大於 3000 張", _volume_gt_lots(df, 3000)),
            Condition("volume_gt_2x_prev", "成交爆大量（大於 2 倍昨量）", _volume_gt_prev_multiple(df, 2)),
        ],
    )


def _kd_overheat_reversal(df: pd.DataFrame) -> TechnicalTemplate:
    return _template(
        "kd_overheat_reversal",
        "KD 高檔轉弱",
        "bearish",
        [
            Condition("volume_gt_2x_prev", "成交爆大量（大於 2 倍昨量）", _volume_gt_prev_multiple(df, 2)),
            Condition("daily_kd_death_gt80", "日 KD 死亡交叉（KD 大於 80）", _kd_cross(df, "death", min_level=80)),
            Condition("volume_gt_1000_lots", "成交量大於 1000 張", _volume_gt_lots(df, 1000)),
        ],
    )


def _new_low_kd_margin_reversal(df: pd.DataFrame, chip_rows: pd.DataFrame | None) -> TechnicalTemplate:
    return _template(
        "new_low_kd_margin_reversal",
        "築底等時機",
        "bullish",
        [
            Condition("new_low_20d", "股價盤中創 20 日新低（日 K low 近似）", _new_low(df, 20)),
            Condition("daily_kd_golden_lt20", "日 KD 黃金交叉（KD 小於 20）", _kd_cross(df, "golden", max_level=20)),
            _margin_three_day_drop(chip_rows),
        ],
    )

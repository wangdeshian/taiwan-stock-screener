from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


LOT_SIZE = 1000
SHARES_PER_YI = 100_000_000


@dataclass(frozen=True)
class Condition:
    id: str
    label: str
    met: bool
    available: bool = True


@dataclass(frozen=True)
class ChipTemplate:
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


def evaluate_chip_templates(
    indicators: pd.DataFrame | None = None,
    institutional_rows: pd.DataFrame | None = None,
    holder_rows: pd.DataFrame | None = None,
    broker_row: dict[str, Any] | pd.Series | None = None,
    financial_row: dict[str, Any] | pd.Series | None = None,
) -> list[dict[str, Any]]:
    """Evaluate user-provided chip strategy templates.

    These templates are display/backtest signals. They intentionally do not
    alter the existing 100-point scoring model. Missing source columns make the
    affected condition unavailable, so a template will not trigger from partial
    data.
    """

    prices = _prepare_price_frame(indicators)
    institutions = _prepare_ordered_frame(institutional_rows, "trade_date")
    holders = _prepare_ordered_frame(holder_rows, "date")

    templates = [
        _institutional_accumulation_watch(institutions),
        _small_cap_foreign_accumulation(prices, institutions, financial_row),
        _insider_alignment(holders, financial_row),
        _large_holder_accumulation_retail_exit(prices, holders),
        _trust_small_cap_accumulation(prices, institutions, financial_row),
        _broker_synchronized_short(prices, broker_row),
        _large_holder_distribution_retail_in(prices, holders),
        _institutional_distribution_watch(institutions),
        _trust_distribution_small_cap(prices, institutions, financial_row),
        _insider_divergence_short_squeeze(holders, financial_row),
    ]

    return [template.as_payload() for template in templates if template.is_triggered]


def chip_reason_ids(signals: list[dict[str, Any]]) -> list[str]:
    return [f"chip_{signal['id']}" for signal in signals if signal.get("id")]


def _template(id_: str, name: str, direction: str, conditions: list[Condition]) -> ChipTemplate:
    return ChipTemplate(id_, name, direction, tuple(conditions))


def _prepare_price_frame(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    rows = frame.sort_values("trade_date").copy() if "trade_date" in frame.columns else frame.copy()
    if "volume" in rows.columns:
        rows["volume"] = pd.to_numeric(rows["volume"], errors="coerce")
    return rows


def _prepare_ordered_frame(frame: pd.DataFrame | None, date_column: str) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None
    rows = frame.copy()
    if date_column in rows.columns:
        rows[date_column] = pd.to_datetime(rows[date_column], errors="coerce")
        rows = rows.sort_values(date_column)
    return rows


def _series_from_aliases(frame: pd.DataFrame | None, aliases: tuple[str, ...]) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    for column in aliases:
        if column in frame.columns:
            series = pd.to_numeric(frame[column], errors="coerce").dropna()
            return series if not series.empty else None
    return None


def _row_value(row: dict[str, Any] | pd.Series | None, aliases: tuple[str, ...]) -> float | None:
    if row is None:
        return None
    for key in aliases:
        value = row.get(key) if isinstance(row, pd.Series) else row.get(key)
        if value is None or pd.isna(value):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _institutional_lots(frame: pd.DataFrame | None, kind: str) -> pd.Series | None:
    aliases = {
        "foreign": ("foreign_buy_sell", "foreign_investor_buy_sell", "foreign_net_buy", "foreign_net_buy_sell"),
        "trust": ("investment_trust_buy_sell", "trust_net_buy", "investment_trust", "trust_buy_sell"),
        "dealer": ("dealer_buy_sell", "dealer_net_buy", "dealer"),
    }[kind]
    series = _series_from_aliases(frame, aliases)
    if series is None:
        return None
    return _maybe_shares_to_lots(series)


def _maybe_shares_to_lots(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    if numeric.empty:
        return numeric
    return numeric / LOT_SIZE if float(numeric.abs().max()) > 10_000 else numeric


def _volume_series_lots(prices: pd.DataFrame | None) -> pd.Series | None:
    if prices is None or prices.empty or "volume" not in prices.columns:
        return None
    volume = pd.to_numeric(prices["volume"], errors="coerce").dropna()
    if volume.empty:
        return None
    return _maybe_shares_to_lots(volume)


def _latest_volume_lots(prices: pd.DataFrame | None) -> float | None:
    volume = _volume_series_lots(prices)
    if volume is None or volume.empty:
        return None
    return float(volume.iloc[-1])


def _volume_ma_lots(prices: pd.DataFrame | None, window: int) -> float | None:
    volume = _volume_series_lots(prices)
    if volume is None or len(volume) < window:
        return None
    return float(volume.tail(window).mean())


def _volume_sum_lots(prices: pd.DataFrame | None, window: int) -> float | None:
    volume = _volume_series_lots(prices)
    if volume is None or len(volume) < window:
        return None
    return float(volume.tail(window).sum())


def _share_capital_yi(financial_row: dict[str, Any] | pd.Series | None) -> float | None:
    value = _row_value(
        financial_row,
        (
            "share_capital_twd",
            "capital_stock_twd",
            "paid_in_capital_twd",
            "share_capital",
            "capital_stock",
            "paid_in_capital",
            "capital",
        ),
    )
    if value is None:
        return None
    return value / SHARES_PER_YI if abs(value) > 10_000 else value


def _holder_series(holder_rows: pd.DataFrame | None, aliases: tuple[str, ...]) -> pd.Series | None:
    return _series_from_aliases(holder_rows, aliases)


def _consecutive_direction(series: pd.Series | None, periods: int, direction: str) -> bool | None:
    if series is None or len(series) < periods + 1:
        return None
    diffs = series.tail(periods + 1).diff().dropna()
    if len(diffs) < periods:
        return None
    return bool((diffs > 0).all()) if direction == "up" else bool((diffs < 0).all())


def _positive_streak(series: pd.Series | None, days: int) -> bool | None:
    if series is None or len(series) < days:
        return None
    return bool((series.tail(days) > 0).all())


def _condition(id_: str, label: str, value: bool | None) -> Condition:
    return Condition(id_, label, bool(value), available=value is not None)


def _institutional_sum_condition(
    series: pd.Series | None,
    id_: str,
    label: str,
    window: int,
    threshold_lots: float,
    direction: str,
) -> Condition:
    if series is None or len(series) < window:
        return Condition(id_, label, False, available=False)
    value = float(series.tail(window).sum())
    met = value >= threshold_lots if direction == "buy" else value <= -threshold_lots
    return Condition(id_, label, met)


def _institutional_ratio_condition(
    net_series: pd.Series | None,
    prices: pd.DataFrame | None,
    id_: str,
    label: str,
    window: int,
    threshold_pct: float,
    direction: str,
) -> Condition:
    volume_sum = _volume_sum_lots(prices, window)
    if net_series is None or len(net_series) < window or volume_sum is None or volume_sum <= 0:
        return Condition(id_, label, False, available=False)
    net_sum = float(net_series.tail(window).sum())
    ratio = abs(net_sum) / volume_sum * 100
    met_direction = net_sum > 0 if direction == "buy" else net_sum < 0
    return Condition(id_, label, met_direction and ratio > threshold_pct)


def _volume_gt_condition(prices: pd.DataFrame | None, id_: str, label: str, threshold_lots: float) -> Condition:
    volume = _latest_volume_lots(prices)
    return _condition(id_, label, None if volume is None else volume > threshold_lots)


def _volume_ma_gt_condition(prices: pd.DataFrame | None, id_: str, label: str, window: int, threshold_lots: float) -> Condition:
    volume = _volume_ma_lots(prices, window)
    return _condition(id_, label, None if volume is None else volume > threshold_lots)


def _capital_condition(
    financial_row: dict[str, Any] | pd.Series | None,
    id_: str,
    label: str,
    threshold_yi: float,
    operator: str,
) -> Condition:
    capital = _share_capital_yi(financial_row)
    if capital is None:
        return Condition(id_, label, False, available=False)
    return Condition(id_, label, capital < threshold_yi if operator == "lt" else capital > threshold_yi)


def _holder_ratio_condition(
    holder_rows: pd.DataFrame | None,
    aliases: tuple[str, ...],
    id_: str,
    label: str,
    threshold_pct: float,
    operator: str,
) -> Condition:
    series = _holder_series(holder_rows, aliases)
    if series is None or series.empty:
        return Condition(id_, label, False, available=False)
    latest = float(series.iloc[-1])
    return Condition(id_, label, latest > threshold_pct if operator == "gt" else latest < threshold_pct)


def _holder_trend_condition(
    holder_rows: pd.DataFrame | None,
    aliases: tuple[str, ...],
    id_: str,
    label: str,
    direction: str,
    periods: int = 3,
) -> Condition:
    trend = _consecutive_direction(_holder_series(holder_rows, aliases), periods, direction)
    return _condition(id_, label, trend)


def _broker_value(row: dict[str, Any] | pd.Series | None, aliases: tuple[str, ...]) -> float | None:
    return _row_value(row, aliases)


def _institutional_accumulation_watch(institutions: pd.DataFrame | None) -> ChipTemplate:
    foreign = _institutional_lots(institutions, "foreign")
    trust = _institutional_lots(institutions, "trust")
    dealer = _institutional_lots(institutions, "dealer")
    return _template(
        "institutional_accumulation_watch",
        "重點持股大增留意股",
        "bullish",
        [
            _institutional_sum_condition(foreign, "foreign_buy_1d_gt_100", "近1日外資買超合計大於100張", 1, 100, "buy"),
            _institutional_sum_condition(trust, "trust_buy_1d_gt_100", "近1日投信買超合計大於100張", 1, 100, "buy"),
            _institutional_sum_condition(dealer, "dealer_buy_1d_gt_100", "近1日自營商買超合計大於100張", 1, 100, "buy"),
            _institutional_sum_condition(foreign, "foreign_buy_5d_gt_500", "近5日外資買超合計大於500張", 5, 500, "buy"),
            _institutional_sum_condition(trust, "trust_buy_5d_gt_300", "近5日投信買超合計大於300張", 5, 300, "buy"),
            _institutional_sum_condition(dealer, "dealer_buy_5d_gt_300", "近5日自營商買超合計大於300張", 5, 300, "buy"),
        ],
    )


def _small_cap_foreign_accumulation(
    prices: pd.DataFrame | None,
    institutions: pd.DataFrame | None,
    financial_row: dict[str, Any] | pd.Series | None,
) -> ChipTemplate:
    foreign = _institutional_lots(institutions, "foreign")
    return _template(
        "small_cap_foreign_accumulation",
        "千張大戶進場散戶出",
        "bullish",
        [
            _institutional_ratio_condition(foreign, prices, "foreign_buy_5d_gt_volume_10pct", "近5日外資買超大於成交量10%", 5, 10, "buy"),
            _institutional_sum_condition(foreign, "foreign_buy_1d_gt_300", "近1日外資買超合計大於300張", 1, 300, "buy"),
            _capital_condition(financial_row, "capital_lt_30yi", "股本小於30億", 30, "lt"),
            _volume_ma_gt_condition(prices, "volume_ma5_gt_1000", "5日均量大於1000張", 5, 1000),
        ],
    )


def _insider_alignment(
    holders: pd.DataFrame | None,
    financial_row: dict[str, Any] | pd.Series | None,
) -> ChipTemplate:
    row = _merge_holder_financial_row(holders, financial_row)
    return _template(
        "insider_alignment",
        "董監持股大戶留意股",
        "bullish",
        [
            _holder_ratio_condition(row, _insider_ratio_aliases(), "director_holding_gt_40pct", "董監持股比例大於40%", 40, "gt"),
            _holder_trend_condition(row, _insider_ratio_aliases(), "insider_holding_1m_up", "內部人持股比例連續1個月增加", "up", periods=1),
        ],
    )


def _large_holder_accumulation_retail_exit(prices: pd.DataFrame | None, holders: pd.DataFrame | None) -> ChipTemplate:
    return _template(
        "large_holder_accumulation_retail_exit",
        "投信掃貨",
        "bullish",
        [
            _holder_trend_condition(holders, _holder_1000_aliases(), "holder_1000_3w_up", "1000張大戶持股比例連續3週增加", "up"),
            _holder_trend_condition(holders, _holder_200_aliases(), "holder_200_3w_down", "200張散戶持股比例連續3週減少", "down"),
            _volume_gt_condition(prices, "volume_gt_1000", "成交量大於1000張", 1000),
        ],
    )


def _trust_small_cap_accumulation(
    prices: pd.DataFrame | None,
    institutions: pd.DataFrame | None,
    financial_row: dict[str, Any] | pd.Series | None,
) -> ChipTemplate:
    trust = _institutional_lots(institutions, "trust")
    return _template(
        "trust_small_cap_accumulation",
        "中小型投信連買",
        "bullish",
        [
            _volume_gt_condition(prices, "volume_gt_1000", "成交量大於1000張", 1000),
            _capital_condition(financial_row, "capital_gt_5yi", "股本大於5億", 5, "gt"),
            _capital_condition(financial_row, "capital_lt_20yi", "股本小於20億", 20, "lt"),
            _condition("trust_5d_buy_streak", "投信連續5日買進", _positive_streak(trust, 5)),
        ],
    )


def _broker_synchronized_short(prices: pd.DataFrame | None, broker_row: dict[str, Any] | pd.Series | None) -> ChipTemplate:
    concentration = _broker_value(broker_row, ("branch_concentration_pct", "main_force_buy_concentration_pct"))
    sell_streak = _broker_value(broker_row, ("sell_branch_count_streak", "sell_branch_dominance_streak"))
    sell_ratio = _broker_value(broker_row, ("sell_branch_count_ratio", "sell_buy_branch_ratio"))
    return _template(
        "broker_synchronized_short",
        "法人同步做空",
        "bearish",
        [
            _volume_gt_condition(prices, "volume_gt_1000", "成交量大於1000張", 1000),
            _volume_ma_gt_condition(prices, "volume_ma10_gt_300", "10日均量大於300張", 10, 300),
            _condition("branch_concentration_gt_5pct", "近5日主力買超集中度大於5%", None if concentration is None else concentration > 5),
            _condition(
                "sell_branches_3d_gt_buy",
                "連續3日賣出分點大於買進分點的1倍",
                None if sell_streak is None or sell_ratio is None else sell_streak >= 3 and sell_ratio > 1,
            ),
        ],
    )


def _large_holder_distribution_retail_in(prices: pd.DataFrame | None, holders: pd.DataFrame | None) -> ChipTemplate:
    return _template(
        "large_holder_distribution_retail_in",
        "投信出貨",
        "bearish",
        [
            _holder_trend_condition(holders, _holder_1000_aliases(), "holder_1000_3w_down", "1000張大戶持股比例連續3週減少", "down"),
            _holder_trend_condition(holders, _holder_200_aliases(), "holder_200_3w_up", "200張散戶持股比例連續3週增加", "up"),
            _volume_gt_condition(prices, "volume_gt_1000", "成交量大於1000張", 1000),
        ],
    )


def _institutional_distribution_watch(institutions: pd.DataFrame | None) -> ChipTemplate:
    foreign = _institutional_lots(institutions, "foreign")
    trust = _institutional_lots(institutions, "trust")
    dealer = _institutional_lots(institutions, "dealer")
    return _template(
        "institutional_distribution_watch",
        "重點持股大減留意股",
        "bearish",
        [
            _institutional_sum_condition(foreign, "foreign_sell_1d_gt_100", "近1日外資賣超合計大於100張", 1, 100, "sell"),
            _institutional_sum_condition(trust, "trust_sell_1d_gt_100", "近1日投信賣超合計大於100張", 1, 100, "sell"),
            _institutional_sum_condition(dealer, "dealer_sell_1d_gt_100", "近1日自營商賣超合計大於100張", 1, 100, "sell"),
            _institutional_sum_condition(foreign, "foreign_sell_5d_gt_500", "近5日外資賣超合計大於500張", 5, 500, "sell"),
            _institutional_sum_condition(trust, "trust_sell_5d_gt_300", "近5日投信賣超合計大於300張", 5, 300, "sell"),
            _institutional_sum_condition(dealer, "dealer_sell_5d_gt_300", "近5日自營商賣超合計大於300張", 5, 300, "sell"),
        ],
    )


def _trust_distribution_small_cap(
    prices: pd.DataFrame | None,
    institutions: pd.DataFrame | None,
    financial_row: dict[str, Any] | pd.Series | None,
) -> ChipTemplate:
    trust = _institutional_lots(institutions, "trust")
    return _template(
        "trust_distribution_small_cap",
        "中小型投信賣超",
        "bearish",
        [
            _institutional_ratio_condition(trust, prices, "trust_sell_5d_gt_volume_10pct", "近5日投信賣超大於成交量10%", 5, 10, "sell"),
            _volume_gt_condition(prices, "volume_gt_1000", "成交量大於1000張", 1000),
            _capital_condition(financial_row, "capital_gt_5yi", "股本大於5億", 5, "gt"),
            _capital_condition(financial_row, "capital_lt_20yi", "股本小於20億", 20, "lt"),
        ],
    )


def _insider_divergence_short_squeeze(
    holders: pd.DataFrame | None,
    financial_row: dict[str, Any] | pd.Series | None,
) -> ChipTemplate:
    row = _merge_holder_financial_row(holders, financial_row)
    return _template(
        "insider_divergence_short_squeeze",
        "軋空行情",
        "caution",
        [
            _holder_ratio_condition(row, _insider_ratio_aliases(), "director_holding_gt_40pct", "董監持股比例大於40%", 40, "gt"),
            _holder_trend_condition(row, _insider_ratio_aliases(), "insider_holding_1m_down", "內部人持股比例連續1個月減少", "down", periods=1),
        ],
    )


def _merge_holder_financial_row(
    holders: pd.DataFrame | None,
    financial_row: dict[str, Any] | pd.Series | None,
) -> pd.DataFrame | None:
    """Build a tiny frame for insider columns that may come from either source."""

    parts: list[pd.DataFrame] = []
    if holders is not None and not holders.empty:
        parts.append(holders)
    if financial_row is not None:
        row_dict = financial_row.to_dict() if isinstance(financial_row, pd.Series) else dict(financial_row)
        if row_dict:
            parts.append(pd.DataFrame([row_dict]))
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True, sort=False)


def _holder_1000_aliases() -> tuple[str, ...]:
    return (
        "holder_1000_plus_pct",
        "holders_1000_plus_pct",
        "big_holder_1000_ratio_pct",
        "big_holder_ratio_pct",
    )


def _holder_200_aliases() -> tuple[str, ...]:
    return (
        "holder_200_minus_pct",
        "holders_200_minus_pct",
        "small_holder_ratio_pct",
        "retail_holder_ratio_pct",
    )


def _insider_ratio_aliases() -> tuple[str, ...]:
    return (
        "director_supervisor_holding_pct",
        "director_holding_pct",
        "insider_holding_pct",
        "insider_ratio_pct",
    )

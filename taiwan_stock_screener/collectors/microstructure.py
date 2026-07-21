from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

# V4 台股微結構策略的資料組裝（純計算，抓取在 scripts/run_screener.py）。
# 規格見 docs/V4_MICROSTRUCTURE_STRATEGIES.md；資料缺漏時對應欄位維持 None，
# 評分引擎會自動不給分（不得用推測值補分）。

_QUARTER_END_MONTHS = (3, 6, 9, 12)


def quarter_end_of(today: date) -> date:
    """回傳 today 所屬季度的最後一天。"""
    for month in _QUARTER_END_MONTHS:
        if today.month <= month:
            if month == 12:
                return date(today.year, 12, 31)
            return date(today.year, month + 1, 1) - timedelta(days=1)
    return date(today.year, 12, 31)


def days_to_quarter_end(today: date) -> int:
    return (quarter_end_of(today) - today).days


def city_of_address(address: Any) -> str | None:
    """從公司/券商地址取出縣市（前三個字，「臺」正規化為「台」）。"""
    text = str(address or "").strip().replace("臺", "台")
    if len(text) < 3:
        return None
    city = text[:3]
    return city if city.endswith(("市", "縣")) else None


def trust_net_buy_recent(institutional_rows: pd.DataFrame | None, days: int = 5) -> float | None:
    if institutional_rows is None or institutional_rows.empty:
        return None
    if "investment_trust_buy_sell" not in institutional_rows.columns:
        return None
    recent = institutional_rows.sort_values("trade_date")["investment_trust_buy_sell"].tail(days)
    if recent.empty:
        return None
    return float(pd.to_numeric(recent, errors="coerce").fillna(0).sum())


def _pick_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in frame.columns:
            return name
    return None


def disposition_fields(
    symbol: str,
    disposition_frame: pd.DataFrame | None,
    history: pd.DataFrame | None,
    holder_rows: pd.DataFrame | None,
    today: date,
) -> dict[str, Any]:
    """處置出關欄位：距處置結束天數、處置期間振幅、大戶持股是否未下滑。"""
    empty: dict[str, Any] = {
        "disposition_days_to_end": None,
        "disposition_range_pct": None,
        "big_holder_ratio_change_pp": None,
    }
    if disposition_frame is None or disposition_frame.empty:
        return empty

    frame = disposition_frame
    symbol_col = _pick_column(frame, ("stock_id", "symbol", "Code"))
    # FinMind TaiwanStockDispositionSecuritiesPeriod 實際欄位是 period_start / period_end
    start_col = _pick_column(frame, ("period_start", "start_date", "DispositionStartDate", "startDate"))
    end_col = _pick_column(frame, ("period_end", "end_date", "DispositionEndDate", "endDate"))
    if not symbol_col or not end_col:
        return empty

    rows = frame[frame[symbol_col].astype(str) == str(symbol)].copy()
    if rows.empty:
        return empty
    rows["_end"] = pd.to_datetime(rows[end_col], errors="coerce")
    rows = rows.dropna(subset=["_end"])
    # 仍在處置期間（結束日 >= 今天）的最近一筆
    active = rows[rows["_end"].dt.date >= today].sort_values("_end")
    if active.empty:
        return empty
    row = active.iloc[0]
    end_day = row["_end"].date()
    start_day = None
    if start_col and pd.notna(row.get(start_col)):
        parsed = pd.to_datetime(row.get(start_col), errors="coerce")
        if pd.notna(parsed):
            start_day = parsed.date()

    result: dict[str, Any] = dict(empty)
    result["disposition_days_to_end"] = int((end_day - today).days)

    if start_day and history is not None and not history.empty:
        window = history[pd.to_datetime(history["trade_date"]).dt.date >= start_day]
        if len(window) >= 2:
            start_close = float(window.iloc[0]["close"])
            if start_close > 0:
                span = float(window["high"].max()) - float(window["low"].min())
                result["disposition_range_pct"] = round(span / start_close * 100, 2)

    if holder_rows is not None and not holder_rows.empty and "big_holder_ratio_pct" in holder_rows.columns:
        sorted_holders = holder_rows.sort_values("date")
        if start_day:
            in_window = sorted_holders[pd.to_datetime(sorted_holders["date"]).dt.date >= start_day]
            sorted_holders = in_window if len(in_window) >= 2 else sorted_holders
        if len(sorted_holders) >= 2:
            change = float(sorted_holders.iloc[-1]["big_holder_ratio_pct"]) - float(
                sorted_holders.iloc[0]["big_holder_ratio_pct"]
            )
            result["big_holder_ratio_change_pp"] = round(change, 2)

    return result


def cb_fields(cb_daily: pd.DataFrame | None, volume_avg_days: int = 20) -> dict[str, Any]:
    """可轉債欄位：最新收盤價與成交量相對 20 日均量的倍數。"""
    empty: dict[str, Any] = {"cb_price": None, "cb_volume_ratio": None}
    if cb_daily is None or cb_daily.empty:
        return empty
    date_col = _pick_column(cb_daily, ("date", "trade_date"))
    close_col = _pick_column(cb_daily, ("close", "Close", "closing_price"))
    volume_col = _pick_column(cb_daily, ("volume", "Volume", "trading_volume", "Trading_Volume"))
    if not date_col or not close_col:
        return empty

    rows = cb_daily.sort_values(date_col)
    closes = pd.to_numeric(rows[close_col], errors="coerce").dropna()
    result: dict[str, Any] = dict(empty)
    if not closes.empty:
        result["cb_price"] = round(float(closes.iloc[-1]), 2)
    if volume_col:
        volumes = pd.to_numeric(rows[volume_col], errors="coerce").dropna()
        if len(volumes) >= 2:
            baseline = float(volumes.tail(volume_avg_days + 1).iloc[:-1].mean())
            if baseline > 0:
                result["cb_volume_ratio"] = round(float(volumes.iloc[-1]) / baseline, 2)
    return result


def geographic_fields(
    broker_frame: pd.DataFrame | None,
    trader_city_map: dict[str, str] | None,
    company_city: str | None,
) -> dict[str, Any]:
    """地緣券商欄位：同縣市分點連續淨買天數與最新一日買超佔成交量比。"""
    empty: dict[str, Any] = {
        "same_city_branch_buy_streak_days": None,
        "same_city_branch_buy_volume_pct": None,
    }
    if (
        broker_frame is None
        or broker_frame.empty
        or not trader_city_map
        or not company_city
        or not {"trade_date", "securities_trader", "buy", "sell"}.issubset(broker_frame.columns)
    ):
        return empty

    rows = broker_frame.copy()
    for column in ("buy", "sell"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce").fillna(0.0)
    rows["_city"] = rows["securities_trader"].astype(str).map(
        lambda name: trader_city_map.get(str(name).strip())
    )
    same_city = rows[rows["_city"] == company_city].copy()
    if same_city.empty:
        return {"same_city_branch_buy_streak_days": 0, "same_city_branch_buy_volume_pct": 0.0}

    same_city["_net"] = same_city["buy"] - same_city["sell"]
    daily_net = same_city.groupby("trade_date")["_net"].sum().sort_index()
    streak = 0
    for value in reversed(daily_net.tolist()):
        if value > 0:
            streak += 1
        else:
            break

    latest_day = rows["trade_date"].max()
    day_rows = rows[rows["trade_date"] == latest_day]
    day_volume = float((day_rows["buy"].sum() + day_rows["sell"].sum())) / 2
    same_city_day = same_city[same_city["trade_date"] == latest_day]
    net_buy = float(same_city_day["buy"].sum() - same_city_day["sell"].sum())
    volume_pct = round(max(0.0, net_buy) / day_volume * 100, 2) if day_volume > 0 else 0.0

    return {
        "same_city_branch_buy_streak_days": int(streak),
        "same_city_branch_buy_volume_pct": volume_pct,
    }


def build_microstructure_row(
    today: date,
    institutional_rows: pd.DataFrame | None = None,
    trust_holding_ratio_pct: float | None = None,
    disposition: dict[str, Any] | None = None,
    cb: dict[str, Any] | None = None,
    has_convertible_bond: bool | None = None,
    geographic: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """組裝評分引擎的 microstructure_row；完全沒有可用資料時回傳 None。"""
    row: dict[str, Any] = {
        "days_to_quarter_end": days_to_quarter_end(today),
        "trust_holding_ratio_pct": trust_holding_ratio_pct,
        "trust_net_buy_5d": trust_net_buy_recent(institutional_rows),
        "has_convertible_bond": has_convertible_bond,
    }
    row.update(disposition or {})
    row.update(cb or {})
    row.update(geographic or {})

    informative = [
        row.get("trust_net_buy_5d"),
        row.get("trust_holding_ratio_pct"),
        row.get("disposition_days_to_end"),
        row.get("cb_price"),
        row.get("cb_volume_ratio"),
        row.get("same_city_branch_buy_streak_days"),
    ]
    if all(value is None for value in informative):
        return None
    return row

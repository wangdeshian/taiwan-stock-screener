from __future__ import annotations

from typing import Any

import pandas as pd

# 券商分點資金流分析（FinMind Sponsor 等級的 TaiwanStockTradingDailyReport）。
#
# 領域知識依據（docs/DOMAIN_KNOWLEDGE.md 三、籌碼面）：
# - 前十大買超分點佔總成交量 50% 以上且連續 3~5 天 = 主力有計畫吸貨
# - 隔日沖分點（凱基松山、元大土城永寧等）進場 = 紊亂期，籌碼紅綠交錯易洗盤
# - 隔日沖退場＋法人續買 = 回穩期，主升浪最佳進場點
# - 主力成本線：主力分點平均買均價；股價剛站上成本線推升動力強

REQUIRED_COLUMNS = {"trade_date", "securities_trader", "buy", "sell"}


def _normalize_name(name: str) -> str:
    return "".join(ch for ch in str(name) if ch not in " -－‐–—　()（）")


def is_blacklisted(trader: str, blacklist: list[str]) -> bool:
    """隔日沖分點比對：正規化後子字串包含即算命中（分點名稱格式各家不一）。"""
    normalized = _normalize_name(trader)
    return any(_normalize_name(entry) in normalized for entry in blacklist if entry)


def analyze_broker_flow(
    frame: pd.DataFrame,
    day_trade_blacklist: list[str] | None = None,
    top_n: int = 10,
    concentration_pct: float = 50.0,
    streak_days: int = 3,
    churn_max_ratio_pct: float = 15.0,
    cost_lookback_days: int = 10,
) -> dict[str, Any] | None:
    """分析單一股票的分點資金流。

    輸入欄位：trade_date, securities_trader, buy, sell（股數），price（選填，成本線用）。
    回傳（皆為近 cost_lookback_days 的統計）：
    - branch_concentration_pct：最新一日前 top_n 買超分點淨買超佔當日總成交比重
    - branch_concentration_streak：集中度連續達標天數
    - day_trade_branch_ratio_pct：隔日沖黑名單分點成交佔比（買+賣 / 全體買+賣）
    - main_cost_line / close_above_cost：主力（累計淨買超前 top_n 分點）加權平均買進成本
    - chip_stage：accumulation（吸貨/回穩）｜churn（紊亂）｜quiet（無明顯訊號）
    """
    if frame is None or frame.empty or not REQUIRED_COLUMNS.issubset(frame.columns):
        return None

    rows = frame.copy()
    rows["trade_date"] = pd.to_datetime(rows["trade_date"])
    for column in ("buy", "sell"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce").fillna(0.0)
    rows["net"] = rows["buy"] - rows["sell"]
    rows["gross"] = rows["buy"] + rows["sell"]

    recent_dates = sorted(rows["trade_date"].unique())[-cost_lookback_days:]
    rows = rows[rows["trade_date"].isin(recent_dates)]
    if rows.empty:
        return None

    blacklist = day_trade_blacklist or []
    traders = rows["securities_trader"].astype(str)
    rows["is_day_trade_branch"] = traders.map(lambda name: is_blacklisted(name, blacklist))

    # 每日前 top_n 買超集中度：淨買超前 top_n 分點的淨買合計 / 當日全體 gross 的一半（≈成交量）
    daily_concentration: list[float] = []
    for _, day_rows in rows.groupby("trade_date"):
        day_volume = float(day_rows["gross"].sum()) / 2
        if day_volume <= 0:
            daily_concentration.append(0.0)
            continue
        top_net = float(day_rows.nlargest(top_n, "net")["net"].clip(lower=0).sum())
        daily_concentration.append(top_net / day_volume * 100)

    latest_concentration = daily_concentration[-1] if daily_concentration else 0.0
    streak = 0
    for value in reversed(daily_concentration):
        if value >= concentration_pct:
            streak += 1
        else:
            break

    total_gross = float(rows["gross"].sum())
    day_trade_ratio = (
        float(rows.loc[rows["is_day_trade_branch"], "gross"].sum()) / total_gross * 100
        if total_gross > 0
        else 0.0
    )

    # 主力成本線：期間累計淨買超前 top_n 的非隔日沖分點，加權平均買進價
    main_cost_line: float | None = None
    if "price" in rows.columns:
        force = rows[~rows["is_day_trade_branch"]].copy()
        force["price"] = pd.to_numeric(force["price"], errors="coerce")
        ranked = force.groupby("securities_trader")["net"].sum().nlargest(top_n)
        buyers = force[force["securities_trader"].isin(ranked[ranked > 0].index)]
        buyers = buyers[(buyers["buy"] > 0) & buyers["price"].notna()]
        total_buy = float(buyers["buy"].sum())
        if total_buy > 0:
            main_cost_line = float((buyers["price"] * buyers["buy"]).sum() / total_buy)

    if day_trade_ratio >= churn_max_ratio_pct:
        stage = "churn"
    elif streak >= streak_days:
        stage = "accumulation"
    else:
        stage = "quiet"

    return {
        "branch_concentration_pct": round(latest_concentration, 2),
        "branch_concentration_streak": int(streak),
        "day_trade_branch_ratio_pct": round(day_trade_ratio, 2),
        "main_cost_line": None if main_cost_line is None else round(main_cost_line, 2),
        "chip_stage": stage,
    }

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
import time
from typing import Any

import pandas as pd
import requests

# 全市場籌碼快照：左側潛伏策略的「起手式」偵測資料源。
#
# 與其對每一檔股票各打數次 FinMind API（全市場約 2000 檔會直接超過額度），
# 這裡用 FinMind 的「日期模式」（不帶 data_id、只帶日期），一次請求取得
# 全市場（上市＋上櫃）當日資料：融資融券餘額、借券賣出餘額、當沖統計、
# 成交量。每個交易日 4 次請求即可回補，累積成滾動歷史檔後就能對全市場
# 計算 20 日餘額趨勢。無 FinMind token 時退回 TWSE openapi 的最新快照
# （僅上市、僅融資，且盤後報表尚未發布時日期可能落後一日）。

STORE_COLUMNS = [
    "date",
    "symbol",
    "margin_balance",
    "short_balance",
    "margin_short_balance",  # 融券餘額（券資比 = 融券/融資 用）
    "day_trade_volume",
    "total_volume",
]
KEEP_TRADING_DAYS = 45

# FinMind 日期模式 dataset → 欄位對應（欄位名為 FinMind 固定英文名）
FINMIND_CHIP_DATASETS: dict[str, dict[str, str]] = {
    "TaiwanStockMarginPurchaseShortSale": {
        "MarginPurchaseTodayBalance": "margin_balance",
        "ShortSaleTodayBalance": "margin_short_balance",  # 融券餘額，借券資料缺漏時的替代
    },
    "TaiwanDailyShortSaleBalances": {
        "SBLShortSalesCurrentDayBalance": "short_balance",  # 借券賣出餘額
    },
    "TaiwanStockDayTrading": {
        "Volume": "day_trade_volume",
    },
    "TaiwanStockPrice": {
        "Trading_Volume": "total_volume",
    },
}

TWSE_OPENAPI_URLS = {
    "margin": "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
}

# 各欄位在不同端點/語言下的關鍵字組合（全部小寫比對）
FIELD_KEYWORDS: dict[str, list[tuple[str, ...]]] = {
    "symbol": [("code",), ("證券代號",), ("股票代號",), ("stkno",), ("securitiescompanycode",)],
    "margin_balance": [
        ("marginpurchase", "todaybalance"),
        ("融資", "今日餘額"),
        ("融資", "當日餘額"),
        ("資餘額",),
    ],
    "short_balance": [
        ("sbl", "currentdaybalance"),
        ("sbl", "todaybalance"),
        ("借券賣出", "當日餘額"),
        ("借券賣出", "今日餘額"),
        ("借券賣出", "餘額"),
    ],
    "day_trade_volume": [
        ("daytrading", "volume"),
        ("daytrade", "volume"),
        ("當日沖銷", "成交股數"),
        ("當沖", "成交股數"),
    ],
    "total_volume": [
        ("totalvolume",),
        ("總成交股數",),
        ("總成交量",),
    ],
}


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in ("", "--", "-", "N/A", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pick_field(keys: Iterable[str], target: str) -> str | None:
    lowered = {key: str(key).lower() for key in keys}
    for keywords in FIELD_KEYWORDS[target]:
        for key, low in lowered.items():
            if all(word in low for word in keywords):
                # 排除比率/百分比欄位誤判成量值
                if target.endswith("volume") and ("%" in low or "rate" in low or "比率" in low or "佔" in low):
                    continue
                return key
    return None


def parse_snapshot(rows: list[dict[str, Any]], value_targets: list[str]) -> pd.DataFrame:
    """把交易所回傳的一批資料列轉成 symbol + 指定欄位的 DataFrame。"""
    if not rows:
        return pd.DataFrame()
    keys = rows[0].keys()
    symbol_key = _pick_field(keys, "symbol")
    if not symbol_key:
        return pd.DataFrame()
    value_keys = {target: _pick_field(keys, target) for target in value_targets}
    if not any(value_keys.values()):
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get(symbol_key, "")).strip()
        if not symbol or len(symbol) > 6:
            continue
        record: dict[str, Any] = {"symbol": symbol}
        has_value = False
        for target, key in value_keys.items():
            number = _to_number(row.get(key)) if key else None
            record[target] = number
            has_value = has_value or number is not None
        if has_value:
            records.append(record)
    return pd.DataFrame(records)


def _get_json(url: str, timeout: int = 30) -> Any:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_twse_snapshot_today() -> pd.DataFrame:
    """TWSE openapi 最新報表快照（無 token 時的備援，僅上市融資餘額）。

    注意：openapi 只回「最新已發布」的報表，盤後尚未發布時內容是前一交易日。
    """
    frames: list[pd.DataFrame] = []
    targets = {"margin": ["margin_balance"]}
    for kind, url in TWSE_OPENAPI_URLS.items():
        try:
            payload = _get_json(url)
            rows = payload if isinstance(payload, list) else []
            frame = parse_snapshot(rows, targets[kind])
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            print(f"WARN TWSE openapi {kind} snapshot failed: {exc}")
        time.sleep(0.4)
    return _merge_on_symbol(frames)


def fetch_finmind_chip_snapshot(finmind_fetch: Any, snapshot_date: date) -> pd.DataFrame:
    """FinMind 日期模式：指定日期一次抓全市場（上市＋上櫃）籌碼資料。

    finmind_fetch(dataset, snapshot_date) 需回傳該 dataset 當日全市場的
    list[dict]。任一 dataset 失敗只會缺對應欄位，不會整體失敗。
    """
    frames: list[pd.DataFrame] = []
    for dataset, mapping in FINMIND_CHIP_DATASETS.items():
        try:
            rows = finmind_fetch(dataset, snapshot_date)
        except Exception as exc:
            print(f"WARN FinMind bulk {dataset} {snapshot_date} failed: {exc}")
            continue
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        id_column = "stock_id" if "stock_id" in frame.columns else None
        if id_column is None:
            continue
        out = pd.DataFrame({"symbol": frame[id_column].astype(str).str.strip()})
        has_value = False
        for source, target in mapping.items():
            if source in frame.columns:
                out[target] = pd.to_numeric(frame[source], errors="coerce")
                has_value = True
        if has_value:
            frames.append(out[out["symbol"].str.len() <= 6].drop_duplicates("symbol"))
        time.sleep(0.3)

    merged = _merge_on_symbol(frames)
    if merged.empty:
        return merged
    # 借券賣出餘額缺漏時退回融券餘額；融券餘額本身保留（券資比計算用）
    if "short_balance" not in merged.columns or merged["short_balance"].isna().all():
        if "margin_short_balance" in merged.columns:
            merged["short_balance"] = merged["margin_short_balance"]
    return merged


def _merge_on_symbol(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="symbol", how="outer")
    return result


def load_chip_store(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=STORE_COLUMNS)
    try:
        store = pd.read_csv(path, dtype={"symbol": str})
    except Exception as exc:
        print(f"WARN cannot read chip store {path}: {exc}")
        return pd.DataFrame(columns=STORE_COLUMNS)
    for column in STORE_COLUMNS:
        if column not in store.columns:
            store[column] = pd.NA
    return store[STORE_COLUMNS]


def save_chip_store(store: pd.DataFrame, path: Path, keep_days: int = KEEP_TRADING_DAYS) -> pd.DataFrame:
    store = store.drop_duplicates(subset=["date", "symbol"], keep="last")
    dates = sorted(store["date"].unique())
    if len(dates) > keep_days:
        store = store[store["date"].isin(dates[-keep_days:])]
    store = store.sort_values(["date", "symbol"])
    path.parent.mkdir(parents=True, exist_ok=True)
    store.to_csv(path, index=False)
    return store


def _recent_weekdays(days_back: int, until: date | None = None) -> list[date]:
    end = until or date.today()
    candidates = [end - timedelta(days=offset) for offset in range(days_back)]
    return sorted(day for day in candidates if day.weekday() < 5)


def _is_partial_row_date(store: pd.DataFrame, day_text: str) -> bool:
    """該日資料是否只有融資欄（舊版 TWSE 備援寫入的殘缺列），可被完整資料覆蓋。"""
    rows = store[store["date"].astype(str) == day_text]
    if rows.empty:
        return False
    for column in ("short_balance", "day_trade_volume"):
        if column in rows.columns and pd.to_numeric(rows[column], errors="coerce").notna().any():
            return False
    return True


def refresh_chip_store(
    path: Path,
    today_volumes: dict[str, float] | None = None,
    finmind_fetch: Any = None,
    backfill_days: int = 40,
    max_backfill_dates: int = 15,
) -> tuple[pd.DataFrame, list[str]]:
    """更新滾動籌碼快照檔並回傳 (store, 成功的資料源列表)。

    有 finmind_fetch 時：以 FinMind 日期模式回補最近缺少的交易日
    （每日 4 次請求，涵蓋上市＋上櫃；當日盤後資料未發布時自動留到下次補）。
    無 finmind_fetch 時：退回 TWSE openapi 最新快照（僅上市融資餘額）。
    最後修剪至 KEEP_TRADING_DAYS 個交易日。
    """
    store = load_chip_store(path)
    sources: list[str] = []
    new_frames: list[pd.DataFrame] = []
    today_text = date.today().isoformat()
    finmind_unavailable = finmind_fetch is None

    if finmind_fetch is not None:
        existing = {
            day for day in store["date"].astype(str)
            if not _is_partial_row_date(store, day)
        }
        missing = [day for day in _recent_weekdays(backfill_days) if day.isoformat() not in existing]
        consecutive_failures = 0
        backfilled = 0
        for day in missing[-max_backfill_dates:]:
            frame = fetch_finmind_chip_snapshot(finmind_fetch, day)
            if frame.empty:
                consecutive_failures += 1
                # 連續失敗多為帳號等級不足（全市場查詢需 FinMind Sponsor）
                # 或額度用盡，直接停止；假日空資料不常連續 3 天
                if consecutive_failures >= 3 and backfilled == 0:
                    print("WARN FinMind bulk backfill unavailable (whole-market queries need sponsor tier); falling back to TWSE snapshot")
                    finmind_unavailable = True
                    break
                continue
            consecutive_failures = 0
            backfilled += 1
            frame["date"] = day.isoformat()
            new_frames.append(frame)
        if backfilled:
            sources.append(f"FinMind×{backfilled}")

    if not new_frames and finmind_unavailable:
        # FinMind 批次不可用（無 token 或等級不足）：改用 TWSE openapi 最新
        # 報表逐日累積。注意 openapi 回「最新已發布」報表，盤後未發布時內容
        # 是前一交易日，日期會偏移一天；隨每日累積趨勢仍然成立。
        already_has_today = today_text in set(store["date"].astype(str))
        if not already_has_today:
            twse_today = fetch_twse_snapshot_today()
            if not twse_today.empty:
                twse_today["date"] = today_text
                new_frames.append(twse_today)
                sources.append("TWSE")

    if new_frames:
        combined = pd.concat([store, *new_frames], ignore_index=True, sort=False)
        combined = _fill_total_volume(combined, today_volumes, today_text)
        store = save_chip_store(combined, path)
    return store, sources


def _fill_total_volume(store: pd.DataFrame, today_volumes: dict[str, float] | None, today_text: str) -> pd.DataFrame:
    if not today_volumes:
        return store
    if "total_volume" not in store.columns:
        store["total_volume"] = pd.NA
    mask = (store["date"] == today_text) & (store["total_volume"].isna())
    store.loc[mask, "total_volume"] = store.loc[mask, "symbol"].map(today_volumes)
    return store


def chip_rows_for(store: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """取出單一股票的籌碼時序，欄位符合 LeftSideScoringEngine 的輸入格式。"""
    if store.empty:
        return pd.DataFrame()
    rows = store[store["symbol"] == str(symbol)].copy()
    if rows.empty:
        return rows
    rows["trade_date"] = pd.to_datetime(rows["date"])
    with pd.option_context("mode.chained_assignment", None):
        day_trade = pd.to_numeric(rows.get("day_trade_volume"), errors="coerce").astype(float)
        total = pd.to_numeric(rows.get("total_volume"), errors="coerce").astype(float)
        rows["day_trade_ratio_pct"] = day_trade / total.where(total > 0) * 100
    wanted = ["trade_date", "margin_balance", "short_balance", "margin_short_balance", "day_trade_ratio_pct"]
    keep = [column for column in wanted if column in rows.columns]
    return rows[keep].sort_values("trade_date")


def _trend_change_pct(series: pd.Series, lookback: int) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(lookback)
    if len(values) < 2 or float(values.iloc[0]) <= 0:
        return None
    return (float(values.iloc[-1]) - float(values.iloc[0])) / float(values.iloc[0]) * 100


def prefilter_left_symbols(
    store: pd.DataFrame,
    symbols: Iterable[str],
    limit: int,
    short_drop_pct: float = 15,
    margin_drop_pct: float = 10,
    day_trade_max_pct: float = 10,
    lookback: int = 20,
) -> pd.DataFrame:
    """全市場籌碼初選漏斗：找出有「佈局起手式」訊號的股票。

    訊號：借券賣出餘額下降（空單回補）、融資餘額下降（散戶棄守）、當沖率極低。
    回傳依訊號強度排序的前 limit 檔（欄位 symbol / signal_score /
    short_balance_change_pct / margin_balance_change_pct / day_trade_ratio_pct）。
    """
    wanted = {str(symbol) for symbol in symbols}
    if store.empty or not wanted:
        return pd.DataFrame(columns=["symbol", "signal_score"])

    subset = store[store["symbol"].isin(wanted)]
    records: list[dict[str, Any]] = []
    for symbol, rows in subset.groupby("symbol"):
        rows = rows.sort_values("date")
        short_change = _trend_change_pct(rows["short_balance"], lookback)
        margin_change = _trend_change_pct(rows["margin_balance"], lookback)

        day_trade_ratio: float | None = None
        day_trade = pd.to_numeric(rows.get("day_trade_volume"), errors="coerce").astype(float)
        total = pd.to_numeric(rows.get("total_volume"), errors="coerce").astype(float)
        ratio = (day_trade / total.where(total > 0) * 100).dropna().tail(5)
        if not ratio.empty:
            day_trade_ratio = float(ratio.mean())

        score = 0.0
        if short_change is not None and short_change < 0:
            score += 0.45 * min(1.0, -short_change / short_drop_pct)
        if margin_change is not None and margin_change < 0:
            score += 0.35 * min(1.0, -margin_change / margin_drop_pct)
        if day_trade_ratio is not None and day_trade_ratio <= day_trade_max_pct:
            score += 0.2
        if score <= 0:
            continue
        records.append(
            {
                "symbol": str(symbol),
                "signal_score": round(score, 4),
                "short_balance_change_pct": None if short_change is None else round(short_change, 2),
                "margin_balance_change_pct": None if margin_change is None else round(margin_change, 2),
                "day_trade_ratio_pct": None if day_trade_ratio is None else round(day_trade_ratio, 2),
            }
        )

    if not records:
        return pd.DataFrame(columns=["symbol", "signal_score"])
    result = pd.DataFrame(records).sort_values("signal_score", ascending=False)
    return result.head(limit).reset_index(drop=True)

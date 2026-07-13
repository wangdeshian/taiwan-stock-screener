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
# 這裡改用交易所的「全市場批次報表」，一次請求取得所有股票的當日餘額：
#   - MI_MARGN 融資融券餘額
#   - TWT93U   信用額度總量管制餘額表（含借券賣出餘額）
#   - TWTB4U   當日沖銷交易標的及成交量值
# 每日快照累積成滾動歷史檔後，即可對全市場計算 20 日餘額趨勢。

STORE_COLUMNS = ["date", "symbol", "margin_balance", "short_balance", "day_trade_volume", "total_volume"]
KEEP_TRADING_DAYS = 45

TWSE_OPENAPI_URLS = {
    "margin": "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
    "sbl": "https://openapi.twse.com.tw/v1/exchangeReport/TWT93U",
    "day_trade": "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U",
}

# rwd 端點支援 date 參數，用於回補歷史（openapi 只有最新一日）
TWSE_RWD_URLS = {
    "margin": "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json",
    "sbl": "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={d}&response=json",
    "day_trade": "https://www.twse.com.tw/rwd/zh/afterTrading/TWTB4U?date={d}&selectType=All&response=json",
}

# TPEx openapi 端點名稱可能隨版本調整，依序嘗試
TPEX_OPENAPI_CANDIDATES = {
    "margin": [
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance",
        "https://www.tpex.org.tw/openapi/v1/margin_balance",
    ],
    "day_trade": [
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daytrading",
        "https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_statistics",
    ],
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


def _rows_from_rwd_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """rwd 端點回傳 fields+data 陣列（可能多張表），挑出含證券代號的明細表。"""
    if str(payload.get("stat", "")).upper() != "OK":
        return []
    tables = payload.get("tables") or [payload]
    for table in tables:
        fields = table.get("fields") or []
        data = table.get("data") or []
        if not fields or not data:
            continue
        if _pick_field(fields, "symbol"):
            return [dict(zip(fields, row)) for row in data]
    return []


def _get_json(url: str, timeout: int = 30) -> Any:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_twse_snapshot_today() -> pd.DataFrame:
    """TWSE openapi 最新一日全市場籌碼快照。"""
    frames: list[pd.DataFrame] = []
    targets = {
        "margin": ["margin_balance"],
        "sbl": ["short_balance"],
        "day_trade": ["day_trade_volume", "total_volume"],
    }
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


def fetch_twse_snapshot_for(snapshot_date: date) -> pd.DataFrame:
    """TWSE rwd 指定日期全市場籌碼快照（用於歷史回補）。"""
    frames: list[pd.DataFrame] = []
    targets = {
        "margin": ["margin_balance"],
        "sbl": ["short_balance"],
        "day_trade": ["day_trade_volume", "total_volume"],
    }
    date_text = snapshot_date.strftime("%Y%m%d")
    for kind, template in TWSE_RWD_URLS.items():
        try:
            payload = _get_json(template.format(d=date_text))
            rows = _rows_from_rwd_payload(payload)
            frame = parse_snapshot(rows, targets[kind])
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:
            print(f"WARN TWSE rwd {kind} {date_text} failed: {exc}")
        time.sleep(0.6)
    return _merge_on_symbol(frames)


def fetch_tpex_snapshot_today() -> pd.DataFrame:
    """TPEx openapi 最新一日快照（端點名稱不穩定，逐一嘗試）。"""
    frames: list[pd.DataFrame] = []
    targets = {
        "margin": ["margin_balance", "short_balance"],
        "day_trade": ["day_trade_volume", "total_volume"],
    }
    for kind, urls in TPEX_OPENAPI_CANDIDATES.items():
        for url in urls:
            try:
                payload = _get_json(url)
                rows = payload if isinstance(payload, list) else []
                frame = parse_snapshot(rows, targets[kind])
                if not frame.empty:
                    frames.append(frame)
                    break
            except Exception as exc:
                print(f"WARN TPEx {kind} snapshot failed ({url}): {exc}")
            time.sleep(0.4)
    return _merge_on_symbol(frames)


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


def refresh_chip_store(
    path: Path,
    today_volumes: dict[str, float] | None = None,
    backfill_days: int = 40,
    max_backfill_requests: int = 30,
) -> tuple[pd.DataFrame, list[str]]:
    """更新滾動籌碼快照檔並回傳 (store, 成功的資料源列表)。

    1. 以 TWSE rwd 端點回補缺少的歷史交易日（一次最多 max_backfill_requests 天）
    2. 以 openapi 取得 TWSE / TPEx 最新一日快照
    3. 修剪至最近 KEEP_TRADING_DAYS 個交易日
    """
    store = load_chip_store(path)
    existing_dates = set(store["date"].astype(str))
    sources: list[str] = []
    new_frames: list[pd.DataFrame] = []
    today_text = date.today().isoformat()

    # 歷史回補（今天以外的缺日）
    missing = [
        day for day in _recent_weekdays(backfill_days)
        if day.isoformat() not in existing_dates and day.isoformat() != today_text
    ]
    consecutive_failures = 0
    backfilled = 0
    for day in missing[-max_backfill_requests:]:
        frame = fetch_twse_snapshot_for(day)
        if frame.empty:
            consecutive_failures += 1
            if consecutive_failures >= 3 and backfilled == 0:
                print("WARN TWSE rwd backfill unavailable; skipping remaining dates")
                break
            continue
        consecutive_failures = 0
        backfilled += 1
        frame["date"] = day.isoformat()
        new_frames.append(frame)
    if backfilled:
        sources.append(f"TWSE-backfill×{backfilled}")

    # 今日快照
    twse_today = fetch_twse_snapshot_today()
    if not twse_today.empty:
        twse_today["date"] = today_text
        new_frames.append(twse_today)
        sources.append("TWSE")
    tpex_today = fetch_tpex_snapshot_today()
    if not tpex_today.empty:
        tpex_today["date"] = today_text
        new_frames.append(tpex_today)
        sources.append("TPEx")

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
        day_trade = pd.to_numeric(rows.get("day_trade_volume"), errors="coerce")
        total = pd.to_numeric(rows.get("total_volume"), errors="coerce")
        rows["day_trade_ratio_pct"] = (day_trade / total.replace(0, pd.NA) * 100).astype(float)
    keep = ["trade_date", "margin_balance", "short_balance", "day_trade_ratio_pct"]
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
        day_trade = pd.to_numeric(rows.get("day_trade_volume"), errors="coerce")
        total = pd.to_numeric(rows.get("total_volume"), errors="coerce")
        ratio = (day_trade / total.replace(0, pd.NA) * 100).dropna().tail(5)
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

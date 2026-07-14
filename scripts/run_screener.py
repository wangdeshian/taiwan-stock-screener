#!/usr/bin/env python3
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taiwan_stock_screener.collectors.sample import (  # noqa: E402
    sample_big_holder_ratios,
    sample_chip_data,
    sample_daily_prices,
    sample_financials,
    sample_institutional_trades,
    sample_monthly_revenue,
    sample_stocks,
)
from taiwan_stock_screener.collectors.market_chip import (  # noqa: E402
    chip_rows_for,
    prefilter_left_symbols,
    refresh_chip_store,
)
from taiwan_stock_screener.indicators.technical import (  # noqa: E402
    add_technical_indicators,
    bollinger_squeeze_signal,
)
from taiwan_stock_screener.scoring.engine import ScoringEngine  # noqa: E402
from taiwan_stock_screener.scoring.left_side import LeftSideScoringEngine  # noqa: E402

TW_TZ = timezone(timedelta(hours=8))
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").replace("\r", "").replace("\n", "").strip()
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "").replace("\r", "").replace("\n", "").strip()
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
TOP_N = int(os.environ.get("SCREENER_TOP_N", "60"))
MAX_OUTPUT = int(os.environ.get("SCREENER_MAX_OUTPUT", "20"))
# 左側潛伏策略：全市場籌碼快照 → 訊號初選 → 入圍名單完整評分
LEFT_SIDE_ENABLED = os.environ.get("SCREENER_LEFT_SIDE", "1").lower() not in {"0", "false", "no"}
# 初選後進入完整評分的檔數上限（每檔需要抓歷史股價與 FinMind 資料）
LEFT_UNIVERSE_LIMIT = int(os.environ.get("SCREENER_LEFT_UNIVERSE", "50"))
# 全市場掃描的最低流動性門檻（成交金額，避免完全無量的殭屍股）
# 左側找的是「無人問津」的股票，門檻放寬：股價 5 元以上、日成交值 1 千萬以上
LEFT_MIN_TURNOVER = float(os.environ.get("SCREENER_LEFT_MIN_TURNOVER", "10000000"))
LEFT_MIN_CLOSE = float(os.environ.get("SCREENER_LEFT_MIN_CLOSE", "5"))
# 布林壓縮點火海選（yfinance 批次），依規格：股價 > 10 元、成交值 > 1 億
SQUEEZE_SCAN_ENABLED = os.environ.get("SCREENER_SQUEEZE_SCAN", "1").lower() not in {"0", "false", "no"}
SQUEEZE_MIN_CLOSE = float(os.environ.get("SCREENER_SQUEEZE_MIN_CLOSE", "10"))
SQUEEZE_MIN_TURNOVER = float(os.environ.get("SCREENER_SQUEEZE_MIN_TURNOVER", "100000000"))
CHIP_STORE_PATH = ROOT / "frontend" / "data" / "chip_history.csv"
OUTPUT = ROOT / "frontend" / "data" / "results.json"
HISTORY = ROOT / "frontend" / "data" / "history.json"
MAX_HISTORY_DAYS = 30


def finmind_get(params: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    """Centralised FinMind GET helper — passes token as Bearer header, not URL param."""
    headers: dict[str, str] = {}
    if FINMIND_TOKEN:
        headers["Authorization"] = f"Bearer {FINMIND_TOKEN}"
    response = requests.get(FINMIND_BASE, params=params, headers=headers, timeout=timeout)
    try:
        payload: dict[str, Any] = response.json()
    except Exception:
        payload = {}
    if not response.ok:
        raise RuntimeError(
            f"FinMind API failed status={response.status_code} "
            f"msg={payload.get('msg') or response.text[:200]}"
        )
    if str(payload.get("status")) not in ("200", "200.0"):
        raise RuntimeError(f"FinMind API error: status={payload.get('status')} msg={payload.get('msg')}")
    return payload


def finmind_fetch_bulk(dataset: str, snapshot_date: date) -> list[dict[str, Any]]:
    """FinMind 日期模式：不帶 data_id、指定單一日期，一次回傳全市場資料。"""
    if not FINMIND_TOKEN:
        return []
    payload = finmind_get(
        {
            "dataset": dataset,
            "start_date": snapshot_date.isoformat(),
            "end_date": snapshot_date.isoformat(),
        },
        timeout=60,
    )
    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def fugle_get(path: str, params: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    if not FUGLE_API_KEY:
        raise RuntimeError("FUGLE_API_KEY is not configured")
    response = requests.get(
        f"{FUGLE_BASE}{path}",
        params=params,
        headers={"X-API-KEY": FUGLE_API_KEY},
        timeout=timeout,
    )
    try:
        payload: dict[str, Any] = response.json()
    except Exception:
        payload = {}
    if not response.ok:
        raise RuntimeError(
            f"Fugle API failed status={response.status_code} "
            f"msg={payload.get('message') or payload.get('msg') or response.text[:200]}"
        )
    return payload


def safe_float(value: object) -> float:
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return 0.0


def roc_date_to_iso(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "/" in text:
        parts = text.split("/")
        if len(parts) == 3:
            return f"{int(parts[0]) + 1911:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 7:
        return f"{int(digits[:3]) + 1911:04d}-{int(digits[3:5]):02d}-{int(digits[5:7]):02d}"
    if len(digits) == 8:
        return f"{int(digits[:4]):04d}-{int(digits[4:6]):02d}-{int(digits[6:8]):02d}"
    return None


def parse_twse_rwd_quotes(payload: dict[str, Any]) -> pd.DataFrame:
    quote_date = roc_date_to_iso(payload.get("date"))
    tables = payload.get("tables") or []
    table = next((item for item in tables if "每日收盤行情" in str(item.get("title", ""))), None)
    if not table:
        return pd.DataFrame()

    fields = table.get("fields") or []
    rows = table.get("data") or []
    data: list[dict[str, Any]] = []
    for values in rows:
        row = dict(zip(fields, values))
        symbol = str(row.get("證券代號", "")).strip()
        close = safe_float(row.get("收盤價"))
        volume = safe_float(row.get("成交股數"))
        turnover = safe_float(row.get("成交金額"))
        if not symbol or close <= 0:
            continue
        data.append(
            {
                "symbol": symbol,
                "name": row.get("證券名稱", symbol),
                "market": "TWSE",
                "quote_date": quote_date,
                "quote_source": "TWSE",
                "open": safe_float(row.get("開盤價")) or close,
                "high": safe_float(row.get("最高價")) or close,
                "low": safe_float(row.get("最低價")) or close,
                "close": close,
                "volume": volume,
                "turnover": turnover or close * volume,
                "pe_ratio": safe_float(row.get("本益比")) or None,
            }
        )
    return pd.DataFrame(data)


def fetch_twse_rwd_today() -> pd.DataFrame:
    trade_date = datetime.now(TW_TZ).strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={trade_date}&type=ALLBUT0999&response=json"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("stat", "")).upper() != "OK":
        return pd.DataFrame()
    return parse_twse_rwd_quotes(payload)


def fetch_twse_openapi_latest() -> pd.DataFrame:
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    rows = response.json()
    data: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("Code", "")).strip()
        close = safe_float(row.get("ClosingPrice", 0))
        volume = safe_float(row.get("TradeVolume", 0))
        turnover = safe_float(row.get("TradeValue", 0))
        pe_ratio = safe_float(
            row.get("PEratio")
            or row.get("PERatio")
            or row.get("PriceEarningRatio")
            or row.get("P/E")
            or 0
        )
        if not symbol or close <= 0:
            continue
        data.append(
            {
                "symbol": symbol,
                "name": row.get("Name", symbol),
                "market": "TWSE",
                "quote_date": roc_date_to_iso(row.get("Date")),
                "quote_source": "TWSE-openapi",
                "open": safe_float(row.get("OpeningPrice")) or close,
                "high": safe_float(row.get("HighestPrice")) or close,
                "low": safe_float(row.get("LowestPrice")) or close,
                "close": close,
                "volume": volume,
                "turnover": turnover or close * volume,
                "pe_ratio": pe_ratio or None,
            }
        )
    return pd.DataFrame(data)


def fetch_twse_today() -> pd.DataFrame:
    try:
        frame = fetch_twse_rwd_today()
        if not frame.empty:
            return frame
    except Exception as exc:
        print(f"WARN TWSE rwd daily close fetch failed: {exc}")
    return fetch_twse_openapi_latest()


def fetch_tpex_today() -> pd.DataFrame:
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
    # TPEx openapi 偶發 5xx，重試三次再放棄（失敗會讓左側範圍只剩上市股）
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"TPEx quotes failed after retries: {last_error}")
    rows = response.json()
    data: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(
            row.get("SecuritiesCompanyCode")
            or row.get("Code")
            or row.get("SecuritiesCode")
            or ""
        ).strip()
        close = safe_float(row.get("Close", 0))
        volume = safe_float(row.get("Volume", 0))
        turnover = safe_float(row.get("Amount", 0)) or close * volume
        pe_ratio = safe_float(
            row.get("PEratio")
            or row.get("PERatio")
            or row.get("PriceEarningRatio")
            or row.get("P/E")
            or row.get("PE")
            or 0
        )
        if not symbol or close <= 0:
            continue
        data.append(
            {
                "symbol": symbol,
                "name": row.get("CompanyName") or row.get("Name") or symbol,
                "market": "TPEx",
                "quote_date": roc_date_to_iso(row.get("Date")),
                "quote_source": "TPEx",
                "open": safe_float(row.get("Open")) or close,
                "high": safe_float(row.get("High")) or close,
                "low": safe_float(row.get("Low")) or close,
                "close": close,
                "volume": volume,
                "turnover": turnover,
                "pe_ratio": pe_ratio or None,
            }
        )
    return pd.DataFrame(data)


def fetch_all_quotes() -> pd.DataFrame:
    """全市場當日行情（TWSE + TPEx），不做流動性篩選。"""
    frames: list[pd.DataFrame] = []
    for label, fetcher in (("TWSE", fetch_twse_today), ("TPEx", fetch_tpex_today)):
        try:
            frame = fetcher()
            print(f"{label}: {len(frame)} rows")
            frames.append(frame)
        except Exception as exc:
            print(f"WARN {label} quote fetch failed: {exc}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def fetch_today_universe(all_quotes: pd.DataFrame | None = None) -> pd.DataFrame:
    """右側動能策略股票池：成交值前 TOP_N 名。"""
    if all_quotes is None:
        all_quotes = fetch_all_quotes()
    if all_quotes.empty:
        return all_quotes

    today = all_quotes[(all_quotes["close"] > 10) & (all_quotes["turnover"] > 100_000_000)]
    today = today.sort_values("turnover", ascending=False).head(TOP_N)
    return today.reset_index(drop=True)


def merge_quote_into_history(history: pd.DataFrame, quote: pd.Series | dict[str, Any]) -> pd.DataFrame:
    """Keep technical indicators on the same date as the displayed close.

    Some historical providers lag behind the official exchange close. When the
    official quote is newer than the history frame, append it; when the same
    date already exists, replace that OHLCV row with the official exchange row.
    """
    if history.empty:
        return history
    quote_date = pd.to_datetime((quote.get("quote_date") if hasattr(quote, "get") else None), errors="coerce")
    close = safe_float(quote.get("close") if hasattr(quote, "get") else 0)
    if pd.isna(quote_date) or close <= 0:
        return history

    result = history.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.tz_localize(None)
    quote_day = pd.Timestamp(quote_date).tz_localize(None).normalize()
    latest_day = result["trade_date"].max().normalize()
    if quote_day < latest_day:
        return result.sort_values("trade_date")

    volume = safe_float(quote.get("volume") if hasattr(quote, "get") else 0)
    turnover = safe_float(quote.get("turnover") if hasattr(quote, "get") else 0) or close * volume
    quote_row = {
        "trade_date": quote_day,
        "open": safe_float(quote.get("open") if hasattr(quote, "get") else 0) or close,
        "high": safe_float(quote.get("high") if hasattr(quote, "get") else 0) or close,
        "low": safe_float(quote.get("low") if hasattr(quote, "get") else 0) or close,
        "close": close,
        "volume": volume,
        "turnover": turnover,
    }
    same_day = result["trade_date"].dt.normalize() == quote_day
    if same_day.any():
        for key, value in quote_row.items():
            result.loc[same_day, key] = value
    else:
        result = pd.concat([result, pd.DataFrame([quote_row])], ignore_index=True)
    return result.sort_values("trade_date").reset_index(drop=True)


def fetch_price_history(symbol: str, market: str) -> tuple[pd.DataFrame, str]:
    """依 FinMind → Fugle → yfinance 順序抓一年日線，回傳 (history, 來源)。"""
    price_source = "none"
    if FINMIND_TOKEN:
        history = fetch_history_finmind(symbol)
        time.sleep(0.2)
        price_source = "FinMind" if not history.empty else "none"
        if history.empty:
            print(f"WARN FinMind unavailable for {symbol}; trying Fugle/yfinance")
    else:
        history = pd.DataFrame()

    if history.empty and FUGLE_API_KEY:
        history = fetch_history_fugle(symbol)
        time.sleep(0.2)
        price_source = "Fugle" if not history.empty else price_source

    if history.empty:
        history = fetch_history_yfinance(symbol, market)
        price_source = "yfinance" if not history.empty else price_source

    return history, price_source


def fetch_twse_pe_ratios() -> dict[str, float]:
    """Fetch PE ratios for all TWSE-listed stocks from BWIBBU_ALL endpoint.

    STOCK_DAY_ALL does not carry PE data; BWIBBU_ALL does.
    Returns a dict mapping stock code → PE ratio (float > 0 only).
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        result: dict[str, float] = {}
        for row in response.json():
            code = str(row.get("Code", "")).strip()
            pe_str = str(row.get("PEratio", "")).replace(",", "").strip()
            try:
                pe = float(pe_str)
                if code and pe > 0:
                    result[code] = pe
            except (ValueError, TypeError):
                pass
        print(f"TWSE BWIBBU: fetched PE for {len(result)} stocks")
        return result
    except Exception as exc:
        print(f"WARN TWSE PE ratio fetch failed: {exc}")
        return {}


def fetch_history_yfinance(symbol: str, market: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()

    suffix = ".TW" if market == "TWSE" else ".TWO"
    try:
        history = yf.Ticker(f"{symbol}{suffix}").history(period="1y", auto_adjust=True)
    except Exception as exc:
        print(f"WARN yfinance history failed for {symbol}: {exc}")
        return pd.DataFrame()
    if history.empty:
        return pd.DataFrame()

    history = history.reset_index()
    history = history.rename(
        columns={
            "Date": "trade_date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    history["trade_date"] = pd.to_datetime(history["trade_date"]).dt.tz_localize(None)
    history["turnover"] = history["close"] * history["volume"]
    keep = ["trade_date", "open", "high", "low", "close", "volume", "turnover"]
    return history[keep].sort_values("trade_date")


def fetch_history_fugle(symbol: str) -> pd.DataFrame:
    if not FUGLE_API_KEY:
        return pd.DataFrame()

    end = date.today()
    start = end - timedelta(days=370)
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timeframe": "D",
        "adjusted": "true",
        "fields": "open,high,low,close,volume,turnover,change",
        "sort": "asc",
    }
    try:
        payload = fugle_get(f"/historical/candles/{symbol}", params=params)
    except Exception as exc:
        print(f"WARN Fugle history failed for {symbol}: {exc}")
        return pd.DataFrame()

    data = payload.get("data", [])
    if not data:
        return pd.DataFrame()

    frame = pd.DataFrame(data)
    frame = frame.rename(columns={"date": "trade_date"})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    for column in ["open", "high", "low", "close", "volume", "turnover"]:
        frame[column] = pd.to_numeric(frame.get(column, 0), errors="coerce").fillna(0)
    keep = ["trade_date", "open", "high", "low", "close", "volume", "turnover"]
    return frame[keep].sort_values("trade_date")


def fetch_benchmark_history() -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()

    try:
        history = yf.Ticker("^TWII").history(period="1y", auto_adjust=True)
    except Exception as exc:
        print(f"WARN benchmark history failed: {exc}")
        return pd.DataFrame()
    if history.empty:
        return pd.DataFrame()

    history = history.reset_index()
    history = history.rename(columns={"Date": "trade_date", "Close": "close"})
    history["trade_date"] = pd.to_datetime(history["trade_date"]).dt.tz_localize(None)
    history["close"] = pd.to_numeric(history["close"], errors="coerce").fillna(0)
    return history[["trade_date", "close"]].sort_values("trade_date")


def trailing_return_pct(history: pd.DataFrame, window: int) -> float | None:
    if history.empty or len(history) <= window:
        return None
    close = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(close) <= window:
        return None
    previous = float(close.iloc[-window - 1])
    latest = float(close.iloc[-1])
    if previous <= 0:
        return None
    return round((latest - previous) / previous * 100, 2)


def relative_strength_metrics(history: pd.DataFrame, benchmark: pd.DataFrame) -> dict[str, float | None]:
    stock_20d = trailing_return_pct(history, 20)
    stock_60d = trailing_return_pct(history, 60)
    benchmark_20d = trailing_return_pct(benchmark, 20)
    benchmark_60d = trailing_return_pct(benchmark, 60)
    return {
        "stock_return_20d_pct": stock_20d,
        "stock_return_60d_pct": stock_60d,
        "benchmark_return_20d_pct": benchmark_20d,
        "benchmark_return_60d_pct": benchmark_60d,
        "relative_strength_20d_pct": round(stock_20d - benchmark_20d, 2)
        if stock_20d is not None and benchmark_20d is not None
        else None,
        "relative_strength_60d_pct": round(stock_60d - benchmark_60d, 2)
        if stock_60d is not None and benchmark_60d is not None
        else None,
    }


def fetch_history_finmind(symbol: str) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=370)
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    try:
        payload = finmind_get(params)
        data = payload.get("data", [])
    except Exception as exc:
        print(f"WARN FinMind history failed for {symbol}: {exc}")
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()

    frame = pd.DataFrame(data)
    frame = frame.rename(
        columns={
            "date": "trade_date",
            "max": "high",
            "min": "low",
            "Trading_Volume": "volume",
            "Trading_money": "turnover",
        }
    )
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    for column in ["open", "high", "low", "close", "volume", "turnover"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    keep = ["trade_date", "open", "high", "low", "close", "volume", "turnover"]
    return frame[keep].sort_values("trade_date")


def fetch_institutional_finmind(symbol: str) -> pd.DataFrame:
    if not FINMIND_TOKEN:
        return pd.DataFrame()

    end = date.today()
    start = end - timedelta(days=14)
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    try:
        payload = finmind_get(params, timeout=20)
        data = payload.get("data", [])
    except Exception as exc:
        print(f"WARN FinMind institutional failed for {symbol}: {exc}")
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()

    frame = pd.DataFrame(data)
    frame["trade_date"] = pd.to_datetime(frame["date"])
    # FinMind 回傳 buy / sell 欄位，需自行計算 buy_sell
    frame["buy"] = pd.to_numeric(frame["buy"], errors="coerce").fillna(0)
    frame["sell"] = pd.to_numeric(frame["sell"], errors="coerce").fillna(0)
    frame["buy_sell"] = frame["buy"] - frame["sell"]

    def sum_by_keywords(*keywords: str) -> pd.Series:
        mask = frame["name"].astype(str).apply(lambda value: any(key in value for key in keywords))
        return frame[mask].groupby("trade_date")["buy_sell"].sum()

    dates = pd.DataFrame({"trade_date": sorted(frame["trade_date"].unique())})
    result = dates.merge(
        sum_by_keywords("Foreign", "foreign", "外資").rename("foreign_buy_sell"),
        on="trade_date",
        how="left",
    )
    result = result.merge(
        sum_by_keywords("Investment", "Trust", "投信").rename("investment_trust_buy_sell"),
        on="trade_date",
        how="left",
    )
    result = result.merge(
        sum_by_keywords("Dealer", "dealer", "自營").rename("dealer_buy_sell"),
        on="trade_date",
        how="left",
    )
    return result.fillna(0)


def fetch_chip_finmind(symbol: str, price_history: pd.DataFrame) -> pd.DataFrame:
    """左側策略籌碼資料：融資餘額、借券賣出餘額、當沖率。

    回傳欄位: trade_date, margin_balance, short_balance, day_trade_ratio_pct。
    short_balance 優先使用借券賣出餘額 (SBL)，抓不到時退回融券餘額。
    """
    if not FINMIND_TOKEN:
        return pd.DataFrame()

    end = date.today()
    start = end - timedelta(days=90)
    date_params = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    # 融資融券餘額
    margin_frame = pd.DataFrame()
    try:
        payload = finmind_get({"dataset": "TaiwanStockMarginPurchaseShortSale", "data_id": symbol, **date_params}, timeout=20)
        data = payload.get("data", [])
        if data:
            frame = pd.DataFrame(data)
            frame["trade_date"] = pd.to_datetime(frame["date"])
            frame["margin_balance"] = pd.to_numeric(frame.get("MarginPurchaseTodayBalance"), errors="coerce")
            frame["margin_short_balance"] = pd.to_numeric(frame.get("ShortSaleTodayBalance"), errors="coerce")
            margin_frame = frame[["trade_date", "margin_balance", "margin_short_balance"]]
    except Exception as exc:
        print(f"WARN FinMind margin failed for {symbol}: {exc}")
    time.sleep(0.15)

    # 借券賣出餘額 (SBL)
    sbl_frame = pd.DataFrame()
    try:
        payload = finmind_get({"dataset": "TaiwanDailyShortSaleBalances", "data_id": symbol, **date_params}, timeout=20)
        data = payload.get("data", [])
        if data:
            frame = pd.DataFrame(data)
            sbl_column = next(
                (col for col in frame.columns if "SBL" in col and col.endswith("CurrentDayBalance")),
                None,
            )
            if sbl_column:
                frame["trade_date"] = pd.to_datetime(frame["date"])
                frame["sbl_balance"] = pd.to_numeric(frame[sbl_column], errors="coerce")
                sbl_frame = frame[["trade_date", "sbl_balance"]]
    except Exception as exc:
        print(f"WARN FinMind SBL failed for {symbol}: {exc}")
    time.sleep(0.15)

    # 當沖成交量 → 當沖率
    day_trade_frame = pd.DataFrame()
    try:
        payload = finmind_get({"dataset": "TaiwanStockDayTrading", "data_id": symbol, **date_params}, timeout=20)
        data = payload.get("data", [])
        if data:
            frame = pd.DataFrame(data)
            frame["trade_date"] = pd.to_datetime(frame["date"])
            frame["day_trade_volume"] = pd.to_numeric(frame.get("Volume"), errors="coerce")
            day_trade_frame = frame[["trade_date", "day_trade_volume"]]
    except Exception as exc:
        print(f"WARN FinMind day trading failed for {symbol}: {exc}")
    time.sleep(0.15)

    if margin_frame.empty and sbl_frame.empty and day_trade_frame.empty:
        return pd.DataFrame()

    frames = [frame for frame in (margin_frame, sbl_frame, day_trade_frame) if not frame.empty]
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="trade_date", how="outer")
    result = result.sort_values("trade_date")

    if "sbl_balance" in result.columns and result["sbl_balance"].notna().any():
        result["short_balance"] = result["sbl_balance"]
    elif "margin_short_balance" in result.columns:
        result["short_balance"] = result["margin_short_balance"]

    if "day_trade_volume" in result.columns and not price_history.empty:
        volumes = price_history[["trade_date", "volume"]].copy()
        volumes["trade_date"] = pd.to_datetime(volumes["trade_date"])
        result = result.merge(volumes, on="trade_date", how="left")
        day_trade = pd.to_numeric(result["day_trade_volume"], errors="coerce").astype(float)
        total = pd.to_numeric(result["volume"], errors="coerce").astype(float)
        result["day_trade_ratio_pct"] = day_trade / total.where(total > 0) * 100

    keep = [col for col in ("trade_date", "margin_balance", "short_balance", "day_trade_ratio_pct") if col in result.columns]
    return result[keep]


_HOLDERS_UNAVAILABLE = False


def fetch_holders_finmind(symbol: str) -> pd.DataFrame:
    """股權分散表（TDCC 週資料）→ 400 張以上大戶持股比例。

    回傳欄位: date, big_holder_ratio_pct。
    注意：此 dataset 在 FinMind 免費（register）等級不可用，偵測到等級錯誤後
    整輪跳過，避免對每一檔入圍股白打一次 API。
    """
    global _HOLDERS_UNAVAILABLE
    if not FINMIND_TOKEN or _HOLDERS_UNAVAILABLE:
        return pd.DataFrame()

    end = date.today()
    start = end - timedelta(days=120)
    params = {
        "dataset": "TaiwanStockHoldingSharesPer",
        "data_id": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    try:
        payload = finmind_get(params, timeout=20)
        data = payload.get("data", [])
    except Exception as exc:
        if "level" in str(exc).lower():
            _HOLDERS_UNAVAILABLE = True
            print("WARN FinMind holders dataset needs sponsor tier; skipping for the rest of this run")
        else:
            print(f"WARN FinMind holders failed for {symbol}: {exc}")
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()

    frame = pd.DataFrame(data)
    if "HoldingSharesLevel" not in frame.columns or "percent" not in frame.columns:
        return pd.DataFrame()

    def is_big_holder_level(level: str) -> bool:
        text = str(level).strip().lower()
        if "total" in text or "合計" in text:
            return False
        if "more than" in text or "以上" in text:
            return True
        first_number = text.replace(",", "").split("-")[0]
        try:
            return int(first_number) >= 400_001
        except ValueError:
            return False

    frame["percent"] = pd.to_numeric(frame["percent"], errors="coerce").fillna(0)
    big = frame[frame["HoldingSharesLevel"].apply(is_big_holder_level)]
    if big.empty:
        return pd.DataFrame()
    grouped = big.groupby("date")["percent"].sum().reset_index()
    grouped = grouped.rename(columns={"percent": "big_holder_ratio_pct"})
    grouped["date"] = pd.to_datetime(grouped["date"])
    return grouped.sort_values("date")


def fetch_revenue_finmind(symbol: str) -> dict[str, Any] | None:
    """Fetch monthly revenue and compute YoY growth % for the scoring engine.

    Returns a dict with ``revenue_yoy_pct`` key, or None on failure.
    """
    if not FINMIND_TOKEN:
        return None
    end = date.today()
    start = end - timedelta(days=450)  # ~15 months: ensures same-month from prior year
    params = {
        "dataset": "TaiwanStockMonthRevenue",
        "data_id": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    try:
        payload = finmind_get(params, timeout=20)
        data = payload.get("data", [])
    except Exception as exc:
        print(f"WARN FinMind revenue failed for {symbol}: {exc}")
        return None
    if not data:
        return None

    frame = pd.DataFrame(data)
    for col in ("revenue", "revenue_month", "revenue_year"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["revenue", "revenue_month", "revenue_year"])
    frame = frame.sort_values(["revenue_year", "revenue_month"])
    if len(frame) < 2:
        return None

    latest = frame.iloc[-1]
    cur_year = int(latest["revenue_year"])
    cur_month = int(latest["revenue_month"])
    cur_revenue = float(latest["revenue"])

    prior_mask = (frame["revenue_year"] == cur_year - 1) & (frame["revenue_month"] == cur_month)
    prior = frame[prior_mask]
    if prior.empty:
        return None
    prior_revenue = float(prior.iloc[-1]["revenue"])
    if prior_revenue <= 0:
        return None

    yoy_pct = (cur_revenue - prior_revenue) / prior_revenue * 100
    return {"revenue_yoy_pct": round(yoy_pct, 2)}


def fetch_financial_finmind(symbol: str) -> dict[str, Any] | None:
    """Fetch latest quarterly EPS (and ROE if present) from FinMind financial statements.

    Returns a dict with ``eps`` and optionally ``roe_pct`` keys, or None on failure.
    """
    if not FINMIND_TOKEN:
        return None
    end = date.today()
    start = end - timedelta(days=730)  # 2 years to capture latest quarterly report
    params = {
        "dataset": "TaiwanStockFinancialStatements",
        "data_id": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    try:
        payload = finmind_get(params, timeout=20)
        data = payload.get("data", [])
    except Exception as exc:
        print(f"WARN FinMind financial statements failed for {symbol}: {exc}")
        return None
    if not data:
        return None

    frame = pd.DataFrame(data)
    if "type" not in frame.columns or "value" not in frame.columns:
        return None

    type_upper = frame["type"].astype(str).str.upper()
    result: dict[str, Any] = {}

    # EPS
    eps_rows = frame[type_upper == "EPS"].sort_values("date")
    if not eps_rows.empty:
        val = pd.to_numeric(eps_rows.iloc[-1]["value"], errors="coerce")
        if pd.notna(val):
            result["eps"] = float(val)

    # ROE：FinMind 免費版沒有現成的獲利能力 dataset（TaiwanStockProfitability
    # 不是合法 dataset，會回 422），維持 None，評分引擎會自動略過 ROE 條件。

    return result if result else None


def chip_change_pct(chip_rows: pd.DataFrame | None, column: str, lookback: int = 20) -> float | None:
    """近 lookback 筆資料的餘額變化百分比（負值代表下降）。"""
    if chip_rows is None or chip_rows.empty or column not in chip_rows.columns:
        return None
    series = chip_rows.sort_values("trade_date")[column].dropna().tail(lookback)
    if len(series) < 2 or float(series.iloc[0]) <= 0:
        return None
    return round((float(series.iloc[-1]) - float(series.iloc[0])) / float(series.iloc[0]) * 100, 2)


def left_side_metrics(chip_rows: pd.DataFrame | None, holder_rows: pd.DataFrame | None) -> dict[str, float | None]:
    day_trade_ratio: float | None = None
    if chip_rows is not None and not chip_rows.empty and "day_trade_ratio_pct" in chip_rows.columns:
        recent = chip_rows.sort_values("trade_date")["day_trade_ratio_pct"].dropna().tail(5)
        if not recent.empty:
            day_trade_ratio = round(float(recent.mean()), 2)

    holder_gain: float | None = None
    if holder_rows is not None and not holder_rows.empty and "big_holder_ratio_pct" in holder_rows.columns:
        window = holder_rows.sort_values("date").tail(8)
        if len(window) >= 2:
            holder_gain = round(
                float(window.iloc[-1]["big_holder_ratio_pct"]) - float(window.iloc[0]["big_holder_ratio_pct"]), 2
            )

    return {
        "short_balance_change_pct": chip_change_pct(chip_rows, "short_balance"),
        "margin_balance_change_pct": chip_change_pct(chip_rows, "margin_balance"),
        "day_trade_ratio_pct": day_trade_ratio,
        "big_holder_gain_pp": holder_gain,
    }


def chip_store_summary(store: pd.DataFrame) -> dict[str, int]:
    def count_numeric_rows(column: str) -> int:
        if column not in store.columns:
            return 0
        return int(pd.to_numeric(store[column], errors="coerce").notna().sum())

    if store.empty:
        return {
            "date_count": 0,
            "row_count": 0,
            "margin_rows": 0,
            "short_rows": 0,
            "day_trade_rows": 0,
        }
    return {
        "date_count": int(store["date"].nunique()) if "date" in store.columns else 0,
        "row_count": int(len(store)),
        "margin_rows": count_numeric_rows("margin_balance"),
        "short_rows": count_numeric_rows("short_balance"),
        "day_trade_rows": count_numeric_rows("day_trade_volume"),
    }


def build_left_observation_shortlist(
    universe: pd.DataFrame,
    limit: int,
    exclude_symbols: set[str] | None = None,
) -> pd.DataFrame:
    """Fallback shortlist used before chip history has enough days to form trends.

    The official left-side signal still comes from the chip funnel. Until that
    rolling store has enough dates, publish a liquid observation pool so the
    dashboard remains useful instead of showing an empty left-side tab.
    Symbols already covered by the momentum screen are excluded so the two
    tabs don't show the same hot stocks.
    """
    columns = [
        "symbol",
        "signal_score",
        "short_balance_change_pct",
        "margin_balance_change_pct",
        "day_trade_ratio_pct",
    ]
    if universe.empty or limit <= 0:
        return pd.DataFrame(columns=columns)

    pool = universe.copy()
    pool["symbol"] = pool["symbol"].astype(str)
    pool["turnover"] = pd.to_numeric(pool.get("turnover"), errors="coerce").fillna(0)
    pool["close"] = pd.to_numeric(pool.get("close"), errors="coerce").fillna(0)
    pool = pool[(pool["close"] > LEFT_MIN_CLOSE) & (pool["turnover"] > 0)]
    if exclude_symbols:
        pool = pool[~pool["symbol"].isin({str(symbol) for symbol in exclude_symbols})]
    if pool.empty:
        return pd.DataFrame(columns=columns)

    result = pool.sort_values("turnover", ascending=False).head(limit).copy()
    result["signal_score"] = 0.0
    result["short_balance_change_pct"] = pd.NA
    result["margin_balance_change_pct"] = pd.NA
    result["day_trade_ratio_pct"] = pd.NA
    return result[columns].reset_index(drop=True)


def serialize_left_candidate(
    symbol: str,
    name: str,
    market: str,
    industry: str | None,
    result: Any,
    close: float | None = None,
    chip_metrics: dict[str, float | None] | None = None,
    price_source: str | None = None,
    quote_date: str | None = None,
    quote_source: str | None = None,
) -> dict[str, Any]:
    plan = result.trade_plan
    metrics = chip_metrics or {}
    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "industry": industry,
        "price_source": price_source,
        "strategy": "left_side",
        "total_score": result.total_score,
        "base_structure_score": result.base_structure_score,
        "short_covering_score": result.short_covering_score,
        "retail_capitulation_score": result.retail_capitulation_score,
        "smart_money_score": result.smart_money_score,
        "fundamental_safety_score": result.fundamental_safety_score,
        "sentiment_score": result.sentiment_score,
        "ignition_score": result.ignition_score,
        "bb_bandwidth_pctile": result.bb_bandwidth_percentile,
        "reasons": result.reasons,
        "close_price": round_optional(close, 2),
        "quote_date": quote_date,
        "quote_source": quote_source,
        "entry_price": plan.entry_price if plan else None,
        "alternate_entry_price": plan.alternate_entry_price if plan else None,
        "stop_loss_price": plan.stop_loss_price if plan else None,
        "target_price_1": plan.target_price_1 if plan else None,
        "target_price_2": plan.target_price_2 if plan else None,
        "risk_reward_ratio": plan.risk_reward_ratio if plan else None,
        "suggested_position_pct": plan.suggested_position_pct if plan else None,
        "short_balance_change_pct": metrics.get("short_balance_change_pct"),
        "margin_balance_change_pct": metrics.get("margin_balance_change_pct"),
        "day_trade_ratio_pct": metrics.get("day_trade_ratio_pct"),
        "big_holder_gain_pp": metrics.get("big_holder_gain_pp"),
    }


def demo_output() -> dict[str, Any]:
    now_tw = datetime.now(TW_TZ)
    prices = sample_daily_prices()
    institutions = sample_institutional_trades()
    revenues = sample_monthly_revenue()
    financials = sample_financials()
    chips = sample_chip_data()
    holders = sample_big_holder_ratios()
    stock_lookup = sample_stocks().set_index("symbol").to_dict("index")
    engine = ScoringEngine()
    left_engine = LeftSideScoringEngine()
    candidates: list[dict[str, Any]] = []
    left_candidates: list[dict[str, Any]] = []

    for symbol, price_rows in prices.groupby("symbol"):
        indicators = add_technical_indicators(price_rows)
        institutional_rows = institutions[institutions["symbol"] == symbol]
        revenue_row = revenues[revenues["symbol"] == symbol].iloc[-1]
        financial_row = financials[financials["symbol"] == symbol].iloc[-1]
        result = engine.score(
            symbol=symbol,
            indicators=indicators,
            institutional_rows=institutional_rows,
            revenue_row=revenue_row,
            financial_row=financial_row,
            industry_rank_pct=1,
        )
        plan = result.trade_plan
        stock = stock_lookup.get(str(symbol), {})
        candidates.append(
            serialize_candidate(
                symbol=str(symbol),
                name=str(stock.get("name", symbol)),
                market=str(stock.get("market", "TWSE")),
                industry=stock.get("industry"),
                result=result,
                plan=plan,
            )
        )

        chip_rows = chips[chips["symbol"] == symbol]
        holder_rows = holders[holders["symbol"] == symbol]
        left_result = left_engine.score(
            symbol=str(symbol),
            indicators=indicators,
            chip_rows=chip_rows,
            holder_rows=holder_rows,
            institutional_rows=institutional_rows,
            revenue_row=revenue_row,
            financial_row=financial_row,
        )
        left_candidates.append(
            serialize_left_candidate(
                symbol=str(symbol),
                name=str(stock.get("name", symbol)),
                market=str(stock.get("market", "TWSE")),
                industry=stock.get("industry"),
                result=left_result,
                close=float(indicators.iloc[-1]["close"]),
                chip_metrics=left_side_metrics(chip_rows, holder_rows),
                quote_date=None,
                quote_source="demo",
            )
        )

    candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    left_candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    return {
        "updated_at": now_tw.isoformat(),
        "screened_count": int(prices["symbol"].nunique()),
        "candidate_count": len(candidates[:MAX_OUTPUT]),
        "has_institutional_data": True,
        "has_fugle_data": False,
        "data_sources": ["demo"],
        "score_threshold": 85,
        "source": "demo",
        "top_candidates": candidates[:MAX_OUTPUT],
        "left_side_threshold": left_engine.candidate_score,
        "left_side_candidates": left_candidates[:MAX_OUTPUT],
    }


def previous_live_output(now_tw: datetime, message: str) -> dict[str, Any] | None:
    if not OUTPUT.exists():
        return None
    try:
        previous: dict[str, Any] = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARN cannot read previous output: {exc}")
        return None

    if previous.get("source") == "demo" or not previous.get("top_candidates"):
        return None

    previous["source"] = "live_stale"
    previous["stale"] = True
    previous["refresh_attempted_at"] = now_tw.isoformat()
    previous["selection_note"] = message
    return previous


def round_optional(value: object, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return round(number, digits)


def calculate_peg_ratio(close: float, pe_ratio: float | None, revenue_yoy_pct: float | None, eps: float | None) -> float | None:
    if revenue_yoy_pct is None or revenue_yoy_pct <= 0:
        return None

    effective_pe = pe_ratio if pe_ratio and pe_ratio > 0 else None
    if effective_pe is None and eps and eps > 0:
        effective_pe = close / (eps * 4)
    if effective_pe is None or effective_pe <= 0:
        return None
    return round(effective_pe / revenue_yoy_pct, 2)


def serialize_candidate(
    symbol: str,
    name: str,
    market: str,
    industry: str | None,
    result: Any,
    plan: Any,
    close: float | None = None,
    pe_ratio: float | None = None,
    revenue_row: dict[str, Any] | None = None,
    financial_row: dict[str, Any] | None = None,
    strength_metrics: dict[str, float | None] | None = None,
    price_source: str | None = None,
    quote_date: str | None = None,
    quote_source: str | None = None,
) -> dict[str, Any]:
    revenue_yoy_pct = round_optional((revenue_row or {}).get("revenue_yoy_pct"))
    eps = round_optional((financial_row or {}).get("eps"))
    roe_pct = round_optional((financial_row or {}).get("roe_pct"))
    rounded_pe = round_optional(pe_ratio)
    peg_ratio = calculate_peg_ratio(float(close or 0), rounded_pe, revenue_yoy_pct, eps)
    metrics = strength_metrics or {}
    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "industry": industry,
        "price_source": price_source,
        "total_score": result.total_score,
        "trend_score": result.trend_score,
        "volume_score": result.volume_score,
        "institutional_score": result.institutional_score,
        "chip_score": result.chip_score,
        "fundamental_score": result.fundamental_score,
        "industry_score": result.industry_score,
        "risk_reward_score": result.risk_reward_score,
        "reasons": result.reasons,
        "close_price": round_optional(close, 2),
        "quote_date": quote_date,
        "quote_source": quote_source,
        "entry_price": plan.entry_price if plan else None,
        "alternate_entry_price": plan.alternate_entry_price if plan else None,
        "stop_loss_price": plan.stop_loss_price if plan else None,
        "target_price_1": plan.target_price_1 if plan else None,
        "target_price_2": plan.target_price_2 if plan else None,
        "risk_reward_ratio": plan.risk_reward_ratio if plan else None,
        "suggested_position_pct": plan.suggested_position_pct if plan else None,
        "revenue_yoy_pct": revenue_yoy_pct,
        "eps": eps,
        "roe_pct": roe_pct,
        "pe_ratio": rounded_pe,
        "peg_ratio": peg_ratio,
        "stock_return_20d_pct": metrics.get("stock_return_20d_pct"),
        "stock_return_60d_pct": metrics.get("stock_return_60d_pct"),
        "benchmark_return_20d_pct": metrics.get("benchmark_return_20d_pct"),
        "benchmark_return_60d_pct": metrics.get("benchmark_return_60d_pct"),
        "relative_strength_20d_pct": metrics.get("relative_strength_20d_pct"),
        "relative_strength_60d_pct": metrics.get("relative_strength_60d_pct"),
    }


def scan_bollinger_squeeze(
    universe: pd.DataFrame,
    thresholds: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """左側第一階段海選（yfinance 批次）：布林極度壓縮＋溫和點火＋收紅站上月線交集。

    對海選池（股價 > SQUEEZE_MIN_CLOSE、成交值 > SQUEEZE_MIN_TURNOVER）批次下載
    近 9 個月日 K，逐檔計算三大觸發條件。回傳 (命中表, 歷史快取)；歷史快取涵蓋
    整個海選池，第二階段直接重用，省下逐檔抓歷史的 API 成本。
    """
    columns = ["symbol", "bb_bandwidth_pctile", "ignition_volume_ratio"]
    empty = pd.DataFrame(columns=columns)
    try:
        import yfinance as yf
    except ImportError:
        print("WARN yfinance not installed; squeeze scan skipped")
        return empty, {}

    pool = universe[(universe["close"] > SQUEEZE_MIN_CLOSE) & (universe["turnover"] > SQUEEZE_MIN_TURNOVER)]
    if pool.empty:
        return empty, {}

    ticker_map: dict[str, str] = {}
    for _, row in pool.iterrows():
        symbol = str(row["symbol"])
        suffix = ".TW" if str(row.get("market", "TWSE")) == "TWSE" else ".TWO"
        ticker_map[f"{symbol}{suffix}"] = symbol

    histories: dict[str, pd.DataFrame] = {}
    hits: list[dict[str, Any]] = []
    tickers = list(ticker_map)
    chunk_size = 100
    for start in range(0, len(tickers), chunk_size):
        chunk = tickers[start:start + chunk_size]
        try:
            data = yf.download(
                tickers=chunk,
                period="9mo",
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            print(f"WARN yfinance batch download failed (offset {start}): {exc}")
            continue
        if data is None or data.empty:
            continue
        for ticker in chunk:
            symbol = ticker_map[ticker]
            try:
                frame = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
                frame = frame.dropna(subset=["Close"])
            except Exception:
                continue
            if frame.empty:
                continue
            history = frame.reset_index().rename(
                columns={
                    "Date": "trade_date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            history["trade_date"] = pd.to_datetime(history["trade_date"]).dt.tz_localize(None)
            for column in ("open", "high", "low", "close", "volume"):
                history[column] = pd.to_numeric(history.get(column), errors="coerce").fillna(0)
            history["turnover"] = history["close"] * history["volume"]
            history = history[["trade_date", "open", "high", "low", "close", "volume", "turnover"]]
            if len(history) < 60:
                continue
            histories[symbol] = history
            signal = bollinger_squeeze_signal(
                history,
                lookback_days=int(thresholds["bb_squeeze_lookback_days"]),
                extreme_percentile=float(thresholds["bb_squeeze_extreme_percentile"]),
                volume_avg_days=int(thresholds["ignition_volume_avg_days"]),
                volume_min_ratio=float(thresholds["ignition_volume_min_ratio"]),
                volume_max_ratio=float(thresholds["ignition_volume_max_ratio"]),
            )
            if signal and signal["is_squeeze_trigger"]:
                hits.append(
                    {
                        "symbol": symbol,
                        "bb_bandwidth_pctile": signal["bandwidth_percentile"],
                        "ignition_volume_ratio": signal["volume_ratio"],
                    }
                )

    print(f"Squeeze scan: pool {len(pool)} → downloaded {len(histories)} → triggers {len(hits)}")
    if not hits:
        return empty, histories
    hits_frame = pd.DataFrame(hits).sort_values("bb_bandwidth_pctile").reset_index(drop=True)
    return hits_frame, histories


def run_left_side_screener(all_quotes: pd.DataFrame, fundamentals_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """左側潛伏全市場兩段式漏斗。

    第一段（低 API 成本）有兩路訊號來源：
    1. 布林壓縮點火海選（yfinance 批次）：極度壓縮＋溫和點火＋收紅站上月線的交集
    2. 籌碼起手式（滾動快照）：借券/融資餘額下降、當沖冷清
    第二段：只對入圍股抓取法人、大戶持股並執行完整評分；歷史股價優先重用
    第一段已下載的快取。右側迴圈抓過的股票也直接重用。
    """
    left_engine = LeftSideScoringEngine()
    thresholds = left_engine.thresholds

    universe = all_quotes[(all_quotes["close"] > LEFT_MIN_CLOSE) & (all_quotes["turnover"] > LEFT_MIN_TURNOVER)]
    payload: dict[str, Any] = {
        "left_side_enabled": True,
        "left_side_threshold": left_engine.candidate_score,
        "left_side_universe_count": int(len(universe)),
        "left_side_candidates": [],
    }
    # 觀察池備援時排除右側動能已抓取的熱門股，避免兩個分頁重疊
    momentum_symbols = set(fundamentals_cache.keys())

    today_volumes = {str(row["symbol"]): safe_float(row["volume"]) for _, row in all_quotes.iterrows()}
    store, chip_sources = refresh_chip_store(
        CHIP_STORE_PATH,
        today_volumes=today_volumes,
        finmind_fetch=finmind_fetch_bulk if FINMIND_TOKEN else None,
    )
    chip_summary = chip_store_summary(store)
    payload["chip_sources"] = chip_sources
    payload["left_side_chip_dates"] = chip_summary["date_count"]
    payload["left_side_chip_rows"] = chip_summary["row_count"]
    payload["left_side_mode"] = "chip_signal"

    # 訊號一：籌碼起手式（借券/融資下降、當沖冷清）
    chip_shortlist = pd.DataFrame()
    if not store.empty:
        chip_shortlist = prefilter_left_symbols(
            store,
            universe["symbol"].astype(str),
            limit=LEFT_UNIVERSE_LIMIT,
            short_drop_pct=float(thresholds["short_balance_drop_pct"]),
            margin_drop_pct=float(thresholds["margin_drop_pct"]),
            day_trade_max_pct=float(thresholds["day_trade_ratio_max_pct"]),
            lookback=int(thresholds["short_balance_lookback_days"]),
        )

    # 訊號二：布林壓縮點火海選（不依賴籌碼快照，第一天就有訊號）
    squeeze_hits = pd.DataFrame()
    squeeze_histories: dict[str, pd.DataFrame] = {}
    if SQUEEZE_SCAN_ENABLED:
        try:
            squeeze_hits, squeeze_histories = scan_bollinger_squeeze(universe, thresholds)
        except Exception as exc:
            print(f"WARN squeeze scan failed: {exc}")
    payload["squeeze_hit_count"] = int(len(squeeze_hits))

    # 合併：壓縮點火命中優先，其次籌碼訊號，總數上限 LEFT_UNIVERSE_LIMIT
    frames = []
    if not squeeze_hits.empty:
        frames.append(squeeze_hits)
    if not chip_shortlist.empty:
        seen = set(squeeze_hits["symbol"].astype(str)) if not squeeze_hits.empty else set()
        frames.append(chip_shortlist[~chip_shortlist["symbol"].astype(str).isin(seen)])
    shortlist = (
        pd.concat(frames, ignore_index=True, sort=False).head(LEFT_UNIVERSE_LIMIT)
        if frames
        else pd.DataFrame()
    )
    payload["left_side_shortlist_count"] = int(len(shortlist))

    if shortlist.empty:
        shortlist = build_left_observation_shortlist(universe, LEFT_UNIVERSE_LIMIT, exclude_symbols=momentum_symbols)
        payload["left_side_mode"] = "observation_pool"
        payload["left_side_shortlist_count"] = int(len(shortlist))
        payload["left_side_note"] = (
            "壓縮點火與籌碼起手式今日皆無命中"
            f"（籌碼歷史 {chip_summary['date_count']} 個交易日），先顯示左側潛伏觀察池排序。"
        )
    elif not squeeze_hits.empty:
        payload["left_side_note"] = (
            f"布林壓縮點火命中 {len(squeeze_hits)} 檔"
            + (f"、籌碼起手式 {len(chip_shortlist)} 檔" if not chip_shortlist.empty else "")
            + "。"
        )
    print(
        f"Left-side funnel: universe {len(universe)} → "
        f"{payload['left_side_mode']} shortlist {len(shortlist)}"
    )
    if shortlist.empty:
        payload["left_side_note"] = "左側潛伏觀察池暫時無法建立，請稍後重新執行。"
        return payload

    quote_lookup = universe.set_index(universe["symbol"].astype(str)).to_dict("index")
    left_scored: list[dict[str, Any]] = []
    left_candidates: list[dict[str, Any]] = []
    is_observation_pool = payload["left_side_mode"] == "observation_pool"

    for index, pre_row in shortlist.iterrows():
        symbol = str(pre_row["symbol"])
        quote = quote_lookup.get(symbol)
        if quote is None:
            continue
        name = str(quote.get("name", symbol))
        market = str(quote.get("market", "TWSE"))
        print(f"[left {index + 1:03d}/{len(shortlist):03d}] {symbol} {name}")

        cached = fundamentals_cache.get(symbol)
        try:
            if cached:
                history = cached["history"]
                indicators = cached["indicators"]
                price_source = cached["price_source"]
                institutions = cached["institutions"]
                revenue_row = cached["revenue_row"]
                financial_row = cached["financial_row"]
            else:
                # 歷史股價優先重用海選階段已下載的 yfinance 快取
                history = squeeze_histories.get(symbol, pd.DataFrame())
                price_source = "yfinance" if not history.empty else "none"
                if history.empty:
                    history, price_source = fetch_price_history(symbol, market)
                history = merge_quote_into_history(history, quote)
                if history.empty or len(history) < 60:
                    continue
                indicators = add_technical_indicators(history)
                institutions = fetch_institutional_finmind(symbol)
                if FINMIND_TOKEN:
                    time.sleep(0.2)
                # 入圍的冷門股不再抓營收/財報（僅影響基本面安全 10 分），控制 API 用量
                revenue_row = None
                financial_row = None

            chip_rows = chip_rows_for(store, symbol)
            if len(chip_rows) < 10 and FINMIND_TOKEN:
                fallback = fetch_chip_finmind(symbol, history)
                if len(fallback) > len(chip_rows):
                    chip_rows = fallback

            holder_rows = fetch_holders_finmind(symbol)
            if FINMIND_TOKEN:
                time.sleep(0.15)

            left_result = left_engine.score(
                symbol=symbol,
                indicators=indicators,
                chip_rows=chip_rows if not chip_rows.empty else None,
                holder_rows=holder_rows if not holder_rows.empty else None,
                institutional_rows=institutions if institutions is not None and not institutions.empty else None,
                revenue_row=revenue_row,
                financial_row=financial_row,
            )
            left_candidate = serialize_left_candidate(
                symbol=symbol,
                name=name,
                market=market,
                industry=None,
                result=left_result,
                close=safe_float(quote.get("close")),
                chip_metrics=left_side_metrics(chip_rows, holder_rows),
                price_source=price_source,
                quote_date=str(quote.get("quote_date") or ""),
                quote_source=str(quote.get("quote_source") or market),
            )
            if is_observation_pool:
                left_candidate["reasons"] = ["observation_pool", *left_candidate.get("reasons", [])]
                left_candidate["is_observation_pool"] = True
            left_scored.append(left_candidate)
            if left_result.is_candidate:
                left_candidates.append(left_candidate)
        except Exception as exc:
            print(f"WARN left-side scoring failed for {symbol}: {exc}")

    left_scored.sort(key=lambda item: float(item["total_score"]), reverse=True)
    left_candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    if not left_candidates and left_scored:
        left_candidates = left_scored[:MAX_OUTPUT]
        if is_observation_pool:
            payload["left_side_note"] = (
                f"{payload.get('left_side_note', '')} 目前尚無股票達正式左側門檻，"
                f"先顯示觀察池分數最高前 {MAX_OUTPUT} 檔。"
            ).strip()
        else:
            payload["left_side_note"] = "沒有股票達到左側潛伏門檻，改顯示左側分數最高的排序。"
    payload["left_side_candidates"] = left_candidates[:MAX_OUTPUT]
    return payload


def run_live_screener() -> dict[str, Any]:
    now_tw = datetime.now(TW_TZ)
    all_quotes = fetch_all_quotes()
    today = fetch_today_universe(all_quotes)
    if today.empty:
        print("WARN no live universe found; keeping previous live output if available")
        previous = previous_live_output(
            now_tw,
            "本次市場資料源暫時無法完整更新，畫面保留上一版真實篩選結果。",
        )
        return previous if previous else demo_output()

    engine = ScoringEngine()
    threshold = 55 if FINMIND_TOKEN else 42
    benchmark_history = fetch_benchmark_history()
    # Fetch TWSE PE ratios once (BWIBBU_ALL has PE; STOCK_DAY_ALL does not)
    twse_pe_map = fetch_twse_pe_ratios()
    scored_candidates: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    # 右側迴圈抓過的資料留給左側入圍股重用，避免重複打 API
    fundamentals_cache: dict[str, dict[str, Any]] = {}

    for index, row in today.iterrows():
        symbol = str(row["symbol"])
        name = str(row["name"])
        market = str(row["market"])
        print(f"[{index + 1:03d}/{len(today):03d}] {symbol} {name}")

        history, price_source = fetch_price_history(symbol, market)
        history = merge_quote_into_history(history, row)
        if history.empty or len(history) < 60:
            continue

        try:
            indicators = add_technical_indicators(history)
            institutions = fetch_institutional_finmind(symbol)
            if FINMIND_TOKEN:
                time.sleep(0.2)

            # 基本面：月營收 YoY + 季EPS/ROE
            revenue_row: dict[str, Any] | None = None
            financial_row: dict[str, Any] | None = None
            if FINMIND_TOKEN:
                try:
                    revenue_row = fetch_revenue_finmind(symbol)
                    time.sleep(0.15)
                except Exception as exc:
                    print(f"WARN revenue fetch failed for {symbol}: {exc}")
                try:
                    financial_row = fetch_financial_finmind(symbol)
                    time.sleep(0.15)
                except Exception as exc:
                    print(f"WARN financial fetch failed for {symbol}: {exc}")

            fundamentals_cache[symbol] = {
                "history": history,
                "price_source": price_source,
                "indicators": indicators,
                "institutions": institutions,
                "revenue_row": revenue_row,
                "financial_row": financial_row,
            }

            result = engine.score(
                symbol=symbol,
                indicators=indicators,
                institutional_rows=institutions if not institutions.empty else None,
                revenue_row=revenue_row,
                financial_row=financial_row,
                industry_rank_pct=0.5,
            )
            strength_metrics = relative_strength_metrics(history, benchmark_history)
            if (strength_metrics.get("relative_strength_20d_pct") or 0) > 0:
                result.reasons.append("relative_strength_20d")
            if (strength_metrics.get("relative_strength_60d_pct") or 0) > 0:
                result.reasons.append("relative_strength_60d")
        except Exception as exc:
            print(f"WARN scoring failed for {symbol}: {exc}")
            continue

        # PE ratio: prefer BWIBBU_ALL (TWSE) over row field (STOCK_DAY_ALL has no PE)
        pe_ratio_val = twse_pe_map.get(symbol) or round_optional(row.get("pe_ratio"))
        candidate = serialize_candidate(
            symbol=symbol,
            name=name,
            market=market,
            industry=None,
            result=result,
            plan=result.trade_plan,
            close=safe_float(row.get("close")),
            pe_ratio=pe_ratio_val,
            revenue_row=revenue_row,
            financial_row=financial_row,
            strength_metrics=strength_metrics,
            price_source=price_source,
            quote_date=str(row.get("quote_date") or ""),
            quote_source=str(row.get("quote_source") or market),
        )
        scored_candidates.append(candidate)

        if result.total_score >= threshold:
            candidates.append(candidate)

    scored_candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    candidates = candidates[:MAX_OUTPUT]

    if LEFT_SIDE_ENABLED:
        try:
            left_payload = run_left_side_screener(all_quotes, fundamentals_cache)
        except Exception as exc:
            print(f"WARN left-side screener failed: {exc}")
            left_payload = {
                "left_side_enabled": True,
                "left_side_candidates": [],
                "left_side_note": "左側潛伏本次執行失敗，畫面沿用上一版資料。",
            }
    else:
        left_payload = {"left_side_enabled": False, "left_side_candidates": []}

    if not candidates and scored_candidates:
        print("WARN no candidates met threshold; publishing relaxed live ranking")
        candidates = scored_candidates[:MAX_OUTPUT]
        return {
            "updated_at": now_tw.isoformat(),
            "screened_count": len(today),
            "candidate_count": len(candidates),
            "has_institutional_data": bool(FINMIND_TOKEN),
            "has_fugle_data": bool(FUGLE_API_KEY),
            "score_threshold": threshold,
            "source": "live_relaxed",
            "data_sources": ["TWSE", "TPEx", "FinMind", *([] if not FUGLE_API_KEY else ["Fugle"]), "yfinance"],
            "selection_note": "No stocks met the strict threshold; showing the highest live scores.",
            "top_candidates": candidates,
            **left_payload,
        }

    if not candidates:
        print("WARN live run produced no candidates; keeping previous live output if available")
        previous = previous_live_output(
            now_tw,
            "本次沒有產生有效候選股，畫面保留上一版真實篩選結果。",
        )
        return previous if previous else demo_output()

    return {
        "updated_at": now_tw.isoformat(),
        "screened_count": len(today),
        "candidate_count": len(candidates),
        "has_institutional_data": bool(FINMIND_TOKEN),
        "has_fugle_data": bool(FUGLE_API_KEY),
        "score_threshold": threshold,
        "source": "live",
        "data_sources": ["TWSE", "TPEx", "FinMind", *([] if not FUGLE_API_KEY else ["Fugle"]), "yfinance"],
        "top_candidates": candidates,
        **left_payload,
    }


def is_trading_day() -> bool:
    """Return False on weekends (台灣假日需手動排除，至少排掉週末)."""
    if os.environ.get("SCREENER_FORCE_RUN", "").lower() in {"1", "true", "yes"}:
        print("SCREENER_FORCE_RUN enabled, running even on a non-trading day.")
        return True

    today_tw = datetime.now(TW_TZ)
    # 0=Monday, 5=Saturday, 6=Sunday
    if today_tw.weekday() >= 5:
        print(f"今天是 {['一','二','三','四','五','六','日'][today_tw.weekday()]}，非交易日，跳過篩選。")
        return False
    return True


def update_history(output: dict[str, Any]) -> None:
    """Append today's result to a rolling history file (max MAX_HISTORY_DAYS entries)."""
    if HISTORY.exists():
        try:
            entries: list[dict[str, Any]] = json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    else:
        entries = []

    today_date = str(output["updated_at"])[:10]  # YYYY-MM-DD

    # Build a compact summary for this run
    today_entry: dict[str, Any] = {
        "date": today_date,
        "updated_at": output["updated_at"],
        "source": output.get("source", "unknown"),
        "data_sources": output.get("data_sources", []),
        "screened_count": output.get("screened_count", 0),
        "candidate_count": output.get("candidate_count", 0),
        "score_threshold": output.get("score_threshold"),
        "selection_note": output.get("selection_note"),
        "candidates": [
            {
                "symbol": c["symbol"],
                "name": c["name"],
                "market": c.get("market", "TWSE"),
                "industry": c.get("industry"),
                "price_source": c.get("price_source"),
                "total_score": c.get("total_score"),
                "entry_price": c.get("entry_price"),
                "stop_loss_price": c.get("stop_loss_price"),
                "target_price_1": c.get("target_price_1"),
                "risk_reward_ratio": c.get("risk_reward_ratio"),
                "revenue_yoy_pct": c.get("revenue_yoy_pct"),
                "eps": c.get("eps"),
                "roe_pct": c.get("roe_pct"),
                "pe_ratio": c.get("pe_ratio"),
                "peg_ratio": c.get("peg_ratio"),
                "relative_strength_20d_pct": c.get("relative_strength_20d_pct"),
                "relative_strength_60d_pct": c.get("relative_strength_60d_pct"),
            }
            for c in output.get("top_candidates", [])
        ],
        "left_side_candidates": [
            {
                "symbol": c["symbol"],
                "name": c["name"],
                "market": c.get("market", "TWSE"),
                "industry": c.get("industry"),
                "total_score": c.get("total_score"),
                "entry_price": c.get("entry_price"),
                "stop_loss_price": c.get("stop_loss_price"),
                "target_price_1": c.get("target_price_1"),
                "risk_reward_ratio": c.get("risk_reward_ratio"),
                "short_balance_change_pct": c.get("short_balance_change_pct"),
                "margin_balance_change_pct": c.get("margin_balance_change_pct"),
                "day_trade_ratio_pct": c.get("day_trade_ratio_pct"),
                "big_holder_gain_pp": c.get("big_holder_gain_pp"),
            }
            for c in output.get("left_side_candidates", [])
        ],
    }

    # Replace existing entry for today or prepend
    entries = [e for e in entries if e.get("date") != today_date]
    entries.insert(0, today_entry)
    entries = entries[:MAX_HISTORY_DAYS]

    HISTORY.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"History updated: {len(entries)} entries in {HISTORY}")


def main() -> None:
    if not is_trading_day():
        return

    print("=== Taiwan Stock AI Screener ===")
    if FINMIND_TOKEN:
        print("FinMind token: configured")
    else:
        print("FinMind token: NOT configured — using demo/yfinance fallback")

    if FUGLE_API_KEY:
        print("Fugle API key: configured")
    else:
        print("Fugle API key: NOT configured")

    output = run_live_screener()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Output written to {OUTPUT}")
    print(f"Source: {output.get('source')}  Candidates: {output.get('candidate_count')}")

    update_history(output)


if __name__ == "__main__":
    main()

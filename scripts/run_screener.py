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
from taiwan_stock_screener.collectors.broker_flow import analyze_broker_flow  # noqa: E402
from taiwan_stock_screener.collectors.microstructure import (  # noqa: E402
    build_microstructure_row,
    cb_fields,
    city_of_address,
    disposition_fields,
    geographic_fields,
)
from taiwan_stock_screener.collectors.market_chip import (  # noqa: E402
    chip_rows_for,
    prefilter_left_symbols,
    refresh_chip_store,
)
from taiwan_stock_screener.config import get_settings  # noqa: E402
from taiwan_stock_screener.indicators.technical import (  # noqa: E402
    add_technical_indicators,
    bollinger_squeeze_signal,
)
from taiwan_stock_screener.scoring.engine import ScoringEngine  # noqa: E402
from taiwan_stock_screener.scoring.left_side import LeftSideScoringEngine  # noqa: E402
from taiwan_stock_screener.catalysts.events import (  # noqa: E402
    events_from_conference_rows,
    load_catalyst_events,
    nearest_catalyst_payload,
)
from taiwan_stock_screener.sector.resonance import (  # noqa: E402
    compute_sector_resonance,
    empty_sector_payload,
    load_sector_history,
    update_sector_history,
)

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
LEFT_FUNDAMENTAL_FETCH_LIMIT = int(os.environ.get("SCREENER_LEFT_FUNDAMENTAL_LIMIT", "12"))
LEFT_BRANCH_ANALYZE_LIMIT = int(os.environ.get("SCREENER_BRANCH_ANALYZE_LIMIT", "20"))
# 分點日報表單次請求延遲極高，改用執行緒池平行預抓＋總體時間預算（秒）
BRANCH_FETCH_WORKERS = int(os.environ.get("SCREENER_BRANCH_WORKERS", "8"))
BRANCH_TIME_BUDGET_SECONDS = int(os.environ.get("SCREENER_BRANCH_TIME_BUDGET", "600"))
CHIP_STORE_PATH = ROOT / "frontend" / "data" / "chip_history.csv"
CATALYST_EVENTS_PATH = ROOT / "frontend" / "data" / "catalysts.json"
SECTOR_HISTORY_PATH = ROOT / "frontend" / "data" / "sector_history.json"
OUTPUT = ROOT / "frontend" / "data" / "results.json"
HISTORY = ROOT / "frontend" / "data" / "history.json"
MAX_HISTORY_DAYS = 30

INDUSTRY_CODE_NAMES = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "07": "化學生技醫療",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險",
    "18": "貿易百貨",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療",
    "23": "油電燃氣",
    "24": "半導體",
    "25": "電腦及週邊",
    "26": "光電",
    "27": "通信網路",
    "28": "電子零組件",
    "29": "電子通路",
    "30": "資訊服務",
    "31": "其他電子",
    "32": "文化創意",
    "33": "農業科技",
    "34": "電子商務",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


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


def industry_label_from_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"-", "--", "－"}:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    code = digits.zfill(2) if digits else text
    return INDUSTRY_CODE_NAMES.get(code, text)


def infer_security_type(symbol: str, name: str | None = None) -> str | None:
    text = f"{symbol} {name or ''}"
    if str(symbol).startswith("00"):
        return "ETF/ETN"
    if "ETF" in text.upper() or "ETN" in text.upper():
        return "ETF/ETN"
    return None


def industry_for_symbol(symbol: str, name: str | None, industry_map: dict[str, str]) -> str | None:
    return infer_security_type(symbol, name) or industry_map.get(str(symbol))


def fetch_industry_map() -> dict[str, str]:
    """Fetch TWSE/TPEx company industry codes and convert them to readable labels."""
    sources = [
        (
            "TWSE",
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
            ("公司代號", "Code", "SecuritiesCompanyCode"),
            ("產業別", "SecuritiesIndustryCode"),
        ),
        (
            "TPEx",
            "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
            ("SecuritiesCompanyCode", "公司代號", "Code"),
            ("SecuritiesIndustryCode", "產業別"),
        ),
    ]
    mapping: dict[str, str] = {}
    for label, url, symbol_keys, industry_keys in sources:
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            print(f"WARN {label} industry fetch failed: {exc}")
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = next((str(row.get(key, "")).strip() for key in symbol_keys if row.get(key)), "")
            industry_code = next((row.get(key) for key in industry_keys if row.get(key)), None)
            industry = industry_label_from_code(industry_code)
            if symbol and industry:
                mapping[symbol] = industry
            # 順便記下公司所在縣市（地緣券商策略用）
            address = next((row.get(key) for key in ("住址", "Address", "address") if row.get(key)), None)
            city = city_of_address(address)
            if symbol and city:
                COMPANY_CITY_MAP[symbol] = city
        print(f"{label} industries: {sum(1 for row in rows if isinstance(row, dict))} rows")
    return mapping


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
_BROKER_FLOW_UNAVAILABLE = False
_MICRO_DATASET_UNAVAILABLE: set[str] = set()
_MICRO_DIAGNOSED: set[str] = set()
# 公司代號 → 縣市（由 fetch_industry_map 的公司基本資料順便填入，地緣券商策略用）
COMPANY_CITY_MAP: dict[str, str] = {}


def _finmind_micro_dataset(
    candidates: tuple[str, ...],
    extra_params: dict[str, Any],
    label: str,
    timeout: int = 60,
    quiet_empty: bool = False,
    require_any: tuple[str, ...] = (),
) -> pd.DataFrame:
    """V4 微結構資料集抓取：dataset 名稱逐一嘗試，錯誤自我診斷。

    只有「等級不足」或「dataset 名稱無效」才整輪封鎖該名稱；其他錯誤僅警告。
    首次成功時把欄位清單印進 log，之後欄位對不上可直接從執行紀錄查。
    """
    if not FINMIND_TOKEN:
        return pd.DataFrame()
    for dataset in candidates:
        if dataset in _MICRO_DATASET_UNAVAILABLE:
            continue
        try:
            payload = finmind_get({"dataset": dataset, **extra_params}, timeout=timeout)
            data = payload.get("data", [])
        except Exception as exc:
            message = str(exc)
            if "level" in message.lower() or "Input should be" in message:
                _MICRO_DATASET_UNAVAILABLE.add(dataset)
            print(f"WARN {label}: dataset {dataset} failed: {message[:200]}")
            continue
        if data:
            frame = pd.DataFrame(data)
            if require_any and not any(col in frame.columns for col in require_any):
                # dataset 有回資料但缺必要欄位（如 Overview 只有發行條件、無價量）→ 換下一個候選
                key = f"{label}:{dataset}"
                if key not in _MICRO_DIAGNOSED:
                    _MICRO_DIAGNOSED.add(key)
                    print(
                        f"WARN {label}: dataset {dataset} lacks {require_any}; "
                        f"columns={list(frame.columns)[:14]}"
                    )
                _MICRO_DATASET_UNAVAILABLE.add(dataset)
                continue
            if label not in _MICRO_DIAGNOSED:
                _MICRO_DIAGNOSED.add(label)
                print(f"{label}: dataset {dataset} rows={len(frame)} columns={list(frame.columns)[:14]}")
            return frame
        if not quiet_empty:
            print(f"WARN {label}: dataset {dataset} returned no data")
    return pd.DataFrame()


def fetch_disposition_finmind() -> pd.DataFrame:
    """處置有價證券公告（近 120 天，全市場一次）。"""
    start = (date.today() - timedelta(days=120)).isoformat()
    return _finmind_micro_dataset(
        ("TaiwanStockDispositionSecuritiesPeriod",),
        {"start_date": start},
        "micro-disposition",
    )


def fetch_cb_map_finmind() -> dict[str, str]:
    """可轉債總覽 → 現股代號對照（一檔股票取一檔 CB）。"""
    frame = _finmind_micro_dataset(("TaiwanStockConvertibleBondInfo",), {}, "micro-cb-info")
    if frame.empty:
        return {}
    cb_col = next((c for c in ("cb_id", "bond_id", "code", "CBCode") if c in frame.columns), None)
    if not cb_col:
        return {}
    stock_col = next((c for c in ("stock_id", "stock_code", "StockCode") if c in frame.columns), None)
    mapping: dict[str, str] = {}
    for _, row in frame.iterrows():
        cb_id = str(row[cb_col]).strip()
        if not cb_id:
            continue
        # 台灣 CB 代號慣例 = 股票代號 + 1 碼序號
        stock = str(row[stock_col]).strip() if stock_col and pd.notna(row.get(stock_col)) else cb_id[:-1]
        if stock and stock not in mapping:
            mapping[stock] = cb_id
    return mapping


def fetch_cb_daily_finmind(cb_id: str) -> pd.DataFrame:
    """單檔可轉債近 45 天日成交。

    注意：DailyOverview 只有發行條件（轉換價/賣回日），沒有價量，
    必須用 Daily（實際成交）為主，並以 require_any 驗證有收盤價欄位。
    """
    start = (date.today() - timedelta(days=45)).isoformat()
    return _finmind_micro_dataset(
        ("TaiwanStockConvertibleBondDaily", "TaiwanStockConvertibleBondDailyOverview"),
        {"data_id": cb_id, "start_date": start},
        "micro-cb-daily",
        quiet_empty=True,
        require_any=("close", "Close", "closing_price"),
    )


def fetch_investor_conferences(today: date) -> list[Any]:
    """TWSE/TPEx openapi 法說會公告 → 催化事件（自我診斷模式）。

    端點失敗只警告不中斷；成功時把筆數與鍵名印進 log，鍵名對不上照 log 修。
    """
    events: list[Any] = []
    sources = (
        ("catalyst-twse-conference", "https://openapi.twse.com.tw/v1/opendata/t187ap38_L"),
        ("catalyst-tpex-conference", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap38_O"),
    )
    for label, url in sources:
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            print(f"WARN {label}: fetch failed: {str(exc)[:160]}")
            continue
        parsed = events_from_conference_rows(rows, today=today)
        keys = list(rows[0].keys())[:10] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else []
        print(f"{label}: rows={len(rows) if isinstance(rows, list) else 'n/a'} upcoming={len(parsed)} keys={keys}")
        events.extend(parsed)
    return events


def fetch_trader_city_map() -> dict[str, str]:
    """券商（分點）基本資料 → 分點名稱對縣市。"""
    frame = _finmind_micro_dataset(("TaiwanSecuritiesTraderInfo",), {}, "micro-trader-info")
    if frame.empty:
        return {}
    name_col = next((c for c in ("securities_trader", "name") if c in frame.columns), None)
    addr_col = next((c for c in ("address", "Address", "location") if c in frame.columns), None)
    if not name_col or not addr_col:
        return {}
    mapping: dict[str, str] = {}
    for _, row in frame.iterrows():
        city = city_of_address(row.get(addr_col))
        name = str(row.get(name_col) or "").strip()
        if name and city:
            mapping[name] = city
    return mapping


def _fetch_branch_day(symbol: str, day: date) -> tuple[str, pd.DataFrame | None]:
    """抓單一股票單一天的分點報表（dataset 限制不得帶 end_date）。"""
    global _BROKER_FLOW_UNAVAILABLE
    if _BROKER_FLOW_UNAVAILABLE:
        return symbol, None
    params = {
        "dataset": "TaiwanStockTradingDailyReport",
        "data_id": symbol,
        "start_date": day.isoformat(),
    }
    try:
        payload = finmind_get(params, timeout=45)
        data = payload.get("data", [])
    except Exception as exc:
        if "level" in str(exc).lower():
            if not _BROKER_FLOW_UNAVAILABLE:
                _BROKER_FLOW_UNAVAILABLE = True
                print("WARN FinMind broker-flow dataset needs sponsor tier; skipping for the rest of this run")
        else:
            print(f"WARN FinMind broker flow failed for {symbol} {day}: {str(exc)[:160]}")
        return symbol, None
    if not data:
        return symbol, None
    frame = pd.DataFrame(data)
    if "date" not in frame.columns or "securities_trader" not in frame.columns:
        return symbol, None
    return symbol, frame


def prefetch_broker_flows(symbols: list[str], trading_days: int) -> dict[str, pd.DataFrame]:
    """平行預抓入圍名單的分點日報表。

    分點 dataset 單次請求延遲很高（伺服器要撈整天數百筆分點明細），逐檔逐日
    序列抓會讓整輪執行超過一小時；改為 (股票, 日期) 全展開後用執行緒池平行抓，
    並設總體時間預算，超時就用已到手的部分資料，不讓分點拖垮每日排程。
    """
    if not FINMIND_TOKEN or _BROKER_FLOW_UNAVAILABLE or not symbols:
        return {}

    days: list[date] = []
    cursor = date.today()
    while len(days) < trading_days + 4:  # 多取 4 天緩衝假日
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    started = time.monotonic()
    collected: dict[str, list[pd.DataFrame]] = {}
    tasks = [(symbol, day) for symbol in symbols for day in days]
    with ThreadPoolExecutor(max_workers=BRANCH_FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_branch_day, symbol, day) for symbol, day in tasks]
        for future in as_completed(futures):
            if time.monotonic() - started > BRANCH_TIME_BUDGET_SECONDS:
                print(
                    f"WARN branch prefetch exceeded {BRANCH_TIME_BUDGET_SECONDS}s budget; "
                    "continuing with partial data"
                )
                pool.shutdown(wait=False, cancel_futures=True)
                break
            symbol, frame = future.result()
            if frame is not None:
                collected.setdefault(symbol, []).append(frame)

    result: dict[str, pd.DataFrame] = {}
    for symbol, parts in collected.items():
        combined = pd.concat(parts, ignore_index=True).rename(columns={"date": "trade_date"})
        recent_days = sorted(combined["trade_date"].unique())[-trading_days:]
        combined = combined[combined["trade_date"].isin(recent_days)]
        keep = [c for c in ("trade_date", "securities_trader", "buy", "sell", "price") if c in combined.columns]
        result[symbol] = combined[keep]
    print(
        f"Branch prefetch: {len(symbols)} symbols × {len(days)} days → "
        f"{len(result)} with data in {time.monotonic() - started:.0f}s"
    )
    return result


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


def _latest_statement_value(frame: pd.DataFrame, exact_types: list[str]) -> float | None:
    """依優先序取最新一期的指定財報科目值（只做精確比對）。

    模糊字串比對（例如 contains "PROFITLOSS"）會誤抓到營業利益、稅前淨利、
    其他權益等科目，導致 ROE 算出 ±數百 % 的離譜值，故只允許精確科目名。
    """
    if frame.empty or "type" not in frame.columns or "value" not in frame.columns:
        return None
    normalized = frame["type"].astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    for exact in exact_types:
        rows = frame[normalized == exact].sort_values("date")
        if rows.empty:
            continue
        value = pd.to_numeric(rows.iloc[-1]["value"], errors="coerce")
        if pd.notna(value):
            return float(value)
    return None


def extract_financial_metrics(
    frame: pd.DataFrame,
    balance_frame: pd.DataFrame | None = None,
) -> dict[str, Any] | None:
    if frame.empty or "type" not in frame.columns or "value" not in frame.columns:
        return None
    result: dict[str, Any] = {}
    eps = _latest_statement_value(frame, ["EPS"])
    if eps is not None:
        result["eps"] = eps

    net_income = _latest_statement_value(
        frame,
        [
            "PROFITLOSSATTRIBUTABLETOOWNERSOFPARENT",
            "NETINCOMEATTRIBUTABLETOOWNERSOFPARENT",
            "INCOMEAFTERTAXES",
            "TOTALCONSOLIDATEDPROFITFORTHEPERIOD",
            "PROFITLOSS",
            "NETINCOME",
        ],
    )
    equity_types = [
        "EQUITY",
        "EQUITYATTRIBUTABLETOOWNERSOFPARENT",
        "EQUITYATTRIBUTABLETOOWNERSOFTHEPARENT",
        "TOTALEQUITY",
        "TOTALSTOCKHOLDERSEQUITY",
    ]
    # 權益只能取自資產負債表：損益表裡也有一個
    # EquityAttributableToOwnersOfParent，但那是「綜合損益歸屬母公司」，
    # 拿去當分母會算出數百 % 的假 ROE（實際執行 log 已驗證）
    equity = None
    if balance_frame is not None:
        equity = _latest_statement_value(balance_frame, equity_types)
    if net_income is not None and equity and equity > 0:
        roe_pct = net_income / equity * 4 * 100  # 單季年化估算
        # 年化估算超出 ±100% 幾乎都是抓錯科目或單位不一致，寧缺勿錯
        if -100 <= roe_pct <= 100:
            result["roe_pct"] = round(roe_pct, 2)

    return result if result else None


_STATEMENT_TYPES_LOGGED = False


def fetch_financial_finmind(symbol: str) -> dict[str, Any] | None:
    """Fetch latest quarterly EPS + annualized ROE estimate from FinMind.

    EPS/淨利來自損益表（TaiwanStockFinancialStatements）；權益總額在資產負債表
    （TaiwanStockBalanceSheet），需要多一次查詢。第一檔股票會把兩個 dataset 的
    科目清單印進 log，之後若科目名對不上可直接從執行紀錄查到正確名稱。
    """
    global _STATEMENT_TYPES_LOGGED
    if not FINMIND_TOKEN:
        return None
    end = date.today()
    start = end - timedelta(days=730)  # 2 years to capture latest quarterly report
    date_params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    try:
        payload = finmind_get({"dataset": "TaiwanStockFinancialStatements", "data_id": symbol, **date_params}, timeout=20)
        data = payload.get("data", [])
    except Exception as exc:
        print(f"WARN FinMind financial statements failed for {symbol}: {exc}")
        return None
    if not data:
        return None
    statements = pd.DataFrame(data)

    balance: pd.DataFrame | None = None
    try:
        time.sleep(0.1)
        payload = finmind_get({"dataset": "TaiwanStockBalanceSheet", "data_id": symbol, **date_params}, timeout=20)
        balance_data = payload.get("data", [])
        if balance_data:
            balance = pd.DataFrame(balance_data)
    except Exception as exc:
        print(f"WARN FinMind balance sheet failed for {symbol}: {exc}")

    if not _STATEMENT_TYPES_LOGGED and "type" in statements.columns:
        _STATEMENT_TYPES_LOGGED = True
        print(f"FinMind statement types ({symbol}): {sorted(statements['type'].astype(str).unique())[:40]}")
        if balance is not None and "type" in balance.columns:
            print(f"FinMind balance types ({symbol}): {sorted(balance['type'].astype(str).unique())[:40]}")

    return extract_financial_metrics(statements, balance)


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

    # 券資比＝最新融券餘額/融資餘額
    short_margin_ratio: float | None = None
    if chip_rows is not None and not chip_rows.empty and {"margin_short_balance", "margin_balance"}.issubset(chip_rows.columns):
        sorted_rows = chip_rows.sort_values("trade_date")
        shorts = pd.to_numeric(sorted_rows["margin_short_balance"], errors="coerce").dropna()
        margins = pd.to_numeric(sorted_rows["margin_balance"], errors="coerce").dropna()
        if not shorts.empty and not margins.empty and float(margins.iloc[-1]) > 0:
            short_margin_ratio = round(float(shorts.iloc[-1]) / float(margins.iloc[-1]) * 100, 2)

    return {
        "short_balance_change_pct": chip_change_pct(chip_rows, "short_balance"),
        "margin_balance_change_pct": chip_change_pct(chip_rows, "margin_balance"),
        "day_trade_ratio_pct": day_trade_ratio,
        "big_holder_gain_pp": holder_gain,
        "short_margin_ratio_pct": short_margin_ratio,
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


def exclude_symbols_from_shortlist(frame: pd.DataFrame, exclude_symbols: set[str] | None) -> pd.DataFrame:
    if frame.empty or not exclude_symbols or "symbol" not in frame.columns:
        return frame
    excluded = {str(symbol) for symbol in exclude_symbols}
    return frame[~frame["symbol"].astype(str).isin(excluded)].copy()


def serialize_left_candidate(
    symbol: str,
    name: str,
    market: str,
    industry: str | None,
    result: Any,
    close: float | None = None,
    revenue_row: dict[str, Any] | None = None,
    financial_row: dict[str, Any] | None = None,
    catalyst_row: dict[str, Any] | None = None,
    sector_row: dict[str, Any] | None = None,
    broker_row: dict[str, Any] | None = None,
    chip_metrics: dict[str, float | None] | None = None,
    price_source: str | None = None,
    quote_date: str | None = None,
    quote_source: str | None = None,
) -> dict[str, Any]:
    plan = result.trade_plan
    metrics = chip_metrics or {}
    revenue_yoy_pct = round_optional((revenue_row or {}).get("revenue_yoy_pct"))
    eps = round_optional((financial_row or {}).get("eps"))
    roe_pct = round_optional((financial_row or {}).get("roe_pct"))
    catalyst = catalyst_row or {}
    sector = sector_row or empty_sector_payload()
    broker = broker_row or {}
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
        "catalyst_score": result.catalyst_score,
        "sector_resonance_score": result.sector_resonance_score,
        "microstructure_score": result.microstructure_score,
        "window_dressing_score": result.window_dressing_score,
        "jailbreak_score": result.jailbreak_score,
        "cb_signal_score": result.cb_signal_score,
        "geographic_broker_score": result.geographic_broker_score,
        "sentiment_score": result.sentiment_score,
        "sentiment_available": False,
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
        "short_margin_ratio_pct": metrics.get("short_margin_ratio_pct"),
        "branch_concentration_pct": broker.get("branch_concentration_pct"),
        "branch_concentration_streak": broker.get("branch_concentration_streak"),
        "day_trade_branch_ratio_pct": broker.get("day_trade_branch_ratio_pct"),
        "main_cost_line": broker.get("main_cost_line"),
        "chip_stage": broker.get("chip_stage"),
        "revenue_yoy_pct": revenue_yoy_pct,
        "eps": eps,
        "roe_pct": roe_pct,
        "nearest_catalyst_type": catalyst.get("nearest_catalyst_type"),
        "nearest_catalyst_date": catalyst.get("nearest_catalyst_date"),
        "catalyst_days_left": catalyst.get("catalyst_days_left"),
        "catalyst_available": bool(catalyst.get("catalyst_available", False)),
        "sector_turnover_rank_pct": round_optional(sector.get("sector_turnover_rank_pct")),
        "sector_turnover_share_pct": round_optional(sector.get("sector_turnover_share_pct")),
        "sector_turnover_jump_pct": round_optional(sector.get("sector_turnover_jump_pct")),
        "sector_resonance_available": bool(sector.get("sector_resonance_available", False)),
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


def previous_left_scores(
    entries: list[dict[str, Any]],
    today_str: str,
    window_days: int,
) -> dict[str, dict[str, Any]]:
    """history.json → 每檔左側股最近一次（今天以前、window_days 內）的評分。

    給「訊號加速」用：左側起手式完成時評分會在數日內大幅跳升
    （例：2493 漲停前兩個交易日 23.5 → 63.5），單看絕對分數門檻會漏掉。
    """
    if not today_str:
        return {}
    try:
        cutoff = (date.fromisoformat(today_str) - timedelta(days=window_days)).isoformat()
    except ValueError:
        return {}
    latest: dict[str, dict[str, Any]] = {}

    def record(symbol: str, score: Any, entry_date: str) -> None:
        if not symbol or score is None:
            return
        prev = latest.get(symbol)
        if prev is None or entry_date > prev["date"]:
            latest[symbol] = {"date": entry_date, "score": float(score)}

    for entry in entries or []:
        entry_date = str(entry.get("date", ""))
        if not entry_date or entry_date >= today_str or entry_date < cutoff:
            continue
        # 完整分數表（2026-07-21 起才有）優先，涵蓋未進榜的低分股
        scores_map = entry.get("left_side_scores") or {}
        if isinstance(scores_map, dict):
            for symbol, score in scores_map.items():
                record(str(symbol).strip(), score, entry_date)
        for cand in entry.get("left_side_candidates", []) or []:
            if not isinstance(cand, dict):
                continue
            record(str(cand.get("symbol", "")).strip(), cand.get("total_score"), entry_date)
    return latest


def run_left_side_screener(
    all_quotes: pd.DataFrame,
    fundamentals_cache: dict[str, dict[str, Any]],
    industry_map: dict[str, str],
    catalyst_events: list[Any],
    sector_payloads: dict[str, dict[str, Any]],
    exclude_symbols: set[str] | None = None,
) -> dict[str, Any]:
    """左側潛伏全市場兩段式漏斗。

    第一段（低 API 成本）有兩路訊號來源：
    1. 布林壓縮點火海選（yfinance 批次）：極度壓縮＋溫和點火＋收紅站上月線的交集
    2. 籌碼起手式（滾動快照）：借券/融資餘額下降、當沖冷清
    第二段：只對入圍股抓取法人、大戶持股並執行完整評分；歷史股價優先重用
    第一段已下載的快取。右側迴圈抓過的股票也直接重用。
    """
    left_phase = _phase_timer()
    left_engine = LeftSideScoringEngine()
    thresholds = left_engine.thresholds
    catalyst_lookahead = int(thresholds["catalyst_lookahead_trading_days"])
    day_trade_blacklist = [
        str(entry) for entry in get_settings().raw["left_side"].get("day_trade_branch_blacklist", [])
    ]
    # V4 微結構的全市場資料（每輪各抓一次）
    disposition_frame = fetch_disposition_finmind()
    cb_map = fetch_cb_map_finmind()
    trader_city_map = fetch_trader_city_map()
    micro_today = datetime.now(TW_TZ).date()

    # 訊號加速：跟自己前幾天的左側評分比，跳升幅度大＝起手式正在完成
    accel_min_delta = float(thresholds.get("acceleration_min_delta", 20))
    accel_window_days = int(thresholds.get("acceleration_window_days", 5))
    accel_min_score = float(thresholds.get("acceleration_min_score", 45))
    history_entries: list[dict[str, Any]] = []
    if HISTORY.exists():
        try:
            history_entries = json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            history_entries = []
    prev_left_scores = previous_left_scores(history_entries, micro_today.isoformat(), accel_window_days)

    universe = all_quotes[(all_quotes["close"] > LEFT_MIN_CLOSE) & (all_quotes["turnover"] > LEFT_MIN_TURNOVER)]
    payload: dict[str, Any] = {
        "left_side_enabled": True,
        "left_side_threshold": left_engine.candidate_score,
        "left_side_universe_count": int(len(universe)),
        "left_side_branch_analyze_limit": LEFT_BRANCH_ANALYZE_LIMIT,
        "left_side_branch_lookback_days": int(thresholds["branch_lookback_days"]),
        "left_side_candidates": [],
    }
    # 觀察池備援時排除右側動能已抓取的熱門股，避免兩個分頁重疊
    published_momentum_symbols = {str(symbol) for symbol in (exclude_symbols or set())}
    observation_exclude_symbols = set(fundamentals_cache.keys()) | published_momentum_symbols

    today_volumes = {str(row["symbol"]): safe_float(row["volume"]) for _, row in all_quotes.iterrows()}
    store, chip_sources = refresh_chip_store(
        CHIP_STORE_PATH,
        today_volumes=today_volumes,
        finmind_fetch=finmind_fetch_bulk if FINMIND_TOKEN else None,
    )
    chip_summary = chip_store_summary(store)
    left_phase("left-chip-refresh")
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
    left_phase("left-squeeze-scan")

    # 合併：壓縮點火命中優先，其次籌碼訊號，總數上限 LEFT_UNIVERSE_LIMIT
    frames = []
    if not squeeze_hits.empty:
        frames.append(squeeze_hits)
    if not chip_shortlist.empty:
        seen = set(squeeze_hits["symbol"].astype(str)) if not squeeze_hits.empty else set()
        frames.append(chip_shortlist[~chip_shortlist["symbol"].astype(str).isin(seen)])
    if frames:
        shortlist = pd.concat(frames, ignore_index=True, sort=False)
        shortlist = exclude_symbols_from_shortlist(shortlist, published_momentum_symbols)
        shortlist = shortlist.head(LEFT_UNIVERSE_LIMIT)
    else:
        shortlist = pd.DataFrame()
    payload["left_side_shortlist_count"] = int(len(shortlist))
    payload["left_side_excluded_momentum_count"] = len(published_momentum_symbols)

    if shortlist.empty:
        shortlist = build_left_observation_shortlist(
            universe,
            LEFT_UNIVERSE_LIMIT,
            exclude_symbols=observation_exclude_symbols,
        )
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

    # 分點日報表平行預抓：只抓入圍前 LEFT_BRANCH_ANALYZE_LIMIT 檔非 ETF
    branch_symbols: list[str] = []
    for _, pre_row in shortlist.iterrows():
        sym = str(pre_row["symbol"])
        quote = quote_lookup.get(sym)
        if quote is None or infer_security_type(sym, str(quote.get("name", sym))):
            continue
        branch_symbols.append(sym)
        if len(branch_symbols) >= LEFT_BRANCH_ANALYZE_LIMIT:
            break
    branch_frames = prefetch_broker_flows(branch_symbols, int(thresholds["branch_lookback_days"]))
    if branch_frames and trader_city_map:
        # 地緣券商策略的命名對照健檢：分點報表名稱有多少能對到縣市
        branch_names = {
            str(value).strip()
            for frame in branch_frames.values()
            for value in frame["securities_trader"].astype(str)
        }
        matched = sum(1 for value in branch_names if value in trader_city_map)
        print(f"Branch trader-city match: {matched}/{len(branch_names)} branch names mapped to a city")
        if matched < len(branch_names) / 2:
            # 命中率過低＝兩邊命名格式不一致，印樣本供設計正規化規則
            unmatched = sorted(value for value in branch_names if value not in trader_city_map)[:8]
            print(f"  sample unmatched branch names: {unmatched}")
            print(f"  sample trader-info names: {sorted(trader_city_map)[:8]}")

    left_scored: list[dict[str, Any]] = []
    left_candidates: list[dict[str, Any]] = []
    is_observation_pool = payload["left_side_mode"] == "observation_pool"
    left_fundamental_fetches = 0
    left_branch_fetches = 0

    for shortlist_index, (_, pre_row) in enumerate(shortlist.iterrows(), start=1):
        symbol = str(pre_row["symbol"])
        quote = quote_lookup.get(symbol)
        if quote is None:
            continue
        name = str(quote.get("name", symbol))
        market = str(quote.get("market", "TWSE"))
        print(f"[left {shortlist_index:03d}/{len(shortlist):03d}] {symbol} {name}")

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
                revenue_row = None
                financial_row = None
                if (
                    FINMIND_TOKEN
                    and left_fundamental_fetches < LEFT_FUNDAMENTAL_FETCH_LIMIT
                    and not infer_security_type(symbol, name)
                ):
                    left_fundamental_fetches += 1
                    try:
                        revenue_row = fetch_revenue_finmind(symbol)
                        time.sleep(0.15)
                    except Exception as exc:
                        print(f"WARN left-side revenue fetch failed for {symbol}: {exc}")
                    try:
                        financial_row = fetch_financial_finmind(symbol)
                        time.sleep(0.15)
                    except Exception as exc:
                        print(f"WARN left-side financial fetch failed for {symbol}: {exc}")

            chip_rows = chip_rows_for(store, symbol)
            if len(chip_rows) < 10 and FINMIND_TOKEN:
                fallback = fetch_chip_finmind(symbol, history)
                if len(fallback) > len(chip_rows):
                    chip_rows = fallback

            holder_rows = fetch_holders_finmind(symbol)
            if FINMIND_TOKEN:
                time.sleep(0.15)

            broker_row: dict[str, Any] | None = None
            # 分點資料已在迴圈前平行預抓（ETF 與超出上限者不在 branch_frames 內）
            broker_frame = branch_frames.get(symbol, pd.DataFrame())
            if not broker_frame.empty:
                left_branch_fetches += 1
                broker_row = analyze_broker_flow(
                    broker_frame,
                    day_trade_blacklist=day_trade_blacklist,
                    top_n=int(thresholds["branch_top_n"]),
                    concentration_pct=float(thresholds["branch_concentration_pct"]),
                    streak_days=int(thresholds["branch_streak_days"]),
                    churn_max_ratio_pct=float(thresholds["branch_churn_max_ratio_pct"]),
                    cost_lookback_days=int(thresholds["branch_lookback_days"]),
                )

            catalyst_row = nearest_catalyst_payload(
                symbol=symbol,
                events=catalyst_events,
                today=datetime.now(TW_TZ).date(),
                lookahead_trading_days=catalyst_lookahead,
            )
            sector_row = sector_payloads.get(symbol, empty_sector_payload())

            cb_id = cb_map.get(symbol)
            cb_daily = fetch_cb_daily_finmind(cb_id) if cb_id else pd.DataFrame()
            micro_row = build_microstructure_row(
                today=micro_today,
                institutional_rows=institutions if institutions is not None and not institutions.empty else None,
                disposition=disposition_fields(symbol, disposition_frame, history, holder_rows, micro_today),
                cb=cb_fields(cb_daily),
                has_convertible_bond=bool(cb_id) if cb_map else None,
                geographic=geographic_fields(broker_frame, trader_city_map, COMPANY_CITY_MAP.get(symbol)),
            )
            left_result = left_engine.score(
                symbol=symbol,
                indicators=indicators,
                chip_rows=chip_rows if not chip_rows.empty else None,
                holder_rows=holder_rows if not holder_rows.empty else None,
                institutional_rows=institutions if institutions is not None and not institutions.empty else None,
                revenue_row=revenue_row,
                financial_row=financial_row,
                catalyst_row=catalyst_row,
                sector_row=sector_row,
                broker_row=broker_row,
                microstructure_row=micro_row,
            )
            left_candidate = serialize_left_candidate(
                symbol=symbol,
                name=name,
                market=market,
                industry=industry_for_symbol(symbol, name, industry_map),
                result=left_result,
                close=safe_float(quote.get("close")),
                revenue_row=revenue_row,
                financial_row=financial_row,
                catalyst_row=catalyst_row,
                sector_row=sector_row,
                broker_row=broker_row,
                chip_metrics=left_side_metrics(chip_rows, holder_rows),
                price_source=price_source,
                quote_date=str(quote.get("quote_date") or ""),
                quote_source=str(quote.get("quote_source") or market),
            )
            left_candidate["microstructure_available"] = micro_row is not None
            prev_entry = prev_left_scores.get(symbol)
            score_delta = (
                round(float(left_candidate["total_score"]) - prev_entry["score"], 1) if prev_entry else None
            )
            left_candidate["score_prev"] = prev_entry["score"] if prev_entry else None
            left_candidate["score_prev_date"] = prev_entry["date"] if prev_entry else None
            left_candidate["score_delta"] = score_delta
            if (
                score_delta is not None
                and score_delta >= accel_min_delta
                and float(left_candidate["total_score"]) >= accel_min_score
            ):
                left_candidate["reasons"] = ["signal_acceleration", *left_candidate.get("reasons", [])]
            if is_observation_pool:
                left_candidate["reasons"] = ["observation_pool", *left_candidate.get("reasons", [])]
                left_candidate["is_observation_pool"] = True
            left_scored.append(left_candidate)
            if left_result.is_candidate:
                left_candidates.append(left_candidate)
        except Exception as exc:
            print(f"WARN left-side scoring failed for {symbol}: {exc}")

    left_phase("left-stage2-loop")
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
    payload["left_side_branch_analyzed_count"] = left_branch_fetches
    payload["left_side_candidates"] = left_candidates[:MAX_OUTPUT]
    # 完整評分名單的分數表（含未進榜低分股）：訊號加速需要隔日比對的基準
    payload["left_side_scores"] = {
        str(item["symbol"]): float(item["total_score"]) for item in left_scored
    }
    return payload


def _phase_timer():
    """整輪執行的階段計時（找出慢的階段用；log 時間戳因緩衝不可信）。"""
    started = time.monotonic()
    last = [started]

    def mark(label: str) -> None:
        now = time.monotonic()
        print(f"PHASE {label}: +{now - last[0]:.0f}s (total {now - started:.0f}s)")
        last[0] = now

    return mark


def run_live_screener() -> dict[str, Any]:
    phase = _phase_timer()
    now_tw = datetime.now(TW_TZ)
    all_quotes = fetch_all_quotes()
    phase("all-quotes")
    industry_map = fetch_industry_map()
    symbol_industries = {
        str(row["symbol"]): industry_for_symbol(str(row["symbol"]), str(row.get("name", "")), industry_map)
        for _, row in all_quotes.iterrows()
    }
    # ETF/ETN 不是產業：其整體成交值恆常巨大，若當成板塊會讓所有 ETF
    # 永久拿到「板塊資金領先」加分，違背產業資金共振的本意
    symbol_industries = {
        symbol: industry
        for symbol, industry in symbol_industries.items()
        if industry and industry != "ETF/ETN"
    }
    sector_payloads, sector_snapshot = compute_sector_resonance(
        all_quotes,
        symbol_industries,
        previous_entries=load_sector_history(SECTOR_HISTORY_PATH),
        rank_threshold_pct=float(get_settings().raw["left_side"]["thresholds"]["sector_rank_threshold_pct"]),
        jump_threshold_pct=float(get_settings().raw["left_side"]["thresholds"]["sector_turnover_jump_pct"]),
    )
    if sector_snapshot:
        update_sector_history(SECTOR_HISTORY_PATH, now_tw.date(), sector_snapshot)
    manual_events = load_catalyst_events(CATALYST_EVENTS_PATH)
    conference_events = fetch_investor_conferences(now_tw.date())
    seen_events = {(e.symbol, e.event_date) for e in manual_events}
    catalyst_events = manual_events + [
        e for e in conference_events if (e.symbol, e.event_date) not in seen_events
    ]
    print(f"Catalyst events: manual {len(manual_events)} + conference {len(conference_events)} → {len(catalyst_events)}")
    phase("industry+sector+catalyst")
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
            industry=industry_for_symbol(symbol, name, industry_map),
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
    momentum_candidate_symbols = {str(item["symbol"]) for item in candidates}
    candidates = candidates[:MAX_OUTPUT]
    phase("momentum-loop")

    if LEFT_SIDE_ENABLED:
        try:
            left_payload = run_left_side_screener(
                all_quotes,
                fundamentals_cache,
                industry_map,
                catalyst_events,
                sector_payloads,
                exclude_symbols=momentum_candidate_symbols,
            )
        except Exception as exc:
            print(f"WARN left-side screener failed: {exc}")
            left_payload = {
                "left_side_enabled": True,
                "left_side_candidates": [],
                "left_side_note": "左側潛伏本次執行失敗，畫面沿用上一版資料。",
            }
    else:
        left_payload = {"left_side_enabled": False, "left_side_candidates": []}
    phase("left-side")

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
        "catalyst_event_count": len(catalyst_events),
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
                "quote_date": c.get("quote_date"),
                "quote_source": c.get("quote_source"),
                "close_price": c.get("close_price"),
                "entry_price": c.get("entry_price"),
                "stop_loss_price": c.get("stop_loss_price"),
                "target_price_1": c.get("target_price_1"),
                "risk_reward_ratio": c.get("risk_reward_ratio"),
                "reasons": c.get("reasons", []),
                "trend_score": c.get("trend_score"),
                "volume_score": c.get("volume_score"),
                "institutional_score": c.get("institutional_score"),
                "chip_score": c.get("chip_score"),
                "fundamental_score": c.get("fundamental_score"),
                "industry_score": c.get("industry_score"),
                "risk_reward_score": c.get("risk_reward_score"),
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
                "quote_date": c.get("quote_date"),
                "quote_source": c.get("quote_source"),
                "close_price": c.get("close_price"),
                "reasons": c.get("reasons", []),
                "score_prev": c.get("score_prev"),
                "score_delta": c.get("score_delta"),
                "entry_price": c.get("entry_price"),
                "stop_loss_price": c.get("stop_loss_price"),
                "target_price_1": c.get("target_price_1"),
                "risk_reward_ratio": c.get("risk_reward_ratio"),
                "base_structure_score": c.get("base_structure_score"),
                "short_covering_score": c.get("short_covering_score"),
                "retail_capitulation_score": c.get("retail_capitulation_score"),
                "smart_money_score": c.get("smart_money_score"),
                "fundamental_safety_score": c.get("fundamental_safety_score"),
                "ignition_score": c.get("ignition_score"),
                "bb_bandwidth_pctile": c.get("bb_bandwidth_pctile"),
                "short_balance_change_pct": c.get("short_balance_change_pct"),
                "margin_balance_change_pct": c.get("margin_balance_change_pct"),
                "day_trade_ratio_pct": c.get("day_trade_ratio_pct"),
                "big_holder_gain_pp": c.get("big_holder_gain_pp"),
                "nearest_catalyst_type": c.get("nearest_catalyst_type"),
                "nearest_catalyst_date": c.get("nearest_catalyst_date"),
                "catalyst_days_left": c.get("catalyst_days_left"),
                "catalyst_score": c.get("catalyst_score"),
                "sector_turnover_rank_pct": c.get("sector_turnover_rank_pct"),
                "sector_turnover_share_pct": c.get("sector_turnover_share_pct"),
                "sector_turnover_jump_pct": c.get("sector_turnover_jump_pct"),
                "sector_resonance_score": c.get("sector_resonance_score"),
                "microstructure_score": c.get("microstructure_score"),
                "window_dressing_score": c.get("window_dressing_score"),
                "jailbreak_score": c.get("jailbreak_score"),
                "cb_signal_score": c.get("cb_signal_score"),
                "geographic_broker_score": c.get("geographic_broker_score"),
            }
            for c in output.get("left_side_candidates", [])
        ],
        "left_side_scores": output.get("left_side_scores", {}),
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

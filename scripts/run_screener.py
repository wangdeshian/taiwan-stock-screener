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
    sample_daily_prices,
    sample_financials,
    sample_institutional_trades,
    sample_monthly_revenue,
    sample_stocks,
)
from taiwan_stock_screener.indicators.technical import add_technical_indicators  # noqa: E402
from taiwan_stock_screener.scoring.engine import ScoringEngine  # noqa: E402

TW_TZ = timezone(timedelta(hours=8))
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").replace("\r", "").replace("\n", "").strip()
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
FUGLE_API_KEY = os.environ.get("FUGLE_API_KEY", "").replace("\r", "").replace("\n", "").strip()
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
TOP_N = int(os.environ.get("SCREENER_TOP_N", "60"))
MAX_OUTPUT = int(os.environ.get("SCREENER_MAX_OUTPUT", "20"))
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


def fetch_twse_today() -> pd.DataFrame:
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
                "close": close,
                "volume": volume,
                "turnover": turnover or close * volume,
                "pe_ratio": pe_ratio or None,
            }
        )
    return pd.DataFrame(data)


def fetch_tpex_today() -> pd.DataFrame:
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
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
                "close": close,
                "volume": volume,
                "turnover": turnover,
                "pe_ratio": pe_ratio or None,
            }
        )
    return pd.DataFrame(data)


def fetch_today_universe() -> pd.DataFrame:
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

    today = pd.concat(frames, ignore_index=True)
    today = today[(today["close"] > 10) & (today["turnover"] > 100_000_000)]
    today = today.sort_values("turnover", ascending=False).head(TOP_N)
    return today.reset_index(drop=True)


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

    # ROE — lives in TaiwanStockProfitability, not TaiwanStockFinancialStatements
    try:
        time.sleep(0.1)
        params_prof = {
            "dataset": "TaiwanStockProfitability",
            "data_id": symbol,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        payload_prof = finmind_get(params_prof, timeout=20)
        data_prof = payload_prof.get("data", [])
        if data_prof:
            frame_prof = pd.DataFrame(data_prof)
            frame_prof = frame_prof.sort_values("date")
            for col in ("ROE", "roe", "roe_a", "ReturnOnEquity"):
                if col in frame_prof.columns:
                    val = pd.to_numeric(frame_prof.iloc[-1][col], errors="coerce")
                    if pd.notna(val):
                        result["roe_pct"] = float(val)
                        break
    except Exception as exc:
        print(f"WARN FinMind profitability failed for {symbol}: {exc}")

    return result if result else None


def demo_output() -> dict[str, Any]:
    now_tw = datetime.now(TW_TZ)
    prices = sample_daily_prices()
    institutions = sample_institutional_trades()
    revenues = sample_monthly_revenue()
    financials = sample_financials()
    stock_lookup = sample_stocks().set_index("symbol").to_dict("index")
    engine = ScoringEngine()
    candidates: list[dict[str, Any]] = []

    for symbol, price_rows in prices.groupby("symbol"):
        indicators = add_technical_indicators(price_rows)
        result = engine.score(
            symbol=symbol,
            indicators=indicators,
            institutional_rows=institutions[institutions["symbol"] == symbol],
            revenue_row=revenues[revenues["symbol"] == symbol].iloc[-1],
            financial_row=financials[financials["symbol"] == symbol].iloc[-1],
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

    candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
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


def run_live_screener() -> dict[str, Any]:
    now_tw = datetime.now(TW_TZ)
    today = fetch_today_universe()
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

    for index, row in today.iterrows():
        symbol = str(row["symbol"])
        name = str(row["name"])
        market = str(row["market"])
        price_source = "none"
        print(f"[{index + 1:03d}/{len(today):03d}] {symbol} {name}")

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
        )
        scored_candidates.append(candidate)

        if result.total_score >= threshold:
            candidates.append(candidate)

    scored_candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    candidates = candidates[:MAX_OUTPUT]
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

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
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
TOP_N = int(os.environ.get("SCREENER_TOP_N", "60"))
MAX_OUTPUT = int(os.environ.get("SCREENER_MAX_OUTPUT", "20"))
OUTPUT = ROOT / "frontend" / "data" / "results.json"
HISTORY = ROOT / "frontend" / "data" / "history.json"
MAX_HISTORY_DAYS = 30


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


def fetch_history_finmind(symbol: str) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=370)
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN

    try:
        response = requests.get(FINMIND_BASE, params=params, timeout=30)
        response.raise_for_status()
        data = response.json().get("data", [])
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
        "token": FINMIND_TOKEN,
    }
    try:
        response = requests.get(FINMIND_BASE, params=params, timeout=20)
        response.raise_for_status()
        data = response.json().get("data", [])
    except Exception as exc:
        print(f"WARN FinMind institutional failed for {symbol}: {exc}")
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()

    frame = pd.DataFrame(data)
    frame["trade_date"] = pd.to_datetime(frame["date"])
    frame["buy_sell"] = pd.to_numeric(frame["buy_sell"], errors="coerce").fillna(0)

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
        "score_threshold": 85,
        "source": "demo",
        "top_candidates": candidates[:MAX_OUTPUT],
    }


def serialize_candidate(
    symbol: str,
    name: str,
    market: str,
    industry: str | None,
    result: Any,
    plan: Any,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "industry": industry,
        "total_score": result.total_score,
        "trend_score": result.trend_score,
        "volume_score": result.volume_score,
        "institutional_score": result.institutional_score,
        "chip_score": result.chip_score,
        "fundamental_score": result.fundamental_score,
        "industry_score": result.industry_score,
        "risk_reward_score": result.risk_reward_score,
        "reasons": result.reasons,
        "entry_price": plan.entry_price if plan else None,
        "alternate_entry_price": plan.alternate_entry_price if plan else None,
        "stop_loss_price": plan.stop_loss_price if plan else None,
        "target_price_1": plan.target_price_1 if plan else None,
        "target_price_2": plan.target_price_2 if plan else None,
        "risk_reward_ratio": plan.risk_reward_ratio if plan else None,
        "suggested_position_pct": plan.suggested_position_pct if plan else None,
    }


def run_live_screener() -> dict[str, Any]:
    now_tw = datetime.now(TW_TZ)
    today = fetch_today_universe()
    if today.empty:
        print("WARN no live universe found; using demo output")
        return demo_output()

    engine = ScoringEngine()
    threshold = 55 if FINMIND_TOKEN else 42
    candidates: list[dict[str, Any]] = []

    for index, row in today.iterrows():
        symbol = str(row["symbol"])
        name = str(row["name"])
        market = str(row["market"])
        print(f"[{index + 1:03d}/{len(today):03d}] {symbol} {name}")

        if FINMIND_TOKEN:
            history = fetch_history_finmind(symbol)
            time.sleep(0.2)
        else:
            history = fetch_history_yfinance(symbol, market)

        if history.empty or len(history) < 60:
            continue

        try:
            indicators = add_technical_indicators(history)
            institutions = fetch_institutional_finmind(symbol)
            result = engine.score(
                symbol=symbol,
                indicators=indicators,
                institutional_rows=institutions if not institutions.empty else None,
                industry_rank_pct=0.5,
            )
        except Exception as exc:
            print(f"WARN scoring failed for {symbol}: {exc}")
            continue

        if result.total_score >= threshold:
            candidates.append(
                serialize_candidate(
                    symbol=symbol,
                    name=name,
                    market=market,
                    industry=None,
                    result=result,
                    plan=result.trade_plan,
                )
            )

    candidates.sort(key=lambda item: float(item["total_score"]), reverse=True)
    candidates = candidates[:MAX_OUTPUT]
    if not candidates:
        print("WARN live run produced no candidates; using demo output")
        return demo_output()

    return {
        "updated_at": now_tw.isoformat(),
        "screened_count": len(today),
        "candidate_count": len(candidates),
        "has_institutional_data": bool(FINMIND_TOKEN),
        "score_threshold": threshold,
        "source": "live",
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
    # Load existing history
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
        "source": output["source"],
        "screened_count": output.get("screened_count", 0),
        "candidate_count": output.get("candidate_count", 0),
        "score_threshold": output.get("score_threshold", 0),
        "candidates": [
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
            }
            for c in output.get("top_candidates", [])
        ],
    }

    # Remove any existing entry for today (avoid duplicates on manual re-runs)
    entries = [e for e in entries if e.get("date") != today_date]

    # Prepend today and keep rolling window
    entries.insert(0, today_entry)
    entries = entries[:MAX_HISTORY_DAYS]

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"History updated: {len(entries)} 筆記錄 → {HISTORY}")


def main() -> None:
    if not is_trading_day():
        return
    output = run_live_screener()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")
    update_history(output)
    print(
        json.dumps(
            {
                "updated_at": output["updated_at"],
                "source": output["source"],
                "screened_count": output["screened_count"],
                "candidate_count": output["candidate_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def sample_stocks() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "2330", "name": "TSMC", "market": "TWSE", "industry": "Semiconductor"},
            {"symbol": "2454", "name": "MediaTek", "market": "TWSE", "industry": "Semiconductor"},
            {"symbol": "2317", "name": "Hon Hai", "market": "TWSE", "industry": "Electronics"},
            {"symbol": "2303", "name": "UMC", "market": "TWSE", "industry": "Semiconductor"},
            {"symbol": "6488", "name": "GlobalWafers", "market": "TPEx", "industry": "Semiconductor"},
        ]
    )


def sample_daily_prices(days: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    records: list[dict[str, object]] = []
    start = date.today() - timedelta(days=days * 2)
    trading_dates = [start + timedelta(days=i) for i in range(days * 2) if (start + timedelta(days=i)).weekday() < 5]
    trading_dates = trading_dates[-days:]
    bases = {"2330": 820.0, "2454": 1180.0, "2317": 190.0, "2303": 54.0, "6488": 620.0}
    for symbol, base in bases.items():
        trend = np.linspace(0, base * 0.18, len(trading_dates))
        noise = rng.normal(0, base * 0.012, len(trading_dates)).cumsum()
        close_series = base + trend + noise
        if symbol in {"2330", "2454"}:
            close_series[-20:] += np.linspace(base * 0.02, base * 0.08, 20)
        for idx, trade_date in enumerate(trading_dates):
            close = max(close_series[idx], 1)
            open_price = close * (1 + rng.normal(0, 0.006))
            high = max(open_price, close) * (1 + abs(rng.normal(0, 0.008)))
            low = min(open_price, close) * (1 - abs(rng.normal(0, 0.008)))
            volume = float(rng.integers(8_000_000, 45_000_000))
            if idx > len(trading_dates) - 10 and symbol in {"2330", "2454", "6488"}:
                volume *= 2.2
            records.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open": round(float(open_price), 2),
                    "high": round(float(high), 2),
                    "low": round(float(low), 2),
                    "close": round(float(close), 2),
                    "volume": volume,
                    "turnover": volume * close,
                }
            )
    return pd.DataFrame(records)


def sample_institutional_trades(days: int = 20) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    latest_dates = sorted(sample_daily_prices(days=days)["trade_date"].unique())
    records: list[dict[str, object]] = []
    for symbol in sample_stocks()["symbol"]:
        for trade_date in latest_dates:
            foreign = float(rng.integers(-2_000_000, 4_000_000))
            trust = float(rng.integers(-600_000, 2_000_000))
            if symbol in {"2330", "2454"}:
                foreign = abs(foreign) + 2_500_000
                trust = abs(trust) + 500_000
            records.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "foreign_buy_sell": foreign,
                    "investment_trust_buy_sell": trust,
                    "dealer_buy_sell": float(rng.integers(-300_000, 800_000)),
                }
            )
    return pd.DataFrame(records)


def sample_chip_data(days: int = 40) -> pd.DataFrame:
    """左側策略示範用籌碼資料：融資餘額、借券賣出餘額、當沖率。

    2303 / 2317 模擬「散戶絕望＋空單回補」情境：融資與借券餘額持續下降、當沖率極低。
    """
    trading_dates = sorted(sample_daily_prices(days=days)["trade_date"].unique())
    profiles = {
        "2330": {"margin": (900_000, 1.05), "short": (450_000, 1.02), "day_trade": 16.0},
        "2454": {"margin": (650_000, 1.02), "short": (300_000, 0.98), "day_trade": 14.0},
        "2317": {"margin": (1_200_000, 0.82), "short": (520_000, 0.70), "day_trade": 5.0},
        "2303": {"margin": (2_000_000, 0.80), "short": (800_000, 0.68), "day_trade": 4.5},
        "6488": {"margin": (400_000, 0.88), "short": (150_000, 0.85), "day_trade": 8.0},
    }
    records: list[dict[str, object]] = []
    steps = max(len(trading_dates) - 1, 1)
    for symbol, profile in profiles.items():
        margin_start, margin_factor = profile["margin"]
        short_start, short_factor = profile["short"]
        for idx, trade_date in enumerate(trading_dates):
            progress = idx / steps
            records.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "margin_balance": round(margin_start * (1 + (margin_factor - 1) * progress)),
                    "short_balance": round(short_start * (1 + (short_factor - 1) * progress)),
                    "day_trade_ratio_pct": profile["day_trade"],
                }
            )
    return pd.DataFrame(records)


def sample_big_holder_ratios(weeks: int = 12) -> pd.DataFrame:
    """左側策略示範用股權分散資料：千張大戶持股比例（週資料）。"""
    end = date.today()
    week_dates = [end - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    profiles = {
        "2330": (78.0, 0.0),
        "2454": (70.0, 0.1),
        "2317": (62.0, 1.4),
        "2303": (55.0, 1.8),
        "6488": (66.0, 0.6),
    }
    records: list[dict[str, object]] = []
    for symbol, (base, total_gain) in profiles.items():
        for idx, week_date in enumerate(week_dates):
            records.append(
                {
                    "symbol": symbol,
                    "date": week_date,
                    "big_holder_ratio_pct": round(base + total_gain * idx / max(weeks - 1, 1), 2),
                }
            )
    return pd.DataFrame(records)


def sample_monthly_revenue() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "2330", "year": date.today().year, "month": max(date.today().month - 1, 1), "revenue": 250_000_000_000, "revenue_yoy_pct": 28.4},
            {"symbol": "2454", "year": date.today().year, "month": max(date.today().month - 1, 1), "revenue": 58_000_000_000, "revenue_yoy_pct": 22.1},
            {"symbol": "2317", "year": date.today().year, "month": max(date.today().month - 1, 1), "revenue": 510_000_000_000, "revenue_yoy_pct": 8.5},
            {"symbol": "2303", "year": date.today().year, "month": max(date.today().month - 1, 1), "revenue": 19_000_000_000, "revenue_yoy_pct": 11.2},
            {"symbol": "6488", "year": date.today().year, "month": max(date.today().month - 1, 1), "revenue": 6_300_000_000, "revenue_yoy_pct": 18.9},
        ]
    )


def sample_financials() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "2330", "year": date.today().year, "quarter": 1, "eps": 9.8, "roe_pct": 29.0, "gross_margin_pct": 54.2, "operating_margin_pct": 43.1, "net_margin_pct": 38.0},
            {"symbol": "2454", "year": date.today().year, "quarter": 1, "eps": 17.2, "roe_pct": 23.4, "gross_margin_pct": 48.0, "operating_margin_pct": 21.4, "net_margin_pct": 19.5},
            {"symbol": "2317", "year": date.today().year, "quarter": 1, "eps": 2.1, "roe_pct": 10.7, "gross_margin_pct": 6.5, "operating_margin_pct": 3.0, "net_margin_pct": 2.4},
            {"symbol": "2303", "year": date.today().year, "quarter": 1, "eps": 0.42, "roe_pct": 5.8, "gross_margin_pct": 24.0, "operating_margin_pct": 8.1, "net_margin_pct": 7.0},
            {"symbol": "6488", "year": date.today().year, "quarter": 1, "eps": 5.6, "roe_pct": 18.1, "gross_margin_pct": 35.4, "operating_margin_pct": 25.1, "net_margin_pct": 21.0},
        ]
    )

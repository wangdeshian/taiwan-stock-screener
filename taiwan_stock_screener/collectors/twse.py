from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from taiwan_stock_screener.collectors.base import MarketDataCollector
from taiwan_stock_screener.collectors.http import HttpClient
from taiwan_stock_screener.config import get_settings


def _to_float(value: Any) -> float:
    if value in (None, "", "--"):
        return 0.0
    return float(str(value).replace(",", ""))


class TwseCollector(MarketDataCollector):
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http = http_client or HttpClient()
        self.url = get_settings().raw["sources"]["twse"]["stock_day_all_url"]

    async def fetch_daily_prices(self, target_date: date | None = None) -> pd.DataFrame:
        payload = await self.http.get_json(self.url)
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        normalized: list[dict[str, Any]] = []
        for row in rows:
            symbol = row.get("Code") or row.get("證券代號")
            if not symbol:
                continue
            close = _to_float(row.get("ClosingPrice") or row.get("收盤價"))
            volume = _to_float(row.get("TradeVolume") or row.get("成交股數"))
            normalized.append(
                {
                    "symbol": str(symbol),
                    "name": row.get("Name") or row.get("證券名稱") or str(symbol),
                    "market": "TWSE",
                    "trade_date": target_date or date.today(),
                    "open": _to_float(row.get("OpeningPrice") or row.get("開盤價") or close),
                    "high": _to_float(row.get("HighestPrice") or row.get("最高價") or close),
                    "low": _to_float(row.get("LowestPrice") or row.get("最低價") or close),
                    "close": close,
                    "volume": volume,
                    "turnover": _to_float(row.get("TradeValue") or row.get("成交金額") or close * volume),
                }
            )
        return pd.DataFrame(normalized)

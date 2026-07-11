from __future__ import annotations

import pandas as pd

from taiwan_stock_screener.collectors.http import HttpClient
from taiwan_stock_screener.config import get_settings


class FredCollector:
    def __init__(self, http_client: HttpClient | None = None) -> None:
        settings = get_settings()
        self.http = http_client or HttpClient()
        self.base_url = settings.raw["sources"]["fred"]["base_url"]
        self.api_key = settings.fred_api_key

    async def fetch_series(self, series_id: str) -> pd.DataFrame:
        params = {"series_id": series_id, "file_type": "json"}
        if self.api_key:
            params["api_key"] = self.api_key
        payload = await self.http.get_json(self.base_url, params=params)
        return pd.DataFrame(payload.get("observations", []))

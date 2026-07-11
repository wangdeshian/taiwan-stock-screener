from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from taiwan_stock_screener.collectors.http import HttpClient
from taiwan_stock_screener.config import get_settings


class FinMindCollector:
    def __init__(self, http_client: HttpClient | None = None) -> None:
        settings = get_settings()
        self.http = http_client or HttpClient()
        self.base_url = settings.raw["sources"]["finmind"]["base_url"]
        self.token = settings.finmind_token

    async def fetch_dataset(
        self,
        dataset: str,
        data_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()
        if self.token:
            params["token"] = self.token
        payload = await self.http.get_json(self.base_url, params=params)
        return pd.DataFrame(payload.get("data", []))

from __future__ import annotations

import io

import pandas as pd

from taiwan_stock_screener.collectors.base import FundamentalCollector
from taiwan_stock_screener.collectors.http import HttpClient
from taiwan_stock_screener.config import get_settings


class MopsCollector(FundamentalCollector):
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http = http_client or HttpClient()
        self.url_template = get_settings().raw["sources"]["mops"]["monthly_revenue_url"]

    async def fetch_monthly_revenue(self, year: int, month: int) -> pd.DataFrame:
        url = self.url_template.format(year=year, month=month)
        html = await self.http.get_text(url)
        tables = pd.read_html(io.StringIO(html))
        if not tables:
            return pd.DataFrame()
        return tables[0]

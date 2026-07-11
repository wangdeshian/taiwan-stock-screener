from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class MarketDataCollector(ABC):
    @abstractmethod
    async def fetch_daily_prices(self, target_date: date | None = None) -> pd.DataFrame:
        """Return normalized daily prices."""


class FundamentalCollector(ABC):
    @abstractmethod
    async def fetch_monthly_revenue(self, year: int, month: int) -> pd.DataFrame:
        """Return normalized monthly revenue."""

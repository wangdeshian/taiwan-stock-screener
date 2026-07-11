from __future__ import annotations

from pydantic import BaseModel


class StockResponse(BaseModel):
    symbol: str
    name: str
    market: str
    industry: str | None


class WatchlistRequest(BaseModel):
    note: str | None = None

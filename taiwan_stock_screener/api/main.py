from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from taiwan_stock_screener import __version__
from taiwan_stock_screener.api.schemas import WatchlistRequest
from taiwan_stock_screener.config import get_settings
from taiwan_stock_screener.database.repository import StockRepository
from taiwan_stock_screener.database.session import get_db, init_db
from taiwan_stock_screener.logging_config import configure_logging
from taiwan_stock_screener.services.screening_service import ScreeningService
from taiwan_stock_screener.services.update_service import DailyUpdateService

configure_logging()
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title=settings.raw["app"]["name"], version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.raw["api"]["cors_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/stocks")
def list_stocks(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    repo = StockRepository(db)
    return [
        {"symbol": stock.symbol, "name": stock.name, "market": stock.market, "industry": stock.industry}
        for stock in repo.list_stocks()
    ]


@app.get("/stocks/{symbol}")
def get_stock(symbol: str, db: Session = Depends(get_db)) -> dict[str, object]:
    repo = StockRepository(db)
    stock = repo.get_stock(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")
    prices = repo.latest_prices(symbol, limit=30)
    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "industry": stock.industry,
        "prices": [
            {
                "trade_date": item.trade_date.isoformat(),
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
                "turnover": item.turnover,
            }
            for item in prices
        ],
    }


@app.get("/search")
def search(q: str = Query(min_length=1), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    repo = StockRepository(db)
    return [
        {"symbol": stock.symbol, "name": stock.name, "market": stock.market, "industry": stock.industry}
        for stock in repo.search_stocks(q)
    ]


@app.get("/candidates")
def candidates(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    service = ScreeningService(db)
    rows = service.list_candidates(limit=limit)
    if not rows and settings.sample_mode:
        DailyUpdateService(db).run()
        rows = service.list_candidates(limit=limit)
    return rows


@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, object]:
    service = ScreeningService(db)
    summary = service.dashboard_summary()
    if summary["candidate_count"] == 0 and settings.sample_mode:
        DailyUpdateService(db).run()
        summary = service.dashboard_summary()
    return summary


@app.post("/jobs/update")
def run_update(db: Session = Depends(get_db)) -> dict[str, int | str]:
    return DailyUpdateService(db).run()


@app.post("/backtest")
def backtest() -> dict[str, object]:
    return {
        "status": "planned",
        "message": "Backtest engine scaffold is available; production walk-forward logic will be added after data collectors are stable.",
    }


@app.get("/watchlist/{user_id}")
def list_watchlist(user_id: str, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    repo = StockRepository(db)
    return [{"symbol": item.symbol, "note": item.note, "updated_at": item.updated_at.isoformat()} for item in repo.list_watchlist(user_id)]


@app.put("/watchlist/{user_id}/{symbol}")
def put_watchlist_item(
    user_id: str,
    symbol: str,
    payload: WatchlistRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    repo = StockRepository(db)
    item = repo.set_watchlist_item(user_id, symbol, payload.note)
    db.commit()
    return {"symbol": item.symbol, "note": item.note}


@app.delete("/watchlist/{user_id}/{symbol}")
def delete_watchlist_item(user_id: str, symbol: str, db: Session = Depends(get_db)) -> dict[str, str]:
    repo = StockRepository(db)
    repo.delete_watchlist_item(user_id, symbol)
    db.commit()
    return {"status": "deleted"}

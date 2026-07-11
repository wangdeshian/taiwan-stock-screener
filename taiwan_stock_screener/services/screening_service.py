from __future__ import annotations

from sqlalchemy.orm import Session

from taiwan_stock_screener.database.repository import StockRepository


class ScreeningService:
    def __init__(self, db: Session) -> None:
        self.repo = StockRepository(db)

    def list_candidates(self, limit: int = 20) -> list[dict[str, object]]:
        results = self.repo.list_candidates(limit=limit)
        rows: list[dict[str, object]] = []
        for item in results:
            stock = self.repo.get_stock(item.symbol)
            rows.append(
                {
                    "symbol": item.symbol,
                    "name": stock.name if stock else item.symbol,
                    "market": stock.market if stock else None,
                    "industry": stock.industry if stock else None,
                    "score_date": item.score_date.isoformat(),
                    "total_score": item.total_score,
                    "trend_score": item.trend_score,
                    "volume_score": item.volume_score,
                    "institutional_score": item.institutional_score,
                    "chip_score": item.chip_score,
                    "fundamental_score": item.fundamental_score,
                    "industry_score": item.industry_score,
                    "risk_reward_score": item.risk_reward_score,
                    "reasons": [reason for reason in item.reasons.split(",") if reason],
                    "entry_price": item.entry_price,
                    "alternate_entry_price": item.alternate_entry_price,
                    "stop_loss_price": item.stop_loss_price,
                    "target_price_1": item.target_price_1,
                    "target_price_2": item.target_price_2,
                    "risk_reward_ratio": item.risk_reward_ratio,
                    "suggested_position_pct": item.suggested_position_pct,
                }
            )
        return rows

    def dashboard_summary(self) -> dict[str, object]:
        candidates = self.list_candidates(limit=20)
        return {
            "candidate_count": len(candidates),
            "top_candidates": candidates,
            "latest_update": candidates[0]["score_date"] if candidates else None,
        }

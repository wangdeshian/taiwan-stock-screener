from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float


def summarize_candidate_returns(signals: pd.DataFrame, returns: pd.DataFrame) -> BacktestResult:
    if signals.empty or returns.empty:
        return BacktestResult(trades=0, win_rate=0, total_return_pct=0, max_drawdown_pct=0, sharpe_ratio=0)

    merged = signals.merge(returns, on=["symbol", "trade_date"], how="inner")
    if merged.empty:
        return BacktestResult(trades=0, win_rate=0, total_return_pct=0, max_drawdown_pct=0, sharpe_ratio=0)

    realized = merged["forward_return_pct"].astype(float)
    equity = (1 + realized / 100).cumprod()
    drawdown = (equity / equity.cummax() - 1) * 100
    sharpe = realized.mean() / realized.std() * (252**0.5) if realized.std() else 0
    return BacktestResult(
        trades=len(realized),
        win_rate=round(float((realized > 0).mean() * 100), 2),
        total_return_pct=round(float((equity.iloc[-1] - 1) * 100), 2),
        max_drawdown_pct=round(float(drawdown.min()), 2),
        sharpe_ratio=round(float(sharpe), 2),
    )

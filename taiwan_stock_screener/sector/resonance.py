from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd


def compute_sector_resonance(
    quotes: pd.DataFrame,
    symbol_industries: dict[str, str],
    previous_entries: list[dict[str, Any]] | None = None,
    rank_threshold_pct: float = 20.0,
    jump_threshold_pct: float = 50.0,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """用全市場成交值計算產業資金共振。

    回傳：
    - symbol -> resonance payload，供候選股序列化與評分使用
    - 今日產業快照，供下次計算五日均值
    """
    if quotes.empty:
        return {}, []

    frame = quotes.copy()
    frame["symbol"] = frame["symbol"].astype(str)
    frame["turnover"] = pd.to_numeric(frame.get("turnover"), errors="coerce").fillna(0)
    frame["industry"] = frame["symbol"].map(symbol_industries)
    frame = frame[(frame["industry"].notna()) & (frame["turnover"] > 0)]
    if frame.empty:
        return {}, []

    total_turnover = float(frame["turnover"].sum())
    if total_turnover <= 0:
        return {}, []

    sector_turnover = frame.groupby("industry", as_index=False)["turnover"].sum()
    sector_turnover["sector_turnover_share_pct"] = sector_turnover["turnover"] / total_turnover * 100
    sector_turnover = sector_turnover.sort_values("sector_turnover_share_pct", ascending=False).reset_index(drop=True)
    sector_count = max(len(sector_turnover), 1)
    sector_turnover["sector_turnover_rank_pct"] = (sector_turnover.index + 1) / sector_count * 100

    previous_average = _previous_sector_share_average(previous_entries or [])
    sector_rows: dict[str, dict[str, Any]] = {}
    today_snapshot: list[dict[str, Any]] = []
    for row in sector_turnover.to_dict("records"):
        industry = str(row["industry"])
        share_pct = round(float(row["sector_turnover_share_pct"]), 2)
        rank_pct = round(float(row["sector_turnover_rank_pct"]), 2)
        avg_share = previous_average.get(industry)
        jump_pct = None
        if avg_share and avg_share > 0:
            jump_pct = round((share_pct - avg_share) / avg_share * 100, 2)

        in_top_rank = rank_pct <= rank_threshold_pct
        is_jumping = jump_pct is not None and jump_pct >= jump_threshold_pct
        sector_rows[industry] = {
            "sector_turnover_rank_pct": rank_pct,
            "sector_turnover_share_pct": share_pct,
            "sector_turnover_jump_pct": jump_pct,
            "sector_resonance_available": True,
            "sector_resonance_active": bool(in_top_rank or is_jumping),
        }
        today_snapshot.append(
            {
                "industry": industry,
                "sector_turnover_share_pct": share_pct,
                "sector_turnover_rank_pct": rank_pct,
            }
        )

    payload_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol, industry in symbol_industries.items():
        if industry in sector_rows:
            payload_by_symbol[str(symbol)] = sector_rows[industry]
    return payload_by_symbol, today_snapshot


def load_sector_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def update_sector_history(
    path: Path,
    snapshot_date: date,
    snapshot: list[dict[str, Any]],
    max_days: int = 10,
) -> None:
    entries = load_sector_history(path)
    today = snapshot_date.isoformat()
    entries = [entry for entry in entries if entry.get("date") != today]
    entries.insert(0, {"date": today, "sectors": snapshot})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries[:max_days], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def empty_sector_payload() -> dict[str, Any]:
    return {
        "sector_turnover_rank_pct": None,
        "sector_turnover_share_pct": None,
        "sector_turnover_jump_pct": None,
        "sector_resonance_available": False,
        "sector_resonance_active": False,
    }


def _previous_sector_share_average(entries: list[dict[str, Any]], lookback: int = 5) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for entry in entries[:lookback]:
        for row in entry.get("sectors", []):
            if not isinstance(row, dict):
                continue
            industry = str(row.get("industry") or "")
            share = row.get("sector_turnover_share_pct")
            if not industry or share is None:
                continue
            try:
                values.setdefault(industry, []).append(float(share))
            except (TypeError, ValueError):
                continue
    return {
        industry: sum(series) / len(series)
        for industry, series in values.items()
        if series
    }


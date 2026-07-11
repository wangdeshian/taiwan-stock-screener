from __future__ import annotations

from dataclasses import dataclass

from taiwan_stock_screener.config import get_settings


@dataclass(frozen=True)
class TradePlan:
    entry_price: float
    alternate_entry_price: float
    stop_loss_price: float
    target_price_1: float
    target_price_2: float
    risk_reward_ratio: float
    suggested_position_pct: float


def build_trade_plan(close: float, atr14: float | None, score: float) -> TradePlan:
    config = get_settings().raw["trade_plan"]
    stop_distance = (atr14 or 0) * float(config["atr_stop_multiplier"])
    if stop_distance <= 0:
        stop_distance = close * float(config["fallback_stop_pct"]) / 100

    entry = close
    alternate_entry = close * 0.985
    stop_loss = max(close - stop_distance, 0.01)
    target_1 = close + stop_distance * float(config["first_target_r_multiple"])
    target_2 = close + stop_distance * float(config["second_target_r_multiple"])
    risk = entry - stop_loss
    reward = target_1 - entry
    rr = reward / risk if risk > 0 else 0
    suggested_position = min(float(config["max_position_pct"]), max(5.0, score / 5))
    return TradePlan(
        entry_price=round(entry, 2),
        alternate_entry_price=round(alternate_entry, 2),
        stop_loss_price=round(stop_loss, 2),
        target_price_1=round(target_1, 2),
        target_price_2=round(target_2, 2),
        risk_reward_ratio=round(rr, 2),
        suggested_position_pct=round(suggested_position, 2),
    )

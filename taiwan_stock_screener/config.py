from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    database_url: str
    config_path: Path
    finmind_token: str | None
    fred_api_key: str | None
    log_level: str

    @property
    def sample_mode(self) -> bool:
        return bool(self.raw.get("app", {}).get("sample_mode", True))

    @property
    def candidate_score(self) -> float:
        return float(self.raw["thresholds"]["candidate_score"])


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    config_path = Path(os.getenv("CONFIG_PATH", "config.yaml"))
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    database_url = os.getenv("DATABASE_URL", raw.get("database", {}).get("url", "sqlite:///./data/taiwan_stock_screener.db"))
    raw = _deep_merge(raw, {"database": {"url": database_url}})
    return Settings(
        raw=raw,
        database_url=database_url,
        config_path=config_path,
        finmind_token=os.getenv("FINMIND_TOKEN") or None,
        fred_api_key=os.getenv("FRED_API_KEY") or None,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )

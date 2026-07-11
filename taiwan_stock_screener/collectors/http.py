from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from taiwan_stock_screener.config import get_settings

logger = logging.getLogger(__name__)


class HttpClient:
    def __init__(self) -> None:
        settings = get_settings()
        http_settings = settings.raw["http"]
        self.timeout = float(http_settings["timeout_seconds"])
        self.retries = int(http_settings["retries"])
        self.backoff = float(http_settings["backoff_seconds"])

    async def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        @retry(
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=self.backoff),
            reraise=True,
        )
        async def _request() -> Any:
            logger.info("GET %s", url)
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()

        return await _request()

    async def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        @retry(
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=self.backoff),
            reraise=True,
        )
        async def _request() -> str:
            logger.info("GET %s", url)
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.text

        return await _request()

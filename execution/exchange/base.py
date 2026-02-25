from __future__ import annotations

import asyncio
import time
import random
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import aiohttp


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    symbol: str
    side: str  # BUY/SELL
    status: str
    executed_qty: float
    avg_price: float


class Exchange(Protocol):
    name: str

    async def fetch_price(self, symbol: str) -> float: ...
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]: ...
    async def fetch_usdt_balance(self) -> float: ...
    async def fetch_base_free(self, symbol: str) -> float: ...

    async def market_buy_quote(self, symbol: str, quote_usdt: float) -> OrderResult: ...
    async def market_sell_base(self, symbol: str, base_qty: float) -> OrderResult: ...
    async def limit_sell_base(self, symbol: str, base_qty: float, price: float) -> OrderResult: ...
    async def cancel_all(self, symbol: str) -> None: ...


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: float) -> None:
        self.rate = rate_per_sec
        self.cap = burst
        self.tokens = burst
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last
                self.last = now
                self.tokens = min(self.cap, self.tokens + elapsed * self.rate)
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                need = cost - self.tokens
                await asyncio.sleep(need / max(self.rate, 1e-9))


@dataclass(frozen=True)
class RetryCfg:
    attempts: int = 6
    base_delay: float = 0.35
    max_delay: float = 8.0
    jitter: float = 0.25


class RestClient:
    def __init__(self, limiter: TokenBucket, retry: RetryCfg | None = None) -> None:
        self.limiter = limiter
        self.retry = retry or RetryCfg()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout_s: float = 15.0,
    ) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retry.attempts + 1):
            try:
                await self.limiter.acquire(1.0)
                timeout = aiohttp.ClientTimeout(total=timeout_s)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(method, url, params=params, headers=headers, json=json_body) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status >= 400:
                            raise RuntimeError(f"HTTP {resp.status}: {data}")
                        return data
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                last_exc = e
                if attempt >= self.retry.attempts:
                    break
                delay = min(self.retry.max_delay, self.retry.base_delay * (2 ** (attempt - 1)))
                delay += delay * self.retry.jitter * random.random()
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

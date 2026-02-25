from __future__ import annotations

import time
import hmac
import hashlib
from typing import Any

from execution.exchange.base import Exchange, OrderResult, RestClient, TokenBucket


def _sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BinanceSpot(Exchange):
    name = "binance"

    def __init__(self, base_url: str, api_key: str, api_secret: str, limiter: TokenBucket) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = api_key
        self.secret = api_secret
        self.rest = RestClient(limiter)

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.key}

    def _signed_params(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        # Binance expects querystring signature
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        params["signature"] = _sign(self.secret, qs)
        return params

    async def fetch_price(self, symbol: str) -> float:
        data = await self.rest.request_json("GET", f"{self.base_url}/api/v3/ticker/price", params={"symbol": symbol})
        return float(data["price"])

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/api/v3/klines",
            params={"symbol": symbol, "interval": timeframe, "limit": limit},
        )
        out: list[dict[str, Any]] = []
        for r in data:
            out.append(
                {
                    "open_time": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                    "close_time": int(r[6]),
                }
            )
        return out

    async def fetch_usdt_balance(self) -> float:
        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/api/v3/account",
            params=self._signed_params({}),
            headers=self._headers(),
        )
        for b in data.get("balances", []):
            if b.get("asset") == "USDT":
                return float(b.get("free", 0.0))
        return 0.0

    async def fetch_base_free(self, symbol: str) -> float:
        base = symbol.replace("USDT", "")
        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/api/v3/account",
            params=self._signed_params({}),
            headers=self._headers(),
        )
        for b in data.get("balances", []):
            if b.get("asset") == base:
                return float(b.get("free", 0.0))
        return 0.0

    async def market_buy_quote(self, symbol: str, quote_usdt: float) -> OrderResult:
        params = self._signed_params(
            {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": f"{quote_usdt:.6f}",
            }
        )
        data = await self.rest.request_json(
            "POST",
            f"{self.base_url}/api/v3/order",
            params=params,
            headers=self._headers(),
        )
        executed_qty = float(data.get("executedQty", 0.0))
        fills = data.get("fills") or []
        if fills and executed_qty > 0:
            cost = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            avg = cost / executed_qty
        else:
            cqq = float(data.get("cummulativeQuoteQty", 0.0))
            avg = cqq / max(executed_qty, 1e-12)
        return OrderResult(str(data.get("orderId")), symbol, "BUY", str(data.get("status")), executed_qty, float(avg))

    async def market_sell_base(self, symbol: str, base_qty: float) -> OrderResult:
        params = self._signed_params(
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": f"{base_qty:.8f}",
            }
        )
        data = await self.rest.request_json(
            "POST",
            f"{self.base_url}/api/v3/order",
            params=params,
            headers=self._headers(),
        )
        executed_qty = float(data.get("executedQty", 0.0))
        cqq = float(data.get("cummulativeQuoteQty", 0.0))
        avg = cqq / max(executed_qty, 1e-12)
        return OrderResult(str(data.get("orderId")), symbol, "SELL", str(data.get("status")), executed_qty, float(avg))

    async def limit_sell_base(self, symbol: str, base_qty: float, price: float) -> OrderResult:
        params = self._signed_params(
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": f"{base_qty:.8f}",
                "price": f"{price:.2f}",
            }
        )
        data = await self.rest.request_json(
            "POST",
            f"{self.base_url}/api/v3/order",
            params=params,
            headers=self._headers(),
        )
        return OrderResult(str(data.get("orderId")), symbol, "SELL", str(data.get("status")), float(data.get("executedQty", 0.0)), float(data.get("price", price)))

    async def cancel_all(self, symbol: str) -> None:
        params = self._signed_params({"symbol": symbol})
        await self.rest.request_json(
            "DELETE",
            f"{self.base_url}/api/v3/openOrders",
            params=params,
            headers=self._headers(),
        )

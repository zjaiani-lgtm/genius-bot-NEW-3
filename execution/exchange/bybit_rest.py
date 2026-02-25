from __future__ import annotations

import time
import hmac
import hashlib
import json
from typing import Any

from execution.exchange.base import Exchange, OrderResult, RestClient, TokenBucket


def _hmac_sha256(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class BybitSpot(Exchange):
    name = "bybit"

    def __init__(self, base_url: str, api_key: str, api_secret: str, limiter: TokenBucket) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = api_key
        self.secret = api_secret
        self.rest = RestClient(limiter)

    def _sign_headers(self, method: str, query: str, body_str: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        payload = ts + self.key + recv + (query if method == "GET" else body_str)
        sign = _hmac_sha256(self.secret, payload)
        return {
            "X-BAPI-API-KEY": self.key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }

    async def fetch_price(self, symbol: str) -> float:
        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/v5/market/tickers",
            params={"category": "spot", "symbol": symbol},
        )
        return float(data["result"]["list"][0]["lastPrice"])

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        if timeframe.endswith("m"):
            interval = timeframe[:-1]
        elif timeframe.endswith("h"):
            interval = str(int(timeframe[:-1]) * 60)
        else:
            raise ValueError("Unsupported timeframe for Bybit in this bot")

        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/v5/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        rows = list(reversed(data["result"]["list"]))  # oldest first
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "open_time": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                    "close_time": int(r[0]) + 1,
                }
            )
        return out

    async def fetch_usdt_balance(self) -> float:
        query = "accountType=UNIFIED"
        headers = self._sign_headers("GET", query, "")
        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            headers=headers,
        )
        for acc in data["result"].get("list", []):
            for c in acc.get("coin", []):
                if c.get("coin") == "USDT":
                    return float(c.get("availableToWithdraw", 0.0))
        return 0.0

    async def fetch_base_free(self, symbol: str) -> float:
        base = symbol.replace("USDT", "")
        query = "accountType=UNIFIED"
        headers = self._sign_headers("GET", query, "")
        data = await self.rest.request_json(
            "GET",
            f"{self.base_url}/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            headers=headers,
        )
        for acc in data["result"].get("list", []):
            for c in acc.get("coin", []):
                if c.get("coin") == base:
                    return float(c.get("availableToWithdraw", 0.0))
        return 0.0

    async def market_buy_quote(self, symbol: str, quote_usdt: float) -> OrderResult:
        # Bybit spot market expects qty in base; approximate using lastPrice
        price = await self.fetch_price(symbol)
        base_qty = quote_usdt / max(price, 1e-12)
        body = {"category": "spot", "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": f"{base_qty:.8f}"}
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._sign_headers("POST", "", body_str)
        data = await self.rest.request_json("POST", f"{self.base_url}/v5/order/create", headers=headers, json_body=body)
        order_id = str(data["result"]["orderId"])
        return OrderResult(order_id, symbol, "BUY", "NEW", float(base_qty), float(price))

    async def market_sell_base(self, symbol: str, base_qty: float) -> OrderResult:
        price = await self.fetch_price(symbol)
        body = {"category": "spot", "symbol": symbol, "side": "Sell", "orderType": "Market", "qty": f"{base_qty:.8f}"}
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._sign_headers("POST", "", body_str)
        data = await self.rest.request_json("POST", f"{self.base_url}/v5/order/create", headers=headers, json_body=body)
        order_id = str(data["result"]["orderId"])
        return OrderResult(order_id, symbol, "SELL", "NEW", float(base_qty), float(price))

    async def limit_sell_base(self, symbol: str, base_qty: float, price: float) -> OrderResult:
        body = {
            "category": "spot",
            "symbol": symbol,
            "side": "Sell",
            "orderType": "Limit",
            "qty": f"{base_qty:.8f}",
            "price": f"{price:.6f}",
            "timeInForce": "GTC",
        }
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._sign_headers("POST", "", body_str)
        data = await self.rest.request_json("POST", f"{self.base_url}/v5/order/create", headers=headers, json_body=body)
        order_id = str(data["result"]["orderId"])
        return OrderResult(order_id, symbol, "SELL", "NEW", 0.0, float(price))

    async def cancel_all(self, symbol: str) -> None:
        body = {"category": "spot", "symbol": symbol}
        body_str = json.dumps(body, separators=(",", ":"))
        headers = self._sign_headers("POST", "", body_str)
        await self.rest.request_json("POST", f"{self.base_url}/v5/order/cancel-all", headers=headers, json_body=body)

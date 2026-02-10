import os
import logging
from typing import Any, Dict, Optional

import ccxt

logger = logging.getLogger("gbm")


class ExchangeClientError(Exception):
    pass


class LiveTradingBlocked(Exception):
    pass


class BinanceSpotClient:
    TESTNET_REST_BASE = "https://testnet.binance.vision/api"

    def __init__(self):
        self.mode = os.getenv("MODE", "DEMO").upper()  # DEMO | TESTNET | LIVE
        self.kill_switch = os.getenv("KILL_SWITCH", "false").lower() == "true"
        self.live_confirmation = os.getenv("LIVE_CONFIRMATION", "false").lower() == "true"

        self.max_quote_per_trade = float(os.getenv("MAX_QUOTE_PER_TRADE", "10"))
        self.symbol_whitelist = set(
            s.strip().upper()
            for s in os.getenv("SYMBOL_WHITELIST", "BTC/USDT").split(",")
            if s.strip()
        )

        api_key = os.getenv("BINANCE_API_KEY", "").strip()
        api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

        if self.mode in ("LIVE", "TESTNET"):
            if not api_key or not api_secret:
                raise ExchangeClientError("Missing BINANCE_API_KEY / BINANCE_API_SECRET for LIVE/TESTNET.")

        self.exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })

        if self.mode == "TESTNET":
            self.exchange.urls["api"] = {
                "public": self.TESTNET_REST_BASE,
                "private": self.TESTNET_REST_BASE,
            }
            self.exchange.options["fetchCurrencies"] = False

        try:
            self.exchange.load_markets()
        except Exception as e:
            logger.warning(f"LOAD_MARKETS_WARN | err={e}")

    def _guard(self, symbol: str, quote_amount: Optional[float] = None) -> None:
        if self.kill_switch:
            raise LiveTradingBlocked("KILL_SWITCH is ON.")
        if self.mode == "LIVE" and not self.live_confirmation:
            raise LiveTradingBlocked("LIVE_CONFIRMATION is OFF.")
        if self.mode == "DEMO":
            raise LiveTradingBlocked("MODE=DEMO -> exchange client must not execute real orders.")
        if symbol and symbol.upper() not in self.symbol_whitelist:
            raise LiveTradingBlocked(f"Symbol not allowed by whitelist: {symbol}.")
        if quote_amount is not None and quote_amount > self.max_quote_per_trade:
            raise LiveTradingBlocked(f"quote_amount {quote_amount} exceeds MAX_QUOTE_PER_TRADE={self.max_quote_per_trade}")

    def diagnostics(self) -> Dict[str, Any]:
        try:
            bal = self.exchange.fetch_balance()
            sym = next(iter(self.symbol_whitelist)) if self.symbol_whitelist else "BTC/USDT"
            t = self.exchange.fetch_ticker(sym)
            return {
                "mode": self.mode,
                "kill_switch": self.kill_switch,
                "live_confirmation": self.live_confirmation,
                "symbol_probe": sym,
                "last_price": float(t.get("last") or 0.0),
                "usdt_free": float((bal.get("free", {}) or {}).get("USDT", 0.0) or 0.0),
                "ok": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def fetch_last_price(self, symbol: str) -> float:
        t = self.exchange.fetch_ticker(symbol)
        return float(t["last"])

    def get_min_notional(self, symbol: str) -> float:
        try:
            m = self.exchange.market(symbol)

            cost_min = (((m.get("limits") or {}).get("cost") or {}).get("min"))
            if cost_min is not None:
                return float(cost_min)

            info = m.get("info") or {}
            filters = info.get("filters") or []
            for f in filters:
                t = str(f.get("filterType") or "").upper()
                if t in ("MIN_NOTIONAL", "NOTIONAL"):
                    v = f.get("minNotional")
                    if v is None:
                        v = f.get("minNotionalValue")
                    if v is None:
                        v = f.get("notional")
                    if v is not None:
                        return float(v)
        except Exception as e:
            logger.warning(f"MIN_NOTIONAL_LOOKUP_FAIL | symbol={symbol} err={e}")

        return 0.0

    def fetch_balance_free(self, asset: str) -> float:
        bal = self.exchange.fetch_balance()
        return float((bal.get("free", {}) or {}).get(asset.upper(), 0.0) or 0.0)

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_order(str(order_id), symbol)

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self.exchange.cancel_order(str(order_id), symbol)

    def floor_amount(self, symbol: str, amount: float) -> float:
        try:
            s = self.exchange.amount_to_precision(symbol, amount)
            return float(s)
        except Exception:
            return float(amount)

    def floor_price(self, symbol: str, price: float) -> float:
        try:
            s = self.exchange.price_to_precision(symbol, price)
            return float(s)
        except Exception:
            return float(price)

    def _amount_str(self, symbol: str, amount: float) -> str:
        return str(self.exchange.amount_to_precision(symbol, amount))

    def _price_str(self, symbol: str, price: float) -> str:
        return str(self.exchange.price_to_precision(symbol, price))

    def place_market_buy_by_quote(self, symbol: str, quote_amount: float) -> Dict[str, Any]:
        self._guard(symbol, quote_amount=quote_amount)
        try:
            params = {"quoteOrderQty": float(quote_amount)}
            return self.exchange.create_order(symbol, "market", "buy", None, None, params)
        except Exception as e:
            raise ExchangeClientError(f"Market buy failed: {e}")

    def place_market_sell(self, symbol: str, base_amount: float) -> Dict[str, Any]:
        self._guard(symbol)
        try:
            amt = float(self.exchange.amount_to_precision(symbol, base_amount))
            return self.exchange.create_order(symbol, "market", "sell", float(amt), None)
        except Exception as e:
            raise ExchangeClientError(f"Market sell failed: {e}")

    def place_limit_sell_amount(self, symbol: str, base_amount: float, price: float) -> Dict[str, Any]:
        self._guard(symbol)
        try:
            amt = float(self.exchange.amount_to_precision(symbol, base_amount))
            px = float(self.exchange.price_to_precision(symbol, price))
            return self.exchange.create_order(symbol, "limit", "sell", float(amt), float(px))
        except Exception as e:
            raise ExchangeClientError(f"Limit sell failed: {e}")

    def place_stop_loss_limit_sell(self, symbol: str, base_amount: float, stop_price: float, limit_price: float) -> Dict[str, Any]:
        self._guard(symbol)
        try:
            amt = float(self.exchange.amount_to_precision(symbol, base_amount))
            stop_px = float(self.exchange.price_to_precision(symbol, stop_price))
            limit_px = float(self.exchange.price_to_precision(symbol, limit_price))
            params = {"stopPrice": stop_px, "timeInForce": "GTC"}
            return self.exchange.create_order(symbol, "STOP_LOSS_LIMIT", "sell", float(amt), float(limit_px), params)
        except Exception as e:
            raise ExchangeClientError(f"Stop-loss-limit sell failed: {e}")

    def place_oco_sell(self, symbol: str, base_amount: float, tp_price: float, sl_stop_price: float, sl_limit_price: float) -> Dict[str, Any]:
        self._guard(symbol)
        try:
            qty = self._amount_str(symbol, base_amount)
            price = self._price_str(symbol, tp_price)
            stop_price = self._price_str(symbol, sl_stop_price)
            stop_limit_price = self._price_str(symbol, sl_limit_price)

            payload = {
                "symbol": self.exchange.market_id(symbol),
                "side": "SELL",
                "quantity": qty,
                "price": price,
                "stopPrice": stop_price,
                "stopLimitPrice": stop_limit_price,
                "stopLimitTimeInForce": "GTC",
            }

            res = self.exchange.privatePostOrderOco(payload)
            return {"raw": res}
        except Exception as e:
            raise ExchangeClientError(f"OCO sell failed: {e}")

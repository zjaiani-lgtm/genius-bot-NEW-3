from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from execution.exchange.base import Exchange, OrderResult

log = logging.getLogger("smart_router")


@dataclass
class ExecResult:
    entry: Optional[OrderResult] = None
    partial_tp_order: Optional[OrderResult] = None
    exit: Optional[OrderResult] = None


class SmartRouter:
    async def open_long(self, ex: Exchange, symbol: str, quote_usdt: float) -> OrderResult:
        log.info("open_long_request", extra={"exchange": ex.name, "symbol": symbol, "quote_usdt": quote_usdt})
        res = await ex.market_buy_quote(symbol, quote_usdt)
        log.info("open_long_done", extra={"exchange": ex.name, "symbol": symbol, "qty": res.executed_qty, "avg": res.avg_price, "status": res.status})
        return res

    async def place_partial_tp_limit(self, ex: Exchange, symbol: str, qty: float, tp_price: float) -> Optional[OrderResult]:
        # best-effort: if fails, engine will fallback to software TP
        try:
            o = await ex.limit_sell_base(symbol, qty, tp_price)
            log.info("partial_tp_limit_placed", extra={"exchange": ex.name, "symbol": symbol, "qty": qty, "tp": tp_price, "order_id": o.order_id})
            return o
        except Exception as e:
            log.warning("partial_tp_limit_failed", extra={"exchange": ex.name, "symbol": symbol, "err": str(e)})
            return None

    async def close_long_market(self, ex: Exchange, symbol: str, qty: float) -> OrderResult:
        log.info("close_long_request", extra={"exchange": ex.name, "symbol": symbol, "qty": qty})
        res = await ex.market_sell_base(symbol, qty)
        log.info("close_long_done", extra={"exchange": ex.name, "symbol": symbol, "qty": res.executed_qty, "avg": res.avg_price, "status": res.status})
        return res

    async def cancel_all(self, ex: Exchange, symbol: str) -> None:
        try:
            await ex.cancel_all(symbol)
            log.info("cancel_all_ok", extra={"exchange": ex.name, "symbol": symbol})
        except Exception as e:
            log.warning("cancel_all_failed", extra={"exchange": ex.name, "symbol": symbol, "err": str(e)})

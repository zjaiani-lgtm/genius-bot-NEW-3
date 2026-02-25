import logging

logger = logging.getLogger("smart_router")

class SmartRouter:
    def best_bid_price(self, orderbook: dict) -> float:
        return float(orderbook["b"][0][0])

    def execute_long(self, exchange, symbol: str, qty: float, orderbook: dict):
        price = self.best_bid_price(orderbook)
        logger.info(f"Smart BUY {symbol} @ {price}")
        return exchange.market_buy(symbol, qty)

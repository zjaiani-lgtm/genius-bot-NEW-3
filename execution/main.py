import asyncio
from .config import SYMBOLS, ML_THRESHOLD, ORDERBOOK_IMBALANCE
from .ml.signal_model import MLSignalFilter
from .strategy.orderbook_alpha import orderbook_signal
from .execution.smart_router import SmartRouter
from .exchange.binance_ws import BinanceWS
from .portfolio import Portfolio
from .risk.manager import position_size
from .database import init_db

class DummyExchange:
    def market_buy(self, symbol, qty):
        print(f"EXECUTED BUY {symbol} {qty}")

    def get_balance(self):
        return 1000.0

portfolio = Portfolio()
ml_filter = MLSignalFilter()
router = SmartRouter()
exchange = DummyExchange()

async def handle_orderbook(symbol: str, data: dict):
    if portfolio.has_position(symbol):
        return

    long_ok, imbalance = orderbook_signal(data["b"], data["a"], ORDERBOOK_IMBALANCE)
    if not long_ok:
        return

    prob = ml_filter.predict_prob([imbalance, 0.5, 0.5])
    if prob < ML_THRESHOLD:
        return

    balance = exchange.get_balance()
    price = float(data["b"][0][0])
    qty = position_size(balance, price)

    router.execute_long(exchange, symbol, qty, data)
    portfolio.open(symbol, qty)

async def main():
    init_db()
    tasks = []
    for sym in SYMBOLS:
        ws = BinanceWS(sym)
        tasks.append(ws.stream_orderbook(lambda data, s=sym: handle_orderbook(s, data)))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())

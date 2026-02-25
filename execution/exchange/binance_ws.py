import json
import websockets

class BinanceWS:
    def __init__(self, symbol: str):
        self.symbol = symbol.lower()
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol}@depth20@100ms"

    async def stream_orderbook(self, callback):
        async with websockets.connect(self.url) as ws:
            async for msg in ws:
                data = json.loads(msg)
                await callback(data)

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator

import websockets


@dataclass(frozen=True)
class KlineMsg:
    symbol: str
    timeframe: str
    is_closed: bool
    o: float
    h: float
    l: float
    c: float
    v: float
    start_ms: int
    end_ms: int


class BybitWS:
    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def stream_klines(self, symbols: list[str], timeframe: str) -> AsyncIterator[KlineMsg]:
        # topic: kline.{interval}.{symbol} where interval in minutes (15/30/60...)
        if timeframe.endswith("m"):
            interval = timeframe[:-1]
        elif timeframe.endswith("h"):
            interval = str(int(timeframe[:-1]) * 60)
        else:
            raise ValueError("Unsupported timeframe")

        sub = {"op": "subscribe", "args": [f"kline.{interval}.{s}" for s in symbols]}

        backoff = 0.5
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps(sub))
                    backoff = 0.5
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        data = json.loads(raw)
                        topic = data.get("topic")
                        if not topic or not str(topic).startswith("kline."):
                            continue
                        items = data.get("data")
                        if not items or not isinstance(items, list):
                            continue
                        item = items[-1]
                        parts = str(topic).split(".")
                        sym = parts[2]
                        # confirm flag means candle closed
                        yield KlineMsg(
                            symbol=sym,
                            timeframe=timeframe,
                            is_closed=bool(item.get("confirm", False)),
                            o=float(item.get("open")),
                            h=float(item.get("high")),
                            l=float(item.get("low")),
                            c=float(item.get("close")),
                            v=float(item.get("volume")),
                            start_ms=int(item.get("start")),
                            end_ms=int(item.get("end")),
                        )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 2)

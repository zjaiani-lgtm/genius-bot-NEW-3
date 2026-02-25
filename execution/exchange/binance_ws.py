from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Optional

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


class BinanceWS:
    def __init__(self, ws_base_url: str) -> None:
        self.ws_base_url = ws_base_url.rstrip("/")
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def stream_klines(self, symbols: list[str], timeframe: str) -> AsyncIterator[KlineMsg]:
        params = [f"{s.lower()}@kline_{timeframe}" for s in symbols]
        sub = {"method": "SUBSCRIBE", "params": params, "id": 1}

        backoff = 0.5
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_base_url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps(sub))
                    backoff = 0.5
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        data = json.loads(raw)
                        if data.get("e") != "kline":
                            continue
                        k = data.get("k") or {}
                        yield KlineMsg(
                            symbol=str(data.get("s")),
                            timeframe=str(k.get("i")),
                            is_closed=bool(k.get("x")),
                            o=float(k.get("o")),
                            h=float(k.get("h")),
                            l=float(k.get("l")),
                            c=float(k.get("c")),
                            v=float(k.get("v")),
                            start_ms=int(k.get("t")),
                            end_ms=int(k.get("T")),
                        )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 2)

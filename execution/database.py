from __future__ import annotations

import aiosqlite
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
import json


@dataclass(frozen=True)
class TradeRow:
    id: int
    exchange: str
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: Optional[float]
    entry_time: str
    exit_time: Optional[str]
    pnl_usd: Optional[float]
    fee_usd: float
    meta_json: str


class TradeDB:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    pnl_usd REAL,
                    fee_usd REAL NOT NULL,
                    meta_json TEXT NOT NULL
                );
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_exchange ON trades(exchange);")
            await db.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def insert_entry(
        self,
        exchange: str,
        symbol: str,
        qty: float,
        entry_price: float,
        fee_usd: float,
        meta: dict[str, Any],
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """
                INSERT INTO trades(exchange, symbol, side, qty, entry_price, exit_price, entry_time, exit_time, pnl_usd, fee_usd, meta_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?);
                """,
                (
                    exchange,
                    symbol,
                    "BUY",
                    qty,
                    entry_price,
                    None,
                    self._now(),
                    None,
                    None,
                    fee_usd,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def close_trade(self, trade_id: int, exit_price: float, pnl_usd: float, fee_usd_add: float) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE trades
                SET exit_price=?, exit_time=?, pnl_usd=?, fee_usd=fee_usd+?
                WHERE id=?;
                """,
                (exit_price, self._now(), pnl_usd, fee_usd_add, trade_id),
            )
            await db.commit()

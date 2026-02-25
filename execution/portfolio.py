from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_time: datetime

    atr_at_entry: float
    stop_price: float
    tp_price: float

    best_price: float
    trailing_enabled: bool
    trailing_stop: float

    trade_id: int
    partial_done: bool = False


@dataclass
class Portfolio:
    positions: dict[str, Position] = field(default_factory=dict)
    cooldown_until_idx: dict[str, int] = field(default_factory=dict)

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def get(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def open(self, p: Position, current_idx: int, cooldown_candles: int) -> None:
        self.positions[p.symbol] = p
        self.cooldown_until_idx[p.symbol] = current_idx + cooldown_candles

    def close(self, symbol: str) -> None:
        self.positions.pop(symbol, None)

    def in_cooldown(self, symbol: str, current_idx: int) -> bool:
        until = self.cooldown_until_idx.get(symbol, -1)
        return current_idx < until

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

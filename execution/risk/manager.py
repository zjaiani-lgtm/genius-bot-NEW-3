from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class RiskManager:
    position_pct: float
    stop_atr_mult: float
    tp_atr_mult: float
    taker_fee: float
    maker_fee: float
    slippage_bps: float
    partial_tp_pct: float

    def order_notional_usdt(self, usdt_balance: float) -> float:
        return max(0.0, usdt_balance * self.position_pct)

    def apply_slippage(self, price: float, is_entry: bool) -> float:
        slip = price * (self.slippage_bps / 10000.0)
        return price + slip if is_entry else price - slip

    def stops_from_atr(self, entry: float, atr_val: float) -> Tuple[float, float]:
        stop = entry - atr_val * self.stop_atr_mult
        tp = entry + atr_val * self.tp_atr_mult
        return stop, tp

    def trailing_stop(self, best_price: float, atr_val: float) -> float:
        return best_price - atr_val * self.stop_atr_mult

    def fee_usd(self, notional: float, taker: bool = True) -> float:
        return notional * (self.taker_fee if taker else self.maker_fee)

    def partial_qty(self, full_qty: float) -> float:
        return full_qty * self.partial_tp_pct

# execution/virtual_wallet.py

from datetime import datetime
from execution.config import VIRTUAL_START_BALANCE
from execution.logger import log_info

_balance = None


def _ensure_init():
    global _balance
    if _balance is None:
        _balance = float(VIRTUAL_START_BALANCE)
        log_info(f"Virtual wallet initialized | balance={_balance}")


def get_balance() -> float:
    _ensure_init()
    return float(_balance)


def simulate_market_entry(symbol: str, side: str, size: float, price: float) -> dict:
    _ensure_init()

    if price is None:
        raise ValueError("price is required for demo entry simulation")

    log_info(f"[DEMO] Simulated ENTRY | {symbol} {side} size={size} price={price}")

    return {
        "status": "FILLED",
        "symbol": symbol,
        "side": side,
        "size": float(size),
        "price": float(price),
        "filled_at": datetime.utcnow().isoformat() + "Z",
        "demo": True,
    }


def simulate_market_close(symbol: str, side: str, size: float, close_price: float) -> dict:
    _ensure_init()

    if close_price is None:
        raise ValueError("close_price is required for demo close simulation")

    log_info(f"[DEMO] Simulated CLOSE | {symbol} {side} size={size} close_price={close_price}")

    return {
        "status": "FILLED",
        "symbol": symbol,
        "side": side,
        "size": float(size),
        "price": float(close_price),
        "filled_at": datetime.utcnow().isoformat() + "Z",
        "demo": True,
    }

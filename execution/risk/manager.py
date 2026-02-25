from ..config import RISK_PER_TRADE

def position_size(balance: float, price: float) -> float:
    return round((balance * RISK_PER_TRADE) / price, 6)

def partial_tp_sizes(qty: float, splits):
    return [round(qty * s, 6) for s in splits]

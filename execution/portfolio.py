class Portfolio:
    def __init__(self):
        self.positions = {}

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def open(self, symbol: str, qty: float):
        self.positions[symbol] = qty

    def close(self, symbol: str):
        self.positions.pop(symbol, None)

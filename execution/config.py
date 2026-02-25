import os
from dotenv import load_dotenv

load_dotenv()

SYMBOLS = ["BTCUSDT", "SOLUSDT"]

RISK_PER_TRADE = 0.02
PARTIAL_TP = [0.5, 0.3, 0.2]

ML_THRESHOLD = 0.55
ORDERBOOK_IMBALANCE = 1.2

DB_URL = "sqlite:///trades_v3.db"

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

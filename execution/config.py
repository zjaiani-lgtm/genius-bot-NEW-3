from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    # Exchanges
    EXCHANGE: str = os.getenv("EXCHANGE", "binance").strip().lower()  # binance | bybit
    MODE: str = os.getenv("MODE", "LIVE").strip().upper()  # LIVE only (spot)

    # Symbols / TF
    SYMBOLS: list[str] = tuple(s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,SOLUSDT").split(",") if s.strip())  # type: ignore
    PRIMARY_TF: str = os.getenv("PRIMARY_TF", "15m")
    SECONDARY_TF: str = os.getenv("SECONDARY_TF", "30m")
    CONFIRM_TF: str = os.getenv("CONFIRM_TF", "1h")

    # Strategy defaults
    EMA_FAST: int = int(os.getenv("EMA_FAST", "50"))
    EMA_SLOW: int = int(os.getenv("EMA_SLOW", "200"))
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
    RSI_LONG_MIN: float = float(os.getenv("RSI_LONG_MIN", "55"))
    ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", "14"))

    # Risk
    POSITION_PCT: float = float(os.getenv("POSITION_PCT", "0.03"))  # 3% balance in USDT
    STOP_ATR_MULT: float = float(os.getenv("STOP_ATR_MULT", "1.5"))
    TP_ATR_MULT: float = float(os.getenv("TP_ATR_MULT", "3.0"))
    TRAILING_ENABLED: bool = _get_bool("TRAILING_ENABLED", True)
    COOLDOWN_CANDLES: int = int(os.getenv("COOLDOWN_CANDLES", "3"))
    MAX_POSITIONS_PER_SYMBOL: int = int(os.getenv("MAX_POSITIONS_PER_SYMBOL", "1"))

    # Fees / slippage assumptions (spot)
    TAKER_FEE: float = float(os.getenv("TAKER_FEE", "0.001"))  # 0.1%
    MAKER_FEE: float = float(os.getenv("MAKER_FEE", "0.001"))
    SLIPPAGE_BPS: float = float(os.getenv("SLIPPAGE_BPS", "5"))  # 5 bps = 0.05%

    # Partial TP
    PARTIAL_TP_PCT: float = float(os.getenv("PARTIAL_TP_PCT", "0.5"))  # 50% off at TP

    # ML
    ML_ENABLED: bool = _get_bool("ML_ENABLED", True)
    ML_MIN_PROBA: float = float(os.getenv("ML_MIN_PROBA", "0.55"))

    # DB
    DB_PATH: str = os.getenv("DB_PATH", "./trades_v3.db")

    # Binance endpoints
    BINANCE_BASE_URL: str = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
    BINANCE_WS_URL: str = os.getenv("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws")
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

    # Bybit endpoints (V5 Spot)
    BYBIT_BASE_URL: str = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
    BYBIT_WS_URL: str = os.getenv("BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/spot")
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_API_SECRET: str = os.getenv("BYBIT_API_SECRET", "")

    # Runtime
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    WS_RECONNECT_MAX_DELAY: float = float(os.getenv("WS_RECONNECT_MAX_DELAY", "10"))
    REST_RATE_PER_SEC: float = float(os.getenv("REST_RATE_PER_SEC", "8"))
    REST_BURST: float = float(os.getenv("REST_BURST", "16"))

    # Backtest
    BACKTEST_START_BALANCE: float = float(os.getenv("BACKTEST_START_BALANCE", "10000"))

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # General
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    db_url: str = Field(default="sqlite+aiosqlite:///./trades.db", alias="DB_URL")

    symbols: str = Field(default="BTCUSDT,SOLUSDT", alias="SYMBOLS")

    primary_tf: str = Field(default="15m", alias="PRIMARY_TF")
    secondary_tf: str = Field(default="30m", alias="SECONDARY_TF")
    confirm_tf: str = Field(default="1h", alias="CONFIRM_TF")

    # Strategy
    ema_fast: int = Field(default=50, alias="EMA_FAST")
    ema_slow: int = Field(default=200, alias="EMA_SLOW")
    rsi_period: int = Field(default=14, alias="RSI_PERIOD")
    rsi_long_min: float = Field(default=55.0, alias="RSI_LONG_MIN")
    atr_period: int = Field(default=14, alias="ATR_PERIOD")

    # Risk
    position_pct: float = Field(default=0.03, alias="POSITION_PCT")
    stop_atr_mult: float = Field(default=1.5, alias="STOP_ATR_MULT")
    tp_atr_mult: float = Field(default=3.0, alias="TP_ATR_MULT")
    trailing_enabled: bool = Field(default=True, alias="TRAILING_ENABLED")
    cooldown_candles: int = Field(default=3, alias="COOLDOWN_CANDLES")
    max_positions_per_symbol: int = Field(default=1, alias="MAX_POSITIONS_PER_SYMBOL")

    taker_fee: float = Field(default=0.001, alias="TAKER_FEE")
    maker_fee: float = Field(default=0.001, alias="MAKER_FEE")
    slippage_bps: float = Field(default=5.0, alias="SLIPPAGE_BPS")

    partial_tp_pct: float = Field(default=0.5, alias="PARTIAL_TP_PCT")

    # ML Filter
    ml_enabled: bool = Field(default=True, alias="ML_ENABLED")
    ml_min_proba: float = Field(default=0.55, alias="ML_MIN_PROBA")

    # Binance
    binance_base_url: str = Field(default="https://api.binance.com", alias="BINANCE_BASE_URL")
    binance_ws_url: str = Field(default="wss://stream.binance.com:9443/ws", alias="BINANCE_WS_URL")
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")

    # Bybit
    bybit_base_url: str = Field(default="https://api.bybit.com", alias="BYBIT_BASE_URL")
    bybit_ws_url: str = Field(default="wss://stream.bybit.com/v5/public/spot", alias="BYBIT_WS_URL")
    bybit_api_key: str = Field(default="", alias="BYBIT_API_KEY")
    bybit_api_secret: str = Field(default="", alias="BYBIT_API_SECRET")

    # Runtime
    poll_open_orders_seconds: float = 2.5
    poll_balance_seconds: float = 10.0

    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

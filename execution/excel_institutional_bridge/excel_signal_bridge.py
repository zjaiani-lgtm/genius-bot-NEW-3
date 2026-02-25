import pandas as pd
import time
import logging
from dataclasses import dataclass
from typing import Optional

EXCEL_PATH = "DYZEN_CAPITAL_OS_AI_LIVE_CORE_READY.xlsx"
SHEET_NAME = "PYTHON_BRIDGE"
POLL_INTERVAL = 5

MIN_CONFIDENCE = 0.55
MIN_TREND = 0.5
MIN_VOLUME = 0.4
REQUIRE_STRUCTURE = 1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("excel_bridge")

@dataclass
class ExcelSignal:
    symbol: str
    confidence: float
    volatility_regime: float
    volume_score: float
    trend_strength: float
    structure_ok: int

    def is_valid_long(self) -> bool:
        return (
            self.confidence >= MIN_CONFIDENCE
            and self.trend_strength >= MIN_TREND
            and self.volume_score >= MIN_VOLUME
            and self.structure_ok == REQUIRE_STRUCTURE
        )

class ExcelCommandBridge:
    def __init__(self, path: str):
        self.path = path

    def _load_sheet(self) -> pd.DataFrame:
        return pd.read_excel(self.path, sheet_name=SHEET_NAME)

    def _to_dict(self, df: pd.DataFrame) -> dict:
        return dict(zip(df["field"], df["value"]))

    def read_signal(self) -> Optional[ExcelSignal]:
        try:
            df = self._load_sheet()
            data = self._to_dict(df)

            return ExcelSignal(
                symbol=str(data.get("symbol_name_input", "BTCUSDT")).replace("/", ""),
                confidence=float(data.get("confidence_score_input", 0)),
                volatility_regime=float(data.get("volatility_regime_input", 0)),
                volume_score=float(data.get("volume_score_input", 0)),
                trend_strength=float(data.get("trend_strength_input", 0)),
                structure_ok=int(data.get("structure_ok_input", 0)),
            )
        except Exception as e:
            logger.exception(f"Excel read failed: {e}")
            return None

class SignalEngine:
    def process(self, signal: ExcelSignal) -> None:
        logger.info(
            f"Signal received | {signal.symbol} | "
            f"conf={signal.confidence:.2f} trend={signal.trend_strength:.2f}"
        )

        if signal.is_valid_long():
            self.on_long(signal)
        else:
            logger.info("No trade conditions met.")

    def on_long(self, signal: ExcelSignal) -> None:
        logger.warning(f"LONG SIGNAL TRIGGERED: {signal.symbol}")
        # integrate your exchange execution here

def main():
    logger.info("Excel Bridge started...")
    bridge = ExcelCommandBridge(EXCEL_PATH)
    engine = SignalEngine()

    while True:
        signal = bridge.read_signal()
        if signal:
            engine.process(signal)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()

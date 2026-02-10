import os
import time
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

import ccxt

from execution.signal_client import append_signal
from execution.db.repository import has_active_oco_for_symbol

logger = logging.getLogger("gbm")

TIMEFRAME = os.getenv("BOT_TIMEFRAME", "15m")
CANDLE_LIMIT = int(os.getenv("BOT_CANDLE_LIMIT", "50"))
COOLDOWN_SECONDS = int(os.getenv("BOT_SIGNAL_COOLDOWN_SECONDS", "180"))

ALLOW_LIVE_SIGNALS = os.getenv("ALLOW_LIVE_SIGNALS", "false").lower() == "true"

# USDT per trade (prevents NOTIONAL issues & keeps sizing consistent across symbols)
BOT_QUOTE_PER_TRADE = float(os.getenv("BOT_QUOTE_PER_TRADE", "15"))

CONFIDENCE = float(os.getenv("BOT_SIGNAL_CONFIDENCE", "0.55"))
BLOCK_SIGNALS_WHEN_ACTIVE_OCO = os.getenv("BLOCK_SIGNALS_WHEN_ACTIVE_OCO", "true").lower() == "true"

GEN_DEBUG = os.getenv("GEN_DEBUG", "true").lower() == "true"
GEN_LOG_EVERY_TICK = os.getenv("GEN_LOG_EVERY_TICK", "true").lower() == "true"

# ✅ Chop / volatility gates (NEW)
# 1) Minimum percent move (range) over last 20 candles
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "0.35"))  # %
# 2) Price must be at least this % above MA20 to avoid micro-cross noise
MA_GAP_PCT = float(os.getenv("MA_GAP_PCT", "0.12"))  # %

_last_emit_ts: float = 0.0
_last_signature: Optional[Tuple[str, str]] = None

EXCHANGE = ccxt.binance({"enableRateLimit": True})


def _now_utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_symbols() -> List[str]:
    raw = os.getenv("BOT_SYMBOLS", "").strip()
    if not raw:
        raw = os.getenv("SYMBOL_WHITELIST", "").strip()
    if not raw:
        raw = os.getenv("BOT_SYMBOL", "BTC/USDT").strip()

    syms = []
    for s in raw.split(","):
        s = s.strip()
        if not s:
            continue
        syms.append(s.upper())
    return syms


SYMBOLS = _parse_symbols()


def _has_active_oco(symbol: str) -> bool:
    try:
        return has_active_oco_for_symbol(symbol)
    except Exception as e:
        # safe default: assume active_oco to avoid opening uncontrolled trades
        logger.warning(f"[GEN] ACTIVE_OCO_CHECK_FAIL | symbol={symbol} err={e} -> assume active_oco=True")
        return True


def _pct(a: float, b: float) -> float:
    # percent change from b -> a
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def generate_signal() -> Optional[Dict[str, Any]]:
    for symbol in SYMBOLS:
        if BLOCK_SIGNALS_WHEN_ACTIVE_OCO and _has_active_oco(symbol):
            if GEN_DEBUG:
                logger.info(f"[GEN] SKIP_SYMBOL | symbol={symbol} reason=active_oco=True")
            continue

        try:
            t0 = time.time()
            ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
            dt_ms = int((time.time() - t0) * 1000)
            if GEN_DEBUG:
                logger.info(f"[GEN] FETCH_OK | symbol={symbol} tf={TIMEFRAME} candles={len(ohlcv) if ohlcv else 0} dt={dt_ms}ms")
        except Exception as e:
            logger.exception(f"[GEN] FETCH_FAIL | symbol={symbol} tf={TIMEFRAME} err={e}")
            continue

        if not ohlcv or len(ohlcv) < 25:
            if GEN_LOG_EVERY_TICK:
                logger.info(f"[GEN] NO_SIGNAL | symbol={symbol} reason=not_enough_candles got={len(ohlcv) if ohlcv else 0} need>=25")
            continue

        closes = [float(c[4]) for c in ohlcv]
        last = float(closes[-1])
        prev = float(closes[-2])
        ma20 = float(sum(closes[-20:]) / 20.0)

        # Core conditions
        cond_ma = last > ma20
        cond_mom = last > prev

        # ✅ NEW gate 1: ensure there is real movement (avoid chop)
        window = closes[-20:]
        hi = max(window)
        lo = min(window)
        move_pct = _pct(hi, lo)  # range % over window
        cond_move = move_pct >= MIN_MOVE_PCT

        # ✅ NEW gate 2: require real separation above MA20
        ma_gap_pct = _pct(last, ma20)
        cond_gap = ma_gap_pct >= MA_GAP_PCT

        if GEN_LOG_EVERY_TICK:
            logger.info(
                f"[GEN] SNAPSHOT | symbol={symbol} last={last:.2f} prev={prev:.2f} ma20={ma20:.2f} "
                f"move20={move_pct:.2f}% ma_gap={ma_gap_pct:.2f}% "
                f"cond(last>ma20)={cond_ma} cond(last>prev)={cond_mom} cond(move>={MIN_MOVE_PCT})={cond_move} cond(gap>={MA_GAP_PCT})={cond_gap}"
            )

        if not (cond_ma and cond_mom and cond_move and cond_gap):
            if GEN_LOG_EVERY_TICK:
                reason = []
                if not cond_ma:
                    reason.append("last<=ma20")
                if not cond_mom:
                    reason.append("last<=prev")
                if not cond_move:
                    reason.append(f"move20<{MIN_MOVE_PCT}%")
                if not cond_gap:
                    reason.append(f"ma_gap<{MA_GAP_PCT}%")
                logger.info(f"[GEN] NO_SIGNAL | symbol={symbol} reason={','.join(reason)}")
            continue

        mode_allowed = {"demo": True, "live": bool(ALLOW_LIVE_SIGNALS)}
        signal_id = f"GBM-AUTO-{uuid.uuid4().hex}"

        quote_amount = float(BOT_QUOTE_PER_TRADE)
        base_amount = quote_amount / float(last) if float(last) > 0 else 0.0

        sig = {
            "signal_id": signal_id,
            "timestamp_utc": _now_utc_iso(),
            "final_verdict": "TRADE",
            "certified_signal": True,
            "confidence": CONFIDENCE,
            "mode_allowed": mode_allowed,
            "execution": {
                "symbol": symbol,
                "direction": "LONG",
                "entry": {"type": "MARKET", "price": None},
                "position_size": base_amount,
                "quote_amount": quote_amount,
                "risk": {"stop_loss": None, "take_profit": None},
            },
        }

        if GEN_DEBUG:
            logger.info(
                f"[GEN] SIGNAL_READY | id={signal_id} verdict=TRADE symbol={symbol} dir=LONG "
                f"mode_allowed={mode_allowed} quote_amount={quote_amount} base_size={base_amount}"
            )

        return sig

    return None


def run_once(outbox_path: str) -> bool:
    global _last_emit_ts, _last_signature

    now = time.time()
    elapsed = now - _last_emit_ts
    if elapsed < COOLDOWN_SECONDS:
        if GEN_DEBUG:
            logger.info(f"[GEN] SKIP | cooldown_active left~{int(COOLDOWN_SECONDS - elapsed)}s")
        return False

    sig = generate_signal()
    if not sig:
        return False

    symbol = (sig.get("execution") or {}).get("symbol")
    direction = (sig.get("execution") or {}).get("direction")
    signature = (str(symbol), str(direction))

    try:
        append_signal(sig, outbox_path)
        _last_emit_ts = now
        _last_signature = signature
        if GEN_DEBUG:
            logger.info(f"[GEN] OUTBOX_APPEND_OK | path={outbox_path} id={sig.get('signal_id')} signature={signature}")
        return True
    except Exception as e:
        logger.exception(f"[GEN] OUTBOX_APPEND_FAIL | path={outbox_path} err={e}")
        return False

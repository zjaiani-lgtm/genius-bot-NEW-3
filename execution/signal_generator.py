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


# =========================
# Timeframes (NEW: multi-TF)
# =========================
REGIME_TIMEFRAME = os.getenv("REGIME_TIMEFRAME", "1d").strip()
ENTRY_TIMEFRAME = os.getenv("ENTRY_TIMEFRAME", os.getenv("BOT_TIMEFRAME", "4h")).strip()
CONFIRM_TIMEFRAME = os.getenv("CONFIRM_TIMEFRAME", "1h").strip()
USE_CONFIRMATION = os.getenv("USE_CONFIRMATION", "false").lower() == "true"

# Candle history
REGIME_CANDLE_LIMIT = int(os.getenv("REGIME_CANDLE_LIMIT", "220"))   # enough for MA200
ENTRY_CANDLE_LIMIT = int(os.getenv("BOT_CANDLE_LIMIT", "220"))       # used to be BOT_CANDLE_LIMIT
CONFIRM_CANDLE_LIMIT = int(os.getenv("CONFIRM_CANDLE_LIMIT", "120"))

# Emit controls
COOLDOWN_SECONDS = int(os.getenv("BOT_SIGNAL_COOLDOWN_SECONDS", "1800"))
ALLOW_LIVE_SIGNALS = os.getenv("ALLOW_LIVE_SIGNALS", "false").lower() == "true"

# USDT per trade (prevents NOTIONAL issues & keeps sizing consistent across symbols)
BOT_QUOTE_PER_TRADE = float(os.getenv("BOT_QUOTE_PER_TRADE", "15"))

# Signal confidence (stored in payload, not used for logic here)
CONFIDENCE = float(os.getenv("BOT_SIGNAL_CONFIDENCE", "0.55"))

# Avoid opening new signals if active OCO exists
BLOCK_SIGNALS_WHEN_ACTIVE_OCO = os.getenv("BLOCK_SIGNALS_WHEN_ACTIVE_OCO", "true").lower() == "true"

# Logging flags
GEN_DEBUG = os.getenv("GEN_DEBUG", "true").lower() == "true"
GEN_LOG_EVERY_TICK = os.getenv("GEN_LOG_EVERY_TICK", "true").lower() == "true"

# =========================
# Chop / volatility gates
# =========================
# Minimum percent move (range) over last 20 candles (ENTRY TF)
MIN_MOVE_PCT = float(os.getenv("MIN_MOVE_PCT", "1.20"))  # % (4h default suggestion)
# Price must be at least this % above MA20 (ENTRY TF)
MA_GAP_PCT = float(os.getenv("MA_GAP_PCT", "0.25"))      # % (4h default suggestion)

# =========================
# Regime control (NEW)
# =========================
# If true: allow LONG only in BULL or NEUTRAL. If false: ignore regime.
USE_REGIME_FILTER = os.getenv("USE_REGIME_FILTER", "true").lower() == "true"
ALLOW_NEUTRAL = os.getenv("ALLOW_NEUTRAL_REGIME", "true").lower() == "true"

_last_emit_ts: float = 0.0
_last_fingerprint: Optional[str] = None

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
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _ma(values: List[float], n: int) -> Optional[float]:
    if not values or len(values) < n:
        return None
    return sum(values[-n:]) / float(n)


def _fetch_closes(symbol: str, timeframe: str, limit: int) -> Optional[List[Tuple[int, float]]]:
    """
    Returns list of (ts_ms, close).
    """
    try:
        t0 = time.time()
        ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        dt_ms = int((time.time() - t0) * 1000)
        if GEN_DEBUG:
            logger.info(f"[GEN] FETCH_OK | symbol={symbol} tf={timeframe} candles={len(ohlcv) if ohlcv else 0} dt={dt_ms}ms")
        if not ohlcv:
            return None
        return [(int(c[0]), float(c[4])) for c in ohlcv]
    except Exception as e:
        logger.exception(f"[GEN] FETCH_FAIL | symbol={symbol} tf={timeframe} err={e}")
        return None


def _compute_regime(symbol: str) -> str:
    """
    Simple regime:
      BULL: close > MA200 and MA50 > MA200
      BEAR: close < MA200 and MA50 < MA200
      NEUTRAL: otherwise
    """
    data = _fetch_closes(symbol, REGIME_TIMEFRAME, REGIME_CANDLE_LIMIT)
    if not data or len(data) < 210:
        return "UNKNOWN"

    closes = [c for _, c in data]
    last = closes[-1]
    ma50 = _ma(closes, 50)
    ma200 = _ma(closes, 200)
    if ma50 is None or ma200 is None:
        return "UNKNOWN"

    if last > ma200 and ma50 > ma200:
        return "BULL"
    if last < ma200 and ma50 < ma200:
        return "BEAR"
    return "NEUTRAL"


def _entry_signal(symbol: str) -> Optional[Dict[str, Any]]:
    data = _fetch_closes(symbol, ENTRY_TIMEFRAME, ENTRY_CANDLE_LIMIT)
    if not data or len(data) < 25:
        if GEN_LOG_EVERY_TICK:
            logger.info(f"[GEN] NO_SIGNAL | symbol={symbol} tf={ENTRY_TIMEFRAME} reason=not_enough_candles")
        return None

    closes = [c for _, c in data]
    last_ts = data[-1][0]
    last = float(closes[-1])
    prev = float(closes[-2])
    ma20 = float(sum(closes[-20:]) / 20.0)

    cond_ma = last > ma20
    cond_mom = last > prev

    window = closes[-20:]
    hi = max(window)
    lo = min(window)
    move_pct = _pct(hi, lo)
    cond_move = move_pct >= MIN_MOVE_PCT

    ma_gap_pct = _pct(last, ma20)
    cond_gap = ma_gap_pct >= MA_GAP_PCT

    if GEN_LOG_EVERY_TICK:
        logger.info(
            f"[GEN] SNAPSHOT | symbol={symbol} tf={ENTRY_TIMEFRAME} last={last:.4f} prev={prev:.4f} ma20={ma20:.4f} "
            f"move20={move_pct:.2f}% ma_gap={ma_gap_pct:.2f}% "
            f"cond_ma={cond_ma} cond_mom={cond_mom} cond_move={cond_move} cond_gap={cond_gap}"
        )

    if not (cond_ma and cond_mom and cond_move and cond_gap):
        return None

    return {"last": last, "last_ts": last_ts}


def _confirm_signal(symbol: str) -> bool:
    if not USE_CONFIRMATION:
        return True

    data = _fetch_closes(symbol, CONFIRM_TIMEFRAME, CONFIRM_CANDLE_LIMIT)
    if not data or len(data) < 25:
        return False

    closes = [c for _, c in data]
    last = float(closes[-1])
    prev = float(closes[-2])
    ma20 = float(sum(closes[-20:]) / 20.0)

    # simple confirmation: stay above MA20 + positive momentum
    return (last > ma20) and (last > prev)


def generate_signal() -> Optional[Dict[str, Any]]:
    for symbol in SYMBOLS:
        if BLOCK_SIGNALS_WHEN_ACTIVE_OCO and _has_active_oco(symbol):
            if GEN_DEBUG:
                logger.info(f"[GEN] SKIP_SYMBOL | symbol={symbol} reason=active_oco=True")
            continue

        # 1) Regime filter (D1 by default)
        regime = "IGNORED"
        if USE_REGIME_FILTER:
            regime = _compute_regime(symbol)
            if regime == "BEAR":
                if GEN_DEBUG:
                    logger.info(f"[GEN] REGIME_BLOCK | symbol={symbol} regime=BEAR")
                continue
            if regime == "NEUTRAL" and not ALLOW_NEUTRAL:
                if GEN_DEBUG:
                    logger.info(f"[GEN] REGIME_BLOCK | symbol={symbol} regime=NEUTRAL allow_neutral=False")
                continue
            if regime == "UNKNOWN":
                if GEN_DEBUG:
                    logger.info(f"[GEN] REGIME_BLOCK | symbol={symbol} regime=UNKNOWN")
                continue

        # 2) Entry signal on H4 (or BOT_TIMEFRAME)
        entry = _entry_signal(symbol)
        if not entry:
            continue

        # 3) Optional confirmation on H1
        if not _confirm_signal(symbol):
            if GEN_DEBUG:
                logger.info(f"[GEN] CONFIRM_BLOCK | symbol={symbol} confirm_tf={CONFIRM_TIMEFRAME}")
            continue

        last = float(entry["last"])
        last_ts = int(entry["last_ts"])

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
            "meta": {
                "regime_tf": REGIME_TIMEFRAME,
                "regime": regime,
                "entry_tf": ENTRY_TIMEFRAME,
                "confirm_tf": CONFIRM_TIMEFRAME if USE_CONFIRMATION else None,
                "entry_candle_ts_ms": last_ts,
            },
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
                f"[GEN] SIGNAL_READY | id={signal_id} symbol={symbol} dir=LONG "
                f"regime={regime} entry_tf={ENTRY_TIMEFRAME} confirm={USE_CONFIRMATION} "
                f"mode_allowed={mode_allowed} quote_amount={quote_amount} base_size={base_amount}"
            )

        return sig

    return None


def run_once(outbox_path: str) -> bool:
    global _last_emit_ts, _last_fingerprint

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
    candle_ts = (sig.get("meta") or {}).get("entry_candle_ts_ms")
    fingerprint = f"{symbol}|{ENTRY_TIMEFRAME}|{candle_ts}"

    # avoid re-emitting same candle signal even if cooldown passed (safety)
    if _last_fingerprint == fingerprint:
        if GEN_DEBUG:
            logger.info(f"[GEN] SKIP | duplicate_fingerprint={fingerprint}")
        return False

    try:
        append_signal(sig, outbox_path)
        _last_emit_ts = now
        _last_fingerprint = fingerprint
        if GEN_DEBUG:
            logger.info(f"[GEN] OUTBOX_APPEND_OK | path={outbox_path} id={sig.get('signal_id')} fp={fingerprint}")
        return True
    except Exception as e:
        logger.exception(f"[GEN] OUTBOX_APPEND_FAIL | path={outbox_path} err={e}")
        return False

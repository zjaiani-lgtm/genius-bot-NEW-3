import json
import os
import hashlib
import logging
from typing import Any, Dict, List, Optional
from tempfile import NamedTemporaryFile

logger = logging.getLogger("gbm")


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _fingerprint(signal: Dict[str, Any]) -> str:
    verdict = str(signal.get("final_verdict") or "").upper().strip()

    execution = signal.get("execution") or {}
    symbol = str(execution.get("symbol") or "").upper().strip()
    direction = str(execution.get("direction") or "").upper().strip()
    entry_type = str((execution.get("entry") or {}).get("type") or "").upper().strip()
    pos_size = _safe_float(execution.get("position_size"))

    base = f"v1:{verdict}:{symbol}:{direction}:{entry_type}:{pos_size}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def validate_signal(signal: Dict[str, Any]) -> None:
    if not isinstance(signal, dict):
        raise ValueError("SIGNAL_NOT_DICT")

    verdict = str(signal.get("final_verdict") or "").upper().strip()
    if verdict not in ("TRADE", "HOLD", "SELL"):
        raise ValueError("INVALID_VERDICT")

    if signal.get("certified_signal") is not True:
        raise ValueError("NOT_CERTIFIED")

    execution = signal.get("execution") or {}
    symbol = execution.get("symbol")
    direction = str(execution.get("direction") or "").upper().strip()
    entry = execution.get("entry") or {}
    entry_type = str(entry.get("type") or "").upper().strip()

    if not symbol:
        raise ValueError("MISSING_EXEC_SYMBOL")
    if direction != "LONG":
        raise ValueError("INVALID_DIRECTION")

    if verdict == "TRADE":
        if entry_type != "MARKET":
            raise ValueError("INVALID_ENTRY_TYPE")

        ps = _safe_float(execution.get("position_size"))
        qa = _safe_float(execution.get("quote_amount"))
        if (ps is None or ps <= 0) and (qa is None or qa <= 0):
            raise ValueError("INVALID_POSITION_SIZE")


def _read_outbox(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"signals": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"signals": []}
    if "signals" not in data or not isinstance(data["signals"], list):
        data["signals"] = []
    return data


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)

    with NamedTemporaryFile("w", delete=False, dir=d, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
        tmp = tf.name

    os.replace(tmp, path)


def append_signal(signal: Dict[str, Any], outbox_path: str) -> None:
    validate_signal(signal)

    fp = _fingerprint(signal)
    signal["_fingerprint"] = fp

    data = _read_outbox(outbox_path)
    signals: List[Dict[str, Any]] = data.get("signals", [])

    if any((s.get("_fingerprint") == fp) for s in signals[-50:]):
        logger.info(f"OUTBOX_DEDUPED | fingerprint={fp}")
        return

    signals.append(signal)
    data["signals"] = signals
    _atomic_write_json(outbox_path, data)


def pop_next_signal(outbox_path: str) -> Optional[Dict[str, Any]]:
    data = _read_outbox(outbox_path)
    signals: List[Dict[str, Any]] = data.get("signals", [])
    if not signals:
        return None

    sig = signals.pop(0)
    data["signals"] = signals
    _atomic_write_json(outbox_path, data)
    return sig

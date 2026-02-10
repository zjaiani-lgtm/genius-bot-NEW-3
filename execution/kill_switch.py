# execution/kill_switch.py
import os
import logging
from typing import Any

from execution.db.repository import get_system_state

logger = logging.getLogger("gbm")


def _to_bool01(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) != 0
    if isinstance(v, str):
        s = v.strip().lower()
        return s in ("1", "true", "yes", "y", "on")
    return False


def is_kill_switch_active() -> bool:
    """
    Absolute kill switch gate.
    ON if:
      - ENV KILL_SWITCH=true
      - DB system_state.kill_switch == 1
    """
    env_kill = os.getenv("KILL_SWITCH", "false").lower() == "true"
    if env_kill:
        return True

    try:
        raw = get_system_state()
        # tuple: (id, status, startup_sync_ok, kill_switch, updated_at)
        if isinstance(raw, (list, tuple)) and len(raw) >= 4:
            return _to_bool01(raw[3])
        if isinstance(raw, dict):
            return _to_bool01(raw.get("kill_switch"))
    except Exception as e:
        # fail-closed for safety
        logger.error(f"KILL_SWITCH_READ_FAIL | err={e} -> assume ACTIVE")
        return True

    return False

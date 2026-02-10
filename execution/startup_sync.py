# execution/startup_sync.py
import os
import logging

from execution.db.repository import update_system_state, log_event

logger = logging.getLogger("gbm")


def run_startup_sync() -> bool:
    """
    Goal:
      - In LIVE/TESTNET: verify exchange connectivity (diagnostics)
      - Mark system_state as ACTIVE + startup_sync_ok=1 if OK
      - Otherwise PAUSE + startup_sync_ok=0
    """
    mode = os.getenv("MODE", "DEMO").upper()

    try:
        if mode in ("LIVE", "TESTNET"):
            from execution.exchange_client import BinanceSpotClient

            ex = BinanceSpotClient()
            diag = ex.diagnostics()

            if not diag.get("ok"):
                err = diag.get("error", "unknown")
                logger.warning(f"STARTUP_SYNC: {mode} -> EXCHANGE_CONNECT_FAILED -> PAUSE | err={err}")
                update_system_state(status="PAUSED", startup_sync_ok=False)
                log_event("STARTUP_SYNC_FAILED", f"{mode} exchange_connect_failed err={err}")
                return False

            logger.info(f"STARTUP_SYNC: {mode} -> EXCHANGE_OK | usdt_free={diag.get('usdt_free')} last={diag.get('last_price')}")
            update_system_state(status="ACTIVE", startup_sync_ok=True)
            log_event("STARTUP_SYNC_OK", f"{mode} exchange_ok usdt_free={diag.get('usdt_free')}")
            return True

        # DEMO: just mark active/synced
        update_system_state(status="ACTIVE", startup_sync_ok=True)
        log_event("STARTUP_SYNC_OK", "DEMO ok")
        return True

    except Exception as e:
        logger.warning(f"STARTUP_SYNC: ERROR -> PAUSE | err={e}")
        update_system_state(status="PAUSED", startup_sync_ok=False)
        log_event("STARTUP_SYNC_FAILED", f"{mode} err={e}")
        return False

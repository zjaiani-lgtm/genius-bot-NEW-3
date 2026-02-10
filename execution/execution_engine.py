import os
import logging
from typing import Any, Dict, Optional

import ccxt

from execution.db.repository import (
    get_system_state,
    log_event,
    list_active_oco_links,
    create_oco_link,
    set_oco_status,
    update_system_state,
    signal_id_already_executed,
    mark_signal_id_executed,
)

from execution.kill_switch import is_kill_switch_active
from execution.virtual_wallet import simulate_market_entry

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


def _norm_pct(p: Any) -> Optional[float]:
    """
    Accept:
      - 0.2  (fraction)
      - 20   (percent)
      - "0.2" / "20"
    Returns fraction in [0..1].
    """
    try:
        if p is None:
            return None
        x = float(p)
        if x > 1.0:
            x = x / 100.0
        if x < 0:
            x = 0.0
        if x > 1:
            x = 1.0
        return x
    except Exception:
        return None


class ExecutionEngine:
    def __init__(self):
        self.mode = os.getenv("MODE", "DEMO").upper()
        self.env_kill_switch = os.getenv("KILL_SWITCH", "false").lower() == "true"
        self.live_confirmation = os.getenv("LIVE_CONFIRMATION", "false").lower() == "true"

        self.price_feed = ccxt.binance({"enableRateLimit": True})

        self.exchange = None
        if self.mode in ("LIVE", "TESTNET"):
            from execution.exchange_client import BinanceSpotClient
            self.exchange = BinanceSpotClient()

        self.state_debug = os.getenv("STATE_DEBUG", "false").lower() == "true"

        self.tp_pct = float(os.getenv("TP_PCT", "0.30"))
        self.sl_pct = float(os.getenv("SL_PCT", "1.00"))
        self.sl_limit_gap_pct = float(os.getenv("SL_LIMIT_GAP_PCT", "0.10"))

        self.sell_buffer = float(os.getenv("SELL_BUFFER", "0.999"))
        self.sell_retry_buffer = float(os.getenv("SELL_RETRY_BUFFER", "0.995"))

    def _load_system_state(self) -> Dict[str, Any]:
        raw = get_system_state()
        if self.state_debug:
            logger.info(f"SYSTEM_STATE_RAW | type={type(raw)} value={raw}")

        if isinstance(raw, (list, tuple)):
            status = raw[1] if len(raw) > 1 else ""
            sync = raw[2] if len(raw) > 2 else 0
            kill = raw[3] if len(raw) > 3 else 0
            return {
                "status": str(status or "").upper(),
                "startup_sync_ok": _to_bool01(sync),
                "kill_switch": _to_bool01(kill),
            }

        if isinstance(raw, dict):
            return {
                "status": str(raw.get("status") or "").upper(),
                "startup_sync_ok": _to_bool01(raw.get("startup_sync_ok")),
                "kill_switch": _to_bool01(raw.get("kill_switch")),
            }

        return {"status": "", "startup_sync_ok": False, "kill_switch": False}

    def reconcile_oco(self) -> None:
        if self.mode not in ("LIVE", "TESTNET"):
            return
        if self.exchange is None:
            return

        rows = list_active_oco_links(limit=50)
        if not rows:
            return

        def _norm(s: Any) -> str:
            return str(s or "").strip().lower()

        CLOSED = {"closed", "filled"}
        CANCELED = {"canceled", "cancelled", "expired", "rejected"}

        for r in rows:
            (
                link_id, signal_id, symbol, base_asset,
                tp_order_id, sl_order_id,
                tp_price, sl_stop_price, sl_limit_price,
                amount, status, created_at, updated_at
            ) = r

            if not tp_order_id or not sl_order_id:
                logger.warning(f"OCO_RECONCILE_SKIP | link={link_id} missing order ids tp='{tp_order_id}' sl='{sl_order_id}'")
                continue

            try:
                tp = self.exchange.fetch_order(tp_order_id, symbol)
                sl = self.exchange.fetch_order(sl_order_id, symbol)

                tp_status = _norm(tp.get("status"))
                sl_status = _norm(sl.get("status"))

                logger.info(
                    f"OCO_RECONCILE | link={link_id} id={signal_id} symbol={symbol} "
                    f"tp={tp_order_id}:{tp_status} sl={sl_order_id}:{sl_status}"
                )

                if sl_status in CLOSED:
                    set_oco_status(link_id, "CLOSED_SL")
                    log_event("OCO_CLOSED", f"{signal_id} SL_FILLED sl={sl_order_id} tp={tp_order_id} tp_status={tp_status}")
                    continue

                if tp_status in CLOSED:
                    set_oco_status(link_id, "CLOSED_TP")
                    log_event("OCO_CLOSED", f"{signal_id} TP_FILLED tp={tp_order_id} sl={sl_order_id} sl_status={sl_status}")
                    continue

                if (tp_status in CANCELED and sl_status == "open") or (sl_status in CANCELED and tp_status == "open"):
                    continue

                if tp_status in CANCELED and sl_status in CANCELED:
                    set_oco_status(link_id, "FAILED")
                    log_event("OCO_FAILED", f"{signal_id} tp={tp_order_id}:{tp_status} sl={sl_order_id}:{sl_status}")
                    continue

            except Exception as e:
                logger.warning(f"OCO_RECONCILE_FAIL | link={link_id} symbol={symbol} err={e}")

    def _cancel_oco_for_symbol(self, symbol: str, sell_signal_id: str) -> None:
        if self.exchange is None:
            return

        def _norm(s: Any) -> str:
            return str(s or "").strip().lower()

        CLOSED = {"closed", "filled"}

        rows = list_active_oco_links(limit=50)
        rows = [r for r in rows if str(r[2] or "").upper() == str(symbol).upper()]

        for r in rows:
            link_id, oco_signal_id, sym, base_asset, tp_order_id, sl_order_id, *_rest = r
            try:
                tp = self.exchange.fetch_order(tp_order_id, symbol)
                sl = self.exchange.fetch_order(sl_order_id, symbol)
                tp_status = _norm(tp.get("status"))
                sl_status = _norm(sl.get("status"))

                if tp_status in CLOSED:
                    set_oco_status(link_id, "CLOSED_TP")
                    log_event("SELL_SKIP", f"{sell_signal_id} {symbol} already closed by TP (link={link_id})")
                    continue
                if sl_status in CLOSED:
                    set_oco_status(link_id, "CLOSED_SL")
                    log_event("SELL_SKIP", f"{sell_signal_id} {symbol} already closed by SL (link={link_id})")
                    continue

                for oid in (tp_order_id, sl_order_id):
                    if not oid:
                        continue
                    try:
                        self.exchange.cancel_order(str(oid), symbol)
                    except Exception as e:
                        logger.warning(f"SELL_CANCEL_WARN | id={sell_signal_id} symbol={symbol} order_id={oid} err={e}")

                set_oco_status(link_id, "CANCELED_BY_SIGNAL")
                log_event("OCO_CANCELED", f"{sell_signal_id} {symbol} link={link_id} canceled_by_signal")

            except Exception as e:
                logger.warning(f"SELL_OCO_LOOKUP_FAIL | id={sell_signal_id} symbol={symbol} link={link_id} err={e}")

    def _rearm_oco_from_db(self, symbol: str, base_asset: str, remaining_amount: float, sell_signal_id: str) -> None:
        """
        Re-arm OCO using last known tp/sl prices in DB for this symbol.
        We take the newest ACTIVE link record values as reference.
        """
        if self.exchange is None:
            return

        rows = list_active_oco_links(limit=50)
        rows = [r for r in rows if str(r[2] or "").upper() == str(symbol).upper()]
        if not rows:
            logger.warning(f"OCO_REARM_SKIP | id={sell_signal_id} symbol={symbol} reason=no_active_link_in_db")
            log_event("OCO_REARM_SKIP", f"{sell_signal_id} {symbol} reason=no_active_link_in_db")
            return

        r = rows[-1]
        (
            link_id, oco_signal_id, sym, base_asset_db,
            tp_order_id, sl_order_id,
            tp_price, sl_stop_price, sl_limit_price,
            amount, status, created_at, updated_at
        ) = r

        try:
            tp_price = float(tp_price)
            sl_stop_price = float(sl_stop_price)
            sl_limit_price = float(sl_limit_price)

            tp_price = self.exchange.floor_price(symbol, tp_price)
            sl_stop_price = self.exchange.floor_price(symbol, sl_stop_price)
            sl_limit_price = self.exchange.floor_price(symbol, sl_limit_price)
            remaining_amount = self.exchange.floor_amount(symbol, remaining_amount)

            if remaining_amount <= 0:
                logger.warning(f"OCO_REARM_SKIP | id={sell_signal_id} symbol={symbol} remaining_amount<=0")
                log_event("OCO_REARM_SKIP", f"{sell_signal_id} {symbol} remaining_amount<=0")
                return

            oco = self.exchange.place_oco_sell(
                symbol=symbol,
                base_amount=remaining_amount,
                tp_price=tp_price,
                sl_stop_price=sl_stop_price,
                sl_limit_price=sl_limit_price,
            )

            raw = oco.get("raw") or {}
            order_reports = raw.get("orderReports") or []

            new_tp_order_id = None
            new_sl_order_id = None

            def _otype(rep):
                return str(rep.get("type") or rep.get("orderType") or "").upper()

            for rep in order_reports:
                oid = rep.get("orderId") or rep.get("order_id")
                if not oid:
                    continue
                oid = str(oid)
                typ = _otype(rep)
                if "STOP" in typ:
                    new_sl_order_id = new_sl_order_id or oid
                else:
                    new_tp_order_id = new_tp_order_id or oid

            orders = raw.get("orders") or []
            if (not new_tp_order_id or not new_sl_order_id) and len(orders) >= 2:
                ids = []
                for o in orders:
                    oid = o.get("orderId") or o.get("order_id")
                    if oid:
                        ids.append(str(oid))
                uniq = []
                for x in ids:
                    if x not in uniq:
                        uniq.append(x)
                if len(uniq) >= 2:
                    new_tp_order_id = new_tp_order_id or uniq[0]
                    new_sl_order_id = new_sl_order_id or uniq[1]

            list_order_id = raw.get("listOrderId") or raw.get("orderListId") or raw.get("list_order_id")

            logger.info(f"OCO_REARM_OK | id={sell_signal_id} symbol={symbol} listOrderId={list_order_id} tp={new_tp_order_id} sl={new_sl_order_id}")
            log_event("OCO_REARM_OK", f"{sell_signal_id} {symbol} listOrderId={list_order_id} tp={new_tp_order_id} sl={new_sl_order_id} amount={remaining_amount}")

            create_oco_link(
                signal_id=f"{sell_signal_id}/REARM",
                symbol=symbol,
                base_asset=base_asset,
                tp_order_id=str(new_tp_order_id or ""),
                sl_order_id=str(new_sl_order_id or ""),
                tp_price=float(tp_price),
                sl_stop_price=float(sl_stop_price),
                sl_limit_price=float(sl_limit_price),
                amount=float(remaining_amount),
            )

        except Exception as e:
            logger.exception(f"OCO_REARM_ERROR | id={sell_signal_id} symbol={symbol} err={e}")
            log_event("OCO_REARM_ERROR", f"{sell_signal_id} {symbol} err={e}")

    def _execute_sell(self, signal: Dict[str, Any]) -> None:
        signal_id = str(signal.get("signal_id", "UNKNOWN"))
        execution = signal.get("execution") or {}
        symbol = str(execution.get("symbol") or "")
        direction = str(execution.get("direction") or "").upper().strip()

        action = str(
            signal.get("action")
            or signal.get("sell_action")
            or ((execution.get("exit") or {}).get("action") if isinstance(execution.get("exit"), dict) else None)
            or "NORMAL"
        ).upper().strip()

        pct = (
            signal.get("pct")
            or signal.get("sell_pct")
            or ((execution.get("exit") or {}).get("pct") if isinstance(execution.get("exit"), dict) else None)
        )
        pct_f = _norm_pct(pct)

        logger.info(f"SELL_ENTER | id={signal_id} symbol={symbol} action={action} pct={pct_f} MODE={self.mode}")

        if not symbol or direction != "LONG":
            log_event("REJECT_BAD_SELL_PAYLOAD", f"{signal_id} symbol={symbol} dir={direction}")
            return

        if self.mode == "DEMO":
            log_event("SELL_DEMO", f"{signal_id} DEMO SELL {symbol} action={action} pct={pct_f}")
            mark_signal_id_executed(signal_id, signal_hash=(signal.get("_fingerprint") or None), action="SELL_DEMO", symbol=symbol)
            return

        if self.exchange is None:
            log_event("SELL_BLOCKED_NO_EXCHANGE", f"{signal_id} {symbol}")
            return

        if is_kill_switch_active():
            log_event("SELL_BLOCKED_KILL_SWITCH_LAST_GATE", f"{signal_id} {symbol}")
            return

        # Cancel any active OCO first (so it doesn't fight us)
        self._cancel_oco_for_symbol(symbol=symbol, sell_signal_id=signal_id)

        base_asset = symbol.split("/")[0].upper()
        free_base = float(self.exchange.fetch_balance_free(base_asset))

        if free_base <= 0:
            log_event("SELL_SKIP_NO_FREE_BASE", f"{signal_id} {symbol} free_{base_asset}={free_base}")
            mark_signal_id_executed(signal_id, signal_hash=(signal.get("_fingerprint") or None), action="SELL_NO_FREE_BASE", symbol=symbol)
            return

        if pct_f is None:
            pct_f = 1.0 if action in ("NORMAL", "EMERGENCY") else 0.3

        sell_amount = free_base * pct_f
        sell_amount = self.exchange.floor_amount(symbol, sell_amount * self.sell_buffer)
        if sell_amount <= 0:
            sell_amount = self.exchange.floor_amount(symbol, free_base * pct_f * self.sell_retry_buffer)

        if sell_amount <= 0:
            log_event("SELL_SKIP_NO_SELL_AMOUNT", f"{signal_id} {symbol} free_{base_asset}={free_base} pct={pct_f}")
            mark_signal_id_executed(signal_id, signal_hash=(signal.get("_fingerprint") or None), action="SELL_NO_AMOUNT", symbol=symbol)
            return

        try:
            sell = self.exchange.place_market_sell(symbol=symbol, base_amount=sell_amount)
            avg = float(sell.get("average") or sell.get("price") or 0.0) or self.exchange.fetch_last_price(symbol)
            log_event("SELL_LIVE_OK", f"{signal_id} {symbol} amount={sell_amount} avg={avg} action={action} pct={pct_f}")
            logger.info(f"SELL_LIVE_OK | id={signal_id} symbol={symbol} amount={sell_amount} avg={avg} action={action} pct={pct_f} order_id={sell.get('id')}")

            # After PARTIAL sell, re-arm OCO for remaining base (best-effort)
            if action == "PARTIAL":
                new_free_base = float(self.exchange.fetch_balance_free(base_asset))
                remaining = self.exchange.floor_amount(symbol, new_free_base * self.sell_buffer)
                if remaining > 0:
                    self._rearm_oco_from_db(symbol=symbol, base_asset=base_asset, remaining_amount=remaining, sell_signal_id=signal_id)

            mark_signal_id_executed(signal_id, signal_hash=(signal.get("_fingerprint") or None), action=f"SELL_{action}", symbol=symbol)

        except Exception as e:
            logger.exception(f"SELL_LIVE_ERROR | id={signal_id} symbol={symbol} err={e}")
            log_event("SELL_LIVE_ERROR", f"{signal_id} {symbol} err={e}")

    def execute_signal(self, signal: Dict[str, Any]) -> None:
        signal_id = str(signal.get("signal_id", "UNKNOWN"))
        verdict = str(signal.get("final_verdict", "")).upper()

        logger.info(f"EXEC_ENTER | id={signal_id} verdict={verdict} MODE={self.mode} ENV_KILL_SWITCH={self.env_kill_switch}")

        try:
            if signal_id_already_executed(signal_id):
                logger.warning(f"EXEC_DEDUPED | duplicate ignored | id={signal_id}")
                log_event("EXEC_DEDUPED", f"id={signal_id}")
                return
        except Exception as e:
            logger.error(f"EXEC_BLOCKED | idempotency_check_failed | id={signal_id} err={e}")
            log_event("EXEC_BLOCKED_IDEMPOTENCY_FAIL", f"{signal_id} err={e}")
            return

        state = self._load_system_state()
        db_status = str(state.get("status") or "").upper()
        db_kill = bool(state.get("kill_switch"))
        sync_ok = bool(state.get("startup_sync_ok"))

        if self.env_kill_switch or db_kill:
            logger.warning(f"EXEC_BLOCKED | KILL_SWITCH=ON | id={signal_id}")
            log_event("EXEC_BLOCKED_KILL_SWITCH", f"{signal_id}")
            return

        if not sync_ok or db_status not in ("ACTIVE", "RUNNING"):
            logger.warning(f"EXEC_BLOCKED | system not ACTIVE/synced | id={signal_id} status={db_status} sync_ok={sync_ok}")
            log_event("EXEC_BLOCKED_SYSTEM_STATE", f"{signal_id} status={db_status} sync_ok={sync_ok}")
            return

        if self.mode == "LIVE" and not self.live_confirmation:
            logger.warning(f"EXEC_BLOCKED | LIVE_CONFIRMATION=OFF | id={signal_id}")
            log_event("EXEC_BLOCKED_LIVE_CONFIRMATION", f"{signal_id}")
            return

        if signal.get("certified_signal") is not True:
            log_event("REJECT_NOT_CERTIFIED", f"{signal_id}")
            return

        # SELL path
        if verdict == "SELL":
            self._execute_sell(signal)
            return

        execution = signal.get("execution") or {}
        symbol = execution.get("symbol")
        direction = str(execution.get("direction", "")).upper()
        entry = execution.get("entry") or {}
        entry_type = str(entry.get("type", "")).upper()

        position_size = execution.get("position_size")
        quote_amount = execution.get("quote_amount")

        if not symbol or direction != "LONG" or entry_type != "MARKET":
            logger.warning(f"EXEC_REJECT | bad payload | id={signal_id} symbol={symbol} dir={direction} entry={entry_type}")
            log_event("REJECT_BAD_PAYLOAD", f"{signal_id}")
            return

        signal_hash = signal.get("_fingerprint") or signal.get("signal_hash")

        if self.mode == "DEMO":
            last_price = float(self.price_feed.fetch_ticker(symbol)["last"])
            base_size = float(position_size) if position_size is not None else float(quote_amount) / float(last_price)
            resp = simulate_market_entry(symbol=symbol, side=direction, size=base_size, price=last_price)

            log_event("TRADE_EXECUTED", f"{signal_id} DEMO {symbol} size={base_size} price={last_price}")
            logger.info(f"EXEC_DEMO_OK | id={signal_id} resp={resp}")

            mark_signal_id_executed(signal_id, signal_hash=signal_hash, action="TRADE_DEMO", symbol=str(symbol))
            return

        if self.exchange is None:
            log_event("EXEC_BLOCKED_NO_EXCHANGE", f"{signal_id}")
            logger.warning(f"EXEC_BLOCKED | exchange client not wired | id={signal_id}")
            return

        from execution.exchange_client import LiveTradingBlocked

        try:
            if quote_amount is None:
                last = self.exchange.fetch_last_price(symbol)
                quote_amount = float(position_size) * float(last)
            quote_amount = float(quote_amount)

            min_notional = 0.0
            try:
                min_notional = float(self.exchange.get_min_notional(symbol))
            except Exception:
                min_notional = 0.0

            if min_notional > 0 and quote_amount < min_notional:
                msg = (
                    f"EXEC_REJECT | MIN_NOTIONAL | id={signal_id} symbol={symbol} "
                    f"quote={quote_amount:.8f} < min_notional={min_notional}"
                )
                logger.warning(msg)
                log_event("EXEC_REJECT_MIN_NOTIONAL", msg)
                mark_signal_id_executed(signal_id, signal_hash=signal_hash, action="REJECT_MIN_NOTIONAL", symbol=str(symbol))
                return

            if is_kill_switch_active():
                logger.error(f"KILL_SWITCH_ACTIVE_LAST_GATE | BUY_BLOCKED | id={signal_id}")
                log_event("EXEC_BLOCKED_KILL_SWITCH_LAST_GATE", f"{signal_id} BUY_BLOCKED")
                return

            buy = self.exchange.place_market_buy_by_quote(symbol=symbol, quote_amount=quote_amount)
            buy_avg = float(buy.get("average") or buy.get("price") or 0.0) or self.exchange.fetch_last_price(symbol)

            logger.info(f"EXEC_LIVE_BUY_OK | id={signal_id} symbol={symbol} quote={quote_amount} avg={buy_avg} order_id={buy.get('id')}")
            log_event("TRADE_EXECUTED", f"{signal_id} LIVE BUY {symbol} quote={quote_amount} avg={buy_avg} order_id={buy.get('id')}")

            mark_signal_id_executed(signal_id, signal_hash=signal_hash, action="TRADE_LIVE_BUY", symbol=str(symbol))

            base_asset = symbol.split("/")[0].upper()
            free_base = float(self.exchange.fetch_balance_free(base_asset))

            sell_amount = self.exchange.floor_amount(symbol, free_base * self.sell_buffer)
            if sell_amount <= 0:
                sell_amount = self.exchange.floor_amount(symbol, free_base * self.sell_retry_buffer)

            if sell_amount <= 0:
                msg = f"OCO_SKIP_NO_FREE_BASE | id={signal_id} free_{base_asset}={free_base}"
                logger.warning(msg)
                log_event("OCO_SKIP_NO_FREE_BASE", msg)
                return

            tp_price = self.exchange.floor_price(symbol, buy_avg * (1.0 + (self.tp_pct / 100.0)))
            sl_stop_price = self.exchange.floor_price(symbol, buy_avg * (1.0 - (self.sl_pct / 100.0)))
            sl_limit_price = self.exchange.floor_price(symbol, sl_stop_price * (1.0 - (self.sl_limit_gap_pct / 100.0)))

            logger.info(
                f"OCO_PREP | id={signal_id} free_{base_asset}={free_base} sell_amount={sell_amount} "
                f"tp={tp_price} sl_stop={sl_stop_price} sl_limit={sl_limit_price}"
            )

            if is_kill_switch_active():
                logger.error(f"KILL_SWITCH_ACTIVE_LAST_GATE | OCO_BLOCKED | id={signal_id}")
                log_event("EXEC_BLOCKED_KILL_SWITCH_LAST_GATE", f"{signal_id} OCO_BLOCKED")
                return

            oco = self.exchange.place_oco_sell(
                symbol=symbol,
                base_amount=sell_amount,
                tp_price=tp_price,
                sl_stop_price=sl_stop_price,
                sl_limit_price=sl_limit_price,
            )

            raw = oco.get("raw") or {}
            order_reports = raw.get("orderReports") or []

            tp_order_id = None
            sl_order_id = None

            def _otype(rep):
                return str(rep.get("type") or rep.get("orderType") or "").upper()

            for rep in order_reports:
                oid = rep.get("orderId") or rep.get("order_id")
                if not oid:
                    continue
                oid = str(oid)
                typ = _otype(rep)
                if "STOP" in typ:
                    sl_order_id = sl_order_id or oid
                else:
                    tp_order_id = tp_order_id or oid

            orders = raw.get("orders") or []
            if (not tp_order_id or not sl_order_id) and len(orders) >= 2:
                ids = []
                for o in orders:
                    oid = o.get("orderId") or o.get("order_id")
                    if oid:
                        ids.append(str(oid))
                uniq = []
                for x in ids:
                    if x not in uniq:
                        uniq.append(x)
                if len(uniq) >= 2:
                    tp_order_id = tp_order_id or uniq[0]
                    sl_order_id = sl_order_id or uniq[1]

            list_order_id = raw.get("listOrderId") or raw.get("orderListId") or raw.get("list_order_id")

            logger.info(f"OCO_OK | id={signal_id} listOrderId={list_order_id} tp={tp_order_id} sl={sl_order_id}")
            log_event("OCO_ARMED", f"{signal_id} symbol={symbol} listOrderId={list_order_id} tp={tp_order_id} sl={sl_order_id} amount={sell_amount}")

            if (not list_order_id) or (not tp_order_id) or (not sl_order_id) or (str(tp_order_id) == str(sl_order_id)):
                msg = (
                    f"OCO_INVALID | id={signal_id} symbol={symbol} "
                    f"listOrderId={list_order_id} tp={tp_order_id} sl={sl_order_id} -> PROTECTION_FAILED"
                )
                logger.error(msg)
                log_event("OCO_INVALID", msg)

                update_system_state(kill_switch=1)
                logger.error("FAILSAFE | DB kill_switch=1 set due to OCO_INVALID")
                log_event("FAILSAFE_KILL_SWITCH_SET", f"{signal_id} OCO_INVALID")
                return

            create_oco_link(
                signal_id=signal_id,
                symbol=symbol,
                base_asset=base_asset,
                tp_order_id=str(tp_order_id or ""),
                sl_order_id=str(sl_order_id or ""),
                tp_price=float(tp_price),
                sl_stop_price=float(sl_stop_price),
                sl_limit_price=float(sl_limit_price),
                amount=float(sell_amount),
            )

            log_event("TRADE_LIVE_ARMED", f"{signal_id} {symbol} OCO_ARMED listOrderId={list_order_id}")

        except LiveTradingBlocked as e:
            msg = f"EXEC_REJECT | LIVE_BLOCKED | id={signal_id} reason={e}"
            logger.warning(msg)
            log_event("EXEC_REJECT_LIVE_BLOCKED", msg)
            mark_signal_id_executed(signal_id, signal_hash=signal_hash, action="REJECT_LIVE_BLOCKED", symbol=str(symbol))
            return

        except Exception as e:
            logger.exception(f"EXEC_LIVE_ERROR | id={signal_id} err={e}")
            log_event("EXEC_LIVE_ERROR", f"{signal_id} err={e}")
            return

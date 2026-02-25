from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from execution.config import Settings
from execution.database import TradeDB
from execution.exchange.base import TokenBucket
from execution.exchange.binance_rest import BinanceSpot
from execution.exchange.bybit_rest import BybitSpot
from execution.exchange.binance_ws import BinanceWS
from execution.exchange.bybit_ws import BybitWS
from execution.ml.signal_model import MLSignalFilter
from execution.portfolio import Portfolio, Position
from execution.risk.manager import RiskManager
from execution.smart_router import SmartRouter
from execution.strategy.orderbook_alpha import compute_long_signal
from execution.backtester import run_backtest

logging.basicConfig(level=Settings().LOG_LEVEL)
log = logging.getLogger("main")


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


class Engine:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.db = TradeDB(s.DB_PATH)
        self.portfolio = Portfolio()
        self.risk = RiskManager(
            position_pct=s.POSITION_PCT,
            stop_atr_mult=s.STOP_ATR_MULT,
            tp_atr_mult=s.TP_ATR_MULT,
            taker_fee=s.TAKER_FEE,
            maker_fee=s.MAKER_FEE,
            slippage_bps=s.SLIPPAGE_BPS,
            partial_tp_pct=s.PARTIAL_TP_PCT,
        )
        self.ml = MLSignalFilter(enabled=s.ML_ENABLED, min_proba=s.ML_MIN_PROBA)
        self.router = SmartRouter()
        self._idx: dict[str, int] = {sym: 0 for sym in s.SYMBOLS}
        self._df15: dict[str, pd.DataFrame] = {}

        limiter = TokenBucket(rate_per_sec=s.REST_RATE_PER_SEC, burst=s.REST_BURST)
        if s.EXCHANGE == "binance":
            self.ex = BinanceSpot(s.BINANCE_BASE_URL, s.BINANCE_API_KEY, s.BINANCE_API_SECRET, limiter)
            self.ws = BinanceWS(s.BINANCE_WS_URL)
        else:
            self.ex = BybitSpot(s.BYBIT_BASE_URL, s.BYBIT_API_KEY, s.BYBIT_API_SECRET, limiter)
            self.ws = BybitWS(s.BYBIT_WS_URL)

    async def seed_history(self, symbol: str) -> None:
        candles = await self.ex.fetch_ohlcv(symbol, self.s.PRIMARY_TF, limit=600)
        df = pd.DataFrame(
            [
                {
                    "ts": _ms_to_dt(c["close_time"]),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                }
                for c in candles
            ]
        ).set_index("ts")
        self._df15[symbol] = df
        log.info("seed_history", extra={"symbol": symbol, "rows": len(df)})

    def resample(self, symbol: str, tf: str) -> pd.DataFrame:
        base = self._df15[symbol]
        if tf == "30m":
            rule = "30min"
        elif tf == "1h":
            rule = "60min"
        else:
            raise ValueError("Unsupported tf")
        o = base["open"].resample(rule, label="right", closed="right").first()
        h = base["high"].resample(rule, label="right", closed="right").max()
        l = base["low"].resample(rule, label="right", closed="right").min()
        c = base["close"].resample(rule, label="right", closed="right").last()
        v = base["volume"].resample(rule, label="right", closed="right").sum()
        return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()

    async def on_closed_15m(self, symbol: str, end_ms: int, o: float, h: float, l: float, c: float, v: float) -> None:
        ts = _ms_to_dt(end_ms)
        df = self._df15[symbol]
        df.loc[ts, ["open", "high", "low", "close", "volume"]] = [o, h, l, c, v]
        df.sort_index(inplace=True)
        if len(df) > 2000:
            df = df.iloc[-2000:]
            self._df15[symbol] = df

        self._idx[symbol] += 1
        idx = self._idx[symbol]

        await self.manage_open_position(symbol, float(c))
        await self.maybe_open_position(symbol, idx)

    async def maybe_open_position(self, symbol: str, idx: int) -> None:
        if self.portfolio.has_position(symbol):
            return
        if self.portfolio.in_cooldown(symbol, idx):
            return

        df15 = self._df15[symbol]
        df30 = self.resample(symbol, "30m")
        df1h = self.resample(symbol, "1h")

        sig = compute_long_signal(
            df15, df30, df1h,
            self.s.EMA_FAST, self.s.EMA_SLOW,
            self.s.RSI_PERIOD, self.s.RSI_LONG_MIN,
            self.s.ATR_PERIOD,
        )
        if sig is None or sig.action != "BUY":
            log.info("signal_hold", extra={"symbol": symbol, "reason": sig.reason if sig else "NO_SIGNAL"})
            return

        # ML confirmation
        if self.s.ML_ENABLED and not self.ml.allow(sig.features):
            log.info("ml_reject", extra={"symbol": symbol, "min": self.s.ML_MIN_PROBA})
            return

        usdt = await self.ex.fetch_usdt_balance()
        notional = self.risk.order_notional_usdt(usdt)
        if notional < 10:
            log.warning("notional_too_small", extra={"symbol": symbol, "usdt": usdt, "notional": notional})
            return

        buy = await self.router.open_long(self.ex, symbol, notional)
        entry = self.risk.apply_slippage(buy.avg_price, is_entry=True)
        qty = buy.executed_qty

        stop, tp = self.risk.stops_from_atr(entry, sig.atr_value)
        trailing = stop
        fee = self.risk.fee_usd(notional, taker=True)

        trade_id = await self.db.insert_entry(
            exchange=self.ex.name,
            symbol=symbol,
            qty=qty,
            entry_price=entry,
            fee_usd=fee,
            meta={"reason": sig.reason, "atr": sig.atr_value},
        )

        pos = Position(
            symbol=symbol,
            qty=qty,
            entry_price=entry,
            entry_time=Portfolio.now(),
            atr_at_entry=sig.atr_value,
            stop_price=stop,
            tp_price=tp,
            best_price=entry,
            trailing_enabled=self.s.TRAILING_ENABLED,
            trailing_stop=trailing,
            trade_id=trade_id,
            partial_done=False,
        )
        self.portfolio.open(pos, idx, self.s.COOLDOWN_CANDLES)

        log.info("entered_long", extra={"symbol": symbol, "qty": qty, "entry": entry, "stop": stop, "tp": tp, "trade_id": trade_id})

        # best-effort partial TP limit
        qty_part = self.risk.partial_qty(qty)
        if qty_part > 0:
            await self.router.place_partial_tp_limit(self.ex, symbol, qty_part, tp)

    async def manage_open_position(self, symbol: str, last_close: float) -> None:
        pos = self.portfolio.get(symbol)
        if pos is None:
            return

        # update best price / trailing
        if last_close > pos.best_price:
            pos.best_price = last_close
            if pos.trailing_enabled:
                pos.trailing_stop = self.risk.trailing_stop(pos.best_price, pos.atr_at_entry)

        stop_level = min(pos.stop_price, pos.trailing_stop) if pos.trailing_enabled else pos.stop_price

        # partial TP software fallback
        if (not pos.partial_done) and last_close >= pos.tp_price:
            qty_part = self.risk.partial_qty(pos.qty)
            if qty_part > 0:
                sell = await self.router.close_long_market(self.ex, symbol, qty_part)
                exit_px = self.risk.apply_slippage(sell.avg_price, is_entry=False)
                notional = qty_part * exit_px
                fee = self.risk.fee_usd(notional, taker=True)
                pnl = (exit_px - pos.entry_price) * qty_part - fee
                pos.qty -= qty_part
                pos.partial_done = True
                log.info("partial_tp", extra={"symbol": symbol, "qty": qty_part, "exit": exit_px, "pnl": pnl})

        # stop out
        if last_close <= stop_level:
            qty = pos.qty
            if qty > 0:
                sell = await self.router.close_long_market(self.ex, symbol, qty)
                exit_px = self.risk.apply_slippage(sell.avg_price, is_entry=False)
                notional = qty * exit_px
                fee = self.risk.fee_usd(notional, taker=True)
                pnl = (exit_px - pos.entry_price) * qty - fee
                await self.db.close_trade(pos.trade_id, exit_px, pnl, fee)
                log.info("stop_exit", extra={"symbol": symbol, "qty": qty, "exit": exit_px, "pnl": pnl, "stop": stop_level})
            await self.router.cancel_all(self.ex, symbol)
            self.portfolio.close(symbol)
            return

        # full TP after partial (simple: same TP target)
        if pos.partial_done and last_close >= pos.tp_price:
            qty = pos.qty
            if qty > 0:
                sell = await self.router.close_long_market(self.ex, symbol, qty)
                exit_px = self.risk.apply_slippage(sell.avg_price, is_entry=False)
                notional = qty * exit_px
                fee = self.risk.fee_usd(notional, taker=True)
                pnl = (exit_px - pos.entry_price) * qty - fee
                await self.db.close_trade(pos.trade_id, exit_px, pnl, fee)
                log.info("tp_exit", extra={"symbol": symbol, "qty": qty, "exit": exit_px, "pnl": pnl})
            await self.router.cancel_all(self.ex, symbol)
            self.portfolio.close(symbol)

    async def run_live(self) -> None:
        await self.db.init()
        for sym in self.s.SYMBOLS:
            await self.seed_history(sym)

        log.info("live_start", extra={"exchange": self.ex.name, "symbols": list(self.s.SYMBOLS), "tf": self.s.PRIMARY_TF})

        async for msg in self.ws.stream_klines(list(self.s.SYMBOLS), self.s.PRIMARY_TF):
            if not msg.is_closed:
                continue
            if msg.symbol not in self._df15:
                continue
            await self.on_closed_15m(msg.symbol, msg.end_ms, msg.o, msg.h, msg.l, msg.c, msg.v)

    async def run_backtest_cli(self, symbol: str, days: int = 90) -> None:
        candles = await self.ex.fetch_ohlcv(symbol, self.s.PRIMARY_TF, limit=min(2000, days * 96))
        df15 = pd.DataFrame(
            [
                {"ts": _ms_to_dt(c["close_time"]), "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c["volume"]}
                for c in candles
            ]
        ).set_index("ts")
        rep = run_backtest(df15, self.s, self.risk, start_balance=self.s.BACKTEST_START_BALANCE)
        log.info("backtest_report", extra={"symbol": symbol, "pnl": rep.pnl, "win_rate": rep.win_rate, "max_dd": rep.max_dd, "sharpe": rep.sharpe, "trades": rep.trades})


async def main() -> None:
    s = Settings()
    engine = Engine(s)

    # Simple CLI via env:
    # RUN_BACKTEST=1 BACKTEST_SYMBOL=BTCUSDT BACKTEST_DAYS=120
    if (np_str := __import__("os").getenv("RUN_BACKTEST")) and np_str.strip() == "1":
        sym = __import__("os").getenv("BACKTEST_SYMBOL", "BTCUSDT").strip().upper()
        days = int(__import__("os").getenv("BACKTEST_DAYS", "90"))
        await engine.run_backtest_cli(sym, days)
        return

    await engine.run_live()


if __name__ == "__main__":
    asyncio.run(main())

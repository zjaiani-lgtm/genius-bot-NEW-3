from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from execution.indicators import ema, rsi, atr
from execution.strategy.orderbook_alpha import compute_long_signal
from execution.risk.manager import RiskManager

log = logging.getLogger("backtester")


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak.replace(0, np.nan)
    return float(dd.min()) if len(dd) else 0.0


def _sharpe(returns: pd.Series, periods_per_year: int) -> float:
    if len(returns) < 50 or returns.std(ddof=0) == 0:
        return 0.0
    return float((returns.mean() / returns.std(ddof=0)) * np.sqrt(periods_per_year))


@dataclass
class BacktestReport:
    pnl: float
    win_rate: float
    max_dd: float
    sharpe: float
    trades: int


def run_backtest(
    df15: pd.DataFrame,
    settings,
    risk: RiskManager,
    start_balance: float = 10000.0,
) -> BacktestReport:
    # resample
    def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        o = df["open"].resample(rule, label="right", closed="right").first()
        h = df["high"].resample(rule, label="right", closed="right").max()
        l = df["low"].resample(rule, label="right", closed="right").min()
        c = df["close"].resample(rule, label="right", closed="right").last()
        v = df["volume"].resample(rule, label="right", closed="right").sum()
        return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()

    df30 = resample(df15, "30min")
    df1h = resample(df15, "60min")

    cash = start_balance
    qty = 0.0
    entry = 0.0
    stop = 0.0
    tp = 0.0
    best = 0.0
    trailing = 0.0
    partial_done = False

    equity = []
    wins = 0
    exits = 0

    idxs = df15.index
    for i in range(300, len(df15)):
        t = idxs[i]
        w15 = df15.iloc[: i + 1]
        w30 = df30[df30.index <= t]
        w1h = df1h[df1h.index <= t]
        price = float(w15["close"].iloc[-1])

        # exits
        if qty > 0:
            if price > best:
                best = price
                trailing = risk.trailing_stop(best, atr_val)

            stop_level = min(stop, trailing) if settings.TRAILING_ENABLED else stop

            if (not partial_done) and price >= tp:
                q_part = qty * settings.PARTIAL_TP_PCT
                px = risk.apply_slippage(price, is_entry=False)
                fee = risk.fee_usd(q_part * px, taker=True)
                cash += q_part * px - fee
                if (px - entry) > 0:
                    wins += 1
                exits += 1
                qty -= q_part
                partial_done = True

            if price <= stop_level:
                px = risk.apply_slippage(price, is_entry=False)
                fee = risk.fee_usd(qty * px, taker=True)
                cash += qty * px - fee
                if (px - entry) > 0:
                    wins += 1
                exits += 1
                qty = 0.0
                partial_done = False

            if qty > 0 and partial_done and price >= tp:
                px = risk.apply_slippage(price, is_entry=False)
                fee = risk.fee_usd(qty * px, taker=True)
                cash += qty * px - fee
                if (px - entry) > 0:
                    wins += 1
                exits += 1
                qty = 0.0
                partial_done = False

        # entries
        if qty == 0:
            sig = compute_long_signal(
                w15,
                w30,
                w1h,
                settings.EMA_FAST,
                settings.EMA_SLOW,
                settings.RSI_PERIOD,
                settings.RSI_LONG_MIN,
                settings.ATR_PERIOD,
            )
            if sig and sig.action == "BUY":
                notional = cash * settings.POSITION_PCT
                px = risk.apply_slippage(price, is_entry=True)
                q = notional / max(px, 1e-12)
                fee = risk.fee_usd(notional, taker=True)
                cash -= notional + fee
                entry = px
                atr_val = sig.atr_value
                stop, tp = risk.stops_from_atr(entry, atr_val)
                best = entry
                trailing = stop
                qty = q
                partial_done = False

        equity.append(cash + qty * price)

    eq = pd.Series(equity, index=df15.index[300:])
    rets = eq.pct_change().fillna(0.0)
    pnl = float(eq.iloc[-1] - start_balance) if len(eq) else 0.0
    wr = (wins / exits) if exits > 0 else 0.0
    mdd = _max_drawdown(eq)
    sh = _sharpe(rets, periods_per_year=365 * 24 * 4)  # 15m bars
    return BacktestReport(pnl=pnl, win_rate=wr, max_dd=mdd, sharpe=sh, trades=exits)

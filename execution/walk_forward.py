from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class WalkForwardConfig:
    train_ratio: float = 0.6
    step_ratio: float = 0.1


def walk_forward_splits(df: pd.DataFrame, cfg: WalkForwardConfig) -> Iterable[tuple[pd.DataFrame, pd.DataFrame]]:
    n = len(df)
    train_n = int(n * cfg.train_ratio)
    step_n = max(1, int(n * cfg.step_ratio))

    start = 0
    while start + train_n + step_n <= n:
        train = df.iloc[start : start + train_n]
        test = df.iloc[start + train_n : start + train_n + step_n]
        yield train, test
        start += step_n

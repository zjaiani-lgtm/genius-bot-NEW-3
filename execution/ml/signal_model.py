from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sklearn.linear_model import LogisticRegression


@dataclass
class MLSignalFilter:
    enabled: bool = True
    min_proba: float = 0.55
    _model: Optional[LogisticRegression] = None

    def fit_placeholder(self, X: np.ndarray, y: np.ndarray) -> None:
        # optional: fit if you have labels; safe skeleton
        if not self.enabled:
            return
        if X.shape[0] < 200:
            return
        self._model = LogisticRegression(max_iter=200)
        self._model.fit(X, y)

    def predict_proba(self, features: np.ndarray) -> float:
        if not self.enabled:
            return 1.0
        if self._model is None:
            # default permissive but not blind
            return 0.60
        p = float(self._model.predict_proba(features.reshape(1, -1))[0, 1])
        return p

    def allow(self, features: np.ndarray) -> bool:
        return self.predict_proba(features) >= self.min_proba

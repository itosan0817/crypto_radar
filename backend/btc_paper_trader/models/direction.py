from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression


@dataclass
class DirectionModel:
    """Wraps logistic (+ optional Platt calibration)."""

    raw: Any
    feature_names: list[str]
    calibration: str | None

    def predict_proba_up(self, X: pd.DataFrame) -> np.ndarray:
        Xv = X[self.feature_names].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
        return self.raw.predict_proba(Xv)[:, 1]

    @classmethod
    def fit(cls, X: pd.DataFrame, y: pd.Series, calibration: str | None) -> DirectionModel:
        names = list(X.columns)
        Xv = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).values
        yv = y.values
        base = LogisticRegression(max_iter=4000, class_weight="balanced", random_state=42)
        if calibration == "platt":
            try:
                cv = min(3, max(2, len(y) // 500))
                model = CalibratedClassifierCV(base, method="sigmoid", cv=cv)
                model.fit(Xv, yv)
            except Exception:
                base.fit(Xv, yv)
                model = base
        else:
            base.fit(Xv, yv)
            model = base
        return cls(raw=model, feature_names=names, calibration=calibration)

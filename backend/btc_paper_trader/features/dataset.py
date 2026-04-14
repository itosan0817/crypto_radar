from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def feature_columns_from_config(cfg: dict[str, Any]) -> list[str]:
    lbs: dict[str, int] = cfg.get("regression", {}).get("lookback_bars", {})
    feats: list[str] = []
    for tf in lbs:
        feats.extend([f"{tf}_slope", f"{tf}_r2"])
    feats.extend(["m15_atr_ratio", "pattern_score"])
    return feats


def add_m15_atr_ratio(df: pd.DataFrame, atr_period: int) -> pd.DataFrame:
    from ..risk.tp_sl import atr_series

    out = df.copy()
    h, low, c = out["m15_high"], out["m15_low"], out["m15_close"]
    atr = atr_series(h, low, c, atr_period)
    out["m15_atr"] = atr
    out["m15_atr_ratio"] = atr / c.replace(0, np.nan)
    return out


def forward_labels(df: pd.DataFrame, forward_bars: int) -> pd.Series:
    lc = np.log(df["m15_close"].astype(float).clip(lower=1e-12))
    fwd = lc.shift(-forward_bars) - lc
    y = (fwd > 0).astype(int)
    return y


def build_training_matrix(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.Series]:
    feats = feature_columns_from_config(cfg)
    y = forward_labels(df, cfg["model"]["forward_bars"])
    mat = df[feats].copy().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    mask = y.notna()
    return mat.loc[mask], y.loc[mask]

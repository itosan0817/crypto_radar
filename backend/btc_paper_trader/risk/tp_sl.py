from __future__ import annotations

import numpy as np
import pandas as pd


def atr_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def tp_sl_prices(
    side: int,
    entry: float,
    atr: float,
    tp_mult: float,
    sl_mult: float,
) -> tuple[float, float]:
    """side: +1 long, -1 short. Returns (tp_price, sl_price)."""
    if side == 1:
        return entry + tp_mult * atr, entry - sl_mult * atr
    if side == -1:
        return entry - tp_mult * atr, entry + sl_mult * atr
    raise ValueError("side must be 1 or -1")

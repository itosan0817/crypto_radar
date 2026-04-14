from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    pnl: float
    win: bool


def equity_curve(pnls: list[float], initial: float) -> tuple[np.ndarray, float]:
    if not pnls:
        return np.array([initial]), 0.0
    bal = initial + np.cumsum(np.array(pnls, dtype=float))
    peak = np.maximum.accumulate(bal)
    dd = (bal - peak) / np.maximum(peak, 1e-12)
    max_dd = float(dd.min()) if len(bal) else 0.0
    return bal, max_dd


def _max_consecutive_losses(pnls: list[float]) -> int:
    run = 0
    best = 0
    for p in pnls:
        if p < 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def summarize_trades(
    pnls: list[float],
    initial_quote: float,
    timestamps: list[Any] | None = None,
) -> dict[str, Any]:
    if not pnls:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "calmar_like": 0.0,
            "avg_win": 0.0,
            "avg_loss_abs": 0.0,
            "payoff_ratio": 0.0,
            "max_consecutive_losses": 0,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = -sum(losses) if losses else 0.0
    pf = gross_win / gross_loss if gross_loss > 1e-12 else float("inf") if gross_win > 0 else 0.0
    eq, max_dd = equity_curve(pnls, initial_quote)
    total_ret = float(eq[-1] - initial_quote) / initial_quote
    # simple calmar-like: annualized not available without bar count; use return / |max_dd|
    calmar = total_ret / max(abs(max_dd), 1e-6) if max_dd != 0 else float("inf")
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss_abs = float(np.mean([-p for p in losses])) if losses else 0.0
    payoff = avg_win / avg_loss_abs if avg_loss_abs > 1e-12 else (float("inf") if avg_win > 0 else 0.0)
    return {
        "n_trades": len(pnls),
        "win_rate": len(wins) / len(pnls),
        "profit_factor": float(pf) if pf != float("inf") else 999.0,
        "expectancy": float(np.mean(pnls)),
        "total_pnl": float(sum(pnls)),
        "max_drawdown": float(max_dd),
        "calmar_like": float(calmar) if calmar != float("inf") else 999.0,
        "avg_win": avg_win,
        "avg_loss_abs": avg_loss_abs,
        "payoff_ratio": float(payoff) if payoff != float("inf") else 999.0,
        "max_consecutive_losses": _max_consecutive_losses(pnls),
    }


def regime_high_vol(atr_series: pd.Series, lookback: int = 200) -> pd.Series:
    """Boolean series: high vol if ATR > rolling median."""
    med = atr_series.rolling(lookback, min_periods=lookback // 2).median()
    return atr_series > med

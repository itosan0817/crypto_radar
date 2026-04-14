from __future__ import annotations

import numpy as np
import pandas as pd


def _rolling_linreg_slope_r2(log_close: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Optimized rolling OLS slope and R²."""
    n = len(log_close)
    slopes = np.full(n, np.nan)
    r2s = np.full(n, np.nan)
    
    if n < window:
        return slopes, r2s

    t = np.arange(window, dtype=float)
    t_mean = t.mean()
    t_diff = t - t_mean
    sst = (t_diff ** 2).sum()
    
    # Using sliding_window_view for Y values
    from numpy.lib.stride_tricks import sliding_window_view
    y_windows = sliding_window_view(log_close, window) # (n - window + 1, window)
    
    y_means = y_windows.mean(axis=1) # (n-window+1,)
    y_diffs = y_windows - y_means[:, np.newaxis]
    
    # b = sum((t - t_mean) * (y - y_mean)) / sst
    # This is a dot product of each row of y_diffs with t_diff
    slopes_valid = np.dot(y_diffs, t_diff) / sst
    
    # r2 calculation
    # ss_res = sum((y - (a + b*t))**2) = sum((y - (y_mean + b*(t-t_mean)))**2)
    #        = sum((y_diff - b*t_diff)**2)
    y_pred_diffs = slopes_valid[:, np.newaxis] * t_diff
    ss_res = ((y_diffs - y_pred_diffs) ** 2).sum(axis=1)
    ss_tot = (y_diffs ** 2).sum(axis=1)
    
    with np.errstate(divide='ignore', invalid='ignore'):
        r2s_valid = np.where(ss_tot > 1e-18, 1.0 - ss_res / ss_tot, np.nan)
    
    slopes[window-1:] = slopes_valid
    r2s[window-1:] = r2s_valid
    
    return slopes, r2s


def add_regression_features(
    df: pd.DataFrame,
    lookback: dict[str, int],
) -> pd.DataFrame:
    """
    For each TF prefix in lookback (e.g. 'm15', '1h'), add `{tf}_slope`, `{tf}_r2`
    from rolling linear regression on log(close).
    Expects columns `{tf}_close`.
    """
    out = df.copy()
    for tf, win in lookback.items():
        col = f"{tf}_close"
        if col not in out.columns:
            continue
        lc = np.log(out[col].astype(float).clip(lower=1e-12).values)
        slopes, r2s = _rolling_linreg_slope_r2(lc, win)
        out[f"{tf}_slope"] = slopes
        out[f"{tf}_r2"] = r2s
    return out

from __future__ import annotations

import numpy as np
import pandas as pd


def pattern_scores(
    m15_close: pd.Series,
    window: int,
    horizon: int,
    top_k: int,
    min_similarity: float,
) -> pd.Series:
    """
    Vectorized version: Uses matrix operations to find top-K similar past patterns.
    """
    close = m15_close.astype(float).values
    # Log returns
    ret = np.diff(np.log(np.maximum(close, 1e-12)), prepend=np.nan)
    n = len(ret)
    out = np.zeros(n, dtype=float)
    out[:] = np.nan

    if n < window + horizon + 2:
        return pd.Series(out, index=m15_close.index)

    # Use sliding_window_view to get all windows of size 'window'
    # shape: (n - window + 1, window)
    from numpy.lib.stride_tricks import sliding_window_view
    
    # We only care about windows that have no NaNs (indices >= 1)
    # The return at index 0 is NaN. So windows starting at index 1 or later.
    valid_ret = ret[1:]
    if len(valid_ret) < window:
        return pd.Series(out, index=m15_close.index)
        
    all_windows = sliding_window_view(valid_ret, window) # (num_windows, window)
    
    # Normalize each window: (x - mean) / std
    means = all_windows.mean(axis=1, keepdims=True)
    stds = all_windows.std(axis=1, keepdims=True) + 1e-12
    norm_windows = (all_windows - means) / stds
    
    # norm_windows[k] is the normalized window ending at actual index k + window
    # actual index in 'ret' is k + window.
    
    # For each i (target window ending at i-1):
    # j ranges from window to i - horizon - 1
    # We can pre-calculate the signs of forward returns
    fwd_returns = np.zeros(n)
    for j in range(1, n - horizon):
        fwd_returns[j] = np.sign(np.nansum(ret[j + 1 : j + 1 + horizon]))

    for i in range(window + horizon + 1, n):
        # target window ends at i-1, target index in norm_windows is (i-1) - window
        target_idx = i - 1 - window
        if target_idx < 0: continue
        z = norm_windows[target_idx]
        
        # Search range for j: window to i - horizon - 1
        # indices in norm_windows for j: j - window
        # j_min = window -> idx = 0
        # j_max = i - horizon - 1 -> idx = (i - horizon - 1) - window
        max_j_idx = i - horizon - 1 - window
        if max_j_idx < 0:
            out[i] = 0.0
            continue
            
        search_windows = norm_windows[:max_j_idx + 1]
        # Cosine similarity is dot product of normalized vectors divided by their norms (which are sqrt(window) here)
        # Actually, since we Z-scored them, dot(z, pz) / (norm(z)*norm(pz))
        # norms are sqrt(window) for Z-scored vectors
        dot_probs = np.dot(search_windows, z)
        norms_prod = np.linalg.norm(z) * np.linalg.norm(search_windows, axis=1) + 1e-12
        sims = dot_probs / norms_prod
        
        # Filter by min_similarity
        mask = sims >= min_similarity
        if not np.any(mask):
            out[i] = 0.0
            continue
            
        valid_sims = sims[mask]
        # map back to j indices to get signs
        # search_windows[k] corresponds to j = k + window
        valid_signs = fwd_returns[np.where(mask)[0] + window]
        
        if len(valid_sims) < 3:
            out[i] = 0.0
            continue
            
        # Get top K
        if len(valid_sims) > top_k:
            top_indices = np.argpartition(valid_sims, -top_k)[-top_k:]
            votes = valid_signs[top_indices]
        else:
            votes = valid_signs
            
        out[i] = float(np.mean(votes))

    return pd.Series(out, index=m15_close.index)

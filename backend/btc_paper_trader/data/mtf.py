from __future__ import annotations

import pandas as pd


def _prep_htf(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = df.rename(
        columns={
            "open": f"{prefix}_open",
            "high": f"{prefix}_high",
            "low": f"{prefix}_low",
            "close": f"{prefix}_close",
            "volume": f"{prefix}_volume",
            "open_time": f"{prefix}_open_time",
            "close_time": f"{prefix}_close_time",
        }
    )
    return out


def merge_asof_left(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_on: str,
    right_on: str,
) -> pd.DataFrame:
    """Attach latest fully closed right row as-of each left timestamp."""
    lv = left.sort_values(left_on)
    rv = right.sort_values(right_on)
    merged = pd.merge_asof(
        lv,
        rv,
        left_on=left_on,
        right_on=right_on,
        direction="backward",
    )
    return merged.reset_index(drop=True)


def build_mtf_frame(
    m15: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    Align higher TF OHLCV to 15m bars using as-of on **close_time** of HTF candles.

    `m15` must include `close_time`. Each HTF frame must include `close_time`.
    For each 15m row at time T, we use the last HTF candle with close_time <= T (typically
    the last fully closed higher-TF bar at that moment).
    """
    base = m15.copy()
    base = base.sort_values("close_time").reset_index(drop=True)
    if "close_time" not in base.columns:
        raise ValueError("m15 must have close_time")

    out = base.rename(
        columns={
            "open": "m15_open",
            "high": "m15_high",
            "low": "m15_low",
            "close": "m15_close",
            "volume": "m15_volume",
            "open_time": "m15_open_time",
            "close_time": "m15_close_time",
        }
    )

    for name, df in frames.items():
        if name == "15m":
            continue
        h = df.sort_values("close_time").reset_index(drop=True)
        h2 = _prep_htf(h, name)
        out = merge_asof_left(out, h2, left_on="m15_close_time", right_on=f"{name}_close_time")

    return out.reset_index(drop=True)

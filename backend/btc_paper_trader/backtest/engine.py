from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from ..config import package_root
from ..data.binance_futures import (
    INTERVAL_MS,
    fetch_funding_rates_range,
    fetch_klines_range,
    load_from_sqlite,
    upsert_sqlite,
)
from ..data.mtf import build_mtf_frame
from ..features.dataset import add_m15_atr_ratio, feature_columns_from_config
from ..features.pattern_knn import pattern_scores
from ..features.regression_mtf import add_regression_features
from ..models.direction import DirectionModel
from ..risk.tp_sl import tp_sl_prices
from ..signal.pipeline import gate_signal_with_reason, signal_config_from_dict


_NEWS_CACHE: dict[str, Any] = {"fetched_at_ms": 0, "events": []}


def _parse_iso_to_ms(value: str) -> int | None:
    try:
        ts = pd.to_datetime(value, utc=True)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return int(ts.timestamp() * 1000)


def _fetch_live_news_events(cfg: dict[str, Any]) -> list[tuple[int, int]]:
    nf = cfg.get("news_filter") or {}
    sync = nf.get("live_sync") or {}
    if not bool(sync.get("enabled", False)):
        return []
    url = str(sync.get("calendar_url", "")).strip()
    if not url:
        return []

    timeout_sec = float(sync.get("request_timeout_seconds", 8))
    try:
        r = requests.get(url, timeout=timeout_sec)
        r.raise_for_status()
        raw = r.json()
    except Exception:
        return []

    if not isinstance(raw, list):
        return []

    allowed_impacts = {str(x).lower() for x in sync.get("impacts", ["High"])}
    allowed_countries = {str(x).upper() for x in sync.get("countries", ["USD"])}
    block_before = int(sync.get("block_before_min", 30))
    block_after = int(sync.get("block_after_min", 60))

    windows: list[tuple[int, int]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        impact = str(e.get("impact", "")).lower()
        country = str(e.get("country", "")).upper()
        if allowed_impacts and impact not in allowed_impacts:
            continue
        if allowed_countries and country not in allowed_countries:
            continue
        event_ms = _parse_iso_to_ms(str(e.get("date", "")))
        if event_ms is None:
            continue
        windows.append((event_ms - block_before * 60_000, event_ms + block_after * 60_000))
    return windows


def _get_live_news_windows_cached(close_time_ms: int, cfg: dict[str, Any]) -> list[tuple[int, int]]:
    nf = cfg.get("news_filter") or {}
    sync = nf.get("live_sync") or {}
    if not bool(sync.get("enabled", False)):
        return []

    # Backtestなどの過去バー評価では外部同期を使わない
    max_age_h = float(sync.get("max_bar_age_hours_for_sync", 72))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if abs(now_ms - int(close_time_ms)) > int(max_age_h * 3600 * 1000):
        return []

    refresh_ms = int(float(sync.get("refresh_seconds", 900)) * 1000)
    fetched_at = int(_NEWS_CACHE.get("fetched_at_ms", 0) or 0)
    if fetched_at <= 0 or (now_ms - fetched_at) >= refresh_ms:
        _NEWS_CACHE["events"] = _fetch_live_news_events(cfg)
        _NEWS_CACHE["fetched_at_ms"] = now_ms
    events = _NEWS_CACHE.get("events", [])
    return events if isinstance(events, list) else []


def _in_static_news_block_window(close_time_ms: int, cfg: dict[str, Any]) -> bool:
    nf = cfg.get("news_filter") or {}
    windows = nf.get("windows_utc") or []
    if not isinstance(windows, list) or not windows:
        return False

    dt = datetime.fromtimestamp(close_time_ms / 1000.0, tz=timezone.utc)
    now_min = dt.hour * 60 + dt.minute
    for w in windows:
        if not isinstance(w, dict):
            continue
        wd = w.get("weekday_utc")
        if wd is not None and int(wd) != dt.weekday():
            continue
        hh = int(w.get("hour_utc", 0))
        mm = int(w.get("minute_utc", 0))
        center = hh * 60 + mm
        before = int(w.get("block_before_min", 0))
        after = int(w.get("block_after_min", 0))
        lo = center - before
        hi = center + after
        if lo <= now_min <= hi:
            return True
    return False


def _in_news_block_window(close_time_ms: int, cfg: dict[str, Any]) -> bool:
    nf = cfg.get("news_filter") or {}
    if not bool(nf.get("enabled", False)):
        return False

    live_windows = _get_live_news_windows_cached(close_time_ms, cfg)
    if live_windows:
        c = int(close_time_ms)
        for lo_ms, hi_ms in live_windows:
            if int(lo_ms) <= c <= int(hi_ms):
                return True
        return False

    # 同期失敗時は固定ウィンドウにフォールバック
    return _in_static_news_block_window(close_time_ms, cfg)


def _is_range_breakout(atr_hist: pd.Series | None, row: pd.Series, cfg: dict[str, Any]) -> bool:
    rg = (cfg.get("regime") or {}).get("range_breakout_guard") or {}
    if not bool(rg.get("enabled", False)):
        return False
    if atr_hist is None or len(atr_hist) < int(rg.get("min_hist_bars", 40)):
        return False
    current = float(row.get("m15_atr_ratio", 0.0) or 0.0)
    if not np.isfinite(current) or current <= 0:
        return False
    baseline = float(atr_hist.quantile(float(rg.get("baseline_quantile", 0.5))))
    if baseline <= 0:
        return False
    mult = float(rg.get("spike_multiple", 1.8))
    min_abs = float(rg.get("min_abs_atr_ratio", 0.003))
    return current >= max(min_abs, baseline * mult)


def _is_range_regime(row: pd.Series, cfg: dict[str, Any]) -> bool:
    rc = cfg.get("regime") or {}
    if not bool(rc.get("enabled", False)):
        return False
    s1h = float(row.get("1h_slope", 0.0) or 0.0)
    s4h = float(row.get("4h_slope", 0.0) or 0.0)
    atr_ratio = float(row.get("m15_atr_ratio", 0.0) or 0.0)
    return (
        abs(s1h) <= float(rc.get("max_abs_slope_1h", 0.0015))
        and abs(s4h) <= float(rc.get("max_abs_slope_4h", 0.0010))
        and atr_ratio <= float(rc.get("max_atr_ratio", 0.004))
    )


def _grid_signal_with_reason(row: pd.Series, cfg: dict[str, Any]) -> tuple[int, str]:
    """
    Single-position "grid-like" entry:
    - Long when price is below center by step*ATR
    - Short when price is above center by step*ATR
    """
    gc = (cfg.get("grid") or {})
    if not bool(gc.get("enabled", False)):
        return 0, "grid_disabled"
    close = float(row.get("m15_close", np.nan))
    center = float(row.get("m15_grid_center", np.nan))
    atr = float(row.get("m15_atr", np.nan))
    if np.isnan(close) or np.isnan(center) or np.isnan(atr) or atr <= 0:
        return 0, "grid_data_unavailable"
    step_mult = float(gc.get("step_atr_mult", 0.6))
    deadband_mult = float(gc.get("deadband_atr_mult", 0.2))
    z = (close - center) / atr
    if z >= step_mult:
        return -1, "emit_short_grid"
    if z <= -step_mult:
        return 1, "emit_long_grid"
    if abs(z) <= deadband_mult:
        return 0, "grid_center_deadband"
    return 0, "grid_wait_band"


def _entry_timing_confirm(side: int, regime: str, row: pd.Series, cfg: dict[str, Any]) -> tuple[bool, str]:
    et = cfg.get("entry_timing_1m") or {}
    if not bool(et.get("enabled", False)):
        return True, "entry_timing_disabled"

    # Default behavior: skip 1m confirmation in range regime unless explicitly required.
    if regime == "range" and not bool(et.get("require_range_confirmation", False)):
        return True, "entry_timing_skipped_regime"

    only = et.get("only_regimes")
    if isinstance(only, list) and only and regime not in only:
        return True, "entry_timing_skipped_regime"

    close = float(row.get("1m_close", np.nan))
    ema_fast = float(row.get("1m_ema_fast", np.nan))
    ema_slow = float(row.get("1m_ema_slow", np.nan))
    recent_high = float(row.get("1m_recent_high", np.nan))
    recent_low = float(row.get("1m_recent_low", np.nan))
    rsi = float(row.get("1m_rsi", np.nan))

    if any(np.isnan(v) for v in (close, ema_fast, ema_slow)):
        return False, "entry_timing_1m_data_unavailable"

    trend = et.get("trend") or {}
    range_cfg = et.get("range") or {}
    breakout_eps = float(trend.get("breakout_buffer_bps", 1.0)) * 1e-4
    overbought = float(range_cfg.get("rsi_overbought", 60))
    oversold = float(range_cfg.get("rsi_oversold", 40))

    if regime == "range":
        if side > 0:
            if not np.isnan(rsi) and rsi <= oversold and close >= ema_fast:
                return True, "entry_timing_1m_range_long_confirmed"
            return False, "entry_timing_1m_range_long_block"
        if side < 0:
            if not np.isnan(rsi) and rsi >= overbought and close <= ema_fast:
                return True, "entry_timing_1m_range_short_confirmed"
            return False, "entry_timing_1m_range_short_block"
        return False, "entry_timing_1m_no_side"

    if side > 0:
        breakout_ok = np.isnan(recent_high) or close >= recent_high * (1.0 - breakout_eps)
        if close >= ema_fast >= ema_slow and breakout_ok:
            return True, "entry_timing_1m_trend_long_confirmed"
        return False, "entry_timing_1m_trend_long_block"
    if side < 0:
        breakout_ok = np.isnan(recent_low) or close <= recent_low * (1.0 + breakout_eps)
        if close <= ema_fast <= ema_slow and breakout_ok:
            return True, "entry_timing_1m_trend_short_confirmed"
        return False, "entry_timing_1m_trend_short_block"
    return False, "entry_timing_1m_no_side"


def _tier_position_and_risk(conf: float, cfg: dict[str, Any]) -> tuple[float, float, float, int]:
    """Returns (position_fraction, tp_atr_mult, sl_atr_mult, max_hold_bars) for this confidence."""
    rcfg = cfg["risk"]
    ct = rcfg.get("confidence_tier") or {}
    base_pf = float(rcfg["position_fraction"])
    base_tp = float(rcfg["tp_atr_mult"])
    base_sl = float(rcfg["sl_atr_mult"])
    base_mh = int(rcfg["max_hold_bars"])
    if not ct.get("enabled"):
        return base_pf, base_tp, base_sl, base_mh
    thresholds = ct.get("thresholds") or []
    pfs = ct.get("position_fraction") or []
    tps = ct.get("tp_atr_mult_scale") or []
    sls = ct.get("sl_atr_mult_scale") or []
    hs = ct.get("max_hold_bars_scale") or []
    for i, upper in enumerate(thresholds):
        if conf <= float(upper) and i < len(pfs):
            pf = float(pfs[i])
            tp_m = base_tp * float(tps[i] if i < len(tps) else 1.0)
            sl_m = base_sl * float(sls[i] if i < len(sls) else 1.0)
            mh = max(1, int(round(base_mh * float(hs[i] if i < len(hs) else 1.0))))
            return pf, tp_m, sl_m, mh
    if thresholds and pfs:
        i = len(pfs) - 1
        return (
            float(pfs[i]),
            base_tp * float(tps[i] if i < len(tps) else 1.0),
            base_sl * float(sls[i] if i < len(sls) else 1.0),
            max(1, int(round(base_mh * float(hs[i] if i < len(hs) else 1.0)))),
        )
    return base_pf, base_tp, base_sl, base_mh


def _utc_day_key(close_time_ms: int) -> str:
    return datetime.fromtimestamp(close_time_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class SimState:
    quote: float
    side: int = 0
    entry_px: float = 0.0
    qty: float = 0.0
    tp: float = 0.0
    sl: float = 0.0
    entry_i: int = 0
    pending: int = 0
    pending_confidence: float = 0.0
    pending_regime: str = "trend"
    entry_max_hold_bars: int = 0
    entry_atr: float = 0.0
    partial_tp_done: bool = False
    breakeven_done: bool = False
    consecutive_losses: int = 0
    cooldown_first_allowed_i: int = 0
    halt_new_entries: bool = False
    day_utc: str = ""
    quote_at_day_start: float = 0.0
    daily_pnl: float = 0.0


def prepare_frame(cfg: dict[str, Any], db_path: Path | None = None) -> pd.DataFrame:
    """Load or fetch klines, build MTF frame, features."""
    sym = cfg["symbol"]
    base = cfg["base_url"]
    cache = package_root() / cfg["data"]["cache_sqlite"]
    if db_path:
        cache = db_path

    feats_iv = cfg["intervals"]["features"]
    bars = {"1m": 12000, "15m": 20000, "1h": 3000, "4h": 1800, "1d": 1200}
    dfs: dict[str, pd.DataFrame] = {}
    fetch_intervals = list(dict.fromkeys(list(feats_iv) + (["1m"] if (cfg.get("entry_timing_1m") or {}).get("enabled", False) else [])))
    for iv in fetch_intervals:
        target_nb = bars.get(iv, 1000)
        df_disk = load_from_sqlite(cache, sym, iv)
        now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
        step_ms = INTERVAL_MS[iv]
        if df_disk.empty:
            # 初回は広めに取得
            start_ms = now_ms - target_nb * step_ms
        else:
            # 通常時は増分更新（直近1本重ねて取得）
            latest_open = int(df_disk["open_time"].iloc[-1])
            start_ms = max(0, latest_open - step_ms)

        if now_ms > start_ms:
            new_df = fetch_klines_range(base, sym, iv, start_ms, now_ms)
            if not new_df.empty:
                if df_disk.empty:
                    df_disk = new_df
                else:
                    df_disk = pd.concat([df_disk, new_df], ignore_index=True)
                df_disk = (
                    df_disk.drop_duplicates(subset=["open_time"], keep="last")
                    .sort_values("open_time")
                    .tail(target_nb + 500)
                    .reset_index(drop=True)
                )
                upsert_sqlite(df_disk, cache, sym, iv)

        dfs[iv] = df_disk

    if "1m" in dfs and not dfs["1m"].empty:
        one = dfs["1m"].copy().sort_values("close_time").reset_index(drop=True)
        one["1m_ema_fast"] = one["close"].ewm(span=int((cfg.get("entry_timing_1m") or {}).get("ema_fast", 9)), adjust=False).mean()
        one["1m_ema_slow"] = one["close"].ewm(span=int((cfg.get("entry_timing_1m") or {}).get("ema_slow", 21)), adjust=False).mean()
        rsi_len = int((cfg.get("entry_timing_1m") or {}).get("rsi_period", 14))
        delta = one["close"].diff()
        gain = delta.clip(lower=0.0).rolling(rsi_len, min_periods=rsi_len).mean()
        loss = (-delta.clip(upper=0.0)).rolling(rsi_len, min_periods=rsi_len).mean()
        rs = gain / loss.replace(0, np.nan)
        one["1m_rsi"] = 100.0 - (100.0 / (1.0 + rs))
        breakout_n = int((cfg.get("entry_timing_1m") or {}).get("breakout_lookback_bars", 5))
        one["1m_recent_high"] = one["high"].rolling(breakout_n, min_periods=1).max().shift(1)
        one["1m_recent_low"] = one["low"].rolling(breakout_n, min_periods=1).min().shift(1)
        dfs["1m"] = one

    m15 = dfs["15m"].copy()
    merged = build_mtf_frame(m15, dfs)
    lb = cfg["regression"]["lookback_bars"]
    merged = add_regression_features(merged, lb)
    pat = pattern_scores(
        merged["m15_close"],
        window=int(cfg["pattern"]["window"]),
        horizon=int(cfg["pattern"]["horizon_bars"]),
        top_k=int(cfg["pattern"]["top_k"]),
        min_similarity=float(cfg["pattern"]["min_similarity"]),
    )
    merged["pattern_score"] = pat.values
    merged["pattern_score"] = merged["pattern_score"].fillna(0.0)

    # Funding rate (8h) を 15m バーへ asof 結合。取得失敗時は無効化（0.0）で継続。
    funding_start = max(0, int(merged["m15_open_time"].iloc[0]) - INTERVAL_MS["1d"])
    funding_end = int(merged["m15_close_time"].iloc[-1]) + 1
    try:
        fdf = fetch_funding_rates_range(base, sym, funding_start, funding_end)
        if not fdf.empty:
            merged = pd.merge_asof(
                merged.sort_values("m15_close_time"),
                fdf.sort_values("funding_time"),
                left_on="m15_close_time",
                right_on="funding_time",
                direction="backward",
            ).sort_values("m15_close_time")
            merged["funding_rate"] = merged["funding_rate"].ffill().fillna(0.0)
        else:
            merged["funding_rate"] = 0.0
    except Exception:
        merged["funding_rate"] = 0.0

    ap = int(cfg["filters"]["atr_period"])
    merged = add_m15_atr_ratio(merged, ap)
    grid_window = int((cfg.get("grid") or {}).get("center_ema_window", 48))
    merged["m15_grid_center"] = merged["m15_close"].ewm(span=max(2, grid_window), adjust=False).mean()
    merged = merged.dropna(subset=["m15_close"]).reset_index(drop=True)
    return merged


def step_simulation(
    df: pd.DataFrame,
    model: DirectionModel,
    cfg: dict[str, Any],
    state: SimState,
    i: int,
    atr_series_full: pd.Series | None = None,
) -> tuple[SimState, list[dict[str, Any]]]:
    """Advance one bar index `i` (signal at i -> entry next open in same call chain as backtest loop)."""
    i = int(i)
    scfg = signal_config_from_dict(cfg)
    rcfg = cfg["risk"]
    max_daily_loss_pct = float(rcfg.get("max_daily_loss_pct", 0.05))
    cd_losses = int(rcfg.get("cooldown_after_losses", 3))
    cd_bars = int(rcfg.get("cooldown_bars", 8))
    fee = float(cfg["filters"]["taker_fee_rate"])
    slip = float(cfg["filters"]["slippage_bps"]) * 1e-4
    max_hold_base = int(rcfg["max_hold_bars"])
    trail_be = float(rcfg.get("trail_breakeven_atr_mult", 0.0) or 0.0)
    partial_tp_mult = float(rcfg.get("partial_take_profit_atr_mult", 0.0) or 0.0)
    partial_tp_fraction = float(rcfg.get("partial_take_profit_fraction", 0.5) or 0.5)
    partial_tp_fraction = float(np.clip(partial_tp_fraction, 0.0, 1.0))
    partial_tp_move_be = bool(rcfg.get("partial_take_profit_move_sl_to_be", True))
    trail_after_partial = float(rcfg.get("trail_after_partial_atr_mult", 0.0) or 0.0)
    feats = feature_columns_from_config(cfg)

    events: list[dict[str, Any]] = []
    quote = state.quote
    side = state.side
    entry_px = state.entry_px
    qty = state.qty
    tp = state.tp
    sl = state.sl
    entry_i = state.entry_i
    pending = state.pending
    pending_confidence = state.pending_confidence
    pending_regime = state.pending_regime
    entry_max_hold_bars = state.entry_max_hold_bars
    entry_atr = state.entry_atr
    partial_tp_done = state.partial_tp_done
    breakeven_done = state.breakeven_done
    consecutive_losses = state.consecutive_losses
    cooldown_first_allowed_i = state.cooldown_first_allowed_i
    halt_new_entries = state.halt_new_entries
    day_utc = state.day_utc
    quote_at_day_start = state.quote_at_day_start
    daily_pnl = state.daily_pnl
    signal_reason = "hold_position"
    signal_value = 0
    regime = "trend"
    close_ms = int(df["m15_close_time"].iloc[i])
    day_key = _utc_day_key(close_ms)
    if day_utc and day_key != day_utc:
        daily_pnl = 0.0
        quote_at_day_start = quote
        halt_new_entries = False
    if not day_utc:
        quote_at_day_start = quote
    day_utc = day_key

    row = df.iloc[i]
    atr_all = df["m15_atr"].values
    atr = float(atr_all[i]) if not np.isnan(atr_all[i]) else 0.0
    atr_hist = df["m15_atr"].iloc[max(0, i - 500) : i] if atr_series_full is None else atr_series_full
    o, h, low, c = (
        float(df["m15_open"].iloc[i]),
        float(df["m15_high"].iloc[i]),
        float(df["m15_low"].iloc[i]),
        float(df["m15_close"].iloc[i]),
    )

    if side == 0 and pending != 0:
        ok_entry, entry_reason = _entry_timing_confirm(int(pending), pending_regime, row, cfg)
        if not ok_entry:
            pending = 0
            pending_confidence = 0.0
            pending_regime = "trend"
            events.append(
                {
                    "type": "decision",
                    "bar": i,
                    "signal": 0,
                    "reason": entry_reason,
                    "pending_after_guard": 0,
                    "regime": pending_regime,
                }
            )
        else:
            fill = o * (1.0 + slip * pending)
            pos_frac, tp_m, sl_m, mh_trade = _tier_position_and_risk(pending_confidence, cfg)
            notional = quote * pos_frac
            qty = notional / fill
            entry_px = fill
            tp, sl = tp_sl_prices(pending, fill, atr, tp_m, sl_m)
            side = pending
            entry_i = i
            entry_max_hold_bars = mh_trade
            entry_atr = max(atr, 0.0)
            partial_tp_done = False
            breakeven_done = False
            pending = 0
            pending_confidence = 0.0
            pending_regime = "trend"

    max_hold = entry_max_hold_bars if entry_max_hold_bars > 0 else max_hold_base

    if side != 0 and not partial_tp_done and partial_tp_mult > 0 and qty > 0 and entry_atr > 0:
        partial_px = entry_px + float(side) * partial_tp_mult * entry_atr
        hit_partial = (side == 1 and h >= partial_px) or (side == -1 and low <= partial_px)
        if hit_partial:
            close_qty = qty * partial_tp_fraction
            if close_qty > 0:
                if side == 1:
                    gross_partial = (partial_px - entry_px) * close_qty
                else:
                    gross_partial = (entry_px - partial_px) * close_qty
                cost_partial = fee * close_qty * (entry_px + partial_px) + slip * close_qty * (entry_px + partial_px)
                pnl_partial = gross_partial - cost_partial
                quote += pnl_partial
                daily_pnl += pnl_partial
                qty -= close_qty
                partial_tp_done = True
                if partial_tp_move_be:
                    if side == 1:
                        sl = max(sl, entry_px)
                    else:
                        sl = min(sl, entry_px)
                events.append(
                    {
                        "type": "partial_exit",
                        "bar": i,
                        "reason": "partial_tp",
                        "side": side,
                        "pnl": float(pnl_partial),
                        "price": float(partial_px),
                        "qty_closed": float(close_qty),
                        "qty_remaining": float(max(qty, 0.0)),
                    }
                )
                if qty <= 1e-12:
                    side = 0
                    qty = 0.0
                    entry_max_hold_bars = 0
                    entry_atr = 0.0
                    partial_tp_done = False
                    breakeven_done = False

    if side != 0 and partial_tp_done and trail_after_partial > 0 and atr > 0:
        if side == 1:
            sl = max(sl, c - trail_after_partial * atr)
        else:
            sl = min(sl, c + trail_after_partial * atr)

    if side != 0 and trail_be > 0 and atr > 0 and not breakeven_done:
        act = trail_be * atr
        if side == 1 and h >= entry_px + act:
            sl = max(sl, entry_px)
            breakeven_done = True
        elif side == -1 and low <= entry_px - act:
            sl = min(sl, entry_px)
            breakeven_done = True

    if side != 0:
        exit_px = None
        reason = ""
        if side == 1:
            if low <= sl:
                exit_px = sl
                reason = "sl"
            elif h >= tp:
                exit_px = tp
                reason = "tp"
        else:
            if h >= sl:
                exit_px = sl
                reason = "sl"
            elif low <= tp:
                exit_px = tp
                reason = "tp"
        if exit_px is None and i - entry_i >= max_hold:
            exit_px = c
            reason = "time"
        if exit_px is not None:
            if side == 1:
                gross = (exit_px - entry_px) * qty
            else:
                gross = (entry_px - exit_px) * qty
            cost = fee * qty * (entry_px + exit_px) + slip * qty * (entry_px + exit_px)
            pnl = gross - cost
            quote += pnl
            daily_pnl += pnl
            if pnl < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0
            if (
                quote_at_day_start > 0
                and daily_pnl <= -max_daily_loss_pct * quote_at_day_start
            ):
                halt_new_entries = True
            if consecutive_losses >= cd_losses:
                cooldown_first_allowed_i = i + 1 + cd_bars
                consecutive_losses = 0
            events.append({"pnl": float(pnl), "reason": reason, "side": side, "bar": i})
            side = 0
            qty = 0.0
            entry_max_hold_bars = 0
            entry_atr = 0.0
            partial_tp_done = False
            breakeven_done = False

    if side == 0:
        if _in_news_block_window(close_ms, cfg):
            signal_value = 0
            signal_reason = "news_event_block"
            sig_conf = 0.0
            pending = 0
            pending_confidence = 0.0
            new_state = SimState(
                quote=quote,
                side=side,
                entry_px=entry_px,
                qty=qty,
                tp=tp,
                sl=sl,
                entry_i=entry_i,
                pending=pending,
                pending_confidence=pending_confidence,
                entry_max_hold_bars=entry_max_hold_bars,
                entry_atr=entry_atr,
                partial_tp_done=partial_tp_done,
                breakeven_done=breakeven_done,
                consecutive_losses=consecutive_losses,
                cooldown_first_allowed_i=cooldown_first_allowed_i,
                halt_new_entries=halt_new_entries,
                day_utc=day_utc,
                quote_at_day_start=quote_at_day_start,
                daily_pnl=daily_pnl,
            )
            events.append(
                {
                    "type": "decision",
                    "bar": i,
                    "signal": 0,
                    "reason": signal_reason,
                    "pending_after_guard": 0,
                    "regime": regime,
                }
            )
            return new_state, events

        X = df.iloc[[i]][feats]
        try:
            p_up = float(model.predict_proba_up(X)[0])
        except Exception:
            p_up = 0.5
        pat = float(row.get("pattern_score", 0.0) or 0.0)
        if np.isnan(pat):
            pat = 0.0
        if _is_range_regime(row, cfg):
            regime = "range"
            if _is_range_breakout(atr_hist, row, cfg):
                signal_value = 0
                signal_reason = "range_breakout_guard"
                sig_conf = 0.0
            else:
                signal_value, signal_reason = _grid_signal_with_reason(row, cfg)
                sig_conf = 1.0 if signal_value != 0 else 0.0
        else:
            signal_value, signal_reason, sig_conf = gate_signal_with_reason(row, p_up, pat, atr, atr_hist, scfg)
        pending = signal_value
        pending_confidence = float(sig_conf) if signal_value != 0 else 0.0
        pending_regime = regime if signal_value != 0 else "trend"
        if halt_new_entries or i < cooldown_first_allowed_i:
            signal_reason = "risk_guard_block"
            pending = 0
            pending_confidence = 0.0
            pending_regime = "trend"
    else:
        pending = 0
        pending_confidence = 0.0
        signal_reason = "position_open"
        pending_regime = "trend"

    new_state = SimState(
        quote=quote,
        side=side,
        entry_px=entry_px,
        qty=qty,
        tp=tp,
        sl=sl,
        entry_i=entry_i,
        pending=pending,
        pending_confidence=pending_confidence,
        pending_regime=pending_regime,
        entry_max_hold_bars=entry_max_hold_bars,
        entry_atr=entry_atr,
        partial_tp_done=partial_tp_done,
        breakeven_done=breakeven_done,
        consecutive_losses=consecutive_losses,
        cooldown_first_allowed_i=cooldown_first_allowed_i,
        halt_new_entries=halt_new_entries,
        day_utc=day_utc,
        quote_at_day_start=quote_at_day_start,
        daily_pnl=daily_pnl,
    )
    events.append(
        {
            "type": "decision",
            "bar": i,
            "signal": int(signal_value),
            "reason": signal_reason,
            "pending_after_guard": int(pending),
            "regime": regime,
        }
    )
    return new_state, events


def run_backtest(
    df: pd.DataFrame,
    model: DirectionModel,
    cfg: dict[str, Any],
    i0: int,
    i1: int,
    atr_series_full: pd.Series | None = None,
) -> tuple[list[float], list[dict[str, Any]]]:
    q0 = float(cfg["backtest"]["initial_quote"])
    state = SimState(quote=q0, quote_at_day_start=q0)
    pnls: list[float] = []
    trades: list[dict[str, Any]] = []
    n = len(df)
    i_start = max(i0 + 1, 200)
    i_end = min(i1, n - 2)
    for i in range(i_start, i_end):
        state, ev = step_simulation(df, model, cfg, state, i, atr_series_full)
        for e in ev:
            if "pnl" in e:
                pnls.append(float(e["pnl"]))
                trades.append(e)
    return pnls, trades


def train_model_slice(df: pd.DataFrame, cfg: dict[str, Any], i0: int, i1: int) -> DirectionModel:
    from ..features.dataset import build_training_matrix

    sub = df.iloc[i0:i1].copy()
    X, y = build_training_matrix(sub, cfg)
    min_s = int(cfg["model"]["train_min_samples"])
    if len(X) < min_s:
        raise ValueError(f"not enough training samples: {len(X)} < {min_s}")
    cal = cfg["model"].get("calibration")
    return DirectionModel.fit(X, y, calibration=cal)

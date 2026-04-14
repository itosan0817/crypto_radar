from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import package_root
from ..data.binance_futures import INTERVAL_MS, fetch_klines_range, load_from_sqlite, upsert_sqlite
from ..data.mtf import build_mtf_frame
from ..features.dataset import add_m15_atr_ratio, feature_columns_from_config
from ..features.pattern_knn import pattern_scores
from ..features.regression_mtf import add_regression_features
from ..models.direction import DirectionModel
from ..risk.tp_sl import tp_sl_prices
from ..signal.pipeline import gate_signal_with_reason, signal_config_from_dict


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
    entry_max_hold_bars: int = 0
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
    bars = {"15m": 20000, "1h": 3000, "4h": 1800, "1d": 1200}
    dfs: dict[str, pd.DataFrame] = {}
    for iv in feats_iv:
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
    ap = int(cfg["filters"]["atr_period"])
    merged = add_m15_atr_ratio(merged, ap)
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
    entry_max_hold_bars = state.entry_max_hold_bars
    breakeven_done = state.breakeven_done
    consecutive_losses = state.consecutive_losses
    cooldown_first_allowed_i = state.cooldown_first_allowed_i
    halt_new_entries = state.halt_new_entries
    day_utc = state.day_utc
    quote_at_day_start = state.quote_at_day_start
    daily_pnl = state.daily_pnl
    signal_reason = "hold_position"
    signal_value = 0

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
        fill = o * (1.0 + slip * pending)
        pos_frac, tp_m, sl_m, mh_trade = _tier_position_and_risk(pending_confidence, cfg)
        notional = quote * pos_frac
        qty = notional / fill
        entry_px = fill
        tp, sl = tp_sl_prices(pending, fill, atr, tp_m, sl_m)
        side = pending
        entry_i = i
        entry_max_hold_bars = mh_trade
        breakeven_done = False
        pending = 0
        pending_confidence = 0.0

    max_hold = entry_max_hold_bars if entry_max_hold_bars > 0 else max_hold_base

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
            breakeven_done = False

    if side == 0:
        X = df.iloc[[i]][feats]
        try:
            p_up = float(model.predict_proba_up(X)[0])
        except Exception:
            p_up = 0.5
        pat = float(row.get("pattern_score", 0.0) or 0.0)
        if np.isnan(pat):
            pat = 0.0
        signal_value, signal_reason, sig_conf = gate_signal_with_reason(row, p_up, pat, atr, atr_hist, scfg)
        pending = signal_value
        pending_confidence = float(sig_conf) if signal_value != 0 else 0.0
        if halt_new_entries or i < cooldown_first_allowed_i:
            signal_reason = "risk_guard_block"
            pending = 0
            pending_confidence = 0.0
    else:
        pending = 0
        pending_confidence = 0.0
        signal_reason = "position_open"

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

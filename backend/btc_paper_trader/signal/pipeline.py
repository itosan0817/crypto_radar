from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class SignalConfig:
    weight_model: float
    weight_pattern: float
    entry_threshold: float
    min_confidence: float
    mtf_align_1h: bool
    agreement_required: bool
    expectancy_gate: bool
    taker_fee_rate: float
    slippage_bps: float
    tp_atr_mult: float
    sl_atr_mult: float
    atr_low_q: float
    atr_high_q: float
    enabled: bool


def combined_score(p_up: float, pattern: float, w_m: float, w_p: float) -> float:
    """Map to roughly [-1, 1]: long bias positive."""
    return w_m * (2.0 * p_up - 1.0) + w_p * pattern


def signal_confidence(p_up: float, pattern: float) -> float:
    """Same notion as gate when filters enabled: max(model edge, pattern magnitude)."""
    edge_model = abs(p_up - 0.5) * 2.0
    edge_pat = abs(pattern)
    return float(max(edge_model, edge_pat))


def gate_signal(
    row: pd.Series,
    p_up: float,
    pattern: float,
    atr: float,
    atr_hist: pd.Series | None,
    cfg: SignalConfig,
) -> int:
    """
    Returns +1 long, -1 short, 0 flat.
    `atr_hist` is rolling history of ATR for same length as backtest (for quantiles).
    """
    sig, _, _ = gate_signal_with_reason(row, p_up, pattern, atr, atr_hist, cfg)
    return sig


def gate_signal_with_reason(
    row: pd.Series,
    p_up: float,
    pattern: float,
    atr: float,
    atr_hist: pd.Series | None,
    cfg: SignalConfig,
) -> tuple[int, str, float]:
    """Returns (signal, reason, confidence). confidence is in [0,1]-ish scale when filters on."""
    conf = signal_confidence(p_up, pattern)
    if not cfg.enabled:
        s = combined_score(p_up, pattern, cfg.weight_model, cfg.weight_pattern)
        if s > cfg.entry_threshold:
            return 1, "emit_long", conf
        if s < -cfg.entry_threshold:
            return -1, "emit_short", conf
        return 0, "score_below_threshold", conf

    if conf < cfg.min_confidence:
        return 0, "low_confidence", conf

    s = combined_score(p_up, pattern, cfg.weight_model, cfg.weight_pattern)
    if abs(s) < cfg.entry_threshold:
        return 0, "score_below_threshold", conf

    if cfg.agreement_required:
        m_sign = np.sign(2.0 * p_up - 1.0)
        p_sign = np.sign(pattern) if abs(pattern) > 1e-9 else 0
        if m_sign != 0 and p_sign != 0 and m_sign != p_sign:
            return 0, "model_pattern_disagree", conf

    if cfg.mtf_align_1h:
        s1h = row.get("1h_slope", np.nan)
        if s == 0:
            pass
        elif s > 0 and not (s1h >= 0 or (isinstance(s1h, float) and np.isnan(s1h))):
            return 0, "mtf_align_block", conf
        elif s < 0 and not (s1h <= 0 or (isinstance(s1h, float) and np.isnan(s1h))):
            return 0, "mtf_align_block", conf

    if atr_hist is not None and len(atr_hist) > 50:
        q_lo = atr_hist.quantile(cfg.atr_low_q)
        q_hi = atr_hist.quantile(cfg.atr_high_q)
        if atr < q_lo or atr > q_hi:
            return 0, "atr_out_of_range", conf

    if cfg.expectancy_gate and atr > 0:
        # Rough edge after two taker fees + slippage both sides
        cost = 2.0 * cfg.taker_fee_rate + 4.0 * cfg.slippage_bps * 1e-4
        exp_move = (cfg.tp_atr_mult + cfg.sl_atr_mult) / 2.0 * atr / float(row["m15_close"])
        if exp_move <= cost * 1.1:
            return 0, "expectancy_gate_block", conf

    if s > cfg.entry_threshold:
        return 1, "emit_long", conf
    if s < -cfg.entry_threshold:
        return -1, "emit_short", conf
    return 0, "score_below_threshold", conf


def signal_config_from_dict(cfg: dict[str, Any]) -> SignalConfig:
    c = cfg["combine"]
    f = cfg["filters"]
    r = cfg["risk"]
    return SignalConfig(
        weight_model=float(c["weight_model"]),
        weight_pattern=float(c["weight_pattern"]),
        entry_threshold=float(c["entry_threshold"]),
        min_confidence=float(f["min_confidence"]),
        mtf_align_1h=bool(f["mtf_align_1h"]),
        agreement_required=bool(f["agreement_required"]),
        expectancy_gate=bool(f["expectancy_gate"]),
        taker_fee_rate=float(f["taker_fee_rate"]),
        slippage_bps=float(f["slippage_bps"]),
        tp_atr_mult=float(r["tp_atr_mult"]),
        sl_atr_mult=float(r["sl_atr_mult"]),
        atr_low_q=float(f["atr_low_quantile"]),
        atr_high_q=float(f["atr_high_quantile"]),
        enabled=bool(f["enabled"]),
    )

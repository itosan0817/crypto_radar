from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

from ..config import package_root
from ..eval.metrics import summarize_trades
from .engine import prepare_frame, run_backtest, train_model_slice


def _tune_score(summ: dict[str, Any], tune_cfg: dict[str, Any], initial_quote: float) -> float:
    min_tr = int(tune_cfg.get("min_test_trades", 5))
    min_pf = float(tune_cfg.get("min_profit_factor", 0.0))
    if summ["n_trades"] < min_tr:
        return float("-inf")
    if summ["profit_factor"] < min_pf:
        return float("-inf")
    sw = tune_cfg.get("score_weights") or {}
    pnl_w = float(sw.get("pnl", 1.0))
    dd_w = float(sw.get("drawdown_penalty", 0.5))
    pf_b = float(sw.get("profit_factor_bonus", 50.0))
    pf = float(summ["profit_factor"])
    if pf >= 999.0:
        pf = 20.0
    score = pnl_w * float(summ["total_pnl"]) - dd_w * abs(float(summ["max_drawdown"])) * initial_quote
    score += pf_b * min(pf, 20.0)
    return float(score)


def _current_runtime_score(
    cfg: dict[str, Any],
    df: Any,
    start: int,
    tr_end: int,
    te_start: int,
    te_end: int,
    initial_quote: float,
    tune_cfg: dict[str, Any],
    runtime_path: Path,
) -> float | None:
    if not runtime_path.exists():
        return None
    try:
        with open(runtime_path, encoding="utf-8") as f:
            rt = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    cfg_rt = copy.deepcopy(cfg)
    for key in ("risk", "combine", "filters"):
        if key in rt and isinstance(rt[key], dict):
            cfg_rt = _deep_merge_small(cfg_rt, {key: rt[key]})
    try:
        model = train_model_slice(df, cfg_rt, start, tr_end)
    except ValueError:
        return None
    pnls, _ = run_backtest(df, model, cfg_rt, te_start, te_end)
    summ = summarize_trades(pnls, initial_quote)
    return _tune_score(summ, tune_cfg, initial_quote)


def _deep_merge_small(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_small(out[k], v)
        else:
            out[k] = v
    return out


def tune_last_window_and_write(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Grid-search last walk-forward fold; write data/runtime_params.json with best params.
    """
    df = prepare_frame(cfg)
    n = len(df)
    wf = cfg["walk_forward"]
    train_n = int(wf["train_bars"])
    test_n = int(wf["test_bars"])
    emb = int(wf["embargo_bars"])
    grid = wf["grid"]
    tune_cfg = cfg.get("tune") or {}
    initial_q = float(cfg["backtest"]["initial_quote"])
    skip_worse = bool(tune_cfg.get("skip_if_worse_than_current", True))

    margin = 200
    for _ in range(100):
        te_end = n - 2
        te_start = te_end - test_n
        tr_end = te_start - emb
        start = tr_end - train_n
        if start >= margin:
            break
        train_n = max(400, int(train_n * 0.9))
        test_n = max(100, int(test_n * 0.9))
        emb = max(30, int(emb * 0.95))
    else:
        raise ValueError(f"tune: could not fit windows into n={n}")

    g_tp = grid["tp_atr_mult"]
    g_sl = grid["sl_atr_mult"]
    g_wm = grid["weight_model"]
    g_et = grid.get("entry_threshold")
    if not g_et:
        g_et = [float(cfg["combine"]["entry_threshold"])]
    g_mc = grid.get("min_confidence")
    if not g_mc:
        g_mc = [float(cfg["filters"]["min_confidence"])]

    runtime_path = package_root() / "data" / "runtime_params.json"
    baseline_score: float | None = None
    if skip_worse:
        baseline_score = _current_runtime_score(
            cfg, df, start, tr_end, te_start, te_end, initial_q, tune_cfg, runtime_path
        )

    best: dict[str, Any] | None = None
    for tp_m, sl_m, w_m, et, mc in product(g_tp, g_sl, g_wm, g_et, g_mc):
        cfg2 = copy.deepcopy(cfg)
        cfg2["risk"]["tp_atr_mult"] = float(tp_m)
        cfg2["risk"]["sl_atr_mult"] = float(sl_m)
        w_p = round(1.0 - float(w_m), 4)
        cfg2["combine"]["weight_model"] = float(w_m)
        cfg2["combine"]["weight_pattern"] = w_p
        cfg2["combine"]["entry_threshold"] = float(et)
        cfg2["filters"]["min_confidence"] = float(mc)
        try:
            model = train_model_slice(df, cfg2, start, tr_end)
        except ValueError:
            continue
        pnls, _ = run_backtest(df, model, cfg2, te_start, te_end)
        summ = summarize_trades(pnls, initial_q)
        score = _tune_score(summ, tune_cfg, initial_q)
        cand = {
            "params": (tp_m, sl_m, w_m, et, mc),
            "summary": summ,
            "score": score,
        }
        if best is None or cand["score"] > best["score"]:
            best = cand

    if best is None:
        raise RuntimeError("tune: no valid parameter combination (training failed?)")

    if (
        skip_worse
        and baseline_score is not None
        and best["score"] <= baseline_score
    ):
        out_skip: dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "tune_last_window",
            "skipped": True,
            "reason": "best_score_not_better_than_current_runtime",
            "best_score": best["score"],
            "baseline_score": baseline_score,
            "would_apply_params": best["params"],
            "test_summary": best["summary"],
            "train_range": [start, tr_end],
            "test_range": [te_start, te_end],
        }
        return out_skip

    tp_m, sl_m, w_m, et, mc = best["params"]
    w_p = round(1.0 - float(w_m), 4)
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "tune_last_window",
        "skipped": False,
        "train_range": [start, tr_end],
        "test_range": [te_start, te_end],
        "best_score": best["score"],
        "test_summary": best["summary"],
        "risk": {"tp_atr_mult": float(tp_m), "sl_atr_mult": float(sl_m)},
        "combine": {
            "weight_model": float(w_m),
            "weight_pattern": w_p,
            "entry_threshold": float(et),
        },
        "filters": {"min_confidence": float(mc)},
    }

    path = package_root() / "data" / "runtime_params.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    return out

from __future__ import annotations

from itertools import product
from typing import Any

import pandas as pd

from ..eval.metrics import summarize_trades
from .engine import prepare_frame, run_backtest, train_model_slice


def walk_forward(cfg: dict[str, Any], df: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    wf = cfg["walk_forward"]
    train_n = int(wf["train_bars"])
    test_n = int(wf["test_bars"])
    emb = int(wf["embargo_bars"])
    step = int(wf["step_bars"])
    grid = wf["grid"]

    if df is None:
        df = prepare_frame(cfg)

    n = len(df)
    results: list[dict[str, Any]] = []
    start = 0
    while True:
        tr_end = start + train_n
        te_start = tr_end + emb
        te_end = te_start + test_n
        if te_end > n:
            break

        g_et = grid.get("entry_threshold") or [float(cfg["combine"]["entry_threshold"])]
        g_mc = grid.get("min_confidence") or [float(cfg["filters"]["min_confidence"])]
        best: dict[str, Any] | None = None
        for tp_m, sl_m, w_m, et, mc in product(
            grid["tp_atr_mult"],
            grid["sl_atr_mult"],
            grid["weight_model"],
            g_et,
            g_mc,
        ):
            cfg2 = _copy_cfg(cfg)
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
            summ = summarize_trades(pnls, cfg["backtest"]["initial_quote"])
            score = summ["total_pnl"] - abs(summ["max_drawdown"]) * cfg["backtest"]["initial_quote"] * 0.5
            cand = {"params": (tp_m, sl_m, w_m, et, mc), "summary": summ, "score": score}
            if best is None or cand["score"] > best["score"]:
                best = cand

        if best:
            results.append(
                {
                    "train": (start, tr_end),
                    "test": (te_start, te_end),
                    "best_params": best["params"],
                    "summary": best["summary"],
                }
            )

        start += step

    return results


def _copy_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    import copy

    return copy.deepcopy(cfg)

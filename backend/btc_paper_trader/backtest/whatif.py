"""仮設定での過去シミュレーション（what-if バックテスト）。

ダッシュボードから渡されたオーバーライドを config に deep-merge し、
sqlite キャッシュのみ（offline）で直近 N バーを再シミュレートする。
paper ループと同じ step_simulation を使うが、Claude アドバイザーは呼ばれない。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from ..config import _deep_merge
from ..eval.metrics import summarize_trades
from .engine import SimState, prepare_frame, step_simulation, train_model_slice


def run_what_if(
    cfg: dict[str, Any],
    overrides: dict[str, Any] | None,
    eval_bars: int = 96,
    train_bars: int = 5000,
) -> dict[str, Any]:
    """直近 eval_bars 本の15分足を、オーバーライド適用後の設定で再シミュレートする。

    戻り値の records は paper_events.jsonl と同じ形
    （bar_open_time / quote / events）なので、取引ペアリングを共用できる。
    """
    cfg2 = _deep_merge(copy.deepcopy(cfg), overrides or {})
    df = prepare_frame(cfg2, offline=True)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if "m15_close_time" in df.columns:
        df = df[df["m15_close_time"] <= now_ms].reset_index(drop=True)

    n = len(df)
    eval_bars = max(8, min(int(eval_bars), 3000))
    # step_simulation は指標のルックバックがあるため十分な過去バーを確保する
    i0 = max(200, n - eval_bars)
    i1 = n - 1
    if i1 - i0 < 4:
        raise ValueError(f"not enough bars for what-if: n={n}")

    tr0 = max(0, i0 - int(train_bars))
    model = train_model_slice(df, cfg2, tr0, i0)

    q0 = float(cfg2["backtest"]["initial_quote"])
    state = SimState(quote=q0, quote_at_day_start=q0)
    ot = df["m15_open_time"]
    records: list[dict[str, Any]] = []
    pnls: list[float] = []
    for i in range(i0, i1 + 1):
        state, events = step_simulation(df, model, cfg2, state, i, None)
        for e in events:
            if "pnl" in e:
                pnls.append(float(e["pnl"]))
        records.append(
            {
                "bar_open_time": int(ot.iloc[i]),
                "quote": float(state.quote),
                "events": events,
            }
        )

    summary = summarize_trades(pnls, q0)
    return {
        "records": records,
        "summary": summary,
        "n_bars": i1 - i0 + 1,
        "initial_quote": q0,
    }

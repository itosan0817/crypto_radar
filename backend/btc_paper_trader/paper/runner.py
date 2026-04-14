from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..backtest.engine import SimState, prepare_frame, step_simulation, train_model_slice
from ..config import load_config, package_root
from ..eval.metrics import summarize_trades
from ..notify.discord import post_daily_summary, post_hourly_summary


def _utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _json_safe(obj: Any) -> Any:
    """Recursively convert numpy scalars / arrays so json.dump does not raise (e.g. int64)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_json_safe(v) for v in obj)
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_state(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _json_safe(obj)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False, indent=2, default=str)


def _df_only_closed(df: pd.DataFrame) -> pd.DataFrame:
    now = _utc_ms()
    if "m15_close_time" not in df.columns:
        return df
    return df[df["m15_close_time"] <= now].reset_index(drop=True)


def _reason_ja(reason: str) -> str:
    mapping = {
        "emit_long": "ロング条件成立",
        "emit_short": "ショート条件成立",
        "score_below_threshold": "スコア不足",
        "low_confidence": "確信度不足",
        "model_pattern_disagree": "モデルとパターン不一致",
        "mtf_align_block": "上位足トレンド不一致",
        "atr_out_of_range": "値動き幅が条件外",
        "expectancy_gate_block": "期待値フィルターで除外",
        "risk_guard_block": "リスク制限で新規停止",
        "hold_position": "保有中のため新規なし",
        "position_open": "保有中",
        "unknown": "不明",
    }
    return mapping.get(reason, reason)


def run_paper_loop(cfg: dict[str, Any] | None = None, once: bool = False) -> None:
    cfg = cfg or load_config()
    root = package_root()
    state_path = root / cfg.get("paper", {}).get("state_path", "data/paper_state.json")
    log_path = root / cfg["logging"]["jsonl_path"]
    train_window = int(cfg.get("paper", {}).get("train_window_bars", 8000))
    poll = float(cfg.get("paper", {}).get("poll_seconds", 30))

    raw = _load_state(state_path)
    q0 = float(cfg["backtest"]["initial_quote"])
    sim = SimState(
        quote=float(raw.get("quote", q0)),
        side=int(raw.get("side", 0)),
        entry_px=float(raw.get("entry_px", 0.0)),
        qty=float(raw.get("qty", 0.0)),
        tp=float(raw.get("tp", 0.0)),
        sl=float(raw.get("sl", 0.0)),
        entry_i=int(raw.get("entry_i", 0)),
        pending=int(raw.get("pending", 0)),
        pending_confidence=float(raw.get("pending_confidence", 0.0)),
        entry_max_hold_bars=int(raw.get("entry_max_hold_bars", 0)),
        breakeven_done=bool(raw.get("breakeven_done", False)),
        consecutive_losses=int(raw.get("consecutive_losses", 0)),
        cooldown_first_allowed_i=int(raw.get("cooldown_first_allowed_i", 0)),
        halt_new_entries=bool(raw.get("halt_new_entries", False)),
        day_utc=str(raw.get("day_utc", "")),
        quote_at_day_start=float(raw.get("quote_at_day_start", q0)),
        daily_pnl=float(raw.get("daily_pnl", 0.0)),
    )
    last_ot = int(raw.get("last_m15_open_time", 0))
    hourly_pnls: list[float] = list(raw.get("hourly_pnls_buffer", []))
    day_pnls: list[float] = list(raw.get("day_pnls_buffer", []))
    last_hour_key = raw.get("last_hour_key")
    last_day_key = raw.get("last_day_key")
    hourly_new_bars = int(raw.get("hourly_new_bars", 0))
    hourly_signal_count = int(raw.get("hourly_signal_count", 0))
    hourly_reason_counts: dict[str, int] = dict(raw.get("hourly_reason_counts", {}))

    last_processed_ot = 0
    cached_df = None
    cached_model = None
    reload_sec = float(cfg.get("paper", {}).get("reload_runtime_params_seconds", 0) or 0)
    last_cfg_reload = time.monotonic()

    while True:
        if reload_sec > 0 and time.monotonic() - last_cfg_reload >= reload_sec:
            cfg = load_config()
            last_cfg_reload = time.monotonic()

        # 1. Fetch fresh data
        full_df = prepare_frame(cfg)
        df = _df_only_closed(full_df)
        
        if len(df) < train_window:
            time.sleep(poll)
            if once:
                break
            continue

        n = len(df)
        current_ot = int(df["m15_open_time"].iloc[-1])
        
        # 2. Only retrain and refresh features for training if a new candle formed
        # or if we don't have a cached model yet.
        if cached_model is None or current_ot != last_processed_ot:
            i_train0 = max(0, n - train_window)
            try:
                cached_model = train_model_slice(df, cfg, i_train0, n - 1)
                last_processed_ot = current_ot
                cached_df = df
            except ValueError:
                time.sleep(poll)
                if once:
                    break
                continue
        else:
            # If no new candle, we can use the cached model and df.
            # However, we still want to check if the CURRENT (unclosed) bar 
            # or the latest closed bar needs processing.
            df = cached_df

        ot = df["m15_open_time"].astype(np.int64)
        # 初回は過去の再生を避け、直近確定済みバーまでスキップ
        if last_ot == 0 and not raw.get("initialized"):
            last_ot = int(ot.iloc[-3])

        new_pnls_tick: list[float] = []
        mask = ot.values > last_ot
        indices = np.where(mask)[0]
        for i in indices:
            i = int(i)
            oti = int(ot.iloc[i])
            hourly_new_bars += 1
            sim, events = step_simulation(df, cached_model, cfg, sim, i, None)
            for e in events:
                if e.get("type") == "decision":
                    hourly_signal_count += 1
                    reason = str(e.get("reason", "unknown"))
                    hourly_reason_counts[reason] = hourly_reason_counts.get(reason, 0) + 1
                    continue
                if "pnl" in e:
                    p = float(e["pnl"])
                    hourly_pnls.append(p)
                    day_pnls.append(p)
                    new_pnls_tick.append(p)
            last_ot = oti
            rec = {
                "t": _utc_ms(),
                "bar_open_time": oti,
                "quote": sim.quote,
                "side": sim.side,
                "pending": sim.pending,
                "halt_new_entries": sim.halt_new_entries,
                "daily_pnl": sim.daily_pnl,
                "cooldown_first_allowed_i": sim.cooldown_first_allowed_i,
                "events": events,
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(json.dumps(_json_safe(rec), ensure_ascii=False, default=str) + "\n")

        now_utc = datetime.now(timezone.utc)
        hour_key = now_utc.strftime("%Y-%m-%d-%H")
        day_key = now_utc.strftime("%Y-%m-%d")

        if last_hour_key is not None and hour_key != last_hour_key:
            summ = summarize_trades(list(hourly_pnls), cfg["backtest"]["initial_quote"])
            top_reasons = sorted(hourly_reason_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            reason_text = ", ".join([f"{_reason_ja(k)}:{v}" for k, v in top_reasons]) if top_reasons else "なし"
            post_hourly_summary(
                f"実現PnL合計: {summ['total_pnl']:.2f} / 取引数 {summ['n_trades']} / 勝率 {summ['win_rate']:.2%} / PF {summ['profit_factor']:.2f}",
                fields=[
                    {"name": "新しいバー数", "value": f"{hourly_new_bars}", "inline": True},
                    {"name": "シグナル数", "value": f"{hourly_signal_count}", "inline": True},
                    {"name": "主な理由", "value": reason_text[:1000], "inline": False},
                    {"name": "1取引あたり期待損益", "value": f"{summ['expectancy']:.4f}", "inline": True},
                    {"name": "最大ドローダウン", "value": f"{summ['max_drawdown']:.4f}", "inline": True},
                    {"name": "平均利益", "value": f"{summ.get('avg_win', 0):.4f}", "inline": True},
                    {"name": "平均損失", "value": f"{summ.get('avg_loss_abs', 0):.4f}", "inline": True},
                    {"name": "ペイオフ比", "value": f"{summ.get('payoff_ratio', 0):.2f}", "inline": True},
                    {"name": "最大連敗", "value": f"{summ.get('max_consecutive_losses', 0)}", "inline": True},
                ],
            )
            hourly_pnls.clear()
            hourly_new_bars = 0
            hourly_signal_count = 0
            hourly_reason_counts = {}

        if last_day_key is not None and day_key != last_day_key:
            summ_d = summarize_trades(list(day_pnls), cfg["backtest"]["initial_quote"])
            post_daily_summary(
                f"日次 実現PnL: {summ_d['total_pnl']:.2f} / 取引 {summ_d['n_trades']} / 勝率 {summ_d['win_rate']:.2%} / PF {summ_d['profit_factor']:.2f}",
                fields=[
                    {"name": "平均利益", "value": f"{summ_d.get('avg_win', 0):.4f}", "inline": True},
                    {"name": "平均損失", "value": f"{summ_d.get('avg_loss_abs', 0):.4f}", "inline": True},
                    {"name": "ペイオフ比", "value": f"{summ_d.get('payoff_ratio', 0):.2f}", "inline": True},
                    {"name": "最大連敗", "value": f"{summ_d.get('max_consecutive_losses', 0)}", "inline": True},
                    {"name": "期待値/取引", "value": f"{summ_d['expectancy']:.4f}", "inline": True},
                ],
            )
            day_pnls.clear()

        last_hour_key = hour_key
        last_day_key = day_key

        initialized_now = not raw.get("initialized")
        _save_state(
            state_path,
            {
                "quote": sim.quote,
                "side": sim.side,
                "entry_px": sim.entry_px,
                "qty": sim.qty,
                "tp": sim.tp,
                "sl": sim.sl,
                "entry_i": sim.entry_i,
                "pending": sim.pending,
                "pending_confidence": sim.pending_confidence,
                "entry_max_hold_bars": sim.entry_max_hold_bars,
                "breakeven_done": sim.breakeven_done,
                "consecutive_losses": sim.consecutive_losses,
                "cooldown_first_allowed_i": sim.cooldown_first_allowed_i,
                "halt_new_entries": sim.halt_new_entries,
                "day_utc": sim.day_utc,
                "quote_at_day_start": sim.quote_at_day_start,
                "daily_pnl": sim.daily_pnl,
                "last_m15_open_time": last_ot,
                "hourly_pnls_buffer": hourly_pnls[-500:],
                "day_pnls_buffer": day_pnls[-5000:],
                "hourly_new_bars": hourly_new_bars,
                "hourly_signal_count": hourly_signal_count,
                "hourly_reason_counts": hourly_reason_counts,
                "last_hour_key": last_hour_key,
                "last_day_key": last_day_key,
                "initialized": True,
            },
        )
        
        if initialized_now:
            post_hourly_summary(
                "🚀 **BTC Paper Trader 稼働開始**",
                fields=[
                    {"name": "Status", "value": "初期計算および学習が完了しました。常時監視を開始します。", "inline": False},
                    {"name": "開始残高", "value": f"${sim.quote:,.2f}", "inline": True},
                    {"name": "学習バー数", "value": f"{train_window} 本", "inline": True},
                ]
            )
            # 以降のループで通知されないように、メモリ上の状態も更新
            raw["initialized"] = True

        if once:
            break
        time.sleep(poll)


def paper_step_once(cfg: dict[str, Any] | None = None) -> None:
    run_paper_loop(cfg, once=True)

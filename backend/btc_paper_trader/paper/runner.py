from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..advisor.daily_review import run_daily_review
from ..advisor.entry_advisor import advise_entry
from ..backtest.engine import SimState, prepare_frame, step_simulation, train_model_slice
from ..config import load_config, package_root
from ..data.binance_futures import INTERVAL_MS
from ..eval.metrics import summarize_trades
from ..notify.discord import post_claude_advice, post_daily_summary, post_hourly_summary


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
        "emit_long_grid": "レンジ逆張りロング成立",
        "emit_short_grid": "レンジ逆張りショート成立",
        "grid_center_deadband": "レンジ中心帯で見送り",
        "grid_wait_band": "レンジ待機帯",
        "grid_disabled": "グリッド無効",
        "grid_data_unavailable": "グリッド判定データ不足",
        "range_breakout_guard": "ボラ急拡大でレンジ停止",
        "entry_timing_1m_data_unavailable": "1分足データ不足で見送り",
        "entry_timing_1m_trend_long_block": "1分足条件不足でトレンドロング見送り",
        "entry_timing_1m_trend_short_block": "1分足条件不足でトレンドショート見送り",
        "entry_timing_1m_range_long_block": "1分足条件不足でレンジロング見送り",
        "entry_timing_1m_range_short_block": "1分足条件不足でレンジショート見送り",
        "entry_timing_skipped_regime": "1分足フィルタ対象外レジーム（即時約定）",
        "score_below_threshold": "スコア不足",
        "low_confidence": "確信度不足",
        "model_pattern_disagree": "モデルとパターン不一致",
        "mtf_align_block": "上位足トレンド不一致",
        "atr_out_of_range": "値動き幅が条件外",
        "expectancy_gate_block": "期待値フィルターで除外",
        "funding_long_block": "Funding過熱でロング除外",
        "funding_short_block": "Funding過熱でショート除外",
        "news_event_block": "ニュース時間帯で新規停止",
        "risk_guard_block": "リスク制限で新規停止",
        "hold_position": "保有中のため新規なし",
        "position_open": "保有中",
        "unknown": "不明",
    }
    return mapping.get(reason, reason)


def _fmt_tune_changes(values: dict[str, Any] | None, old_values: dict[str, Any] | None) -> str:
    parts = []
    for k, v in (values or {}).items():
        if k == "combine.weight_pattern":  # weight_model に連動する派生値は表示しない
            continue
        short = k.split(".")[-1]
        old = (old_values or {}).get(k)
        parts.append(f"{short}: {old}→{v}" if old is not None else f"{short}={v}")
    return ", ".join(parts) or "—"


def _tune_field_value(tune_res: dict[str, Any] | None, tune_err: str | None) -> str | None:
    """自動チューニング結果を日次レポートの1フィールドに要約する。無効時は None。"""
    if tune_err:
        return f"⚠ 実行エラー: {tune_err}"
    if tune_res is None:
        return None
    action = tune_res.get("action")
    if action == "rollback":
        return (
            f"⏪ ロールバック: 適用後 {tune_res.get('days_since_change')}日 の実現PnL "
            f"{tune_res.get('live_pnl_since_change')} USDT が閾値 {tune_res.get('threshold')} を下回ったため復元\n"
            f"復元: {_fmt_tune_changes(tune_res.get('reverted_to'), None)}"
        )
    if action == "no_candidates":
        return "➖ 現状維持（有効な候補なし）"
    b = tune_res.get("baseline") or {}
    d = tune_res.get("eval_days")
    base_txt = (
        f"検証({d}日) 現行: PnL {b.get('total_pnl', 0):.1f} / {b.get('n_trades', 0)}件"
        if b else ""
    )
    if action == "applied":
        winner_name = tune_res.get("winner")
        w = next(
            (c for c in tune_res.get("candidates", []) if c.get("name") == winner_name),
            {},
        )
        ws = w.get("summary") or {}
        return (
            f"✅ 適用「{winner_name}」: {_fmt_tune_changes(tune_res.get('values'), tune_res.get('old_values'))}\n"
            f"仮説: {w.get('rationale', '—')}\n"
            f"検証({d}日): 現行 PnL {b.get('total_pnl', 0):.1f}({b.get('n_trades', 0)}件) "
            f"→ 採用 {ws.get('total_pnl', 0):.1f}({ws.get('n_trades', 0)}件)"
        )
    n = len(tune_res.get("candidates") or [])
    return f"➖ 現状維持（{n}候補を検証、現行に明確に勝る候補なし）\n{base_txt}"


def _post_daily_report(
    day_key: str,
    summ_d: dict[str, Any],
    review: dict[str, Any] | None,
    tune_res: dict[str, Any] | None,
    tune_err: str | None,
) -> None:
    """日次サマリ・Claudeレビュー・自動チューニング結果を1通のDiscordメッセージにまとめる。"""
    text = (
        f"📊 **日次レポート ({day_key} UTC)**\n"
        f"実現PnL **{summ_d['total_pnl']:+.2f}** / 取引 {summ_d['n_trades']} / "
        f"勝率 {summ_d['win_rate']:.1%} / PF {summ_d['profit_factor']:.2f}"
    )
    if review and review.get("summary"):
        text += f"\n\n🤖 **総評**: {str(review['summary'])[:500]}"

    fields: list[dict[str, Any]] = [
        {
            "name": "成績詳細",
            "value": (
                f"平均利益 {summ_d.get('avg_win', 0):.2f} / 平均損失 {summ_d.get('avg_loss_abs', 0):.2f} / "
                f"ペイオフ比 {summ_d.get('payoff_ratio', 0):.2f} / 最大連敗 {summ_d.get('max_consecutive_losses', 0)} / "
                f"期待値 {summ_d['expectancy']:.3f}"
            ),
            "inline": False,
        },
    ]
    if review:
        suggestions = review.get("suggestions") or []
        if isinstance(suggestions, str):
            suggestions = [suggestions]
        fields += [
            {"name": "✅ 機能した点", "value": str(review.get("what_worked", "—"))[:700], "inline": False},
            {"name": "⚠️ 課題", "value": str(review.get("issues", "—"))[:700], "inline": False},
            {"name": "💡 改善提案", "value": ("\n".join(f"- {s}" for s in suggestions) or "—")[:700], "inline": False},
            {"name": "🔭 翌日の注目点", "value": str(review.get("tomorrow_focus", "—"))[:400], "inline": False},
        ]
    tune_txt = _tune_field_value(tune_res, tune_err)
    if tune_txt:
        fields.append({"name": "🔧 自動チューニング", "value": tune_txt[:1000], "inline": False})
    post_daily_summary(text, fields=fields)


def _maybe_claude_gate(
    df: pd.DataFrame,
    i: int,
    sim: SimState,
    events: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> None:
    """
    エントリー候補 (pending) が立った直後に Claude セカンドオピニオンを取得する。
    mode=advise: 記録と通知のみ / mode=gate: veto ならエントリー中止。
    過去バーのリプレイ時は呼ばない（直近30分以内に確定したバーのみ対象）。
    """
    adv_cfg = cfg.get("advisor") or {}
    if not adv_cfg.get("enabled", False):
        return
    mode = str(adv_cfg.get("mode", "advise"))
    if mode == "off" or sim.pending == 0:
        return
    decision = next(
        (
            e for e in events
            if e.get("type") == "decision" and int(e.get("pending_after_guard", 0)) != 0
        ),
        None,
    )
    if decision is None:
        return
    # ライブのバーのみ対象（起動直後の過去バー処理でサブスクを浪費しない）
    try:
        close_time = int(df["m15_close_time"].iloc[i])
        if _utc_ms() - close_time > 30 * 60 * 1000:
            return
    except (KeyError, ValueError, TypeError):
        return

    advice = advise_entry(df, i, decision, sim, cfg)
    if advice is None:
        return
    blocked = mode == "gate" and advice["verdict"] == "veto"
    if blocked:
        sim.pending = 0
        sim.pending_confidence = 0.0
        sim.pending_regime = "trend"
    events.append({"type": "claude_advice", "blocked": blocked, **advice})
    if adv_cfg.get("notify", True):
        post_claude_advice(advice, blocked=blocked, mode=mode)


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
        pending_regime=str(raw.get("pending_regime", "trend")),
        entry_max_hold_bars=int(raw.get("entry_max_hold_bars", 0)),
        entry_atr=float(raw.get("entry_atr", 0.0)),
        partial_tp_done=bool(raw.get("partial_tp_done", False)),
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
    hourly_short_signal_count = int(raw.get("hourly_short_signal_count", 0))
    hourly_short_blocked_count = int(raw.get("hourly_short_blocked_count", 0))
    hourly_short_block_reasons: dict[str, int] = dict(raw.get("hourly_short_block_reasons", {}))
    hourly_long_signal_count = int(raw.get("hourly_long_signal_count", 0))
    hourly_long_blocked_count = int(raw.get("hourly_long_blocked_count", 0))
    hourly_long_block_reasons: dict[str, int] = dict(raw.get("hourly_long_block_reasons", {}))
    hourly_regime_counts: dict[str, int] = dict(raw.get("hourly_regime_counts", {}))
    hourly_entry_count = int(raw.get("hourly_entry_count", 0))
    hourly_entry_long_count = int(raw.get("hourly_entry_long_count", 0))
    hourly_entry_short_count = int(raw.get("hourly_entry_short_count", 0))

    last_processed_ot = 0
    cached_df = None
    cached_model = None
    reload_sec = float(cfg.get("paper", {}).get("reload_runtime_params_seconds", 0) or 0)
    last_cfg_reload = time.monotonic()
    signal_step_ms = INTERVAL_MS[cfg["intervals"]["signal"]]

    while True:
        if reload_sec > 0 and time.monotonic() - last_cfg_reload >= reload_sec:
            cfg = load_config()
            last_cfg_reload = time.monotonic()
            signal_step_ms = INTERVAL_MS[cfg["intervals"]["signal"]]

        # 新しい足が閉じたかどうかを、Binanceへ問い合わせず壁時計だけで先に見積もる。
        # Kline は UTC 境界に整列するため計算だけで判定できる。閉じていなければ
        # prepare_frame（全期間の特徴量再構築・パターン検索）を丸ごとスキップし、
        # 直近確定バーの取得のためだけに毎ポーリング重い処理が走るのを防ぐ。
        now_ms = _utc_ms()
        guessed_last_closed_ot = (now_ms // signal_step_ms - 1) * signal_step_ms
        need_fetch = cached_df is None or guessed_last_closed_ot > last_processed_ot

        if need_fetch:
            full_df = prepare_frame(cfg)
            df = _df_only_closed(full_df)

            if len(df) < train_window:
                time.sleep(poll)
                if once:
                    break
                continue

            n = len(df)
            current_ot = int(df["m15_open_time"].iloc[-1])

            # 新しい足が確定していた場合のみ再学習する（壁時計の見積もりが Binance 側の
            # 反映遅延で先走ることがあるため、実データでも念のため確認する）。
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
                df = cached_df
        else:
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
            _maybe_claude_gate(df, i, sim, events, cfg)
            for e in events:
                if e.get("type") == "entry":
                    hourly_entry_count += 1
                    es = int(e.get("side", 0))
                    if es == 1:
                        hourly_entry_long_count += 1
                    elif es == -1:
                        hourly_entry_short_count += 1
                    continue
                if e.get("type") == "decision":
                    hourly_signal_count += 1
                    signal = int(e.get("signal", 0))
                    pending_after_guard = int(e.get("pending_after_guard", 0))
                    reason = str(e.get("reason", "unknown"))
                    regime = str(e.get("regime", "unknown"))
                    hourly_reason_counts[reason] = hourly_reason_counts.get(reason, 0) + 1
                    hourly_regime_counts[regime] = hourly_regime_counts.get(regime, 0) + 1
                    if signal == 1:
                        hourly_long_signal_count += 1
                        if pending_after_guard == 0:
                            hourly_long_blocked_count += 1
                            hourly_long_block_reasons[reason] = (
                                hourly_long_block_reasons.get(reason, 0) + 1
                            )
                    elif signal == -1:
                        hourly_short_signal_count += 1
                        if pending_after_guard == 0:
                            hourly_short_blocked_count += 1
                            hourly_short_block_reasons[reason] = (
                                hourly_short_block_reasons.get(reason, 0) + 1
                            )
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
            # 毎時サマリ通知は notify.hourly_summary で制御（既定オフ。日次通知で十分なため）
            if (cfg.get("notify") or {}).get("hourly_summary", False):
                summ = summarize_trades(list(hourly_pnls), cfg["backtest"]["initial_quote"])
                top_reasons = sorted(hourly_reason_counts.items(), key=lambda x: x[1], reverse=True)[:3]
                reason_text = ", ".join([f"{_reason_ja(k)}:{v}" for k, v in top_reasons]) if top_reasons else "なし"
                top_short_block_reasons = sorted(
                    hourly_short_block_reasons.items(), key=lambda x: x[1], reverse=True
                )[:3]
                short_block_text = (
                    ", ".join([f"{_reason_ja(k)}:{v}" for k, v in top_short_block_reasons])
                    if top_short_block_reasons
                    else "なし"
                )
                top_long_block_reasons = sorted(
                    hourly_long_block_reasons.items(), key=lambda x: x[1], reverse=True
                )[:3]
                long_block_text = (
                    ", ".join([f"{_reason_ja(k)}:{v}" for k, v in top_long_block_reasons])
                    if top_long_block_reasons
                    else "なし"
                )
                regime_text = ", ".join([f"{k}:{v}" for k, v in sorted(hourly_regime_counts.items())]) or "なし"
                entry_breakdown = f"ロング {hourly_entry_long_count} / ショート {hourly_entry_short_count}"
                post_hourly_summary(
                    f"実現PnL合計: {summ['total_pnl']:.2f} / 取引数 {summ['n_trades']} / 勝率 {summ['win_rate']:.2%} / PF {summ['profit_factor']:.2f}",
                    fields=[
                        {"name": "新しいバー数", "value": f"{hourly_new_bars}", "inline": True},
                        {"name": "シグナル数", "value": f"{hourly_signal_count}", "inline": True},
                        {"name": "エントリー件数", "value": f"{hourly_entry_count}（{entry_breakdown}）", "inline": False},
                        {"name": "レジーム内訳", "value": regime_text[:1000], "inline": False},
                        {"name": "主な理由", "value": reason_text[:1000], "inline": False},
                        {"name": "ロング候補シグナル数", "value": f"{hourly_long_signal_count}", "inline": True},
                        {"name": "ロング阻止数", "value": f"{hourly_long_blocked_count}", "inline": True},
                        {"name": "ロング阻止の主因", "value": long_block_text[:1000], "inline": False},
                        {"name": "ショート候補シグナル数", "value": f"{hourly_short_signal_count}", "inline": True},
                        {"name": "ショート阻止数", "value": f"{hourly_short_blocked_count}", "inline": True},
                        {"name": "ショート阻止の主因", "value": short_block_text[:1000], "inline": False},
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
            hourly_short_signal_count = 0
            hourly_short_blocked_count = 0
            hourly_short_block_reasons = {}
            hourly_long_signal_count = 0
            hourly_long_blocked_count = 0
            hourly_long_block_reasons = {}
            hourly_regime_counts = {}
            hourly_entry_count = 0
            hourly_entry_long_count = 0
            hourly_entry_short_count = 0

        if last_day_key is not None and day_key != last_day_key:
            # 日次サマリ・Claudeレビュー・自動チューニングを1通のDiscordメッセージに
            # まとめて送る。レビューとチューニングは数分かかる重い処理のため、
            # paperループを止めないよう別スレッドで実行する。
            day_pnls_snapshot = list(day_pnls)
            day_pnls.clear()
            closed_day = last_day_key
            cfg_snapshot = cfg

            def _daily_report_bg() -> None:
                summ_d = summarize_trades(day_pnls_snapshot, cfg_snapshot["backtest"]["initial_quote"])
                review = None
                if (cfg_snapshot.get("daily_review") or {}).get("enabled", False):
                    try:
                        review = run_daily_review(cfg_snapshot, day_key=closed_day, post=False)
                    except Exception as e:
                        print(f"[daily_review] unexpected error: {str(e)[:150]}")
                tune_res = None
                tune_err = None
                if (cfg_snapshot.get("auto_tune") or {}).get("enabled", False):
                    try:
                        from ..advisor.auto_tune import run_auto_tune
                        tune_res = run_auto_tune(notify=False)
                    except Exception as e:
                        tune_err = str(e)[:200]
                        print(f"[auto_tune] unexpected error: {tune_err}")
                try:
                    _post_daily_report(closed_day, summ_d, review, tune_res, tune_err)
                except Exception as e:
                    print(f"[daily_report] post failed: {str(e)[:150]}")

            threading.Thread(target=_daily_report_bg, daemon=True).start()

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
                "pending_regime": sim.pending_regime,
                "entry_max_hold_bars": sim.entry_max_hold_bars,
                "entry_atr": sim.entry_atr,
                "partial_tp_done": sim.partial_tp_done,
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
                "hourly_short_signal_count": hourly_short_signal_count,
                "hourly_short_blocked_count": hourly_short_blocked_count,
                "hourly_short_block_reasons": hourly_short_block_reasons,
                "hourly_long_signal_count": hourly_long_signal_count,
                "hourly_long_blocked_count": hourly_long_blocked_count,
                "hourly_long_block_reasons": hourly_long_block_reasons,
                "hourly_regime_counts": hourly_regime_counts,
                "hourly_entry_count": hourly_entry_count,
                "hourly_entry_long_count": hourly_entry_long_count,
                "hourly_entry_short_count": hourly_entry_short_count,
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

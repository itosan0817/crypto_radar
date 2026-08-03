"""Claude 自動チューニングループ。

日次で以下を無人実行する:
  ① 診断   直近成績を集計
  ② 仮説   Claude(提案役) が候補パラメータセットを複数提案（変更幅に上限）
  ③ 審査   Claude(リスク審査役) が危険な候補を veto
  ④ 検証   現行設定と各候補を what-if バックテストで同条件比較
  ⑤ 適用   現行に明確に勝った候補のみ config.local.yaml に適用し Discord 通知
  ⑥ 監視   適用後の実成績が悪ければ前回値へ自動ロールバック

原則: Claude は仮説を出す係、シミュレーターが審判。数値を無検証で
採用することはなく、資金リスク系パラメータには触れない（settings_spec の
auto: False）。全判断は data/auto_tune_history.jsonl に記録される。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import load_config, package_root
from ..data.binance_futures import bars_per_hour, interval_label_ja
from ..eval.trade_log import build_trades, period_stats, tail_jsonl
from ..notify.discord import post_daily_summary
from ..settings_spec import (
    EDITABLE_PARAMS,
    get_by_path,
    validate_values,
    values_to_nested,
    write_local_overrides,
)
from .claude_cli import extract_json, run_claude
from .news_context import build_news_context_block

HISTORY_REL = "data/auto_tune_history.jsonl"


def _utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _summary_lite(s: dict[str, Any]) -> dict[str, Any]:
    return {
        k: s.get(k)
        for k in ("total_pnl", "n_trades", "win_rate", "profit_factor", "max_drawdown")
    }


def _auto_specs() -> list[dict[str, Any]]:
    return [p for p in EDITABLE_PARAMS if p.get("auto")]


def _current_auto_values(cfg: dict[str, Any]) -> dict[str, Any]:
    return {p["path"]: get_by_path(cfg, p["path"]) for p in _auto_specs()}


def _params_block(cfg: dict[str, Any], max_pct: float) -> str:
    lines = []
    for p in _auto_specs():
        cur = get_by_path(cfg, p["path"])
        lines.append(
            f"- {p['path']} ({p['label']}) 現在値: {cur} 許容範囲: {p['min']}〜{p['max']}"
            f"（今回変更してよいのは現在値の±{max_pct:.0f}%まで）"
        )
    return "\n".join(lines)


def _stats_block(stats: dict[str, Any]) -> str:
    if not stats.get("n_trades"):
        return "直近の取引はゼロ。エントリー見送り理由: " + (
            ", ".join(f"{k}:{v}" for k, v in (stats.get("block_reasons") or {}).items()) or "情報なし"
        )
    return (
        f"直近{stats['days']}日: 取引 {stats['n_trades']} / 勝率 {stats['win_rate']:.1%} / "
        f"合計PnL {stats['total_pnl']:.2f} USDT / 平均利益 {stats['avg_win']:.2f} / "
        f"平均損失 {stats['avg_loss']:.2f}\n"
        f"決済理由: {', '.join(f'{k}:{v}' for k, v in stats['exit_reasons'].items()) or 'なし'}\n"
        f"見送り理由上位: {', '.join(f'{k}:{v}' for k, v in stats['block_reasons'].items()) or 'なし'}"
    )


def _clamp_to_delta(
    values: dict[str, Any], cfg: dict[str, Any], max_pct: float
) -> dict[str, Any]:
    """候補値を「現在値±max_pct%」かつスペック範囲内へ丸める。"""
    spec_by_path = {p["path"]: p for p in _auto_specs()}
    out: dict[str, Any] = {}
    for path, v in values.items():
        spec = spec_by_path.get(path)
        if spec is None:
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            continue
        cur = get_by_path(cfg, path)
        try:
            cur_f = float(cur)
        except (TypeError, ValueError):
            cur_f = None
        if cur_f is not None and cur_f > 0:
            lo = cur_f * (1.0 - max_pct / 100.0)
            hi = cur_f * (1.0 + max_pct / 100.0)
            num = min(max(num, lo), hi)
        num = min(max(num, float(spec["min"])), float(spec["max"]))
        out[path] = int(round(num)) if spec["type"] == "int" else round(num, 6)
    return out


def _news_section(news_block: str) -> str:
    if not news_block:
        return ""
    return f"\n【経済指標カレンダー(高インパクトのみ、実際の値動きへの影響は未確定)】\n{news_block}\n"


def _propose(
    cfg: dict[str, Any], stats: dict[str, Any], at_cfg: dict[str, Any], news_block: str = ""
) -> dict[str, Any]:
    n_cand = int(at_cfg.get("candidates", 4))
    max_pct = float(at_cfg.get("max_change_pct", 25))
    iv_label = interval_label_ja(str(cfg.get("intervals", {}).get("signal", "15m")))
    prompt = f"""
あなたはBTC永久先物Bot({iv_label}、ATRベースTP/SL、ロング/ショート両対応)のパラメータ最適化担当です。
直近成績を踏まえ、バックテスト検証にかける候補パラメータセットを最大{n_cand}個提案してください。

【変更してよいパラメータ】
{_params_block(cfg, max_pct)}

【直近の成績】
{_stats_block(stats)}
{_news_section(news_block)}
【方針】
- 手数料を払う短期売買では取引の質(1回あたり期待値)が最優先。損切り比率が高い場合は
  「厳選(閾値・確信度を上げる)」と「損益レンジの見直し(TP/SL比)」の両方向を試す。
- 大きな方向転換より小さな着実な調整。各候補は1〜3個のパラメータだけ変える。
- 候補同士は異なる仮説を表すこと(全部同じ方向の微修正にしない)。
- 経済指標カレンダーに直近〜今後の高インパクト指標があれば考慮してよい(例: 重要指標を
  控えている場合は entry_threshold / min_confidence を厳選方向にする、max_hold_bars を
  短くして指標発表を跨ぎにくくする、など)。ただし指標が無ければ無理に絡めなくてよい。

【出力】JSONのみ。コードフェンス不可:
{{"candidates": [{{"name": "候補の短い名前", "values": {{"combine.entry_threshold": 0.12}},
  "rationale": "仮説の説明(日本語60字以内)"}}],
 "comment": "全体方針(日本語100字以内)"}}
values には変更するキーだけを含めてください。
"""
    model = str(at_cfg.get("model", "sonnet"))
    timeout = int(at_cfg.get("timeout_seconds", 300))
    return extract_json(run_claude(prompt, model=model, timeout=timeout))


def _review(
    candidates: list[dict[str, Any]], stats: dict[str, Any], at_cfg: dict[str, Any], news_block: str = ""
) -> dict[str, dict[str, str]]:
    """リスク審査役。候補ごとに approve / veto を返す。失敗時は全件 approve 扱い。"""
    cand_txt = "\n".join(
        f"- {c['name']}: {json.dumps(c.get('values', {}), ensure_ascii=False)} — {c.get('rationale', '')}"
        for c in candidates
    )
    prompt = f"""
あなたはトレーディングBotのリスク管理責任者です。提案された設定変更候補を審査してください。

【直近の成績】
{_stats_block(stats)}

【候補】
{cand_txt}
{_news_section(news_block)}
【審査基準】
- 損切り幅(sl_atr_mult)の大幅拡大、保有時間(max_hold_bars)の大幅延長、
  エントリー条件の大幅緩和(閾値・確信度の同時大幅引き下げ)など、
  一度の負けを大きくする/負け頻度を上げる方向の複合変更は veto。
- 直近〜今後数日に高インパクト指標が控えている場合、その状況でエントリー条件を
  緩める方向の候補はより慎重に見る(ボラティリティ急変で損切りが連続しやすいため)。
  ただし指標が無ければこの観点は無視してよい。
- 判断がつかない場合は approve(検証はバックテストが行う)。

【出力】JSONのみ:
{{"reviews": [{{"name": "候補の名前", "verdict": "approve" または "veto", "reason": "日本語40字以内"}}]}}
"""
    model = str(at_cfg.get("reviewer_model", "sonnet"))
    timeout = int(at_cfg.get("timeout_seconds", 300))
    try:
        raw = extract_json(run_claude(prompt, model=model, timeout=timeout))
        return {
            str(r.get("name")): {
                "verdict": "veto" if str(r.get("verdict", "approve")).lower() == "veto" else "approve",
                "reason": str(r.get("reason", ""))[:120],
            }
            for r in raw.get("reviews", [])
            if isinstance(r, dict)
        }
    except Exception as e:
        print(f"[auto_tune] reviewer failed (treating all as approve): {str(e)[:120]}")
        return {}


def _score(summary: dict[str, Any], q0: float) -> float:
    return float(summary.get("total_pnl", 0.0)) - 0.5 * abs(float(summary.get("max_drawdown", 0.0))) * q0


def _append_history(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _fmt_values(values: dict[str, Any]) -> str:
    return ", ".join(f"{k.split('.')[-1]}={v}" for k, v in values.items()) or "なし"


def _maybe_rollback(
    cfg: dict[str, Any], at_cfg: dict[str, Any], history_path: Path, log_path: Path
) -> dict[str, Any] | None:
    """直近の適用が実運用で裏目に出ていたら前回値へ戻す。戻したら履歴レコードを返す。"""
    hist = tail_jsonl(history_path, max_lines=50)
    last_applied = None
    for rec in reversed(hist):
        if rec.get("type") == "rollback":
            return None  # 直近がロールバックなら再度は戻さない
        if rec.get("type") == "tune" and rec.get("applied") and not rec.get("dry_run"):
            last_applied = rec
            break
    if not last_applied:
        return None
    days_since = (_utc_ms() - int(last_applied["t"])) / 86_400_000
    wait_days = float(at_cfg.get("rollback_after_days", 2))
    if days_since < wait_days:
        return None
    records = tail_jsonl(log_path, max_lines=16000)
    trades, _ = build_trades(records)
    live_pnl = sum(
        t.get("pnl") or 0 for t in trades if (t.get("exit_time") or 0) >= int(last_applied["t"])
    )
    q0 = float(cfg.get("backtest", {}).get("initial_quote", 10000.0))
    threshold = -float(at_cfg.get("rollback_loss_pct", 0.02)) * q0
    if live_pnl > threshold:
        return None
    old_values = last_applied.get("old_values") or {}
    values, err = validate_values(old_values, auto_only=True)
    if err or not values:
        return None
    write_local_overrides(package_root() / "config.local.yaml", values, source="auto_tune rollback")
    rec = {
        "t": _utc_ms(), "type": "rollback",
        "reverted_to": values, "from_values": last_applied.get("values"),
        "live_pnl_since_change": round(live_pnl, 2), "threshold": round(threshold, 2),
        "days_since_change": round(days_since, 1),
    }
    _append_history(history_path, rec)
    return rec


def run_auto_tune(
    cfg: dict[str, Any] | None = None, dry_run: bool = False, notify: bool = True
) -> dict[str, Any]:
    cfg = cfg or load_config()
    at_cfg = cfg.get("auto_tune") or {}
    root = package_root()
    history_path = root / HISTORY_REL
    log_path = root / cfg.get("logging", {}).get("jsonl_path", "data/paper_events.jsonl")
    q0 = float(cfg.get("backtest", {}).get("initial_quote", 10000.0))
    eval_days = int(at_cfg.get("eval_days", 14))
    max_pct = float(at_cfg.get("max_change_pct", 25))
    min_trades_floor = int(at_cfg.get("min_trades_floor", 5))
    bph = bars_per_hour(str(cfg["intervals"]["signal"]))

    entry_mode = str(cfg.get("entry_mode", "regression"))
    if entry_mode != "regression":
        rec = {
            "t": _utc_ms(), "type": "tune", "applied": False, "dry_run": dry_run,
            "reason": f"entry_mode={entry_mode} のため回帰パラメータのチューニング対象外",
        }
        _append_history(history_path, rec)
        if notify:
            post_daily_summary(
                f"➖ 自動チューニング: entry_mode={entry_mode} のため回帰ベースのパラメータは"
                "チューニング対象外としてスキップしました。"
            )
        return {"action": "skipped_entry_mode", **rec}

    # ⑥ まずロールバック判定（戻した日は新たな変更をしない）
    rb = None if dry_run else _maybe_rollback(cfg, at_cfg, history_path, log_path)
    if rb is not None:
        if notify:
            post_daily_summary(
                f"⏪ 自動ロールバック: 前回の設定変更後 {rb['days_since_change']}日 の実現PnLが "
                f"{rb['live_pnl_since_change']:.2f} USDT（閾値 {rb['threshold']:.2f}）だったため、"
                "変更前の設定に戻しました。",
                fields=[{"name": "復元した設定", "value": _fmt_values(rb["reverted_to"])[:1000], "inline": False}],
            )
        return {"action": "rollback", **rb}

    # ① 診断
    days = 7
    since = _utc_ms() - days * 86_400_000
    records = tail_jsonl(log_path, max_lines=min(16000, int(days * 24 * bph) + 800))
    stats = period_stats(records, since, days)
    news_block = build_news_context_block(cfg)

    # ② 仮説
    proposal = _propose(cfg, stats, at_cfg, news_block)
    raw_candidates = [c for c in proposal.get("candidates", []) if isinstance(c, dict)]
    candidates: list[dict[str, Any]] = []
    current = _current_auto_values(cfg)
    for c in raw_candidates:
        clamped = _clamp_to_delta(c.get("values") or {}, cfg, max_pct)
        values, err = validate_values(clamped, auto_only=True)
        if err or not values:
            continue
        # 現在値と実質同じ候補は捨てる
        changed = {k: v for k, v in values.items() if k in current and v != current[k]}
        if not changed:
            continue
        candidates.append({"name": str(c.get("name", f"cand{len(candidates)+1}"))[:40],
                           "values": values, "rationale": str(c.get("rationale", ""))[:200]})
    if not candidates:
        rec = {"t": _utc_ms(), "type": "tune", "applied": False, "dry_run": dry_run,
               "reason": "有効な候補なし", "stats": stats,
               "comment": str(proposal.get("comment", ""))[:300]}
        _append_history(history_path, rec)
        return {"action": "no_candidates", **rec}

    # ③ リスク審査
    reviews = _review(candidates, stats, at_cfg, news_block)
    survivors = []
    for c in candidates:
        r = reviews.get(c["name"], {"verdict": "approve", "reason": ""})
        c["review"] = r
        if r["verdict"] == "approve":
            survivors.append(c)

    # ④ 検証（現行 + 生き残り候補を同条件で）
    from ..backtest.whatif import run_what_if

    eval_bars = int(eval_days * 24 * bph)
    baseline = run_what_if(cfg, {}, eval_bars=eval_bars)
    baseline_score = _score(baseline["summary"], q0)
    for c in survivors:
        try:
            res = run_what_if(cfg, values_to_nested(c["values"]), eval_bars=eval_bars)
            c["summary"] = _summary_lite(res["summary"])
            c["score"] = _score(res["summary"], q0)
        except Exception as e:
            c["summary"] = None
            c["score"] = float("-inf")
            c["error"] = str(e)[:150]

    # ⑤ 選抜と適用（ノイズ程度の差で毎日設定が揺れないよう最低改善マージンを要求）
    min_improve = float(at_cfg.get("min_improvement_pct", 0.5)) / 100.0 * q0
    ranked = sorted([c for c in survivors if c.get("summary")], key=lambda c: c["score"], reverse=True)
    winner = None
    for c in ranked:
        if (
            c["score"] > baseline_score + min_improve
            and int(c["summary"].get("n_trades") or 0) >= min_trades_floor
        ):
            winner = c
            break

    applied = False
    if winner and not dry_run:
        old_values = {k: current.get(k) for k in winner["values"] if k in current}
        write_local_overrides(root / "config.local.yaml", winner["values"], source="auto_tune")
        applied = True
    else:
        old_values = {}

    rec = {
        "t": _utc_ms(), "type": "tune", "applied": applied, "dry_run": dry_run,
        "stats": stats, "comment": str(proposal.get("comment", ""))[:300],
        "baseline": _summary_lite(baseline["summary"]), "baseline_score": round(baseline_score, 2),
        "candidates": [
            {"name": c["name"], "values": c["values"], "rationale": c["rationale"],
             "review": c.get("review"), "summary": c.get("summary"),
             "score": (round(c["score"], 2) if c.get("score", float("-inf")) != float("-inf") else None)}
            for c in candidates
        ],
        "winner": winner["name"] if winner else None,
        "values": winner["values"] if winner else None,
        "old_values": old_values,
        "eval_days": eval_days,
        "news_context": news_block[:2000] if news_block else None,
    }
    _append_history(history_path, rec)

    if notify:
        if winner:
            ws = winner["summary"]
            title = ("✅ 自動チューニング適用" if applied else "🧪 自動チューニング（dry-run: 適用せず）")
            post_daily_summary(
                f"{title}: 「{winner['name']}」が現行設定に勝ったため"
                + ("適用しました。" if applied else "採用候補になりました。"),
                fields=[
                    {"name": "変更", "value": _fmt_values(winner["values"])[:1000], "inline": False},
                    {"name": "仮説", "value": winner["rationale"][:1000] or "—", "inline": False},
                    {"name": f"検証({eval_days}日) 現行", "value": f"PnL {baseline['summary']['total_pnl']:.2f} / 取引 {baseline['summary']['n_trades']} / PF {baseline['summary']['profit_factor']:.2f}", "inline": True},
                    {"name": "採用候補", "value": f"PnL {ws['total_pnl']:.2f} / 取引 {ws['n_trades']} / PF {ws['profit_factor']:.2f}", "inline": True},
                ],
            )
        else:
            post_daily_summary(
                f"➖ 自動チューニング: {len(candidates)}候補を検証しましたが、"
                f"現行設定に明確に勝る候補が無かったため現状維持しました。",
                fields=[
                    {"name": f"検証({eval_days}日) 現行", "value": f"PnL {baseline['summary']['total_pnl']:.2f} / 取引 {baseline['summary']['n_trades']}", "inline": True},
                    {"name": "方針コメント", "value": (str(proposal.get("comment", "")) or "—")[:1000], "inline": False},
                ],
            )
    return {"action": "applied" if applied else ("winner_dry_run" if winner else "keep"), **rec}

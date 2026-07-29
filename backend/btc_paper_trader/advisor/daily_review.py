"""案2: Claude による日次トレードレビュー。

その日の paper_events.jsonl（約定・損切り・見送り理由）を集計して Claude に渡し、
「何が機能して何が機能しなかったか」「パラメータ改善案」を Discord にレポートする。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import package_root
from ..data.binance_futures import interval_label_ja
from ..notify.discord import post_daily_summary
from .claude_cli import extract_json, find_cli, run_claude


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _collect_day(cfg: dict[str, Any], day_key: str) -> dict[str, Any] | None:
    """paper_events.jsonl から指定日 (UTC) のレコードを集計する"""
    log_path = package_root() / cfg["logging"]["jsonl_path"]
    if not log_path.exists():
        return None

    pnls: list[float] = []
    entries: list[dict[str, Any]] = []
    block_reasons: dict[str, int] = {}
    regime_counts: dict[str, int] = {}
    advice_events: list[dict[str, Any]] = []
    quotes: list[float] = []

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("t")
            if not isinstance(ts, (int, float)):
                continue
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if day != day_key:
                continue
            if isinstance(rec.get("quote"), (int, float)):
                quotes.append(float(rec["quote"]))
            for e in rec.get("events", []):
                etype = e.get("type")
                if etype == "entry":
                    entries.append(e)
                elif etype == "decision":
                    regime = str(e.get("regime", "unknown"))
                    regime_counts[regime] = regime_counts.get(regime, 0) + 1
                    if int(e.get("signal", 0)) != 0 and int(e.get("pending_after_guard", 0)) == 0:
                        r = str(e.get("reason", "unknown"))
                        block_reasons[r] = block_reasons.get(r, 0) + 1
                elif etype == "claude_advice":
                    advice_events.append(e)
                if "pnl" in e:
                    try:
                        pnls.append(float(e["pnl"]))
                    except (TypeError, ValueError):
                        pass

    if not quotes and not pnls and not entries:
        return None

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    long_entries = sum(1 for e in entries if int(e.get("side", 0)) == 1)
    short_entries = sum(1 for e in entries if int(e.get("side", 0)) == -1)
    vetoes = sum(1 for a in advice_events if a.get("verdict") == "veto")

    return {
        "day": day_key,
        "quote_start": quotes[0] if quotes else None,
        "quote_end": quotes[-1] if quotes else None,
        "n_trades": len(pnls),
        "total_pnl": sum(pnls),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "n_entries_long": long_entries,
        "n_entries_short": short_entries,
        "block_reasons": dict(sorted(block_reasons.items(), key=lambda x: x[1], reverse=True)[:8]),
        "regime_counts": regime_counts,
        "n_claude_advice": len(advice_events),
        "n_claude_veto": vetoes,
    }


def _params_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    """Claude に渡す現在の主要パラメータ（改善提案の材料）"""
    risk = cfg.get("risk") or {}
    filters = cfg.get("filters") or {}
    combine = cfg.get("combine") or {}
    grid = cfg.get("grid") or {}
    return {
        "entry_threshold": combine.get("entry_threshold"),
        "min_confidence": filters.get("min_confidence"),
        "funding_long_block_above": filters.get("funding_long_block_above"),
        "funding_short_block_below": filters.get("funding_short_block_below"),
        "tp_atr_mult": risk.get("tp_atr_mult"),
        "sl_atr_mult": risk.get("sl_atr_mult"),
        "max_hold_bars": risk.get("max_hold_bars"),
        "max_daily_loss_pct": risk.get("max_daily_loss_pct"),
        "cooldown_after_losses": risk.get("cooldown_after_losses"),
        "grid_step_atr_mult": grid.get("step_atr_mult"),
        "grid_deadband_atr_mult": grid.get("deadband_atr_mult"),
    }


def run_daily_review(cfg: dict[str, Any], day_key: str | None = None, post: bool = True) -> dict[str, Any] | None:
    """指定日 (UTC, 省略時は昨日) のトレードを Claude にレビューさせる"""
    day_key = day_key or _yesterday_utc()
    dr_cfg = cfg.get("daily_review") or {}

    if not find_cli():
        print("[daily_review] claude CLI not found; skip")
        return None

    stats = _collect_day(cfg, day_key)
    if stats is None:
        print(f"[daily_review] no records for {day_key}; skip")
        return None

    iv_label = interval_label_ja(str(cfg.get("intervals", {}).get("signal", "15m")))
    prompt = f"""
あなたはBTC先物(USDT-M)の自動売買システムのトレードコーチです。
以下は当システムの {day_key} (UTC) のペーパートレード実績です。
データに基づいて具体的にレビューしてください。一般論は避けてください。

【当日の実績】
{json.dumps(stats, ensure_ascii=False, indent=2)}

【現在の主要パラメータ】
{json.dumps(_params_snapshot(cfg), ensure_ascii=False, indent=2)}

【補足】
- block_reasons はシグナルが出たがフィルターで見送った理由の集計。
- n_claude_veto はAIセカンドオピニオンが拒否推奨した件数。
- 戦略は{iv_label}ベース。トレンド時はモデル+パターン合成、レンジ時はグリッド逆張り。

【出力要件】
以下の要素を持つJSONのみを出力してください（日本語）。JSON以外の文章やコードフェンスは含めないでください:
"summary": "当日の総評（200文字以内）"
"what_worked": "機能した点（250文字以内。データの数値を引用すること）"
"issues": "課題・機能しなかった点（250文字以内。データの数値を引用すること）"
"suggestions": ["具体的なパラメータ調整案や検証すべき仮説（各120文字以内、最大3件）"]
"tomorrow_focus": "翌日に注目すべきポイント（120文字以内）"
"""

    model = str(dr_cfg.get("model", "sonnet"))
    timeout = int(dr_cfg.get("timeout_seconds", 300))
    try:
        text = run_claude(prompt, model=model, timeout=timeout)
        review = extract_json(text)
    except Exception as e:
        print(f"[daily_review] claude review failed: {str(e)[:150]}")
        return None

    if post:
        suggestions = review.get("suggestions") or []
        if isinstance(suggestions, str):
            suggestions = [suggestions]
        fields = [
            {"name": "✅ 機能した点", "value": str(review.get("what_worked", "-"))[:1000], "inline": False},
            {"name": "⚠️ 課題", "value": str(review.get("issues", "-"))[:1000], "inline": False},
            {
                "name": "💡 改善提案",
                "value": ("\n".join(f"- {s}" for s in suggestions) or "-")[:1000],
                "inline": False,
            },
            {"name": "🔭 翌日の注目点", "value": str(review.get("tomorrow_focus", "-"))[:1000], "inline": False},
        ]
        post_daily_summary(
            f"🤖 **Claude 日次レビュー ({day_key} UTC)**\n{str(review.get('summary', ''))[:1500]}",
            fields=fields,
        )
    return review

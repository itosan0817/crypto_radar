"""ダッシュボードの設定項目に対する Claude の参考値提案。

現在の設定値と直近の取引成績を渡し、編集可能な各パラメータについて
推奨値と理由を JSON で受け取る。結果は参考表示のみで、適用は人が行う。
"""
from __future__ import annotations

import time
from typing import Any

from ..data.binance_futures import interval_label_ja
from .claude_cli import extract_json, run_claude


def _params_block(params: list[dict[str, Any]]) -> str:
    lines = []
    for p in params:
        if p.get("type") == "choice":
            rng = "選択肢: " + " / ".join(p.get("choices", []))
        else:
            rng = f"範囲: {p.get('min')}〜{p.get('max')}"
        help_txt = f" — {p['help']}" if p.get("help") else ""
        lines.append(f"- {p['path']} ({p['label']}{help_txt}) 現在値: {p.get('value')} {rng}")
    return "\n".join(lines)


def _stats_block(stats: dict[str, Any]) -> str:
    if not stats or not stats.get("n_trades"):
        return "直近の取引データはありません（取引ゼロ）。"
    reason_txt = ", ".join(f"{k}:{v}" for k, v in (stats.get("exit_reasons") or {}).items()) or "なし"
    block_txt = ", ".join(f"{k}:{v}" for k, v in (stats.get("block_reasons") or {}).items()) or "なし"
    return (
        f"対象期間: 直近{stats.get('days', '?')}日\n"
        f"取引数: {stats.get('n_trades')} / 勝率: {stats.get('win_rate', 0):.1%} / "
        f"合計PnL: {stats.get('total_pnl', 0):.2f} USDT\n"
        f"平均利益: {stats.get('avg_win', 0):.2f} / 平均損失: {stats.get('avg_loss', 0):.2f}\n"
        f"決済理由の内訳: {reason_txt}\n"
        f"エントリー見送り理由の上位: {block_txt}"
    )


def advise_params(
    cfg: dict[str, Any],
    params: list[dict[str, Any]],
    stats: dict[str, Any],
) -> dict[str, Any]:
    """編集可能パラメータ一覧と成績サマリを Claude に渡し、参考値を得る。

    戻り値: {"suggestions": {path: {"value": v, "reason": str}}, "comment": str, ...}
    提案が検証（範囲・選択肢）を通らない場合、その項目は落とす。
    """
    adv_cfg = cfg.get("param_advisor") or {}
    model = str(adv_cfg.get("model", "sonnet"))
    timeout = int(adv_cfg.get("timeout_seconds", 240))

    iv_label = interval_label_ja(str(cfg.get("intervals", {}).get("signal", "15m")))
    prompt = f"""
あなたはBTC永久先物(USDT-M)の売買Bot({iv_label}、ロング/ショート両対応、ATRベースのTP/SL)の
パラメータチューニング専門家です。ペーパートレーディングの直近成績を踏まえ、
編集可能な各パラメータの推奨値を提案してください。

【編集可能なパラメータ】
{_params_block(params)}

【直近の成績】
{_stats_block(stats)}

【提案の方針】
- 成績データが少ない場合は大きな変更を避け、現在値近辺を推奨する。
- 損切り(sl)ばかりで負けている場合は、エントリーの厳選(閾値/確信度を上げる)や損切り幅の見直しを検討。
- 取引がほとんど無い場合は、閾値/確信度を下げる方向を検討。
- 各推奨は必ず範囲内の値にすること。

【出力要件】
以下のJSONのみを出力してください。JSON以外の文章やコードフェンスは含めないでください:
{{"suggestions": [{{"path": "パラメータのpath", "value": 推奨値, "reason": "理由(日本語40文字以内)"}}, ...],
 "comment": "全体方針のまとめ(日本語120文字以内)"}}
suggestions には編集可能な全パラメータを含め、変更不要なものは現在値をそのまま入れてください。
"""

    t0 = time.monotonic()
    text = run_claude(prompt, model=model, timeout=timeout)
    raw = extract_json(text)
    latency = time.monotonic() - t0

    spec_by_path = {p["path"]: p for p in params}
    suggestions: dict[str, dict[str, Any]] = {}
    for s in raw.get("suggestions", []):
        if not isinstance(s, dict):
            continue
        path = str(s.get("path", ""))
        spec = spec_by_path.get(path)
        if spec is None:
            continue
        value = s.get("value")
        if spec.get("type") == "choice":
            if value not in spec.get("choices", []):
                continue
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value < float(spec["min"]) or value > float(spec["max"]):
                continue
            if spec.get("type") == "int":
                value = int(round(value))
        suggestions[path] = {"value": value, "reason": str(s.get("reason", ""))[:120]}

    return {
        "suggestions": suggestions,
        "comment": str(raw.get("comment", ""))[:400],
        "model": model,
        "latency_sec": round(latency, 1),
    }

from __future__ import annotations

import time
from typing import Any

import requests

from ..config import env_webhook_daily, env_webhook_hourly


def _post_webhook(url: str, payload: dict[str, Any], retries: int = 4) -> None:
    """失敗時は理由(ステータスコード/レスポンス本文/例外)を必ず1行ログに残す。
    以前は成功するまで無言でリトライし、諦める時も何も出力していなかったため、
    通知が届かない原因（Discord側の4xx/5xx等）を後から一切追跡できなかった。"""
    if not url:
        return
    last_reason = "unknown"
    backoff = 1.0
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code in (200, 204):
                return
            last_reason = f"HTTP {r.status_code}: {r.text[:300]}"
        except requests.RequestException as e:
            last_reason = f"{type(e).__name__}: {str(e)[:300]}"
        time.sleep(backoff)
        backoff = min(backoff * 2.0, 30.0)
    print(f"[discord] webhook post failed after {retries} attempts: {last_reason}")


def _normalize_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not fields:
        return []
    if isinstance(fields[0], dict) and "name" in fields[0]:
        return fields[:25]
    return [{"name": k, "value": str(v)[:1000], "inline": True} for k, v in fields[:20]]


def post_hourly_summary(
    text: str,
    fields: list[dict[str, Any]] | None = None,
) -> None:
    url = env_webhook_hourly()
    if not url:
        return
    embed: dict[str, Any] = {"title": "BTC Paper — 直近1時間", "description": text[:1800]}
    if fields:
        embed["fields"] = _normalize_fields(fields)
    payload = {"embeds": [embed]}
    _post_webhook(url, payload)


def post_daily_summary(
    text: str,
    fields: list[dict[str, Any]] | None = None,
) -> None:
    url = env_webhook_daily()
    if not url:
        return
    embed: dict[str, Any] = {"title": "BTC Paper — 日次まとめ", "description": text[:1800]}
    if fields:
        embed["fields"] = _normalize_fields(fields)
    payload = {"embeds": [embed]}
    _post_webhook(url, payload)


def post_claude_advice(advice: dict[str, Any], blocked: bool, mode: str) -> None:
    """エントリー候補への Claude セカンドオピニオンを毎時チャンネルへ通知する"""
    url = env_webhook_hourly()
    if not url:
        return
    side_ja = "ロング" if int(advice.get("side", 0)) == 1 else "ショート"
    verdict = advice.get("verdict", "approve")
    if blocked:
        title_emoji, verdict_ja = "🛑", "拒否（エントリー中止）"
    elif verdict == "veto":
        title_emoji, verdict_ja = "⚠️", "拒否推奨（advise モードのためエントリーは続行）"
    else:
        title_emoji, verdict_ja = "✅", "承認"
    embed: dict[str, Any] = {
        "title": f"{title_emoji} Claude セカンドオピニオン — {side_ja}候補",
        "description": f"判定: **{verdict_ja}** (自信度 {advice.get('advice_confidence', '-')}%)",
        "fields": [
            {"name": "判断理由", "value": str(advice.get("advice_reason", "-"))[:1000], "inline": False},
            {"name": "注意点", "value": str(advice.get("advice_caution", "-") or "-")[:1000], "inline": False},
            {
                "name": "シグナル情報",
                "value": (
                    f"根拠: {advice.get('signal_reason', '-')} / レジーム: {advice.get('regime', '-')} / "
                    f"システム確信度: {advice.get('system_confidence', 0):.3f}"
                )[:1000],
                "inline": False,
            },
        ],
        "footer": {"text": f"mode={mode} / model={advice.get('model', '-')} / {advice.get('latency_sec', '-')}s"},
    }
    _post_webhook(url, {"embeds": [embed]})


def post_claude_native_signal(
    raw: dict[str, Any],
    entered: bool,
    skip_reason: str | None,
    threshold: float,
    state_summary: dict[str, Any],
) -> None:
    """entry_mode=claude_native: 毎時(=毎バー確定ごと)のClaude判断を1通で通知する"""
    url = env_webhook_hourly()
    if not url:
        return
    direction = str(raw.get("direction", "flat"))
    dir_ja = {"long": "ロング", "short": "ショート", "flat": "様子見"}.get(direction, direction)
    confidence = raw.get("confidence", 0)
    if entered:
        title_emoji, status_ja = "✅", "エントリーしました"
    elif direction == "flat":
        title_emoji, status_ja = "💤", "様子見（Claude自身の判断）"
    else:
        reason_ja = {
            "claude_below_threshold": f"自信度が閾値({threshold:.0f}%)未満のため見送り",
            "risk_guard_block": "日次損失ガードにより新規停止中",
            "cooldown": "連敗クールダウン中",
        }.get(skip_reason or "", skip_reason or "見送り")
        title_emoji, status_ja = "⏸️", reason_ja
    embed: dict[str, Any] = {
        "title": f"{title_emoji} Claude判断 — {dir_ja} (自信度 {confidence}%)",
        "description": f"**{status_ja}**",
        "fields": [
            {"name": "判断理由", "value": str(raw.get("reasoning", "-"))[:1000], "inline": False},
            {
                "name": "口座状況",
                "value": (
                    f"評価額: {state_summary.get('quote', 0):.2f} USDT / "
                    f"本日実現PnL: {state_summary.get('daily_pnl', 0):.2f} / "
                    f"現在ポジション: {state_summary.get('position', 'FLAT')}"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": f"model={raw.get('model', '-')} / {raw.get('latency_sec', '-')}s / 閾値={threshold:.0f}%"},
    }
    _post_webhook(url, {"embeds": [embed]})


def post_claude_native_failure_alert(
    consecutive_failures: int,
    last_error: str | None,
    recovered: bool,
) -> None:
    """entry_mode=claude_native: Claude呼び出しが連続失敗/復旧した時にアラートする
    （使用上限到達などで新規エントリー判断が止まっていることに気づけるようにするため）"""
    url = env_webhook_hourly()
    if not url:
        return
    if recovered:
        embed: dict[str, Any] = {
            "title": "✅ Claude呼び出しが復旧しました",
            "description": f"直前まで {consecutive_failures} 回連続で失敗していましたが、正常に応答が返るようになりました。",
        }
    else:
        embed = {
            "title": "🚨 Claude呼び出しが連続で失敗しています",
            "description": (
                f"**{consecutive_failures}回連続**で失敗中です。使用上限（サブスクリプションの利用上限）に"
                "到達している可能性があります。この間、新規エントリー判断は行われません"
                "（保有中ポジションのTP/SL監視は影響を受けず継続します）。"
            ),
            "fields": [
                {"name": "直近のエラー", "value": str(last_error or "-")[:1000], "inline": False},
            ],
        }
    _post_webhook(url, {"embeds": [embed]})


def post_tune_result(
    text: str,
    fields: list[dict[str, Any]] | None = None,
) -> None:
    """Notify daily webhook about automated tune outcome (uses DAILY webhook)."""
    url = env_webhook_daily()
    if not url:
        return
    embed: dict[str, Any] = {"title": "BTC Paper — 自動 tune", "description": text[:1800]}
    if fields:
        embed["fields"] = _normalize_fields(fields)
    payload = {"embeds": [embed]}
    _post_webhook(url, payload)



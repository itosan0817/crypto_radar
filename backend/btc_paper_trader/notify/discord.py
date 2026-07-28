from __future__ import annotations

import time
from typing import Any

import requests

from ..config import env_webhook_daily, env_webhook_hourly


def _post_webhook(url: str, payload: dict[str, Any], retries: int = 4) -> None:
    if not url:
        return
    backoff = 1.0
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code in (200, 204):
                return
        except requests.RequestException:
            pass
        time.sleep(backoff)
        backoff = min(backoff * 2.0, 30.0)


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



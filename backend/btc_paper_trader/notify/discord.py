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



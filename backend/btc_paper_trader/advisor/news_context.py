"""自動チューニング(auto_tune)向け: 直近〜今後の経済指標カレンダーをテキスト要約する。

news_filter.live_sync と同じカレンダーソースを使うが、こちらは1日1回しか
呼ばれないため専用の軽い取得のみで、engine.py 側のバー単位キャッシュとは共有しない。
取得失敗時は空文字を返し、呼び出し側はニュースセクションを省略する
（auto_tune の実行自体を失敗させない）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests


def _parse_date(value: str) -> datetime | None:
    try:
        ts = pd.to_datetime(value, utc=True)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.to_pydatetime()


def build_news_context_block(cfg: dict[str, Any]) -> str:
    """審査役/提案役プロンプトに差し込む経済指標カレンダーのテキストブロックを作る。"""
    sync = ((cfg.get("news_filter") or {}).get("live_sync")) or {}
    nc = (cfg.get("auto_tune") or {}).get("news_context") or {}
    if not bool(nc.get("enabled", True)) or not bool(sync.get("enabled", False)):
        return ""
    url = str(sync.get("calendar_url", "")).strip()
    if not url:
        return ""

    timeout_sec = float(sync.get("request_timeout_seconds", 8))
    try:
        r = requests.get(url, timeout=timeout_sec)
        r.raise_for_status()
        raw = r.json()
    except Exception:
        return ""
    if not isinstance(raw, list):
        return ""

    allowed_impacts = {str(x).lower() for x in nc.get("impacts", ["High"])}
    allowed_countries = {str(x).upper() for x in nc.get("countries", ["USD"])}
    lookback_days = float(nc.get("lookback_days", 3))
    lookahead_days = float(nc.get("lookahead_days", 7))

    now = datetime.now(timezone.utc)
    lo = now - timedelta(days=lookback_days)
    hi = now + timedelta(days=lookahead_days)

    rows: list[tuple[datetime, str]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        impact = str(e.get("impact", "")).lower()
        country = str(e.get("country", "")).upper()
        if allowed_impacts and impact not in allowed_impacts:
            continue
        if allowed_countries and country not in allowed_countries:
            continue
        dt = _parse_date(str(e.get("date", "")))
        if dt is None or not (lo <= dt <= hi):
            continue
        when = "発表済" if dt <= now else "予定"
        forecast = e.get("forecast") or "—"
        previous = e.get("previous") or "—"
        rows.append((
            dt,
            f"{dt.strftime('%m/%d %H:%M UTC')} [{when}] {country} {e.get('title', '?')} "
            f"(予想:{forecast} / 前回:{previous})",
        ))

    if not rows:
        return ""
    rows.sort(key=lambda x: x[0])
    return "\n".join(line for _, line in rows)

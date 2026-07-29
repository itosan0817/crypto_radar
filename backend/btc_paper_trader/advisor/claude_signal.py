"""案3: entry_mode=claude_native 用の「Claude自身がエントリー方向を判断する」ネイティブシグナル。

案1のセカンドオピニオン(entry_advisor.py)はルールベースの signal を承認/拒否するだけだが、
このモードではルールベースの signal 生成自体を使わず、Claude が直接
long/short/flat と自信度を判断する（回帰・パターンマッチングは一切参照しない）。

呼び出し元（paper/runner.py）が「新規建玉を検討してよいか」（フラット・クールダウン外・
新規停止でない・ライブバー）を確認した上でのみ呼ぶ。閾値判定・実際の建玉執行は
呼び出し元と engine.step_simulation の external_signal 引数が担う。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from ..config import package_root
from ..data.binance_futures import interval_label_ja
from .claude_cli import extract_json, find_cli, run_claude

RECENT_BARS = 24

_last_error: str | None = None


def get_last_error() -> str | None:
    """直近の get_claude_signal 呼び出しが失敗していた場合の理由（成功時は None）。
    連続失敗のDiscordアラートで、失敗内容をそのまま伝えるために使う。
    """
    return _last_error


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if f != f:  # NaN
        return "n/a"
    return f"{f:.{nd}f}"


def _build_snapshot(df: pd.DataFrame, i: int, iv_label: str) -> str:
    lo = max(0, i - RECENT_BARS + 1)
    rows = []
    for j in range(lo, i + 1):
        r = df.iloc[j]
        ts = datetime.fromtimestamp(int(r["m15_open_time"]) / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        rows.append(
            f"{ts} O:{_fmt(r.get('m15_open'))} H:{_fmt(r.get('m15_high'))} "
            f"L:{_fmt(r.get('m15_low'))} C:{_fmt(r.get('m15_close'))}"
        )
    row = df.iloc[i]
    indicators = (
        f"ATR比率: {_fmt(row.get('m15_atr_ratio'), 5)} / "
        f"{iv_label}自身のトレンド傾き: {_fmt(row.get('m15_slope'), 5)} / "
        f"4hトレンド傾き: {_fmt(row.get('4h_slope'), 5)} / "
        f"1dトレンド傾き: {_fmt(row.get('1d_slope'), 5)} / "
        f"Funding(8h): {_fmt(row.get('funding_rate'), 6)}"
    )
    return f"直近{RECENT_BARS}本の{iv_label} (UTC):\n" + "\n".join(rows) + "\n\n指標:\n" + indicators


def get_claude_signal(
    df: pd.DataFrame,
    i: int,
    sim_state: Any,
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """市況だけを見てClaudeにロング/ショート/様子見と自信度を判断させる。失敗時は None。"""
    global _last_error
    cn_cfg = cfg.get("claude_native") or {}
    if not find_cli():
        _last_error = "claude CLI not found"
        return None

    iv_label = interval_label_ja(str(cfg.get("intervals", {}).get("signal", "1h")))
    snapshot = _build_snapshot(df, i, iv_label)
    prompt = f"""
あなたはBTC永久先物(USDT-M)の裁量トレーダーです。ルールベースのシグナルは使わず、
以下の市況だけを見て、次の{iv_label}でロング・ショート・様子見のいずれを取るべきか
自分で判断してください。

【市況】
{snapshot}

【口座状態】
残高: {float(getattr(sim_state, 'quote', 0.0)):.2f} USDT
本日実現PnL: {float(getattr(sim_state, 'daily_pnl', 0.0)):.2f}
連敗数: {int(getattr(sim_state, 'consecutive_losses', 0))}

【判断方針】
- 根拠が薄い・方向感が読めない場合は無理にロング/ショートを選ばず様子見(flat)にすること。
- confidence は「この方向判断にどれだけ自信があるか」を0-100の整数で。様子見なら0でよい。

【出力】JSONのみ。コードフェンス不可:
{{"direction": "long" または "short" または "flat", "confidence": 0から100の整数, "reasoning": "判断理由(日本語100字以内)"}}
"""
    model = str(cn_cfg.get("model", "sonnet"))
    timeout = int(cn_cfg.get("timeout_seconds", 120))
    t0 = time.monotonic()
    try:
        text = run_claude(prompt, model=model, timeout=timeout)
        raw = extract_json(text)
    except Exception as e:
        _last_error = str(e)[:200]
        print(f"[claude_native] signal call failed: {_last_error}")
        return None
    _last_error = None
    latency = time.monotonic() - t0

    direction = str(raw.get("direction", "flat")).lower()
    if direction not in ("long", "short", "flat"):
        direction = "flat"
    try:
        confidence = int(raw.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    result = {
        "direction": direction,
        "confidence": confidence,
        "reasoning": str(raw.get("reasoning", ""))[:300],
        "model": model,
        "latency_sec": round(latency, 1),
    }
    _log_signal(cfg, df, i, result)
    return result


def _log_signal(cfg: dict[str, Any], df: pd.DataFrame, i: int, result: dict[str, Any]) -> None:
    """閾値未満の判断も含め毎回記録する。後から異なる閾値での再検証を可能にするため。"""
    cn_cfg = cfg.get("claude_native") or {}
    log_path = package_root() / str(cn_cfg.get("log_path", "data/claude_native_signal.jsonl"))
    rec = {
        "t": int(datetime.now(timezone.utc).timestamp() * 1000),
        "bar_open_time": int(df["m15_open_time"].iloc[i]),
        "close": float(df["m15_close"].iloc[i]),
        **result,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[claude_native] failed to write signal log: {e}")

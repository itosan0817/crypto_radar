"""案1: エントリー候補に対する Claude セカンドオピニオン。

パイプラインが pending (次バーでエントリー予定) を立てた直後に呼ばれ、
市況スナップショットを渡して approve / veto の判断をもらう。

mode:
  advise ... 記録と通知のみ（エントリーは通常どおり実行）
  gate   ... veto ならエントリーを中止する
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import package_root
from ..data.binance_futures import interval_label_ja
from .claude_cli import extract_json, find_cli, run_claude

RECENT_BARS = 16


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if f != f:  # NaN
        return "n/a"
    return f"{f:.{nd}f}"


def _build_snapshot(df: pd.DataFrame, i: int, iv_label: str) -> str:
    """直近バーの値動きと指標をコンパクトなテキストにする"""
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
    close = float(row.get("m15_close", 0.0) or 0.0)
    center = row.get("m15_grid_center")
    center_dist = ""
    try:
        center_f = float(center)
        if close > 0 and center_f == center_f:
            center_dist = f"{(close - center_f) / close * 100:.3f}%"
    except (TypeError, ValueError):
        pass
    indicators = (
        f"ATR比率({iv_label}): {_fmt(row.get('m15_atr_ratio'), 5)} / "
        f"{iv_label}自身のトレンド傾き: {_fmt(row.get('m15_slope'), 5)} / "
        f"4hトレンド傾き: {_fmt(row.get('4h_slope'), 5)} / "
        f"Funding(8h): {_fmt(row.get('funding_rate'), 6)} / "
        f"パターンスコア: {_fmt(row.get('pattern_score'), 3)} / "
        f"レンジ中心からの乖離: {center_dist or 'n/a'}"
    )
    return f"直近{iv_label} (UTC):\n" + "\n".join(rows) + "\n\n指標:\n" + indicators


def advise_entry(
    df: pd.DataFrame,
    i: int,
    decision: dict[str, Any],
    sim_state: Any,
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """エントリー候補について Claude の判断を得る。失敗時は None（エントリーは通常続行）。"""
    adv_cfg = cfg.get("advisor") or {}
    if not find_cli():
        return None

    side = int(decision.get("pending_after_guard", 0))
    side_ja = "ロング(買い)" if side == 1 else "ショート(売り)"
    regime = str(decision.get("regime", "unknown"))
    reason = str(decision.get("reason", "unknown"))
    confidence = float(getattr(sim_state, "pending_confidence", 0.0))
    iv_label = interval_label_ja(str(cfg.get("intervals", {}).get("signal", "15m")))

    snapshot = _build_snapshot(df, i, iv_label)
    prompt = f"""
あなたはBTC先物(USDT-M)の短期トレードのリスク管理専門家です。
ルールベースの売買システムがエントリー候補を出しました。
以下の情報から、このエントリーを承認すべきか(approve)、見送るべきか(veto)を判断してください。

【エントリー候補】
方向: {side_ja}
シグナル根拠: {reason} (レジーム判定: {regime})
システム確信度: {confidence:.3f}
執行予定: 次の{iv_label}の始値で成行

【市況スナップショット】
{snapshot}

【口座状態】
残高: {float(getattr(sim_state, 'quote', 0.0)):.2f} USDT
本日実現PnL: {float(getattr(sim_state, 'daily_pnl', 0.0)):.2f}
連敗数: {int(getattr(sim_state, 'consecutive_losses', 0))}

【判断基準】
- 明確な逆行サイン（直近の急激な逆方向の値動き、Funding過熱、ボラ急拡大など）がある場合のみ veto。
- 判断がつかない場合は approve（ルールベースの判断を尊重する）。

【出力要件】
以下のJSONのみを出力してください。JSON以外の文章やコードフェンスは含めないでください:
"verdict": "approve" または "veto"
"confidence": 0-100の整数（この判断への自信度）
"reason": "判断理由（日本語120文字以内）"
"caution": "エントリーする場合に注意すべき価格帯や条件（日本語80文字以内）"
"""

    model = str(adv_cfg.get("model", "haiku"))
    timeout = int(adv_cfg.get("timeout_seconds", 120))
    t0 = time.monotonic()
    try:
        text = run_claude(prompt, model=model, timeout=timeout)
        result = extract_json(text)
    except Exception as e:
        print(f"[advisor] claude advice failed: {str(e)[:150]}")
        return None
    latency = time.monotonic() - t0

    advice = {
        "verdict": "veto" if str(result.get("verdict", "approve")).lower() == "veto" else "approve",
        "advice_confidence": int(result.get("confidence", 50)),
        "advice_reason": str(result.get("reason", ""))[:300],
        "advice_caution": str(result.get("caution", ""))[:200],
        "side": side,
        "signal_reason": reason,
        "regime": regime,
        "system_confidence": confidence,
        "model": model,
        "latency_sec": round(latency, 1),
    }
    _log_advice(cfg, df, i, advice)
    return advice


def _log_advice(cfg: dict[str, Any], df: pd.DataFrame, i: int, advice: dict[str, Any]) -> None:
    """後から的中率を検証できるよう jsonl に記録する"""
    adv_cfg = cfg.get("advisor") or {}
    log_path = package_root() / str(adv_cfg.get("log_path", "data/claude_advice.jsonl"))
    rec = {
        "t": int(datetime.now(timezone.utc).timestamp() * 1000),
        "bar_open_time": int(df["m15_open_time"].iloc[i]),
        "close": float(df["m15_close"].iloc[i]),
        "mode": str(adv_cfg.get("mode", "advise")),
        **advice,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[advisor] failed to write advice log: {e}")

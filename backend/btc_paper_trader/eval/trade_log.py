"""paper_events.jsonl（および同形の what-if 結果）を読むための共用ヘルパー。

ダッシュボードと Claude 自動チューナーの両方から使う。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def tail_jsonl(path: Path, max_lines: int = 200) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    size = path.stat().st_size
    chunk = min(8_000_000, size)
    with open(path, "rb") as f:
        f.seek(-chunk, 2)
        raw = f.read().decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    out: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build_trades(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """イベントレコード列からエントリー/決済をペアリングして取引一覧を作る。

    records は paper_events.jsonl の行（bar_open_time / quote / events）と同形。
    what-if の結果 (whatif.run_what_if の records) も同じ形なので共用できる。
    決済イベントは旧形式 (type なし・price なし) にも対応する。
    """
    trades: list[dict[str, Any]] = []
    open_tr: dict[str, Any] | None = None
    for rec in records:
        bar_t = rec.get("bar_open_time")
        for e in rec.get("events", []):
            etype = e.get("type")
            if etype == "entry":
                open_tr = {
                    "entry_time": bar_t,
                    "side": e.get("side"),
                    "entry_px": e.get("price"),
                    "qty": e.get("qty"),
                    "tp": e.get("tp"),
                    "sl": e.get("sl"),
                }
            elif etype == "partial_exit":
                trades.append(
                    {
                        "entry_time": open_tr.get("entry_time") if open_tr else None,
                        "entry_px": open_tr.get("entry_px") if open_tr else None,
                        "side": e.get("side"),
                        "exit_time": bar_t,
                        "exit_px": e.get("price"),
                        "pnl": e.get("pnl"),
                        "reason": "partial_tp",
                        "partial": True,
                    }
                )
            elif etype == "exit" or (etype is None and "pnl" in e):
                entry_px = e.get("entry_px")
                if entry_px is None and open_tr:
                    entry_px = open_tr.get("entry_px")
                trades.append(
                    {
                        "entry_time": open_tr.get("entry_time") if open_tr else None,
                        "entry_px": entry_px,
                        "side": e.get("side"),
                        "exit_time": bar_t,
                        "exit_px": e.get("price"),
                        "pnl": e.get("pnl"),
                        "reason": e.get("reason"),
                        "partial": False,
                    }
                )
                open_tr = None
    return trades, open_tr


def advisor_accuracy(
    advice_records: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    step_ms: int,
    max_gap_bars: int = 3,
) -> dict[str, Any]:
    """Claudeセカンドオピニオン(advisor)の判断と実際の取引結果を突き合わせる。

    各advice記録（advise_entry呼び出し時点＝pendingが立ったバー）に対し、
    直後のバーで同方向にエントリーした取引を探し、その実現損益(partial込み)で
    win/lossを判定する。一致する取引が無ければ（entry_timing_1m等でエントリー
    自体が成立しなかった場合）評価対象から除外し no_entry として数える。

    hit = (verdict=="approve" かつ win) または (verdict=="veto" かつ not win)
    veto側のwin_rateが低いほど、Claudeの警告が実際のリスクを的中させていることになる。
    """
    closed = [t for t in trades if t.get("pnl") is not None and t.get("entry_time") is not None]

    evaluated: list[dict[str, Any]] = []
    no_entry = 0
    for adv in advice_records:
        bar_t = adv.get("bar_open_time")
        side = adv.get("side")
        verdict = str(adv.get("verdict", "approve")).lower()
        if bar_t is None or side is None:
            continue
        lo = bar_t + step_ms
        hi = bar_t + step_ms * max_gap_bars
        candidates = [
            t for t in closed
            if t.get("side") == side and lo <= t["entry_time"] <= hi
        ]
        if not candidates:
            no_entry += 1
            continue
        entry_time = min(t["entry_time"] for t in candidates)
        total_pnl = sum(t["pnl"] for t in candidates if t["entry_time"] == entry_time)
        win = total_pnl > 0
        hit = (verdict == "veto" and not win) or (verdict != "veto" and win)
        evaluated.append({**adv, "matched_pnl": total_pnl, "win": win, "hit": hit})

    n = len(evaluated)
    approved = [e for e in evaluated if e["verdict"].lower() != "veto"]
    vetoed = [e for e in evaluated if e["verdict"].lower() == "veto"]
    hits = sum(1 for e in evaluated if e["hit"])

    def _win_rate(rows: list[dict[str, Any]]) -> float | None:
        return (sum(1 for r in rows if r["win"]) / len(rows)) if rows else None

    return {
        "n_evaluated": n,
        "n_no_entry": no_entry,
        "hit_rate": (hits / n) if n else None,
        "n_approve": len(approved),
        "approve_win_rate": _win_rate(approved),
        "n_veto": len(vetoed),
        "veto_win_rate": _win_rate(vetoed),
        "recent": sorted(evaluated, key=lambda e: e.get("bar_open_time") or 0, reverse=True)[:50],
    }


def period_stats(records: list[dict[str, Any]], since_ms: int, days: int) -> dict[str, Any]:
    """指定期間の成績サマリ（Claude へ渡す診断情報）"""
    trades, _ = build_trades(records)
    closed = [
        t for t in trades
        if not t.get("partial") and t.get("pnl") is not None
        and (t.get("exit_time") or 0) >= since_ms
    ]
    partial_pnl = sum(
        t.get("pnl") or 0 for t in trades
        if t.get("partial") and (t.get("exit_time") or 0) >= since_ms
    )
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] <= 0]
    exit_reasons: dict[str, int] = {}
    for t in closed:
        r = str(t.get("reason") or "?")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    block_reasons: dict[str, int] = {}
    for rec in records:
        if (rec.get("bar_open_time") or 0) < since_ms:
            continue
        for e in rec.get("events", []):
            if (
                e.get("type") == "decision"
                and int(e.get("signal", 0) or 0) != 0
                and int(e.get("pending_after_guard", 0) or 0) == 0
            ):
                r = str(e.get("reason") or "?")
                block_reasons[r] = block_reasons.get(r, 0) + 1
    top_blocks = dict(sorted(block_reasons.items(), key=lambda x: x[1], reverse=True)[:5])
    return {
        "days": days,
        "n_trades": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "total_pnl": sum(t["pnl"] for t in closed) + partial_pnl,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "exit_reasons": exit_reasons,
        "block_reasons": top_blocks,
    }

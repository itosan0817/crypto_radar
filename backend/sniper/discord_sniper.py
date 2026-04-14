"""
Bribe Sniper Simulator v3.0 - Discord 通知サービス
エントリー・決済・週次報告のリッチな Embed 通知を非同期で送信する
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Optional

import aiohttp
import pytz

from sniper.config import DISCORD_WEBHOOK_URL
from sniper.models import Position, ExitRecord, ExitPhase
from sniper.safe_io import safe_print

TZ_JST = pytz.timezone("Asia/Tokyo")

# Embed カラー定義
COLOR_S_ENTRY   = 0xFFD700   # ゴールド (S級エントリー)
COLOR_A_ENTRY   = 0xC0C0C0   # シルバー (A級エントリー)
COLOR_WIN       = 0x00FF88   # 緑 (勝ちトレード)
COLOR_LOSS      = 0xFF4444   # 赤 (負けトレード)
COLOR_HARD_STOP = 0xFF0000   # 真紅 (ハードストップ)
COLOR_TIME_EXIT = 0xFF8C00   # オレンジ (タイムエグジット)
COLOR_REPORT    = 0x7289DA   # Discord ブルー (週次報告)


def _fmt_jst(utc_dt: datetime.datetime) -> str:
    """UTC datetime を JST の読みやすい文字列に変換する"""
    return utc_dt.astimezone(TZ_JST).strftime("%Y-%m-%d %H:%M:%S JST")


def _webhook_configured() -> bool:
    u = DISCORD_WEBHOOK_URL or ""
    return bool(u) and "YOUR_DISCORD" not in u and u.startswith("https://")


async def _send_embed(embed: dict) -> None:
    """Discord Webhook に Embed形式のメッセージを非同期送信する"""
    if not DISCORD_WEBHOOK_URL:
        safe_print(
            "⚠️ [DiscordSniper] Webhook URL 未設定のため送信しません。"
            " backend/.env に BRIBE_WEBHOOK_URL=... を設定してください。"
        )
        return
    if "YOUR_DISCORD" in DISCORD_WEBHOOK_URL:
        safe_print("⚠️ [DiscordSniper] Webhook がプレースホルダーのため送信しません。")
        return
    if not DISCORD_WEBHOOK_URL.startswith("https://"):
        safe_print("⚠️ [DiscordSniper] Webhook URL が https で始まりません。")
        return
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                DISCORD_WEBHOOK_URL,
                json={"embeds": [embed]},
                timeout=aiohttp.ClientTimeout(total=10)
            )
            body_preview = ""
            if resp.status not in (200, 204):
                body_preview = (await resp.text())[:200]
                safe_print(f"⚠️ [DiscordSniper] 送信失敗 status={resp.status}: {body_preview[:100]}")
    except Exception as e:
        safe_print(f"⚠️ [DiscordSniper] 送信エラー: {e}")


async def notify_bribe_sniper_started() -> None:
    """起動確認用（Webhook が有効なとき1通だけ送る）"""
    if not _webhook_configured():
        safe_print(
            "⚠️ [DiscordSniper] 起動確認通知をスキップしました。"
            " `.env` の `BRIBE_WEBHOOK_URL` に Discord の Incoming Webhook URL を設定してください。"
        )
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    embed = {
        "title": "✅ Bribe Sniper 起動",
        "description": "プロセスが起動しました。Bribe 検知時は別途エントリー通知が届きます。",
        "color": COLOR_REPORT,
        "footer": {"text": f"{_fmt_jst(now_utc)} | Bribe Sniper v3.0"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)


async def notify_health_check() -> None:
    """毎日定例の健康診断通知（Bribe Sniper）"""
    if not _webhook_configured():
        return
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    embed = {
        "title": "📡 定例システム健康診断報告 | Bribe Sniper",
        "description": "24時間稼働の死活監視チェックが完了しました。現在Bribe検知エンジンは正常に待機中です。",
        "color": 0x3498db, # 青
        "fields": [
            {"name": "🛰️ 稼働プロセス", "value": "🟢 Bribe Sniper SIM (Main)", "inline": True},
            {"name": "⏳ 監視状況", "value": "🟢 正常 (Active Scan)", "inline": True},
            {"name": "🗓️ 最終確認時刻", "value": f"`{_fmt_jst(now_utc)}`", "inline": False}
        ],
        "footer": {"text": "Bribe Sniper v3.0 | 24/7 Monitoring System"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)


# ────────────────────────────────────────────────────────
# 🎯 エントリー通知
# ────────────────────────────────────────────────────────
async def notify_entry(pos: Position, net_ev_jst: float, delay_sec: float,
                        bribe_amount_usd: float, tvl_usd: float, entry_score: int) -> None:
    """仮想エントリー通知を Discord に送信する"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    color = COLOR_S_ENTRY if pos.grade == "S" else COLOR_A_ENTRY
    grade_icon = "🥇" if pos.grade == "S" else "🥈"

    pnl_threshold_s = pos.entry_price_usd * 1.08
    pnl_threshold_h = pos.entry_price_usd * 0.95

    embed = {
        "title": f"🎯 [{pos.grade}級] 仮想エントリー — {pos.pool_name}",
        "description": (
            f"**Bribe Sniper** がBribe入金を検知し、仮想ポジションを建てました。\n"
            f"> 📦 ポジションID: `{pos.position_id}`"
        ),
        "color": color,
        "fields": [
            {
                "name": f"{grade_icon} グレード / スコア",
                "value": f"**{pos.grade}級** ({entry_score}/100)",
                "inline": True,
            },
            {
                "name": "💰 投入サイズ",
                "value": f"**{pos.entry_size_jst:,.0f} JST**\n(${pos.entry_size_usd:,.0f})",
                "inline": True,
            },
            {
                "name": "📊 判定 NetEV",
                "value": f"**{net_ev_jst:+,.1f} JST**",
                "inline": True,
            },
            {
                "name": "⏱️ 仮想約定価格（遅延込み）",
                "value": f"`${pos.entry_price_usd:,.6f}` (遅延 {delay_sec:.1f}秒)",
                "inline": True,
            },
            {
                "name": "🏊 プール TVL",
                "value": f"${tvl_usd:,.0f}",
                "inline": True,
            },
            {
                "name": "🎁 Bribe金額",
                "value": f"${bribe_amount_usd:,.2f} ({pos.bribe_token})",
                "inline": True,
            },
            {
                "name": "🚦 出口戦略サマリー",
                "value": (
                    f"Phase1 +8% → `${pnl_threshold_s:,.4f}`\n"
                    f"HardStop -5% → `${pnl_threshold_h:,.4f}`\n"
                    f"TimeExit: 毎週木曜 08:50 JST"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": f"検知時刻: {_fmt_jst(now_utc)} | Bribe Sniper v3.0"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)


# ────────────────────────────────────────────────────────
# 💰 決済通知
# ────────────────────────────────────────────────────────
async def notify_exit(pos: Position, exit_rec: ExitRecord, current_price_usd: float) -> None:
    """決済通知（フェーズ・損益含む）を Discord に送信する"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    is_win    = exit_rec.pnl_jst >= 0
    is_hard   = exit_rec.phase == ExitPhase.HARD_STOP
    is_time   = exit_rec.phase == ExitPhase.TIME_EXIT

    if is_hard:
        color = COLOR_HARD_STOP
        title_icon = "🛑"
    elif is_time:
        color = COLOR_TIME_EXIT
        title_icon = "⏰"
    elif is_win:
        color = COLOR_WIN
        title_icon = "💰"
    else:
        color = COLOR_LOSS
        title_icon = "📉"

    embed = {
        "title": f"{title_icon} 決済通知 [{exit_rec.phase}] — {pos.pool_name}",
        "description": (
            f"**ポジションID**: `{pos.position_id}`\n"
            f"グレード: **{pos.grade}級** | トークン: **{pos.bribe_token}**"
        ),
        "color": color,
        "fields": [
            {
                "name": "🔵 エントリー価格",
                "value": f"`${pos.entry_price_usd:,.6f}`",
                "inline": True,
            },
            {
                "name": "🔴 決済価格",
                "value": f"`${exit_rec.exit_price_usd:,.6f}`",
                "inline": True,
            },
            {
                "name": "📈 価格変化",
                "value": f"`{exit_rec.pnl_pct:+.2f}%`",
                "inline": True,
            },
            {
                "name": "💴 今回確定損益（ガス控除後）",
                "value": f"**{exit_rec.pnl_jst:+,.1f} JST**",
                "inline": True,
            },
            {
                "name": "📦 今回決済量",
                "value": f"{exit_rec.closed_ratio * 100:.0f}% ({exit_rec.size_jst:,.0f} JST)",
                "inline": True,
            },
            {
                "name": "📊 累計確定損益",
                "value": f"**{pos.realized_pnl_jst:+,.1f} JST**",
                "inline": True,
            },
            {
                "name": "🔄 残ポジション",
                "value": f"{pos.remaining_ratio * 100:.0f}%",
                "inline": True,
            },
        ],
        "footer": {"text": f"決済時刻: {_fmt_jst(now_utc)} | Bribe Sniper v3.0"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)


# ────────────────────────────────────────────────────────
# 📊 週次レポート通知
# ────────────────────────────────────────────────────────
async def notify_weekly_report(stats: dict) -> None:
    """週次パフォーマンスレポートを Discord に送信する"""
    if not stats:
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    def _grade_field(grade: str) -> str:
        d = stats.get(grade, {})
        count    = d.get("count", 0)
        wins     = d.get("wins", 0)
        total_p  = d.get("total_pnl", 0.0)
        pf       = d.get("pf", 0.0)
        max_dd   = d.get("max_dd", 0.0)
        win_rate = (wins / count * 100) if count > 0 else 0
        avg_pnl  = (total_p / count) if count > 0 else 0
        pf_str   = f"{pf:.2f}" if pf != float("inf") else "∞"
        return (
            f"トレード数: **{count}** | 勝率: **{win_rate:.1f}%**\n"
            f"合計損益: `{total_p:+,.0f} JST` (平均 `{avg_pnl:+,.0f}`)\n"
            f"PF: **{pf_str}** | 最大DD: `{max_dd:,.0f} JST`"
        )

    # 上位3トークン
    by_token = stats.get("by_token", {})
    top_tokens = sorted(by_token.items(), key=lambda x: x[1]["pnl"], reverse=True)[:3]
    token_str = "\n".join(
        f"• **{sym}**: {d['count']}件 / `{d['pnl']:+,.0f} JST`"
        for sym, d in top_tokens
    ) or "データなし"

    # ピーク時間帯
    hourly = stats.get("hourly", {})
    best_hour = max(hourly, key=lambda h: hourly[h]["pnl"]) if hourly else 0
    best_h_data = hourly.get(best_hour, {})

    embed = {
        "title": "📊 週次パフォーマンスレポート — Bribe Sniper v3.0",
        "description": "過去7日間のシミュレーション結果の集計です。",
        "color": COLOR_REPORT,
        "fields": [
            {
                "name": "🥇 S級トレード",
                "value": _grade_field("S"),
                "inline": False,
            },
            {
                "name": "🥈 A級トレード",
                "value": _grade_field("A"),
                "inline": False,
            },
            {
                "name": "📦 全体合計",
                "value": _grade_field("total"),
                "inline": False,
            },
            {
                "name": "🏆 トップ Bribeトークン",
                "value": token_str,
                "inline": True,
            },
            {
                "name": f"⏰ ピーク時間帯 ({best_hour:02d}:00 UTC)",
                "value": (
                    f"件数: {best_h_data.get('count', 0)} "
                    f"/ 損益: `{best_h_data.get('pnl', 0):+,.0f} JST`"
                ),
                "inline": True,
            },
        ],
        "footer": {"text": f"集計時刻: {_fmt_jst(now_utc)} | 毎週木曜 08:50 JST 配信"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)


# ────────────────────────────────────────────────────────
# ⚠️ フィルタリング棄却通知（デバッグ用）
# ────────────────────────────────────────────────────────
async def notify_rejected(pool_name: str, bribe_token: str, reason: str) -> None:
    """エントリー棄却の通知（デバッグ用・低優先度）"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    embed = {
        "title": f"🚫 エントリー棄却 — {pool_name}",
        "description": f"Bribeトークン: **{bribe_token}**\n理由: {reason}",
        "color": 0x808080,
        "footer": {"text": f"{_fmt_jst(now_utc)} | Bribe Sniper v3.0"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)


# ────────────────────────────────────────────────────────
# 🚨 エラー（プログラム異常）通知
# ────────────────────────────────────────────────────────
async def notify_error(process_name: str, error_msg: str) -> None:
    """プログラム異常発生時（例外）の通知をDiscordへ送信する"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # 2000文字の制限に引っかからないようにログを切り詰め
    safe_error_msg = error_msg[:2000]
    
    embed = {
        "title": f"🚨 [SYSTEM ERROR] {process_name} 異常発生",
        "description": f"プログラムの実行中に警告・致命的なエラーが発生しました。\n```python\n{safe_error_msg}\n```",
        "color": COLOR_HARD_STOP,
        "footer": {"text": f"発生時刻: {_fmt_jst(now_utc)} | Bribe Sniper SIM"},
        "timestamp": now_utc.isoformat(),
    }
    await _send_embed(embed)

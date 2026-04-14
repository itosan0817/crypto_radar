"""
Bribe Sniper Simulator v3.0 — メインエントリーポイント
aerodrome_radar.py（タイムロック監視）とは完全に独立したシステム。

起動コマンド:
    python bribe_sniper.py
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import os

# Windows cp932 環境でも絵文字ログでクラッシュしないように標準出力をUTF-8化
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.providers import WebSocketProvider

from sniper.config import ALCHEMY_BASE_WSS_URL, ALCHEMY_BASE_HTTP_URL
from sniper.safe_io import safe_print
from sniper.event_monitor import start_bribe_monitor
from sniper.position_manager import PositionManager
from sniper.exit_scheduler import exit_scheduler_loop
from sniper.discord_sniper import notify_weekly_report, notify_bribe_sniper_started, notify_health_check
from sniper.firestore_sniper import FirestoreSniperService

import pytz
TZ_JST = pytz.timezone("Asia/Tokyo")


async def _health_check_loop() -> None:
    """1日2回（JST 09:00 / 21:00）に健康診断通知を送るループ"""
    last_check_hour = None
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        # UTC 00:00 (JST 09:00) と UTC 12:00 (JST 21:00) に通知
        if now.hour in [0, 12] and last_check_hour != now.hour:
            try:
                await notify_health_check()
                last_check_hour = now.hour
                safe_print(f"✅ 定例健康診断通知を送信しました (Sniper - {now.hour} UTC)")
            except Exception as e:
                safe_print(f"⚠️ 定例健康診断通知の送信に失敗: {e}")
        await asyncio.sleep(60)


async def main_loop() -> None:
    """
    全バックグラウンドタスクを起動し、WebSocket切断時に自動再接続するメインループ。
    タスク:
      1. NotifyReward イベント監視
      2. ポジション価格監視（段階的利確・トレーリングストップ）
      3. 木曜タイムエグジットスケジューラー
    """
    now_jst = datetime.datetime.now(TZ_JST).strftime("%Y-%m-%d %H:%M:%S JST")
    safe_print("=" * 60)
    safe_print("🛰️  Bribe Sniper Simulator v3.0 起動")
    safe_print(f"   起動時刻: {now_jst}")
    safe_print("   Target: Aerodrome Finance (Base Chain)")
    safe_print("   Mode: Forward Testing Simulator")
    safe_print("=" * 60)

    # ──────────────────────────────────────────
    # 起動時サマリーを Discord 送信（月間）
    # ──────────────────────────────────────────
    try:
        monthly_stats = FirestoreSniperService.get_weekly_stats(days=30)
        if monthly_stats and monthly_stats.get("total", {}).get("count", 0) > 0:
            await notify_weekly_report(monthly_stats)
            safe_print("📊 起動時月間サマリーを送信しました。")
        else:
            safe_print(
                "ℹ️ 起動時月間サマリーはスキップ（直近30日のトレード件数が0）。"
                " Webhook 確認用に起動通知を送ります。"
            )
            await notify_bribe_sniper_started()
    except Exception as e:
        safe_print(f"⚠️ 起動時サマリー送信失敗: {e}")

    # ──────────────────────────────────────────
    # HTTP Provider（ポジション価格ポーリング用）
    # ──────────────────────────────────────────
    w3_http = AsyncWeb3(AsyncHTTPProvider(ALCHEMY_BASE_HTTP_URL))

    # ──────────────────────────────────────────
    # PositionManager の生成（HTTPで価格を取得）
    # ──────────────────────────────────────────
    position_manager = PositionManager(w3_http)

    # ポジション価格監視タスクを開始
    asyncio.create_task(position_manager.monitor_loop())
    safe_print("✅ ポジション監視タスク 起動")

    # 木曜タイムエグジットスケジューラータスクを開始
    asyncio.create_task(exit_scheduler_loop(position_manager))
    safe_print("✅ タイムエグジットスケジューラー 起動")

    # 定例健康診断タスクを開始
    asyncio.create_task(_health_check_loop())

    # ──────────────────────────────────────────
    # WebSocket監視ループ（切断→自動再接続）
    # ──────────────────────────────────────────
    reconnect_count = 0
    while True:
        try:
            safe_print(
                f"\n🔌 BaseチェーンへのWebSocket接続試行 "
                f"(再接続: {reconnect_count}回目)..."
            )

            async with AsyncWeb3(WebSocketProvider(ALCHEMY_BASE_WSS_URL)) as w3_wss:
                if not await w3_wss.is_connected():
                    safe_print("🔴 WebSocket接続失敗。5秒後に再試行...")
                    await asyncio.sleep(5)
                    continue

                safe_print("🟢 Alchemy WebSocket 接続成功！")
                safe_print(
                    f"   監視中: NotifyReward (Aerodrome ExternalBribe)\n"
                    f"   ホワイトリスト: USDC / WETH / AERO / cbBTC / wstETH / cbETH "
                    f"/ DEGEN / WELL / SNX / LINK"
                )

                # NotifyReward イベント監視（切断までブロックする）
                await start_bribe_monitor(w3_wss, position_manager)

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            reconnect_count += 1
            safe_print(f"\n❌ WebSocket切断/エラー: {e}")
            
            error_str = str(e)
            if "no close frame received or sent" not in error_str and "Connection closed" not in error_str:
                try:
                    from sniper.discord_sniper import notify_error
                    await notify_error("Bribe Sniper SIM (Main Loop)", error_trace)
                except Exception as notify_e:
                    safe_print(f"⚠️ エラー通知送信失敗: {notify_e}")

            safe_print(f"🔄 5秒後に再接続します... (累計{reconnect_count}回)")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        safe_print("\n👋 Bribe Sniper Simulator v3.0 を終了します。")
    except Exception as fatal_e:
        import traceback
        error_trace = traceback.format_exc()
        try:
            from sniper.discord_sniper import notify_error
            # 同期コンテキストで実行中のため新しいイベントループで送信
            asyncio.run(notify_error("Bribe Sniper SIM (Fatal Crash)", error_trace))
        except Exception:
            pass
        raise

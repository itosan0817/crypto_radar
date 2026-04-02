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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.providers import WebSocketProvider

from sniper.config import ALCHEMY_BASE_WSS_URL, ALCHEMY_BASE_HTTP_URL
from sniper.event_monitor import start_bribe_monitor
from sniper.position_manager import PositionManager
from sniper.exit_scheduler import exit_scheduler_loop
from sniper.discord_sniper import notify_weekly_report
from sniper.firestore_sniper import FirestoreSniperService

import pytz
TZ_JST = pytz.timezone("Asia/Tokyo")


async def main_loop() -> None:
    """
    全バックグラウンドタスクを起動し、WebSocket切断時に自動再接続するメインループ。
    タスク:
      1. NotifyReward イベント監視
      2. ポジション価格監視（段階的利確・トレーリングストップ）
      3. 木曜タイムエグジットスケジューラー
    """
    now_jst = datetime.datetime.now(TZ_JST).strftime("%Y-%m-%d %H:%M:%S JST")
    print("=" * 60, flush=True)
    print("🛰️  Bribe Sniper Simulator v3.0 起動", flush=True)
    print(f"   起動時刻: {now_jst}", flush=True)
    print("   Target: Aerodrome Finance (Base Chain)", flush=True)
    print("   Mode: Forward Testing Simulator", flush=True)
    print("=" * 60, flush=True)

    # ──────────────────────────────────────────
    # 起動時サマリーを Discord 送信（月間）
    # ──────────────────────────────────────────
    try:
        monthly_stats = FirestoreSniperService.get_weekly_stats(days=30)
        if monthly_stats and monthly_stats.get("total", {}).get("count", 0) > 0:
            await notify_weekly_report(monthly_stats)
            print("📊 起動時月間サマリーを送信しました。", flush=True)
    except Exception as e:
        print(f"⚠️ 起動時サマリー送信失敗: {e}", flush=True)

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
    print("✅ ポジション監視タスク 起動", flush=True)

    # 木曜タイムエグジットスケジューラータスクを開始
    asyncio.create_task(exit_scheduler_loop(position_manager))
    print("✅ タイムエグジットスケジューラー 起動", flush=True)

    # ──────────────────────────────────────────
    # WebSocket監視ループ（切断→自動再接続）
    # ──────────────────────────────────────────
    reconnect_count = 0
    while True:
        try:
            print(
                f"\n🔌 BaseチェーンへのWebSocket接続試行 "
                f"(再接続: {reconnect_count}回目)...",
                flush=True
            )

            async with AsyncWeb3(WebSocketProvider(ALCHEMY_BASE_WSS_URL)) as w3_wss:
                if not await w3_wss.is_connected():
                    print("🔴 WebSocket接続失敗。5秒後に再試行...", flush=True)
                    await asyncio.sleep(5)
                    continue

                print("🟢 Alchemy WebSocket 接続成功！", flush=True)
                print(
                    f"   監視中: NotifyReward (Aerodrome ExternalBribe)\n"
                    f"   ホワイトリスト: USDC / WETH / AERO / cbBTC / wstETH / cbETH "
                    f"/ DEGEN / WELL / SNX / LINK",
                    flush=True
                )

                # NotifyReward イベント監視（切断までブロックする）
                await start_bribe_monitor(w3_wss, position_manager)

        except Exception as e:
            reconnect_count += 1
            print(f"\n❌ WebSocket切断/エラー: {e}", flush=True)
            print(f"🔄 5秒後に再接続します... (累計{reconnect_count}回)", flush=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n👋 Bribe Sniper Simulator v3.0 を終了します。", flush=True)

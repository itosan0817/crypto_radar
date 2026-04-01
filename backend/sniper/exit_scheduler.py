"""
Bribe Sniper Simulator v3.0 - タイムエグジットスケジューラー
毎週木曜日 08:50 JST にすべてのポジションを強制クローズし、週次レポートを送信する
"""
from __future__ import annotations

import asyncio
import datetime

import pytz

from sniper.config import EXIT_WEEKDAY_THURSDAY, EXIT_TIME_HOUR_JST, EXIT_TIME_MINUTE_JST
from sniper.discord_sniper import notify_weekly_report
from sniper.firestore_sniper import FirestoreSniperService

TZ_JST = pytz.timezone("Asia/Tokyo")


async def exit_scheduler_loop(position_manager) -> None:
    """
    毎週木曜 08:50 JST に全ポジションを強制クローズし、週次レポートを送信する
    バックグラウンドタスクとして asyncio.create_task で起動する。
    """
    print("🗓️ [ExitScheduler] タイムエグジットスケジューラー 開始", flush=True)
    last_exit_date = None  # 同一週の二重実行を防ぐ

    while True:
        try:
            now_jst = datetime.datetime.now(TZ_JST)

            is_thursday = (now_jst.weekday() == EXIT_WEEKDAY_THURSDAY)
            is_exit_time = (
                now_jst.hour == EXIT_TIME_HOUR_JST
                and now_jst.minute == EXIT_TIME_MINUTE_JST
            )
            already_done = (last_exit_date == now_jst.date())

            if is_thursday and is_exit_time and not already_done:
                print(
                    f"⏰ [ExitScheduler] 木曜タイムエグジット 実行 "
                    f"({now_jst.strftime('%Y-%m-%d %H:%M JST')})",
                    flush=True
                )

                # 全ポジション強制クローズ
                closed_count = await position_manager.force_close_all(
                    reason="TimeExit (木曜強制決済)"
                )

                # 週次レポートを Firestore から取得して Discord 通知
                stats = FirestoreSniperService.get_weekly_stats(days=7)
                if stats:
                    await notify_weekly_report(stats)
                    print(
                        f"📊 [ExitScheduler] 週次レポート送信完了 "
                        f"(クローズ {closed_count}件)",
                        flush=True
                    )

                last_exit_date = now_jst.date()

        except Exception as e:
            print(f"❌ [ExitScheduler] スケジューラーエラー: {e}", flush=True)

        # 30秒ごとにチェック（分単位の精度で十分）
        await asyncio.sleep(30)

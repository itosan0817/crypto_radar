import asyncio
import datetime
import sys
import os

# Windows cp932 環境でも絵文字ログでクラッシュしないように標準出力をUTF-8化
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# サブモジュールのインポートに対応するためパスを追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3
from web3.providers import WebSocketProvider
from config.settings import ALCHEMY_BASE_WSS_URL
from core.event_handler import handle_call_scheduled, handle_call_executed
from sniper.safe_io import safe_print

# ==========================================
# 監視対象の代表的なイベントシグネチャのトピックハッシュ
# ==========================================
TOPIC_CALL_SCHEDULED = "0x" + AsyncWeb3.keccak(text="CallScheduled(bytes32,uint256,address,uint256,bytes,bytes32,uint256)").hex()
# NOTE: CallExecutedはコントラクト種類により引数が異なる場合がありますが、代表的なものをプレースホルダーとしてセット
TOPIC_CALL_EXECUTED = "0x" + AsyncWeb3.keccak(text="CallExecuted(bytes32,uint256,address,uint256,bytes)").hex()

async def subscribe_and_listen(w3: AsyncWeb3):
    """
    両方のイベント（スケジュール、実行）の購読処理と無限リスンループ
    """
    subscription_id = await w3.eth.subscribe(
        'logs', 
        {
            # 2つのトピックのいずれかにマッチするものを購読
            "topics": [[TOPIC_CALL_SCHEDULED, TOPIC_CALL_EXECUTED]]
        }
    )
    safe_print(f"📡 Subscription ID: {subscription_id}")

    # レーダー無限受信ループ
    async for response in w3.socket.process_subscriptions():
        log = response['result']
        topics = log.get('topics', [])
        
        if not topics:
            continue
            
        # HexBytes.hex() は "0x" prefix なしの文字列を返すため、明示的に正規化する
        raw_topic0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]
        topic0 = raw_topic0 if raw_topic0.startswith("0x") else ("0x" + raw_topic0)
        
        # 処理がブロッキングされないよう、create_taskでバックグラウンド実行を投げる
        if topic0 == TOPIC_CALL_SCHEDULED:
            asyncio.create_task(handle_call_scheduled(w3, log))
        elif topic0 == TOPIC_CALL_EXECUTED:
            asyncio.create_task(handle_call_executed(w3, log))

async def _weekly_report_loop(FirebaseService, DiscordService) -> None:
    """WS購読とは独立して週次レポート条件を監視する。"""
    last_report_date = None
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.weekday() == 3 and now.hour == 0 and last_report_date != now.date():
            weekly_stats = FirebaseService.get_simulation_stats(days=7)
            if weekly_stats:
                DiscordService.send_summary_notification(weekly_stats, is_monthly=False)
                last_report_date = now.date()
        await asyncio.sleep(30)

async def _health_check_loop(DiscordService) -> None:
    """1日2回（JST 09:00 / 21:00）に健康診断通知を送るループ"""
    last_check_hour = None
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        # UTC 00:00 (JST 09:00) と UTC 12:00 (JST 21:00) に通知
        if now.hour in [0, 12] and last_check_hour != now.hour:
            try:
                DiscordService.send_health_check()
                last_check_hour = now.hour
                safe_print(f"✅ 定例健康診断通知を送信しました (Radar - {now.hour} UTC)")
            except Exception as e:
                safe_print(f"⚠️ 定例健康診断通知の送信に失敗: {e}")
        await asyncio.sleep(60)

async def main_loop():
    """
    インフラ防衛のための無限再接続ループを内包したメインプロセス
    """
    now_utc_dt = datetime.datetime.now(datetime.timezone.utc)
    now_utc_str = now_utc_dt.strftime('%H:%M:%S')
    safe_print(f"🔄 Baseチェーン監視網（シミュレーター極）起動... UTC: {now_utc_str}")
    
    # 🧼 [30日間の保持制限] 起動時に古いシミュレーション記録（30日前より古いもの）をお掃除
    from services.firebase_service import FirebaseService
    from services.discord_service import DiscordService
    FirebaseService.cleanup_old_simulations(days=30)
    
    # 📊 起動時に月間(30日)のサマリーを一度送信
    monthly_stats = FirebaseService.get_simulation_stats(days=30)
    # 統計データが存在し、かつ1件以上の記録がある場合のみサマリーを送信
    if monthly_stats and monthly_stats.get("total", {}).get("count", 0) > 0:
        DiscordService.send_summary_notification(monthly_stats, is_monthly=True)
    else:
        # データがない、または0件の場合は「起動通知」を優先して送る
        DiscordService.send_startup_notification()
    
    asyncio.create_task(_weekly_report_loop(FirebaseService, DiscordService))
    asyncio.create_task(_health_check_loop(DiscordService))

    while True:
        try:
            async with AsyncWeb3(WebSocketProvider(ALCHEMY_BASE_WSS_URL)) as w3:
                if await w3.is_connected():
                    safe_print("🟢 Alchemy接続成功！監視レーダー・極（シミュレーター）稼働中...")
                else:
                    safe_print("🔴 接続失敗。URLを確認してください。")
                    await asyncio.sleep(5)
                    continue

                await subscribe_and_listen(w3)
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            safe_print(f"❌ WebSocket切断または致命的エラー: {e}")
            
            error_str = str(e)
            if "no close frame received or sent" not in error_str and "Connection closed" not in error_str:
                try:
                    from services.discord_service import DiscordService
                    DiscordService.send_error_notification("Aerodrome Radar (Main Loop)", error_trace)
                except Exception as notify_e:
                    safe_print(f"❌ エラー通知送信失敗: {notify_e}")

            safe_print("🔄 5秒後に無限再接続ループを発動します...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        safe_print("\n👋 監視レーダー・極 を終了します。")
    except Exception as fatal_e:
        import traceback
        error_trace = traceback.format_exc()
        try:
            from services.discord_service import DiscordService
            DiscordService.send_error_notification("Aerodrome Radar (Fatal Crash)", error_trace)
        except Exception:
            pass
        raise
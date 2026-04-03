import asyncio
import datetime
import sys
import os

# サブモジュールのインポートに対応するためパスを追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3
from web3.providers import WebSocketProvider
from config.settings import ALCHEMY_BASE_WSS_URL
from core.event_handler import handle_call_scheduled, handle_call_executed

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
    print(f"📡 Subscription ID: {subscription_id}", flush=True)

    # レーダー無限受信ループ
    async for response in w3.socket.process_subscriptions():
        log = response['result']
        topics = log.get('topics', [])
        
        if not topics:
            continue
            
        topic0 = topics[0].hex() if type(topics[0]) is bytes else topics[0]
        
        # 処理がブロッキングされないよう、create_taskでバックグラウンド実行を投げる
        if topic0 == TOPIC_CALL_SCHEDULED:
            asyncio.create_task(handle_call_scheduled(w3, log))
        elif topic0 == TOPIC_CALL_EXECUTED:
            asyncio.create_task(handle_call_executed(w3, log))

async def main_loop():
    """
    インフラ防衛のための無限再接続ループを内包したメインプロセス
    """
    now_utc_dt = datetime.datetime.now(datetime.timezone.utc)
    now_utc_str = now_utc_dt.strftime('%H:%M:%S')
    print(f"🔄 Baseチェーン監視網（シミュレーター極）起動... UTC: {now_utc_str}", flush=True)
    
    # 🧼 [30日間の保持制限] 起動時に古いシミュレーション記録（30日前より古いもの）をお掃除
    from services.firebase_service import FirebaseService
    from services.discord_service import DiscordService
    FirebaseService.cleanup_old_simulations(days=30)
    
    # 📊 起動時に月間(30日)のサマリーを一度送信
    monthly_stats = FirebaseService.get_simulation_stats(days=30)
    if monthly_stats:
        DiscordService.send_summary_notification(monthly_stats, is_monthly=True)
    
    last_report_date = None # 週間レポートの重複送信防止用

    while True:
        try:
            # 🕒 週間レポート送信チェック (毎週木曜 00:00 UTC)
            now = datetime.datetime.now(datetime.timezone.utc)
            if now.weekday() == 3 and now.hour == 0 and last_report_date != now.date():
                weekly_stats = FirebaseService.get_simulation_stats(days=7)
                if weekly_stats:
                    DiscordService.send_summary_notification(weekly_stats, is_monthly=False)
                    last_report_date = now.date()


            async with AsyncWeb3(WebSocketProvider(ALCHEMY_BASE_WSS_URL)) as w3:
                if await w3.is_connected():
                    print("🟢 Alchemy接続成功！監視レーダー・極（シミュレーター）稼働中...", flush=True)
                else:
                    print("🔴 接続失敗。URLを確認してください。", flush=True)
                    await asyncio.sleep(5)
                    continue

                await subscribe_and_listen(w3)
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"❌ WebSocket切断または致命的エラー: {e}", flush=True)
            
            error_str = str(e)
            if "no close frame received or sent" not in error_str and "Connection closed" not in error_str:
                try:
                    from services.discord_service import DiscordService
                    DiscordService.send_error_notification("Aerodrome Radar (Main Loop)", error_trace)
                except Exception as notify_e:
                    print(f"❌ エラー通知送信失敗: {notify_e}", flush=True)

            print("🔄 5秒後に無限再接続ループを発動します...", flush=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n👋 監視レーダー・極 を終了します。", flush=True)
    except Exception as fatal_e:
        import traceback
        error_trace = traceback.format_exc()
        try:
            from services.discord_service import DiscordService
            DiscordService.send_error_notification("Aerodrome Radar (Fatal Crash)", error_trace)
        except Exception:
            pass
        raise
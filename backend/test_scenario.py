import asyncio
import os
import sys
from web3 import AsyncWeb3

# カレントディレクトリをパスに追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.event_handler import handle_call_scheduled, handle_call_executed
from services.firebase_service import FirebaseService
from web3.providers import WebSocketProvider
from config.settings import ALCHEMY_BASE_WSS_URL

async def run_test_scenario():
    """
    本物のイベントが発生したかのように偽装データをハンドラーに流し込むテスト
    """
    print("🚀 [TEST] 疑似イベント・シナリオテストを開始します...")
    
    # 1. Web3 接続 (価格取得などのために必要)
    async with AsyncWeb3(WebSocketProvider(ALCHEMY_BASE_WSS_URL)) as w3:
        if not await w3.is_connected():
            print("❌ Alchemy接続失敗。テストを中断します。")
            return

        # --- 🧪 シナリオ1: CallScheduled (エントリー予約) のテスト ---
        print("\n🧪 STEP 1: CallScheduled (エントリー予約) のテスト...")
        # 偽のログデータを作成 (Aerodromeの実際のトピック構成に近いもの)
        mock_log_scheduled = {
            'transactionHash': AsyncWeb3.to_bytes(hexstr="0x1111111111111111111111111111111111111111111111111111111111111111"),
            'address': "0x00000000000000000000000000000000TESTADDR", # ダミーアドレス
            'data': "0x1234567890abcdef" * 8 # 適当なCalldata
        }
        
        # ハンドラーを呼び出し (AI解析、Firebase保存、Discord通知が走る)
        await handle_call_scheduled(w3, mock_log_scheduled)
        
        print("\n⏳ 5秒待機してから答え合わせテストへ移ります...")
        await asyncio.sleep(5)

        # --- 🧪 シナリオ2: CallExecuted (48時間後の実行) のテスト ---
        print("\n🧪 STEP 2: CallExecuted (48時間後の実行・答え合わせ) のテスト...")
        mock_log_executed = {
            'transactionHash': AsyncWeb3.to_bytes(hexstr="0x1111111111111111111111111111111111111111111111111111111111111111"),
            'address': "0x00000000000000000000000000000000TESTADDR"
        }
        
        # ハンドラーを呼び出し (PnL計算とレポート通知が走る)
        await handle_call_executed(w3, mock_log_executed)

    print("\n✅ [TEST] 全てのテストシナリオが終了しました。")
    print("📱 DiscordとFirebase Consoleを確認してください！")

if __name__ == "__main__":
    asyncio.run(run_test_scenario())

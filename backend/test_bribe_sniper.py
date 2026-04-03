import asyncio
import os
import sys
import datetime
from web3 import AsyncWeb3

# カレントディレクトリをパスに追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3, AsyncHTTPProvider
from sniper.config import ALCHEMY_BASE_HTTP_URL, WHITELISTED_TOKENS, JST_PER_USD
from sniper.event_monitor import _process_bribe_event, NOTIFY_REWARD_TOPIC
from sniper.position_manager import PositionManager

async def run_sniper_test():
    """
    Bribe Sniper の疑似イベント・テストシナリオ
    高額な Bribe が入金されたと仮定して、システムを強制的に発火させます。
    """
    print("🚀 [TEST] Bribe Sniper 疑似イベント・シナリオテストを開始します...")

    # 1. Web3 接続 (HTTP)
    w3 = AsyncWeb3(AsyncHTTPProvider(ALCHEMY_BASE_HTTP_URL))
    if not await w3.is_connected():
        print("❌ Alchemy接続失敗。テストを中断します。")
        return

    # 2. PositionManager の初期化 (テスト用)
    position_manager = PositionManager(w3)

    # 3. テスト用の擬似ログデータを作成
    # ターゲット: WETH/USDC vAMM プール
    target_pool_name = "WETH/USDC"
    bribe_token_symbol = "USDC"
    # Checksum化して確実にアドレス型として扱う
    bribe_token_addr = w3.to_checksum_address(WHITELISTED_TOKENS["USDC"])
    # 実際の WETH/USDC vAMM 対応の ExternalBribe アドレス
    external_bribe_addr = w3.to_checksum_address("0x78D1CefD2Cc5975d9e5bB10f63EAeb3B8647000d")
    
    # トピック構成: [EventSignature, from (indexed), reward (indexed), epoch (indexed)]
    mock_topics = [
        NOTIFY_REWARD_TOPIC,
        "0x0000000000000000000000000000000000000000000000000000000000000000", # Dummy from
        "0x" + "0" * 24 + bribe_token_addr.replace("0x", "").lower(), # USDC (Reward)
        "0x" + "0" * 63 + "1" # Epoch 1
    ]
    
    # Data: 100,000 USDC 入金 (6 decimals) -> 100,000,000,000 wei
    # 16進数で 0x174876E800
    mock_data = "0x" + "0" * 61 + "174876e800" 

    mock_log = {
        'transactionHash': AsyncWeb3.to_bytes(hexstr="0x" + "5" * 64),
        'address': external_bribe_addr,
        'topics': [AsyncWeb3.to_bytes(hexstr=t) for t in mock_topics],
        'data': mock_data,
        'blockNumber': 1000000,
    }

    print(f"\n🧪 STEP 1: {target_pool_name} への {bribe_token_symbol} Bribe 入金検知テスト...")
    print(f"   入金トークン: {bribe_token_addr}")
    print(f"   Bribeコントラクト: {external_bribe_addr}")
    
    try:
        await _process_bribe_event(
            w3, 
            position_manager, 
            mock_log, 
            bribe_token_addr, 
            bribe_token_symbol, 
            external_bribe_addr, 
            mock_log['transactionHash'].hex()
        )
    except Exception as e:
        print(f"❌ テスト実行エラー: {e}")
        import traceback
        traceback.print_exc()

    print("\n✅ [TEST] シナリオ投入完了。")
    print("📱 Discordのアラートと、Firestoreの 'bribe_positions' コレクションを確認してください。")

if __name__ == "__main__":
    asyncio.run(run_sniper_test())

"""
Aerodromeの実際のNotifyRewardイベントのシグネチャを確認する診断スクリプト
様々なシグネチャを試して、実際にBaseチェーンに存在するものを特定する
"""
import sys
import asyncio
from web3 import AsyncWeb3

HTTP_URL = "https://base-mainnet.g.alchemy.com/v2/lcau4KV3k-6quLk___bH2"

# 様々なNotifyRewardシグネチャのバリエーションを試す
SIGNATURES = [
    "NotifyReward(address,address,uint256,uint256)",
    "NotifyReward(address,address,uint256)",
    "NotifyReward(address indexed,address indexed,uint256 indexed,uint256)",
    "RewardAdded(address,uint256)",
    "NotifyReward(address,uint256)",
]

print("=== Aerodrome NotifyRewardシグネチャ診断 ===")
print()
for sig in SIGNATURES:
    topic = "0x" + AsyncWeb3.keccak(text=sig).hex()
    print(f"  {sig}")
    print(f"    => Topic: {topic}")
    print()

# 有名なAerodrome外部Bribeコントラクトでテスト
# AerodromeのWETH/AEROのBribeコントラクト (公式から確認)
KNOWN_BRIBE_CONTRACTS = [
    "0x5e27B876AF8A073d77E0a9e151F46CBED3Be8af8",  # Aerodrome WETH/USDC ExternalBribe
    "0xB875F3F2A7a6f60E462e09a6e14a92DAfBB0e1Bb",  # 別のBribeコントラクト
]

async def check_known_contract():
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(HTTP_URL))
    latest = await w3.eth.block_number
    
    print(f"\n最新ブロック: {latest}")
    print("既知のBribeコントラクトで過去100ブロックを検索...\n")
    
    # 全トピックなしで、既知コントラクトからのログだけ100ブロック取得
    for addr in KNOWN_BRIBE_CONTRACTS:
        try:
            logs = await w3.eth.get_logs({
                "fromBlock": latest - 100,
                "toBlock": latest,
                "address": addr,
            })
            print(f"  {addr}: {len(logs)}件のイベント")
            for log in logs[:3]:
                topics = log.get("topics", [])
                topic0 = topics[0].hex() if topics and isinstance(topics[0], bytes) else (topics[0] if topics else "none")
                print(f"    - topic0: {topic0}")
        except Exception as e:
            print(f"  {addr}: エラー ({e})")

asyncio.run(check_known_contract())

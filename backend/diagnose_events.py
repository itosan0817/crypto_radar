"""
診断スクリプト: NotifyRewardイベントが実際にBaseチェーン上で発生しているか確認する
過去100ブロック分のイベントをHTTPでスキャンして、検知できるか確認する
"""
import sys
import os
import asyncio
sys.path.append('/home/yoshi/backend')

from web3 import AsyncWeb3
from sniper.config import (
    ALCHEMY_BASE_HTTP_URL, ALCHEMY_BASE_WSS_URL,
    WHITELISTED_TOKEN_ADDRESSES, TOKEN_SYMBOL_MAP
)

# NotifyRewardのイベントシグネチャ
NOTIFY_REWARD_TOPIC = (
    "0x" + AsyncWeb3.keccak(
        text="NotifyReward(address,address,uint256,uint256)"
    ).hex()
)
NOTIFY_REWARD_TOPIC_V1 = (
    "0x" + AsyncWeb3.keccak(
        text="NotifyReward(address,address,uint256)"
    ).hex()
)

print(f"[*] NotifyReward Topic (v2): {NOTIFY_REWARD_TOPIC}")
print(f"[*] NotifyReward Topic (v1): {NOTIFY_REWARD_TOPIC_V1}")
print(f"[*] ホワイトリスト件数: {len(WHITELISTED_TOKEN_ADDRESSES)}個")

async def check_recent_events():
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(ALCHEMY_BASE_HTTP_URL))
    
    latest_block = await w3.eth.block_number
    # Alchemyの制限（最大2000ブロック/リクエスト）に対して1500ブロック刻みでスキャン
    CHUNK = 1500
    # 過去18000ブロック（約1時間分）を分割取得
    TOTAL_BLOCKS = 18000
    from_block = latest_block - TOTAL_BLOCKS
    
    print(f"\n[*] 最新ブロック: {latest_block}")
    print(f"[*] スキャン範囲: Block {from_block} 〜 {latest_block}")
    print(f"[*] NotifyRewardイベントを {CHUNK}ブロック刻みで検索中...\n")
    
    all_logs = []
    cur = from_block
    while cur < latest_block:
        end = min(cur + CHUNK - 1, latest_block)
        try:
            chunk_logs = await w3.eth.get_logs({
                "fromBlock": cur,
                "toBlock": end,
                "topics": [[NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1]],
            })
            all_logs.extend(chunk_logs)
            print(f"  Block {cur}~{end}: {len(chunk_logs)}件")
        except Exception as e:
            print(f"  Block {cur}~{end}: エラー ({e})")
        cur = end + 1
    
    print(f"\n✅ 合計 発見したNotifyRewardイベント: {len(all_logs)}件")
    
    whitelist_hits = 0
    for log in all_logs[:30]:  # 最初の30件を表示
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        
        raw = topics[2].hex() if isinstance(topics[2], bytes) else topics[2]
        reward_addr = "0x" + raw[-40:]
        reward_lower = reward_addr.lower()
        symbol = TOKEN_SYMBOL_MAP.get(reward_lower, "UNKNOWN")
        in_whitelist = reward_lower in WHITELISTED_TOKEN_ADDRESSES
        
        if in_whitelist:
            whitelist_hits += 1
        
        print(f"  - Block {log.get('blockNumber')}: token={symbol} ({reward_addr[:10]}...) whitelist={in_whitelist}")
    
    print(f"\n✅ ホワイトリスト対象のイベント数: {whitelist_hits}件 (先頭30件中)")
    
    if len(all_logs) > 30:
        print(f"  ※ 残り {len(all_logs) - 30} 件は省略")

asyncio.run(check_recent_events())

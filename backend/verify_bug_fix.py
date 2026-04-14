"""
Final verification: use tiny block ranges and test the actual event_monitor topic constants
"""
import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3
from sniper.config import ALCHEMY_BASE_HTTP_URL, WHITELISTED_TOKEN_ADDRESSES, TOKEN_SYMBOL_MAP
from sniper.safe_io import safe_print

# Import the FIXED constants directly from event_monitor
from sniper.event_monitor import NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1


async def verify():
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(ALCHEMY_BASE_HTTP_URL))
    latest = await w3.eth.block_number

    safe_print(f"Latest block: {latest}")
    safe_print(f"NOTIFY_REWARD_TOPIC (v2): {NOTIFY_REWARD_TOPIC}")
    safe_print(f"NOTIFY_REWARD_TOPIC (v1): {NOTIFY_REWARD_TOPIC_V1}")
    safe_print("")

    # Use very small range: 100 blocks at a time, scan 2000 total
    CHUNK = 100
    TOTAL = 2000
    from_block = latest - TOTAL

    safe_print(f"Scanning {from_block} ~ {latest} ({TOTAL} blocks, chunk={CHUNK})")

    all_logs = []
    cur = from_block
    while cur < latest:
        end = min(cur + CHUNK - 1, latest)
        try:
            chunk_logs = await w3.eth.get_logs({
                "fromBlock": cur,
                "toBlock": end,
                "topics": [[NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1]],
            })
            if chunk_logs:
                all_logs.extend(chunk_logs)
                safe_print(f"  Block {cur}~{end}: {len(chunk_logs)} events")
        except Exception as e:
            err_str = str(e)[:80]
            safe_print(f"  Block {cur}~{end}: error ({err_str})")
        cur = end + 1

    safe_print(f"\nTotal events found: {len(all_logs)}")

    if all_logs:
        for i, log in enumerate(all_logs[:5]):
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            raw_topic0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]
            fixed = raw_topic0 if raw_topic0.startswith("0x") else ("0x" + raw_topic0)

            raw_rw = topics[2].hex() if isinstance(topics[2], bytes) else topics[2]
            reward_addr = "0x" + raw_rw[-40:]
            reward_lower = reward_addr.lower()
            symbol = TOKEN_SYMBOL_MAP.get(reward_lower, "UNKNOWN")
            in_wl = reward_lower in WHITELISTED_TOKEN_ADDRESSES

            old_match = raw_topic0 in (NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1)
            new_match = fixed in (NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1)

            safe_print(f"  [{i}] Block {log.get('blockNumber')}: {symbol} wl={in_wl} old_code_match={old_match} fixed_code_match={new_match}")
    else:
        safe_print("  (No events in this range -- try running during active Bribe period)")

    safe_print("\n--- Verification done ---")


if __name__ == "__main__":
    asyncio.run(verify())

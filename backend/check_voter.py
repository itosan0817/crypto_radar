"""
Aerodrome Voter.isWhitelistedToken の動作確認スクリプト (v2)
web3.py v7 の eth_call で直接テスト
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3, AsyncHTTPProvider
from sniper.config import ALCHEMY_BASE_HTTP_URL, AERODROME_VOTER_ADDRESS, WHITELISTED_TOKENS
from sniper.safe_io import safe_print

# web3.py v7 では ABI の inputs の name は "" でも "token" でも機能するはず
# ただし mapping の public getter は関数として ABI に記述する必要がある

ABI_VARIANT_1 = [
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "isWhitelistedToken",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    }
]

ABI_VARIANT_2 = [
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "isWhitelistedToken",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    }
]


async def main():
    w3 = AsyncWeb3(AsyncHTTPProvider(ALCHEMY_BASE_HTTP_URL))
    safe_print(f"接続状態: {await w3.is_connected()}")
    voter_addr = w3.to_checksum_address(AERODROME_VOTER_ADDRESS)
    usdc = w3.to_checksum_address(WHITELISTED_TOKENS["USDC"])
    weth = w3.to_checksum_address(WHITELISTED_TOKENS["WETH"])

    for label, abi in [("ABI variant1 (name=token)", ABI_VARIANT_1),
                        ("ABI variant2 (name='')", ABI_VARIANT_2)]:
        safe_print(f"\n=== {label} ===")
        voter = w3.eth.contract(address=voter_addr, abi=abi)
        for sym, addr_cs in [("USDC", usdc), ("WETH", weth)]:
            try:
                result = await voter.functions.isWhitelistedToken(addr_cs).call()
                safe_print(f"  {sym}: {result}")
            except Exception as e:
                safe_print(f"  {sym}: ERROR - {e}")

    # 方法3: eth_call で関数セレクタを直接エンコード
    safe_print("\n=== 直接 eth_call (function selector) ===")
    try:
        # isWhitelistedToken(address) のセレクタ = keccak256("isWhitelistedToken(address)")[:4]
        selector = w3.keccak(text="isWhitelistedToken(address)")[:4]
        # アドレスを32バイトにパディング
        padded = b"\x00" * 12 + bytes.fromhex(usdc[2:])
        call_data = (selector + padded).hex()
        result_hex = await w3.eth.call({
            "to": voter_addr,
            "data": "0x" + call_data
        })
        result_bool = bool(int(result_hex.hex(), 16)) if result_hex else False
        safe_print(f"  USDC isWhitelistedToken = {result_bool}")

        padded_weth = b"\x00" * 12 + bytes.fromhex(weth[2:])
        result_hex2 = await w3.eth.call({
            "to": voter_addr,
            "data": "0x" + (selector + padded_weth).hex()
        })
        result_bool2 = bool(int(result_hex2.hex(), 16)) if result_hex2 else False
        safe_print(f"  WETH isWhitelistedToken = {result_bool2}")
    except Exception as e:
        safe_print(f"  ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())

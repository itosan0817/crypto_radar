import asyncio
from web3 import AsyncWeb3
from web3.providers import WebSocketProvider

ALCHEMY_BASE_WSS_URL = 'wss://base-mainnet.g.alchemy.com/v2/lcau4KV3k-6quLk___bH2'

async def main():
    async with AsyncWeb3(WebSocketProvider(ALCHEMY_BASE_WSS_URL)) as w3:
        pass
        print("dir:", dir(w3))
        # also dir of eth
        print("dir w3.eth:", dir(w3.eth))

if __name__ == '__main__':
    asyncio.run(main())

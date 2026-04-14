"""
Bribe Sniper Simulator v3.0 - オンチェーン検証モジュール
Sugar/Voterコントラクトを用いたホワイトリスト確認・TVL計算・価格取得・スパイクチェックを行う
"""
from __future__ import annotations

import asyncio
import datetime
import time
from collections import deque
from typing import Optional

from web3 import AsyncWeb3

from sniper.config import (
    AERODROME_VOTER_ADDRESS, WETH_USDC_REF_POOL_ADDRESS,
    STABLECOIN_ADDRESSES, WETH_ADDRESS,
    VOTER_ABI, POOL_ABI, ERC20_ABI,
    MIN_TVL_USD, PRICE_SPIKE_THRESHOLD, PRICE_SPIKE_WINDOW_SEC,
)
from sniper.safe_io import safe_print

# 過去5分間の価格履歴（プールアドレス → deque(タイムスタンプ, 価格)）
_price_history: dict[str, deque] = {}


async def is_token_whitelisted(w3: AsyncWeb3, token_address: str) -> bool:
    """Voter コントラクトで対象トークンのホワイトリスト状態を確認する
    ※ Aerodrome V2 では isWhitelisted → isWhitelistedToken に変更済み
    RPC の一時失敗時は1回だけ再試行する。
    """
    voter = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(AERODROME_VOTER_ADDRESS),
        abi=VOTER_ABI
    )
    addr = AsyncWeb3.to_checksum_address(token_address)
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            return await voter.functions.isWhitelistedToken(addr).call()
        except Exception as e:
            last_err = e
            if attempt == 0:
                await asyncio.sleep(0.2)
    safe_print(
        f"⚠️ [SugarChecker] isWhitelistedToken チェック失敗（2回）: {last_err}",
    )
    return False


async def _get_token_decimals(w3: AsyncWeb3, token_address: str) -> int:
    """ERC20 decimals を取得。失敗時は18を返す"""
    try:
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )
        return await token.functions.decimals().call()
    except Exception:
        return 18


async def get_token_decimals(w3: AsyncWeb3, token_address: str) -> int:
    """ERC20 decimals（外部モジュール用の公開ラッパー）"""
    return await _get_token_decimals(w3, token_address)


async def _get_token_symbol(w3: AsyncWeb3, token_address: str) -> str:
    """ERC20 symbol を取得。失敗時はアドレス短縮形を返す"""
    try:
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )
        return await token.functions.symbol().call()
    except Exception:
        return token_address[:8] + "..."


async def get_weth_price_usd(w3: AsyncWeb3) -> float:
    """WETH の USD 価格を Aerodrome 参照プールから取得する"""
    try:
        pool = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(WETH_USDC_REF_POOL_ADDRESS),
            abi=POOL_ABI
        )
        # 1 WETH (1e18 wei) の USDC 出力量を取得
        weth_addr = AsyncWeb3.to_checksum_address(WETH_ADDRESS)
        amount_out = await pool.functions.getAmountOut(10**18, weth_addr).call()
        # USDC は 6 decimals
        return float(amount_out) / 1e6
    except Exception as e:
        safe_print(f"⚠️ [SugarChecker] WETH価格取得失敗、デフォルト3500を使用: {e}")
        return 3500.0  # フォールバック価格


async def get_token_price_usd(w3: AsyncWeb3, token_address: str, pool_address: str,
                               weth_price_usd: float = 0.0) -> float:
    """
    対象トークンのUSD価格をプールの getAmountOut から取得する。
    - stablecoin → 1.0
    - WETH       → weth_price_usd
    - その他     → プールで USDC or WETH と交換して推計
    """
    addr_lower = token_address.lower()

    # Stablecoin は 1.0 固定
    if addr_lower in STABLECOIN_ADDRESSES:
        return 1.0

    # WETH の場合
    if addr_lower == WETH_ADDRESS.lower():
        return weth_price_usd if weth_price_usd > 0 else await get_weth_price_usd(w3)

    try:
        pool = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(pool_address),
            abi=POOL_ABI
        )
        token0_raw = await pool.functions.token0().call()
        token0 = token0_raw.lower()
        token1_raw = (await pool.functions.token1().call()).lower()

        decimals = await _get_token_decimals(w3, token_address)
        amount_in = 10 ** decimals  # 1 トークン分

        amount_out = await pool.functions.getAmountOut(
            amount_in, AsyncWeb3.to_checksum_address(token_address)
        ).call()

        # 出力先トークンを判定
        other_token = token1_raw if token0 == addr_lower else token0

        if other_token in STABLECOIN_ADDRESSES:
            # 出力が USDC → そのまま USD価格
            other_decimals = await _get_token_decimals(w3, other_token)
            return float(amount_out) / (10 ** other_decimals)
        elif other_token == WETH_ADDRESS.lower():
            # 出力が WETH → WETH価格 で換算
            if weth_price_usd == 0:
                weth_price_usd = await get_weth_price_usd(w3)
            weth_amount = float(amount_out) / 1e18
            return weth_amount * weth_price_usd
        else:
            return 0.0

    except Exception as e:
        safe_print(f"⚠️ [SugarChecker] トークン価格取得失敗 {token_address[:8]}: {e}")
        return 0.0


async def get_pool_info(w3: AsyncWeb3, pool_address: str) -> dict:
    """
    プールの token0/token1 アドレス・シンボル・decimals を取得して返す。
    失敗時は空辞書を返す。
    """
    try:
        pool = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(pool_address),
            abi=POOL_ABI
        )
        token0 = await pool.functions.token0().call()
        token1 = await pool.functions.token1().call()
        sym0, sym1 = await asyncio.gather(
            _get_token_symbol(w3, token0),
            _get_token_symbol(w3, token1)
        )
        dec0, dec1 = await asyncio.gather(
            _get_token_decimals(w3, token0),
            _get_token_decimals(w3, token1)
        )
        return {
            "token0": token0,
            "token1": token1,
            "symbol0": sym0,
            "symbol1": sym1,
            "decimals0": dec0,
            "decimals1": dec1,
            "pool_name": f"{sym0}/{sym1}",
        }
    except Exception as e:
        safe_print(f"⚠️ [SugarChecker] プール情報取得失敗 {pool_address[:10]}: {e}")
        return {}


async def get_pool_tvl_usd(w3: AsyncWeb3, pool_address: str, pool_info: dict,
                            weth_price_usd: float = 0.0) -> float:
    """プールのTVLをUSDで計算する（両トークンの残高 × 価格）"""
    try:
        pool = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(pool_address),
            abi=POOL_ABI
        )
        reserves = await pool.functions.getReserves().call()
        r0, r1 = reserves[0], reserves[1]

        token0 = pool_info.get("token0", "")
        token1 = pool_info.get("token1", "")
        dec0   = pool_info.get("decimals0", 18)
        dec1   = pool_info.get("decimals1", 18)

        price0, price1 = await asyncio.gather(
            get_token_price_usd(w3, token0, pool_address, weth_price_usd),
            get_token_price_usd(w3, token1, pool_address, weth_price_usd)
        )

        tvl = (r0 / 10**dec0) * price0 + (r1 / 10**dec1) * price1
        return tvl

    except Exception as e:
        safe_print(f"⚠️ [SugarChecker] TVL計算失敗 {pool_address[:10]}: {e}")
        return 0.0


def check_price_spike(pool_address: str, current_price: float) -> bool:
    """
    過去 PRICE_SPIKE_WINDOW_SEC 秒間で価格が PRICE_SPIKE_THRESHOLD 以上上昇していれば True を返す。
    同時に現在価格を履歴に追記する。
    """
    now = time.time()
    key = pool_address.lower()

    if key not in _price_history:
        _price_history[key] = deque()

    # 古いエントリーを削除
    dq = _price_history[key]
    while dq and now - dq[0][0] > PRICE_SPIKE_WINDOW_SEC:
        dq.popleft()

    # スパイク判定
    spiked = False
    if dq:
        oldest_price = dq[0][1]
        if oldest_price > 0 and current_price > 0:
            change = (current_price - oldest_price) / oldest_price
            if change >= PRICE_SPIKE_THRESHOLD:
                spiked = True

    # 現在価格を追記
    dq.append((now, current_price))
    return spiked


async def get_pool_weight(w3: AsyncWeb3, pool_address: str) -> int:
    """Voter から対象プールの現在の投票ウェイトを取得する"""
    try:
        voter = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(AERODROME_VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        return await voter.functions.weights(
            AsyncWeb3.to_checksum_address(pool_address)
        ).call()
    except Exception as e:
        safe_print(f"⚠️ [SugarChecker] weight取得失敗: {e}")
        return 0


async def get_total_weight(w3: AsyncWeb3) -> int:
    """Voter から全体の投票ウェイトを取得する"""
    try:
        voter = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(AERODROME_VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        return await voter.functions.totalWeight().call()
    except Exception as e:
        safe_print(f"⚠️ [SugarChecker] totalWeight取得失敗: {e}")
        return 1

"""
Bribe Sniper Simulator v3.0 - NetEV計算エンジン
仕様書に基づくNetEV計算・スコアリング・約定シミュレーションを実装する
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from web3 import AsyncWeb3

from sniper.config import (
    MAX_POSITION_SIZE_S_JST, MAX_POSITION_SIZE_A_JST,
    GAS_COST_JST, JST_PER_USD,
    MIN_NET_EV_RATIO, MIN_ENTRY_SCORE,
    SCORE_S_GRADE, SCORE_A_GRADE,
    ENTRY_DELAY_MIN_SEC, ENTRY_DELAY_MAX_SEC,
    POOL_ABI,
)
from sniper.models import NetEVResult
from sniper.sugar_checker import get_token_price_usd, get_weth_price_usd


def calculate_entry_score(
    bribe_usd: float,
    tvl_usd: float,
    bribe_token_symbol: str,
    current_weight: int,
    total_weight: int,
) -> int:
    """
    エントリースコアを計算する（0〜100点）。
    Bribe/TVL比率・トークン品質・TVLサイズ・希薄化ペナルティを総合評価する。
    """
    score = 0

    # 1. Bribe/TVL 比率スコア（最大50点）
    if tvl_usd > 0:
        ratio = bribe_usd / tvl_usd
        score += min(50, int(ratio * 10_000))

    # 2. トークン品質ボーナス（最大20点）
    quality_bonus = {
        "USDC": 20, "WETH": 18, "cbBTC": 18,
        "wstETH": 16, "cbETH": 15, "LINK": 14,
        "AERO": 12, "SNX": 10, "WELL": 8, "DEGEN": 6,
    }
    score += quality_bonus.get(bribe_token_symbol, 5)

    # 3. TVLサイズボーナス（最大20点）
    if tvl_usd >= 1_000_000:
        score += 20
    elif tvl_usd >= 500_000:
        score += 16
    elif tvl_usd >= 100_000:
        score += 12
    elif tvl_usd >= 50_000:
        score += 8
    elif tvl_usd >= 10_000:
        score += 4

    # 4. 希薄化ペナルティ（既に多くの票が集中している場合）
    if total_weight > 0 and current_weight > 0:
        weight_ratio = current_weight / total_weight
        if weight_ratio > 0.3:
            score -= 15
        elif weight_ratio > 0.2:
            score -= 8

    return max(0, min(100, score))


def calculate_net_ev(
    entry_score: int,
    trade_size_jst: float,
    pool_liquidity_usd: float,
) -> NetEVResult:
    """
    NetEV（純期待値）を仕様書の計算式に基づいて算出する。

    NetEV = (E_r × T_s) - S_c - (G_c × 2)
      E_r = (EntryScore / 100) × 0.20
      S_c = TradeSize × (TradeSize / Liquidity × 0.5)
      G_c = GAS_COST_JST
    """
    result = NetEVResult()

    # グレード判定
    if entry_score >= SCORE_S_GRADE:
        grade = "S"
        t_s = MAX_POSITION_SIZE_S_JST
    elif entry_score >= SCORE_A_GRADE:
        grade = "A"
        t_s = MAX_POSITION_SIZE_A_JST
    else:
        result.reject_reason = f"スコア不足 ({entry_score} < {MIN_ENTRY_SCORE})"
        return result

    # E_r: 期待リターン率
    e_r = (entry_score / 100) * 0.20

    # S_c: スリッページコスト (JST換算)
    trade_size_usd = t_s / JST_PER_USD
    if pool_liquidity_usd > 0:
        slippage_rate = trade_size_usd / pool_liquidity_usd * 0.5
    else:
        slippage_rate = 0.01  # 流動性不明の場合は1%とみなす
    s_c_usd = trade_size_usd * slippage_rate
    s_c_jst = s_c_usd * JST_PER_USD

    # G_c: ガス代（往復）
    g_c_total = GAS_COST_JST * 2  # = 150 JST

    # NetEV 計算
    net_ev = (e_r * t_s) - s_c_jst - g_c_total
    net_ev_ratio = net_ev / t_s if t_s > 0 else 0

    result.entry_score          = entry_score
    result.grade                = grade
    result.trade_size_jst       = t_s
    result.expected_return_rate = e_r
    result.slippage_cost_jst    = s_c_jst
    result.gas_cost_jst         = g_c_total
    result.net_ev_jst           = net_ev
    result.net_ev_ratio         = net_ev_ratio

    # エントリー判定: NetEV >= 1.5% of T_s かつ score >= 50
    if net_ev_ratio >= MIN_NET_EV_RATIO and entry_score >= MIN_ENTRY_SCORE:
        result.is_valid = True
    else:
        if net_ev_ratio < MIN_NET_EV_RATIO:
            result.reject_reason = (
                f"NetEV比率不足 ({net_ev_ratio*100:.2f}% < {MIN_NET_EV_RATIO*100:.1f}%)"
            )
        else:
            result.reject_reason = f"スコア不足 ({entry_score} < {MIN_ENTRY_SCORE})"

    return result


async def simulate_entry_price(
    w3: AsyncWeb3,
    pool_address: str,
    target_token_address: str,
    trade_size_jst: float,
) -> tuple[float, float]:
    """
    約定遅延を模倣し、スリッページ加算後の仮想エントリー価格を返す。
    Returns: (entry_price_usd, delay_seconds)
    """
    # 2〜5秒のランダム遅延
    delay = random.uniform(ENTRY_DELAY_MIN_SEC, ENTRY_DELAY_MAX_SEC)
    await asyncio.sleep(delay)

    # 遅延後のオンチェーン価格を取得
    weth_price = await get_weth_price_usd(w3)
    spot_price = await get_token_price_usd(w3, target_token_address, pool_address, weth_price)

    if spot_price <= 0:
        return 0.0, delay

    # スリッページを加算（買いの場合、スポット価格より高くなる）
    trade_size_usd = trade_size_jst / 150.0
    if spot_price > 0:
        pool = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(pool_address),
            abi=POOL_ABI
        )
        try:
            liq = await _estimate_liquidity_usd(w3, pool, weth_price)
            slippage_rate = trade_size_usd / max(liq, 1) * 0.5
        except Exception:
            slippage_rate = 0.005  # デフォルト0.5%

        entry_price = spot_price * (1 + slippage_rate)
    else:
        entry_price = spot_price

    return entry_price, delay


async def _estimate_liquidity_usd(w3: AsyncWeb3, pool_contract, weth_price: float) -> float:
    """プールの流動性をUSDで概算する（内部ヘルパー）"""
    try:
        reserves = await pool_contract.functions.getReserves().call()
        # 両reserve合計 × WETH価格（簡易版）
        # 実際はトークンのdecimalsと価格が必要だが、ここでは概算
        return float(reserves[0] + reserves[1]) / 1e18 * weth_price
    except Exception:
        return 100_000.0  # フォールバック

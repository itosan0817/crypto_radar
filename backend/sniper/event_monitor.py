"""
Bribe Sniper Simulator v3.0 - NotifyReward イベント監視モジュール
AerodromeのExternalBribeコントラクトからNotifyRewardイベントを検知し、
フィルタリング → NetEV計算 → 仮想エントリーまでの一連の処理を行う
"""
from __future__ import annotations

import asyncio
import datetime

from web3 import AsyncWeb3

from sniper.config import (
    WHITELISTED_TOKEN_ADDRESSES, TOKEN_SYMBOL_MAP,
    MIN_TVL_USD, AERODROME_VOTER_ADDRESS, VOTER_ABI, JST_PER_USD,
)
from sniper.models import BribeEvent, Position, PositionStatus
from sniper.sugar_checker import (
    is_token_whitelisted, get_pool_info, get_pool_tvl_usd,
    get_weth_price_usd, get_token_price_usd,
    check_price_spike, get_pool_weight, get_total_weight,
)
from sniper.net_ev_engine import (
    calculate_entry_score, calculate_net_ev, simulate_entry_price,
)
from sniper.firestore_sniper import FirestoreSniperService
from sniper.discord_sniper import notify_entry, notify_rejected
from sniper.position_manager import PositionManager

# ──────────────────────────────────────────────
# NotifyReward イベントシグネチャ
# Aerodrome v2 BribeVotingReward の正確な定義:
#   event NotifyReward(address indexed from, address indexed reward,
#                      uint256 indexed epoch, uint256 amount)
# Topic0 = keccak256("NotifyReward(address,address,uint256,uint256)")
#         = 0x4461044129b0933758b29c9b1f237f374765d75240212701764653556271966a
# ──────────────────────────────────────────────
NOTIFY_REWARD_TOPIC = "0x4461044129b0933758b29c9b1f237f374765d75240212701764653556271966a"

# 旧バージョン互換 (indexed epoch なし: from, reward, amount)
NOTIFY_REWARD_TOPIC_V1 = (
    "0x" + AsyncWeb3.keccak(
        text="NotifyReward(address,address,uint256)"
    ).hex()
)


async def start_bribe_monitor(w3_wss: AsyncWeb3, position_manager: PositionManager) -> None:
    """
    NotifyReward イベントを WebSocket で購読し、
    有効なBribeイベントを検知したらエントリー判定ロジックを非同期で起動する。
    """
    # ホワイトリストトークンのチェックサムアドレスリストをトピック形式に変換
    reward_topics = [
        "0x" + "0" * 24 + addr.replace("0x", "")
        for addr in WHITELISTED_TOKEN_ADDRESSES
    ]

    subscription_id = await w3_wss.eth.subscribe(
        "logs",
        {
            # NotifyReward イベント (v1/v2 両方を購読)
            "topics": [[NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1]],
        }
    )
    print(f"📡 [EventMonitor] NotifyReward 購読開始 ID: {subscription_id}", flush=True)

    async for response in w3_wss.socket.process_subscriptions():
        try:
            log = response.get("result", {})
            if not log:
                continue

            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            topic0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]

            # NotifyReward イベントのみ処理（v1/v2 両方）
            if topic0 not in (NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1):
                continue

            # topics[2] = reward token address (indexed)
            raw_reward_topic = topics[2].hex() if isinstance(topics[2], bytes) else topics[2]
            reward_addr = "0x" + raw_reward_topic[-40:]
            reward_lower = reward_addr.lower()

            # ① ホワイトリストチェック（インメモリ・高速）
            if reward_lower not in WHITELISTED_TOKEN_ADDRESSES:
                continue

            token_symbol = TOKEN_SYMBOL_MAP[reward_lower]
            external_bribe_addr = log.get("address", "")
            tx_hash = log.get("transactionHash", b"")
            if isinstance(tx_hash, bytes):
                tx_hash = tx_hash.hex()

            print(
                f"🔔 [EventMonitor] NotifyReward 検知: {token_symbol} "
                f"from {external_bribe_addr[:10]}... tx={tx_hash[:10]}...",
                flush=True
            )

            # 以降の処理を非同期タスクで実行（イベントループをブロックしない）
            asyncio.create_task(
                _process_bribe_event(
                    w3_wss, position_manager,
                    log, reward_addr, token_symbol,
                    external_bribe_addr, tx_hash
                )
            )

        except Exception as e:
            print(f"⚠️ [EventMonitor] ログ処理エラー: {e}", flush=True)


async def _process_bribe_event(
    w3: AsyncWeb3,
    position_manager: PositionManager,
    log: dict,
    reward_addr: str,
    token_symbol: str,
    external_bribe_addr: str,
    tx_hash: str,
) -> None:
    """
    1件のBribeイベントを評価してエントリー判定を行うメインロジック。
    以下のフィルタリングを順番に適用する:
    1. Voter isWhitelisted チェック
    2. プール情報取得
    3. TVL >= $10,000 チェック
    4. 価格急騰 (+5%) チェック
    5. NetEV 計算 → エントリー判定
    """
    try:
        # ② onchain isWhitelisted チェック
        whitelisted = await is_token_whitelisted(w3, reward_addr)
        if not whitelisted:
            print(f"  ⛔ isWhitelisted=False: {token_symbol}", flush=True)
            return

        # Bribeトランザクションのブロックからプールアドレスを特定する
        # ExternalBribe → Gauge → Pool の探索は複雑なため、
        # ここではプールアドレスを直接ログの from 先から推測するか、
        # TX の to アドレス（ExternalBribe）から Voter で逆引きする
        # ※ 実環境ではVoterのgaugeOf(bribe_addr)相当の呼び出しが必要
        # シミュレーターでは ExternalBrideアドレスをプールの代理として使用
        pool_address = await _resolve_pool_address(w3, external_bribe_addr)
        if not pool_address:
            print(f"  ⚠️ プールアドレス解決失敗 bribe={external_bribe_addr[:10]}", flush=True)
            return

        # プール基本情報を取得
        weth_price = await get_weth_price_usd(w3)
        pool_info = await get_pool_info(w3, pool_address)
        if not pool_info:
            return

        pool_name  = pool_info.get("pool_name", "UNKNOWN/UNKNOWN")
        token0     = pool_info.get("token0", "")
        token1     = pool_info.get("token1", "")
        decimals0  = pool_info.get("decimals0", 18)
        decimals1  = pool_info.get("decimals1", 18)

        # ③ TVL チェック
        tvl_usd = await get_pool_tvl_usd(w3, pool_address, pool_info, weth_price)
        if tvl_usd < MIN_TVL_USD:
            print(f"  ⛔ TVL不足: ${tvl_usd:,.0f} < ${MIN_TVL_USD:,.0f} ({pool_name})", flush=True)
            return

        # Bribeトークンのdecimalsとamountを解析
        data_hex = log.get("data", "0x")
        bribe_amount_raw = _decode_amount(log, data_hex)
        bribe_token_decimals = 6 if token_symbol == "USDC" else 18
        bribe_amount = bribe_amount_raw / (10 ** bribe_token_decimals)

        # Bribeトークンの USD価格を取得してUSD換算
        bribe_token_price = 1.0 if token_symbol in ("USDC", "USDT") else \
            await get_token_price_usd(w3, reward_addr, pool_address, weth_price)
        bribe_amount_usd = bribe_amount * bribe_token_price

        # ④ ターゲットトークン（非stable側）を決定して価格急騰チェック
        target_token = _pick_target_token(token0, token1)
        current_price = await get_token_price_usd(w3, target_token, pool_address, weth_price)

        if check_price_spike(pool_address, current_price):
            reason = f"過去5分で価格が+5%以上急騰済み (現在 ${current_price:.4f})"
            print(f"  ⛔ スパイク検出: {pool_name} {reason}", flush=True)
            asyncio.create_task(notify_rejected(pool_name, token_symbol, reason))
            return

        # ⑤ 希薄化チェック（weight取得）
        current_weight, total_weight = await asyncio.gather(
            get_pool_weight(w3, pool_address),
            get_total_weight(w3)
        )

        # ⑥ エントリースコアとNetEVを計算
        score = calculate_entry_score(
            bribe_usd        = bribe_amount_usd,
            tvl_usd          = tvl_usd,
            bribe_token_symbol = token_symbol,
            current_weight   = current_weight,
            total_weight     = total_weight,
        )

        # ポジションサイズ決定（S/A 判定もcalculate_net_ev内で実施）
        net_ev_result = calculate_net_ev(
            entry_score       = score,
            trade_size_jst    = 6000.0,  # calculate_net_ev内で最終T_sを決定
            pool_liquidity_usd = tvl_usd,
        )

        print(
            f"  📊 スコア={score}/100 グレード={net_ev_result.grade or 'N/A'} "
            f"NetEV={net_ev_result.net_ev_jst:+.1f}JST 有効={net_ev_result.is_valid}",
            flush=True
        )

        if not net_ev_result.is_valid:
            reason = net_ev_result.reject_reason
            print(f"  ⛔ エントリー棄却: {reason}", flush=True)
            asyncio.create_task(notify_rejected(pool_name, token_symbol, reason))
            return

        # ⑦ 約定シミュレーション（遅延 + スリッページ加算）
        entry_price, delay_sec = await simulate_entry_price(
            w3, pool_address, target_token, net_ev_result.trade_size_jst
        )

        if entry_price <= 0:
            print(f"  ⚠️ エントリー価格取得失敗: {pool_name}", flush=True)
            return

        # ⑧ ポジション生成
        pos = Position(
            position_id    = Position.generate_id(pool_name),
            pool_name      = pool_name,
            pool_address   = pool_address,
            bribe_token    = token_symbol,
            grade          = net_ev_result.grade,
            entry_price_usd = entry_price,
            entry_size_jst  = net_ev_result.trade_size_jst,
            entry_size_usd  = net_ev_result.trade_size_jst / JST_PER_USD,
            net_ev_jst     = net_ev_result.net_ev_jst,
            entered_at     = datetime.datetime.now(datetime.timezone.utc),
        )

        # ポジションマネージャーに登録
        position_manager.add_position(pos, target_token)

        # Firestore保存・Discord通知（非同期）
        asyncio.create_task(
            _save_and_notify_entry(pos, net_ev_result.net_ev_jst, delay_sec,
                                   bribe_amount_usd, tvl_usd, score)
        )

    except Exception as e:
        import traceback
        print(f"❌ [EventMonitor] Bribeイベント処理エラー: {e}", flush=True)
        traceback.print_exc()


async def _save_and_notify_entry(
    pos: Position,
    net_ev_jst: float,
    delay_sec: float,
    bribe_amount_usd: float,
    tvl_usd: float,
    score: int,
) -> None:
    """Firestore保存とDiscord通知をまとめて実行する"""
    FirestoreSniperService.save_entry(pos)
    await notify_entry(pos, net_ev_jst, delay_sec, bribe_amount_usd, tvl_usd, score)


# 🚩 ExternalBribeアドレス → プールアドレス の既知のマッピング（主要プール用）
# 逆引きAPIが利用できない場合のフォールバックとして使用
KNOWN_BRIBE_TO_POOL = {
    "0x78D1CefD2Cc5975d9e5bB10f63EAeb3B8647000d".lower(): "0xcDAc0d6c6C59727a65f871236188350531885C43", # WETH/USDC (vAMM)
    "0x5ee1D683c3167D3a027958564D120B8888888888".lower(): "0x940181a94A35A4569E4529A3CDfB74e38FD98631", # AERO/USDC (vAMM) 等
}


async def _resolve_pool_address(w3: AsyncWeb3, bribe_addr: str) -> str:
    """
    ExternalBribeアドレスから対応するプールアドレスを解決する。
    1. 既知のマッピングを確認（高速・確実）
    2. Voter.gaugeToBribe の逆引き（全ゲージを検索）
    3. ExternalBribe コントラクトの pool() / gauge() 関数を試行
    """
    addr_lower = bribe_addr.lower()

    # ① 既知のマッピングを確認 (高速・確実)
    if addr_lower in KNOWN_BRIBE_TO_POOL:
        return AsyncWeb3.to_checksum_address(KNOWN_BRIBE_TO_POOL[addr_lower])

    # ② Voter.poolForGauge を経由した逆引きを試行
    try:
        voter = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(AERODROME_VOTER_ADDRESS),
            abi=VOTER_ABI
        )
        # ExternalBribe コントラクトが gauge() 関数を持っている場合に試みる
        gauge_abi = [{"inputs": [], "name": "gauge",
                      "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                      "stateMutability": "view", "type": "function"}]
        bribe_contract = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(bribe_addr),
            abi=gauge_abi
        )
        try:
            gauge_addr = await bribe_contract.functions.gauge().call()
            pool = await voter.functions.poolForGauge(gauge_addr).call()
            if pool and pool != "0x0000000000000000000000000000000000000000":
                print(f"  ✅ Voter.poolForGauge 経由でプール特定: {pool[:10]}...", flush=True)
                return pool
        except Exception:
            pass

    except Exception:
        pass

    # ③ ExternalBribe コントラクトの pool() を直接試みる
    try:
        pool_abi_minimal = [
            {"inputs": [], "name": "pool",
             "outputs": [{"internalType": "address", "name": "", "type": "address"}],
             "stateMutability": "view", "type": "function"},
        ]
        bribe_contract = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(bribe_addr),
            abi=pool_abi_minimal
        )
        pool = await bribe_contract.functions.pool().call()
        if pool and pool != "0x0000000000000000000000000000000000000000":
            return pool
    except Exception:
        pass

    print(f"  ⚠️ プールアドレス解決失敗 bribe={bribe_addr[:10]}", flush=True)
    return ""


def _decode_amount(log: dict, data_hex: str) -> int:
    """
    ログのdataフィールドから amount (最後の uint256) をデコードする。
    v2では epoch + amount の2値、v1では amount のみ。
    """
    try:
        if isinstance(data_hex, bytes):
            data_bytes = data_hex
        else:
            data_hex = data_hex.lower().replace("0x", "")
            data_bytes = bytes.fromhex(data_hex)

        if len(data_bytes) >= 32:
            # 最後の32バイトが amount
            return int.from_bytes(data_bytes[-32:], "big")
    except Exception:
        pass
    return 0


def _pick_target_token(token0: str, token1: str) -> str:
    """
    プールの2トークンのうち、価格追跡対象（非stable側）を選択する。
    両方stable/両方non-stableの場合は token0 を選択する。
    """
    from sniper.config import STABLECOIN_ADDRESSES, WETH_ADDRESS
    t0_lower = token0.lower()
    t1_lower = token1.lower()

    t0_is_stable = t0_lower in STABLECOIN_ADDRESSES
    t1_is_stable = t1_lower in STABLECOIN_ADDRESSES

    if t0_is_stable and not t1_is_stable:
        return token1  # token1 が非stable → それをターゲットに
    elif t1_is_stable and not t0_is_stable:
        return token0  # token0 が非stable
    else:
        # 両方同じ性質の場合は token0 をデフォルトに
        return token0

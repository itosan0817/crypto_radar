"""
Bribe Sniper Simulator v3.0 - NotifyReward イベント監視モジュール
AerodromeのExternalBribeコントラクトからNotifyRewardイベントを検知し、
フィルタリング → NetEV計算 → 仮想エントリーまでの一連の処理を行う
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os

from web3 import AsyncWeb3

from sniper.config import (
    WHITELISTED_TOKEN_ADDRESSES,
    TOKEN_SYMBOL_MAP,
    STABLECOIN_ADDRESSES,
    MIN_TVL_USD,
    AERODROME_VOTER_ADDRESS,
    VOTER_ABI,
    JST_PER_USD,
)
from sniper.models import BribeEvent, Position, PositionStatus
from sniper.sugar_checker import (
    is_token_whitelisted,
    get_pool_info,
    get_pool_tvl_usd,
    get_weth_price_usd,
    get_token_price_usd,
    get_token_decimals,
    check_price_spike,
    get_pool_weight,
    get_total_weight,
)
from sniper.net_ev_engine import (
    calculate_entry_score, calculate_net_ev, simulate_entry_price,
)
from sniper.firestore_sniper import FirestoreSniperService
from sniper.discord_sniper import notify_entry, notify_rejected
from sniper.position_manager import PositionManager
from sniper.safe_io import safe_print

# ──────────────────────────────────────────────
# NotifyReward イベントシグネチャ
# Aerodrome v2 BribeVotingReward の正確な定義:
#   event NotifyReward(address indexed from, address indexed reward,
#                      uint256 indexed epoch, uint256 amount)
# ──────────────────────────────────────────────
# ※ ハードコーディングのハッシュ値が不正だったため、
#    keccak256 で動的に生成して正確な値を使用する
NOTIFY_REWARD_TOPIC = (
    "0x" + AsyncWeb3.keccak(
        text="NotifyReward(address,address,uint256,uint256)"
    ).hex()
)

# 旧バージョン互換 (indexed epoch なし: from, reward, amount)
NOTIFY_REWARD_TOPIC_V1 = (
    "0x" + AsyncWeb3.keccak(
        text="NotifyReward(address,address,uint256)"
    ).hex()
)

_NOTIFY_SIG_LO = {NOTIFY_REWARD_TOPIC.lower(), NOTIFY_REWARD_TOPIC_V1.lower()}


def _hex_digits_from_rpc_field(value: object) -> str:
    """RPC の topic / address / tx を小文字の16進数字列のみに正規化（型ゆれ対策）。"""
    if value is None:
        return ""
    try:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).hex().lower()
        if hasattr(value, "hex") and callable(value.hex):
            h = value.hex()
            if isinstance(h, bytes):
                h = h.decode("ascii", errors="ignore")
            if isinstance(h, str):
                return h.lower().replace("0x", "")
    except Exception:
        pass
    s = str(value).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return "".join(c for c in s if c in "0123456789abcdef")


def _canonical_topic0(value: object) -> str | None:
    d = _hex_digits_from_rpc_field(value)
    if not d:
        return None
    if len(d) > 64:
        d = d[-64:]
    if len(d) != 64:
        return None
    return "0x" + d


def _address_from_topic(value: object) -> str | None:
    """indexed address は 32 バイト topic の下位 20 バイト（末尾40hex）。"""
    d = _hex_digits_from_rpc_field(value)
    if len(d) < 40:
        return None
    tail = d[-40:]
    if len(tail) != 40 or any(c not in "0123456789abcdef" for c in tail):
        return None
    return "0x" + tail


def _address_from_log_contract(value: object) -> str | None:
    """ログの contract address（20 バイトまたは hex 文字列）。"""
    d = _hex_digits_from_rpc_field(value)
    if len(d) < 40:
        return None
    return "0x" + d[-40:]


def _tx_hash_from_log(value: object) -> str:
    d = _hex_digits_from_rpc_field(value)
    if len(d) > 64:
        d = d[-64:]
    if not d:
        return "0x"
    return "0x" + d


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

    # topic0=シグネチャ, topic1=from 任意, topic2=報酬トークン（ホワイトリストのみ）
    strict_filter = {
        "topics": [
            [NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1],
            None,
            reward_topics,
        ],
    }
    loose_filter = {
        "topics": [[NOTIFY_REWARD_TOPIC, NOTIFY_REWARD_TOPIC_V1]],
    }
    try:
        subscription_id = await w3_wss.eth.subscribe("logs", strict_filter)
    except Exception as e:
        safe_print(
            f"⚠️ [EventMonitor] 報酬トークン絞り込み購読に失敗、緩いフィルタにフォールバック: {e}",
        )
        subscription_id = await w3_wss.eth.subscribe("logs", loose_filter)
    safe_print(f"📡 [EventMonitor] NotifyReward 購読開始 ID: {subscription_id}")

    async for response in w3_wss.socket.process_subscriptions():
        try:
            log = response.get("result", {})
            if not log:
                continue

            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            topic0 = _canonical_topic0(topics[0])
            if not topic0 or topic0.lower() not in _NOTIFY_SIG_LO:
                continue

            reward_addr = _address_from_topic(topics[2])
            if not reward_addr:
                continue
            reward_lower = reward_addr.lower()

            # ① ホワイトリストチェック（インメモリ・高速）
            if reward_lower not in WHITELISTED_TOKEN_ADDRESSES:
                continue

            token_symbol = TOKEN_SYMBOL_MAP[reward_lower]

            external_bribe_addr = _address_from_log_contract(log.get("address"))
            if not external_bribe_addr:
                continue

            tx_hash = _tx_hash_from_log(log.get("transactionHash", b""))

            safe_print(
                f"🔔 [EventMonitor] NotifyReward 検知: {token_symbol} "
                f"from {external_bribe_addr[:10]}... tx={tx_hash[:10]}..."
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
            import traceback
            safe_print(f"⚠️ [EventMonitor] ログ処理エラー: {e}")
            safe_print(traceback.format_exc())


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
            safe_print(f"  ⛔ isWhitelisted=False: {token_symbol}")
            return

        # Bribeトランザクションのブロックからプールアドレスを特定する
        # ExternalBribe → Gauge → Pool の探索は複雑なため、
        # ここではプールアドレスを直接ログの from 先から推測するか、
        # TX の to アドレス（ExternalBribe）から Voter で逆引きする
        # ※ 実環境ではVoterのgaugeOf(bribe_addr)相当の呼び出しが必要
        # シミュレーターでは ExternalBrideアドレスをプールの代理として使用
        pool_address = await _resolve_pool_address(w3, external_bribe_addr)
        if not pool_address:
            safe_print(f"  ⚠️ プールアドレス解決失敗 bribe={external_bribe_addr[:10]}")
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
            safe_print(f"  ⛔ TVL不足: ${tvl_usd:,.0f} < ${MIN_TVL_USD:,.0f} ({pool_name})")
            return

        # Bribeトークンの decimals と amount（オンチェーン decimals で正規化）
        data_hex = log.get("data", "0x")
        bribe_amount_raw = _decode_amount(log, data_hex)
        bribe_token_decimals = await get_token_decimals(w3, reward_addr)
        bribe_amount = bribe_amount_raw / (10 ** bribe_token_decimals)

        # Bribeトークンの USD 価格（ステーブルは STABLECOIN_ADDRESSES で 1.0 近似）
        reward_lower = reward_addr.lower()
        bribe_token_price = (
            1.0
            if reward_lower in STABLECOIN_ADDRESSES
            else await get_token_price_usd(w3, reward_addr, pool_address, weth_price)
        )
        bribe_amount_usd = bribe_amount * bribe_token_price

        # ④ ターゲットトークン（非stable側）を決定して価格急騰チェック
        target_token = _pick_target_token(token0, token1)
        current_price = await get_token_price_usd(w3, target_token, pool_address, weth_price)

        if check_price_spike(pool_address, current_price):
            reason = f"過去5分で価格が+5%以上急騰済み (現在 ${current_price:.4f})"
            safe_print(f"  ⛔ スパイク検出: {pool_name} {reason}")
            await notify_rejected(pool_name, token_symbol, reason)
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

        # ポジションサイズ決定（S/A 上限は calculate_net_ev 内の T_s）
        net_ev_result = calculate_net_ev(
            entry_score=score,
            pool_liquidity_usd=tvl_usd,
        )

        safe_print(
            f"  📊 スコア={score}/100 グレード={net_ev_result.grade or 'N/A'} "
            f"NetEV={net_ev_result.net_ev_jst:+.1f}JST 有効={net_ev_result.is_valid}"
        )

        if not net_ev_result.is_valid:
            reason = net_ev_result.reject_reason
            safe_print(f"  ⛔ エントリー棄却: {reason}")
            await notify_rejected(pool_name, token_symbol, reason)
            return

        # ⑦ 約定シミュレーション（遅延 + スリッページ加算）
        entry_price, delay_sec = await simulate_entry_price(
            w3, pool_address, target_token, net_ev_result.trade_size_jst
        )

        if entry_price <= 0:
            safe_print(f"  ⚠️ エントリー価格取得失敗: {pool_name}")
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

        # Firestore 保存 → 成功時のみメモリ登録と Discord（保存失敗時は未登録のまま）
        if not FirestoreSniperService.save_entry(pos):
            safe_print(
                f"⚠️ [EventMonitor] Firestore 保存失敗のためポジション未登録: {pos.position_id}",
            )
            return

        position_manager.add_position(pos, target_token)
        await _notify_entry_discord(
            pos, net_ev_result.net_ev_jst, delay_sec,
            bribe_amount_usd, tvl_usd, score,
        )

    except Exception as e:
        import traceback
        safe_print(f"❌ [EventMonitor] Bribeイベント処理エラー: {e}")
        safe_print(traceback.format_exc())


async def _notify_entry_discord(
    pos: Position,
    net_ev_jst: float,
    delay_sec: float,
    bribe_amount_usd: float,
    tvl_usd: float,
    score: int,
) -> None:
    """エントリー確定後の Discord 通知（保存・登録は呼び出し元で済ませている）"""
    await notify_entry(pos, net_ev_jst, delay_sec, bribe_amount_usd, tvl_usd, score)


# 🚩 ExternalBribeアドレス → プールアドレス の既知のマッピング（主要プール用）
BRIBE_MAPPING_FILE = os.path.join(os.path.dirname(__file__), "bribe_mapping.json")

def _load_bribe_mapping() -> dict[str, str | None]:
    """ファイルからマッピングを読み込む。存在しない場合は初期値を返す。"""
    default_mapping = {
        "0x78d1cefd2cc5975d9e5bb10f63eaeb3b8647000d": "0xcDAc0d6c6C59727a65f871236188350531885C43",
    }
    if not os.path.exists(BRIBE_MAPPING_FILE):
        return default_mapping
    try:
        with open(BRIBE_MAPPING_FILE, "r") as f:
            data = json.load(f)
            # 全て小文字で正規化
            return {k.lower(): v for k, v in data.items()}
    except Exception as e:
        safe_print(f"⚠️ [EventMonitor] マッピング読み込み失敗: {e}")
        return default_mapping

def _save_bribe_mapping(mapping: dict[str, str | None]):
    """マッピングをファイルに保存する。"""
    try:
        with open(BRIBE_MAPPING_FILE, "w") as f:
            json.dump(mapping, f, indent=2)
    except Exception as e:
        safe_print(f"⚠️ [EventMonitor] マッピング保存失敗: {e}")

# 初期ロード
KNOWN_BRIBE_TO_POOL: dict[str, str | None] = _load_bribe_mapping()

_voter_scan_lock = asyncio.Lock()

async def _resolve_pool_address(w3: AsyncWeb3, bribe_addr: str) -> str:
    """
    ExternalBribeアドレスから対応するプールアドレスを解決する（完全自動メンテフリー版）。
    1. インメモリキャッシュを最優先で確認 (負のキャッシュ含む)
    2. Voter の登録全プールをスキャンし、Bribe を持つ Pool を探し出してキャッシュに永続化
    """
    addr_lower = bribe_addr.lower()

    # ① キャッシュの確認 (高速・確実)
    if addr_lower in KNOWN_BRIBE_TO_POOL:
        val = KNOWN_BRIBE_TO_POOL[addr_lower]
        if val is None:
            # 過去にスキャンして見つからなかったものは即座にリターン
            return ""
        return AsyncWeb3.to_checksum_address(val)

    # ② 初回未知のBribeが来た場合のみ、Voterからプール全件スキャンを実施(Auto-Mapping)
    async with _voter_scan_lock:
        # ロック待ちの間に別のタスクが解決しているかもしれないので再度確認
        if addr_lower in KNOWN_BRIBE_TO_POOL:
            val = KNOWN_BRIBE_TO_POOL[addr_lower]
            return AsyncWeb3.to_checksum_address(val) if val else ""

        safe_print(f"  🔍 未知のBribe ({bribe_addr[:10]}) を検知。Voterの全台帳を自動スキャンして追跡します...")
        found_target = False
        try:
            voter = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(AERODROME_VOTER_ADDRESS),
                abi=[
                    {"inputs": [], "name": "length", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
                    {"inputs": [{"type": "uint256"}], "name": "pools", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
                    {"inputs": [{"type": "address"}], "name": "gauges", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
                    {"inputs": [{"type": "address"}], "name": "external_bribes", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}
                ]
            )
            
            length = await voter.functions.length().call()
            # 最新のプールから逆順に探す (新規Bribeほど新しいプールの確率が高いため)
            for i in range(length - 1, -1, -1):
                try:
                    pool = await voter.functions.pools(i).call()
                    if not pool or pool == "0x0000000000000000000000000000000000000000": continue
                    
                    gauge = await voter.functions.gauges(pool).call()
                    if not gauge or gauge == "0x0000000000000000000000000000000000000000": continue
                    
                    ext_bribe = await voter.functions.external_bribes(gauge).call()
                    if ext_bribe:
                        ext_lower = ext_bribe.lower()
                        # 副産物としてスキャンできたものも全てキャッシュに学習させておく
                        if ext_lower not in KNOWN_BRIBE_TO_POOL:
                            KNOWN_BRIBE_TO_POOL[ext_lower] = pool
                        
                        if ext_lower == addr_lower:
                            safe_print(f"  ✅ 自動マッピング完了: {ext_bribe[:10]} -> Pool {pool[:10]}")
                            found_target = True
                except Exception:
                    continue
            
            # 全件スキャン後、ファイルに保存
            _save_bribe_mapping(KNOWN_BRIBE_TO_POOL)
            
        except Exception as e:
            safe_print(f"  ⚠️ Voterスキャン中にエラー発生: {e}")

        if not found_target:
            # 負のキャッシュに登録 (None を入れる)
            KNOWN_BRIBE_TO_POOL[addr_lower] = None
            _save_bribe_mapping(KNOWN_BRIBE_TO_POOL)
            safe_print(f"  ⚠️ プールアドレス完全解決不能 bribe={bribe_addr[:10]} (負のキャッシュに登録しました)")

    val = KNOWN_BRIBE_TO_POOL.get(addr_lower)
    return AsyncWeb3.to_checksum_address(val) if val else ""


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

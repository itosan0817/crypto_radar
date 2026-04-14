"""
Bribe Sniper Simulator v3.0 - 設定・定数・ABI管理
全モジュールから参照される中心的な設定ファイル
"""
from __future__ import annotations

import os
import sys
from sniper.safe_io import safe_print

# 親ディレクトリをパスに追加（config/settings.py を参照するため）
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    safe_print("⚠️ python-dotenv が未インストールのため .env 自動読込をスキップします。")

# ==========================================
# 🌐 RPC エンドポイント
# ==========================================
ALCHEMY_BASE_WSS_URL = os.getenv("ALCHEMY_BASE_WSS_URL")
ALCHEMY_BASE_HTTP_URL = os.getenv("ALCHEMY_BASE_HTTP_URL")

# Discord の Webhook URL
DISCORD_WEBHOOK_URL = os.getenv("BRIBE_WEBHOOK_URL")


# ==========================================
# 🔗 Aerodrome コントラクトアドレス (Base Chain)
# ==========================================
AERODROME_VOTER_ADDRESS     = "0x16613524e02ad97eDfeF371bC883F2F5d6C480A5"
# WETH/USDC基準プール（WETH価格の取得用。Aerodromeの主要プールから確認すること）
WETH_USDC_REF_POOL_ADDRESS  = "0xcDAc0d6c6C59727a65f871236188350531885C43"

# ==========================================
# ✅ 有効な報酬トークン ホワイトリスト (Base Chain)
# ==========================================
WHITELISTED_TOKENS: dict[str, str] = {
    # 既存 (10個)
    "USDC":   "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "WETH":   "0x4200000000000000000000000000000000000006",
    "AERO":   "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    "cbBTC":  "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
    "wstETH": "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452",
    "cbETH":  "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
    "DEGEN":  "0x4ed4E862860beD51a9570b96d89aAf5E1B0Efefed",
    "WELL":   "0xA88594D404727625A9437C3f886C7643872296AE",
    "SNX":    "0x22e6966B799c4D5B13BE962E1D117b56327FDa66",
    "LINK":   "0x88Fb150BDc53A65fe94Dea0c9BA0a6dAf8C6e196",  # Base Chain
    # 追加 (10個)
    "PYUSD":  "0x8b175474E8062F03f98235Af4061f301C91Be582",  # PayPal USD
    "USDbC":  "0xd9aaEC86B65D86f6A7B5B1B0C42FFA531710b6CA",  # Bridged USDC
    "DAI":    "0x50c5725949A6F0C72E6C4a641f24049a917DB0CB",  # DAI
    "EURC":   "0x1abaea1f7c830fed89f2532441e15AC2a061263d",  # Euro Coin
    "ezETH":  "0x2416092f143378750bb29b79ed961ab195cceea5",  # Renzo ezETH (Base)
    "weETH":  "0x04C066422FBD43480184942c3C83D818aba10758",  # weETH
    "rsETH":  "0xab57D6BB1349fF1626db86D3380B2256E2a2A88a",  # rsETH
    "clAERO": "0x403d1596700c25d888f21E5336d3c8c7B34638De",  # Concentrated AERO
    "Virtual":"0x0b3e328455c4055EEb9e3f84b5534924DA9480FD",  # Virtual Protocol
    "LUSD":   "0xED8880004990A2E2A661556a3EcC8EF477960714",  # LUSD
}

# 小文字アドレスのセット（高速チェック用）
WHITELISTED_TOKEN_ADDRESSES: set[str] = {
    addr.lower() for addr in WHITELISTED_TOKENS.values()
}

# アドレス → シンボル 変換マップ
TOKEN_SYMBOL_MAP: dict[str, str] = {
    addr.lower(): sym for sym, addr in WHITELISTED_TOKENS.items()
}

# Stablecoinアドレス（USD 1.0 相当として扱う。EURC/LUSD はシミュレーション上の近似）
STABLECOIN_ADDRESSES: set[str] = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
    "0x8b175474e8062f03f98235af4061f301c91be582",  # PYUSD
    "0x1abaea1f7c830fed89f2532441e15ac2a061263d",  # EURC
    "0xed8880004990a2e2a661556a3ecc8ef477960714",  # LUSD
}

WETH_ADDRESS = "0x4200000000000000000000000000000000000006"

# ==========================================
# 💰 資金管理パラメータ
# ==========================================
VIRTUAL_TOTAL_FUNDS_JST = 300_000.0  # 仮想総資金 (JST)
MAX_POSITION_SIZE_S_JST = 60_000.0   # S級 最大ポジションサイズ
MAX_POSITION_SIZE_A_JST = 30_000.0   # A級 最大ポジションサイズ
GAS_COST_USD            = 0.5        # 1往復ガス代 (USD)
JST_PER_USD             = 150.0      # レート換算: 1 USD ≒ 150 JST (円)
GAS_COST_JST            = GAS_COST_USD * JST_PER_USD  # = 75.0 JST

# ==========================================
# 📊 エントリー判定パラメータ
# ==========================================
MIN_TVL_USD             = 5_000.0    # 最低TVL フィルタ ($)
PRICE_SPIKE_THRESHOLD   = 0.05       # 価格急騰フィルタ (5%)
PRICE_SPIKE_WINDOW_SEC  = 5 * 60     # 急騰監視ウィンドウ (5分)
MIN_NET_EV_RATIO        = 0.005      # NetEV 最低比率 (T_s の 0.5%)
MIN_ENTRY_SCORE         = 30         # エントリー最低スコア
SCORE_S_GRADE           = 60         # S級スコアしきい値
SCORE_A_GRADE           = 30         # A級スコアしきい値
ENTRY_DELAY_MIN_SEC     = 2          # 約定遅延 最小秒
ENTRY_DELAY_MAX_SEC     = 5          # 約定遅延 最大秒

# ==========================================
# 🚪 出口戦略パラメータ
# ==========================================
EXIT_PHASE1_PROFIT      = 0.08       # Phase1 利確ライン +8%
EXIT_PHASE2_PROFIT      = 0.12       # Phase2 利確ライン +12%
EXIT_TRAILING_DROP      = 0.05       # トレーリングストップ (最高値から -5%)
EXIT_HARD_STOP          = -0.05      # ハードストップ (買値から -5%)
EXIT_WEEKDAY_THURSDAY   = 3          # 木曜日 (weekday: 0=月)
EXIT_TIME_HOUR_JST      = 8          # 強制エグジット 時 (JST)
EXIT_TIME_MINUTE_JST    = 50         # 強制エグジット 分 (JST)

# Phase別決済割合（残りポジション全体に対する比率）
PHASE1_CLOSE_RATIO      = 0.50       # Phase1: 全体の 50% 決済
PHASE2_CLOSE_RATIO      = 0.50       # Phase2: 残り50%の さらに50% = 全体25%
PHASE3_CLOSE_RATIO      = 1.00       # Phase3: 残り全部 (25%)

# ポジション価格チェック間隔
POSITION_MONITOR_SEC    = 30         # 30秒ごとに監視

# ==========================================
# 🔑 最小 ABI 定義
# ==========================================
VOTER_ABI = [
    {
        # isWhitelistedToken: public mapping(address => bool) の getter
        # Aerodrome V2 Voter では isWhitelisted → isWhitelistedToken (public mapping) に変更
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "isWhitelistedToken",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        # weights: public mapping(address => uint256) の getter
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "weights",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        # gaugeToBribe: ExternalBriibe アドレスから Bribe コントラクトを取得
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "gaugeToBribe",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        # poolForGauge: ゲージアドレスからプールアドレスを取得
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "poolForGauge",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        # gauges: プールアドレスからゲージアドレスを取得
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "gauges",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalWeight",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
]


POOL_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint256", "name": "_reserve0", "type": "uint256"},
            {"internalType": "uint256", "name": "_reserve1", "type": "uint256"},
            {"internalType": "uint256", "name": "_blockTimestampLast", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address", "name": "tokenIn", "type": "address"}
        ],
        "name": "getAmountOut",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
]

ERC20_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
]

import os
from dotenv import load_dotenv

# .envファイルがあれば読み込む
load_dotenv()

# ==========================================
# ⚙️ 基本設定 (RPC, API Keys)
# ==========================================
ALCHEMY_BASE_WSS_URL = os.getenv("ALCHEMY_BASE_WSS_URL", "wss://base-mainnet.g.alchemy.com/v2/lcau4KV3k-6quLk___bH2")

# フォールバック用パブリックRPC (例)
FALLBACK_BASE_WSS_URL = os.getenv("FALLBACK_BASE_WSS_URL", "wss://base-rpc.publicnode.com")

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL", 
    "https://discord.com/api/webhooks/1486024387235807363/5uOvGnnf61kMLboYSKJwYbnHgohr3ebaTna1e4fqdaDrQVGxrnTx49ucZFt0xam1x2K8"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCaJIQoweH2qpnJCg-zb7wzg4n6iuBuTS8")

# ==========================================
# 🎯 デプロイメント・ターゲット情報
# ==========================================
# Aerodrome Timelock / Pool など、監視対象の代表的アドレス（必要に応じて拡張）
TARGET_CONTRACTS = [
    # ここにAerodrome等の対象コントラクトのリストを追加予定
]

# Timezone
TZ_JST = "Asia/Tokyo"

import os
from sniper.safe_io import safe_print
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

# .envファイルがあれば読み込む
if load_dotenv is not None:
    load_dotenv()
else:
    safe_print("⚠️ python-dotenv が未インストールのため .env 自動読込をスキップします。")

# ==========================================
# ⚙️ 基本設定 (RPC, API Keys)
# ==========================================
ALCHEMY_BASE_WSS_URL = os.getenv("ALCHEMY_BASE_WSS_URL")
FALLBACK_BASE_WSS_URL = os.getenv("FALLBACK_BASE_WSS_URL", "wss://base-rpc.publicnode.com")

DISCORD_WEBHOOK_URL = os.getenv("RADAR_WEBHOOK_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")



# ==========================================
# 🎯 デプロイメント・ターゲット情報
# ==========================================
# Aerodrome Timelock / Pool など、監視対象の代表的アドレス（必要に応じて拡張）
TARGET_CONTRACTS = [
    # ここにAerodrome等の対象コントラクトのリストを追加予定
]

# Timezone
TZ_JST = "Asia/Tokyo"

import os
from pathlib import Path
from safe_io import safe_print
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

# .envファイルがあれば読み込む（cwdに依存せず backend/.env を優先）
if load_dotenv is not None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path)
else:
    safe_print("⚠️ python-dotenv が未インストールのため .env 自動読込をスキップします。")

# ==========================================
# ⚙️ 基本設定 (RPC, API Keys)
# ==========================================
ALCHEMY_BASE_WSS_URL = os.getenv("ALCHEMY_BASE_WSS_URL")
FALLBACK_BASE_WSS_URL = os.getenv("FALLBACK_BASE_WSS_URL", "wss://base-rpc.publicnode.com")

DISCORD_WEBHOOK_URL = os.getenv("RADAR_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Claude Code CLI (ヘッドレスモード) — AI解析に使用。サブスク認証のためAPI課金なし
CLAUDE_CLI_PATH = os.getenv("CLAUDE_CLI_PATH")  # 未設定なら PATH から自動検出
CLAUDE_FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "haiku")   # 一次判定用 (旧 gemini-2.5-flash 相当)
CLAUDE_DEEP_MODEL = os.getenv("CLAUDE_DEEP_MODEL", "sonnet")  # 深層分析用 (旧 gemini-2.5-pro 相当)



# ==========================================
# 🎯 デプロイメント・ターゲット情報
# ==========================================
# Aerodrome Timelock / Pool など、監視対象の代表的アドレス（必要に応じて拡張）
TARGET_CONTRACTS = [
    # ここにAerodrome等の対象コントラクトのリストを追加予定
]

# Timezone
TZ_JST = "Asia/Tokyo"

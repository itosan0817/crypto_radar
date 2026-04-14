import google.generativeai as genai
import sys
import os
from sniper.safe_io import safe_print

# プロジェクトのルートをパスに追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from config.settings import GEMINI_API_KEY
    if not GEMINI_API_KEY or "YOUR_GEMINI" in GEMINI_API_KEY:
        safe_print("❌ ERROR: APIキーが設定されていません。 (config/settings.py)")
        sys.exit(1)
        
    genai.configure(api_key=GEMINI_API_KEY)
    
    safe_print("--- 利用可能なモデル一覧 ---")
    available_models = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            safe_print(f"- {m.name}")
            available_models.append(m.name)
    
    if not available_models:
        safe_print("❌ ERROR: 利用可能なモデルが一つも見つかりませんでした。")
        safe_print("APIキーが無効、またはGoogle Cloudのプロジェクト設定により全てのモデルへのアクセスが制限されている可能性があります。")
    else:
        safe_print(f"✅ 合計 {len(available_models)} 個の生成可能モデルが見つかりました。")
        
except Exception as e:
    safe_print(f"❌ 調査中にエラーが発生しました: {e}")

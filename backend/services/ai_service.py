import json
import asyncio
import google.generativeai as genai
from config.settings import GEMINI_API_KEY
from sniper.safe_io import safe_print

class AIService:
    """
    Gemini AI を使用した解析を担当するサービスクラス。
    """
    _initialized = False

    @classmethod
    def _initialize(cls):
        """Gemini API の初期化"""
        if not cls._initialized:
            if GEMINI_API_KEY and "YOUR_GEMINI" not in GEMINI_API_KEY:
                genai.configure(api_key=GEMINI_API_KEY)
                cls._initialized = True

    @classmethod
    async def _generate_with_retry(cls, model_type: str, prompt: str, max_retries: int = 3) -> str:
        """指定モデルでリトライ付きの生成を行う。JSON形式固定"""
        target_model = "models/gemini-2.5-flash" if model_type == "flash" else "models/gemini-2.5-pro"
        fallback_model = "models/gemini-flash-latest" if model_type == "flash" else "models/gemini-2.5-flash"

        generation_config = {"response_mime_type": "application/json"}
        last_e = None
        
        # メインモデル
        for attempt in range(max_retries):
            try:
                model = genai.GenerativeModel(target_model, generation_config=generation_config)
                response = await asyncio.to_thread(model.generate_content, prompt)
                return response.text
            except Exception as e:
                last_e = e
                err_str = str(e).lower()
                if "429" in err_str or "quota" in err_str or "too many requests" in err_str:
                    wait_sec = 2 ** attempt * 2  # 2, 4, 8秒
                    safe_print(f"⏳ {target_model} Quota到達。{wait_sec}秒後に再試行... ({attempt+1}/{max_retries})")
                    await asyncio.sleep(wait_sec)
                else:
                    break
        
        # フォールバックモデル
        safe_print(f"🔄 {target_model} 制限到達のため、{fallback_model} で代行実行します...")
        for attempt in range(max_retries):
            try:
                model = genai.GenerativeModel(fallback_model, generation_config=generation_config)
                response = await asyncio.to_thread(model.generate_content, prompt)
                return response.text
            except Exception as e:
                last_e = e
                err_str = str(e).lower()
                if "429" in err_str or "quota" in err_str or "too many requests" in err_str:
                    wait_sec = 2 ** attempt * 2
                    await asyncio.sleep(wait_sec)
                else:
                    break
                    
        raise last_e

    @classmethod
    async def analyze_calldata_risk(cls, decoded_data_str: str, tvl_ratio: float, model_type: str = "flash") -> tuple[int, str, str]:
        """
        Gemini APIを使用して変更予約のリスクとインパクトを解析する。
        """
        cls._initialize()
        
        if not GEMINI_API_KEY or "YOUR_GEMINI" in GEMINI_API_KEY:
            return 75, "A", "Gemini APIキー未設定 (モック判定)"

        prompt = f"""
        あなたはDeFiのセキュリティ専門家で、
        なおかつ、Aerodrome Finance（Base Chain）の極めて優秀なオンチェーン・アナリストです。
        以下のタイムロック変更予約の内容を解析してください。

        【コンテキスト】
        TVL割合 (影響の大きさ): {tvl_ratio * 100:.2f}%
        デコード済みCalldata: 
        {decoded_data_str}

        【出力要件】
        以下の要素を持つJSON形式でのみ出力してください:
        "ai_score": 0~100の整数 (価格インパクトの強さ。100が最強)
        "ai_rank": "S", "A", または "B" (S: 80以上, A: 60-79, B: 59以下)
        "ai_summary": "日本語で簡潔な要約（150文字以内）。スコアをつけた理由、根拠。"
        """
        
        try:
            text = await cls._generate_with_retry(model_type, prompt)
            # generation_configでJSON固定されているが、念のためクリーンアップ
            text = text.replace('```json', '').replace('```', '').strip()
            result = json.loads(text)
            
            score = int(result.get("ai_score", 50))
            rank = result.get("ai_rank", "B")
            summary = result.get("ai_summary", "解析不能なデータが含まれていました")
            
            return score, rank, summary
        except Exception as e:
            safe_print(f"⚠️ Gemini AI解析エラー: {e}")
            return 50, "B", f"AI処理リソース確保失敗: {str(e)[:50]}"

    @classmethod
    async def analyze_with_trend(cls, decoded_data_str: str, tvl_ratio: float, recent_events: list) -> dict:
        """
        過去のトレンドデータを含めた深層分析（Proモデル優先・リトライ有）
        """
        cls._initialize()
        
        history_str = "\n".join([
            f"- {e['timestamp']}: Method {e['method_id']} (Rank {e['ai_rank']}, Score {e['ai_score']})"
            for e in recent_events
        ]) if recent_events else "過去7日間に記録されたデータはありません。"

        prompt = f"""
        あなたはDeFiのセキュリティ専門家で、Aerodrome Financeの主席アナリストです。
        最新のタイムロック変更予約と、過去7日間のトレンドを統合して【最終深層分析】を行ってください。

        【今回の変更内容】
        TVL割合: {tvl_ratio * 100:.2f}%
        デコード済みCalldata: 
        {decoded_data_str}

        【過去7日間の履歴トレンド】
        {history_str}

        【出力要件】
        以下の要素を持つJSON形式でのみ出力してください（日本語で回答）:
        "daily_insight": "今回の単独変数の詳細分析（120文字以内）"
        "trend_insight": "過去の傾向との比較、予兆、整合性の分析（120文字以内）"
        "final_decision": "強気買い(BUY), 売り逃げ(SELL), 即撤退(DANGER), または 静観(WAIT)"
        "ai_score": 0~100の整数
        "ai_rank": "S", "A", または "B"
        """
        
        try:
            text = await cls._generate_with_retry("pro", prompt, max_retries=3)
            text = text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
        except Exception as e:
            short_err = str(e)[:100]
            safe_print(f"⚠️ Gemini 深層分析限界エラー: {short_err}...")
            return {
                "daily_insight": "API制限またはタイムアウトによりAI詳細分析をスキップしました。",
                "trend_insight": "しばらく時間をおいてから再チェックしてください。一時的なリソース不足です。",
                "final_decision": "WAIT",
                "ai_score": 50,
                "ai_rank": "B"
            }

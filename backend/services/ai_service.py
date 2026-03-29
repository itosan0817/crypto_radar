import json
import asyncio
import google.generativeai as genai
from config.settings import GEMINI_API_KEY

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
    async def analyze_calldata_risk(cls, decoded_data_str: str, tvl_ratio: float, model_type: str = "flash") -> tuple[int, str, str]:
        """
        Gemini 2.5 を使用して変更予約のリスクとインパクトを解析する。
        model_type: "flash" (日常監視・高速) または "pro" (トレンド分析・高精度)
        Returns: (ai_score, ai_rank, ai_summary)
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
        以下のJSON形式でのみ出力してください:
        {{
            "ai_score": 0~100の整数 (価格インパクトの強さ。100が最強),
            "ai_rank": "S", "A", または "B" (S: 80以上, A: 60-79, B: 59以下),
            "ai_summary": "日本語で簡潔な要約（150文字以内）。スコアをつけた理由、根拠。"
        }}
        """
        
        # 2026年最新環境のモデルIDマップ
        target_model = "models/gemini-2.5-flash" if model_type == "flash" else "models/gemini-2.5-pro"
        fallback_model = "models/gemini-flash-latest" if model_type == "flash" else "models/gemini-pro-latest"

        try:
            # 指定されたモデルタイプで実行
            try:
                model = genai.GenerativeModel(target_model)
                response = await asyncio.to_thread(model.generate_content, prompt)
            except Exception as inner_e:
                # 指定モデルが不可、または404の場合、最新用ID("latest")で再試行
                if "404" in str(inner_e) or "not found" in str(inner_e).lower():
                    print(f"🔄 {target_model} 不可のため {fallback_model} で再試行します...")
                    model = genai.GenerativeModel(fallback_model)
                    response = await asyncio.to_thread(model.generate_content, prompt)
                else:
                    raise inner_e
            
            text = response.text.replace('```json', '').replace('```', '').strip()
            result = json.loads(text)
            
            score = int(result.get("ai_score", 50))
            rank = result.get("ai_rank", "B")
            summary = result.get("ai_summary", "解析不能なデータが含まれていました")
            
            return score, rank, summary
        except Exception as e:
            print(f"⚠️ Gemini AI解析エラー: {e}")
            return 50, "B", f"AI処理エラー: {str(e)[:50]}"

    @classmethod
    async def analyze_with_trend(cls, decoded_data_str: str, tvl_ratio: float, recent_events: list) -> dict:
        """
        過去のトレンドデータを含めた深層分析（Proモデル使用）
        Returns: {daily_insight, trend_insight, final_decision, score, rank}
        """
        cls._initialize()
        
        # 履歴を読みやすい形式に整形
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
        以下のJSON形式でのみ出力してください（日本語で回答）:
        {{
            "daily_insight": "今回の単独変数の詳細分析（120文字以内）",
            "trend_insight": "過去の傾向との比較、予兆、整合性の分析（120文字以内）",
            "final_decision": "強気買い(BUY), 売り逃げ(SELL), 即撤退(DANGER), または 静観(WAIT)",
            "ai_score": 0~100の整数,
            "ai_rank": "S", "A", または "B"
        }}
        """
        
        try:
            # 深層分析には Pro モデルを優先使用
            try:
                model = genai.GenerativeModel('models/gemini-2.5-pro')
                response = await asyncio.to_thread(model.generate_content, prompt)
            except Exception as inner_e:
                # クォータ制限(429)または404の場合、Flashモデルで代行
                if "429" in str(inner_e) or "404" in str(inner_e) or "quota" in str(inner_e).lower():
                    print(f"🔄 Proモデルの制限(Quota)につき、Flashモデルで深層分析を代行します...")
                    model = genai.GenerativeModel('models/gemini-2.5-flash')
                    response = await asyncio.to_thread(model.generate_content, prompt)
                else:
                    raise inner_e
            
            text = response.text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
        except Exception as e:
            # エラーログを簡潔に表示 (巨大なJSON errorは表示しない)
            short_err = str(e)[:100]
            print(f"⚠️ Gemini 深層分析エラー: {short_err}...")
            return {
                "daily_insight": "AIリソース制限により詳細分析をスキップしました。",
                "trend_insight": "Flashモデルによる暫定チェックを実施してください。",
                "final_decision": "WAIT",
                "ai_score": 50,
                "ai_rank": "B"
            }



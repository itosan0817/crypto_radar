import json
import asyncio
import os
import shutil
import subprocess
from config.settings import CLAUDE_CLI_PATH, CLAUDE_FAST_MODEL, CLAUDE_DEEP_MODEL
from sniper.safe_io import safe_print

class AIService:
    """
    サーバー上の Claude Code CLI (ヘッドレスモード) を使用した解析を担当するサービスクラス。
    サブスクリプション認証で動作するため API 課金は発生しない。
    """
    _cli_path = None
    _cli_checked = False

    @classmethod
    def _get_cli(cls):
        """claude CLI のパスを取得（初回のみ探索）"""
        if not cls._cli_checked:
            cls._cli_path = CLAUDE_CLI_PATH or shutil.which("claude")
            cls._cli_checked = True
            if cls._cli_path:
                safe_print(f"✅ Claude Code CLI 検出: {cls._cli_path}")
            else:
                safe_print("⚠️ Claude Code CLI が見つかりません (モック判定で動作します)")
        return cls._cli_path

    @staticmethod
    def _extract_json(text: str) -> dict:
        """応答テキストからJSON部分を取り出してパースする"""
        text = text.replace('```json', '').replace('```', '').strip()
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end + 1]
        return json.loads(text)

    @classmethod
    def _run_claude_sync(cls, model: str, prompt: str, timeout: int) -> str:
        """claude -p を同期実行し、応答テキストを返す"""
        cli = cls._get_cli()
        proc = subprocess.run(
            [cli, "-p", "--model", model, "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:200]}")
        envelope = json.loads(proc.stdout)
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            raise RuntimeError(f"claude CLI error: {str(envelope.get('result'))[:200]}")
        return envelope.get("result", "")

    @classmethod
    async def _generate_with_retry(cls, model_type: str, prompt: str, max_retries: int = 3) -> str:
        """指定モデルでリトライ付きの生成を行う"""
        target_model = CLAUDE_FAST_MODEL if model_type == "flash" else CLAUDE_DEEP_MODEL
        fallback_model = CLAUDE_FAST_MODEL
        timeout = 180 if model_type == "flash" else 300

        last_e = None

        # メインモデル
        for attempt in range(max_retries):
            try:
                return await asyncio.to_thread(cls._run_claude_sync, target_model, prompt, timeout)
            except Exception as e:
                last_e = e
                wait_sec = 2 ** attempt * 2  # 2, 4, 8秒
                safe_print(f"⏳ Claude ({target_model}) エラー。{wait_sec}秒後に再試行... ({attempt+1}/{max_retries}): {str(e)[:100]}")
                await asyncio.sleep(wait_sec)

        # フォールバックモデル
        if fallback_model != target_model:
            safe_print(f"🔄 {target_model} 失敗のため、{fallback_model} で代行実行します...")
            for attempt in range(max_retries):
                try:
                    return await asyncio.to_thread(cls._run_claude_sync, fallback_model, prompt, timeout)
                except Exception as e:
                    last_e = e
                    await asyncio.sleep(2 ** attempt * 2)

        raise last_e

    @classmethod
    async def analyze_calldata_risk(cls, decoded_data_str: str, tvl_ratio: float, model_type: str = "flash") -> tuple[int, str, str]:
        """
        Claude を使用して変更予約のリスクとインパクトを解析する。
        """
        if not cls._get_cli():
            return 75, "A", "Claude CLI 未検出 (モック判定)"

        prompt = f"""
        あなたはDeFiのセキュリティ専門家で、
        なおかつ、Aerodrome Finance（Base Chain）の極めて優秀なオンチェーン・アナリストです。
        以下のタイムロック変更予約の内容を解析してください。

        【コンテキスト】
        TVL割合 (影響の大きさ): {tvl_ratio * 100:.2f}%
        デコード済みCalldata:
        {decoded_data_str}

        【出力要件】
        以下の要素を持つJSONのみを出力してください。JSON以外の文章やコードフェンスは含めないでください:
        "ai_score": 0~100の整数 (価格インパクトの強さ。100が最強)
        "ai_rank": "S", "A", または "B" (S: 80以上, A: 60-79, B: 59以下)
        "ai_summary": "日本語で簡潔な要約（150文字以内）。スコアをつけた理由、根拠。"
        """

        try:
            text = await cls._generate_with_retry(model_type, prompt)
            result = cls._extract_json(text)

            score = int(result.get("ai_score", 50))
            rank = result.get("ai_rank", "B")
            summary = result.get("ai_summary", "解析不能なデータが含まれていました")

            return score, rank, summary
        except Exception as e:
            safe_print(f"⚠️ Claude AI解析エラー: {e}")
            return 50, "B", f"AI処理リソース確保失敗: {str(e)[:50]}"

    @classmethod
    async def analyze_with_trend(cls, decoded_data_str: str, tvl_ratio: float, recent_events: list) -> dict:
        """
        過去のトレンドデータを含めた深層分析（上位モデル優先・リトライ有）
        """
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
        以下の要素を持つJSONのみを出力してください（日本語で回答）。JSON以外の文章やコードフェンスは含めないでください:
        "daily_insight": "今回の単独変数の詳細分析（120文字以内）"
        "trend_insight": "過去の傾向との比較、予兆、整合性の分析（120文字以内）"
        "final_decision": "強気買い(BUY), 売り逃げ(SELL), 即撤退(DANGER), または 静観(WAIT)"
        "ai_score": 0~100の整数
        "ai_rank": "S", "A", または "B"
        """

        try:
            text = await cls._generate_with_retry("pro", prompt, max_retries=3)
            return cls._extract_json(text)
        except Exception as e:
            short_err = str(e)[:100]
            safe_print(f"⚠️ Claude 深層分析エラー: {short_err}...")
            return {
                "daily_insight": "AI呼び出しエラーによりAI詳細分析をスキップしました。",
                "trend_insight": "しばらく時間をおいてから再チェックしてください。一時的なリソース不足です。",
                "final_decision": "WAIT",
                "ai_score": 50,
                "ai_rank": "B"
            }

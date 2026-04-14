import datetime
import requests
import pytz
from config.settings import DISCORD_WEBHOOK_URL, TZ_JST
from sniper.safe_io import safe_print

class DiscordService:
    """
    Discord への通知を担当するサービスクラス。
    """

    @staticmethod
    def _format_jst(utc_dt: datetime.datetime) -> str:
        """UTCのdatetimeをJSTの読める文字列に変換"""
        jst = pytz.timezone(TZ_JST)
        return utc_dt.astimezone(jst).strftime('%Y-%m-%d %H:%M:%S (JST)')

    @staticmethod
    def _send_embed(embed: dict):
        """Discord Webhook に埋め込みメッセージを送信する"""
        if not DISCORD_WEBHOOK_URL or "YOUR_DISCORD" in DISCORD_WEBHOOK_URL:
            return
        data = {"embeds": [embed]}
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=data)
            if response.status_code not in (200, 204):
                safe_print(f"❌ Discord通知送信失敗: {response.status_code}")
        except Exception as e:
            safe_print(f"❌ Discord接続エラー: {e}")

    @classmethod
    def send_t0_entry_notification(cls, contract_addr: str, tx_hash: str, t0_price: float, slippage: float, ai_rank: str, ai_score: int, ai_summary: str):
        """T=0 (エントリー予約) の通知を送信する"""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_jst_str = cls._format_jst(now_utc)
        
        color = 0x00FF00 if ai_rank == 'S' else (0xFFFF00 if ai_rank == 'A' else 0x808080)
        
        embed = {
            "title": "🟢 [T=0] 仮想エントリー：タイムロック変更予約",
            "description": f"**🤖 AIサマリー**\n> {ai_summary}",
            "color": color,
            "fields": [
                {"name": "仮想購入価格 (スリッページ補正済)", "value": f"`${t0_price:.5f}` (想定 {slippage:.2f}%)", "inline": True},
                {"name": "AI ランク / スコア", "value": f"**{ai_rank}級** ({ai_score}/100)", "inline": True},
                {"name": "📝 Contract", "value": f"`{contract_addr}`", "inline": False},
                {"name": "🔗 TX", "value": f"[BaseScanで確認する](https://basescan.org/tx/{tx_hash})", "inline": False}
            ],
            "footer": {"text": f"検知時刻: {now_jst_str}"},
            "timestamp": now_utc.isoformat()
        }
        cls._send_embed(embed)

    @classmethod
    def send_t48_answer_notification(cls, contract_addr: str, tx_hash: str, t0_price: float, t48_price: float, pnl: float):
        """T+48 (実行) の答え合わせ通知を送信する"""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_jst_str = cls._format_jst(now_utc)
        
        # 利益率に応じた色 (プラスなら緑、マイナスなら赤)
        color = 0x00FF00 if pnl >= 0 else 0xFF0000
        emoji = "🚀" if pnl >= 0 else "📉"
        
        embed = {
            "title": f"{emoji} [T+48] 答え合わせ：仮想トレード結果",
            "description": "48時間経過（またはExecuted）による価格変動シミュレーション結果",
            "color": color,
            "fields": [
                {"name": "T=0 エントリー価格", "value": f"`${t0_price:.5f}`", "inline": True},
                {"name": "T+48 エグジット価格", "value": f"`${t48_price:.5f}`", "inline": True},
                {"name": "💰 仮想 PnL (利益率)", "value": f"**{pnl:+.2f}%**", "inline": False},
                {"name": "📝 Contract", "value": f"`{contract_addr}`", "inline": False},
                {"name": "🔗 TX", "value": f"[BaseScan](https://basescan.org/tx/{tx_hash})", "inline": False}
            ],
            "footer": {"text": f"解決時刻: {now_jst_str}"},
            "timestamp": now_utc.isoformat()
        }
        cls._send_embed(embed)

    @classmethod
    def send_summary_notification(cls, stats: dict, is_monthly: bool = False):
        """週間または月間のシミュレーション総括レポートを送信する"""
        range_text = "【月間シミュレーション総括】" if is_monthly else "【週間シミュレーション総括】"
        color = 0xAAFF00 if is_monthly else 0x00FFFF
        
        def _fmt(r):
            s = stats[r]
            c = s['count']
            w = s['wins']
            p = s['total_pnl']
            rate = (w / c * 100) if c > 0 else 0
            avg = (p / c) if c > 0 else 0
            return f"➤ 検知: {c}件 (勝率 {rate:.1f}%)\n➤ 合計利益: **{p:+.2f}%** (平均 {avg:+.2f}%)"

        embed = {
            "title": f"📊 {range_text}",
            "description": "システムによる自動シミュレーションの結果を集計しました。",
            "color": color,
            "fields": [
                {"name": "🔥 S級ランク (最強推奨)", "value": _fmt("S"), "inline": False},
                {"name": "⭐ A級ランク (注目対象)", "value": _fmt("A"), "inline": False},
                {"name": "📉 合計パフォーマンス", "value": _fmt("total"), "inline": False}
            ],
            "footer": {"text": "エッジの完全証明サイクル 🛰️"},
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        cls._send_embed(embed)

    @classmethod
    def send_deep_analysis_alert(cls, contract_addr: str, tx_hash: str, analysis_result: dict):
        """
        AI による深層分析結果（日常・トレンド・総合判断）を含む特製アラートを送信。
        """
        # アクションに応じたアイコンと色
        decision_map = {
            "BUY": ("💎 PREMIUM BUY (強気買い)", 0x00FF00),       # 緑
            "SELL": ("⚠️ SELL ALERT (売り逃げ)", 0xFFA500),      # オレンジ
            "DANGER": ("🚨 DANGER: EXIT (即撤退)", 0xFF0000),   # 赤
            "WAIT": ("⏳ MONITORING (静観)", 0x808080)          # グレー
        }
        status_text, color = decision_map.get(analysis_result.get("final_decision"), ("🔍 ANALYSIS MODE", 0x3498db))

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_jst_str = cls._format_jst(now_utc)

        embed = {
            "title": f"{status_text} | Crypto Radar 極",
            "description": f"タイムロック変更予約の【深層分析】が完了しました。\n[BaseScanでTXを確認](https://basescan.org/tx/{tx_hash})",
            "color": color,
            "fields": [
                {
                    "name": "📊 ① 日常評価 (Flash Scan)",
                    "value": f"評価: **{analysis_result.get('ai_rank')}級** ({analysis_result.get('ai_score')}点)\n> {analysis_result.get('daily_insight')}",
                    "inline": False
                },
                {
                    "name": "📈 ② 週間トレンド分析 (Trend Scan)",
                    "value": f"> {analysis_result.get('trend_insight')}",
                    "inline": False
                },
                {
                    "name": "⚖️ ③ 投資家への最終判断 (PRO DECISION)",
                    "value": f"結論: **{analysis_result.get('final_decision')}**",
                    "inline": False
                },
                {
                    "name": "📝 対象コントラクト",
                    "value": f"`{contract_addr}`",
                    "inline": False
                }
            ],
            "footer": {"text": f"深層分析完了: {now_jst_str} | Powered by Gemini 2.5 Pro"},
            "timestamp": now_utc.isoformat()
        }
        cls._send_embed(embed)

    @classmethod
    def send_error_notification(cls, process_name: str, error_msg: str):
        """プログラム異常発生時（例外）の通知を送信する"""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_jst_str = cls._format_jst(now_utc)
        
        # エラーログが長すぎるとDiscordの2048文字制限に引っかかるため切り詰め
        error_msg = error_msg[:2000]
        
        embed = {
            "title": f"🚨 [SYSTEM ERROR] {process_name} 異常終了・例外発生",
            "description": f"プログラムの実行中に致命的なエラーが発生しました。ログを確認してください。\n```python\n{error_msg}\n```",
            "color": 0xFF0000,
            "footer": {"text": f"発生時刻: {now_jst_str}"},
            "timestamp": now_utc.isoformat()
        }
        cls._send_embed(embed)

    @classmethod
    def send_startup_notification(cls):
        """起動確認用の通知を送信する"""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_jst_str = cls._format_jst(now_utc)
        
        embed = {
            "title": "✅ Aerodrome Radar 起動",
            "description": "Baseチェーン監視網（シミュレーター極）プロセスが正常に起動しました。",
            "color": 0x00FFFF,
            "footer": {"text": f"起動時刻: {now_jst_str}"},
            "timestamp": now_utc.isoformat()
        }
        cls._send_embed(embed)

    @classmethod
    def send_health_check(cls):
        """毎日定例の健康診断通知を報告する"""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_jst_str = cls._format_jst(now_utc)
        
        embed = {
            "title": "📡 定例システム健康診断報告 | Aerodrome Radar",
            "description": "24時間稼働の死活監視チェックが完了しました。現在すべてのシステムは正常に Blockchain を監視中です。",
            "color": 0x27ae60, # 鮮やかな緑
            "fields": [
                {"name": "🛰️ 稼働プロセス", "value": "🟢 Aerodrome Radar (Main Loop)", "inline": True},
                {"name": "⏳ 監視状況", "value": "🟢 正常 (Listening...)", "inline": True},
                {"name": "🗓️ 最終確認時刻", "value": f"`{now_jst_str}`", "inline": False}
            ],
            "footer": {"text": "Crypto Radar 極 | 24/7 Monitoring System"},
            "timestamp": now_utc.isoformat()
        }
        cls._send_embed(embed)

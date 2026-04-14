import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
import os
from sniper.safe_io import safe_print

class FirebaseConfig:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FirebaseConfig, cls).__new__(cls)
            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                service_account_path = os.path.join(base_dir, 'serviceAccountKey.json')
                if not os.path.exists(service_account_path):
                    service_account_path = "serviceAccountKey.json"
                
                cred = credentials.Certificate(service_account_path)
                try:
                    cls._instance.app = firebase_admin.initialize_app(cred)
                except ValueError:
                    cls._instance.app = firebase_admin.get_app()
                safe_print("✅ Firebase Admin SDK 初期化成功 (Firestore)")
            except Exception as e:
                safe_print(f"❌ Firebase初期化エラー: {e}")
        return cls._instance

class FirebaseService:
    """
    Firebase (Firestore) との通信を担当するサービスクラス。
    """
    _db = None

    @classmethod
    def _get_db(cls):
        """Firestore クライアントを取得（シングルトン）"""
        if cls._db is None:
            FirebaseConfig() # 初期化
            cls._db = firestore.client()
        return cls._db

    @classmethod
    def save_simulation_t0(cls, tx_hash: str, contract_addr: str, raw_data: str, t0_price: float, slippage: float, ai_score: int, ai_rank: str, ai_summary: str):
        db = cls._get_db()
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            doc_ref = db.collection("simulations").document(tx_hash)
            doc_ref.set({
                "event_id": tx_hash,
                "contract_address": contract_addr,
                "raw_calldata": raw_data,
                "t0_timestamp": now_utc,
                "t0_price": t0_price,
                "slippage": slippage,
                "ai_score": ai_score,
                "ai_rank": ai_rank,
                "ai_summary": ai_summary,
                "status": "pending" 
            })
            safe_print(f"✨ [Firestore] T=0 記録保存完了: {tx_hash[:10]}")
        except Exception as e:
            safe_print(f"❌ [Firestore] T=0 保存エラー: {e}")

    @classmethod
    def update_simulation_t48(cls, tx_hash: str, t48_price: float):
        db = cls._get_db()
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            doc_ref = db.collection("simulations").document(tx_hash)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                t0_price = data.get("t0_price", 0)
                pnl = ((t48_price - t0_price) / t0_price * 100) if t0_price > 0 else 0.0
                doc_ref.update({
                    "t48_timestamp": now_utc,
                    "t48_price": t48_price,
                    "simulated_pnl": pnl,
                    "status": "executed"
                })
                safe_print(f"✅ [Firestore] T+48 シミュレーション完了 PnL: {pnl:.2f}%")
                return pnl, t0_price
            return None, None
        except Exception as e:
            safe_print(f"❌ [Firestore] T+48 更新エラー: {e}")
            return None, None

    @classmethod
    def get_simulation_stats(cls, days: int = 7):
        db = cls._get_db()
        try:
            threshold_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            docs = db.collection("simulations").where(filter=FieldFilter("t0_timestamp", ">=", threshold_date)).where(filter=FieldFilter("status", "==", "executed")).stream()
            stats = {"S": {"count": 0, "wins": 0, "total_pnl": 0.0}, "A": {"count": 0, "wins": 0, "total_pnl": 0.0}, "total": {"count": 0, "wins": 0, "total_pnl": 0.0}}
            for doc in docs:
                data = doc.to_dict()
                rank = data.get("ai_rank", "B")
                pnl = data.get("simulated_pnl", 0.0)
                if rank not in ["S", "A"]: continue
                stats[rank]["count"] += 1
                stats["total"]["count"] += 1
                stats[rank]["total_pnl"] += pnl
                stats["total"]["total_pnl"] += pnl
                if pnl > 0:
                    stats[rank]["wins"] += 1
                    stats["total"]["wins"] += 1
            return stats
        except Exception as e:
            safe_print(f"❌ [Firestore] 統計取得エラー: {e}")
            return None

    @classmethod
    def get_recent_scheduled_events(cls, days: int = 7):
        db = cls._get_db()
        try:
            start_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            docs = db.collection("scheduled_events").where(filter=FieldFilter("timestamp", ">=", start_time)).order_by("timestamp", direction="DESCENDING").limit(40).stream()
            events = []
            for doc in docs:
                data = doc.to_dict()
                events.append({"timestamp": str(data.get("timestamp")), "method_id": data.get("method_id"), "ai_rank": data.get("ai_rank"), "ai_score": data.get("ai_score")})
            return events
        except Exception as e:
            safe_print(f"⚠️ 過去ログ取得エラー: {e}")
            return []

    @classmethod
    def cleanup_old_simulations(cls, days: int = 30):
        try:
            db = cls._get_db()
            threshold_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            docs = db.collection("simulations").where(filter=FieldFilter("t0_timestamp", "<", threshold_date)).limit(50).stream()
            deleted_count = 0
            for doc in docs:
                doc.reference.delete()
                deleted_count += 1
            if deleted_count > 0:
                safe_print(f"🧹 [Firestore] 古い記録を {deleted_count} 件削除しました。")
        except Exception as e:
            safe_print(f"⚠️ データベース清掃エラー: {e}")

"""
Bribe Sniper Simulator v3.0 - Firestore 保存サービス
bribe_positions コレクションへのエントリー記録・フェーズ更新・決済記録・週次集計を担う
"""
from __future__ import annotations

import datetime
import os
import sys

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from sniper.models import Position, ExitRecord
from sniper.safe_io import safe_print

# serviceAccountKey.json のパスを解決
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICE_ACCOUNT_PATH = os.path.join(_BASE_DIR, "serviceAccountKey.json")


class _FirestoreClient:
    """Firestore クライアントのシングルトン"""
    _db = None

    @classmethod
    def get(cls):
        if cls._db is None:
            try:
                try:
                    firebase_admin.get_app()
                except ValueError:
                    cred = credentials.Certificate(_SERVICE_ACCOUNT_PATH)
                    firebase_admin.initialize_app(cred)
                cls._db = firestore.client()
                safe_print("✅ [FirestoreSniper] Firestore 接続成功")
            except Exception as e:
                safe_print(f"❌ [FirestoreSniper] Firestore 初期化失敗: {e}")
                raise
        return cls._db


class FirestoreSniperService:
    """Bribe Sniperのポジションデータを Firestore に保存・取得するサービスクラス"""

    COLLECTION = "bribe_positions"

    # ────────────────────────────────────────
    # エントリー保存
    # ────────────────────────────────────────
    @classmethod
    def save_entry(cls, pos: Position) -> bool:
        """新規エントリーを Firestore に保存する"""
        try:
            db = _FirestoreClient.get()
            db.collection(cls.COLLECTION).document(pos.position_id).set({
                "position_id":     pos.position_id,
                "pool_name":       pos.pool_name,
                "pool_address":    pos.pool_address,
                "bribe_token":     pos.bribe_token,
                "grade":           pos.grade,
                "entry_price_usd": pos.entry_price_usd,
                "entry_size_jst":  pos.entry_size_jst,
                "entry_size_usd":  pos.entry_size_usd,
                "net_ev_jst":      pos.net_ev_jst,
                "entered_at":      pos.entered_at,
                "status":          pos.status,
                "phase1_done":     pos.phase1_done,
                "phase2_done":     pos.phase2_done,
                "remaining_ratio": pos.remaining_ratio,
                "peak_price_usd":  pos.peak_price_usd,
                "trailing_stop_price": pos.trailing_stop_price,
                "realized_pnl_jst": pos.realized_pnl_jst,
                "gas_cost_total_jst": pos.gas_cost_total_jst,
            })
            safe_print(f"✨ [FirestoreSniper] エントリー保存: {pos.position_id}")
            return True
        except Exception as e:
            safe_print(f"❌ [FirestoreSniper] エントリー保存失敗: {e}")
            return False

    # ────────────────────────────────────────
    # 決済記録の追記
    # ────────────────────────────────────────
    @classmethod
    def record_exit(cls, pos: Position, exit_rec: ExitRecord) -> bool:
        """決済記録をサブコレクションに追記し、ポジション本体も更新する"""
        try:
            db = _FirestoreClient.get()
            pos_ref = db.collection(cls.COLLECTION).document(pos.position_id)

            # サブコレクション exits に追記
            pos_ref.collection("exits").add({
                "phase":          exit_rec.phase,
                "exit_price_usd": exit_rec.exit_price_usd,
                "closed_ratio":   exit_rec.closed_ratio,
                "size_jst":       exit_rec.size_jst,
                "pnl_jst":        exit_rec.pnl_jst,
                "pnl_pct":        exit_rec.pnl_pct,
                "exited_at":      exit_rec.exited_at,
            })

            # ポジション本体を更新
            pos_ref.update({
                "status":            pos.status,
                "phase1_done":       pos.phase1_done,
                "phase2_done":       pos.phase2_done,
                "remaining_ratio":   pos.remaining_ratio,
                "peak_price_usd":    pos.peak_price_usd,
                "trailing_stop_price": pos.trailing_stop_price,
                "realized_pnl_jst":  pos.realized_pnl_jst,
                "gas_cost_total_jst": pos.gas_cost_total_jst,
            })
            return True
        except Exception as e:
            safe_print(f"❌ [FirestoreSniper] 決済記録失敗 {pos.position_id}: {e}")
            return False

    # ────────────────────────────────────────
    # 週次レポート用統計
    # ────────────────────────────────────────
    @classmethod
    def get_weekly_stats(cls, days: int = 7) -> dict:
        """
        指定期間内のクローズ済みポジションを集計して返す。
        戻り値: { "S": {...}, "A": {...}, "total": {...}, "hourly": {...}, "by_token": {...} }
        """
        try:
            db = _FirestoreClient.get()
            since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

            docs = (
                db.collection(cls.COLLECTION)
                .where(filter=FieldFilter("entered_at", ">=", since))
                .where(filter=FieldFilter("status", "==", "closed"))
                .stream()
            )

            stats: dict = {
                "S":       {"count": 0, "wins": 0, "total_pnl": 0.0, "pnls": []},
                "A":       {"count": 0, "wins": 0, "total_pnl": 0.0, "pnls": []},
                "total":   {"count": 0, "wins": 0, "total_pnl": 0.0, "pnls": []},
                "hourly":  {h: {"count": 0, "pnl": 0.0} for h in range(24)},
                "by_token": {},
            }

            for doc in docs:
                data = doc.to_dict()
                grade  = data.get("grade", "A")
                pnl    = data.get("realized_pnl_jst", 0.0)
                token  = data.get("bribe_token", "UNKNOWN")
                ts     = data.get("entered_at")

                if grade not in ["S", "A"]:
                    continue

                for key in [grade, "total"]:
                    stats[key]["count"]     += 1
                    stats[key]["total_pnl"] += pnl
                    stats[key]["pnls"].append(pnl)
                    if pnl > 0:
                        stats[key]["wins"] += 1

                # 時間帯別
                if ts and hasattr(ts, "hour"):
                    hour = ts.hour
                    stats["hourly"][hour]["count"] += 1
                    stats["hourly"][hour]["pnl"]   += pnl

                # トークン別
                if token not in stats["by_token"]:
                    stats["by_token"][token] = {"count": 0, "pnl": 0.0}
                stats["by_token"][token]["count"] += 1
                stats["by_token"][token]["pnl"]   += pnl

            # 最大ドローダウン計算
            for key in ["S", "A", "total"]:
                pnls = stats[key]["pnls"]
                stats[key]["max_dd"] = _calc_max_drawdown(pnls)
                stats[key]["pf"]     = _calc_profit_factor(pnls)
                del stats[key]["pnls"]  # 生データは削除

            return stats
        except Exception as e:
            safe_print(f"❌ [FirestoreSniper] 週次統計取得失敗: {e}")
            return {}

    @classmethod
    def get_active_positions_ids(cls) -> list[str]:
        """Firestore から active/phase1/phase2 のポジションIDを取得する（再起動時の復元用）"""
        try:
            db = _FirestoreClient.get()
            docs = (
                db.collection(cls.COLLECTION)
                .where(filter=FieldFilter("status", "in", ["active", "phase1", "phase2"]))
                .stream()
            )
            return [doc.id for doc in docs]
        except Exception as e:
            safe_print(f"⚠️ [FirestoreSniper] アクティブポジション取得失敗: {e}")
            return []

    @classmethod
    async def record_exit_async(cls, pos: "Position", exit_rec: "ExitRecord") -> bool:
        """record_exit の非同期ラッパー（asyncio.create_task から呼び出し可能）"""
        return cls.record_exit(pos, exit_rec)


def _calc_max_drawdown(pnls: list[float]) -> float:
    """最大ドローダウンを計算する"""
    if not pnls:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    equity = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _calc_profit_factor(pnls: list[float]) -> float:
    """プロフィットファクター（総利益 / 総損失）を計算する"""
    gains  = sum(p for p in pnls if p > 0)
    losses = sum(-p for p in pnls if p < 0)
    return round(gains / losses, 2) if losses > 0 else float("inf")

"""
Bribe Sniper Simulator v3.0 - データモデル定義
ポジション・イベント・計算結果などのデータクラスを管理する
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Grade(str, Enum):
    """エントリーグレード"""
    S = "S"
    A = "A"


class PositionStatus(str, Enum):
    """ポジションのライフサイクルステータス"""
    ACTIVE    = "active"
    PHASE1    = "phase1"      # Phase1 (50%) 決済完了
    PHASE2    = "phase2"      # Phase2 (25%) 決済完了
    CLOSED    = "closed"      # 完全クローズ


class ExitPhase(str, Enum):
    """決済フェーズの種類"""
    PHASE1    = "Phase1 (+8% 利確)"
    PHASE2    = "Phase2 (+12% 利確)"
    PHASE3    = "Phase3 (トレーリングストップ)"
    HARD_STOP = "HardStop (-5% 損切)"
    TIME_EXIT = "TimeExit (木曜強制決済)"


@dataclass
class BribeEvent:
    """検知したBribeイベントのデータ"""
    bribe_token_address:  str
    bribe_token_symbol:   str
    bribe_amount_raw:     int
    bribe_amount_usd:     float
    pool_address:         str
    pool_name:            str
    external_bribe_addr:  str
    tx_hash:              str
    detected_at:          datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    tvl_usd:              float = 0.0
    pool_liquidity_usd:   float = 0.0
    current_weight:       int   = 0
    token0:               str   = ""
    token1:               str   = ""
    token0_symbol:        str   = ""
    token1_symbol:        str   = ""
    token0_decimals:      int   = 18
    token1_decimals:      int   = 18


@dataclass
class NetEVResult:
    """NetEV計算の結果"""
    entry_score:          int   = 0
    grade:                str   = ""
    trade_size_jst:       float = 0.0
    expected_return_rate: float = 0.0   # E_r
    slippage_cost_jst:    float = 0.0   # S_c
    gas_cost_jst:         float = 0.0   # G_c × 2
    net_ev_jst:           float = 0.0   # NetEV
    net_ev_ratio:         float = 0.0   # NetEV / T_s
    is_valid:             bool  = False
    reject_reason:        str   = ""


@dataclass
class ExitRecord:
    """決済記録"""
    phase:            str
    exit_price_usd:   float
    closed_ratio:     float   # 今回決済した割合
    size_jst:         float   # 今回決済したサイズ (JST)
    pnl_jst:          float   # 今回の損益 (JST、ガス控除後)
    pnl_pct:          float   # 損益率 (%)
    exited_at:        datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class Position:
    """仮想ポジション（エントリーから決済まで管理）"""
    # 識別情報
    position_id:          str
    pool_name:            str
    pool_address:         str
    bribe_token:          str
    grade:                str

    # エントリー情報
    entry_price_usd:      float
    entry_size_jst:       float
    entry_size_usd:       float
    net_ev_jst:           float
    entered_at:           datetime.datetime

    # ポジション追跡
    remaining_ratio:      float = 1.0
    peak_price_usd:       float = 0.0
    status:               str   = PositionStatus.ACTIVE
    phase1_done:          bool  = False
    phase2_done:          bool  = False
    trailing_stop_price:  float = 0.0

    # 損益集計
    realized_pnl_jst:     float = 0.0
    gas_cost_total_jst:   float = 0.0
    exit_records:         list  = field(default_factory=list)

    def __post_init__(self):
        self.peak_price_usd = self.entry_price_usd

    @staticmethod
    def generate_id(pool_name: str) -> str:
        """ユニークなポジションIDを生成"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short_id = str(uuid.uuid4())[:6].upper()
        safe_name = pool_name.replace("/", "-")
        return f"BS_{safe_name}_{ts}_{short_id}"

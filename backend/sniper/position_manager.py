"""
Bribe Sniper Simulator v3.0 - ポジション管理モジュール
段階的利確・トレーリングストップ・ハードストップを監視するバックグラウンドタスク
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Optional

from web3 import AsyncWeb3

from sniper.config import (
    EXIT_PHASE1_PROFIT, EXIT_PHASE2_PROFIT,
    EXIT_TRAILING_DROP, EXIT_HARD_STOP,
    PHASE1_CLOSE_RATIO, PHASE2_CLOSE_RATIO, PHASE3_CLOSE_RATIO,
    GAS_COST_JST, POSITION_MONITOR_SEC, JST_PER_USD,
)
from sniper.models import Position, ExitRecord, ExitPhase, PositionStatus
from sniper.sugar_checker import get_token_price_usd, get_weth_price_usd
from sniper.firestore_sniper import FirestoreSniperService
from sniper.discord_sniper import notify_exit


class PositionManager:
    """
    全仮想ポジションをオンメモリで管理し、定期的に価格をチェックして
    段階的利確・トレーリングストップ・ハードストップを自動実行する。
    """

    def __init__(self, w3_http: AsyncWeb3):
        # HTTP接続のWeb3（ポーリング用）
        self._w3 = w3_http
        # アクティブポジション: { position_id → Position }
        self._positions: dict[str, Position] = {}
        # 各ポジションのターゲットトークンアドレスのキャッシュ
        self._target_tokens: dict[str, str] = {}
        # 各ポジションのプールアドレスのキャッシュ
        self._pool_addrs: dict[str, str] = {}

    # ──────────────────────────────────────────────
    # ポジションの追加
    # ──────────────────────────────────────────────
    def add_position(self, pos: Position, target_token_address: str) -> None:
        """新規ポジションを管理対象に追加する"""
        self._positions[pos.position_id] = pos
        self._target_tokens[pos.position_id] = target_token_address
        self._pool_addrs[pos.position_id] = pos.pool_address
        print(
            f"➕ [PositionManager] ポジション追加: {pos.position_id} "
            f"({pos.pool_name} / {pos.grade}級 / エントリー ${pos.entry_price_usd:.4f})",
            flush=True
        )

    def get_all(self) -> list[Position]:
        return list(self._positions.values())

    def get_count(self) -> int:
        return len(self._positions)

    # ──────────────────────────────────────────────
    # バックグラウンド監視ループ
    # ──────────────────────────────────────────────
    async def monitor_loop(self) -> None:
        """
        30秒ごとに全ポジションの現在価格を確認し、
        出口条件を満たしていれば自動決済する。
        """
        print("🔄 [PositionManager] ポジション監視ループ 開始", flush=True)
        while True:
            try:
                await self._check_all_positions()
            except Exception as e:
                print(f"❌ [PositionManager] 監視ループエラー: {e}", flush=True)
            await asyncio.sleep(POSITION_MONITOR_SEC)

    async def _check_all_positions(self) -> None:
        """全ポジションを価格チェックして出口判定する"""
        if not self._positions:
            return

        weth_price = await get_weth_price_usd(self._w3)
        closed_ids = []

        for pid, pos in list(self._positions.items()):
            try:
                target_token = self._target_tokens.get(pid, "")
                pool_addr    = self._pool_addrs.get(pid, pos.pool_address)

                if not target_token:
                    continue

                # 現在価格を取得
                current_price = await get_token_price_usd(
                    self._w3, target_token, pool_addr, weth_price
                )
                if current_price <= 0:
                    continue

                # 最高値を更新
                if current_price > pos.peak_price_usd:
                    pos.peak_price_usd = current_price

                # トレーリングストップ価格を更新（Phase2完了後のみ）
                if pos.phase2_done and pos.trailing_stop_price > 0:
                    new_trail = pos.peak_price_usd * (1 - EXIT_TRAILING_DROP)
                    if new_trail > pos.trailing_stop_price:
                        pos.trailing_stop_price = new_trail

                # 出口条件をチェック（優先順位: ハードストップ > トレーリング > Phase2 > Phase1）
                is_closed = await self._evaluate_exits(pos, current_price)
                if is_closed:
                    closed_ids.append(pid)

            except Exception as e:
                print(f"⚠️ [PositionManager] {pid} チェックエラー: {e}", flush=True)

        # クローズ済みを管理対象から削除
        for pid in closed_ids:
            self._positions.pop(pid, None)
            self._target_tokens.pop(pid, None)
            self._pool_addrs.pop(pid, None)

    # ──────────────────────────────────────────────
    # 出口条件の評価
    # ──────────────────────────────────────────────
    async def _evaluate_exits(self, pos: Position, current_price: float) -> bool:
        """
        出口条件を評価して、該当する場合は決済処理を実行する。
        Returns: True なら完全クローズ（管理対象から除去すべき）
        """
        entry = pos.entry_price_usd
        if entry <= 0:
            return False

        price_change = (current_price - entry) / entry

        # ① ハードストップ (-5%)
        if price_change <= EXIT_HARD_STOP:
            await self._close_partial(
                pos, current_price,
                close_ratio=pos.remaining_ratio,
                phase=ExitPhase.HARD_STOP
            )
            pos.status = PositionStatus.CLOSED
            return True

        # ② トレーリングストップ（Phase2完了後・残り25%）
        if pos.phase2_done and pos.trailing_stop_price > 0:
            if current_price <= pos.trailing_stop_price:
                await self._close_partial(
                    pos, current_price,
                    close_ratio=pos.remaining_ratio,
                    phase=ExitPhase.PHASE3
                )
                pos.status = PositionStatus.CLOSED
                return True

        # ③ Phase2 (+12%) ─ Phase1完了後、残り25%をトレーリングに移行
        if pos.phase1_done and not pos.phase2_done:
            if price_change >= EXIT_PHASE2_PROFIT:
                close_ratio = pos.remaining_ratio * PHASE2_CLOSE_RATIO
                await self._close_partial(
                    pos, current_price,
                    close_ratio=close_ratio,
                    phase=ExitPhase.PHASE2
                )
                pos.phase2_done          = True
                pos.status               = PositionStatus.PHASE2
                # トレーリングストップを起動
                pos.trailing_stop_price  = current_price * (1 - EXIT_TRAILING_DROP)

        # ④ Phase1 (+8%)
        if not pos.phase1_done:
            if price_change >= EXIT_PHASE1_PROFIT:
                close_ratio = PHASE1_CLOSE_RATIO
                await self._close_partial(
                    pos, current_price,
                    close_ratio=close_ratio,
                    phase=ExitPhase.PHASE1
                )
                pos.phase1_done = True
                pos.status      = PositionStatus.PHASE1

        return pos.status == PositionStatus.CLOSED

    # ──────────────────────────────────────────────
    # 部分決済の実行
    # ──────────────────────────────────────────────
    async def _close_partial(
        self,
        pos: Position,
        exit_price_usd: float,
        close_ratio: float,
        phase: str,
    ) -> None:
        """
        ポジションの一部（close_ratio）を現在価格で仮想決済し、
        損益を計算してFirestore保存・Discord通知を行う。
        """
        if close_ratio <= 0:
            return

        entry = pos.entry_price_usd
        size_to_close_jst = pos.entry_size_jst * close_ratio

        # 損益率 (価格変化 + スリッページは無視（シミュレーションのため簡略化）)
        if entry > 0:
            pnl_pct = (exit_price_usd - entry) / entry
        else:
            pnl_pct = 0.0

        pnl_jst_before_gas = size_to_close_jst * pnl_pct
        gas = GAS_COST_JST  # 決済1回分のガス代
        pnl_jst = pnl_jst_before_gas - gas

        # ポジション更新
        pos.remaining_ratio   = max(0.0, pos.remaining_ratio - close_ratio)
        pos.realized_pnl_jst += pnl_jst
        pos.gas_cost_total_jst += gas

        # 決済記録
        exit_rec = ExitRecord(
            phase          = phase,
            exit_price_usd = exit_price_usd,
            closed_ratio   = close_ratio,
            size_jst       = size_to_close_jst,
            pnl_jst        = pnl_jst,
            pnl_pct        = pnl_pct * 100,
        )
        pos.exit_records.append(exit_rec)

        print(
            f"✅ [PositionManager] {phase} 決済: {pos.position_id} "
            f"価格=${exit_price_usd:.4f} 損益={pnl_jst:+.1f}JST",
            flush=True
        )

        # 非同期でFirestore保存・Discord通知
        asyncio.create_task(
            FirestoreSniperService.record_exit_async(pos, exit_rec)
        )
        asyncio.create_task(
            notify_exit(pos, exit_rec, exit_price_usd)
        )

    # ──────────────────────────────────────────────
    # 木曜タイムエグジット（全ポジション強制クローズ）
    # ──────────────────────────────────────────────
    async def force_close_all(self, reason: str = ExitPhase.TIME_EXIT) -> int:
        """
        全ポジションを強制クローズする（木曜タイムエグジット用）。
        Returns: 決済したポジション数
        """
        if not self._positions:
            return 0

        weth_price = await get_weth_price_usd(self._w3)
        count = 0

        for pid, pos in list(self._positions.items()):
            try:
                target_token = self._target_tokens.get(pid, "")
                pool_addr    = self._pool_addrs.get(pid, pos.pool_address)

                current_price = await get_token_price_usd(
                    self._w3, target_token, pool_addr, weth_price
                ) if target_token else pos.entry_price_usd

                await self._close_partial(
                    pos, current_price,
                    close_ratio=pos.remaining_ratio,
                    phase=reason
                )
                pos.status = PositionStatus.CLOSED
                count += 1

            except Exception as e:
                print(f"⚠️ [PositionManager] 強制クローズ失敗 {pid}: {e}", flush=True)

        # 全クリア
        self._positions.clear()
        self._target_tokens.clear()
        self._pool_addrs.clear()

        print(f"🏳️ [PositionManager] {count}件のポジションを強制クローズしました。", flush=True)
        return count

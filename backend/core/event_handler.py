import asyncio
from web3 import AsyncWeb3
from core.analyzer import decode_calldata, track_proxy_implementation
from core.pricing import get_t0_price_and_slippage, get_current_price
from services.ai_service import AIService
from services.discord_service import DiscordService
from services.firebase_service import FirebaseService

async def handle_call_scheduled(w3: AsyncWeb3, log: dict):
    """
    Phase 1: Scheduled (T=0) イベント検知時の処理
    """
    tx_hash = log.get('transactionHash').hex()
    contract_addr = log.get('address')
    raw_data = log.get('data', '0x')
    if isinstance(raw_data, bytes):
        raw_data = raw_data.hex()
    
    print("\n" + "="*50)
    print(f"🚨 [T=0] タイムロック変更予約(CallScheduled) を検知！")
    print(f"🔗 Contract: {contract_addr}")
    print(f"🧾 TX: https://basescan.org/tx/{tx_hash}")
    
    # Phase 2: データ構造解析
    decoded_data = decode_calldata(raw_data)
    print(f"🧬 デコード済みデータ: {decoded_data}")
    
    # Phase 3: ラグゼロ価格取得 & スリッページ計算
    t0_price, slippage = await get_t0_price_and_slippage(w3, contract_addr)
    print(f"📊 リアルタイム価格取得完了: ${t0_price} (想定スリッページ: {slippage}%)")
    
    # Phase 4: S/A/Bのスコアリング・GeminiAI連携 (一次判定: Flash)
    tvl_ratio = 0.10 # モックのTVL割合 (10%)
    ai_score, ai_rank, ai_summary = await AIService.analyze_calldata_risk(decoded_data, tvl_ratio)
    print(f"🤖 AI評価(一次): {ai_rank}級 (Score: {ai_score}) - {ai_summary}")
    
    # ランクが低すぎる場合は無視
    if ai_score < 60:
        print("⚠️ スコアが基準未満のため、Discord通知と重い処理をスキップします。")
        return
        
    # Phase 6: Firestore (T=0) シミュレーション保存 (まずは一次情報を記録)
    FirebaseService.save_simulation_t0(
        tx_hash=tx_hash,
        contract_addr=contract_addr,
        raw_data=raw_data,
        t0_price=t0_price,
        slippage=slippage,
        ai_score=ai_score,
        ai_rank=ai_rank,
        ai_summary=ai_summary
    )

    # --- 二段構えの解析フロー (Aランク以上で深層分析発動) ---
    if ai_rank in ["S", "A"]:
        print(f"🔍 {ai_rank}級ランクにつき、深層トレンド分析を開始します...")
        
        # 1. 過去7日間の履歴を取得
        recent_events = FirebaseService.get_recent_scheduled_events(days=7)
        
        # 2. 最新の Pro モデルで深層分析 (日常・トレンド・総合判断)
        deep_result = await AIService.analyze_with_trend(decoded_data, tvl_ratio, recent_events)
        print(f"⚖️ 深層分析完了: {deep_result.get('final_decision')}")
        
        # 3. リッチ版の深層分析アラートを送信
        DiscordService.send_deep_analysis_alert(contract_addr, tx_hash, deep_result)
        print("📱 Discordへ【深層分析】リッチ通知を送信しました")
    else:
        # 通常の B ランク等は Flash の結果で標準通知
        DiscordService.send_t0_entry_notification(contract_addr, tx_hash, t0_price, slippage, ai_rank, ai_score, ai_summary)
        print("📱 Discordへ通常エントリー通知を送信しました")


async def handle_call_executed(w3: AsyncWeb3, log: dict):
    """
    Phase 1: Executed (T+48) イベント検知時の処理
    """
    tx_hash = log.get('transactionHash').hex()
    contract_addr = log.get('address')
    
    print("\n" + "="*50)
    print(f"🏁 [T+48] タイムロック実行(CallExecuted) を検知！")
    print(f"🔗 Contract: {contract_addr}")
    
    # T+48時の価格を取得
    t48_price = await get_current_price(w3, contract_addr)
    print(f"📊 実行時の価格取得完了: ${t48_price}")
    
    # Firestore情報の更新と仮想PnL計算
    pnl, t0_price = FirebaseService.update_simulation_t48(tx_hash, t48_price)
    
    if pnl is not None and t0_price is not None:
        DiscordService.send_t48_answer_notification(contract_addr, tx_hash, t0_price, t48_price, pnl)
        print("📱 Discordへの答え合わせ通知完了。")

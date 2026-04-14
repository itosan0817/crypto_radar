import sys
import os
import asyncio
import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.discord_service import DiscordService
from sniper.discord_sniper import notify_error, notify_entry
from sniper.models import Position
from sniper.firestore_sniper import FirestoreSniperService
from sniper.safe_io import safe_print

async def test_discord():
    safe_print("🚀 Discord通知の送信テストを開始します...")
    
    # 1. Aerodrome Radar Test (DiscordService) - エリア：タイムロックなど
    safe_print("1️⃣ Aerodrome Radar のエラー通知（生存確認）を送信...")
    DiscordService.send_error_notification(
        "Aerodrome Radar [テスト送信]", 
        "【生存確認】通信テストです。このプログラムはインターネットとDiscordに接続できています。ご安心ください！"
    )
    
    # 2. Bribe Sniper Test (discord_sniper) - エリア：スナイパー
    safe_print("2️⃣ Bribe Sniper のエラー通知（生存確認）を送信...")
    await notify_error(
        "Bribe Sniper SIM [テスト送信]", 
        "【生存確認】通知テストです。WebSocket等に致命的エラーが起きれば、このように確実に通知されます。"
    )
    
    # 3. エントリー通知も偽造して送ってみる（ポジティブな稼働確認）
    safe_print("3️⃣ Bribe Sniper の架空のS級エントリー通知を送信...")
    dummy_pos = Position(
        position_id="TEST-0000-0000",
        pool_name="WETH/USDC (シミュレーションテスト)",
        pool_address="0x000000000000000000000000000000000000TEST",
        bribe_token="USDC",
        grade="S",
        entry_price_usd=2500.0,
        entry_size_jst=100000.0,
        entry_size_usd=666.0,
        net_ev_jst=150000.0,
        entered_at=datetime.datetime.now(datetime.timezone.utc)
    )
    
    await notify_entry(
        pos=dummy_pos, 
        net_ev_jst=150000.0, 
        delay_sec=1.2, 
        bribe_amount_usd=100000.0, 
        tvl_usd=5000000.0, 
        entry_score=98
    )
    
    # 4. Firestore 書き込みテスト
    safe_print("4️⃣ Firestore 'bribe_positions' へのテストデータ保存を開始...")
    success = FirestoreSniperService.save_entry(dummy_pos)
    if success:
        safe_print("✅ Firestore への書き込みに成功しました！")
    else:
        safe_print("❌ Firestore への書き込みに失敗しました。ログを確認してください。")

    safe_print("\n🏁 全てのテスト工程が完了しました！")
    safe_print("Discordの通知と、Google CloudコンソールのFirestore画面をリロードして確認してください。")

if __name__ == "__main__":
    asyncio.run(test_discord())

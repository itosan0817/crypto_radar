import sys
import os
import asyncio
import datetime
import pytz

# カレントディレクトリをパスに追加
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.discord_service import DiscordService
from sniper.discord_sniper import notify_entry, notify_error, notify_weekly_report
from sniper.models import Position
from sniper.safe_io import safe_print

async def test_notifications():
    safe_print("--- Discord notification test start (Rich Version) ---")
    
    # 1. Timelock monitoring system (DiscordService) test
    safe_print("1: DiscordService: Sending system status notification...")
    DiscordService.send_error_notification(
        "Crypto Radar [System Test]", 
        "【動作確認】タイムロック監視システムの通知テストです。リッチなEmbedが表示されていれば成功です。"
    )
    
    # 2. Bribe Sniper (discord_sniper) entry notification test
    safe_print("2: Bribe Sniper: Sending virtual entry notification (S-grade)...")
    dummy_pos = Position(
        position_id="TEST-PNL-8888",
        pool_name="WETH/USDC (Test Pool)",
        pool_address="0x000000000000000000000000000000000000TEST",
        bribe_token="USDC",
        grade="S",
        entry_price_usd=2500.0,
        entry_size_jst=90000.0,
        entry_size_usd=600.0,
        net_ev_jst=120000.0,
        entered_at=datetime.datetime.now(datetime.timezone.utc)
    )
    
    await notify_entry(
        pos=dummy_pos, 
        net_ev_jst=125000.0, 
        delay_sec=1.5, 
        bribe_amount_usd=50000.0, 
        tvl_usd=10000000.0, 
        entry_score=99
    )
    
    # 3. Bribe Sniper Weekly Report test
    safe_print("3: Bribe Sniper: Sending weekly report test notification...")
    dummy_stats = {
        "S": {"count": 12, "wins": 10, "total_pnl": 45000.0, "pf": 3.5, "max_dd": 5000.0},
        "A": {"count": 25, "wins": 15, "total_pnl": 12000.0, "pf": 1.8, "max_dd": 8000.0},
        "total": {"count": 37, "wins": 25, "total_pnl": 57000.0, "pf": 2.4, "max_dd": 8000.0},
        "by_token": {
            "USDC": {"count": 10, "pnl": 25000.0},
            "WETH": {"count": 8, "pnl": 15000.0},
            "AERO": {"count": 15, "pnl": 17000.0}
        },
        "hourly": {
            8: {"count": 5, "pnl": 20000.0},
            0: {"count": 2, "pnl": 5000.0}
        }
    }
    await notify_weekly_report(dummy_stats)

    safe_print("\n--- All notification tests completed! ---")
    safe_print("Please check your Discord channels.")

if __name__ == "__main__":
    asyncio.run(test_notifications())

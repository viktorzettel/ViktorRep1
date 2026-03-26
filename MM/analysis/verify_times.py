"""
Time Zone Verification Script

Verifies that Binance timestamps are correctly translated to ET (Eastern Time).
"""

import requests
from datetime import datetime, timedelta, timezone

BINANCE_API = "https://api.binance.com/api/v3/klines"
UTC = timezone.utc

def verify_times():
    print("=" * 70)
    print("TIME ZONE VERIFICATION")
    print("=" * 70)
    
    # Get current time for reference
    now_utc = datetime.now(UTC)
    print(f"\nCurrent UTC time: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Current local time (system): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Determine if we're in EST or EDT
    # EST = UTC-5 (Nov-Mar)
    # EDT = UTC-4 (Mar-Nov)
    # Currently January 31, 2026 = EST
    print(f"\nNote: January = EST (UTC-5), not EDT (UTC-4)")
    
    ET_OFFSET = -5  # EST
    now_et = now_utc + timedelta(hours=ET_OFFSET)
    print(f"Current ET time (calculated): {now_et.strftime('%Y-%m-%d %H:%M:%S')} ET")
    
    # Fetch latest 5 hourly candles from Binance
    print("\n" + "-" * 70)
    print("BINANCE CANDLE TIMESTAMPS")
    print("-" * 70)
    
    end_ms = int(now_utc.timestamp() * 1000)
    start_ms = end_ms - (5 * 60 * 60 * 1000)  # 5 hours ago
    
    params = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 10,
    }
    
    resp = requests.get(BINANCE_API, params=params)
    klines = resp.json()
    
    print(f"\nFetched {len(klines)} hourly candles\n")
    print(f"{'Raw Open (ms)':<18} | {'UTC Time':<22} | {'ET Time (UTC-5)':<22} | Open Price")
    print("-" * 90)
    
    for k in klines:
        open_ms = k[0]
        open_price = k[1]
        
        # Convert to UTC
        open_utc = datetime.fromtimestamp(open_ms / 1000, tz=UTC)
        
        # Convert to ET (manual offset)
        open_et = open_utc + timedelta(hours=ET_OFFSET)
        
        print(f"{open_ms:<18} | {open_utc.strftime('%Y-%m-%d %H:%M:%S')} | {open_et.strftime('%Y-%m-%d %H:%M:%S')} ET | ${float(open_price):,.2f}")
    
    # Verify with Polymarket market times
    print("\n" + "-" * 70)
    print("POLYMARKET MARKET ALIGNMENT CHECK")
    print("-" * 70)
    
    print("""
    Polymarket hourly markets run on ET (Eastern Time):
    - "9am ET" market: 9:00:00 AM to 9:59:59 AM ET
    - Strike = BTC price at 9:00:00 AM ET
    - Close = BTC price at 9:59:59 AM ET
    
    If our ET conversion is correct, then:
    - A Binance candle with open_time "2026-01-31 14:00:00 UTC"
    - Should be labeled as "2026-01-31 09:00:00 ET" (9 AM ET market)
    """)
    
    # Example verification
    print("-" * 70)
    print("SANITY CHECK:")
    print("-" * 70)
    
    # Check a specific timestamp
    test_utc = datetime(2026, 1, 31, 14, 0, 0, tzinfo=UTC)
    test_et = test_utc + timedelta(hours=ET_OFFSET)
    print(f"\n  Test: 2026-01-31 14:00:00 UTC")
    print(f"  → Should be 9:00 AM ET (14 - 5 = 9)")
    print(f"  → Calculated: {test_et.strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"  → Correct? {test_et.hour == 9}")
    
    # Another check
    test_utc2 = datetime(2026, 1, 31, 20, 0, 0, tzinfo=UTC)
    test_et2 = test_utc2 + timedelta(hours=ET_OFFSET)
    print(f"\n  Test: 2026-01-31 20:00:00 UTC")
    print(f"  → Should be 3:00 PM ET (20 - 5 = 15)")
    print(f"  → Calculated: {test_et2.strftime('%Y-%m-%d %H:%M:%S')} ET")
    print(f"  → Correct? {test_et2.hour == 15}")
    
    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    verify_times()

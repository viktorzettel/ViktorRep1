"""
DEBUG STRADDLE CHECK

Diagnose why the previous script found 0 hourly markets.
"""

import requests
import time
from datetime import datetime

# Helper to parse time
def parse_iso(ts):
    if not ts: return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

print("=" * 80)
print("🧐 DEBUGGING MARKET FILTER")
print("=" * 80)

# Fetch batch of markets and print raw duration data
url = "https://gamma-api.polymarket.com/events"
params = {
    "closed": "true",
    "limit": 50,
    "tag_slug": "bitcoin"
}

resp = requests.get(url, params=params)
events = resp.json()

print(f"Fetched {len(events)} events.")

print("\nChecking first 20 events:")
print(f"{'Title':<50} | {'Start':<20} | {'End':<20} | {'Duration (h)':^10} | {'Is Hourly?'}")
print("-" * 120)

for e in events[:20]:
    title = e.get("title", "")[:48]
    
    markets = e.get("markets", [])
    if not markets:
        print(f"{title:<50} | {'No Markets':<20}")
        continue
        
    m = markets[0]
    start_str = m.get("startDate")
    end_str = m.get("endDate")
    
    start = parse_iso(start_str)
    end = parse_iso(end_str)
    
    if start and end:
        duration_hours = (end - start).total_seconds() / 3600
        is_hourly = 0.5 <= duration_hours <= 1.5
        
        # Check specific slug
        slug = e.get("slug", "")
        if "january-31-10am" in slug:
            print(f"--> FOUND TARGET: {slug}")
            print(f"    Start: {start_str}")
            print(f"    End:   {end_str}")
            print(f"    Diff:  {duration_hours}")
            
        print(f"{title:<50} | {start_str[:19]:<20} | {end_str[:19]:<20} | {duration_hours:^10.2f} | {is_hourly}")
    else:
        print(f"{title:<50} | {'Invalid Dates':<20}")

print("\nConclusion: Check if startDate is actually the market open time or something else.")
print("Sometimes startDate = creationDate, which might be days before.")

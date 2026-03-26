from scanner import scan_markets

print("Testing scan_markets...")
results = scan_markets(limit=5)
if results:
    for r in results:
        print(f"[{r.score:.1f}] {r.slug} - ${r.liquidity:,.0f} liq - Price: {r.mid_price}")
else:
    print("Zero markets found.")

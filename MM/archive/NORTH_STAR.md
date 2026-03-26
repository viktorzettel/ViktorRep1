# The North Star: Path to Profitability

## Current Status
We have successfully diagnosed the "Model Failure" as a **Volatility Mismatch**. The theoretical model (Gram-Charlier) was using "Realized Volatility" (e.g., 40%) while the "Degenerate Market" was pricing in "Implied Volatility" (e.g., 80-150%). This caused the model to think the market was irrational (divergent) and refuse to trade.

## The Solution: Implied Volatility Calibration
I have upgraded `strategy.py` and `pricing.py` to:
1.  **Listen**: Calculate the Implied Volatility (IV) from the current market price.
2.  **Adapt**: Use this IV to calibrate the "Fair Value".
3.  **Quote**: Anchor quotes around the Market Price (now validated by the Calibrated Model) and capture the spread.

## Next Steps

### 1. Manual Market Selection
Since the API search for "Hourly Bitcoin" is currently returning noise, you must verify a market manually:
1.  Go to [Polymarket - Bitcoin](https://polymarket.com/event/bitcoin-prices).
2.  Find an "Hourly" or "Daily" market expiring soon (e.g., "Bitcoin > 96k").
3.  Copy the **Slug** from the URL (e.g., `bitcoin-above-96k-jan-28`).
4.  Copy the **Token ID** for the YES outcome (from Developer Tools or by running `python get_tokens.py <slug>`).

### 2. The Dry Run (Safety First)
Run the bot in simulation mode to verify it quotes correctly without risking capital:
```bash
python3 main.py <market_slug> <token_id_YES> --dry-run
```
**Watch the logs for:**
- `⚡ Implied Vol Calibration: 40% -> 120%` (This confirms the fix is working).
- `🧪 DRY RUN: Would POST ...` (This confirms it wants to trade).
- `🛡️ TREND GUARD` (This confirms it detects risks).

### 3. Live Deployment
Once you see sensible quotes in Dry Run:
1.  Remove `--dry-run`.
2.  Start small (Inventory Limit $10 is already set).
3.  Monitor `data_feed.py` logs for any "Toxic Flow" warnings.

## Future: Automation
Once this single-market loop is profitable:
1.  We can fix `find_market.py` to parse the exact specific naming convention of the hourly markets.
2.  Enable `AutoPilot` (`--auto`) to rotate through markets 10,000 times a day.

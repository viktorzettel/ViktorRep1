import json
from pathlib import Path

# Load data
with open('data/btc_1min_30d.json') as f:
    klines_1m = json.load(f)

# Reconstruct 15-min candles
candles = []
for i in range(0, len(klines_1m)-14, 15):
    block = klines_1m[i:i+15]
    open_p = block[0]['open']
    
    # Minute returns
    min_rets = [(c['close'] - open_p)/open_p for c in block]
    candles.append(min_rets)

print(f'{len(candles)} candles loaded')

# ------------------
# STRATEGY 1: MOMENTUM
# Buy Winner at 0.05% (~55¢), Sell at 0.10% (~60¢)
# Loss: Expiry (0¢)
# ------------------
wins_mom = 0
losses_mom = 0

for candle in candles:
    entry_idx = -1
    direction = 0
    
    # Check for entry
    for j, ret in enumerate(candle):
        if abs(ret) > 0.0005: 
            entry_idx = j
            direction = 1 if ret > 0 else -1
            break
            
    if entry_idx != -1:
        # Check if hits profit target
        win = False
        target = 0.0010 * direction
        
        # Check subsequent minutes
        for k in range(entry_idx+1, len(candle)):
            if (direction == 1 and candle[k] >= target) or \
               (direction == -1 and candle[k] <= target):
                win = True
                break
        
        if win:
            wins_mom += 1
        else:
            # Check final close - if closed in direction > target, count as win?
            # Strategy says "sell at 55+5", implying limit order.
            # If never hit, we hold to expiry. 
            # If close > 0 (for Buy YES) -> Pay 100¢.
            # Let's count expiry wins too.
            if (direction == 1 and candle[-1] > 0) or \
               (direction == -1 and candle[-1] < 0):
                # But wait, original plan was "sell at 60¢". 
                # If we don't hit 60¢ but close winning, we get 100¢.
                # To be conservative, let's say we only get 100¢ if close > target?
                # No, binary pays 100¢ if merely > 0.
                wins_mom += 1
            else:
                losses_mom += 1

total_mom = wins_mom + losses_mom
if total_mom > 0:
    # EV calculation:
    # On 55¢ -> 60¢ scalp: +5¢ profit
    # On expiry win: +45¢ profit (100-55)
    # On loss: -55¢ loss
    # Hard to model mix. Let's look at raw win rate first.
    win_rate = wins_mom / total_mom * 100
    print(f"\nSTRATEGY 1 (Momentum):")
    print(f"Trades: {total_mom}")
    print(f"Win Rate: {win_rate:.1f}%")
    
    # Simplified EV: Assume we simply Hold to Expiry if triggered
    # Entry 55¢, Win 100¢, Loss 0¢
    ev_hold = (win_rate/100 * 45) - ((100-win_rate)/100 * 55)
    print(f"EV (Hold to Expiry): {ev_hold:.1f}¢ per trade")


# ------------------
# STRATEGY 2: REVERSION
# Buy Loser at 0.08% (~42¢), Sell at 0.04% (~46¢)
# ------------------
wins_rev = 0
losses_rev = 0

for candle in candles:
    entry_idx = -1
    direction = 0 # 1 = Buy NO (when price up), -1 = Buy YES (when price down)
    
    for j, ret in enumerate(candle):
        if abs(ret) > 0.0008:
            entry_idx = j
            # If ret > 0 (price UP), loser is NO. We buy NO.
            direction = 1 if ret > 0 else -1
            break
    
    if entry_idx != -1:
        reverted = False
        # Target: price moves back halfway (to 0.04%)
        target = 0.0004 * (1 if direction == 1 else -1)
        
        for k in range(entry_idx+1, len(candle)):
            # If bought NO (price was > 0.08), want price <= 0.04
            if direction == 1:
                if candle[k] <= 0.0004:
                    reverted = True
                    break
            # If bought YES (price was < -0.08), want price >= -0.04
            else:
                if candle[k] >= -0.0004:
                    reverted = True
                    break
        
        if reverted:
            wins_rev += 1
        else:
            losses_rev += 1

total_rev = wins_rev + losses_rev
if total_rev > 0:
    win_rate_rev = wins_rev / total_rev * 100
    print(f"\nSTRATEGY 2 (Reversion):")
    print(f"Trades: {total_rev}")
    print(f"Win Rate: {win_rate_rev:.1f}%")
    # EV: +4¢ win, -42¢ loss
    ev_rev = (win_rate_rev/100 * 4) - ((100-win_rate_rev)/100 * 42)
    print(f"EV: {ev_rev:.1f}¢ per trade")

from pricing import CryptoHourlyPricer

current = 100000.0
strike = 100100.0 # Slightly OTM (Up)
time_left = 1800 # 30 mins

print(f"Spot: {current}, Strike: {strike}, Time: {time_left/60}m")
print("-" * 40)

for vol in [0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0]:
    prob = CryptoHourlyPricer.calculate_probability(current, strike, time_left, vol)
    print(f"Vol: {vol*100:>3.0f}% | Prob: {prob:.4f}")

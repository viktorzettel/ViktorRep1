"""
BTC 15-Minute Return Distribution Analysis

Downloads 30 days of 1-minute candle data from Binance,
aggregates into 15-minute blocks, and analyzes whether
Black-Scholes (normal distribution) is appropriate for
binary options pricing.

Outputs:
1. Distribution statistics (skewness, kurtosis, normality tests)
2. Intraday volatility heatmap (hour × day-of-week)
3. Q-Q plot vs normal distribution
4. Fat tail analysis
5. Empirical CDF of 15-min returns
"""

import asyncio
import time
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import aiohttp
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy import stats

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

CACHE_FILE = DATA_DIR / "btc_1min_30d.json"


# =============================================================================
# DATA DOWNLOAD
# =============================================================================

async def download_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    days: int = 30,
) -> list[dict]:
    """
    Download historical klines from Binance.
    
    Binance limits to 1000 candles per request for 1m data.
    30 days × 1440 min/day = 43,200 candles → 44 API calls.
    """
    # Check cache first
    if CACHE_FILE.exists():
        print(f"📂 Loading cached data from {CACHE_FILE.name}...")
        with open(CACHE_FILE) as f:
            data = json.load(f)
        
        # Check if cache is recent enough
        if data and len(data) > 0:
            last_ts = data[-1]["timestamp"] / 1000
            age_hours = (time.time() - last_ts) / 3600
            if age_hours < 24:
                print(f"   Cache is {age_hours:.1f}h old, {len(data)} candles")
                return data
            else:
                print(f"   Cache is {age_hours:.0f}h old, refreshing...")
    
    all_candles = []
    end_time = int(time.time() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    
    current_start = start_time
    batch_size = 1000
    total_expected = days * 24 * 60
    
    print(f"\n📥 Downloading {days} days of {interval} data for {symbol}")
    print(f"   Expected: ~{total_expected:,} candles")
    print(f"   API calls needed: ~{total_expected // batch_size + 1}")
    
    url = "https://api.binance.com/api/v3/klines"
    
    async with aiohttp.ClientSession() as session:
        call_count = 0
        while current_start < end_time:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "limit": batch_size,
            }
            
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    print(f"   ❌ API error: {resp.status}")
                    break
                
                klines = await resp.json()
            
            if not klines:
                break
            
            for k in klines:
                all_candles.append({
                    "timestamp": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            
            current_start = klines[-1][0] + 60000  # Next minute
            call_count += 1
            
            if call_count % 10 == 0:
                pct = len(all_candles) / total_expected * 100
                print(f"   [{call_count}] {len(all_candles):,} candles ({pct:.0f}%)")
            
            # Rate limit (Binance allows 1200 req/min)
            await asyncio.sleep(0.1)
    
    print(f"   ✅ Downloaded {len(all_candles):,} candles")
    
    # Cache to disk
    with open(CACHE_FILE, 'w') as f:
        json.dump(all_candles, f)
    print(f"   💾 Cached to {CACHE_FILE.name}")
    
    return all_candles


# =============================================================================
# DATA PROCESSING
# =============================================================================

def aggregate_to_15min(candles_1m: list[dict]) -> list[dict]:
    """Aggregate 1-minute candles into 15-minute blocks."""
    candles_15m = []
    
    for i in range(0, len(candles_1m) - 14, 15):
        block = candles_1m[i:i+15]
        
        candles_15m.append({
            "timestamp": block[0]["timestamp"],
            "open": block[0]["open"],
            "high": max(c["high"] for c in block),
            "low": min(c["low"] for c in block),
            "close": block[-1]["close"],
            "volume": sum(c["volume"] for c in block),
        })
    
    return candles_15m


def compute_returns(candles: list[dict], return_type: str = "log") -> np.ndarray:
    """Compute returns from candle data."""
    opens = np.array([c["open"] for c in candles])
    closes = np.array([c["close"] for c in candles])
    
    if return_type == "log":
        return np.log(closes / opens)
    else:
        return (closes - opens) / opens


# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================

def analyze_distribution(returns: np.ndarray, output_dir: Path):
    """Full distribution analysis of 15-minute returns."""
    
    print("\n" + "="*70)
    print("📊 15-MINUTE RETURN DISTRIBUTION ANALYSIS")
    print("="*70)
    
    # Basic stats
    n = len(returns)
    mean = np.mean(returns)
    std = np.std(returns)
    skew = stats.skew(returns)
    kurt = stats.kurtosis(returns)  # Excess kurtosis (normal = 0)
    
    print(f"\nSample Size: {n} candles")
    print(f"Mean Return: {mean*100:.4f}%")
    print(f"Std Dev:     {std*100:.4f}%")
    print(f"Skewness:    {skew:.3f}  (normal = 0)")
    print(f"Ex. Kurtosis: {kurt:.3f}  (normal = 0)")
    
    # Annualized vol
    annual_vol = std * np.sqrt(96 * 365)  # 96 fifteen-min periods per day
    print(f"Annualized Vol: {annual_vol*100:.1f}%")
    
    # Dollar terms
    btc_price = 70000
    dollar_std = btc_price * std
    print(f"\n1σ move (15 min): ${dollar_std:.0f}")
    print(f"2σ move (15 min): ${2*dollar_std:.0f}")
    print(f"3σ move (15 min): ${3*dollar_std:.0f}")
    
    # Normality tests
    print("\n" + "-"*70)
    print("NORMALITY TESTS")
    print("-"*70)
    
    # Jarque-Bera
    jb_stat, jb_p = stats.jarque_bera(returns)
    print(f"Jarque-Bera:    stat={jb_stat:.1f}, p={jb_p:.6f}  {'❌ REJECT' if jb_p < 0.05 else '✅ NORMAL'}")
    
    # Shapiro-Wilk (use subsample if n > 5000)
    subsample = returns[:5000] if n > 5000 else returns
    sw_stat, sw_p = stats.shapiro(subsample)
    print(f"Shapiro-Wilk:   stat={sw_stat:.6f}, p={sw_p:.6f}  {'❌ REJECT' if sw_p < 0.05 else '✅ NORMAL'}")
    
    # Anderson-Darling
    ad_result = stats.anderson(returns, dist='norm')
    ad_reject = ad_result.statistic > ad_result.critical_values[2]  # 5% level
    print(f"Anderson-Darling: stat={ad_result.statistic:.3f}, 5% crit={ad_result.critical_values[2]:.3f}  {'❌ REJECT' if ad_reject else '✅ NORMAL'}")
    
    # Tail analysis
    print("\n" + "-"*70)
    print("TAIL ANALYSIS (vs Normal Distribution)")
    print("-"*70)
    
    thresholds = [1, 1.5, 2, 2.5, 3, 4]
    print(f"{'Threshold':>12} {'Observed':>10} {'Normal Expected':>16} {'Ratio':>8}")
    print("-"*50)
    
    for t in thresholds:
        observed = np.mean(np.abs(returns) > t * std)
        expected = 2 * (1 - stats.norm.cdf(t))
        ratio = observed / expected if expected > 0 else float('inf')
        print(f"  >{t:.1f}σ moves  {observed*100:>8.2f}%  {expected*100:>14.2f}%  {ratio:>7.1f}x")
    
    # Largest moves
    print("\n" + "-"*70)
    print("TOP 10 LARGEST 15-MIN MOVES")
    print("-"*70)
    
    sorted_idx = np.argsort(np.abs(returns))[::-1]
    print(f"{'Rank':>4} {'Return':>10} {'Dollar':>10} {'Sigma':>8}")
    for rank, idx in enumerate(sorted_idx[:10], 1):
        r = returns[idx]
        dollar = btc_price * r
        sigma = abs(r) / std
        print(f"  {rank:>2}  {r*100:>+8.3f}%  ${dollar:>+8.0f}  {sigma:>6.1f}σ")
    
    return {
        "mean": mean, "std": std, "skew": skew, "kurtosis": kurt,
        "jb_p": jb_p, "sw_p": sw_p, "n": n
    }


def analyze_intraday_vol(candles_15m: list[dict], returns: np.ndarray, output_dir: Path):
    """Analyze volatility patterns by hour and day of week."""
    
    print("\n" + "="*70)
    print("🕐 INTRADAY VOLATILITY PATTERNS")
    print("="*70)
    
    # Tag each return with hour and day
    hours = []
    days = []
    for c in candles_15m:
        dt = datetime.fromtimestamp(c["timestamp"] / 1000, tz=timezone.utc)
        hours.append(dt.hour)
        days.append(dt.weekday())
    
    hours = np.array(hours[:len(returns)])
    days = np.array(days[:len(returns)])
    
    # Hourly volatility
    print(f"\n{'Hour (UTC)':>12} {'Vol (%)':>10} {'Samples':>10} {'Avg $Move':>12}")
    print("-"*50)
    
    hourly_vol = {}
    for h in range(24):
        mask = hours == h
        if mask.sum() > 10:
            vol = np.std(returns[mask])
            hourly_vol[h] = vol
            dollar_move = 70000 * vol
            print(f"  {h:02d}:00 UTC  {vol*100:>8.4f}%  {mask.sum():>8}  ${dollar_move:>10.0f}")
    
    # Find most/least volatile hours
    if hourly_vol:
        max_h = max(hourly_vol, key=hourly_vol.get)
        min_h = min(hourly_vol, key=hourly_vol.get)
        ratio = hourly_vol[max_h] / hourly_vol[min_h]
        print(f"\n  Most volatile:  {max_h:02d}:00 UTC ({hourly_vol[max_h]*100:.4f}%)")
        print(f"  Least volatile: {min_h:02d}:00 UTC ({hourly_vol[min_h]*100:.4f}%)")
        print(f"  Ratio: {ratio:.1f}x")
    
    # Day of week volatility
    print(f"\n{'Day':>12} {'Vol (%)':>10} {'Samples':>10}")
    print("-"*35)
    
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for d in range(7):
        mask = days == d
        if mask.sum() > 10:
            vol = np.std(returns[mask])
            print(f"  {day_names[d]:>10}  {vol*100:>8.4f}%  {mask.sum():>8}")
    
    return hourly_vol


def plot_all(returns: np.ndarray, candles_15m: list[dict], hourly_vol: dict, output_dir: Path):
    """Generate all plots."""
    
    std = np.std(returns)
    mean = np.mean(returns)
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle("BTC 15-Minute Return Analysis (30 Days)", fontsize=16, fontweight='bold')
    
    # 1. Histogram vs Normal
    ax = axes[0, 0]
    ax.hist(returns * 100, bins=100, density=True, alpha=0.7, color='steelblue', 
            label='Observed', edgecolor='white', linewidth=0.5)
    x = np.linspace(returns.min() * 100, returns.max() * 100, 200)
    ax.plot(x, stats.norm.pdf(x, mean * 100, std * 100), 'r-', linewidth=2, label='Normal fit')
    ax.set_xlabel('Return (%)')
    ax.set_ylabel('Density')
    ax.set_title('Return Distribution vs Normal')
    ax.legend()
    ax.set_xlim(-1.5, 1.5)
    
    # 2. Q-Q Plot
    ax = axes[0, 1]
    sorted_returns = np.sort(returns)
    n = len(sorted_returns)
    theoretical = stats.norm.ppf(np.linspace(0.001, 0.999, n))
    actual = (sorted_returns - mean) / std
    ax.scatter(theoretical, actual, s=1, alpha=0.3, color='steelblue')
    lim = max(abs(theoretical.min()), abs(theoretical.max()))
    ax.plot([-lim, lim], [-lim, lim], 'r--', linewidth=2, label='Perfect normal')
    ax.set_xlabel('Theoretical Quantiles (Normal)')
    ax.set_ylabel('Observed Quantiles')
    ax.set_title('Q-Q Plot — Deviation from Normal')
    ax.legend()
    ax.set_xlim(-5, 5)
    ax.set_ylim(-8, 8)
    
    # 3. Intraday volatility
    ax = axes[1, 0]
    hours_sorted = sorted(hourly_vol.keys())
    vols = [hourly_vol[h] * 100 for h in hours_sorted]
    colors = ['#e74c3c' if v > np.mean(vols) else '#3498db' for v in vols]
    ax.bar(hours_sorted, vols, color=colors, edgecolor='white', linewidth=0.5)
    ax.axhline(np.mean(vols), color='gray', linestyle='--', alpha=0.7, label='Average')
    ax.set_xlabel('Hour (UTC)')
    ax.set_ylabel('Volatility (%)')
    ax.set_title('Intraday Volatility by Hour')
    ax.set_xticks(range(0, 24, 2))
    ax.legend()
    
    # 4. Empirical CDF vs Normal CDF
    ax = axes[1, 1]
    sorted_returns_pct = np.sort(returns * 100)
    empirical_cdf = np.arange(1, len(sorted_returns_pct) + 1) / len(sorted_returns_pct)
    ax.plot(sorted_returns_pct, empirical_cdf, 'b-', linewidth=2, label='Empirical CDF')
    x_norm = np.linspace(sorted_returns_pct[0], sorted_returns_pct[-1], 500)
    ax.plot(x_norm, stats.norm.cdf(x_norm, mean * 100, std * 100), 'r--', linewidth=2, label='Normal CDF')
    ax.set_xlabel('Return (%)')
    ax.set_ylabel('Cumulative Probability')
    ax.set_title('Empirical CDF vs Normal CDF')
    ax.legend()
    ax.axvline(0, color='gray', linestyle=':', alpha=0.5)
    ax.set_xlim(-1, 1)
    
    plt.tight_layout()
    
    plot_path = output_dir / "btc_15min_analysis.png"
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\n📊 Plots saved to {plot_path}")
    
    return plot_path


def analyze_near_strike(returns: np.ndarray, output_dir: Path):
    """
    Critical analysis: how does the distribution behave near zero return?
    This is where binary options decisions are made.
    """
    print("\n" + "="*70)
    print("🎯 NEAR-STRIKE ANALYSIS (Key for Binary Options)")
    print("="*70)
    
    std = np.std(returns)
    mean = np.mean(returns)
    
    # What fraction of candles close above vs below open?
    pct_up = np.mean(returns > 0) * 100
    pct_down = np.mean(returns < 0) * 100
    pct_flat = np.mean(returns == 0) * 100
    
    print(f"\nCandle direction:")
    print(f"  UP:   {pct_up:.1f}%")
    print(f"  DOWN: {pct_down:.1f}%")
    print(f"  FLAT: {pct_flat:.1f}%")
    
    # For small ranges around zero, how accurate is the normal CDF?
    print(f"\nAccuracy of Normal CDF near strike (current_return → P_up):")
    print(f"{'Current Return':>16} {'Normal P(up)':>14} {'Empirical P(up)':>16} {'Error':>8}")
    print("-"*60)
    
    test_returns = [-0.003, -0.002, -0.001, -0.0005, 0, 0.0005, 0.001, 0.002, 0.003]
    
    for r in test_returns:
        p_normal = stats.norm.cdf(r, loc=mean, scale=std)
        # Empirical: fraction of returns > 0 among candles that started with return ≈ r
        # Simplified: P(final_return > 0 | final_return distribution)
        p_empirical = np.mean(returns > r)
        error = (p_normal - p_empirical) * 100
        dollar = r * 70000
        print(f"  {r*100:>+8.3f}% (${dollar:>+5.0f})  {p_normal:>12.1%}  {p_empirical:>14.1%}  {error:>+6.1f}pp")
    
    # What edge does the distribution shape give us?
    print(f"\n  Normal model error at strike (0% return): ", end="")
    p_norm_at_zero = stats.norm.cdf(0, loc=mean, scale=std)
    p_emp_at_zero = np.mean(returns > 0)
    print(f"{abs(p_norm_at_zero - p_emp_at_zero)*100:.2f} percentage points")


# =============================================================================
# MAIN
# =============================================================================

async def main():
    output_dir = DATA_DIR / "analysis"
    output_dir.mkdir(exist_ok=True)
    
    # Step 1: Download data
    candles_1m = await download_binance_klines(days=30)
    
    # Step 2: Aggregate to 15-min
    candles_15m = aggregate_to_15min(candles_1m)
    returns = compute_returns(candles_15m, "log")
    
    print(f"\n📐 Aggregated: {len(candles_15m)} fifteen-minute candles")
    print(f"   Date range: {datetime.fromtimestamp(candles_15m[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(candles_15m[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d')}")
    
    # Step 3: Distribution analysis
    dist_stats = analyze_distribution(returns, output_dir)
    
    # Step 4: Intraday patterns
    hourly_vol = analyze_intraday_vol(candles_15m, returns, output_dir)
    
    # Step 5: Near-strike analysis
    analyze_near_strike(returns, output_dir)
    
    # Step 6: Generate plots
    plot_path = plot_all(returns, candles_15m, hourly_vol, output_dir)
    
    # Step 7: Final verdict
    print("\n" + "="*70)
    print("⚖️  VERDICT: IS BLACK-SCHOLES APPROPRIATE?")
    print("="*70)
    
    kurt = dist_stats["kurtosis"]
    skew = dist_stats["skew"]
    jb_p = dist_stats["jb_p"]
    
    print(f"\n  Excess Kurtosis: {kurt:.2f} ", end="")
    if kurt > 3:
        print("→ VERY fat tails (3x+ normal)")
    elif kurt > 1:
        print("→ Moderately fat tails")
    else:
        print("→ Near-normal tails")
    
    print(f"  Skewness: {skew:+.3f} ", end="")
    if abs(skew) > 0.5:
        print("→ Significantly asymmetric")
    elif abs(skew) > 0.1:
        print("→ Slightly asymmetric")
    else:
        print("→ Near-symmetric")
    
    print(f"  Normality (JB): p={jb_p:.6f} ", end="")
    if jb_p < 0.001:
        print("→ STRONGLY rejected")
    elif jb_p < 0.05:
        print("→ Rejected at 5%")
    else:
        print("→ Cannot reject")
    
    recommend = "EMPIRICAL CDF" if jb_p < 0.05 else "BLACK-SCHOLES (adequate)"
    print(f"\n  📌 Recommendation: Use {recommend}")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())

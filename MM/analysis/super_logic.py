import numpy as np
import pandas as pd
import math

# =============================================================================
# METRICS & SIGNAL GENERATION
# =============================================================================

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def get_market_regime(history_1m):
    """
    Analyze recent history (last 60-240 mins) to get Vol, Skew, Kurt, Drift.
    """
    closes = history_1m['close'].astype(float)
    log_rets = np.log(closes / closes.shift(1)).dropna()
    
    # annualized vol from 1m data? 
    # sigma_annual = std_1m * sqrt(525600)
    sigma_min = log_rets.std()
    sigma_annual = sigma_min * math.sqrt(365*24*60)
    
    # Drift (per minute)
    drift_min = log_rets.mean()
    
    # Skew / Kurtosis (Fisher)
    skew = log_rets.skew()
    kurt = log_rets.kurtosis()
    
    # RSI (Momentum)
    rsi = calculate_rsi(closes)
    
    return {
        "vol_annual": sigma_annual,
        "vol_min": sigma_min,
        "drift_min": drift_min,
        "skew": skew, 
        "kurt": kurt,
        "rsi": rsi
    }

# =============================================================================
# MODELS
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def gram_charlier_pricing(current_price, strike, time_left_sec, vol, skew, kurt):
    if time_left_sec <= 0: return 1.0 if current_price > strike else 0.0
    
    T_years = time_left_sec / (365 * 24 * 3600)
    sigma = vol if vol > 0.1 else 0.5 # Safety floor
    
    # Standard BS d2
    d2 = (math.log(current_price / strike) - 0.5 * sigma**2 * T_years) / (sigma * math.sqrt(T_years))
    
    # Standard Normal PDF/CDF
    phi = (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * d2**2)
    Phi = norm_cdf(d2)
    
    # GC Adjustment
    # Hermite Polynomials
    H2 = d2**2 - 1
    H3 = d2**3 - 3*d2
    
    # Skew/Kurt adjustment terms (Divided by 6 and 24 as per expansion)
    # Note: Skew/Kurt here are observable moments of returns.
    # The GC expansion often uses normalized coefficients.
    
    adj = phi * ((skew/6)*H2 + (kurt/24)*H3)
    
    prob = Phi - adj
    return max(0.001, min(0.999, prob))

def monte_carlo_simulation(current_price, strike, time_left_mins, vol_min, drift_min, num_sims=5000):
    # Time steps
    # We can do 1 big jump for prediction if we assume geometric brownian motion
    # S_T = S_0 * exp( (mu - 0.5*sigma^2)*T + sigma*sqrt(T)*Z )
    
    T = time_left_mins
    Z = np.random.normal(0, 1, num_sims)
    
    drift_term = (drift_min - 0.5 * vol_min**2) * T
    shock_term = vol_min * np.sqrt(T) * Z
    
    sim_prices = current_price * np.exp(drift_term + shock_term)
    
    wins = np.sum(sim_prices > strike)
    return wins / num_sims

# =============================================================================
# ENSEMBLE PREDICTOR
# =============================================================================

def get_super_prediction(current_price, open_price, strike_price, time_left_mins, history_1m):
    """
    Returns: { "prob": float, "signal": "UP"|"DOWN"|"SKIP", "details": dict }
    """
    # 1. Get Regime
    regime = get_market_regime(history_1m)
    
    # 2. Run Models
    prob_gc = gram_charlier_pricing(
        current_price, 
        strike_price, 
        time_left_mins * 60, 
        regime['vol_annual'], 
        regime['skew'], 
        regime['kurt']
    )
    
    prob_mc = monte_carlo_simulation(
        current_price,
        strike_price,
        time_left_mins,
        regime['vol_min'],
        regime['drift_min']
    )
    
    # 3. Weighted Consensus
    # Maybe 50/50? Or trust MC more for path dependency?
    # GC is better for "Theoretical Value". MC is better for "Trend Drift".
    weighted_prob = (prob_gc * 0.4) + (prob_mc * 0.6)
    
    # 4. RSI Filter (The "Smart" logic)
    # If RSI is Extreme (>70), Betting UP is dangerous even if model says so.
    # If RSI is Extreme (<30), Betting DOWN is dangerous.
    
    signal = "SKIP"
    
    # Direction
    prediction = "UP" if weighted_prob > 0.5 else "DOWN"
    confidence = abs(weighted_prob - 0.5) * 2 # 0.0 to 1.0 scale
    
    # Filter Logic
    is_safe = True
    
    if prediction == "UP" and regime['rsi'] > 75:
        is_safe = False # Overbought, don't long
    if prediction == "DOWN" and regime['rsi'] < 25:
        is_safe = False # Oversold, don't short
        
    if is_safe and confidence > 0.10: # >55% or <45%
        signal = prediction
        
    return {
        "final_prob": weighted_prob,
        "signal": signal,
        "confidence": confidence,
        "components": {
            "gc_prob": prob_gc,
            "mc_prob": prob_mc,
            "rsi": regime['rsi'],
            "skew": regime['skew'],
            "vol": regime['vol_annual']
        }
    }

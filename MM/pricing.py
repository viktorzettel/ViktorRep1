"""
Pricing Engine for Hourly "Up/Down" Crypto Markets

Logic:
    Probability(Price_Close > Price_Open)
    Model: Black-Scholes for Binary Call (Cash-or-Nothing)
    Strike K = Price_Open (Fixed at T=0)
    Reference S = Price_Current
    
    Formula:
        Prob = N(d2)
        d2 = (ln(S/K) + (r - 0.5*sigma^2)*T) / (sigma * sqrt(T))
        
        Assumptions:
        r = 0 (Interest rate negligible for 1h)
        Div = 0
"""

import math
from typing import Tuple

class CryptoHourlyPricer:
    
    @staticmethod
    def calculate_probability(
        current_price: float,
        open_price: float,
        time_remaining_seconds: float,
        volatility: float
    ) -> float:
        """
        Calculate the theoretical price (probability) of the "UP" (YES) token.
        
        Args:
            current_price: Live price from Binance.
            open_price: The 1H candle open price (Strike).
            time_remaining_seconds: Seconds until hour boundary.
            volatility: Annualized volatility (sigma).
            
        Returns:
            Float between 0.0 and 1.0 (Probability of closing > open).
        """
        if time_remaining_seconds <= 0:
            return 1.0 if current_price > open_price else 0.0
            
        if volatility <= 0:
            return 1.0 if current_price > open_price else 0.0
            
        # Convert T to Years for Black-Scholes
        T_years = time_remaining_seconds / (365 * 24 * 3600)
        
        S = current_price
        K = open_price
        sigma = volatility
        
        # d2 numerator: ln(S/K) - 0.5 * sigma^2 * T
        # (Assuming risk-free rate r = 0)
        numerator = math.log(S / K) - (0.5 * sigma**2 * T_years)
        denominator = sigma * math.sqrt(T_years)
        
        d2 = numerator / denominator
        
        # Cumulative Normal Distribution N(d2)
        return CryptoHourlyPricer._norm_cdf(d2)

    @staticmethod
    def calculate_gram_charlier_probability(
        current_price: float,
        open_price: float,
        time_remaining_seconds: float,
        volatility: float,
        skew: float,
        kurt: float
    ) -> float:
        """
        Calculate Gram-Charlier Series A pricing for Binary Call.
        Incorporates Skewness and Kurtosis of returns.
        
        Ref: Market Models (Alexander)
        """
        if time_remaining_seconds <= 0:
            return 1.0 if current_price > open_price else 0.0
            
        if volatility <= 0:
            return 1.0 if current_price > open_price else 0.0
            
        T_years = time_remaining_seconds / (365 * 24 * 3600)
        S = current_price
        K = open_price
        sigma = volatility
        
        # d2 calculation (assuming r=0)
        numerator = math.log(S / K) - (0.5 * sigma**2 * T_years)
        denominator = sigma * math.sqrt(T_years)
        d2 = numerator / denominator
        
        # Standard Normal PDF and CDF
        phi_d2 = (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * d2**2)
        Phi_d2 = CryptoHourlyPricer._norm_cdf(d2)
        
        # Gram-Charlier Adjustment
        # F(x) ~ N(x) - n(x)[ (skew/6)H2(x) + (kurt/24)H3(x) ]
        # H2(x) = x^2 - 1
        # H3(x) = x^3 - 3x
        
        H2 = d2**2 - 1
        H3 = d2**3 - 3*d2
        
        adjustment = phi_d2 * (
            (skew / 6.0) * H2 +
            (kurt / 24.0) * H3
        )
        
        fair_prob = Phi_d2 - adjustment
        
        # Clamp
        return max(0.0, min(1.0, fair_prob))

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Standard Normal Cumulative Distribution Function."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @staticmethod
    def get_time_remaining() -> float:
        """Calculate seconds remaining in current hour."""
        import time
        now = time.time()
        # Next hour boundary
        seconds_since_epoch = int(now)
        seconds_into_hour = seconds_since_epoch % 3600
        return 3600 - seconds_into_hour

    @staticmethod
    def get_implied_volatility(
        target_price: float,
        current_price: float,
        strike_price: float,
        time_remaining_seconds: float
    ) -> float:
        """
        Solve for Implied Volatility where ModelPrice(vol) ~= TargetPrice.
        Uses binary search.
        """
        # Bounds
        low = 0.01
        high = 5.0 # 500%
        
        # Check directionality for optimization
        # If ITM (Spot > Strike), Price goes DOWN as Vol goes UP.
        # If OTM (Spot < Strike), Price goes UP as Vol goes UP.
        is_itm = current_price > strike_price
        
        for _ in range(15): # ~15 iterations for sufficient precision
            mid = (low + high) / 2
            p = CryptoHourlyPricer.calculate_probability(current_price, strike_price, time_remaining_seconds, mid)
            
            if is_itm:
                # Price decreases with Vol
                if p < target_price:
                   high = mid # Need lower vol to get higher price
                else:
                   low = mid # Need higher vol to get lower price
            else:
                # Price increases with Vol
                if p < target_price:
                   low = mid # Need higher vol
                else:
                   high = mid # Need lower vol
                   
        return (low + high) / 2

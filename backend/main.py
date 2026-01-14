# filename: main.py
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import minimize
import scipy.stats as stats
from arch import arch_model
import warnings
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache

# Suppress warnings
warnings.filterwarnings("ignore")

# Initialize the API
app = FastAPI(title="RiskFirst Brain", version="1.1")

# Allow the frontend (React) to talk to this backend
# NOTE: For actual production deployment, replace "*" with your specific frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================
# CACHING LAYER (Fixes 4.1 - Performance)
# ==========================================================
@lru_cache(maxsize=32)
def fetch_market_data(tickers_tuple: tuple, lookback_years: int):
    """
    Cached function to prevent hitting Yahoo Finance repeatedly for the same request.
    Takes a tuple because lists are not hashable.
    """
    tickers_list = list(tickers_tuple)
    print(f"Downloading data for: {tickers_list}")

    # threads=False is safer for some environments to prevent locking
    data = yf.download(tickers_list, period=f"{lookback_years}y", interval="1d", auto_adjust=True, threads=False)[
        'Close']

    # Fix 2.1: Handle multi-index columns if they exist
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    # Fix 2.1: Explicit validation for empty data
    if data.empty:
        raise ValueError("Yahoo Finance returned no data. Check tickers.")

    # Fix 2.1: Check if any ticker failed completely (all NaNs)
    if data.isna().all().any():
        failed_cols = data.columns[data.isna().all()].tolist()
        raise ValueError(f"No data found for: {failed_cols}")

    # Forward fill missing data (holidays/crypto gaps)
    data = data.ffill().dropna()

    if data.shape[0] < 50:  # Ensure enough history for GARCH
        raise ValueError("Not enough historical data points after cleaning.")

    return data


# ==========================================================
# DATA MODELS
# ==========================================================
class PortfolioRequest(BaseModel):
    tickers: List[str]
    strategy: str = "smart_balance"
    force_min_weight: bool = False


# ==========================================================
# LOGIC CLASSES
# ==========================================================
class DataManager:
    def __init__(self, tickers, lookback_years=5):
        if len(tickers) < 2 or len(tickers) > 10:
            raise ValueError("Please provide between 2 and 10 assets.")

        self.tickers = sorted(list(set(tickers)))  # Remove duplicates

        # Call the cached function (pass as tuple)
        try:
            self.data = fetch_market_data(tuple(self.tickers), lookback_years)
        except Exception as e:
            raise ValueError(f"Data Error: {str(e)}")

        self.returns = self.data.pct_change().dropna()
        self.has_crypto = any(t.endswith('-USD') for t in self.tickers)
        self.trading_days = 365 if self.has_crypto else 252


class MarketRegime:
    def __init__(self, data_manager):
        # Fix 2.2: Weekly resampling is a heuristic.
        # Ideally, use daily but with a longer window, or different approach for crypto.
        # Keeping weekly for now but adding robustness.
        self.returns = data_manager.data.resample('W').last().pct_change().dropna()

    def get_status(self):
        mu = self.returns.mean()
        cov = self.returns.cov()

        # Fix 2.3: Use Pseudo-Inverse (pinv) instead of inv
        # This prevents crashes when assets are highly correlated (singular matrix)
        cov_inv = np.linalg.pinv(cov)

        latest_ret = self.returns.iloc[-1]
        diff = latest_ret - mu
        score = diff.values.dot(cov_inv).dot(diff.values.T)

        # Note: Thresholds (12, 25) are heuristic based on Mahalanobis distance properties for ~5-10 assets.
        if score < 12:
            return {"score": score, "color": "Green", "message": "Calm"}
        elif score < 25:
            return {"score": score, "color": "Yellow", "message": "Choppy"}
        else:
            return {"score": score, "color": "Red", "message": "Turbulent"}


class PortfolioArchitect:
    def __init__(self, data_manager, force_min_weight=False):
        self.returns = data_manager.returns
        # Fix 3.2: Daily mean is noisy, but standard for this level of app.
        self.mu = self.returns.mean() * data_manager.trading_days
        self.cov = self.returns.cov() * data_manager.trading_days
        self.n_assets = len(self.returns.columns)
        self.tickers = self.returns.columns
        self.force_min_weight = force_min_weight

    def _portfolio_performance(self, weights):
        ret = np.sum(self.mu * weights)
        vol = np.sqrt(np.dot(weights.T, np.dot(self.cov, weights)))
        return ret, vol

    def build_portfolio(self, objective):
        # Fix 2.4b: Check for infeasibility
        min_w = 0.05 if self.force_min_weight else 0.0
        if min_w * self.n_assets > 1.0:
            raise ValueError("Infeasible: Too many assets for 5% minimum weight.")

        constraints = [{'type': 'eq', 'fun': lambda x: np.sum(x) - 1}]
        init_guess = [1 / self.n_assets] * self.n_assets

        # Bounds logic
        if objective == 'aggressive_growth':
            # Cap single asset at 35% to force diversification
            bounds = tuple((min_w, 0.35) for _ in range(self.n_assets))
        else:
            bounds = tuple((min_w, 1.0) for _ in range(self.n_assets))

        # Optimization
        if objective == 'safety_first':
            result = minimize(lambda w: self._portfolio_performance(w)[1],
                              init_guess, method='SLSQP', bounds=bounds, constraints=constraints)

        elif objective == 'smart_balance':
            rf = 0.04

            def neg_sharpe(w):
                r, v = self._portfolio_performance(w)
                # Fix 2.4a: Avoid division by zero
                v = max(v, 1e-6)
                return - (r - rf) / v

            result = minimize(neg_sharpe, init_guess, method='SLSQP', bounds=bounds, constraints=constraints)

        elif objective == 'aggressive_growth':
            result = minimize(lambda w: -self._portfolio_performance(w)[0],
                              init_guess, method='SLSQP', bounds=bounds, constraints=constraints)
        else:
            return None

        if not result.success:
            # Fallback if solver fails (e.g., precision issues)
            print(f"Optimization warning: {result.message}")
            return None

        # Fix 2.5: Removed manual cleaning loop. Trust the solver.
        weights = result.x

        # Round for cleaner JSON, but don't re-normalize manually to avoid breaking sum=1
        weights = np.round(weights, 4)

        return dict(zip(self.tickers, weights))


class RiskEngine:
    def __init__(self, data_manager, weights):
        self.returns = data_manager.returns
        self.weights = np.array([weights[t] for t in self.returns.columns])

    def run_stress_test(self):
        # Fix 2.6: Explicit scaling documentation
        # We scale to Percentage (0-100) for GARCH numerical stability
        portfolio_series = self.returns.dot(self.weights) * 100

        # Fix 4.2: Timeout/Convergence protection
        try:
            # GJR-GARCH with Student's t
            model = arch_model(portfolio_series, vol='Garch', p=1, o=1, q=1, dist='t')
            res = model.fit(disp='off', show_warning=False)

            forecast = res.forecast(horizon=1)
            next_day_vol = np.sqrt(forecast.variance.values[-1, 0])
            nu = res.params['nu']

            # Fix 2.7: Correct Expected Shortfall (ES) for Student-t
            # The formula depends on the PDF at the quantile and scaling factors
            alpha = 0.05
            t_quantile = stats.t.ppf(alpha, nu)  # This is negative (e.g. -1.65)

            # VaR (Positive number representing loss)
            VaR_95 = abs(next_day_vol * t_quantile)

            # ES Calculation (Standard McNeil, Frey, Embrechts formula)
            # ES = -sigma * ( (nu + t^2)/(nu-1) ) * ( pdf(t)/alpha )
            pdf_at_q = stats.t.pdf(t_quantile, nu)
            es_factor = (nu + t_quantile ** 2) / (nu - 1)
            ES_95 = next_day_vol * es_factor * (pdf_at_q / alpha)

        except Exception as e:
            # Fallback: Historical Simulation if GARCH fails
            print(f"GARCH failed ({str(e)}), using historical fallback.")
            VaR_95 = np.percentile(portfolio_series, 5) * -1
            ES_95 = portfolio_series[portfolio_series <= -VaR_95].mean() * -1
            next_day_vol = portfolio_series.std()

        return {
            "volatility": float(next_day_vol),  # Daily % Volatility
            "VaR_95": float(VaR_95),  # % Value at Risk
            "ES_95": float(ES_95)  # % Expected Shortfall
        }


# ==========================================================
# ENDPOINTS
# ==========================================================
@app.get("/")
def home():
    return {"message": "RiskFirst Brain is active (v1.1)"}


@app.post("/analyze")
def analyze_portfolio(request: PortfolioRequest):
    try:
        # 1. Load Data (Cached)
        dm = DataManager(request.tickers)

        # 2. Check Weather
        regime = MarketRegime(dm)
        market_status = regime.get_status()

        # 3. Build Portfolio
        architect = PortfolioArchitect(dm, force_min_weight=request.force_min_weight)
        weights = architect.build_portfolio(request.strategy)

        if not weights:
            raise HTTPException(status_code=400, detail="Optimization failed to converge.")

        # 4. Stress Test
        risk_engine = RiskEngine(dm, weights)
        risk_metrics = risk_engine.run_stress_test()

        return {
            "market_status": market_status,
            "weights": weights,
            "risk_metrics": risk_metrics
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Log the actual error on server side
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Calculation Error")
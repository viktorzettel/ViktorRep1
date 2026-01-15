# filename: main.py
# RiskLens Backend v2.1 - Type Safe & Production Ready
import numpy as np
import pandas as pd
import yfinance as yf
import scipy.stats as stats
from arch import arch_model
import riskfolio as rp
import warnings
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache

# Suppress warnings
warnings.filterwarnings("ignore")

# Initialize the API
app = FastAPI(title="RiskLens Brain", version="2.1")

# Allow the frontend (React) to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================
# CACHING LAYER
# ==========================================================
@lru_cache(maxsize=32)
def fetch_market_data(tickers_tuple: tuple, lookback_years: int):
    """
    Cached function to prevent hitting Yahoo Finance repeatedly for the same request.
    """
    tickers_list = list(tickers_tuple)
    print(f"Downloading data for: {tickers_list}")

    data = yf.download(tickers_list, period=f"{lookback_years}y", interval="1d", auto_adjust=True, threads=False)[
        'Close']

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    if data.empty:
        raise ValueError("Yahoo Finance returned no data. Check tickers.")

    if data.isna().all().any():
        failed_cols = data.columns[data.isna().all()].tolist()
        raise ValueError(f"No data found for: {failed_cols}")

    data = data.ffill().dropna()

    if data.shape[0] < 50:
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

        self.tickers = sorted(list(set(tickers)))

        try:
            self.data = fetch_market_data(tuple(self.tickers), lookback_years)
        except Exception as e:
            raise ValueError(f"Data Error: {str(e)}")

        self.returns = self.data.pct_change().dropna()
        self.has_crypto = any(t.endswith('-USD') for t in self.tickers)
        self.trading_days = 365 if self.has_crypto else 252


class MarketRegime:
    def __init__(self, data_manager):
        self.returns = data_manager.data.resample('W').last().pct_change().dropna()

    def get_status(self):
        mu = self.returns.mean()
        cov = self.returns.cov()
        cov_inv = np.linalg.pinv(cov)

        latest_ret = self.returns.iloc[-1]
        diff = latest_ret - mu
        score = diff.values.dot(cov_inv).dot(diff.values.T)
        
        # TYPE SAFETY: Explicitly convert to Python float
        score_val = float(score)

        if score_val < 12:
            return {"score": round(score_val, 2), "color": "Green", "message": "Calm"}
        elif score_val < 25:
            return {"score": round(score_val, 2), "color": "Yellow", "message": "Choppy"}
        else:
            return {"score": round(score_val, 2), "color": "Red", "message": "Turbulent"}


class PortfolioArchitect:
    """
    Institutional-grade portfolio optimization using Riskfolio-Lib.
    Strategies:
    - Safety First: Hierarchical Risk Parity (HRP) - Pure risk management.
    - Smart Balance: Nested Clustered Optimization (NCO) - Max Sharpe with clustering.
    - Aggressive: Nested Clustered Optimization (NCO) - Max Return with clustering.
    """
    def __init__(self, data_manager, force_min_weight=False):
        self.returns = data_manager.returns
        self.n_assets = len(self.returns.columns)
        self.tickers = list(self.returns.columns)
        self.force_min_weight = force_min_weight
        self.cluster_order = None 

    def build_portfolio(self, objective):
        try:
            # Create HRP/NCO Portfolio Object
            port = rp.HCPortfolio(returns=self.returns)
            
            # --- STRATEGY SELECTION ---
            if objective == 'safety_first':
                # HRP is excellent for safety because it ignores returns and focuses on
                # de-correlating the portfolio.
                model = 'HRP'
                obj = 'MinRisk' # Not used by HRP but kept for consistency
                rm = 'CVaR'     # Conditional Value at Risk (Safety focus)
                codependence = 'pearson'
                
            elif objective == 'smart_balance':
                # NCO (Nested Clustered Optimization) is better here.
                # It clusters assets first, then optimizes for Sharpe *within* clusters.
                # This prevents the "98% SHY" problem while still being robust.
                model = 'NCO'
                obj = 'Sharpe'  # Maximize Sharpe Ratio
                rm = 'MV'       # Standard Variance
                codependence = 'pearson'

            elif objective == 'aggressive_growth':
                # NCO optimized for Maximum Return
                model = 'NCO'
                obj = 'MaxRet'  # Maximize Returns
                rm = 'MV'
                codependence = 'pearson'
            
            else:
                # Default fallback
                model = 'HRP'
                obj = 'MinRisk'
                rm = 'MV'
                codependence = 'pearson'

            # --- OPTIMIZATION ---
            # rf=0.04 (4% risk free rate for Sharpe calc)
            weights = port.optimization(
                model=model,
                rm=rm,
                obj=obj,
                codependence=codependence,
                rf=0.04,
                linkage='ward',
                leaf_order=True
            )

            if weights is None or weights.empty:
                raise ValueError("Optimization returned empty weights")

            weights_dict = weights.iloc[:, 0].to_dict()
            
            # --- CONSTRAINTS (Min/Max Logic) ---
            # 1. Force Minimum Weight (5%)
            if self.force_min_weight:
                min_w = 0.05
                below_min = {k: v for k, v in weights_dict.items() if v < min_w}
                above_min = {k: v for k, v in weights_dict.items() if v >= min_w}
                
                if below_min:
                    deficit = sum(min_w - v for v in below_min.values())
                    surplus = sum(v - min_w for v in above_min.values())
                    if surplus > 0: # Avoid div/0
                        for k in below_min: weights_dict[k] = min_w
                        reduction_factor = deficit / surplus
                        for k in above_min: weights_dict[k] -= (weights_dict[k] - min_w) * reduction_factor

            # 2. Cap Max Weight (Aggressive only)
            if objective == 'aggressive_growth':
                max_w = 0.35
                excess = sum(max(0, v - max_w) for v in weights_dict.values())
                if excess > 0:
                    for k in weights_dict:
                        if weights_dict[k] > max_w: weights_dict[k] = max_w
                    
                    below_max = {k: v for k, v in weights_dict.items() if v < max_w}
                    if below_max:
                        total_below = sum(below_max.values())
                        if total_below > 0:
                            for k in below_max: weights_dict[k] += excess * (weights_dict[k] / total_below)

            # --- FINAL CLEANUP ---
            total = sum(weights_dict.values())
            # TYPE SAFETY: Cast to float
            weights_dict = {str(k): round(float(v / total), 4) for k, v in weights_dict.items()}

            # Extract Cluster Order
            try:
                if hasattr(port, 'sort_order') and port.sort_order is not None:
                    raw_order = list(port.sort_order)
                    self.cluster_order = [str(item) for item in raw_order]
                else:
                    self.cluster_order = self.tickers
            except Exception:
                self.cluster_order = self.tickers

            return weights_dict

        except Exception as e:
            print(f"Optimization failed ({str(e)}), falling back to Simple Mean-Variance.")
            return self._build_mean_variance(objective)

    def _build_mean_variance(self, objective):
        # Fallback to standard optimization if clustering fails
        port = rp.Portfolio(returns=self.returns)
        port.assets_stats(method_mu='hist', method_cov='hist')

        min_w = 0.05 if self.force_min_weight else 0.0
        max_w = 0.35 if objective == 'aggressive_growth' else 1.0

        port.lowerret = None
        port.upperlng = max_w
        port.lowerlng = min_w

        if objective == 'safety_first':
            obj = 'MinRisk'; rm = 'CVaR'
        elif objective == 'aggressive_growth':
            obj = 'MaxRet'; rm = 'MV'
        else:
            obj = 'Sharpe'; rm = 'MV'

        weights = port.optimization(model='Classic', rm=rm, obj=obj, rf=0.04)
        
        if weights is None or weights.empty:
            raise ValueError("Mean-Variance optimization failed")

        weights_dict = weights.iloc[:, 0].to_dict()
        total = sum(weights_dict.values())
        weights_dict = {str(k): round(float(v / total), 4) for k, v in weights_dict.items()}
        self.cluster_order = self.tickers
        return weights_dict


class RiskEngine:
    def __init__(self, data_manager, weights):
        self.returns = data_manager.returns
        self.weights = np.array([weights[t] for t in self.returns.columns])
        self.weights_dict = weights
        self.cov = self.returns.cov() * data_manager.trading_days

    def calculate_diversification_ratio(self):
        individual_vols = np.sqrt(np.diag(self.cov))
        weighted_sum_vols = np.sum(self.weights * individual_vols)
        portfolio_vol = np.sqrt(np.dot(self.weights.T, np.dot(self.cov, self.weights)))
        
        if portfolio_vol > 0:
            # TYPE SAFETY: Cast numpy result to python float
            return round(float(weighted_sum_vols / portfolio_vol), 2)
        return 1.0

    def calculate_risk_contribution(self):
        portfolio_vol = np.sqrt(np.dot(self.weights.T, np.dot(self.cov, self.weights)))
        
        if portfolio_vol == 0:
            return {t: round(1.0 / len(self.weights_dict), 4) for t in self.weights_dict}

        marginal = np.dot(self.cov, self.weights) / portfolio_vol
        risk_contrib = self.weights * marginal
        total_contrib = np.sum(risk_contrib)
        
        if total_contrib > 0:
            risk_contrib_pct = risk_contrib / total_contrib
        else:
            risk_contrib_pct = np.ones(len(self.weights)) / len(self.weights)

        # TYPE SAFETY: Cast each value to float
        return {str(t): round(float(rc), 4) for t, rc in zip(self.weights_dict.keys(), risk_contrib_pct)}

    def run_stress_test(self):
        portfolio_series = self.returns.dot(self.weights) * 100

        try:
            model = arch_model(portfolio_series, vol='Garch', p=1, o=1, q=1, dist='t')
            res = model.fit(disp='off', show_warning=False)
            forecast = res.forecast(horizon=1)
            next_day_vol = np.sqrt(forecast.variance.values[-1, 0])
            nu = res.params['nu']
            alpha = 0.05
            t_quantile = stats.t.ppf(alpha, nu)
            VaR_95 = abs(next_day_vol * t_quantile)
            pdf_at_q = stats.t.pdf(t_quantile, nu)
            es_factor = (nu + t_quantile ** 2) / (nu - 1)
            ES_95 = next_day_vol * es_factor * (pdf_at_q / alpha)

        except Exception as e:
            print(f"GARCH failed ({str(e)}), using historical fallback.")
            VaR_95 = abs(np.percentile(portfolio_series, 5))
            tail_losses = portfolio_series[portfolio_series <= -VaR_95]
            ES_95 = abs(tail_losses.mean()) if len(tail_losses) > 0 else VaR_95
            next_day_vol = portfolio_series.std()

        diversification_ratio = self.calculate_diversification_ratio()
        risk_contribution = self.calculate_risk_contribution()

        return {
            "volatility": round(float(next_day_vol), 2),
            "VaR_95": round(float(VaR_95), 2),
            "ES_95": round(float(ES_95), 2),
            "diversification_ratio": diversification_ratio,
            "risk_contribution": risk_contribution
        }


# ==========================================================
# ENDPOINTS
# ==========================================================
@app.get("/")
def home():
    return {"message": "RiskLens Brain is active (v2.1 - Type Safe)"}

@app.post("/analyze")
def analyze_portfolio(request: PortfolioRequest):
    try:
        dm = DataManager(request.tickers)
        regime = MarketRegime(dm)
        market_status = regime.get_status()

        architect = PortfolioArchitect(dm, force_min_weight=request.force_min_weight)
        weights = architect.build_portfolio(request.strategy)

        if not weights:
            raise HTTPException(status_code=400, detail="Optimization failed.")

        risk_engine = RiskEngine(dm, weights)
        risk_metrics = risk_engine.run_stress_test()

        # TYPE SAFETY: Handle Cluster Order
        raw_cluster_order = architect.cluster_order or list(weights.keys())
        cluster_order = [str(x) for x in raw_cluster_order]

        # TYPE SAFETY: Handle Correlation Matrix
        corr_matrix = dm.returns.corr().round(4)
        # Convert dataframe values to a pure list of floats (no numpy types)
        corr_values = [[float(x) for x in row] for row in corr_matrix.values]
        
        correlation_data = {
            "labels": [str(x) for x in corr_matrix.columns],
            "values": corr_values
        }

        return {
            "market_status": market_status,
            "weights": weights,
            "risk_metrics": risk_metrics,
            "cluster_order": cluster_order,
            "correlation_matrix": correlation_data
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Log the full error to the console for debugging
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Calculation Error: {str(e)}")

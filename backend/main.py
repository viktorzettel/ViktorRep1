# filename: main.py
# RiskLens Backend v2.0 - Institutional Grade with Riskfolio-Lib
import numpy as np
import pandas as pd
import yfinance as yf
import scipy.stats as stats
from arch import arch_model
import riskfolio as rp
import warnings
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache

# Suppress warnings
warnings.filterwarnings("ignore")

# Initialize the API
app = FastAPI(title="RiskLens Brain", version="2.0")

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
    """
    Market regime detection using Mahalanobis distance.
    Future upgrade: Hidden Markov Model (HMM) for regime switching.
    """

    def __init__(self, data_manager):
        self.returns = data_manager.data.resample('W').last().pct_change().dropna()

    def get_status(self):
        mu = self.returns.mean()
        cov = self.returns.cov()
        cov_inv = np.linalg.pinv(cov)

        latest_ret = self.returns.iloc[-1]
        diff = latest_ret - mu
        score = diff.values.dot(cov_inv).dot(diff.values.T)

        if score < 12:
            return {"score": round(float(score), 2), "color": "Green", "message": "Calm"}
        elif score < 25:
            return {"score": round(float(score), 2), "color": "Yellow", "message": "Choppy"}
        else:
            return {"score": round(float(score), 2), "color": "Red", "message": "Turbulent"}


class PortfolioArchitect:
    """
    Institutional-grade portfolio optimization using Riskfolio-Lib.
    Primary method: Hierarchical Risk Parity (HRP)
    Fallback: Mean-Variance Optimization
    """

    def __init__(self, data_manager, force_min_weight=False):
        self.returns = data_manager.returns
        self.n_assets = len(self.returns.columns)
        self.tickers = list(self.returns.columns)
        self.force_min_weight = force_min_weight
        self.cluster_order = None  # Will be populated by HRP

    def build_portfolio(self, objective):
        """
        Build portfolio using Hierarchical Risk Parity (HRP).
        Falls back to Mean-Variance if HRP fails.
        """
        try:
            weights, cluster_order = self._build_hrp(objective)
            self.cluster_order = cluster_order
            return weights
        except Exception as e:
            print(f"HRP failed ({str(e)}), falling back to Mean-Variance.")
            return self._build_mean_variance(objective)

    def _build_hrp(self, objective):
        """
        Hierarchical Risk Parity using Riskfolio-Lib.
        HRP doesn't directly optimize for returns, but we can adjust risk measures.
        """
        # Create portfolio object
        port = rp.HCPortfolio(returns=self.returns)

        # Risk measure mapping based on strategy
        # MV = Variance, CVaR = Conditional VaR, CDaR = Conditional Drawdown
        rm_map = {
            'safety_first': 'CVaR',  # Focus on tail risk
            'smart_balance': 'MV',  # Standard variance
            'aggressive_growth': 'MV'  # Still use variance but linkage differs
        }
        rm = rm_map.get(objective, 'MV')

        # Linkage method affects clustering
        # 'ward' = minimize variance within clusters (conservative)
        # 'single' = minimum distance (aggressive)
        linkage_map = {
            'safety_first': 'ward',
            'smart_balance': 'ward',
            'aggressive_growth': 'single'
        }
        linkage = linkage_map.get(objective, 'ward')

        # Optimize using HRP
        weights = port.optimization(
            model='HRP',
            codependence='pearson',
            rm=rm,
            rf=0.04,  # Risk-free rate
            linkage=linkage,
            leaf_order=True
        )

        if weights is None or weights.empty:
            raise ValueError("HRP returned empty weights")

        # Apply minimum weight constraint if requested
        # Use iloc for safer column access (Riskfolio column names can vary)
        weights_dict = weights.iloc[:, 0].to_dict()

        if self.force_min_weight:
            min_w = 0.05
            # Redistribute from assets below minimum
            below_min = {k: v for k, v in weights_dict.items() if v < min_w}
            above_min = {k: v for k, v in weights_dict.items() if v >= min_w}

            if below_min:
                deficit = sum(min_w - v for v in below_min.values())
                surplus = sum(v - min_w for v in above_min.values())

                if surplus >= deficit:
                    # Set below-min to minimum, reduce above-min proportionally
                    for k in below_min:
                        weights_dict[k] = min_w
                    reduction_factor = deficit / surplus if surplus > 0 else 0
                    for k in above_min:
                        weights_dict[k] -= (weights_dict[k] - min_w) * reduction_factor

        # Cap at 35% for aggressive strategy diversification
        if objective == 'aggressive_growth':
            max_w = 0.35
            excess = sum(max(0, v - max_w) for v in weights_dict.values())
            if excess > 0:
                for k in weights_dict:
                    if weights_dict[k] > max_w:
                        weights_dict[k] = max_w
                # Redistribute excess proportionally to those below max
                below_max = {k: v for k, v in weights_dict.items() if v < max_w}
                if below_max:
                    total_below = sum(below_max.values())
                    if total_below > 0:  # Guard against division by zero
                        for k in below_max:
                            weights_dict[k] += excess * (weights_dict[k] / total_below)

        # Normalize to sum to 1
        total = sum(weights_dict.values())
        weights_dict = {k: round(v / total, 4) for k, v in weights_dict.items()}

        # Get cluster order from the dendrogram (handle different Riskfolio versions)
        try:
            cluster_order = list(port.sort_order) if hasattr(port,
                                                             'sort_order') and port.sort_order is not None else self.tickers
        except Exception:
            cluster_order = self.tickers

        return weights_dict, cluster_order

    def _build_mean_variance(self, objective):
        """
        Fallback: Mean-Variance Optimization using Riskfolio-Lib.
        """
        port = rp.Portfolio(returns=self.returns)

        # Calculate expected returns and covariance
        port.assets_stats(method_mu='hist', method_cov='hist')

        # Set constraints
        min_w = 0.05 if self.force_min_weight else 0.0
        max_w = 0.35 if objective == 'aggressive_growth' else 1.0

        port.lowerret = None
        port.upperlng = max_w
        port.lowerlng = min_w

        # Optimization objective
        rm_map = {
            'safety_first': 'MV',  # Minimize variance
            'smart_balance': 'MV',  # Max Sharpe (default)
            'aggressive_growth': 'MV'
        }
        rm = rm_map.get(objective, 'MV')

        obj_map = {
            'safety_first': 'MinRisk',
            'smart_balance': 'Sharpe',
            'aggressive_growth': 'MaxRet'
        }
        obj = obj_map.get(objective, 'Sharpe')

        weights = port.optimization(model='Classic', rm=rm, obj=obj, rf=0.04)

        if weights is None or weights.empty:
            raise ValueError("Mean-Variance optimization failed")

        # Use iloc for safer column access
        weights_dict = weights.iloc[:, 0].to_dict()

        # Normalize and round
        total = sum(weights_dict.values())
        weights_dict = {k: round(v / total, 4) for k, v in weights_dict.items()}

        self.cluster_order = self.tickers  # No clustering in MV fallback

        return weights_dict


class RiskEngine:
    """
    Risk analytics engine using GARCH for volatility forecasting.
    Now includes Diversification Ratio and Risk Contribution metrics.
    """

    def __init__(self, data_manager, weights):
        self.returns = data_manager.returns
        self.weights = np.array([weights[t] for t in self.returns.columns])
        self.weights_dict = weights
        self.cov = self.returns.cov() * data_manager.trading_days

    def calculate_diversification_ratio(self):
        """
        Diversification Ratio = Sum of weighted individual volatilities / Portfolio volatility
        A ratio > 1 indicates diversification benefit.
        Higher = better hedged portfolio.
        """
        individual_vols = np.sqrt(np.diag(self.cov))
        weighted_sum_vols = np.sum(self.weights * individual_vols)
        portfolio_vol = np.sqrt(np.dot(self.weights.T, np.dot(self.cov, self.weights)))

        if portfolio_vol > 0:
            return round(weighted_sum_vols / portfolio_vol, 2)
        return 1.0

    def calculate_risk_contribution(self):
        """
        Calculate the marginal risk contribution of each asset.
        Returns percentage contribution to total portfolio risk.
        """
        portfolio_vol = np.sqrt(np.dot(self.weights.T, np.dot(self.cov, self.weights)))

        if portfolio_vol == 0:
            return {t: round(1.0 / len(self.weights_dict), 4) for t in self.weights_dict}

        # Marginal contribution = (Cov * w) / portfolio_vol
        marginal = np.dot(self.cov, self.weights) / portfolio_vol

        # Risk contribution = w * marginal_contribution
        risk_contrib = self.weights * marginal

        # Normalize to percentage
        total_contrib = np.sum(risk_contrib)
        if total_contrib > 0:
            risk_contrib_pct = risk_contrib / total_contrib
        else:
            risk_contrib_pct = np.ones(len(self.weights)) / len(self.weights)

        return {t: round(float(rc), 4) for t, rc in zip(self.weights_dict.keys(), risk_contrib_pct)}

    def run_stress_test(self):
        """
        GARCH-based volatility forecasting with VaR and Expected Shortfall.
        """
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

        # Calculate additional metrics
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
    return {"message": "RiskLens Brain is active (v2.0 - Institutional Grade)"}


@app.post("/analyze")
def analyze_portfolio(request: PortfolioRequest):
    try:
        # 1. Load Data (Cached)
        dm = DataManager(request.tickers)

        # 2. Check Market Regime
        regime = MarketRegime(dm)
        market_status = regime.get_status()

        # 3. Build Portfolio using HRP
        architect = PortfolioArchitect(dm, force_min_weight=request.force_min_weight)
        weights = architect.build_portfolio(request.strategy)

        if not weights:
            raise HTTPException(status_code=400, detail="Optimization failed to converge.")

        # 4. Risk Analysis
        risk_engine = RiskEngine(dm, weights)
        risk_metrics = risk_engine.run_stress_test()

        # 5. Get cluster order for frontend visualization
        cluster_order = architect.cluster_order or list(weights.keys())

        # 6. Correlation Matrix (Added for Heatmap)
        # We use round(4) to keep JSON size small
        corr_matrix = dm.returns.corr().round(4)

        # Format for Frontend: { "labels": ["BTC", "NVDA"], "values": [[1.0, 0.5], [0.5, 1.0]] }
        correlation_data = {
            "labels": list(corr_matrix.columns),
            "values": corr_matrix.values.tolist()
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
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Calculation Error")
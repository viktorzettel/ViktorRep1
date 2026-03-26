# RiskLens Backend v2.1

**Institutional-Grade Portfolio Optimization API**

A FastAPI backend that provides portfolio optimization and risk analysis using quantitative finance methods.

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 10000
```

---

## 📊 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/analyze` | POST | Portfolio optimization & risk analysis |

### Request Body (`/analyze`)
```json
{
  "tickers": ["AAPL", "NVDA", "BTC-USD"],
  "strategy": "smart_balance",
  "force_min_weight": false
}
```

### Strategies
| Strategy | Description |
|----------|-------------|
| `safety_first` | Minimizes tail risk using CVaR |
| `smart_balance` | Maximizes Sharpe ratio |
| `aggressive_growth` | Maximizes returns with position caps |

---

## 🧮 Methods & Algorithms

### Portfolio Optimization

| Method | Library | Description |
|--------|---------|-------------|
| **HRP** (Hierarchical Risk Parity) | Riskfolio-Lib | Primary for `safety_first` - builds portfolios using hierarchical clustering, no covariance inversion needed |
| **NCO** (Nested Clustered Optimization) | Riskfolio-Lib | Primary for `smart_balance` / `aggressive` - combines clustering with mean-variance within clusters |
| **Mean-Variance** | Riskfolio-Lib | Fallback method - classic Markowitz optimization |

### Risk Metrics

| Metric | Method | Description |
|--------|--------|-------------|
| **Volatility** | GARCH(1,1,1) | Forecasted volatility using ARCH library with Student-t distribution |
| **VaR (95%)** | GARCH + t-distribution | Value at Risk - max expected loss at 95% confidence |
| **ES (95%)** | GARCH + t-distribution | Expected Shortfall - average loss beyond VaR |
| **Diversification Ratio** | Analytical | Weighted sum of individual vols / portfolio vol |

### Market Regime Detection

| Method | Description |
|--------|-------------|
| **Mahalanobis Distance** | Measures how "unusual" current returns are relative to historical distribution |

| Score | Regime | Color |
|-------|--------|-------|
| < 12 | Calm | 🟢 Green |
| 12-25 | Choppy | 🟡 Yellow |
| > 25 | Turbulent | 🔴 Red |

---

## 📦 Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | REST API framework |
| `riskfolio-lib` | Portfolio optimization (HRP, NCO, MV) |
| `yfinance` | Market data fetching |
| `arch` | GARCH volatility modeling |
| `scipy` | Statistical computations |
| `numpy` / `pandas` | Data manipulation |

---

## 🏗️ Architecture

```
Request → DataManager → MarketRegime → PortfolioArchitect → RiskEngine → Response
            ↓              ↓                   ↓                ↓
         yfinance      Mahalanobis        HRP/NCO/MV         GARCH
```

### Core Classes

| Class | Responsibility |
|-------|----------------|
| `DataManager` | Fetches & caches market data, calculates returns |
| `MarketRegime` | Detects market conditions using Mahalanobis distance |
| `PortfolioArchitect` | Optimizes portfolio weights with cascading fallback |
| `RiskEngine` | Calculates risk metrics (VaR, ES, volatility) |

---

## 🔧 Configuration

- **Lookback Period**: 5 years of historical data
- **Risk-Free Rate**: 4% (0.04)
- **Trading Days**: 252 (stocks) / 365 (crypto)
- **Max Assets**: 10
- **Position Caps**: 35% max for aggressive strategy

---

## 📝 Response Format

```json
{
  "market_status": { "score": 8.5, "color": "Green", "message": "Calm" },
  "weights": { "AAPL": 0.35, "NVDA": 0.40, "BTC-USD": 0.25 },
  "risk_metrics": {
    "volatility": 1.82,
    "VaR_95": 2.95,
    "ES_95": 3.72,
    "diversification_ratio": 1.24
  },
  "cluster_order": ["AAPL", "NVDA", "BTC-USD"],
  "correlation_matrix": { "labels": [...], "values": [[...]] }
}
```

---

## 📚 References

- [Hierarchical Risk Parity (López de Prado, 2016)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678)
- [Riskfolio-Lib Documentation](https://riskfolio-lib.readthedocs.io/)
- [GARCH Models (Bollerslev, 1986)](https://en.wikipedia.org/wiki/GARCH)

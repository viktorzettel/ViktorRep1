import { useState, useEffect, useRef } from 'react';
import './App.css';
import TickerInput from './components/TickerInput';
import StrategySelector from './components/StrategySelector';
import AllocationToggle from './components/AllocationToggle';
import AnalyzeButton from './components/AnalyzeButton';
import MarketWeather from './components/MarketWeather';
import PortfolioChart from './components/PortfolioChart';
import RiskCard from './components/RiskCard';
import CorrelationHeatmap from './components/CorrelationHeatmap';

const API_URL = 'https://risklens-api-r8yc.onrender.com';

// Map frontend strategy IDs to backend values
const strategyMap = {
  safety: 'safety_first',
  balanced: 'smart_balance',
  aggressive: 'aggressive_growth',
};

// Transform backend response to frontend format
const transformResponse = (backendData) => {
  const portfolioData = Object.entries(backendData.weights).map(([name, weight]) => ({
    name,
    value: Math.round(weight * 100),
  }));

  // Round all metrics to 2 decimal places
  const roundTo2 = (num) => Math.round(num * 100) / 100;

  return {
    marketStatus: {
      color: backendData.market_status.color.toLowerCase(),
      score: roundTo2(backendData.market_status.score),
      message: backendData.market_status.message,
    },
    portfolioData,
    riskMetrics: {
      volatility: roundTo2(backendData.risk_metrics.volatility),
      var: roundTo2(backendData.risk_metrics.VaR_95),
      es: roundTo2(backendData.risk_metrics.ES_95),
      diversificationRatio: backendData.risk_metrics.diversification_ratio,
    },
    correlationMatrix: backendData.correlation_matrix || null,
    clusterOrder: backendData.cluster_order || [],
  };
};

function App() {
  const [tickers, setTickers] = useState([]);
  const [strategy, setStrategy] = useState('balanced');
  const [forceMinAllocation, setForceMinAllocation] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [showWakeUpNotice, setShowWakeUpNotice] = useState(false);
  const wakeUpTimerRef = useRef(null);

  const canAnalyze = tickers.length >= 2;

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (wakeUpTimerRef.current) {
        clearTimeout(wakeUpTimerRef.current);
      }
    };
  }, []);

  const handleAnalyze = async () => {
    if (!canAnalyze) return;

    setLoading(true);
    setError(null);
    setShowWakeUpNotice(false);

    // Show wake-up notice after 3 seconds
    wakeUpTimerRef.current = setTimeout(() => {
      setShowWakeUpNotice(true);
    }, 3000);

    try {
      const response = await fetch(`${API_URL}/analyze`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          tickers: tickers,
          strategy: strategyMap[strategy],
          force_min_weight: forceMinAllocation,
        }),
      });

      // Clear the wake-up timer
      if (wakeUpTimerRef.current) {
        clearTimeout(wakeUpTimerRef.current);
        setShowWakeUpNotice(false);
      }

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Server error: ${response.status}`);
      }

      const backendData = await response.json();
      setResult(transformResponse(backendData));
    } catch (err) {
      console.error('API Error:', err);
      setError(err.message || 'Failed to analyze portfolio. Please try again.');

      // Clear wake-up timer on error
      if (wakeUpTimerRef.current) {
        clearTimeout(wakeUpTimerRef.current);
        setShowWakeUpNotice(false);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-logo">
          <span className="app-logo-primary">RiskLens</span>
          <span className="app-logo-secondary">Portfolio Manager</span>
        </div>
        <p className="app-tagline">Institutional Grade Risk Analysis</p>
      </header>

      <div className="app-container">
        {/* Input Panel */}
        <div className="input-panel">
          <TickerInput
            tickers={tickers}
            onTickersChange={setTickers}
          />

          <StrategySelector
            value={strategy}
            onChange={setStrategy}
          />

          <AllocationToggle
            enabled={forceMinAllocation}
            onChange={setForceMinAllocation}
          />

          <AnalyzeButton
            onClick={handleAnalyze}
            disabled={!canAnalyze}
            loading={loading}
          />

          {/* Wake-up notice */}
          {showWakeUpNotice && (
            <div className="wake-up-notice">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
              <span>Waking up the server... this might take up to 30 seconds</span>
            </div>
          )}

          {/* Error notice */}
          {error && (
            <div className="error-notice">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Dashboard Panel */}
        <div className="dashboard-panel">
          {result ? (
            <>
              <MarketWeather
                status={result.marketStatus.color}
                score={result.marketStatus.score}
                message={result.marketStatus.message}
              />

              <div className="dashboard-grid">
                <div className="chart-container">
                  <PortfolioChart data={result.portfolioData} />
                </div>

                <div className="risk-cards-container">
                  <RiskCard
                    type="volatility"
                    title="Daily Volatility"
                    value={`${result.riskMetrics.volatility}%`}
                    subtitle="Annualized risk measure"
                  />
                  <RiskCard
                    type="var"
                    title="Value at Risk (95%)"
                    value={`-${result.riskMetrics.var}%`}
                    subtitle="Max daily loss at 95% confidence"
                  />
                  <RiskCard
                    type="es"
                    title="Expected Shortfall (95%)"
                    value={`-${result.riskMetrics.es}%`}
                    subtitle="Average loss beyond VaR"
                  />
                </div>
              </div>

              {/* Correlation Heatmap - Only show if data exists */}
              {result.correlationMatrix && (
                <CorrelationHeatmap data={result.correlationMatrix} />
              )}
            </>
          ) : (
            <div className="empty-state card">
              <div className="empty-state-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M3 3v18h18" />
                  <path d="M18 9l-5 5-4-4-3 3" />
                </svg>
              </div>
              <h3 className="empty-state-title">Ready to Analyze</h3>
              <p className="empty-state-text">
                Add at least 2 tickers, select your strategy, and run the analysis to see your optimized portfolio.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;

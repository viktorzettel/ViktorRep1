import { useState } from 'react';
import './TickerInput.css';

const TickerInput = ({ tickers, onTickersChange }) => {
  const [inputValue, setInputValue] = useState('');
  const [error, setError] = useState('');

  const handleAdd = () => {
    const ticker = inputValue.trim().toUpperCase();

    if (!ticker) {
      setError('Please enter a ticker symbol');
      return;
    }

    if (tickers.includes(ticker)) {
      setError('Ticker already added');
      return;
    }

    if (tickers.length >= 10) {
      setError('Maximum 10 tickers allowed');
      return;
    }

    if (!/^[A-Z0-9-]{1,10}$/.test(ticker)) {
      setError('Invalid ticker format');
      return;
    }

    onTickersChange([...tickers, ticker]);
    setInputValue('');
    setError('');
  };

  const handleRemove = (tickerToRemove) => {
    onTickersChange(tickers.filter(t => t !== tickerToRemove));
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAdd();
    }
  };

  return (
    <div className="ticker-input card">
      <div className="ticker-header">
        <span className="ticker-label">Stock Tickers</span>
        <span className="ticker-count">{tickers.length}/10</span>
      </div>

      <p className="ticker-description">
        Enter stock symbols (e.g., AAPL for Apple, NVDA for Nvidia) or crypto pairs (e.g., BTC-USD).
        These are the unique identifiers used on exchanges.
      </p>

      <div className="ticker-input-row">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value.toUpperCase());
            setError('');
          }}
          onKeyDown={handleKeyDown}
          placeholder="Enter ticker (e.g., NVDA)"
          maxLength={10}
          className="ticker-field"
        />
        <button
          onClick={handleAdd}
          className="ticker-add-btn"
          disabled={tickers.length >= 10}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          Add
        </button>
      </div>

      {error && <div className="ticker-error">{error}</div>}

      {tickers.length < 2 && (
        <div className="ticker-hint">Add at least 2 tickers to analyze</div>
      )}

      <div className="ticker-chips">
        {tickers.map((ticker) => (
          <div key={ticker} className="ticker-chip">
            <span>{ticker}</span>
            <button
              onClick={() => handleRemove(ticker)}
              className="ticker-chip-remove"
              aria-label={`Remove ${ticker}`}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TickerInput;

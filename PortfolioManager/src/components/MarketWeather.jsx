import './MarketWeather.css';

const MarketWeather = ({ status, score, message }) => {
    const isGreen = status === 'green';

    const getStatusMessage = () => {
        if (message) {
            return message === 'Calm'
                ? 'Market conditions are favorable. Current volatility is within normal ranges.'
                : 'High volatility detected. Consider reducing exposure or hedging your positions.';
        }
        return isGreen
            ? 'Market conditions are favorable. Current volatility is within normal ranges.'
            : 'High volatility detected. Consider reducing exposure or hedging your positions.';
    };

    return (
        <div className={`market-weather card ${isGreen ? 'green' : 'red'}`}>
            <div className="weather-icon">
                {isGreen ? (
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="5"></circle>
                        <line x1="12" y1="1" x2="12" y2="3"></line>
                        <line x1="12" y1="21" x2="12" y2="23"></line>
                        <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                        <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                        <line x1="1" y1="12" x2="3" y2="12"></line>
                        <line x1="21" y1="12" x2="23" y2="12"></line>
                        <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                        <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
                    </svg>
                ) : (
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9"></path>
                        <polyline points="13 11 9 17 15 17 11 23"></polyline>
                    </svg>
                )}
            </div>

            <div className="weather-content">
                <span className="weather-title">
                    {message || (isGreen ? 'Calm' : 'Volatile')}
                    {score && <span className="weather-score"> · Score: {score}</span>}
                </span>
                <span className="weather-message">
                    {getStatusMessage()}
                </span>
            </div>

            <div className="weather-indicator">
                <span className="weather-status">{isGreen ? 'STABLE' : 'CAUTION'}</span>
            </div>
        </div>
    );
};

export default MarketWeather;

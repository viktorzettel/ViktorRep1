import './RiskCard.css';

const explanations = {
    volatility: 'Measures how much your portfolio value fluctuates day-to-day. Higher volatility means larger price swings.',
    var: 'Maximum expected loss on 95% of trading days. There\'s only a 5% chance of losing more than this.',
    es: 'Average loss during the worst 5% of days. Shows how bad it could get when losses exceed VaR.'
};

const RiskCard = ({ type, title, value, subtitle }) => {
    const getIcon = () => {
        switch (type) {
            case 'volatility':
                return (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
                    </svg>
                );
            case 'var':
                return (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
                    </svg>
                );
            case 'es':
                return (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                    </svg>
                );
            default:
                return null;
        }
    };

    const getColorClass = () => {
        switch (type) {
            case 'volatility':
                return 'info';
            case 'var':
                return 'warning';
            case 'es':
                return 'danger';
            default:
                return 'info';
        }
    };

    const getMetricName = () => {
        switch (type) {
            case 'volatility':
                return 'Volatility';
            case 'var':
                return 'Value at Risk';
            case 'es':
                return 'Expected Shortfall';
            default:
                return '';
        }
    };

    return (
        <div className={`risk-card card ${getColorClass()}`}>
            <div className="risk-icon">
                {getIcon()}
            </div>

            <div className="risk-content">
                <span className="risk-title">{title}</span>
                <span className="risk-value">{value}</span>
                {subtitle && <span className="risk-subtitle">{subtitle}</span>}
                <div className="risk-explanation">
                    <span className="risk-explanation-name">{getMetricName()}:</span>
                    <span className="risk-explanation-text">{explanations[type]}</span>
                </div>
            </div>
        </div>
    );
};

export default RiskCard;

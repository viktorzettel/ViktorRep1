import './StrategySelector.css';

const strategies = [
    {
        id: 'safety',
        name: 'Safety First',
        description: 'Conservative approach with minimal risk',
        icon: '🛡️',
        color: '#10b981'
    },
    {
        id: 'balanced',
        name: 'Smart Balance',
        description: 'Balanced risk and reward strategy',
        icon: '⚖️',
        color: '#3b82f6'
    },
    {
        id: 'aggressive',
        name: 'Aggressive Growth',
        description: 'Maximum growth potential, higher risk',
        icon: '🚀',
        color: '#f59e0b'
    }
];

const StrategySelector = ({ value, onChange }) => {
    return (
        <div className="strategy-selector card">
            <div className="strategy-label">Investment Strategy</div>

            <div className="strategy-options">
                {strategies.map((strategy) => (
                    <button
                        key={strategy.id}
                        className={`strategy-option ${value === strategy.id ? 'active' : ''}`}
                        onClick={() => onChange(strategy.id)}
                        style={{ '--strategy-color': strategy.color }}
                    >
                        <span className="strategy-icon">{strategy.icon}</span>
                        <div className="strategy-content">
                            <span className="strategy-name">{strategy.name}</span>
                            <span className="strategy-desc">{strategy.description}</span>
                        </div>
                        <div className="strategy-check">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                                <polyline points="20 6 9 17 4 12"></polyline>
                            </svg>
                        </div>
                    </button>
                ))}
            </div>
        </div>
    );
};

export default StrategySelector;

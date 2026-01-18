import './AllocationToggle.css';

const AllocationToggle = ({ enabled, onChange }) => {
    return (
        <div className="allocation-toggle card">
            <div className="toggle-content">
                <div className="toggle-info">
                    <span className="toggle-label">Force Minimum 5% Allocation</span>
                    <span className="toggle-description">
                        Ensure every selected asset receives at least 5% of the portfolio
                    </span>
                </div>

                <button
                    className={`toggle-switch ${enabled ? 'active' : ''}`}
                    onClick={() => onChange(!enabled)}
                    role="switch"
                    aria-checked={enabled}
                >
                    <span className="toggle-knob"></span>
                </button>
            </div>
        </div>
    );
};

export default AllocationToggle;

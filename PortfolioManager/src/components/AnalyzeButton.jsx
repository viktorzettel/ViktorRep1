import './AnalyzeButton.css';

const AnalyzeButton = ({ onClick, disabled, loading }) => {
    return (
        <button
            className={`analyze-button ${loading ? 'loading' : ''}`}
            onClick={onClick}
            disabled={disabled || loading}
        >
            {loading ? (
                <>
                    <span className="analyze-spinner"></span>
                    <span>Analyzing Portfolio...</span>
                </>
            ) : (
                <>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                    </svg>
                    <span>Analyze Portfolio</span>
                </>
            )}
        </button>
    );
};

export default AnalyzeButton;

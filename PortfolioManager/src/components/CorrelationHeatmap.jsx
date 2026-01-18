import './CorrelationHeatmap.css';

const CorrelationHeatmap = ({ data }) => {
    if (!data || !data.labels || !data.values) {
        return null;
    }

    const { labels, values } = data;

    // Get color based on correlation value
    // 1.0 = Dark Red, 0.0 = Light Gray, -1.0 = Dark Blue
    const getColor = (value) => {
        if (value >= 0) {
            // Positive: Light gray to dark red
            const intensity = Math.min(value, 1);
            const r = 220;
            const g = Math.round(220 - (intensity * 180));
            const b = Math.round(220 - (intensity * 180));
            return `rgb(${r}, ${g}, ${b})`;
        } else {
            // Negative: Light gray to dark blue
            const intensity = Math.min(Math.abs(value), 1);
            const r = Math.round(220 - (intensity * 180));
            const g = Math.round(220 - (intensity * 140));
            const b = 220;
            return `rgb(${r}, ${g}, ${b})`;
        }
    };

    const getTextColor = (value) => {
        return Math.abs(value) > 0.5 ? '#fff' : '#1e293b';
    };

    return (
        <div className="correlation-heatmap card">
            <div className="heatmap-header">
                <span className="heatmap-title">Asset Correlation Matrix</span>
                <span className="heatmap-subtitle">How assets move together</span>
            </div>

            <div
                className="heatmap-container"
                style={{
                    gridTemplateColumns: `auto repeat(${labels.length}, auto)`
                }}
            >
                {/* Empty corner cell */}
                <div className="heatmap-corner"></div>

                {/* Column headers */}
                {labels.map((label, i) => (
                    <div key={`col-${i}`} className="heatmap-col-label">
                        {label}
                    </div>
                ))}

                {/* Rows */}
                {values.map((row, rowIndex) => (
                    <>
                        {/* Row label */}
                        <div key={`row-label-${rowIndex}`} className="heatmap-row-label">
                            {labels[rowIndex]}
                        </div>

                        {/* Cells */}
                        {row.map((value, colIndex) => (
                            <div
                                key={`cell-${rowIndex}-${colIndex}`}
                                className="heatmap-cell"
                                style={{
                                    backgroundColor: getColor(value),
                                    color: getTextColor(value)
                                }}
                                title={`${labels[rowIndex]} ↔ ${labels[colIndex]}: ${value.toFixed(2)}`}
                            >
                                {value.toFixed(2)}
                            </div>
                        ))}
                    </>
                ))}
            </div>

            {/* Legend */}
            <div className="heatmap-legend">
                <div className="legend-item">
                    <span className="legend-color" style={{ background: 'rgb(40, 80, 220)' }}></span>
                    <span>Negative</span>
                </div>
                <div className="legend-item">
                    <span className="legend-color" style={{ background: 'rgb(220, 220, 220)' }}></span>
                    <span>Uncorrelated</span>
                </div>
                <div className="legend-item">
                    <span className="legend-color" style={{ background: 'rgb(220, 40, 40)' }}></span>
                    <span>Positive</span>
                </div>
            </div>
        </div>
    );
};

export default CorrelationHeatmap;

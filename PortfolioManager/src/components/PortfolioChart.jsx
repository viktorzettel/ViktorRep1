import { PieChart, Pie, Cell, ResponsiveContainer, Legend } from 'recharts';
import './PortfolioChart.css';

const COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16'];

const PortfolioChart = ({ data }) => {
    // Normalize data to ensure it sums to exactly 100%
    const total = data.reduce((sum, item) => sum + item.value, 0);
    const normalizedData = data.map((item, index) => {
        if (index === data.length - 1) {
            // Last item gets the remainder to ensure exactly 100%
            const othersSum = data.slice(0, -1).reduce((sum, d) => sum + Math.round((d.value / total) * 100), 0);
            return { ...item, value: 100 - othersSum };
        }
        return { ...item, value: Math.round((item.value / total) * 100) };
    });

    const assetCount = data.length;
    const showMaxAllocationNote = assetCount <= 3;

    const renderLabel = ({ cx, cy }) => {
        return (
            <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central">
                <tspan x={cx} dy="-0.5em" className="chart-center-label">Portfolio</tspan>
                <tspan x={cx} dy="1.5em" className="chart-center-value">100%</tspan>
            </text>
        );
    };

    const renderLegend = ({ payload }) => {
        return (
            <ul className="chart-legend">
                {payload.map((entry, index) => (
                    <li key={`legend-${index}`} className="legend-item">
                        <span className="legend-color" style={{ background: entry.color }}></span>
                        <span className="legend-name">{entry.value}</span>
                        <span className="legend-value">{normalizedData[index].value}%</span>
                    </li>
                ))}
            </ul>
        );
    };

    return (
        <div className="portfolio-chart card">
            <div className="chart-header">
                <span className="chart-title">Portfolio Allocation</span>
            </div>

            <div className="chart-wrapper">
                <ResponsiveContainer width="100%" height={300}>
                    <PieChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                        <Pie
                            data={normalizedData}
                            cx="50%"
                            cy="50%"
                            innerRadius={60}
                            outerRadius={90}
                            paddingAngle={2}
                            dataKey="value"
                            labelLine={false}
                            label={renderLabel}
                        >
                            {normalizedData.map((entry, index) => (
                                <Cell
                                    key={`cell-${index}`}
                                    fill={COLORS[index % COLORS.length]}
                                    stroke="rgba(0,0,0,0.3)"
                                    strokeWidth={1}
                                />
                            ))}
                        </Pie>
                        <Legend content={renderLegend} />
                    </PieChart>
                </ResponsiveContainer>
            </div>

            {showMaxAllocationNote && (
                <div className="chart-note">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="12" y1="16" x2="12" y2="12"></line>
                        <line x1="12" y1="8" x2="12.01" y2="8"></line>
                    </svg>
                    <span>
                        Maximum allocation per asset is capped at 35% for diversification.
                        Add more assets for a more balanced distribution.
                    </span>
                </div>
            )}
        </div>
    );
};

export default PortfolioChart;

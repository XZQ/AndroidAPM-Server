import { formatDateTime } from "../format";

export interface TrendSeries {
  label: string;
  color: string;
  values: number[];
}

export function TrendChart({
  timestamps,
  series,
  summary,
}: {
  timestamps: number[];
  series: TrendSeries[];
  summary: string;
}) {
  const width = 820;
  const height = 214;
  const padding = { top: 18, right: 18, bottom: 34, left: 34 };
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const maxValue = Math.max(1, ...series.flatMap((item) => item.values));
  const count = Math.max(1, timestamps.length);
  const xFor = (index: number) => padding.left + (index / Math.max(1, count - 1)) * innerWidth;
  const yFor = (value: number) => padding.top + innerHeight - (value / maxValue) * innerHeight;

  return (
    <div className="trend-chart">
      <div className="trend-legend" aria-hidden="true">
        {series.map((item) => (
          <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary}>
        {[0, 0.5, 1].map((ratio) => {
          const y = padding.top + ratio * innerHeight;
          return <line key={ratio} x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="chart-grid" />;
        })}
        {series.map((item) => (
          <polyline
            key={item.label}
            points={item.values.map((value, index) => `${xFor(index)},${yFor(value)}`).join(" ")}
            fill="none"
            stroke={item.color}
            strokeWidth="2.5"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        <text x="8" y={padding.top + 4} className="chart-label">{maxValue}</text>
        <text x="18" y={padding.top + innerHeight + 4} className="chart-label">0</text>
        {timestamps.length > 0 ? (
          <>
            <text x={padding.left} y={height - 8} className="chart-label">{shortTime(timestamps[0] ?? 0)}</text>
            <text x={width - padding.right} y={height - 8} textAnchor="end" className="chart-label">{shortTime(timestamps.at(-1) ?? 0)}</text>
          </>
        ) : null}
      </svg>
      <p className="sr-only">{summary}</p>
    </div>
  );
}

function shortTime(value: number): string {
  const full = formatDateTime(value);
  return full === "—" ? full : full.slice(5, 16);
}

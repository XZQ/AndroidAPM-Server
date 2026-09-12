import { formatDateTime } from "../format";

export interface TrendSeries {
  label: string;
  color: string;
  values: (number | null)[];
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
  const observed = series.flatMap((item) => item.values).filter((value): value is number => value !== null && Number.isFinite(value));
  const maxValue = Math.max(1, ...observed);
  const count = Math.max(1, timestamps.length);
  const xFor = (index: number) => padding.left + (index / Math.max(1, count - 1)) * innerWidth;
  const yFor = (value: number) => padding.top + innerHeight - (value / maxValue) * innerHeight;

  if (observed.length === 0) {
    return <div className="trend-chart"><p role="status">当前窗口没有可绘制的趋势数据</p><p className="sr-only">{summary}</p></div>;
  }

  return (
    <div className="trend-chart">
      <div className="trend-legend" aria-hidden="true">
        {series.map((item) => (
          <span key={item.label}><i style={{ background: item.color }} />{item.label}{item.values.some((value) => value === null) ? " · 有数据缺口" : ""}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={summary}>
        {[0, 0.5, 1].map((ratio) => {
          const y = padding.top + ratio * innerHeight;
          return <line key={ratio} x1={padding.left} x2={width - padding.right} y1={y} y2={y} className="chart-grid" />;
        })}
        {series.map((item) => <g key={item.label}>
          {segments(item.values).map((segment) => segment.length === 1 ? (
            <circle key={segment[0]!.index} cx={xFor(segment[0]!.index)} cy={yFor(segment[0]!.value)} r="3" fill={item.color} />
          ) : (
            <polyline key={segment[0]!.index} points={segment.map(({ value, index }) => `${xFor(index)},${yFor(value)}`).join(" ")}
              fill="none" stroke={item.color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
          ))}
        </g>)}
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

function segments(values: (number | null)[]): { index: number; value: number }[][] {
  const result: { index: number; value: number }[][] = [];
  let current: { index: number; value: number }[] = [];
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      current = [];
    } else {
      if (current.length === 0) result.push(current);
      current.push({ index, value });
    }
  });
  return result;
}

function shortTime(value: number): string {
  const full = formatDateTime(value);
  return full === "—" ? full : full.slice(5, 16);
}

import { cn } from "@/lib/utils";

type Point = {
  label: string;
  value: number;
};

export function LineChart({
  data,
  className,
  stroke = "hsl(var(--primary))",
}: {
  data: Point[];
  className?: string;
  stroke?: string;
}) {
  const width = 760;
  const height = 240;
  const padding = 30;

  if (data.length === 0) {
    return <div className={cn("rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground", className)}>No data</div>;
  }

  const maxValue = Math.max(...data.map((point) => point.value), 1);
  const minValue = Math.min(...data.map((point) => point.value), 0);
  const range = Math.max(maxValue - minValue, 1);

  const points = data.map((point, index) => {
    const x = padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2);
    const normalized = (point.value - minValue) / range;
    const y = height - padding - normalized * (height - padding * 2);
    return { x, y, ...point };
  });

  const polyline = points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <div className={cn("rounded-md border bg-white/80 p-3", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[240px] w-full">
        <defs>
          <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.03" />
          </linearGradient>
        </defs>
        <line x1={padding} y1={padding} x2={padding} y2={height - padding} stroke="hsl(var(--border))" />
        <line
          x1={padding}
          y1={height - padding}
          x2={width - padding}
          y2={height - padding}
          stroke="hsl(var(--border))"
        />

        <polyline
          points={`${padding},${height - padding} ${polyline} ${width - padding},${height - padding}`}
          fill="url(#chart-fill)"
          stroke="none"
        />
        <polyline points={polyline} fill="none" stroke={stroke} strokeWidth="2" />

        {points.map((point) => (
          <g key={`${point.label}-${point.x}`}>
            <circle cx={point.x} cy={point.y} r="3" fill={stroke} />
            <title>{`${point.label}: ${point.value}`}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

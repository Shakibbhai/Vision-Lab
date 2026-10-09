"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

// Validated reference palette (light mode): categorical slots in fixed order, chart chrome and ink.
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"] as const;
const INK = { primary: "#0b0b0b", secondary: "#52514e", muted: "#898781" };
const GRID = "#e1e0d9";
const BASELINE = "#c3c2b7";
const SURFACE = "#fcfcfb";

const fmt = new Intl.NumberFormat("en-US");
export const formatNumber = (n: number) => fmt.format(Math.round(n));

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceMax(value: number): number {
  if (value <= 0) return 4;
  const exp = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * exp * 4 >= value) ?? 10;
  return step * exp * 4;
}

function roundedTop(x: number, y: number, w: number, h: number, r = 4): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function roundedRight(x: number, y: number, w: number, h: number, r = 4): string {
  if (w <= 0) return "";
  const rr = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h - rr}Q${x + w},${y + h} ${x + w - rr},${y + h}H${x}Z`;
}

type Tip = { x: number; y: number; title: string; rows: { color: string; label: string; value: string }[] } | null;

function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  return (
    <div
      className="pointer-events-none absolute z-10 min-w-[120px] -translate-x-1/2 -translate-y-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs shadow-lg"
      style={{ left: tip.x, top: tip.y - 8 }}
    >
      <p className="mb-1 font-semibold" style={{ color: INK.primary }}>{tip.title}</p>
      {tip.rows.map((row) => (
        <p key={row.label} className="flex items-center justify-between gap-3" style={{ color: INK.secondary }}>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: row.color }} />
            {row.label}
          </span>
          <span className="font-semibold tabular-nums" style={{ color: INK.primary }}>{row.value}</span>
        </p>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap gap-4 text-xs" style={{ color: INK.secondary }}>
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

export function ChartCard({
  title,
  subtitle,
  action,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-2xl border border-slate-200 bg-white p-5 shadow-sm", className)}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function EmptyChart({ message, height = 220 }: { message: string; height?: number }) {
  return (
    <div
      className="grid place-items-center rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-6 text-center text-sm text-slate-500"
      style={{ height }}
    >
      {message}
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  icon,
  tone = "bg-sky-50 text-sky-700",
}: {
  label: string;
  value: string;
  hint?: string;
  icon: ReactNode;
  tone?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-3">
        <span className={cn("grid h-10 w-10 place-items-center rounded-full", tone)}>{icon}</span>
        <p className="text-sm font-medium text-slate-600">{label}</p>
      </div>
      <p className="mt-4 text-3xl font-semibold text-slate-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

export type BarSeries = { key: string; label: string; color: string };
export type BarDatum = { label: string; values: Record<string, number> };

/** Vertical bars; several series render side by side per category with a 2px gap. */
export function BarChart({
  data,
  series,
  height = 240,
  valueFormat = formatNumber,
}: {
  data: BarDatum[];
  series: BarSeries[];
  height?: number;
  valueFormat?: (n: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip>(null);
  const pad = { top: 12, right: 8, bottom: 28, left: 40 };
  const max = niceMax(Math.max(0, ...data.flatMap((d) => series.map((s) => d.values[s.key] ?? 0))));
  const plotW = Math.max(0, width - pad.left - pad.right);
  const plotH = height - pad.top - pad.bottom;
  const band = data.length ? plotW / data.length : 0;
  const groupW = Math.min(band * 0.7, 28 * series.length + 2 * (series.length - 1));
  const barW = series.length ? (groupW - 2 * (series.length - 1)) / series.length : 0;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 ? (
        <svg width={width} height={height} role="img" aria-label="Bar chart">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? BASELINE : GRID} />
              <text x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill={INK.muted} className="tabular-nums">
                {valueFormat(t)}
              </text>
            </g>
          ))}
          {data.map((d, i) => {
            const cx = pad.left + band * i + band / 2;
            const x0 = cx - groupW / 2;
            return (
              <g key={d.label}>
                {series.map((s, j) => {
                  const v = d.values[s.key] ?? 0;
                  return <path key={s.key} d={roundedTop(x0 + j * (barW + 2), y(v), barW, pad.top + plotH - y(v))} fill={s.color} />;
                })}
                <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill={INK.secondary}>
                  {d.label.length > Math.max(4, Math.floor(band / 7)) ? `${d.label.slice(0, Math.max(3, Math.floor(band / 7) - 1))}…` : d.label}
                </text>
                <rect
                  x={pad.left + band * i}
                  y={pad.top}
                  width={band}
                  height={plotH}
                  fill="transparent"
                  onMouseEnter={() =>
                    setTip({
                      x: cx,
                      y: y(Math.max(...series.map((s) => d.values[s.key] ?? 0))),
                      title: d.label,
                      rows: series.map((s) => ({ color: s.color, label: s.label, value: valueFormat(d.values[s.key] ?? 0) })),
                    })
                  }
                  onMouseLeave={() => setTip(null)}
                />
              </g>
            );
          })}
        </svg>
      ) : null}
      <Tooltip tip={tip} />
    </div>
  );
}

/** Horizontal ranked bars with the value printed at the bar end (single series). */
export function HBarChart({
  data,
  color = SERIES[0],
  valueFormat = formatNumber,
  rowHeight = 26,
}: {
  data: { label: string; value: number }[];
  color?: string;
  valueFormat?: (n: number) => string;
  rowHeight?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const labelW = 96;
  const valueW = 56;
  const max = Math.max(1, ...data.map((d) => d.value));
  const plotW = Math.max(0, width - labelW - valueW);
  const height = data.length * rowHeight;

  return (
    <div ref={ref} className="w-full" style={{ height }}>
      {width > 0 ? (
        <svg width={width} height={height} role="img" aria-label="Ranked bar chart">
          {data.map((d, i) => {
            const w = (d.value / max) * plotW;
            const top = i * rowHeight + 4;
            const h = rowHeight - 8;
            return (
              <g key={d.label}>
                <title>{`${d.label}: ${valueFormat(d.value)}`}</title>
                <text x={labelW - 10} y={top + h / 2} dy="0.32em" textAnchor="end" fontSize={12} fill={INK.secondary}>
                  {d.label}
                </text>
                <path d={roundedRight(labelW, top, Math.max(w, 2), h)} fill={color} />
                <text x={labelW + Math.max(w, 2) + 6} y={top + h / 2} dy="0.32em" fontSize={12} fontWeight={600} fill={INK.primary} className="tabular-nums">
                  {valueFormat(d.value)}
                </text>
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}

/** Single-series line with a soft area, crosshair and tooltip. */
export function AreaChart({
  points,
  height = 220,
  color = SERIES[0],
  xFormat = (x: number) => String(x),
  valueFormat = formatNumber,
  seriesLabel,
}: {
  points: { x: number; y: number }[];
  height?: number;
  color?: string;
  xFormat?: (x: number) => string;
  valueFormat?: (n: number) => string;
  seriesLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 12, right: 12, bottom: 28, left: 40 };
  const plotW = Math.max(0, width - pad.left - pad.right);
  const plotH = height - pad.top - pad.bottom;
  const xMin = points.length ? points[0].x : 0;
  const xMax = points.length ? points[points.length - 1].x : 1;
  const max = niceMax(Math.max(0, ...points.map((p) => p.y)));
  const sx = (x: number) => pad.left + (xMax === xMin ? plotW / 2 : ((x - xMin) / (xMax - xMin)) * plotW);
  const sy = (v: number) => pad.top + plotH - (v / max) * plotH;

  const { line, area } = useMemo(() => {
    if (!points.length || width === 0) return { line: "", area: "" };
    const l = points.map((p, i) => `${i ? "L" : "M"}${sx(p.x)},${sy(p.y)}`).join("");
    const a = `${l}L${sx(points[points.length - 1].x)},${pad.top + plotH}L${sx(points[0].x)},${pad.top + plotH}Z`;
    return { line: l, area: a };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, width, max]);

  // Fewer x labels on narrow charts so they never collide
  const xSteps = width < 520 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];
  const xTicks = points.length ? xSteps.map((t) => xMin + t * (xMax - xMin)) : [];
  const active = hover !== null ? points[hover] : null;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 ? (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`${seriesLabel} over time`}
          onMouseMove={(e) => {
            const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const px = e.clientX - box.left;
            let best = 0;
            points.forEach((p, i) => {
              if (Math.abs(sx(p.x) - px) < Math.abs(sx(points[best].x) - px)) best = i;
            });
            setHover(points.length ? best : null);
          }}
          onMouseLeave={() => setHover(null)}
        >
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={sy(t * max)} y2={sy(t * max)} stroke={t === 0 ? BASELINE : GRID} />
              <text x={pad.left - 8} y={sy(t * max)} dy="0.32em" textAnchor="end" fontSize={11} fill={INK.muted} className="tabular-nums">
                {valueFormat(t * max)}
              </text>
            </g>
          ))}
          {xTicks.map((t, i) => (
            <text
              key={t}
              x={sx(t)}
              y={height - 8}
              textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
              fontSize={11}
              fill={INK.muted}
            >
              {xFormat(t)}
            </text>
          ))}
          <path d={area} fill={color} opacity={0.12} />
          <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
          {active ? (
            <g>
              <line x1={sx(active.x)} x2={sx(active.x)} y1={pad.top} y2={pad.top + plotH} stroke={BASELINE} />
              <circle cx={sx(active.x)} cy={sy(active.y)} r={5} fill={color} stroke={SURFACE} strokeWidth={2} />
            </g>
          ) : null}
        </svg>
      ) : null}
      <Tooltip
        tip={
          active
            ? { x: sx(active.x), y: sy(active.y), title: xFormat(active.x), rows: [{ color, label: seriesLabel, value: valueFormat(active.y) }] }
            : null
        }
      />
    </div>
  );
}

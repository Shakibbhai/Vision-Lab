"use client";

import { useMemo, useState } from "react";
import { Download, Loader2 } from "lucide-react";

import { AreaChart, BarChart, ChartCard, EmptyChart, HBarChart, Legend, SERIES, formatNumber } from "@/components/charts/viz";
import { formatSeconds, useInsights } from "@/hooks/use-insights";

const ALL = "all";

export default function AnalyticsPage() {
  const { data, error } = useInsights();
  const [selected, setSelected] = useState<string>(ALL);

  const cameras = useMemo(() => data?.cameras ?? [], [data]);
  const scoped = selected === ALL ? cameras : cameras.filter((c) => String(c.id) === selected);
  // Identity charts need one video: the selected source, else the most recently processed one
  const focus = useMemo(() => {
    if (selected !== ALL) return cameras.find((c) => String(c.id) === selected) ?? null;
    return [...cameras].filter((c) => c.reid).sort((a, b) => (b.reid?.processed_at ?? 0) - (a.reid?.processed_at ?? 0))[0] ?? null;
  }, [cameras, selected]);

  if (!data) {
    return (
      <div className="grid min-h-[50vh] place-items-center text-sm text-slate-500">
        {error ? <p className="text-red-600">{error}</p> : <Loader2 className="h-6 w-6 animate-spin" />}
      </div>
    );
  }

  const queues = data.queues.filter((q) => selected === ALL || String(q.camera_id) === selected);
  const reid = focus?.reid ?? null;

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <label htmlFor="source-filter" className="text-sm font-medium text-slate-700">
          Source
        </label>
        <select
          id="source-filter"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="h-9 min-w-[200px] rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900"
        >
          <option value={ALL}>All sources</option>
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <p className="ml-auto text-xs text-slate-500">Updated {new Date(`${data.generated_at}Z`).toLocaleTimeString()}</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard title="Unique persons per source" subtitle="Distinct people after re-identification">
          {scoped.some((c) => c.unique_persons > 0) ? (
            <BarChart
              data={scoped.map((c) => ({ label: c.name, values: { v: c.unique_persons } }))}
              series={[{ key: "v", label: "Unique persons", color: SERIES[0] }]}
            />
          ) : (
            <EmptyChart message="No identities yet. Process an uploaded video from its source card." />
          )}
        </ChartCard>
        <ChartCard title="Detections per source" subtitle="Person boxes counted across processed frames">
          {scoped.some((c) => c.detections > 0) ? (
            <BarChart
              data={scoped.map((c) => ({ label: c.name, values: { v: c.detections } }))}
              series={[{ key: "v", label: "Detections", color: SERIES[0] }]}
            />
          ) : (
            <EmptyChart message="No detections recorded yet." />
          )}
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Screen time per person"
          subtitle={focus ? `How long each person was visible in "${focus.name}"` : "How long each person was visible"}
          action={
            reid ? (
              <a
                href={`/api/backend${reid.download_url}`}
                download
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                <Download className="h-3.5 w-3.5" /> Video
              </a>
            ) : null
          }
        >
          {reid?.identities.length ? (
            <HBarChart
              data={reid.identities.map((p) => ({ label: `PERSON_${String(p.id).padStart(4, "0")}`, value: p.seconds }))}
              valueFormat={formatSeconds}
            />
          ) : (
            <EmptyChart message="This source has no processed Re-ID video yet. Press the green Download button on its source card." />
          )}
        </ChartCard>
        <ChartCard
          title="People in view over time"
          subtitle={reid ? `Persons on screen through the ${formatSeconds(reid.duration_seconds)} video` : "Persons on screen over the video"}
        >
          {reid?.timeline.length ? (
            <AreaChart
              points={reid.timeline.map(([x, y]) => ({ x, y }))}
              xFormat={(s) => formatSeconds(s)}
              seriesLabel="Persons in view"
            />
          ) : (
            <EmptyChart message="Available after a video is processed." />
          )}
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Entries and exits"
          subtitle="People crossing zone entry and exit lines"
          action={
            <Legend items={[{ label: "Entries", color: SERIES[0] }, { label: "Exits", color: SERIES[1] }]} />
          }
        >
          {scoped.some((c) => c.entries + c.exits > 0) ? (
            <BarChart
              data={scoped.map((c) => ({ label: c.name, values: { in: c.entries, out: c.exits } }))}
              series={[
                { key: "in", label: "Entries", color: SERIES[0] },
                { key: "out", label: "Exits", color: SERIES[1] },
              ]}
            />
          ) : (
            <EmptyChart message="Draw entry and exit lines on a zone and add Entry/Exit Count to record crossings." />
          )}
        </ChartCard>
        <ChartCard title="Queues" subtitle="Latest queue analysis per zone">
          {queues.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="py-2 pr-4 font-semibold">Zone</th>
                    <th className="py-2 pr-4 text-right font-semibold">Avg in queue</th>
                    <th className="py-2 pr-4 text-right font-semibold">Max in queue</th>
                    <th className="py-2 text-right font-semibold">Avg wait</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 tabular-nums">
                  {queues.map((q) => (
                    <tr key={q.zone_id}>
                      <td className="py-2 pr-4 font-medium text-slate-900">{q.zone_name}</td>
                      <td className="py-2 pr-4 text-right">{q.avg_queue_count.toFixed(1)}</td>
                      <td className="py-2 pr-4 text-right">{q.max_queue_count}</td>
                      <td className="py-2 text-right">{q.avg_wait_time_sec != null ? formatSeconds(q.avg_wait_time_sec) : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyChart message="No queue analysis yet. Add the Queue analytics type to a zone." />
          )}
        </ChartCard>
      </div>

      <ChartCard title="All numbers" subtitle="Table view of every source">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-4 font-semibold">Source</th>
                <th className="py-2 pr-4 font-semibold">Type</th>
                <th className="py-2 pr-4 font-semibold">Status</th>
                <th className="py-2 pr-4 text-right font-semibold">Unique persons</th>
                <th className="py-2 pr-4 text-right font-semibold">Detections</th>
                <th className="py-2 pr-4 text-right font-semibold">Entries</th>
                <th className="py-2 pr-4 text-right font-semibold">Exits</th>
                <th className="py-2 text-right font-semibold">Frames</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 tabular-nums">
              {scoped.map((c) => (
                <tr key={c.id}>
                  <td className="py-2 pr-4 font-medium text-slate-900">{c.name}</td>
                  <td className="py-2 pr-4 text-slate-600">{c.source_type}</td>
                  <td className="py-2 pr-4 text-slate-600">{c.live ? "Live" : "Paused"}</td>
                  <td className="py-2 pr-4 text-right">{formatNumber(c.unique_persons)}</td>
                  <td className="py-2 pr-4 text-right">{formatNumber(c.detections)}</td>
                  <td className="py-2 pr-4 text-right">{formatNumber(c.entries)}</td>
                  <td className="py-2 pr-4 text-right">{formatNumber(c.exits)}</td>
                  <td className="py-2 text-right">{formatNumber(c.frames)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ChartCard>
    </>
  );
}

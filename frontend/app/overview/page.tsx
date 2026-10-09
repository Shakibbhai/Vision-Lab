"use client";

import Link from "next/link";
import { Activity, Film, Fingerprint, Loader2, Users, Video } from "lucide-react";

import { AreaChart, BarChart, ChartCard, EmptyChart, HBarChart, SERIES, StatTile, formatNumber } from "@/components/charts/viz";
import { formatSeconds, useInsights } from "@/hooks/use-insights";
import { cn } from "@/lib/utils";

export default function OverviewPage() {
  const { data, error } = useInsights();

  if (!data) {
    return (
      <div className="grid min-h-[50vh] place-items-center text-sm text-slate-500">
        {error ? <p className="text-red-600">{error}</p> : <Loader2 className="h-6 w-6 animate-spin" />}
      </div>
    );
  }

  const { totals, cameras, activity } = data;
  const withReid = cameras.filter((c) => c.reid);
  const latest = [...withReid].sort((a, b) => (b.reid?.processed_at ?? 0) - (a.reid?.processed_at ?? 0))[0];
  const activityPoints = activity.map((a, i) => ({ x: i, y: a.frames }));
  const hourLabel = (i: number) => {
    const point = activity[Math.round(i)];
    return point ? new Date(`${point.hour}Z`).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
  };

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Sources"
          value={formatNumber(totals.cameras)}
          hint={`${totals.live_cameras} live now · ${totals.zones} zones`}
          icon={<Video className="h-5 w-5" />}
          tone="bg-violet-50 text-violet-700"
        />
        <StatTile
          label="Total Detections"
          value={formatNumber(totals.detections)}
          hint={`${formatNumber(totals.frames_captured)} frames captured`}
          icon={<Users className="h-5 w-5" />}
          tone="bg-sky-50 text-sky-700"
        />
        <StatTile
          label="Unique Identities"
          value={formatNumber(totals.unique_persons)}
          hint="Distinct people after re-identification"
          icon={<Fingerprint className="h-5 w-5" />}
          tone="bg-emerald-50 text-emerald-700"
        />
        <StatTile
          label="Re-ID Videos"
          value={formatNumber(totals.reid_videos)}
          hint={`${totals.enrolled_faces} faces enrolled`}
          icon={<Film className="h-5 w-5" />}
          tone="bg-amber-50 text-amber-700"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard
          title="Unique persons by source"
          subtitle="People identified in each camera or video"
          className="xl:col-span-2"
          action={<Link href="/analytics" className="text-sm font-semibold text-primary hover:underline">Open analytics</Link>}
        >
          {cameras.length && cameras.some((c) => c.unique_persons > 0) ? (
            <BarChart
              data={cameras.map((c) => ({ label: c.name, values: { persons: c.unique_persons } }))}
              series={[{ key: "persons", label: "Unique persons", color: SERIES[0] }]}
            />
          ) : (
            <EmptyChart message="No identities yet. Upload a video and press the green Download button on its source card to process it." />
          )}
        </ChartCard>

        <ChartCard title="Sources" subtitle="Status of every camera and video">
          {cameras.length ? (
            <ul className="divide-y divide-slate-100">
              {cameras.map((c) => (
                <li key={c.id} className="flex items-center gap-3 py-2.5">
                  <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", c.live ? "bg-emerald-500" : "bg-slate-300")} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-900">{c.name}</p>
                    <p className="text-xs text-slate-500">
                      {c.source_type} · {c.live ? "Live" : "Paused"}
                    </p>
                  </div>
                  <span className="text-right text-xs text-slate-500">
                    <span className="block text-sm font-semibold text-slate-900">{formatNumber(c.unique_persons)}</span>
                    persons
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyChart message="No sources yet." height={160} />
          )}
          <Link
            href="/configurations"
            className="mt-3 inline-flex text-sm font-semibold text-primary hover:underline"
          >
            Add source
          </Link>
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Most seen identities"
          subtitle={latest ? `Screen time in "${latest.name}" (latest processed video)` : "Screen time per person"}
        >
          {latest?.reid?.identities.length ? (
            <HBarChart
              data={latest.reid.identities.map((p) => ({ label: `PERSON_${String(p.id).padStart(4, "0")}`, value: p.seconds }))}
              valueFormat={formatSeconds}
            />
          ) : (
            <EmptyChart message="Process an uploaded video to see how long each person was visible." />
          )}
        </ChartCard>

        <ChartCard title="Capture activity" subtitle="Frames analysed per hour, last 24 hours">
          {activity.some((a) => a.frames > 0) ? (
            <AreaChart points={activityPoints} xFormat={hourLabel} seriesLabel="Frames" />
          ) : (
            <EmptyChart message="No frames captured in the last 24 hours. Start a source on Live Monitoring." />
          )}
          <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
            <Activity className="h-3.5 w-3.5" /> Updates every 20 seconds
          </p>
        </ChartCard>
      </div>
    </>
  );
}

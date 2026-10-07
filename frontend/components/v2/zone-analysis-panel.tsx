"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type ZoneAnalysisResponse,
  getZoneAnalysis,
  listAnalyzerCameras,
  listAnalyzerJobs,
  runAnalyzerAnalysis,
} from "@/lib/api";
import { usePageVisibility } from "@/hooks/use-page-visibility";
import { parseSelectedZoneId } from "@/lib/zone-selection";
import { StatTile } from "@/components/v2/ui";

function toPercentText(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${Math.round(value)}%`;
}

function levelTone(level: string): string {
  const lowered = level.toLowerCase();
  if (lowered === "critical") return "text-rose-700";
  if (lowered === "high") return "text-orange-700";
  if (lowered === "medium") return "text-amber-700";
  return "text-emerald-700";
}

function staffRatioPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function hasMeaningfulZoneAnalysis(payload: ZoneAnalysisResponse): boolean {
  if ((payload.summary?.tracked_paths ?? 0) > 0) return true;
  if ((payload.summary?.estimated_person_tracks ?? 0) > 0) return true;
  if ((payload.summary?.estimated_staff_tracks ?? 0) > 0) return true;
  if (payload.congestion_by_zone.some((zone) => zone.current_count > 0 || zone.peak_count > 0 || zone.avg_count > 0)) {
    return true;
  }
  if (payload.heatmaps.some((heatmap) => heatmap.max_cell_count > 0)) return true;
  if (
    payload.service_staff_presence.some(
      (zone) =>
        zone.person_tracks > 0 ||
        zone.staff_tracks > 0 ||
        zone.person_presence_seconds > 0 ||
        zone.staff_presence_seconds > 0,
    )
  ) {
    return true;
  }
  return false;
}

function isAnalyzerJobRecent(job: { status: string; created_at: string }): boolean {
  if (job.status === "running" || job.status === "pending") return true;
  const createdAt = new Date(job.created_at).getTime();
  if (Number.isNaN(createdAt)) return false;
  return Date.now() - createdAt < 2 * 60 * 1000;
}

export function ZoneAnalysisPanel({
  cameraId,
  selectedZoneId = "",
  start,
}: {
  cameraId: number;
  selectedZoneId?: string;
  start?: string;
}) {
  const [data, setData] = useState<ZoneAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [latestJobStatus, setLatestJobStatus] = useState<string | null>(null);
  const triggerAttemptedRef = useRef(false);
  const isPageVisible = usePageVisibility();

  const autoTriggerJob = useCallback(
    async (zoneId: number | undefined) => {
      if (triggerAttemptedRef.current) return;
      triggerAttemptedRef.current = true;

      const cameras = await listAnalyzerCameras();
      const camera = cameras.find((row) => row.id === cameraId);
      if (!camera?.first_frame_time || !camera?.latest_frame_time) {
        throw new Error("No captured CCTV frames are available yet for zone analysis.");
      }

      const startTime = new Date(
        camera.first_frame_time.endsWith("Z") ? camera.first_frame_time : `${camera.first_frame_time}Z`,
      );
      const endTime = new Date(
        camera.latest_frame_time.endsWith("Z") ? camera.latest_frame_time : `${camera.latest_frame_time}Z`,
      );

      await runAnalyzerAnalysis({
        camera_id: cameraId,
        zone_id: zoneId,
        start: startTime.toISOString(),
        end: endTime.toISOString(),
      });
    },
    [cameraId],
  );

  useEffect(() => {
    if (!isPageVisible) return undefined;
    let active = true;
    triggerAttemptedRef.current = false;

    async function load() {
      try {
        const zoneId = parseSelectedZoneId(selectedZoneId) ?? undefined;
        const existingJobs = await listAnalyzerJobs(cameraId);
        const latestJob = existingJobs[0] ?? null;

        if (!latestJob || !isAnalyzerJobRecent(latestJob)) {
          await autoTriggerJob(zoneId);
        }

        const refreshedJobs = await listAnalyzerJobs(cameraId);
        const currentJob = refreshedJobs[0] ?? latestJob;
        const payload = await getZoneAnalysis({
          cameraId,
          zoneId,
          start,
        });
        if (!active) return;
        setLatestJobStatus(currentJob?.status ?? null);
        setData(payload);
        setError(null);
      } catch (err) {
        if (!active) return;
        setData(null);
        setLatestJobStatus(null);
        setError(err instanceof Error ? err.message : "Failed to load zone analysis.");
      } finally {
        if (!active) return;
        setLoading(false);
      }
    }

    setLoading(true);
    void load();
    const id = window.setInterval(() => {
      void load();
    }, 8000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [autoTriggerJob, cameraId, isPageVisible, selectedZoneId, start]);

  if (loading && !data) {
    return <p className="text-sm text-muted-foreground">Loading zone analysis...</p>;
  }

  if (latestJobStatus === "pending" || latestJobStatus === "running") {
    return <p className="text-sm text-muted-foreground">Zone analysis is running on the latest CCTV frames...</p>;
  }

  if (error) {
    return <p className="text-sm text-muted-foreground">{error}</p>;
  }

  if (!data) {
    return <p className="text-sm text-muted-foreground">Zone analysis is not available yet for this camera.</p>;
  }

  if (!hasMeaningfulZoneAnalysis(data)) {
    return (
      <p className="text-sm text-muted-foreground">
        No completed zone-analysis results are available yet for this camera. Start analytics from Zone Configuration or wait
        for the current analyzer job to finish.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Zones Monitored" value={data.summary.total_zones ?? 0} hint="Configured zones" />
        <StatTile label="Tracked Paths" value={data.summary.tracked_paths ?? 0} hint="People + staff trajectories" />
        <StatTile label="Staff Tracks" value={data.summary.estimated_staff_tracks ?? 0} hint="Estimated from service-zone behavior" />
        <StatTile
          label="Peak Congestion Zone"
          value={data.summary.peak_congestion_zone?.zone_name ?? "N/A"}
          hint={data.summary.peak_congestion_zone?.congestion_level ?? "No congestion data"}
        />
      </div>

      <div className="overflow-x-auto rounded-xl border border-border/70 bg-white/80">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Zone</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Current</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Peak</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Avg</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Capacity</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Utilization</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Level</th>
            </tr>
          </thead>
          <tbody>
            {data.congestion_by_zone.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-muted-foreground" colSpan={7}>
                  No congestion metrics yet.
                </td>
              </tr>
            ) : (
              data.congestion_by_zone.map((zone) => (
                <tr key={zone.zone_id} className="border-t border-border/50">
                  <td className="px-3 py-2 text-foreground">
                    {zone.zone_name}
                    {zone.is_service_zone ? <span className="ml-1 text-[10px] text-indigo-600">(service)</span> : null}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-sky-700">{zone.current_count}</td>
                  <td className="px-3 py-2 tabular-nums text-slate-700">{zone.peak_count}</td>
                  <td className="px-3 py-2 tabular-nums text-slate-700">{zone.avg_count.toFixed(1)}</td>
                  <td className="px-3 py-2 tabular-nums text-slate-700">{zone.capacity ?? "-"}</td>
                  <td className="px-3 py-2 tabular-nums text-slate-700">{toPercentText(zone.current_utilization_pct)}</td>
                  <td className={`px-3 py-2 font-semibold capitalize ${levelTone(zone.congestion_level)}`}>{zone.congestion_level}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="overflow-x-auto rounded-xl border border-border/70 bg-white/80">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Track</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Type</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Dwell</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Path</th>
              </tr>
            </thead>
            <tbody>
              {data.tracked_paths.length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-muted-foreground" colSpan={4}>
                    No tracked paths available yet.
                  </td>
                </tr>
              ) : (
                data.tracked_paths.slice(0, 12).map((track) => (
                  <tr key={track.track_key} className="border-t border-border/50">
                    <td className="px-3 py-2 text-foreground">#{track.track_id}</td>
                    <td className="px-3 py-2 capitalize text-slate-700">{track.track_type}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-700">{track.estimated_dwell_seconds.toFixed(1)}s</td>
                    <td className="px-3 py-2 text-slate-700">{track.zone_sequence_names.join(" -> ") || "-"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="overflow-x-auto rounded-xl border border-border/70 bg-white/80">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Zone</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Staff</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">People</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Staff Ratio</th>
              </tr>
            </thead>
            <tbody>
              {data.service_staff_presence.length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-muted-foreground" colSpan={4}>
                    No staff/service presence metrics yet.
                  </td>
                </tr>
              ) : (
                data.service_staff_presence.map((zone) => (
                  <tr key={zone.zone_id} className="border-t border-border/50">
                    <td className="px-3 py-2 text-foreground">
                      {zone.zone_name}
                      {zone.is_service_zone ? <span className="ml-1 text-[10px] text-indigo-600">(service)</span> : null}
                    </td>
                    <td className="px-3 py-2 tabular-nums text-indigo-700">{zone.staff_tracks}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-700">{zone.person_tracks}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-700">{staffRatioPercent(zone.staff_presence_ratio)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

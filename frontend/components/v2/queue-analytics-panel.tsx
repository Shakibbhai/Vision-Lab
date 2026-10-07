"use client";

import { useEffect, useState, useMemo, useRef, useCallback } from "react";
import { 
  type QueueJob,
  type QueueResult,
  type Zone,
  listQueueJobs,
  listQueueCameras,
  getQueueResult,
  runQueueAnalysis,
} from "@/lib/api";
import { usePageVisibility } from "@/hooks/use-page-visibility";
import { parseSelectedZoneId } from "@/lib/zone-selection";
import { MiniLineChart, StatTile } from "@/components/v2/ui";

function pickBestQueueJob(jobs: QueueJob[]): QueueJob | null {
  if (jobs.length === 0) return null;

  const latestJob = jobs[0];

  if ((latestJob.status === "running" || latestJob.status === "pending") && latestJob.processed_frames >= 0) {
    return latestJob;
  }

  const completedWithFrames = jobs.find((job) => job.status === "completed" && job.processed_frames > 0);
  if (completedWithFrames) return completedWithFrames;

  const anyWithFrames = jobs.find((job) => job.processed_frames > 0);
  if (anyWithFrames) return anyWithFrames;

  return latestJob;
}

/** Check if a job is "recent" enough that we don't need to trigger a new one */
function isJobRecent(job: QueueJob): boolean {
  if (job.status === "running" || job.status === "pending") return true;
  // Consider completed jobs older than 2 minutes as stale
  const createdAt = new Date(job.created_at).getTime();
  const now = Date.now();
  return (now - createdAt) < 2 * 60 * 1000;
}

export function QueueAnalyticsPanel({
  cameraId,
  zones = [],
  selectedZoneId = "",
}: {
  cameraId: number;
  zones?: Zone[];
  selectedZoneId?: string;
}) {
  const [queueData, setQueueData] = useState<QueueResult | null>(null);
  const [latestStatus, setLatestStatus] = useState<string | null>(null);
  const [showingHistoricalResult, setShowingHistoricalResult] = useState(false);
  const [loading, setLoading] = useState(true);
  const isPageVisible = usePageVisibility();
  const triggerAttempted = useRef(false);

  /** Auto-trigger a queue analysis for the selected zone */
  const autoTriggerJob = useCallback(async (zoneId: number | null) => {
    if (triggerAttempted.current) return;
    triggerAttempted.current = true;

    try {
      // Fall back to the first configured zone when no zone filter is selected.
      const targetZoneId = zoneId ?? zones[0]?.id;
      if (!targetZoneId) return;

      // Get camera frame time range from queue cameras API
      const cameras = await listQueueCameras();
      const cam = cameras.find((c) => c.id === cameraId);
      if (!cam?.first_frame_time || !cam?.latest_frame_time) return;

      // Use the actual frame range from the database. Append 'Z' to force UTC parsing natively if missing
      const startTime = new Date(cam.first_frame_time.endsWith('Z') ? cam.first_frame_time : `${cam.first_frame_time}Z`);
      const latestTime = new Date(cam.latest_frame_time.endsWith('Z') ? cam.latest_frame_time : `${cam.latest_frame_time}Z`);

      await runQueueAnalysis({
        camera_id: cameraId,
        zone_id: targetZoneId,
        start: startTime.toISOString(),
        end: latestTime.toISOString(),
      });
    } catch (err) {
      console.warn("Auto-trigger queue analysis failed:", err);
    }
  }, [cameraId, zones]);

  useEffect(() => {
    if (!isPageVisible) return undefined;
    let active = true;
    triggerAttempted.current = false;

    async function fetchData() {
      try {
        const zoneId = parseSelectedZoneId(selectedZoneId);

        // Fetch jobs scoped to the current camera and selected zone (if any).
        const jobs = await listQueueJobs(cameraId, zoneId ?? undefined);
        const latestJob = jobs[0] ?? null;

        // Auto-trigger if no recent job exists
        if (!latestJob || !isJobRecent(latestJob)) {
          await autoTriggerJob(zoneId);
          // Re-fetch after triggering
          const refreshedJobs = await listQueueJobs(cameraId, zoneId ?? undefined);
          const displayJob = pickBestQueueJob(refreshedJobs);
          if (displayJob) {
            const result = await getQueueResult(displayJob.id);
            if (active) {
              setLatestStatus(refreshedJobs[0]?.status ?? null);
              setShowingHistoricalResult(false);
              setQueueData(result);
            }
          } else if (active) {
            setLatestStatus(null);
            setShowingHistoricalResult(false);
            setQueueData(null);
          }
          return;
        }

        const displayJob = pickBestQueueJob(jobs);

        if (displayJob) {
          const result = await getQueueResult(displayJob.id);
          if (active) {
            setLatestStatus(latestJob?.status ?? null);
            setShowingHistoricalResult(Boolean(latestJob && latestJob.id !== displayJob.id));
            setQueueData(result);
          }
        } else if (active) {
          setLatestStatus(null);
          setShowingHistoricalResult(false);
          setQueueData(null);
        }
      } catch (err) {
        console.error("Failed to load queue analytics", err);
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void fetchData();
    const id = setInterval(fetchData, 5000); // Poll every 5s for faster updates
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [autoTriggerJob, cameraId, isPageVisible, selectedZoneId]);

  const zone = useMemo(() => {
    if (!queueData) return null;
    return zones.find((z) => z.id === queueData.zone_id) || null;
  }, [queueData, zones]);

  const series = useMemo(() => {
    if (!queueData) return [];
    if (queueData.frame_results.length > 0) {
      return queueData.frame_results.map((f) => f.queue_count);
    }
    if (queueData.current_line_length != null) {
      return [queueData.current_line_length];
    }
    return [];
  }, [queueData]);

  if (loading && !queueData) {
    return <p className="text-sm text-muted-foreground animate-pulse">Loading queue data...</p>;
  }

  if (!queueData) {
    return <p className="text-sm text-muted-foreground">Queue analytics not available yet for this selection.</p>;
  }

  const formatWaitTime = (sec: number | null) => {
    if (sec == null) return "Estimating...";
    if (sec < 60) return `${Math.round(sec)}s`;
    return `${(sec / 60).toFixed(1)}m`;
  };

  const waitTimeStr = formatWaitTime(queueData.avg_wait_time_sec);
  const expectedWaitTimeStr = zone?.expected_wait_time_sec != null ? formatWaitTime(zone.expected_wait_time_sec) : null;

  const waitTimeLabel = expectedWaitTimeStr
    ? `${waitTimeStr} / ${expectedWaitTimeStr}`
    : waitTimeStr;

  const isOverWaitTime = queueData.avg_wait_time_sec != null && zone?.expected_wait_time_sec != null
     ? queueData.avg_wait_time_sec > zone.expected_wait_time_sec
     : false;

  const currentLineLength = queueData.current_line_length
    ?? queueData.frame_results[queueData.frame_results.length - 1]?.queue_count
    ?? null;
  const avgLineLength = queueData.avg_line_length ?? queueData.avg_queue_count;

  const isOverCapacity = zone?.capacity != null && currentLineLength != null
    ? currentLineLength > zone.capacity
    : false;
  const numberOverCapacity = queueData.number_over_capacity ?? 0;

  return (
    <div className="space-y-3">
      {showingHistoricalResult && latestStatus && latestStatus !== "completed" ? (
        <p className="text-xs text-amber-700">
          Showing last completed queue result while latest job is {latestStatus}.
        </p>
      ) : null}

      {latestStatus === "running" ? (
        <p className="text-xs text-emerald-600 animate-pulse">
          ● Live — analyzing queue in real time…
        </p>
      ) : null}

      {isOverCapacity ? (
        <p className="text-xs font-bold text-rose-600 animate-pulse">
          ⚠ Over Capacity — {numberOverCapacity} {numberOverCapacity === 1 ? "person" : "people"} waiting beyond limit
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className={isOverWaitTime ? "text-rose-600 font-bold" : ""}>
          <StatTile 
            label="Avg Wait Time" 
            value={waitTimeLabel} 
            hint={expectedWaitTimeStr ? "Curr / Target" : "Estimated delay"} 
          />
        </div>
        <div className={isOverCapacity ? "text-rose-600 font-bold" : ""}>
          <StatTile 
            label="Line Length" 
            value={currentLineLength != null && zone?.capacity != null
              ? `${currentLineLength} / ${zone.capacity}`
              : currentLineLength ?? "N/A"}
            hint={zone?.capacity != null ? "Current / Capacity" : "Current queue size"} 
          />
        </div>
        <StatTile 
          label="Max Line Length" 
          value={queueData.max_queue_count} 
          hint="Peak queue size" 
        />
        <StatTile 
          label="Avg Line Length" 
          value={Math.round(avgLineLength * 10) / 10} 
          hint="Across analyzed frames" 
        />
      </div>

      <div className="mt-3 rounded-xl border border-border/70 bg-white/80 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">Queue Count History</p>
        <MiniLineChart values={series} />
      </div>
    </div>
  );
}

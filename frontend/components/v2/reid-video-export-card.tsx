"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2, PlaySquare, Users, Video } from "lucide-react";
import { type ReidVideoJob, getReidVideo, startReidVideo } from "@/lib/api";

const POLL_MS = 2000;

export function ReidVideoExportCard({ cameraId, zoneId }: { cameraId: number; zoneId: number | null }) {
  const [job, setJob] = useState<ReidVideoJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => {
    stopPolling();
    setJob(null);
    setError(null);
    return stopPolling;
  }, [cameraId, zoneId]);

  const handleGenerate = async () => {
    stopPolling();
    setStarting(true);
    setError(null);
    try {
      const started = await startReidVideo(cameraId, zoneId);
      setJob(started);
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getReidVideo(started.job_id);
          setJob(latest);
          if (latest.status === "completed" || latest.status === "failed") stopPolling();
        } catch {
          // Ignore transient polling errors; the next tick retries.
        }
      }, POLL_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start Re-ID video");
    } finally {
      setStarting(false);
    }
  };

  const busy = starting || job?.status === "queued" || job?.status === "running";
  const videoUrl = job?.download_url ? `/api/backend${job.download_url}` : null;
  const percent = Math.round((job?.progress ?? 0) * 100);

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <PlaySquare className="h-5 w-5 text-emerald-700" />
        <div className="mr-auto">
          <p className="text-sm font-semibold text-emerald-900">Person Re-ID Video</p>
          <p className="text-xs text-emerald-800/80">
            Processes the whole uploaded video with the current Re-ID models and labels every person.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleGenerate()}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Video className="h-4 w-4" />}
          {busy ? "Processing..." : job?.status === "completed" ? "Generate Again" : "Generate Re-ID Video"}
        </button>
      </div>

      {busy && job ? (
        <div className="space-y-1">
          <div className="h-2 w-full overflow-hidden rounded-full bg-emerald-100">
            <div className="h-full rounded-full bg-emerald-600 transition-all" style={{ width: `${percent}%` }} />
          </div>
          <p className="text-xs text-emerald-800">Processing video... {percent}%</p>
        </div>
      ) : null}

      {error || job?.status === "failed" ? (
        <p className="text-xs font-medium text-destructive">{error ?? job?.error ?? "Re-ID video failed"}</p>
      ) : null}

      {job?.status === "completed" && videoUrl ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1 text-xs font-semibold text-emerald-900">
              <Users className="h-4 w-4" /> {job.unique_persons} unique person{job.unique_persons === 1 ? "" : "s"}
            </span>
            <a
              href={videoUrl}
              download={`person_reid_camera${job.camera_id}.mp4`}
              className="ml-auto flex items-center gap-1.5 rounded-md border border-emerald-600 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-50"
            >
              <Download className="h-4 w-4" /> Download Video
            </a>
          </div>
          <video
            key={videoUrl}
            src={videoUrl}
            controls
            playsInline
            preload="metadata"
            className="aspect-video w-full rounded-lg border border-emerald-100 bg-black object-contain"
          />
        </div>
      ) : null}
    </div>
  );
}

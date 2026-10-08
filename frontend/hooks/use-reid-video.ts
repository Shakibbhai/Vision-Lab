"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { type ReidVideoJob, getLatestReidVideo, getReidVideo, startReidVideo } from "@/lib/api";

const POLL_MS = 2000;

/** Saved / in-progress person Re-ID video for an uploaded source, with progress polling. */
export function useReidVideo(cameraId: number, zoneId: number | null, enabled = true) {
  const [job, setJob] = useState<ReidVideoJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const follow = useCallback(
    (current: ReidVideoJob) => {
      stopPolling();
      setJob(current);
      if (current.status !== "queued" && current.status !== "running") return;
      pollRef.current = setInterval(async () => {
        try {
          const latest = await getReidVideo(current.job_id);
          setJob(latest);
          if (latest.status === "completed" || latest.status === "failed") stopPolling();
        } catch {
          // Ignore transient polling errors; the next tick retries.
        }
      }, POLL_MS);
    },
    [stopPolling],
  );

  useEffect(() => {
    stopPolling();
    setJob(null);
    setError(null);
    if (!enabled) return stopPolling;
    let active = true;
    getLatestReidVideo(cameraId, zoneId)
      .then((saved) => {
        if (active && saved) follow(saved);
      })
      .catch(() => {
        // No saved result yet is the normal case; errors surface when the user generates.
      });
    return () => {
      active = false;
      stopPolling();
    };
  }, [cameraId, zoneId, enabled, follow, stopPolling]);

  const generate = useCallback(
    async (force = false) => {
      setStarting(true);
      setError(null);
      try {
        const started = await startReidVideo(cameraId, zoneId, force);
        follow(started);
        return started;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to start Re-ID video");
        return null;
      } finally {
        setStarting(false);
      }
    },
    [cameraId, zoneId, follow],
  );

  const busy = starting || job?.status === "queued" || job?.status === "running";
  const downloadUrl = job?.status === "completed" && job.download_url ? `/api/backend${job.download_url}` : null;
  return { job, error, busy, downloadUrl, generate };
}

/** Starts a browser download of a same-origin URL. */
export function triggerDownload(url: string, filename: string) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

"use client";

import { useEffect, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { triggerDownload, useReidVideo } from "@/hooks/use-reid-video";

/** Source-card button: downloads the saved Re-ID video, processing the upload first when needed. */
export function ReidDownloadButton({ cameraId, cameraName }: { cameraId: number; cameraName: string }) {
  const { job, error, busy, downloadUrl, generate } = useReidVideo(cameraId, null);
  const [pending, setPending] = useState(false);
  const filename = `${cameraName.replace(/[^\w-]+/g, "_") || `camera${cameraId}`}_person_reid.mp4`;

  useEffect(() => {
    if (!pending) return;
    if (downloadUrl) {
      triggerDownload(downloadUrl, filename);
      setPending(false);
    } else if (job?.status === "failed" || error) {
      setPending(false);
    }
  }, [pending, downloadUrl, job?.status, error, filename]);

  const percent = Math.round((job?.progress ?? 0) * 100);
  const title = busy
    ? `Processing Re-ID video... ${percent}%`
    : error || job?.status === "failed"
      ? `Re-ID video failed: ${error ?? job?.error ?? "unknown error"}`
      : downloadUrl
        ? "Download saved Re-ID video"
        : "Process with Re-ID and download video";

  return (
    <button
      type="button"
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-emerald-600/40 bg-emerald-500/10 text-emerald-700 transition hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60"
      title={title}
      onClick={(e) => {
        e.stopPropagation();
        if (downloadUrl) {
          triggerDownload(downloadUrl, filename);
          return;
        }
        setPending(true);
        void generate(false);
      }}
      disabled={busy}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
    </button>
  );
}

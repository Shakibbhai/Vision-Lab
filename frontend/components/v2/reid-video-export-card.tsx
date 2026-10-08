"use client";

import { Download, Loader2, PlaySquare, RefreshCcw, Users, Video } from "lucide-react";
import { useReidVideo } from "@/hooks/use-reid-video";

export function ReidVideoExportCard({ cameraId, zoneId }: { cameraId: number; zoneId: number | null }) {
  const { job, error, busy, downloadUrl, generate } = useReidVideo(cameraId, zoneId);
  const percent = Math.round((job?.progress ?? 0) * 100);
  const saved = job?.status === "completed" && downloadUrl;

  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <PlaySquare className="h-5 w-5 text-emerald-700" />
        <div className="mr-auto">
          <p className="text-sm font-semibold text-emerald-900">Person Re-ID Video</p>
          <p className="text-xs text-emerald-800/80">
            {saved
              ? "Saved result: plays and downloads without running the GPU again."
              : "Processes the whole uploaded video once with the Re-ID models and saves the result."}
          </p>
        </div>
        {saved ? (
          <button
            type="button"
            onClick={() => void generate(true)}
            disabled={busy}
            title="Process the video again with the current settings"
            className="flex items-center gap-1.5 rounded-md border border-emerald-600 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCcw className="h-4 w-4" /> Re-process
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void generate(false)}
            disabled={busy}
            className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Video className="h-4 w-4" />}
            {busy ? "Processing..." : "Generate Re-ID Video"}
          </button>
        )}
      </div>

      {busy && job ? (
        <div className="space-y-1">
          <div className="h-2 w-full overflow-hidden rounded-full bg-emerald-100">
            <div className="h-full rounded-full bg-emerald-600 transition-all" style={{ width: `${percent}%` }} />
          </div>
          <p className="text-xs text-emerald-800">Processing video on the GPU... {percent}%</p>
        </div>
      ) : null}

      {error || job?.status === "failed" ? (
        <p className="text-xs font-medium text-destructive">{error ?? job?.error ?? "Re-ID video failed"}</p>
      ) : null}

      {saved && job ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1 text-xs font-semibold text-emerald-900">
              <Users className="h-4 w-4" /> {job.unique_persons} unique person{job.unique_persons === 1 ? "" : "s"}
            </span>
            <a
              href={downloadUrl}
              download={`person_reid_camera${job.camera_id}.mp4`}
              className="ml-auto flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-700"
            >
              <Download className="h-4 w-4" /> Download Video
            </a>
          </div>
          <video
            key={downloadUrl}
            src={downloadUrl}
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

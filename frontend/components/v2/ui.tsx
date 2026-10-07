"use client";

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { uploadBrowserWebcamFrame, type RealtimeDetectionBox, type Zone } from "@/lib/api";
import type { SourceStatus } from "@/hooks/use-sources-data";
import { isZoneFilterSelected } from "@/lib/zone-selection";
import { cn } from "@/lib/utils";

export type AnalyticsMode =
  | "person_count"
  | "queue"
  | "entry_exit_count"
  | "person_reid"
  | "facial_expression"
  | "trajectory_heatmap"
  | "face_recognition";

export const ANALYTICS_MODES: Array<{ id: AnalyticsMode; label: string }> = [
  { id: "person_count", label: "Person Count" },
  { id: "trajectory_heatmap", label: "Trajectory Heatmap" },
  { id: "facial_expression", label: "Facial Expression" },
  { id: "face_recognition", label: "Face Recognition" },
  { id: "queue", label: "Queue Management" },
  { id: "entry_exit_count", label: "Entry/Exit Count" },
  { id: "person_reid", label: "Person ReID" },
];

function backendAnalyticsMode(mode: AnalyticsMode): AnalyticsMode | null {
  if (mode === "trajectory_heatmap" || mode === "facial_expression" || mode === "person_reid" || mode === "face_recognition") {
    return mode;
  }
  return null;
}

function shouldUseBackendZoneFilter(mode: AnalyticsMode): boolean {
  return mode === "trajectory_heatmap" || mode === "facial_expression" || mode === "person_reid" || mode === "face_recognition";
}

function readZonePolygons(zone: Zone): Array<Array<{ x: number; y: number }>> {
  const evaluatePoints = (pts: unknown) => {
    if (!Array.isArray(pts)) return [];
    return pts
      .map((point) => {
        if (!point || typeof point !== "object") return null;
        let x = Number((point as { x?: number }).x);
        let y = Number((point as { y?: number }).y);
        if (Number.isNaN(x) || Number.isNaN(y)) return null;

        if (x > 2 || y > 2) {
          x = x / 1920;
          y = y / 1080;
        }

        return { x: x * 100, y: y * 100 };
      })
      .filter((point): point is { x: number; y: number } => point !== null);
  };

  const rawPolygons = zone.polygon?.polygons;
  if (Array.isArray(rawPolygons)) {
    return rawPolygons.map(evaluatePoints).filter((poly) => poly.length >= 3);
  }

  const legacyPoints = zone.polygon?.points;
  const legacyPoly = evaluatePoints(legacyPoints);
  return legacyPoly.length >= 3 ? [legacyPoly] : [];
}

function readLinePoints(zone: Zone, key: "entry_line" | "exit_line"):
  | {
      x1: number;
      y1: number;
      x2: number;
      y2: number;
    }
  | null {
  const raw = zone.polygon?.[key];
  if (!raw || typeof raw !== "object") return null;

  const points = (raw as { points?: unknown }).points;
  if (!Array.isArray(points) || points.length < 2) return null;

  const parsed = points
    .slice(0, 2)
    .map((point) => {
      if (!point || typeof point !== "object") return null;
      let x = Number((point as { x?: number }).x);
      let y = Number((point as { y?: number }).y);
      if (Number.isNaN(x) || Number.isNaN(y)) return null;

      if (x > 2 || y > 2) {
        x = x / 1920;
        y = y / 1080;
      }
      return { x: x * 100, y: y * 100 };
    })
    .filter((point): point is { x: number; y: number } => point !== null);

  if (parsed.length < 2) return null;

  return {
    x1: parsed[0].x,
    y1: parsed[0].y,
    x2: parsed[1].x,
    y2: parsed[1].y,
  };
}

function canvasToJpegBlob(canvas: HTMLCanvasElement, quality = 0.82): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality);
  });
}

export function sourceStatusLabel(status: SourceStatus): string {
  if (status === "live") return "Live";
  if (status === "paused") return "Paused";
  return "Offline";
}

function sourceStatusTone(status: SourceStatus): string {
  if (status === "live") return "border-emerald-600/70 bg-emerald-500/10 text-emerald-700";
  if (status === "paused") return "border-orange-500/70 bg-orange-400/10 text-orange-700";
  return "border-red-500/70 bg-red-500/10 text-red-700";
}

export function PanelCard({
  title,
  subtitle,
  right,
  className,
  children,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("dashboard-panel rounded-2xl p-4 md:p-5", className)}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">{title}</p>
          {subtitle ? <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p> : null}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export function StatusPill({ status }: { status: SourceStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
        sourceStatusTone(status),
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          status === "live" ? "bg-emerald-600" : status === "paused" ? "bg-orange-600" : "bg-red-600",
        )}
      />
      {sourceStatusLabel(status)}
    </span>
  );
}

export function CameraFeed({
  cameraId,
  title,
  status,
  resolution,
  fps,
  zones = [],
  detectionBoxes = [],
  hasLiveFrame = false,
  compact = false,
  staticFirstFrame = false,
  zoneId,
  analyticsMode,
  sourceType,
}: {
  cameraId: number;
  title: string;
  status: SourceStatus;
  resolution: string;
  fps: number;
  zones?: Zone[];
  detectionBoxes?: RealtimeDetectionBox[];
  hasLiveFrame?: boolean;
  compact?: boolean;
  staticFirstFrame?: boolean;
  zoneId?: string;
  analyticsMode?: AnalyticsMode | null;
  sourceType?: "RTSP" | "Video File" | "Webcam" | string;
}) {
  const [streamErrored, setStreamErrored] = useState(false);
  const [streamReady, setStreamReady] = useState(false);
  const [snapshotErrored, setSnapshotErrored] = useState(false);
  const [browserWebcamReady, setBrowserWebcamReady] = useState(false);
  const [browserWebcamError, setBrowserWebcamError] = useState("");
  const [snapshotTick, setSnapshotTick] = useState(0);
  const [streamNonce, setStreamNonce] = useState(0);
  const browserWebcamRef = useRef<HTMLVideoElement | null>(null);
  const browserWebcamCaptureRef = useRef<HTMLVideoElement | null>(null);
  const browserWebcamCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const browserWebcamStreamRef = useRef<MediaStream | null>(null);
  const browserWebcamUploadInFlightRef = useRef(false);
  const streamLoadedRef = useRef(false);
  const statusRef = useRef(status);
  statusRef.current = status;

  const activeAnalyticsMode = analyticsMode ?? null;
  const analyticsModeParam = activeAnalyticsMode ? backendAnalyticsMode(activeAnalyticsMode) : null;
  const hasZoneFilter = isZoneFilterSelected(zoneId);
  const filteredZoneId = hasZoneFilter ? zoneId ?? "" : "";

  // For facial_expression mode, if no zone is explicitly selected, automatically fall back to the
  // first configured zone. Without a zone, the backend skips DeepFace detection entirely.
  const facialExpressionFallbackZoneId = useMemo(() => {
    if (activeAnalyticsMode !== "facial_expression") return "";
    if (hasZoneFilter) return "";
    const firstZone = zones.find((z) => z.id);
    return firstZone ? String(firstZone.id) : "";
  }, [activeAnalyticsMode, hasZoneFilter, zones]);

  const backendZoneFilter = useMemo(() => {
    if (!activeAnalyticsMode || !shouldUseBackendZoneFilter(activeAnalyticsMode)) return "";
    if (hasZoneFilter) return filteredZoneId;
    // For facial_expression: use fallback first zone if no zone is selected
    if (activeAnalyticsMode === "facial_expression") return facialExpressionFallbackZoneId;
    return "";
  }, [activeAnalyticsMode, facialExpressionFallbackZoneId, filteredZoneId, hasZoneFilter]);

  const selectedZoneNumber = hasZoneFilter ? Number(filteredZoneId) : null;

  const streamUrlBase = `/api/stream/cameras/${cameraId}/stream.mjpg`;
  const streamParams = new URLSearchParams({
    v: String(streamNonce),
  });
  if (analyticsModeParam) {
    streamParams.set("analytics_mode", analyticsModeParam);
  }
  if (backendZoneFilter) {
    streamParams.set("zone_id", backendZoneFilter);
  }
  const streamUrl = `${streamUrlBase}?${streamParams.toString()}`;

  const snapshotUrlBase = staticFirstFrame
    ? `/api/backend/api/monitoring/cameras/${cameraId}/first-frame`
    : `/api/backend/api/monitoring/cameras/${cameraId}/frame`;
  const snapshotParams = new URLSearchParams();
  if (analyticsModeParam) {
    snapshotParams.set("analytics_mode", analyticsModeParam);
  }
  if (!staticFirstFrame) {
    snapshotParams.set("t", String(snapshotTick));
  }
  if (backendZoneFilter) {
    snapshotParams.set("zone_id", backendZoneFilter);
  }
  const snapshotUrl = `${snapshotUrlBase}?${snapshotParams.toString()}`;

  const parsedZones = useMemo(() => {
    let filteredZones = zones;
    if (hasZoneFilter) {
      filteredZones = zones.filter((z) => z.id.toString() === filteredZoneId);
    }
    const results = filteredZones.map((zone) => {
      const polygons = readZonePolygons(zone);
      if (polygons.length === 0) return null;

      return {
        id: zone.id,
        name: zone.name,
        polygons,
        entryLine: readLinePoints(zone, "entry_line"),
        exitLine: readLinePoints(zone, "exit_line"),
      };
    });
    return results.filter((zone): zone is NonNullable<typeof results[number]> => zone !== null);
  }, [filteredZoneId, hasZoneFilter, zones]);

  const parsedDetectionBoxes = useMemo(
    () =>
      detectionBoxes
        .map((box, index) => {
          if (selectedZoneNumber !== null && Number(box.zone_id) !== selectedZoneNumber) {
            return null;
          }
          const x1 = Number(box.x1);
          const y1 = Number(box.y1);
          const x2 = Number(box.x2);
          const y2 = Number(box.y2);
          if (
            Number.isNaN(x1) ||
            Number.isNaN(y1) ||
            Number.isNaN(x2) ||
            Number.isNaN(y2) ||
            x2 <= x1 ||
            y2 <= y1
          ) {
            return null;
          }
          return {
            key: `${index}-${x1}-${y1}-${x2}-${y2}`,
            x: Math.max(0, Math.min(100, x1 * 100)),
            y: Math.max(0, Math.min(100, y1 * 100)),
            width: Math.max(0, Math.min(100, (x2 - x1) * 100)),
            height: Math.max(0, Math.min(100, (y2 - y1) * 100)),
            inZone: Boolean(box.in_zone),
          };
        })
        .filter(
          (
            box,
          ): box is {
            key: string;
            x: number;
            y: number;
            width: number;
            height: number;
            inZone: boolean;
          } => box !== null,
        ),
    [detectionBoxes, selectedZoneNumber],
  );

  const stopBrowserWebcam = useCallback(() => {
    const stream = browserWebcamStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      browserWebcamStreamRef.current = null;
    }

    if (browserWebcamRef.current) {
      browserWebcamRef.current.srcObject = null;
    }
    if (browserWebcamCaptureRef.current) {
      browserWebcamCaptureRef.current.srcObject = null;
    }

    setBrowserWebcamReady(false);
  }, []);

  const attachBrowserWebcam = useCallback((videoElement: HTMLVideoElement | null) => {
    const stream = browserWebcamStreamRef.current;
    if (!videoElement || !stream) return;

    if (videoElement.srcObject !== stream) {
      videoElement.srcObject = stream;
    }

    const playPromise = videoElement.play();
    if (playPromise) {
      void playPromise.catch(() => {});
    }
  }, []);

  const canUseLiveStream = status === "live" || hasLiveFrame || streamLoadedRef.current;
  const showStream = !staticFirstFrame && canUseLiveStream && !streamErrored;
  const showBrowserWebcam = sourceType === "Webcam" && browserWebcamReady && !streamReady;
  const showSnapshot = !showBrowserWebcam && (staticFirstFrame || !showStream || !streamReady);
  const showFallbackBackground = false;
  const showFrontendPersonBoxes = activeAnalyticsMode === "person_count" || activeAnalyticsMode === "queue";
  const showFrontendZones =
    activeAnalyticsMode === null ||
    activeAnalyticsMode === "person_count" ||
    activeAnalyticsMode === "queue" ||
    activeAnalyticsMode === "entry_exit_count" ||
    (activeAnalyticsMode === "face_recognition" && !hasZoneFilter);
  const mediaObjectClass = activeAnalyticsMode === "facial_expression" ? "object-cover" : "object-fill";
  const shouldIngestBrowserWebcam = sourceType === "Webcam" && !staticFirstFrame;
  const shouldRefreshSnapshot =
    showSnapshot &&
    !staticFirstFrame &&
    (status === "live" || hasLiveFrame || streamLoadedRef.current);

  useEffect(() => {
    setStreamErrored(false);
    setStreamReady(false);
    setSnapshotErrored(false);
    streamLoadedRef.current = false;
  }, [analyticsModeParam, cameraId, staticFirstFrame]);

  useEffect(() => {
    setSnapshotTick(0);
    setStreamNonce((value) => value + 1);
    setStreamReady(false);
    setSnapshotErrored(false);
    streamLoadedRef.current = false;
  }, [analyticsModeParam, backendZoneFilter, cameraId, staticFirstFrame]);

  useEffect(() => {
    if (sourceType !== "Webcam" || staticFirstFrame) {
      setBrowserWebcamError("");
      stopBrowserWebcam();
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setBrowserWebcamError("This browser cannot access the webcam.");
      setBrowserWebcamReady(false);
      return;
    }

    let cancelled = false;
    setBrowserWebcamError("");

    navigator.mediaDevices
      .getUserMedia({ video: true, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        browserWebcamStreamRef.current = stream;
        setBrowserWebcamReady(true);
        attachBrowserWebcam(browserWebcamRef.current);
        attachBrowserWebcam(browserWebcamCaptureRef.current);
      })
      .catch((err) => {
        setBrowserWebcamReady(false);
        setBrowserWebcamError(err instanceof Error ? err.message : "Unable to access the browser webcam.");
      });

    return () => {
      cancelled = true;
      stopBrowserWebcam();
    };
  }, [attachBrowserWebcam, cameraId, sourceType, staticFirstFrame, stopBrowserWebcam]);

  useEffect(() => {
    if (sourceType !== "Webcam") return;
    attachBrowserWebcam(browserWebcamRef.current);
    attachBrowserWebcam(browserWebcamCaptureRef.current);
  }, [attachBrowserWebcam, showBrowserWebcam, sourceType]);

  const uploadCurrentBrowserFrame = useCallback(async () => {
    const videoElement = browserWebcamCaptureRef.current;
    if (!videoElement || videoElement.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      return;
    }

    const frameWidth = videoElement.videoWidth;
    const frameHeight = videoElement.videoHeight;
    if (!frameWidth || !frameHeight) {
      return;
    }

    let canvas = browserWebcamCanvasRef.current;
    if (!canvas) {
      canvas = document.createElement("canvas");
      browserWebcamCanvasRef.current = canvas;
    }

    if (canvas.width !== frameWidth) {
      canvas.width = frameWidth;
    }
    if (canvas.height !== frameHeight) {
      canvas.height = frameHeight;
    }

    const context = canvas.getContext("2d", { alpha: false });
    if (!context) {
      return;
    }

    context.drawImage(videoElement, 0, 0, frameWidth, frameHeight);
    const frameBlob = await canvasToJpegBlob(canvas);
    if (!frameBlob) {
      return;
    }

    await uploadBrowserWebcamFrame({
      cameraId,
      frame: frameBlob,
      width: frameWidth,
      height: frameHeight,
    });
  }, [cameraId]);

  useEffect(() => {
    if (!shouldIngestBrowserWebcam || !browserWebcamReady) {
      return undefined;
    }

    let cancelled = false;
    const uploadFrame = async () => {
      if (cancelled || browserWebcamUploadInFlightRef.current) {
        return;
      }

      browserWebcamUploadInFlightRef.current = true;
      try {
        await uploadCurrentBrowserFrame();
      } catch {
        // Keep the preview usable while backend ingest reconnects.
      } finally {
        browserWebcamUploadInFlightRef.current = false;
      }
    };

    void uploadFrame();
    const id = window.setInterval(() => {
      void uploadFrame();
    }, 1000);

    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [browserWebcamReady, shouldIngestBrowserWebcam, uploadCurrentBrowserFrame]);

  useEffect(() => {
    if (!shouldRefreshSnapshot) return undefined;
    const id = window.setInterval(() => {
      setSnapshotTick((value) => value + 1);
    }, 1000);
    return () => window.clearInterval(id);
  }, [shouldRefreshSnapshot]);

  useEffect(() => {
    if (staticFirstFrame || !streamErrored) return undefined;
    const id = window.setTimeout(() => {
      if (statusRef.current === "live" || streamLoadedRef.current) {
        setStreamErrored(false);
        setStreamNonce((value) => value + 1);
      }
    }, 5000);
    return () => window.clearTimeout(id);
  }, [staticFirstFrame, streamErrored]);

  const showStatusMessage =
    !showBrowserWebcam &&
    ((!showStream && !showSnapshot) || (showSnapshot && snapshotErrored && !streamReady));
  const statusMessage =
    sourceType === "Webcam" && browserWebcamError
      ? browserWebcamError
      : sourceType === "Webcam" && snapshotErrored && !streamReady
        ? "Backend webcam frames are unavailable. Allow browser webcam access to see a live preview here."
        : status === "live"
          ? "Waiting for frames..."
          : "Stream unavailable";

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border border-border/70",
        compact ? "aspect-[16/9]" : "aspect-video",
      )}
    >
      {showSnapshot ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={`snapshot-${cameraId}-${staticFirstFrame ? "first" : "latest"}-${snapshotTick}`}
          src={snapshotUrl}
          alt={`${title} latest frame`}
          className={cn("absolute inset-0 h-full w-full", mediaObjectClass)}
          loading="lazy"
          onLoad={() => {
            setSnapshotErrored(false);
          }}
          onError={() => {
            setSnapshotErrored(true);
          }}
        />
      ) : null}

      {showBrowserWebcam ? (
        <video
          ref={browserWebcamRef}
          autoPlay
          playsInline
          muted
          className={cn("absolute inset-0 h-full w-full", mediaObjectClass)}
        />
      ) : null}

      {showStream ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={`stream-${cameraId}-${streamNonce}`}
          src={streamUrl}
          alt={`${title} live feed`}
          className={cn("absolute inset-0 h-full w-full", mediaObjectClass)}
          loading="eager"
          onLoad={() => {
            setStreamErrored(false);
            setStreamReady(true);
            streamLoadedRef.current = true;
          }}
          onError={() => {
            setStreamReady(false);
            setStreamErrored(true);
          }}
        />
      ) : null}

      {showFallbackBackground ? (
        <div className="h-full w-full bg-[linear-gradient(160deg,#111a23_0%,#182939_45%,#263d4f_100%)]" />
      ) : null}

      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.03)_1px,transparent_1px)] bg-[size:22px_22px]" />

      {showFrontendZones && parsedZones.length > 0 ? (
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
          {parsedZones.map((zone) => (
            <g key={zone.id}>
              {activeAnalyticsMode !== "entry_exit_count" ? (
                <>
                  {zone.polygons.map((poly, index) => (
                    <polygon
                      key={`${zone.id}-poly-${index}`}
                      points={poly.map((point) => `${point.x},${point.y}`).join(" ")}
                      fill="rgba(20, 184, 166, 0.15)"
                      stroke="rgba(20, 184, 166, 0.98)"
                      strokeWidth="1.2"
                      strokeDasharray="3 2"
                    />
                  ))}
                  <text
                    x={zone.polygons[0]?.[0]?.x ?? 2}
                    y={Math.max((zone.polygons[0]?.[0]?.y ?? 3) - 1.2, 2)}
                    fill="rgba(237, 253, 255, 0.95)"
                    fontSize="2.8"
                    fontWeight="700"
                  >
                    {zone.name}
                  </text>
                </>
              ) : null}
              {zone.entryLine ? (
                <line
                  x1={zone.entryLine.x1}
                  y1={zone.entryLine.y1}
                  x2={zone.entryLine.x2}
                  y2={zone.entryLine.y2}
                  stroke="rgba(16, 185, 129, 0.98)"
                  strokeWidth="0.9"
                  strokeDasharray="1.6 1.2"
                />
              ) : null}
              {zone.exitLine ? (
                <line
                  x1={zone.exitLine.x1}
                  y1={zone.exitLine.y1}
                  x2={zone.exitLine.x2}
                  y2={zone.exitLine.y2}
                  stroke="rgba(239, 68, 68, 0.98)"
                  strokeWidth="0.9"
                  strokeDasharray="1.6 1.2"
                />
              ) : null}
            </g>
          ))}
        </svg>
      ) : null}

      {parsedDetectionBoxes.length > 0 && (showStream || showSnapshot || showBrowserWebcam) && showFrontendPersonBoxes ? (
        <>
          {parsedDetectionBoxes.map((box) => (
            <div
              key={box.key}
              className="absolute border-[1.5px] transition-all duration-300"
              style={{
                left: `${box.x}%`,
                top: `${box.y}%`,
                width: `${box.width}%`,
                height: `${box.height}%`,
                borderColor: box.inZone ? "rgba(20, 184, 166, 0.8)" : "rgba(239, 68, 68, 0.6)",
                backgroundColor: box.inZone ? "rgba(20, 184, 166, 0.15)" : "transparent",
                boxShadow: box.inZone ? "0 0 10px rgba(20, 184, 166, 0.3)" : "none",
              }}
            >
              {box.inZone ? (
                <div className="absolute -top-5 left-0 rounded bg-teal-500/90 px-1.5 py-0.5 text-[0.6rem] font-bold tracking-wider text-white shadow-sm backdrop-blur-md">
                  PERSON
                </div>
              ) : null}
            </div>
          ))}
        </>
      ) : null}

      <div className="absolute left-2 top-2 inline-flex items-center gap-2 rounded-md bg-slate-900/70 px-2 py-1 text-[10px] font-semibold text-slate-100">
        <StatusPill status={status} />
        <span className="max-w-[140px] truncate">{title}</span>
      </div>

      <div className="absolute bottom-2 right-2 rounded-md bg-slate-900/70 px-2 py-1 text-[10px] text-slate-200">
        {resolution} • {fps}fps
      </div>

      {showBrowserWebcam ? (
        <div className="absolute bottom-2 left-2 rounded-md bg-slate-900/70 px-2 py-1 text-[10px] text-slate-200">
          Browser webcam preview
        </div>
      ) : null}

      <video
        ref={browserWebcamCaptureRef}
        autoPlay
        playsInline
        muted
        aria-hidden="true"
        className="pointer-events-none absolute h-0 w-0 opacity-0"
      />

      {showStatusMessage ? (
        <div className="absolute inset-0 grid place-items-center text-[11px] text-slate-200/90">
          {statusMessage}
        </div>
      ) : null}
    </div>
  );
}

export function MiniLineChart({ values, className }: { values: number[]; className?: string }) {
  const width = 420;
  const height = 110;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = Math.max(max - min, 1);

  const points = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * width;
      const y = height - ((value - min) / span) * (height - 10) - 5;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={cn("h-[110px] w-full", className)}>
      <polyline points={points} fill="none" stroke="hsl(var(--primary))" strokeWidth="2.4" />
      <polyline
        points={`${points} ${width},${height} 0,${height}`}
        fill="hsl(var(--primary) / 0.2)"
        stroke="none"
      />
    </svg>
  );
}

export function MultiZoneLineChart({
  series,
  className,
}: {
  series: { id: string | number; name: string; color: string; values: number[] }[];
  className?: string;
}) {
  const width = 420;
  const height = 110;
  const allValues = series.flatMap((line) => line.values);
  const max = Math.max(...allValues, 1);
  const min = Math.min(...allValues, 0);
  const span = Math.max(max - min, 1);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className={cn("h-[110px] w-full", className)}>
        {series.map((line) => {
          if (line.values.length === 0) return null;

          const points = line.values
            .map((value, index) => {
              const x = (index / Math.max(line.values.length - 1, 1)) * width;
              const y = height - ((value - min) / span) * (height - 10) - 5;
              return `${x},${y}`;
            })
            .join(" ");

          return (
            <polyline
              key={line.id}
              points={points}
              fill="none"
              stroke={line.color}
              strokeWidth="2.4"
              opacity="0.85"
            />
          );
        })}
      </svg>
    </div>
  );
}

export function MiniBarChart({ values, className }: { values: number[]; className?: string }) {
  const width = 420;
  const height = 110;
  const gap = 4;
  const barWidth = (width - gap * (values.length + 1)) / Math.max(values.length, 1);
  const max = Math.max(...values, 1);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className={cn("h-[110px] w-full", className)}>
      {values.map((value, index) => {
        const x = gap + index * (barWidth + gap);
        const barHeight = (value / max) * (height - 10);
        const y = height - barHeight - 2;
        return (
          <rect
            key={`${index}-${value}`}
            x={x}
            y={y}
            width={barWidth}
            height={barHeight}
            rx="4"
            fill="hsl(var(--secondary))"
            opacity="0.85"
          />
        );
      })}
    </svg>
  );
}

export function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-border/70 bg-white/80 p-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-primary">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

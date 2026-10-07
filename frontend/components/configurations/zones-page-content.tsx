"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCcw, Shapes, Slash, Move, Undo2, X, Trash2, Save, Maximize, Square } from "lucide-react";

import { type DrawMode, type PolygonCanvasHandle, type Pt, type Shape, PolygonCanvas } from "@/components/polygon-canvas";
import { ANALYTICS_MODES, type AnalyticsMode, PanelCard, StatusPill } from "@/components/v2/ui";
import { ZoneConfigDialog } from "@/components/zone-config-dialog";
import { useSourcesData } from "@/hooks/use-sources-data";
import {
  DASHBOARD_OVERLAY_CONFIG_KEY,
  getDashboardOverlayModes,
  mergeDashboardOverlayModes,
  normalizeDashboardOverlayConfig,
  readDashboardOverlayConfigFromStorage,
  removeDashboardOverlayZone,
  setDashboardOverlayModes,
  type DashboardOverlayConfig,
  writeDashboardOverlayConfigToStorage,
} from "@/lib/dashboard-overlay-config";
import {
  type Zone,
  createZone,
  deleteZone,
  getAnalyticsRunState,
  getRuntimeConfig,
  listZones,
  runAnalyzerAnalysis,
  runFacialExpressionRecognition,
  runFaceRecognition,
  getRealtimeMonitoring,
  startStream,
  startAnalyticsRun,
  stopStream,
  runQueueAnalysis,
  uploadBrowserWebcamFrame,
  updateRuntimeConfig,
} from "@/lib/api";

const ZONE_COLORS = [
  "#0f766e",
  "#ea580c",
  "#15803d",
  "#1d4ed8",
  "#dc2626",
  "#7c3aed",
];

type DrawPoint = { x: number; y: number };
type ImageSize = { width: number; height: number };
type ElementLine = {
  points: DrawPoint[];
};


type QueueRunStatus = "pending" | "running" | "completed" | "failed";
type LocalAnalyticsTask = {
  id: string;
  mode: AnalyticsMode;
  zoneName: string;
  status: QueueRunStatus;
  message: string;
  createdAt: string;
};
type QueuePanelRow = {
  id: string;
  label: string;
  status: QueueRunStatus;
  details: string;
  createdAt: string;
};
type DrawingTool = DrawMode;
type CanvasContextSignature = {
  cameraId: number | null;
  zoneId: number | null;
  creating: boolean;
};

function pointsEqual(a: Pt, b: Pt): boolean {
  return Math.abs(a[0] - b[0]) <= 1 && Math.abs(a[1] - b[1]) <= 1;
}

function shapesEqual(a: Shape, b: Shape): boolean {
  if (a.mode !== b.mode) return false;
  if (a.points.length !== b.points.length) return false;
  for (let idx = 0; idx < a.points.length; idx += 1) {
    if (!pointsEqual(a.points[idx], b.points[idx])) return false;
  }
  return true;
}

function extractUnsavedShapes(canvas: Shape[], saved: Shape[]): Shape[] {
  if (saved.length === 0) return canvas;
  if (canvas.length < saved.length) return canvas;

  for (let idx = 0; idx < saved.length; idx += 1) {
    if (!shapesEqual(canvas[idx], saved[idx])) {
      return canvas;
    }
  }
  return canvas.slice(saved.length);
}

function zonePolygons(zone: Zone): DrawPoint[][] {
  const rawPolygons = zone.polygon?.polygons;
  if (Array.isArray(rawPolygons)) {
    return rawPolygons.map(poly => {
      if (!Array.isArray(poly)) return [];
      return poly
        .map((point) => {
          if (!point || typeof point !== "object") return null;
          const x = Number((point as { x?: number }).x);
          const y = Number((point as { y?: number }).y);
          if (Number.isNaN(x) || Number.isNaN(y)) return null;
          return { x, y };
        })
        .filter((point): point is DrawPoint => point !== null);
    }).filter(poly => poly.length >= 3);
  }

  // Fallback to legacy single polygon format
  const rawPoints = zone.polygon?.points;
  if (!Array.isArray(rawPoints)) return [];
  const legacyPoly = rawPoints
    .map((point) => {
      if (!point || typeof point !== "object") return null;
      const x = Number((point as { x?: number }).x);
      const y = Number((point as { y?: number }).y);
      if (Number.isNaN(x) || Number.isNaN(y)) return null;
      return { x, y };
    })
    .filter((point): point is DrawPoint => point !== null);
  
  return legacyPoly.length >= 3 ? [legacyPoly] : [];
}

function extractLinePayload(payload: unknown): ElementLine | null {
  if (!payload || typeof payload !== "object") return null;
  const rawPoints = (payload as { points?: unknown }).points;
  if (!Array.isArray(rawPoints)) return null;

  const points = rawPoints
    .slice(0, 2)
    .map((point) => {
      if (!point || typeof point !== "object") return null;
      const x = Number((point as { x?: number }).x);
      const y = Number((point as { y?: number }).y);
      if (Number.isNaN(x) || Number.isNaN(y)) return null;
      return { x, y };
    })
    .filter((point): point is DrawPoint => point !== null);

  if (points.length < 2) return null;
  return { points };
}

function zoneEntryLine(zone: Zone): ElementLine | null {
  return extractLinePayload(zone.polygon?.entry_line);
}

function zoneExitLine(zone: Zone): ElementLine | null {
  return extractLinePayload(zone.polygon?.exit_line);
}

function zonePointsAreNormalized(points: DrawPoint[]): boolean {
  return points.every((point) => point.x >= -0.05 && point.x <= 1.05 && point.y >= -0.05 && point.y <= 1.05);
}

function toCanvasCoordinate(value: number, maxIndex: number): number {
  const clamped = Math.min(Math.max(value, 0), 1);
  return Math.round(clamped * Math.max(maxIndex, 1));
}

function toNormalizedCoordinate(value: number, maxIndex: number): number {
  const clamped = Math.min(Math.max(value, 0), Math.max(maxIndex, 1));
  return Number((clamped / Math.max(maxIndex, 1)).toFixed(5));
}

function zoneColorValue(zone: Zone): string {
  const color = zone.polygon?.color;
  return typeof color === "string" && color.trim() ? color : "hsl(var(--primary))";
}

function analyticsModeLabel(mode: AnalyticsMode): string {
  return ANALYTICS_MODES.find((item) => item.id === mode)?.label ?? mode;
}

// Redundant mode definition removed since ReID is now inside ANALYTICS_MODES globally.

function parseAnalysisTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const raw = value.trim();
  if (!raw) return null;
  const hasTimezone = /([zZ]|[+-]\d{2}:\d{2})$/.test(raw);
  const parsed = new Date(hasTimezone ? raw : `${raw}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function buildAnalysisWindow(latestFrameTimestamp: string | null | undefined): { start: string; end: string } {
  const anchor = parseAnalysisTimestamp(latestFrameTimestamp) ?? new Date();
  // Add a small buffer to avoid dropping the latest frame on sub-second boundaries.
  const end = new Date(anchor.getTime() + 60 * 1000);
  const start = new Date(anchor.getTime() - 30 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

function canvasToJpegBlob(canvas: HTMLCanvasElement, quality = 0.92): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality);
  });
}

async function waitForCapturedFrame(
  cameraId: number,
  timeoutMs = 30000,
): Promise<{ frame_timestamp: string | null } | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const monitoring = await getRealtimeMonitoring([cameraId]);
    const cam = monitoring.cameras.find((c) => c.camera_id === cameraId);
    if (cam?.frame_timestamp) {
      return cam;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

function queueStatusTone(status: QueueRunStatus): string {
  if (status === "completed") return "border-emerald-600/50 bg-emerald-500/10 text-emerald-700";
  if (status === "running") return "border-blue-600/50 bg-blue-500/10 text-blue-700";
  if (status === "pending") return "border-amber-600/50 bg-amber-500/10 text-amber-700";
  return "border-destructive/50 bg-destructive/10 text-destructive";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

async function loadDashboardOverlayConfig() {
  const payload = await getRuntimeConfig();
  const config = normalizeDashboardOverlayConfig(payload.config?.[DASHBOARD_OVERLAY_CONFIG_KEY]);
  if (Object.keys(config).length > 0) {
    writeDashboardOverlayConfigToStorage(config);
    return config;
  }
  return readDashboardOverlayConfigFromStorage();
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select";
}

type ZonesPageContentProps = {
  preferredCameraId?: number | null;
};

export function ZonesPageContent({ preferredCameraId: preferredCameraIdProp = null }: ZonesPageContentProps) {
  const {
    busy,
    error,
    notice,
    setError,
    setNotice,
    analyticsRunning,
    sources,
    refreshCore,
    refreshRealtime,
  } = useSourcesData({ pollRealtimeMs: 2000 });

  const [selectedCameraId, setSelectedCameraId] = useState<number | null>(null);
  const [preferredCameraId, setPreferredCameraId] = useState<number | null>(preferredCameraIdProp);
  const [zonesByCamera, setZonesByCamera] = useState<Record<number, Zone[]>>({});
  const [selectedZoneId, setSelectedZoneId] = useState<number | null>(null);
  const [isCreatingZone, setIsCreatingZone] = useState(false);
  const [zoneName, setZoneName] = useState("Zone A");
  const [zoneCapacity, setZoneCapacity] = useState("");
  const [zoneExpectedWaitTime, setZoneExpectedWaitTime] = useState("");
  const [zoneColor, setZoneColor] = useState(ZONE_COLORS[0]);
  const [imageSize, setImageSize] = useState<ImageSize | null>(null);
  const [canvasPolygons, setCanvasPolygons] = useState<Shape[]>([]);
  const [drawingTool, setDrawingTool] = useState<DrawingTool>("polygon");
  const [editMode, setEditMode] = useState(false);
  const [isCanvasFullscreen, setIsCanvasFullscreen] = useState(false);
  const [frameLoadError, setFrameLoadError] = useState("");
  const [frameVersion, setFrameVersion] = useState(0);
  const [browserWebcamFrameUrl, setBrowserWebcamFrameUrl] = useState("");

  const [analyticsMode, setAnalyticsMode] = useState<AnalyticsMode>("person_count");
  const [stagedAnalyticsTypes, setStagedAnalyticsTypes] = useState<AnalyticsMode[]>([]);
  const [localAnalyticsTasks, setLocalAnalyticsTasks] = useState<LocalAnalyticsTask[]>([]);
  const [queueBusy, setQueueBusy] = useState(false);
  const [dashboardOverlayConfig, setDashboardOverlayConfig] = useState<DashboardOverlayConfig>({});
  
  const promptedCamerasRef = useRef<Set<number>>(new Set());
  const [showZoneDialog, setShowZoneDialog] = useState(false);
  
  const canvasRef = useRef<PolygonCanvasHandle | null>(null);
  // Mirror of canvasPolygons kept in a ref so the savedPolygons useEffect can
  // always read the latest value without adding it to the dependency array.
  const canvasPolygonsRef = useRef<Shape[]>([]);
  const canvasContextRef = useRef<CanvasContextSignature>({
    cameraId: null,
    zoneId: null,
    creating: false,
  });
  const webcamVideoRef = useRef<HTMLVideoElement | null>(null);
  const webcamStreamRef = useRef<MediaStream | null>(null);
  const webcamSnapshotCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const browserWebcamUploadInFlightRef = useRef(false);
  function setPolygons(shapes: Shape[]) {
    canvasPolygonsRef.current = shapes;
    setCanvasPolygons(shapes);
  }

  const stopBrowserWebcam = useCallback(() => {
    const stream = webcamStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      webcamStreamRef.current = null;
    }

    if (webcamVideoRef.current) {
      webcamVideoRef.current.srcObject = null;
    }

    setBrowserWebcamFrameUrl("");
  }, []);

  const attachBrowserWebcam = useCallback((videoElement: HTMLVideoElement | null) => {
    const stream = webcamStreamRef.current;
    if (!videoElement || !stream) return;

    if (videoElement.srcObject !== stream) {
      videoElement.srcObject = stream;
    }

    const playPromise = videoElement.play();
    if (playPromise) {
      void playPromise.catch(() => {});
    }
  }, []);

  const renderBrowserWebcamFrame = useCallback(() => {
    const video = webcamVideoRef.current;
    if (!video || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || video.videoWidth <= 0 || video.videoHeight <= 0) {
      setFrameLoadError("Waiting for browser webcam frame...");
      return null;
    }

    let canvas = webcamSnapshotCanvasRef.current;
    if (!canvas) {
      canvas = document.createElement("canvas");
      webcamSnapshotCanvasRef.current = canvas;
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const context = canvas.getContext("2d");
    if (!context) {
      setFrameLoadError("Unable to prepare a canvas snapshot from the browser webcam.");
      return null;
    }

    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return {
      canvas,
      width: canvas.width,
      height: canvas.height,
    };
  }, []);

  const captureBrowserWebcamFrame = useCallback(() => {
    const snapshot = renderBrowserWebcamFrame();
    if (!snapshot) {
      return false;
    }

    setBrowserWebcamFrameUrl(snapshot.canvas.toDataURL("image/jpeg", 0.92));
    setFrameLoadError("");
    return true;
  }, [renderBrowserWebcamFrame]);

  const uploadCurrentBrowserWebcamFrame = useCallback(async () => {
    if (!selectedCameraId) {
      return false;
    }

    const snapshot = renderBrowserWebcamFrame();
    if (!snapshot) {
      return false;
    }

    const frameBlob = await canvasToJpegBlob(snapshot.canvas, 0.92);
    if (!frameBlob) {
      return false;
    }

    await uploadBrowserWebcamFrame({
      cameraId: selectedCameraId,
      frame: frameBlob,
      width: snapshot.width,
      height: snapshot.height,
    });
    return true;
  }, [renderBrowserWebcamFrame, selectedCameraId]);

  useEffect(() => {
    if (preferredCameraIdProp && preferredCameraIdProp > 0) {
      setPreferredCameraId(preferredCameraIdProp);
      return;
    }

    if (typeof window === "undefined") {
      setPreferredCameraId(null);
      return;
    }

    const queryCameraId = Number(new URLSearchParams(window.location.search).get("camera_id"));
    if (!Number.isFinite(queryCameraId) || queryCameraId <= 0) {
      setPreferredCameraId(null);
      return;
    }
    setPreferredCameraId(queryCameraId);
  }, [preferredCameraIdProp]);

  useEffect(() => {
    if (sources.length === 0) {
      setSelectedCameraId(null);
      return;
    }

    const hasPreferred = preferredCameraId
      ? sources.some((source) => source.camera.id === preferredCameraId)
      : false;
    if (hasPreferred && selectedCameraId !== preferredCameraId) {
      setSelectedCameraId(preferredCameraId);
      return;
    }

    if (!selectedCameraId || !sources.some((source) => source.camera.id === selectedCameraId)) {
      setSelectedCameraId(sources[0].camera.id);
    }
  }, [preferredCameraId, selectedCameraId, sources]);

  const selectedSource = useMemo(() => {
    if (!selectedCameraId) return null;
    return sources.find((source) => source.camera.id === selectedCameraId) ?? null;
  }, [selectedCameraId, sources]);
  const isWebcamSource = selectedSource?.type === "Webcam";

  const selectedZones = useMemo(() => {
    if (!selectedCameraId) return [];
    return zonesByCamera[selectedCameraId] ?? [];
  }, [selectedCameraId, zonesByCamera]);
  const selectedZone = useMemo(
    () => selectedZones.find((zone) => zone.id === selectedZoneId) ?? null,
    [selectedZoneId, selectedZones],
  );
  const selectedZoneEntryLine = useMemo(
    () => (selectedZone ? zoneEntryLine(selectedZone) : null),
    [selectedZone],
  );
  const selectedZoneExitLine = useMemo(
    () => (selectedZone ? zoneExitLine(selectedZone) : null),
    [selectedZone],
  );

  useEffect(() => {
    if (!selectedCameraId || !selectedSource) return;
    if (analyticsRunning) return;
    if (selectedSource.status !== "live") return;
    if (selectedSource.type === "Webcam") return;

    let cancelled = false;
    void (async () => {
      try {
        await stopStream({ camera_id: selectedCameraId });
        if (cancelled) return;
        await refreshCore();
        void refreshRealtime();
        if (!cancelled) {
          setNotice("Capture is paused. Use Save & Start Processing to begin frame capture.");
        }
      } catch {
        // Keep the page usable even if stop fails due transient backend/network issues.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [analyticsRunning, refreshCore, refreshRealtime, selectedCameraId, selectedSource, setNotice]);

  useEffect(() => {
    if (!selectedCameraId || !isWebcamSource) {
      stopBrowserWebcam();
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setBrowserWebcamFrameUrl("");
      setFrameLoadError("This browser cannot access the webcam on the zones page.");
      return;
    }

    let cancelled = false;
    setBrowserWebcamFrameUrl("");

    navigator.mediaDevices
      .getUserMedia({ video: true, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        webcamStreamRef.current = stream;
        attachBrowserWebcam(webcamVideoRef.current);
      })
      .catch((err) => {
        setBrowserWebcamFrameUrl("");
        setFrameLoadError(err instanceof Error ? err.message : "Unable to access the browser webcam.");
      });

    return () => {
      cancelled = true;
      stopBrowserWebcam();
    };
  }, [attachBrowserWebcam, isWebcamSource, selectedCameraId, stopBrowserWebcam]);

  useEffect(() => {
    if (!isWebcamSource) return;

    attachBrowserWebcam(webcamVideoRef.current);
    const video = webcamVideoRef.current;
    if (!video) return;

    const handleReady = () => {
      window.requestAnimationFrame(() => {
        captureBrowserWebcamFrame();
      });
    };

    video.addEventListener("loadedmetadata", handleReady);
    video.addEventListener("canplay", handleReady);

    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      handleReady();
    }

    return () => {
      video.removeEventListener("loadedmetadata", handleReady);
      video.removeEventListener("canplay", handleReady);
    };
  }, [attachBrowserWebcam, captureBrowserWebcamFrame, isWebcamSource]);

  useEffect(() => {
    if (!selectedCameraId || !isWebcamSource || !browserWebcamFrameUrl) return undefined;

    let cancelled = false;
    const uploadFrame = async () => {
      if (cancelled || browserWebcamUploadInFlightRef.current) {
        return;
      }

      browserWebcamUploadInFlightRef.current = true;
      try {
        await uploadCurrentBrowserWebcamFrame();
      } catch {
        // Keep the zones page usable while backend ingest reconnects.
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
  }, [browserWebcamFrameUrl, isWebcamSource, selectedCameraId, uploadCurrentBrowserWebcamFrame]);

  useEffect(() => {
    if (!selectedCameraId || !isWebcamSource) return;
    if (!webcamStreamRef.current) return;

    captureBrowserWebcamFrame();
  }, [captureBrowserWebcamFrame, frameVersion, isWebcamSource, selectedCameraId]);

  const refreshZones = useCallback(
    async (cameraId: number) => {
      try {
        const zoneRows = await listZones(cameraId);
        setZonesByCamera((previous) => ({ ...previous, [cameraId]: zoneRows }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load zones");
      }
    },
    [setError],
  );

  useEffect(() => {
    if (!selectedCameraId) return;
    void refreshZones(selectedCameraId).then(() => {
      setZonesByCamera((latest) => {
        const zones = latest[selectedCameraId];
        if (zones && zones.length === 0 && !promptedCamerasRef.current.has(selectedCameraId)) {
          promptedCamerasRef.current.add(selectedCameraId);
          setShowZoneDialog(true);
        }
        return latest;
      });
    });
  }, [refreshZones, selectedCameraId]);

  useEffect(() => {
    if (!selectedCameraId) return;
    if (!selectedSource) return;
    const realtimeZoneCount = selectedSource.realtime?.zones?.length;
    if (typeof realtimeZoneCount !== "number") return;
    if (realtimeZoneCount === selectedZones.length) return;
    void refreshZones(selectedCameraId);
  }, [refreshZones, selectedCameraId, selectedSource, selectedZones.length]);

  useEffect(() => {
    if (selectedZones.length === 0) {
      setSelectedZoneId(null);
      setIsCreatingZone(false);
      return;
    }
    if (isCreatingZone) return;
    if (!selectedZoneId || !selectedZones.some((zone) => zone.id === selectedZoneId)) {
      setSelectedZoneId(selectedZones[0].id);
    }
  }, [isCreatingZone, selectedZoneId, selectedZones]);

  useEffect(() => {
    let active = true;

    void (async () => {
      try {
        const config = await loadDashboardOverlayConfig();
        if (!active) return;
        setDashboardOverlayConfig(config);
      } catch {
        if (!active) return;
        setDashboardOverlayConfig({});
      }
    })();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedCameraId || !selectedZoneId) {
      setStagedAnalyticsTypes([]);
      return;
    }

    const configuredModes = getDashboardOverlayModes(dashboardOverlayConfig, selectedCameraId, selectedZoneId);
    setStagedAnalyticsTypes(configuredModes as AnalyticsMode[]);
  }, [dashboardOverlayConfig, selectedCameraId, selectedZoneId]);

  useEffect(() => {
    setIsCreatingZone(false);
    setImageSize(null);
    setPolygons([]);
    setDrawingTool("polygon");
    setEditMode(false);
    setFrameLoadError("");
  }, [selectedCameraId]);

  const savedPolygons = useMemo(() => {
    if (!imageSize) return [];
    const maxX = Math.max(imageSize.width - 1, 1);
    const maxY = Math.max(imageSize.height - 1, 1);

    return selectedZones
      .flatMap((zone) => {
        const polygons = zonePolygons(zone);
        return polygons.map(points => {
           const normalized = zonePointsAreNormalized(points);
           const pts = points.map((point) => {
             if (normalized) {
               return [toCanvasCoordinate(point.x, maxX), toCanvasCoordinate(point.y, maxY)] as Pt;
             }
             return [
               Math.round(Math.min(Math.max(point.x, 0), maxX)),
               Math.round(Math.min(Math.max(point.y, 0), maxY)),
             ] as Pt;
           });
           return { zoneId: zone.id, mode: "polygon" as DrawMode, points: pts, color: zoneColorValue(zone) };
        });
      })
      .filter((shape) => shape.points.length >= 3);
  }, [imageSize, selectedZones]);

  const activeSavedPolygons = useMemo(() => {
    if (isCreatingZone) return [];
    return savedPolygons.filter((s) => s.zoneId === selectedZoneId).map((s) => ({ mode: s.mode, points: s.points }));
  }, [isCreatingZone, savedPolygons, selectedZoneId]);

  const contextPolygons = useMemo(() => {
    const excludeId = isCreatingZone ? null : selectedZoneId;
    return savedPolygons.filter((s) => s.zoneId !== excludeId);
  }, [isCreatingZone, savedPolygons, selectedZoneId]);

  const convertLine = useCallback((rawLine: ElementLine | null, mode: DrawMode): Shape | null => {
    if (!imageSize || !rawLine || isCreatingZone) return null;
    const maxX = Math.max(imageSize.width - 1, 1);
    const maxY = Math.max(imageSize.height - 1, 1);
    const normalized = zonePointsAreNormalized(rawLine.points);
    const converted = rawLine.points.map((point) => {
      if (normalized) {
        return [toCanvasCoordinate(point.x, maxX), toCanvasCoordinate(point.y, maxY)] as Pt;
      }
      return [
        Math.round(Math.min(Math.max(point.x, 0), maxX)),
        Math.round(Math.min(Math.max(point.y, 0), maxY)),
      ] as Pt;
    });
    if (converted.length < 2) return null;
    return { mode, points: [converted[0], converted[1]] as Pt[] };
  }, [imageSize, isCreatingZone]);

  const savedEntryLine = useMemo(() => convertLine(selectedZoneEntryLine, "entry_line"), [convertLine, selectedZoneEntryLine]);
  const savedExitLine = useMemo(() => convertLine(selectedZoneExitLine, "exit_line"), [convertLine, selectedZoneExitLine]);

  const savedCanvasShapes = useMemo(() => {
    const shapes = [...activeSavedPolygons];
    if (savedEntryLine) shapes.push(savedEntryLine);
    if (savedExitLine) shapes.push(savedExitLine);
    return shapes;
  }, [activeSavedPolygons, savedEntryLine, savedExitLine]);

  useEffect(() => {
    if (!imageSize) return;
    const nextContext: CanvasContextSignature = {
      cameraId: selectedCameraId,
      zoneId: selectedZoneId,
      creating: isCreatingZone,
    };
    const sameContext =
      canvasContextRef.current.cameraId === nextContext.cameraId &&
      canvasContextRef.current.zoneId === nextContext.zoneId &&
      canvasContextRef.current.creating === nextContext.creating;
    // Reload saved shapes onto the canvas, preserving unsaved drafts only
    // while staying in the same camera/zone editing context.
    const currentUnsaved = sameContext ? extractUnsavedShapes(canvasPolygonsRef.current, savedCanvasShapes) : [];
    const merged = [...savedCanvasShapes, ...currentUnsaved];
    canvasRef.current?.loadPolygons(merged);
    setPolygons(merged);
    canvasContextRef.current = nextContext;
  }, [imageSize, isCreatingZone, savedCanvasShapes, selectedCameraId, selectedZoneId]);

  const unsavedShapes = useMemo(
    () => extractUnsavedShapes(canvasPolygons, savedCanvasShapes),
    [canvasPolygons, savedCanvasShapes],
  );
  const draftPolygon = useMemo(() => {
    for (let index = unsavedShapes.length - 1; index >= 0; index -= 1) {
      if (unsavedShapes[index].mode === "polygon" && unsavedShapes[index].points.length >= 3) {
        return unsavedShapes[index].points;
      }
    }
    return [];
  }, [unsavedShapes]);
  const draftEntryLine = useMemo(() => {
    for (let index = unsavedShapes.length - 1; index >= 0; index -= 1) {
      if (unsavedShapes[index].mode === "entry_line" && unsavedShapes[index].points.length === 2) {
        return unsavedShapes[index].points as [Pt, Pt];
      }
    }
    return null;
  }, [unsavedShapes]);
  const draftExitLine = useMemo(() => {
    for (let index = unsavedShapes.length - 1; index >= 0; index -= 1) {
      if (unsavedShapes[index].mode === "exit_line" && unsavedShapes[index].points.length === 2) {
        return unsavedShapes[index].points as [Pt, Pt];
      }
    }
    return null;
  }, [unsavedShapes]);

  const canSaveDraft = Boolean(selectedCameraId && imageSize && (draftPolygon.length >= 3 || draftEntryLine || draftExitLine));
  const isVideoFileSource = selectedSource?.type === "Video File";

  const frameUrl = useMemo(() => {
    if (!selectedCameraId || !selectedSource) return "";
    if (isWebcamSource) return browserWebcamFrameUrl;
    const frameEndpoint = isVideoFileSource ? "first-frame" : "source-frame";
    return `/api/backend/api/monitoring/cameras/${selectedCameraId}/${frameEndpoint}?t=${frameVersion}`;
  }, [browserWebcamFrameUrl, frameVersion, isVideoFileSource, isWebcamSource, selectedCameraId, selectedSource]);

  const handleImageSize = useCallback((width: number, height: number) => {
    setImageSize({ width, height });
    setFrameLoadError("");
  }, []);

  const handleImageError = useCallback(() => {
    setImageSize(null);
    if (isVideoFileSource) {
      setFrameLoadError("No first frame is available yet for this video source.");
      return;
    }
    if (isWebcamSource) {
      setFrameLoadError("Unable to load a frame from the browser webcam.");
      return;
    }
    setFrameLoadError("Unable to fetch a static frame from the RTSP source.");
  }, [isVideoFileSource, isWebcamSource]);

  useEffect(() => {
    if (!selectedCameraId) return;
    if (!frameLoadError) return;
    if (imageSize) return;
    if (isWebcamSource && !webcamStreamRef.current) return;

    const id = window.setTimeout(() => {
      setFrameVersion((value) => value + 1);
    }, 2000);
    return () => window.clearTimeout(id);
  }, [frameLoadError, frameVersion, imageSize, isWebcamSource, selectedCameraId]);

  const activatePolygonMode = useCallback(() => {
    setEditMode(false);
    setDrawingTool("polygon");
  }, []);

  const activateRectangleMode = useCallback(() => {
    setEditMode(false);
    setDrawingTool("rectangle");
  }, []);

  const activateEntryLineMode = useCallback(() => {
    setEditMode(false);
    setDrawingTool("entry_line");
  }, []);

  const activateExitLineMode = useCallback(() => {
    setEditMode(false);
    setDrawingTool("exit_line");
  }, []);

  const toggleCanvasEditMode = useCallback(() => {
    setEditMode((previous) => !previous);
  }, []);

  const clearCanvasAll = useCallback(() => {
    canvasRef.current?.clearAll();
    setPolygons([]);
    setDrawingTool("polygon");
    setEditMode(false);
  }, []);

  const saveCanvasImage = useCallback(() => {
    canvasRef.current?.saveImage();
  }, []);

  const toggleCanvasFullscreen = useCallback(async () => {
    try {
      await canvasRef.current?.toggleFullscreen();
      setIsCanvasFullscreen(Boolean(document.fullscreenElement));
    } catch {
      // Ignore fullscreen API errors; canvas remains usable.
    }
  }, []);

  useEffect(() => {
    function onFullscreenChange() {
      setIsCanvasFullscreen(Boolean(document.fullscreenElement));
    }
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => {
      document.removeEventListener("fullscreenchange", onFullscreenChange);
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isTypingTarget(event.target)) return;
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && key === "s") {
        event.preventDefault();
        saveCanvasImage();
        return;
      }
      if (event.ctrlKey || event.metaKey) return;

      if (key === "p") {
        event.preventDefault();
        activatePolygonMode();
        return;
      }
      if (key === "r") {
        event.preventDefault();
        activateRectangleMode();
        return;
      }
      if (key === "l") {
        event.preventDefault();
        if (event.shiftKey) activateExitLineMode();
        else activateEntryLineMode();
        return;
      }
      if (key === "e") {
        event.preventDefault();
        toggleCanvasEditMode();
        return;
      }
      if (key === "f") {
        event.preventDefault();
        void toggleCanvasFullscreen();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [activateEntryLineMode, activateExitLineMode, activatePolygonMode, activateRectangleMode, saveCanvasImage, toggleCanvasEditMode, toggleCanvasFullscreen]);

  function resetDraft() {
    canvasRef.current?.loadPolygons(savedCanvasShapes);
    setPolygons(savedCanvasShapes);
  }

  async function saveZone() {
    if (!selectedCameraId) {
      setError("Select a camera first");
      return;
    }
    if (!zoneName.trim()) {
      setError("Zone name is required");
      return;
    }
    if (!imageSize) {
      setError("No captured frame is available yet for this camera");
      return;
    }
    if (draftPolygon.length < 3 && !draftEntryLine && !draftExitLine) {
      setError("Please draw a polygon (at least 3 points) or an entry/exit line before saving.");
      return;
    }

    setError("");
    setNotice("");
    try {
      const maxX = Math.max(imageSize.width - 1, 1);
      const maxY = Math.max(imageSize.height - 1, 1);
      
      const draftPolygonsNodes = unsavedShapes.filter(s => s.mode === "polygon" && s.points.length >= 3);
      if (draftPolygonsNodes.length === 0 && !draftEntryLine && !draftExitLine) {
        setError("Please draw at least one polygon or an entry/exit line before saving.");
        return;
      }
      
      const payloadPolygons = draftPolygonsNodes.map(shape => 
        shape.points.map(([x, y]: Pt) => ({
          x: toNormalizedCoordinate(x, maxX),
          y: toNormalizedCoordinate(y, maxY),
        }))
      );
      
      const entryLinePayload = draftEntryLine
        ? {
            points: draftEntryLine.map(([x, y]: Pt) => ({
              x: toNormalizedCoordinate(x, maxX),
              y: toNormalizedCoordinate(y, maxY),
            })),
          }
        : undefined;
      const exitLinePayload = draftExitLine
        ? {
            points: draftExitLine.map(([x, y]: Pt) => ({
              x: toNormalizedCoordinate(x, maxX),
              y: toNormalizedCoordinate(y, maxY),
            })),
          }
        : undefined;

    const parsedWaitTime = zoneExpectedWaitTime.trim() ? parseInt(zoneExpectedWaitTime.trim(), 10) : null;
    const parsedCapacity = zoneCapacity.trim() ? parseInt(zoneCapacity.trim(), 10) : null;
    const createdZone = await createZone(selectedCameraId, zoneName.trim(), {
      color: zoneColor,
      points: payloadPolygons[0] || [], // Legacy fallback
      polygons: payloadPolygons, // New Array of Arrays format
      entry_line: entryLinePayload,
      exit_line: exitLinePayload,
    }, parsedCapacity, parsedWaitTime);

      await refreshZones(selectedCameraId);
      setSelectedZoneId(createdZone.id);
      setIsCreatingZone(false);
      setNotice(
        `Saved ${zoneName.trim()} with ${payloadPolygons.length} polygons${entryLinePayload ? " and entry line" : ""}${exitLinePayload ? " and exit line" : ""}`,
      );
      setZoneName((previous) => (previous.toLowerCase().endsWith("a") ? "Zone B" : "Zone A"));
      setZoneCapacity("");
      setZoneExpectedWaitTime("");

      const idx = ZONE_COLORS.indexOf(zoneColor);
      setZoneColor(ZONE_COLORS[(idx + 1) % ZONE_COLORS.length]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save zone");
    }
  }

  async function handleDeleteZone() {
    if (!selectedZoneId) {
      setError("Select a zone to delete");
      return;
    }
    const zoneIdToDelete = selectedZoneId;
    const zone = selectedZones.find((z) => z.id === selectedZoneId);
    const confirmed = window.confirm(`Delete zone "${zone?.name ?? selectedZoneId}"?`);
    if (!confirmed) return;
    setError("");
    setNotice("");
    try {
      await deleteZone(zoneIdToDelete);
      let overlayConfigUpdated = true;
      if (selectedCameraId) {
        try {
          const nextOverlayConfig = removeDashboardOverlayZone(
            dashboardOverlayConfig,
            selectedCameraId,
            zoneIdToDelete,
          );
          setDashboardOverlayConfig(nextOverlayConfig);
          writeDashboardOverlayConfigToStorage(nextOverlayConfig);
          await updateRuntimeConfig(DASHBOARD_OVERLAY_CONFIG_KEY, nextOverlayConfig);
        } catch {
          overlayConfigUpdated = false;
        }
      }
      setSelectedZoneId(null);
      setIsCreatingZone(false);
      if (selectedCameraId) await refreshZones(selectedCameraId);
      setNotice(
        overlayConfigUpdated
          ? "Zone deleted."
          : "Zone deleted. Dashboard overlay options may need another analytics save to refresh.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete zone");
    }
  }

  const queueRows = useMemo<QueuePanelRow[]>(() => {
    const localRows = localAnalyticsTasks.map((task) => ({
      id: task.id,
      label: `${analyticsModeLabel(task.mode)} | ${task.zoneName}`,
      status: task.status,
      details: task.message,
      createdAt: task.createdAt,
    }));

    return [...localRows]
      .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
      .slice(0, 12);
  }, [localAnalyticsTasks]);

  const persistStagedAnalyticsTypes = useCallback(
    async (nextModes: AnalyticsMode[]) => {
      if (!selectedCameraId || !selectedZoneId) {
        setStagedAnalyticsTypes(nextModes);
        return;
      }

      const nextConfig = setDashboardOverlayModes(
        dashboardOverlayConfig,
        selectedCameraId,
        selectedZoneId,
        nextModes,
      );

      setDashboardOverlayConfig(nextConfig);
      setStagedAnalyticsTypes(nextModes);
      writeDashboardOverlayConfigToStorage(nextConfig);

      try {
        await updateRuntimeConfig(DASHBOARD_OVERLAY_CONFIG_KEY, nextConfig);
      } catch {
        setNotice("Saved analytics selection locally, but dashboard sync to backend failed.");
      }
    },
    [dashboardOverlayConfig, selectedCameraId, selectedZoneId, setNotice],
  );
  
  async function enqueueAnalyticsForZone() {
    if (!selectedCameraId) {
      setError("Select a camera first");
      return;
    }
    if (!selectedZone) {
      setError("Select a saved zone to run analytics");
      return;
    }
    if (stagedAnalyticsTypes.length === 0) {
      setError("Add at least one analytics type to the queue");
      return;
    }

    setQueueBusy(true);
    setError("");
    setNotice("");

    try {
      if (!analyticsRunning) {
        const state = await startAnalyticsRun();
        if (!state.running) {
          throw new Error("Realtime analytics could not be started");
        }
        void refreshRealtime();
      } else {
        const state = await getAnalyticsRunState();
        if (!state.running) {
          throw new Error("Realtime analytics is not active");
        }
      }

      let currentStatus = selectedSource?.status ?? "offline";
      if (currentStatus !== "live") {
        const started = await startStream(selectedCameraId);
        currentStatus = started.status === "running" ? "live" : currentStatus;
      }
      if (currentStatus !== "live") {
        await refreshCore();
        void refreshRealtime();
      }

      const newTasks: LocalAnalyticsTask[] = [];
      for (const mode of stagedAnalyticsTypes) {
        const taskId = `${mode}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        if (mode === "person_reid") {
          try {
            const liveCamSource = await waitForCapturedFrame(selectedCameraId, isWebcamSource ? 45000 : 30000);
            if (!liveCamSource) {
              throw new Error(
                isWebcamSource
                  ? "Timeout waiting for webcam frames. Please allow webcam access and make sure the camera is active."
                  : "Timeout waiting for camera frames. Please ensure the source is active.",
              );
            }

            const window = buildAnalysisWindow(liveCamSource.frame_timestamp);
            const response = await runAnalyzerAnalysis({
              camera_id: selectedCameraId,
              zone_id: selectedZone.id,
              start: window.start,
              end: window.end,
            });
            newTasks.push({
              id: taskId,
              mode,
              zoneName: selectedZone.name,
              status: "pending",
              message: `Person ReID job queued (job #${response.job_id})`,
              createdAt: new Date().toISOString(),
            });
          } catch (err) {
            newTasks.push({
              id: taskId,
              mode,
              zoneName: selectedZone.name,
              status: "failed",
              message: err instanceof Error ? err.message : "Failed to queue Person ReID",
              createdAt: new Date().toISOString(),
            });
          }
          continue;
        }

        if (mode === "queue") {
          try {
            const liveCamSource = await waitForCapturedFrame(selectedCameraId, isWebcamSource ? 45000 : 30000);
            if (!liveCamSource) {
              throw new Error(
                isWebcamSource
                  ? "Timeout waiting for webcam frames. Please allow webcam access and make sure the camera is active."
                  : "Timeout waiting for camera frames. Please ensure the source is active.",
              );
            }

            const window = buildAnalysisWindow(liveCamSource.frame_timestamp);
            const response = await runQueueAnalysis({
              camera_id: selectedCameraId,
              zone_id: selectedZone.id,
              start: window.start,
              end: window.end,
            });
            newTasks.push({
              id: taskId,
              mode,
              zoneName: selectedZone.name,
              status: "pending",
              message: `Queue Analytics active (job #${response.job_id})`,
              createdAt: new Date().toISOString(),
            });
          } catch (err) {
            newTasks.push({
              id: taskId,
              mode,
              zoneName: selectedZone.name,
              status: "failed",
              message: err instanceof Error ? err.message : "Failed to start Queue Analytics",
              createdAt: new Date().toISOString(),
            });
          }
          continue;
        }

        if (mode === "facial_expression" || mode === "face_recognition") {
          try {
            const liveCamSource = await waitForCapturedFrame(selectedCameraId, isWebcamSource ? 45000 : 30000);
            if (!liveCamSource) {
              throw new Error(
                isWebcamSource
                  ? "Timeout waiting for webcam frames. Please allow webcam access and make sure the camera is active."
                  : "Timeout waiting for camera frames. Please ensure the source is active.",
              );
            }

            const window = buildAnalysisWindow(liveCamSource.frame_timestamp);
            if (mode === "facial_expression") {
              const response = await runFacialExpressionRecognition({
                camera_id: selectedCameraId,
                zone_id: selectedZone.id,
                start: window.start,
                end: window.end,
              });

              const emotionLabel = response.dominant_emotion ?? "No dominant emotion";
              newTasks.push({
                id: taskId,
                mode,
                zoneName: selectedZone.name,
                status: "completed",
                message: `Facial Expression completed (${response.analyzed_faces} faces, dominant: ${emotionLabel})`,
                createdAt: new Date().toISOString(),
              });
            } else {
              const response = await runFaceRecognition({
                camera_id: selectedCameraId,
                zone_id: selectedZone.id,
                start: window.start,
                end: window.end,
              });

              newTasks.push({
                id: taskId,
                mode,
                zoneName: selectedZone.name,
                status: "completed",
                message: `Face Recognition completed (${response.analyzed_faces} faces analyzed)`,
                createdAt: new Date().toISOString(),
              });
            }
          } catch (err) {
            newTasks.push({
              id: taskId,
              mode,
              zoneName: selectedZone.name,
              status: "failed",
              message: err instanceof Error ? err.message : `Failed to run ${mode === 'face_recognition' ? 'Face Recognition' : 'Facial Expression'}`,
              createdAt: new Date().toISOString(),
            });
          }
          continue;
        }

        newTasks.push({
          id: taskId,
          mode,
          zoneName: selectedZone.name,
          status: "completed",
          message:
            mode === "entry_exit_count"
              ? "Entry/Exit tracking is active in background."
              : "Realtime person analytics is active in background.",
          createdAt: new Date().toISOString(),
        });
      }

      let overlayConfigUpdated = true;
      try {
        const nextOverlayConfig = mergeDashboardOverlayModes(
        dashboardOverlayConfig,
        selectedCameraId,
        selectedZone.id,
        stagedAnalyticsTypes,
      );
        setDashboardOverlayConfig(nextOverlayConfig);
        writeDashboardOverlayConfigToStorage(nextOverlayConfig);
        await updateRuntimeConfig(DASHBOARD_OVERLAY_CONFIG_KEY, nextOverlayConfig);
      } catch {
        overlayConfigUpdated = false;
      }

      setLocalAnalyticsTasks((previous) => [...newTasks, ...previous].slice(0, 12));
      setNotice(
        overlayConfigUpdated
          ? `Started ${newTasks.length} analytics process(es) for zone "${selectedZone.name}"`
          : `Started ${newTasks.length} analytics process(es) for zone "${selectedZone.name}". Dashboard overlays may update after the next config sync.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run analytics");
    } finally {
      setQueueBusy(false);
    }
  }

  function addStagedAnalytics() {
    if (stagedAnalyticsTypes.includes(analyticsMode)) return;
    void persistStagedAnalyticsTypes([...stagedAnalyticsTypes, analyticsMode]);
  }

  function removeStagedAnalytics(mode: AnalyticsMode) {
    void persistStagedAnalyticsTypes(stagedAnalyticsTypes.filter((m) => m !== mode));
  }

  function handleDeleteQueueRow(id: string) {
    setLocalAnalyticsTasks((prev) => prev.filter((task) => task.id !== id));
    setNotice("Removed logic task from analytics queue");
  }

  return (
    <div className="space-y-3">
      {isWebcamSource ? (
        <video
          ref={webcamVideoRef}
          autoPlay
          playsInline
          muted
          tabIndex={-1}
          aria-hidden="true"
          className="pointer-events-none fixed left-[-9999px] top-0 h-px w-px opacity-0"
        />
      ) : null}
      <PanelCard
        title="Zone Configuration"
        subtitle="PolygonZone-style drawing on any available captured frame for each camera"
        right={
          <button
            type="button"
            onClick={() => {
              if (!selectedCameraId) return;
              setFrameLoadError("");
              setFrameVersion((value) => value + 1);
              void refreshZones(selectedCameraId);
            }}
            className="inline-flex items-center gap-1 rounded-lg border border-border bg-white/85 px-3 py-1.5 text-xs font-semibold text-foreground transition hover:bg-accent"
            disabled={!selectedCameraId || busy}
          >
            <RefreshCcw className="h-3.5 w-3.5" /> Reload
          </button>
        }
      >
        <p className="text-xs text-muted-foreground">
          The canvas stays pinned to the loaded frame so zone points remain stable while drawing.
        </p>
      </PanelCard>

      {error ? (
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">
          {notice}
        </div>
      ) : null}

      <div className="grid gap-3 xl:grid-cols-[340px_minmax(0,1fr)_340px]">
        <PanelCard title="Zone Controls" subtitle="Switch camera, name zone, choose color, and save polygon">
          <div className="grid gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Camera</label>
              <select
                value={selectedCameraId ?? ""}
                onChange={(event) => {
                  setPreferredCameraId(null);
                  setSelectedCameraId(event.target.value ? Number(event.target.value) : null);
                }}
                className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
              >
                {sources.map((source) => (
                  <option key={source.camera.id} value={source.camera.id}>
                    #{source.camera.id} {source.camera.name}
                  </option>
                ))}
              </select>
              {selectedSource ? (
                <div className="mt-2 inline-flex items-center gap-2 text-xs text-muted-foreground">
                  <StatusPill status={selectedSource.status} />
                  <span>{selectedSource.type}</span>
                </div>
              ) : null}
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Zone Name</label>
              <input
                value={zoneName}
                onChange={(event) => setZoneName(event.target.value)}
                className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                placeholder="Queue Lane A"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Person Capacity</label>
              <input
                type="number"
                min="1"
                value={zoneCapacity}
                onChange={(event) => setZoneCapacity(event.target.value)}
                className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                placeholder="Optional capacity limit"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Wait Time Threshold (sec)</label>
              <input
                type="number"
                min="1"
                value={zoneExpectedWaitTime}
                onChange={(event) => setZoneExpectedWaitTime(event.target.value)}
                className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                placeholder="Expected wait time target"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Zone Color</label>
              <div className="flex flex-wrap gap-2">
                {ZONE_COLORS.map((color) => (
                  <button
                    key={color}
                    type="button"
                    onClick={() => setZoneColor(color)}
                    className={`h-6 w-6 rounded-full border-2 ${
                      zoneColor === color ? "border-foreground" : "border-white"
                    }`}
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Drawing Tool</label>
              <select
                value={drawingTool}
                onChange={(event) => {
                  const val = event.target.value as DrawingTool;
                  setDrawingTool(val);
                  setEditMode(false);
                }}
                className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
              >
                <option value="polygon">Zone Polygon Tool</option>
                <option value="rectangle">Rectangle Tool</option>
                <option value="entry_line">Entry Line Tool</option>
                <option value="exit_line">Exit Line Tool</option>
              </select>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Current interaction: {editMode ? "Edit Mode" : drawingTool === "polygon" ? "Polygon Mode" : drawingTool === "rectangle" ? "Rectangle Mode" : drawingTool === "entry_line" ? "Entry Line Mode" : "Exit Line Mode"}
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void saveZone()}
                disabled={!canSaveDraft || busy}
                className="rounded-lg border border-primary bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground"
              >
                Save
              </button>
              <button
                type="button"
                onClick={resetDraft}
                disabled={unsavedShapes.length === 0}
                className="rounded-lg border border-border bg-white px-3 py-1.5 text-xs font-semibold text-foreground disabled:cursor-not-allowed disabled:opacity-60"
              >
                Reset Draft
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsCreatingZone(true);
                  setSelectedZoneId(null);
                  setZoneName((previous) => (previous.toLowerCase().endsWith("a") ? "Zone B" : "Zone A"));
                  setZoneCapacity("");
                  setZoneExpectedWaitTime("");
                  setZoneColor(ZONE_COLORS[0]);
                  canvasRef.current?.loadPolygons([]);
                  setPolygons([]);
                  activatePolygonMode();
                }}
                className="rounded-lg border border-dashed border-primary/60 bg-primary/5 px-3 py-1.5 text-xs font-semibold text-primary transition hover:bg-primary/10"
              >
                + New
              </button>
              <button
                type="button"
                onClick={clearCanvasAll}
                className="rounded-lg border border-border bg-white px-3 py-1.5 text-xs font-semibold text-foreground transition hover:bg-accent/60"
              >
                Clear Canvas
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteZone()}
                disabled={!selectedZoneId || busy}
                className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs font-semibold text-destructive transition hover:bg-destructive/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Delete Zone
              </button>
            </div>

            <div className="rounded-xl border border-border/70 bg-white/80 p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                Saved Zones ({selectedZones.length})
              </p>
              <div className="mt-2 grid max-h-[280px] gap-2 overflow-y-auto">
                {selectedZones.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No zones saved for this camera yet.</p>
                ) : (
                  selectedZones.map((zone) => {
                    const polygons = zonePolygons(zone);
                    const entryLine = zoneEntryLine(zone);
                    const exitLine = zoneExitLine(zone);
                    const color = zoneColorValue(zone);
                    const active = zone.id === selectedZoneId;
                    return (
                      <button
                        key={zone.id}
                        type="button"
                        onClick={() => {
                          setIsCreatingZone(false);
                          setSelectedZoneId(zone.id);
                        }}
                        className={`rounded-lg border p-2 text-left text-xs transition ${
                          active ? "border-primary bg-primary/10" : "border-border/70 bg-white hover:bg-accent/60"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-semibold text-foreground">{zone.name}</p>
                          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
                        </div>
                        <p className="mt-1 text-muted-foreground">
                          {polygons.length} polygons {entryLine ? "| entry line" : ""} {exitLine ? "| exit line" : ""}{" "}
                          {active ? "| selected for analytics" : ""}
                        </p>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        </PanelCard>

        <PanelCard title="Drawing Canvas" subtitle="Use Polygon or Line tool on the same canvas">
          {selectedSource ? (
            <div className="space-y-2">
              <div className="overflow-hidden rounded-xl border border-border/70 bg-slate-900/10 p-2">
                {frameUrl ? (
                  <PolygonCanvas
                    ref={canvasRef}
                    imageUrl={frameUrl}
                    drawMode={drawingTool}
                    editMode={editMode}
                    onImageSize={handleImageSize}
                    onImageError={handleImageError}
                    onPolygonsChange={setPolygons}
                    contextPolygons={contextPolygons}
                    className="w-full"
                  />
                ) : (
                  <div className="flex h-[420px] items-center justify-center text-sm text-muted-foreground">
                    {frameLoadError || (isWebcamSource ? "Waiting for browser webcam frame..." : "Loading image...")}
                  </div>
                )}
              </div>
              {isWebcamSource ? (
                <p className="text-[11px] text-muted-foreground">
                  Using the browser webcam for zone drawing because the Dockerized backend cannot read the host webcam device directly.
                </p>
              ) : null}
              <div className="flex w-full overflow-x-auto items-stretch rounded-xl border border-border/10 bg-[#8622FF]">
                <button
                  type="button"
                  onClick={activatePolygonMode}
                  className={`flex flex-col items-center justify-center px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold transition whitespace-nowrap ${
                    !editMode && drawingTool === "polygon"
                      ? "bg-white text-[#8622FF]"
                      : "bg-transparent text-white hover:bg-white/10"
                  }`}
                >
                  <Shapes className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Polygon Mode<br/>(P)</span>
                </button>
                <button
                  type="button"
                  onClick={activateRectangleMode}
                  className={`flex flex-col items-center justify-center border-l border-white/20 px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold transition whitespace-nowrap ${
                    !editMode && drawingTool === "rectangle"
                      ? "bg-white text-[#8622FF]"
                      : "bg-transparent text-white hover:bg-white/10"
                  }`}
                >
                  <Square className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Rectangle<br/>(R)</span>
                </button>
                <button
                  type="button"
                  onClick={activateEntryLineMode}
                  className={`flex flex-col items-center justify-center border-l border-white/20 px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold transition whitespace-nowrap ${
                    !editMode && drawingTool === "entry_line"
                      ? "bg-white text-[#8622FF]"
                      : "bg-transparent text-white hover:bg-white/10"
                  }`}
                >
                  <Slash className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Entry Line<br/>(L)</span>
                </button>
                <button
                  type="button"
                  onClick={activateExitLineMode}
                  className={`flex flex-col items-center justify-center border-l border-white/20 px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold transition whitespace-nowrap ${
                    !editMode && drawingTool === "exit_line"
                      ? "bg-white text-[#8622FF]"
                      : "bg-transparent text-white hover:bg-white/10"
                  }`}
                >
                  <Slash className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Exit Line<br/>(Shift+L)</span>
                </button>
                <button
                  type="button"
                  onClick={toggleCanvasEditMode}
                  className={`flex flex-col items-center justify-center border-l border-white/20 px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold transition whitespace-nowrap ${
                    editMode
                      ? "bg-white text-[#8622FF]"
                      : "bg-transparent text-white hover:bg-white/10"
                  }`}
                >
                  <Move className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Edit Mode<br/>(E)</span>
                </button>
                <button
                  type="button"
                  onClick={() => canvasRef.current?.undo()}
                  className="flex flex-col items-center justify-center border-l border-white/20 bg-transparent px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold text-white transition hover:bg-white/10 whitespace-nowrap"
                >
                  <Undo2 className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Undo<br/>(Ctrl/⌘-Z)</span>
                </button>
                <button
                  type="button"
                  onClick={() => canvasRef.current?.discardCurrent()}
                  className="flex flex-col items-center justify-center border-l border-white/20 bg-transparent px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold text-white transition hover:bg-white/10 whitespace-nowrap"
                >
                  <X className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Discard current<br/>(Esc)</span>
                </button>
                <button
                  type="button"
                  onClick={clearCanvasAll}
                  className="flex flex-col items-center justify-center border-l border-white/20 bg-transparent px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold text-white transition hover:bg-white/10 whitespace-nowrap"
                >
                  <Trash2 className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Clear all polygons<br/>(Ctrl/⌘-E)</span>
                </button>
                <button
                  type="button"
                  onClick={saveCanvasImage}
                  className="flex flex-col items-center justify-center border-l border-white/20 bg-transparent px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold text-white transition hover:bg-white/10 whitespace-nowrap"
                >
                  <Save className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">Save image<br/>(Ctrl/⌘-S)</span>
                </button>
                <button
                  type="button"
                  onClick={() => void toggleCanvasFullscreen()}
                  className="flex flex-col items-center justify-center border-l border-white/20 bg-transparent px-4 py-3 min-w-[80px] sm:min-w-0 flex-1 text-[10px] sm:text-[11px] font-semibold text-white transition hover:bg-white/10 whitespace-nowrap"
                >
                  <Maximize className="mb-1.5 h-5 w-5" />
                  <span className="text-center leading-tight">{isCanvasFullscreen ? "Exit Fullscreen" : "Fullscreen"}<br/>(F)</span>
                </button>
              </div>
              <p className="text-[11px] text-muted-foreground">Shift snaps angles while drawing lines/polygons.</p>

              <div className="rounded-md bg-slate-900/70 px-2 py-1 text-[11px] text-slate-100">
                {frameLoadError
                  ? frameLoadError
                  : `Mode: ${editMode ? "Edit" : drawingTool === "polygon" ? "Polygon" : drawingTool === "entry_line" ? "Entry Line" : "Exit Line"} • Saved: ${savedCanvasShapes.length} • Unsaved: ${unsavedShapes.length} • Entry Line: ${draftEntryLine ? "Ready" : "None"} • Exit Line: ${draftExitLine ? "Ready" : "None"}`}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No camera selected.</p>
          )}
        </PanelCard>

        <PanelCard title="Analytics Queue" subtitle="Select analytics type for the selected zone and run it in background">
          <div className="grid gap-3">

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Selected Zone</label>
              <div className="flex h-10 w-full items-center rounded-lg border border-input bg-muted px-3 text-sm text-foreground">
                {selectedZone ? (
                  <span className="font-medium">{selectedZone.name}</span>
                ) : (
                  <span className="text-muted-foreground italic">Select a zone from the list/map</span>
                )}
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Add Analytics Type</label>
              <div className="flex gap-2">
                <select
                  value={analyticsMode}
                  onChange={(event) => setAnalyticsMode(event.target.value as AnalyticsMode)}
                  className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                >
                  {ANALYTICS_MODES.map((modeOption) => (
                    <option key={modeOption.id} value={modeOption.id}>
                      {modeOption.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={addStagedAnalytics}
                  disabled={!selectedZone || stagedAnalyticsTypes.includes(analyticsMode)}
                  className="whitespace-nowrap rounded-lg border border-primary/20 bg-primary/10 px-4 text-xs font-semibold text-primary transition hover:bg-primary/20 disabled:opacity-50"
                >
                  Add +
                </button>
              </div>
              {(analyticsMode === "face_recognition" || stagedAnalyticsTypes.includes("face_recognition")) && (
                <div className="mt-2 text-right">
                  <a
                    href="/dashboard/faces"
                    target="_blank"
                    className="inline-flex h-9 items-center justify-center rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground shadow-sm transition-colors hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 w-full"
                  >
                    Manage Faces
                  </a>
                </div>
              )}
            </div>

            {stagedAnalyticsTypes.length > 0 ? (
              <div className="rounded-lg border border-border/60 bg-white p-2">
                <p className="mb-2 text-[10px] font-semibold uppercase text-muted-foreground">Staged Configuration</p>
                <div className="flex-1 min-w-[150px]">
                <span className="text-muted-foreground">Polygons:</span>
                <span className="ml-2 font-mono">{selectedZone ? zonePolygons(selectedZone).length : 0} drawn</span>
              </div>    <div className="flex flex-wrap gap-2">
                  {stagedAnalyticsTypes.map((mode) => (
                    <div
                      key={mode}
                      className="inline-flex items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-xs text-indigo-700"
                    >
                      <span>{analyticsModeLabel(mode)}</span>
                      <button
                        type="button"
                        onClick={() => removeStagedAnalytics(mode)}
                        className="ml-1 text-indigo-400 hover:text-indigo-900"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}



            <button
              type="button"
              onClick={() => void enqueueAnalyticsForZone()}
              disabled={!selectedCameraId || !selectedZone || stagedAnalyticsTypes.length === 0 || queueBusy || busy}
              className="rounded-lg border border-primary bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground"
            >
              {queueBusy ? "Processing..." : "Save & Start Processing"}
            </button>

            <div className="rounded-xl border border-border/70 bg-white/80 p-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                Feature List ({queueRows.length})
              </p>
              <div className="mt-2 grid max-h-[480px] gap-2 overflow-y-auto">
                {queueRows.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No recent analytics tasks.</p>
                ) : (
                  queueRows.map((row) => (
                    <div
                      key={row.id}
                      className={`relative flex flex-col justify-between overflow-hidden rounded-lg border p-2 text-left text-xs ${queueStatusTone(
                        row.status,
                      )}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-semibold">{row.label}</p>
                        <button
                          type="button"
                          onClick={() => handleDeleteQueueRow(row.id)}
                          className="text-muted-foreground hover:text-destructive active:scale-95"
                          title="Delete analytics task"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <p className="mt-1 opacity-80">{row.details}</p>
                      <span className="mt-2 block text-[10px] font-medium uppercase tracking-wider opacity-60">
                        {formatTimestamp(row.createdAt)}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>

          </div>
        </PanelCard>
      </div>

      {showZoneDialog && selectedCameraId ? (
        <ZoneConfigDialog
          onChoice={async (configureZones) => {
            setShowZoneDialog(false);
            if (!configureZones) {
              try {
                await createZone(
                  selectedCameraId,
                  "Full Frame",
                  {
                    color: "#EA580C",
                    points: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }],
                    polygons: [[{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }]],
                  },
                  null,
                  null
                );
                await refreshZones(selectedCameraId);
              } catch (e) {
                console.error("Failed to default zone", e);
              }
            }
          }}
        />
      ) : null}
    </div>
  );
}

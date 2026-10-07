"use client";

import { type ChangeEvent, type DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Upload } from "lucide-react";
import { useRouter } from "next/navigation";

import { PanelCard } from "@/components/v2/ui";
import { useSourcesData } from "@/hooks/use-sources-data";

function existingUploadedFileName(input: { rtsp_url: string | null; location: string | null }): string {
  const location = (input.location || "").trim();
  const fromLocation = location.includes("File:") ? location.split("File:")[1]?.split("|")[0]?.trim() : "";
  if (fromLocation) return fromLocation;

  const source = (input.rtsp_url || "").trim();
  if (!source.toLowerCase().startsWith("file://")) return "";
  const encodedName = source.split("/").pop() || "";
  if (!encodedName) return "";
  try {
    return decodeURIComponent(encodedName);
  } catch {
    return encodedName;
  }
}

function isRtspLikeUrl(value: string): boolean {
  return /^rtsps?:\/\//i.test(value.trim());
}

function StepBadge({
  index,
  label,
  active,
  complete,
}: {
  index: number;
  label: string;
  active: boolean;
  complete: boolean;
}) {
  return (
    <div className="inline-flex items-center gap-2">
      <span
        className={`inline-flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-semibold ${
          active || complete
            ? "border-primary bg-primary text-primary-foreground"
            : "border-border bg-white text-muted-foreground"
        }`}
      >
        {complete ? <Check className="h-3.5 w-3.5" /> : index}
      </span>
      <span className={`text-xs font-semibold ${active ? "text-foreground" : "text-muted-foreground"}`}>{label}</span>
    </div>
  );
}

function scheduleSummary(input: {
  type: "continuous" | "scheduled" | "fixed";
  from: string;
  to: string;
  minutes: number;
}): string {
  if (input.type === "continuous") return "Continuous";
  if (input.type === "scheduled") return `Scheduled ${input.from}-${input.to}`;
  return `Fixed Period ${input.minutes}min`;
}

export function NewSourceWizard({ onWizardComplete }: { onWizardComplete?: (complete: boolean) => void }) {
  const router = useRouter();
  const [editingId, setEditingId] = useState<number | null>(null);

  const {
    busy,
    error,
    notice,
    setError,
    cameras,
    saveSource,
  } = useSourcesData({ enableRealtime: false, pollCoreMs: 60000 });

  const [step, setStep] = useState(1);
  const [sourceType, setSourceType] = useState<"rtsp" | "file" | "webcam">("rtsp");
  const [sourceName, setSourceName] = useState("");
  const [rtspUrl, setRtspUrl] = useState("");
  const [webcamId, setWebcamId] = useState("0");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [scheduleType, setScheduleType] = useState<"continuous" | "scheduled" | "fixed">("continuous");
  const [scheduleFrom, setScheduleFrom] = useState("09:00");
  const [scheduleTo, setScheduleTo] = useState("21:00");
  const [fixedMinutes, setFixedMinutes] = useState(30);

  // New state to hold the saved camera for Step 4
  const [savedCamera, setSavedCamera] = useState<{ id: number; name: string } | null>(null);

  const [webcamError, setWebcamError] = useState<string>("");
  const videoPreviewRef = useRef<HTMLVideoElement | null>(null);
  const webcamKeepAliveRef = useRef<HTMLVideoElement | null>(null);
  const webcamStreamRef = useRef<MediaStream | null>(null);

  const stopWebcamStream = useCallback(() => {
    const stream = webcamStreamRef.current;
    if (!stream) return;

    stream.getTracks().forEach((track) => track.stop());
    webcamStreamRef.current = null;

    if (videoPreviewRef.current) {
      videoPreviewRef.current.srcObject = null;
    }
    if (webcamKeepAliveRef.current) {
      webcamKeepAliveRef.current.srcObject = null;
    }
  }, []);

  const attachWebcamStream = useCallback((videoElement: HTMLVideoElement | null) => {
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

  useEffect(() => {
    setWebcamError("");
    if (sourceType !== "webcam") {
      stopWebcamStream();
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setWebcamError("Your browser does not support accessing the webcam (or requires HTTPS).");
      return;
    }

    let cancelled = false;

    navigator.mediaDevices
      .getUserMedia({ video: true, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        webcamStreamRef.current = stream;
        attachWebcamStream(videoPreviewRef.current);
        attachWebcamStream(webcamKeepAliveRef.current);
      })
      .catch((err) => {
        console.error("Failed to access webcam:", err);
        setWebcamError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
      stopWebcamStream();
    };
  }, [attachWebcamStream, sourceType, stopWebcamStream]);

  useEffect(() => {
    if (sourceType !== "webcam") return;

    attachWebcamStream(webcamKeepAliveRef.current);
    attachWebcamStream(videoPreviewRef.current);
  }, [attachWebcamStream, sourceType, step]);

  const initializedRef = useRef(false);
  const editingCamera = useMemo(
    () => (editingId ? cameras.find((camera) => camera.id === editingId) ?? null : null),
    [cameras, editingId],
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const editId = Number(new URLSearchParams(window.location.search).get("edit"));
    if (!Number.isFinite(editId) || editId <= 0) return;
    setEditingId(editId);
  }, []);

  useEffect(() => {
    if (!editingCamera || initializedRef.current) return;
    initializedRef.current = true;

    setSourceName(editingCamera.name);
    if (editingCamera.rtsp_url) {
      if (editingCamera.rtsp_url.startsWith("webcam://")) {
        setSourceType("webcam");
        setWebcamId(editingCamera.rtsp_url.replace("webcam://", ""));
      } else if (isRtspLikeUrl(editingCamera.rtsp_url)) {
        setSourceType("rtsp");
        setRtspUrl(editingCamera.rtsp_url);
      } else {
        setSourceType("file");
        setFileName(existingUploadedFileName(editingCamera) || "existing-video.mp4");
      }
    } else {
      setSourceType("file");
      setFileName(existingUploadedFileName(editingCamera) || "existing-video.mp4");
    }
  }, [editingCamera]);

  function stepOneValid() {
    if (!sourceName.trim()) return false;
    if (sourceType === "rtsp") return isRtspLikeUrl(rtspUrl);
    if (sourceType === "webcam") return true;
    if (videoFile) return true;
    return Boolean(editingId && fileName.trim().length > 0);
  }

  function setSelectedVideo(file: File) {
    setVideoFile(file);
    setFileName(file.name);
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedVideo(file);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (!file) return;
    setSelectedVideo(file);
  }

  async function handleSave() {
    if (!stepOneValid()) {
      setError("Provide valid source details before saving");
      return;
    }

    try {
      const camera = await saveSource({
        cameraId: editingId ?? undefined,
        name: sourceName,
        sourceType,
        rtspUrl,
        webcamId,
        file: sourceType === "file" ? videoFile : null,
        fileName,
        scheduleLabel: scheduleSummary({
          type: scheduleType,
          from: scheduleFrom,
          to: scheduleTo,
          minutes: fixedMinutes,
        }),
        autoStartStream: false,
      });

      if (sourceType === "rtsp") {
        onWizardComplete?.(false);
        router.push(`/configurations?tab=zones&camera_id=${camera.id}`);
        return;
      }

      // Instead of redirecting immediately, we move to Step 3
      setSavedCamera(camera);
      setStep(3);
      onWizardComplete?.(true);
    } catch {
      // Error is already handled in the hook state.
    }
  }

  return (
    <div className="space-y-3">
      {sourceType === "webcam" ? (
        <video
          ref={webcamKeepAliveRef}
          autoPlay
          playsInline
          muted
          tabIndex={-1}
          aria-hidden="true"
          className="pointer-events-none fixed left-[-9999px] top-0 h-px w-px opacity-0"
        />
      ) : null}
      <PanelCard
        title={editingId ? "Edit Source" : "Add New Source"}
        subtitle="Three-step wizard for source setup and processing schedule"
      >
        <div className="flex flex-wrap items-center gap-4">
          <StepBadge index={1} label="Source Input" active={step === 1} complete={step > 1} />
          <StepBadge index={2} label="Schedule + Summary" active={step === 2} complete={step > 2} />
          <StepBadge index={3} label="Zone Configuration" active={step === 3} complete={step > 3} />
        </div>
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

      {step === 1 ? (
        <PanelCard title="Step 1" subtitle="Provide RTSP URL or select a video file">
          <div className="grid gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold text-muted-foreground">Source Name</label>
              <input
                value={sourceName}
                onChange={(event) => setSourceName(event.target.value)}
                placeholder="Example: Main Entrance"
                className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground outline-none"
              />
            </div>

            <div className="inline-flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setSourceType("rtsp")}
                className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                  sourceType === "rtsp"
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white text-foreground"
                }`}
              >
                RTSP URL
              </button>
              <button
                type="button"
                onClick={() => setSourceType("file")}
                className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                  sourceType === "file"
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white text-foreground"
                }`}
              >
                Video Upload
              </button>
              <button
                type="button"
                onClick={() => setSourceType("webcam")}
                className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                  sourceType === "webcam"
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white text-foreground"
                }`}
              >
                Webcam
              </button>
            </div>

            {sourceType === "rtsp" ? (
              <div>
                <label className="mb-1 block text-xs font-semibold text-muted-foreground">RTSP Endpoint</label>
                <input
                  value={rtspUrl}
                  onChange={(event) => setRtspUrl(event.target.value)}
                  placeholder="rtsp://192.168.0.2/live"
                  className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground outline-none"
                />
                <p className="mt-1 text-[11px] text-muted-foreground">Supports `rtsp://` and `rtsps://` URLs.</p>
              </div>
            ) : sourceType === "webcam" ? (
              <div className="space-y-3">
                {webcamError && (
                  <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-3 text-sm text-red-600">
                    <strong>Webcam Error:</strong> {webcamError}
                  </div>
                )}
                <div>
                  <label className="mb-1 block text-xs font-semibold text-muted-foreground">Live Preview</label>
                  <div className="overflow-hidden rounded-lg border border-border bg-black relative flex items-center justify-center min-h-[200px]">
                    {!webcamError && (
                      <video
                        ref={videoPreviewRef}
                        autoPlay
                        playsInline
                        muted
                        className="h-[200px] w-full object-contain"
                      />
                    )}
                    {webcamError && (
                       <p className="text-xs text-muted-foreground">Preview unavailable</p>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <label
                onDragOver={(event) => {
                  event.preventDefault();
                  setIsDragOver(true);
                }}
                onDragLeave={() => setIsDragOver(false)}
                onDrop={handleDrop}
                className={`rounded-xl border-2 border-dashed p-4 text-center ${
                  isDragOver
                    ? "border-primary bg-primary/20"
                    : "border-primary/60 bg-primary/10"
                }`}
              >
                <Upload className="mx-auto h-5 w-5 text-primary" />
                <p className="mt-2 text-sm font-semibold text-foreground">Drop video here or click to select</p>
                <p className="mt-1 text-xs text-muted-foreground">{fileName || "No file selected"}</p>
                <p className="mt-2 text-[11px] text-muted-foreground">
                </p>
                <input type="file" accept="video/*" onChange={handleFileChange} className="hidden" />
              </label>
            )}

            <div className="flex justify-end mt-4">
              <button
                type="button"
                disabled={!stepOneValid()}
                onClick={() => setStep(2)}
                className="rounded-lg border border-primary bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground"
              >
                Continue
              </button>
            </div>
          </div>
        </PanelCard>
      ) : null}



      {step === 2 ? (
        <PanelCard title="Step 2" subtitle="Processing schedule and summary before save">
          <div className="grid gap-3">
            <div className="inline-flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setScheduleType("continuous")}
                className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                  scheduleType === "continuous"
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white text-foreground"
                }`}
              >
                Continuous
              </button>
              <button
                type="button"
                onClick={() => setScheduleType("scheduled")}
                className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                  scheduleType === "scheduled"
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white text-foreground"
                }`}
              >
                Scheduled
              </button>
              <button
                type="button"
                onClick={() => setScheduleType("fixed")}
                className={`rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                  scheduleType === "fixed"
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-white text-foreground"
                }`}
              >
                Fixed Period
              </button>
            </div>

            {scheduleType === "scheduled" ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-semibold text-muted-foreground">From</label>
                  <input
                    type="time"
                    value={scheduleFrom}
                    onChange={(event) => setScheduleFrom(event.target.value)}
                    className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold text-muted-foreground">To</label>
                  <input
                    type="time"
                    value={scheduleTo}
                    onChange={(event) => setScheduleTo(event.target.value)}
                    className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                  />
                </div>
              </div>
            ) : null}

            {scheduleType === "fixed" ? (
              <div>
                <label className="mb-1 block text-xs font-semibold text-muted-foreground">Duration (minutes)</label>
                <input
                  type="number"
                  min={1}
                  max={720}
                  value={fixedMinutes}
                  onChange={(event) => setFixedMinutes(Number(event.target.value) || 1)}
                  className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm text-foreground"
                />
              </div>
            ) : null}

            <div className="rounded-xl border border-border/70 bg-white/80 p-3 text-sm">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Summary</p>
              <div className="mt-2 space-y-1 text-foreground">
                <p><strong>Name:</strong> {sourceName || "-"}</p>
                <p><strong>Type:</strong> {sourceType === "rtsp" ? "RTSP URL" : sourceType === "webcam" ? "Webcam" : "Video File"}</p>
                <p><strong>Source:</strong> {sourceType === "rtsp" ? rtspUrl || "-" : sourceType === "webcam" ? (webcamId || "0") : fileName || "-"}</p>

                <p>
                  <strong>Schedule:</strong>{" "}
                  {scheduleSummary({
                    type: scheduleType,
                    from: scheduleFrom,
                    to: scheduleTo,
                    minutes: fixedMinutes,
                  })}
                </p>
              </div>
            </div>

            <div className="flex justify-between">
              <button
                type="button"
                onClick={() => setStep(1)}
                className="rounded-lg border border-border bg-white px-3 py-1.5 text-xs font-semibold text-foreground"
              >
                Back
              </button>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={busy || !stepOneValid()}
                className="rounded-lg border border-primary bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:border-border disabled:bg-muted disabled:text-muted-foreground"
              >
                {editingId ? "Update Source" : "Save Source"}
              </button>
            </div>
          </div>
        </PanelCard>
      ) : null}

      {step === 3 && savedCamera ? (
        <PanelCard
          title="Step 3"
          subtitle="Success! Source saved. Proceed to Zone Configuration."
        >
          <div className="flex flex-col items-center justify-center gap-6 py-8 text-center">
            <div className="rounded-full bg-emerald-100 p-3 text-emerald-600">
              <Check className="h-8 w-8" />
            </div>
            
            <div className="space-y-1">
              <h3 className="text-lg font-semibold text-foreground">Source Saved Successfully</h3>
              <p className="text-sm text-muted-foreground">
                &quot;{savedCamera.name}&quot; has been added to your inventory.
              </p>
            </div>

            <div className="flex w-full max-w-sm flex-col gap-3 sm:flex-row">
               <button
                type="button"
                onClick={() => {
                  setStep(1);
                  onWizardComplete?.(false);
                }}
                className="flex-1 rounded-lg border border-border bg-white px-4 py-2.5 text-sm font-semibold text-foreground transition hover:bg-accent"
              >
                Return to Dashboard
              </button>
              <button
                type="button"
                onClick={() => {
                   onWizardComplete?.(false);
                   router.push(`/configurations?tab=zones&camera_id=${savedCamera.id}`);
                }}
                className="flex-1 rounded-lg border border-primary bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90"
              >
                Configure Zones &rarr;
              </button>
            </div>
          </div>
        </PanelCard>
      ) : null}
    </div>
  );
}

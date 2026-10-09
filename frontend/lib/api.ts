export type Camera = {
  id: number;
  name: string;
  rtsp_url: string | null;
  location: string | null;
  created_at: string;
};

export type MediaUploadResponse = {
  camera: Camera;
  source_type: string;
  file_name: string;
  size_bytes: number;
  source_url: string;
};

export type Stream = {
  id: number;
  camera_id: number;
  port: number | null;
  rtsp_url: string | null;
  status: "starting" | "running" | "stopped" | "error" | string;
  started_at: string | null;
  stopped_at: string | null;
  last_heartbeat: string | null;
};

export type MonitoringMetrics = {
  timestamp: string;
  counts: Record<string, number>;
  disk: Record<string, number | string>;
  load: [number, number, number] | null;
  alerts: string[];
};

export type RealtimeZoneCount = {
  zone_id: number;
  zone_name: string;
  person_count: number;
};

export type RealtimeDetectionBox = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  zone_id: number | null;
  in_zone: boolean;
};

export type RealtimeCameraSnapshot = {
  camera_id: number;
  camera_name: string;
  stream_status: string;
  frame_timestamp: string | null;
  has_live_frame: boolean;
  total_person_count: number;
  total_detected_person_count: number;
  detection_boxes: RealtimeDetectionBox[];
  zones: RealtimeZoneCount[];
};

export type RealtimeMonitoringResponse = {
  timestamp: string;
  detector_available: boolean;
  analytics_running: boolean;
  cameras: RealtimeCameraSnapshot[];
};

export type AnalyticsRunState = {
  running: boolean;
};

export type BrowserFrameIngestResponse = {
  accepted: boolean;
  camera_id: number;
  stream_id: number | null;
  frame_id: number;
  frame_timestamp: string;
};

export type Zone = {
  id: number;
  camera_id: number;
  name: string;
  polygon: {
    points?: Array<{ x: number; y: number }>;
    entry_line?: {
      points?: Array<{ x: number; y: number }>;
    };
    exit_line?: {
      points?: Array<{ x: number; y: number }>;
    };
    [key: string]: unknown;
  };
  capacity: number | null;
  expected_wait_time_sec: number | null;
  created_at: string;
};

export type FootfallSeriesPoint = {
  timestamp: string;
  zone_id: number | null;
  event_type: "entry" | "exit" | string;
  count: number;
  entry_count: number;
  exit_count: number;
  net_count: number;
};

export type FootfallZoneTotal = {
  zone_id: number | null;
  zone_name: string | null;
  total_entries: number;
  total_exits: number;
  net_flow: number;
};

export type PeakPersonCountHour = {
  hour_start: string | null;
  hour_end: string | null;
  person_count: number;
  zone_id: number | null;
  zone_name: string | null;
};

export type PeakTrafficHour = {
  hour_start: string | null;
  hour_end: string | null;
  traffic_count: number;
};

export type MostCrowdedQuarter = {
  quarter_index: number | null;
  quarter_label: string | null;
  start_time: string | null;
  end_time: string | null;
  person_count: number;
  zone_id: number | null;
  zone_name: string | null;
};

export type FootfallResponse = {
  camera_id: number;
  zone_id: number | null;
  total: number;
  total_entries: number;
  total_exits: number;
  net_flow: number;
  series: FootfallSeriesPoint[];
  zone_totals: FootfallZoneTotal[];
  video_available_seconds: number;
  video_available_hours: number;
  peak_traffic_hour: PeakTrafficHour | null;
  peak_person_count_hour: PeakPersonCountHour | null;
  source_type: "rtsp" | "video" | string;
  most_crowded_quarter: MostCrowdedQuarter | null;
};

export type ZoneCongestionMetric = {
  zone_id: number;
  zone_name: string;
  is_service_zone: boolean;
  capacity: number | null;
  current_count: number;
  peak_count: number;
  avg_count: number;
  current_utilization_pct: number | null;
  peak_utilization_pct: number | null;
  congestion_level: "low" | "medium" | "high" | "critical" | string;
  latest_observed_at: string | null;
};

export type ZonePathPoint = {
  timestamp: string;
  zone_id: number;
  x: number | null;
  y: number | null;
};

export type ZonePathTrack = {
  track_key: string;
  job_id: number;
  track_id: number;
  track_type: "person" | "staff" | string;
  first_seen: string;
  last_seen: string;
  detections: number;
  estimated_dwell_seconds: number;
  service_zone_ratio: number;
  predominant_zone_id: number | null;
  predominant_zone_name: string | null;
  zone_sequence: number[];
  zone_sequence_names: string[];
  path_points: ZonePathPoint[];
};

export type ZoneHeatmap = {
  zone_id: number;
  zone_name: string;
  grid_x: number;
  grid_y: number;
  max_cell_count: number;
  grid: number[][];
  normalized_grid: number[][];
};

export type ZoneServiceStaffPresence = {
  zone_id: number;
  zone_name: string;
  is_service_zone: boolean;
  person_tracks: number;
  staff_tracks: number;
  person_presence_seconds: number;
  staff_presence_seconds: number;
  total_presence_seconds: number;
  staff_presence_ratio: number;
};

export type ZoneAnalysisSummary = {
  total_zones: number;
  service_zones: number;
  tracked_paths: number;
  estimated_staff_tracks: number;
  estimated_person_tracks: number;
  peak_congestion_zone?: {
    zone_id: number;
    zone_name: string;
    congestion_level: string;
    current_count: number;
    peak_count: number;
  } | null;
};

export type ZoneAnalysisResponse = {
  camera_id: number;
  zone_id: number | null;
  start_time: string | null;
  end_time: string | null;
  frame_interval_seconds: number;
  congestion_by_zone: ZoneCongestionMetric[];
  tracked_paths: ZonePathTrack[];
  heatmaps: ZoneHeatmap[];
  service_staff_presence: ZoneServiceStaffPresence[];
  summary: ZoneAnalysisSummary;
};

export type AnalyzerStatus = {
  available: boolean;
  fetcher_enabled: boolean;
};

export type AnalyzerCamera = {
  id: number;
  name: string;
  location: string | null;
  rtsp_url: string | null;
  stream_status: string | null;
  stream_port?: number | null;
  zone_count: number;
  first_frame_time: string | null;
  latest_frame_time: string | null;
};

export type AnalyzerRunResponse = {
  job_id: number;
  status: string;
  message: string;
};

export type AnalyzerJob = {
  id: number;
  camera_id: number;
  start_time: string;
  end_time: string;
  status: string;
  total_frames: number;
  processed_frames: number;
  unique_persons: number;
  error: string | null;
  created_at: string;
};

export type AnalyzerZoneStats = {
  zone_id: number | null;
  zone_name: string | null;
  unique_persons: number;
  total_appearances: number;
  total_dwell_seconds: number;
};

export type AnalyzerStats = {
  job_id: number;
  camera_id: number;
  status: string;
  unique_persons: number;
  zone_stats: AnalyzerZoneStats[];
  start_time: string;
  end_time: string;
};

export type AnalyzerTrack = {
  id: number;
  track_id: number;
  zone_id: number | null;
  first_seen: string;
  last_seen: string;
  appearances: number;
  avg_confidence: number;
  dwell_seconds: number;
};

export type TotalPersonFrameCount = {
  frame_id: number;
  timestamp: string | null;
  person_count: number;
};

export type TotalPersonDetectionResponse = {
  camera_id: number;
  start_time: string;
  end_time: string;
  detector_available: boolean;
  processed_frames: number;
  total_person_detections: number;
  max_person_count: number;
  avg_person_count: number;
  series: TotalPersonFrameCount[];
};

export type FacialExpressionFrameResult = {
  frame_id: number;
  timestamp: string | null;
  analyzed_faces: number;
  dominant_emotion: string | null;
  emotion_counts: Record<string, number>;
  avg_age?: number | null;
  dominant_gender?: string | null;
};

export type FacialExpressionRecognitionResponse = {
  camera_id: number;
  zone_id: number | null;
  zone_name: string | null;
  start_time: string;
  end_time: string;
  detector_available: boolean;
  processed_frames: number;
  analyzed_faces: number;
  dominant_emotion: string | null;
  emotion_counts: Record<string, number>;
  emotion_percentages: Record<string, number>;
  avg_age?: number | null;
  gender_counts?: Record<string, number>;
  age_distribution?: Record<string, number>;
  series: FacialExpressionFrameResult[];
};

export type FacialExpressionRealtimeResponse = {
  camera_id: number;
  zone_id: number | null;
  zone_name: string | null;
  frame_id: number | null;
  frame_timestamp: string | null;
  detector_available: boolean;
  zone_filter_available: boolean;
  analyzed_faces: number;
  dominant_emotion: string | null;
  emotion_counts: Record<string, number>;
  emotion_percentages: Record<string, number>;
  avg_age?: number | null;
  gender_counts?: Record<string, number>;
  age_distribution?: Record<string, number>;
};


export type QueueStatus = {
  available: boolean;
  fetcher_enabled: boolean;
};

export type QueueCamera = {
  id: number;
  name: string;
  location: string | null;
  rtsp_url: string | null;
  stream_status: string | null;
  zone_count: number;
  first_frame_time: string | null;
  latest_frame_time: string | null;
};

export type QueueZone = {
  id: number;
  name: string;
  polygon: {
    points?: Array<{ x: number; y: number }>;
    [key: string]: unknown;
  };
  capacity: number | null;
  expected_wait_time_sec: number | null;
  created_at: string;
};

export type QueueJob = {
  id: number;
  camera_id: number;
  zone_id: number;
  start_time: string;
  end_time: string;
  status: string;
  total_frames: number;
  processed_frames: number;
  max_queue_count: number;
  avg_queue_count: number;
  avg_wait_time_sec: number | null;
  avg_throughput_exits_per_sec: number | null;
  error: string | null;
  created_at: string;
};

export type QueueResult = {
  job_id: number;
  camera_id: number;
  zone_id: number;
  zone_name: string | null;
  status: string;
  max_queue_count: number;
  avg_queue_count: number;
  avg_line_length?: number | null;
  current_line_length?: number | null;
  avg_wait_time_sec: number | null;
  avg_throughput_exits_per_sec: number | null;
  avg_dwell_sec?: number | null;
  number_over_capacity?: number | null;
  frame_results: Array<{ timestamp: string; queue_count: number; wait_time_sec: number | null; throughput_exits_per_sec: number | null }>;
};

export type ReconstructionJob = {
  id: number;
  camera_id: number;
  start_time: string;
  end_time: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  output_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type RuntimeConfigResponse = {
  config: Record<string, unknown>;
};

function withQuery(path: string, query: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") {
      return;
    }
    params.set(key, String(value));
  });
  const queryText = params.toString();
  return queryText ? `${path}?${queryText}` : path;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) {
      return body.detail;
    }
  } catch {
    // noop
  }
  return `${response.status} ${response.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? {});
  const hasFormDataBody = typeof FormData !== "undefined" && init?.body instanceof FormData;
  if (!hasFormDataBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/api/backend${path}`, {
    ...init,
    cache: "no-store",
    headers,
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function getHealth() {
  return request<{ status: string }>("/health");
}

export async function getRuntimeConfig() {
  return request<RuntimeConfigResponse>("/api/config");
}

export async function updateRuntimeConfig(key: string, value: unknown) {
  return request<RuntimeConfigResponse>("/api/config", {
    method: "PUT",
    body: JSON.stringify({ key, value }),
  });
}

export async function listCameras() {
  return request<Camera[]>("/api/cameras");
}

export async function createCamera(payload: { name: string; rtsp_url?: string; location?: string }) {
  return request<Camera>("/api/cameras", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function uploadVideoSource(input: {
  file: File;
  name?: string;
  location?: string;
  cameraId?: number;
}) {
  const formData = new FormData();
  formData.append("file", input.file);

  const name = input.name?.trim();
  if (name) {
    formData.append("name", name);
  }
  if (input.location !== undefined) {
    formData.append("location", input.location);
  }
  if (input.cameraId) {
    formData.append("camera_id", String(input.cameraId));
  }

  return request<MediaUploadResponse>("/api/media/upload", {
    method: "POST",
    body: formData,
  });
}

export async function updateCamera(
  cameraId: number,
  payload: { name?: string; rtsp_url?: string | null; location?: string | null },
) {
  return request<Camera>(`/api/cameras/${cameraId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteCamera(cameraId: number) {
  return request<void>(`/api/cameras/${cameraId}`, { method: "DELETE" });
}

export async function listStreams() {
  return request<Stream[]>("/api/streams");
}

export async function deleteStreamRecord(streamId: number) {
  return request<void>(`/api/streams/${streamId}`, { method: "DELETE" });
}

export async function startStream(cameraId: number) {
  return request<Stream>("/api/streams/start", {
    method: "POST",
    body: JSON.stringify({ camera_id: cameraId }),
  });
}

export async function stopStream(input: { camera_id?: number; stream_id?: number }) {
  return request<Stream>("/api/streams/stop", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getMonitoringMetrics() {
  return request<MonitoringMetrics>("/api/monitoring/metrics");
}

export async function getRealtimeMonitoring(cameraIds?: number[]) {
  const params = new URLSearchParams();
  for (const cameraId of cameraIds ?? []) {
    params.append("camera_id", String(cameraId));
  }
  const query = params.toString();
  return request<RealtimeMonitoringResponse>(query ? `/api/monitoring/realtime?${query}` : "/api/monitoring/realtime");
}

export async function getAnalyticsRunState() {
  return request<AnalyticsRunState>("/api/monitoring/analytics");
}

export async function startAnalyticsRun() {
  return request<AnalyticsRunState>("/api/monitoring/analytics/start", {
    method: "POST",
  });
}

export async function stopAnalyticsRun() {
  return request<AnalyticsRunState>("/api/monitoring/analytics/stop", {
    method: "POST",
  });
}

export async function uploadBrowserWebcamFrame(input: {
  cameraId: number;
  frame: Blob;
  width?: number;
  height?: number;
}) {
  const formData = new FormData();
  formData.append("frame", input.frame, `camera-${input.cameraId}-${Date.now()}.jpg`);
  if (input.width && input.width > 0) {
    formData.append("width", String(input.width));
  }
  if (input.height && input.height > 0) {
    formData.append("height", String(input.height));
  }

  return request<BrowserFrameIngestResponse>(`/api/monitoring/cameras/${input.cameraId}/browser-frame`, {
    method: "POST",
    body: formData,
  });
}

export async function listZones(cameraId?: number) {
  return request<Zone[]>(withQuery("/api/analytics/zones", { camera_id: cameraId }));
}

export async function createZone(cameraId: number, name: string, polygon: Record<string, unknown>, capacity: number | null = null, expectedWaitTimeSec: number | null = null) {
  return request<Zone>("/api/analytics/zones", {
    method: "POST",
    body: JSON.stringify({ camera_id: cameraId, name, polygon, capacity, expected_wait_time_sec: expectedWaitTimeSec }),
  });
}

export async function deleteZone(zoneId: number) {
  await request<void>(`/api/analytics/zones/${zoneId}`, { method: "DELETE" });
}

export async function getFootfall(input: { cameraId: number; zoneId?: number | null; start?: string; end?: string }) {
  return request<FootfallResponse>(
    withQuery("/api/analytics/footfall", {
      camera_id: input.cameraId,
      zone_id: input.zoneId,
      start: input.start,
      end: input.end,
    })
  );
}

export async function getZoneAnalysis(input: { cameraId: number; zoneId?: number | null; start?: string; end?: string }) {
  return request<ZoneAnalysisResponse>(
    withQuery("/api/analytics/zone-analysis", {
      camera_id: input.cameraId,
      zone_id: input.zoneId,
      start: input.start,
      end: input.end,
    })
  );
}

export async function getAnalyzerStatus() {
  return request<AnalyzerStatus>("/api/analyzer/status");
}

export async function listAnalyzerCameras() {
  return request<AnalyzerCamera[]>("/api/analyzer/cameras");
}

export async function runAnalyzerAnalysis(payload: {
  camera_id: number;
  zone_id?: number | null;
  start: string;
  end: string;
}) {
  return request<AnalyzerRunResponse>("/api/analyzer/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function runTotalPersonDetection(payload: {
  camera_id: number;
  start: string;
  end: string;
}) {
  return request<TotalPersonDetectionResponse>("/api/analyzer/total-person-detection", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function runFacialExpressionRecognition(payload: {
  camera_id: number;
  zone_id?: number | null;
  start: string;
  end: string;
}) {
  return request<FacialExpressionRecognitionResponse>("/api/analyzer/facial-expression-recognition", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type FaceRecognitionFrameResult = {
  frame_id: number;
  timestamp: string | null;
  analyzed_faces: number;
  matched_faces: Array<{ name: string; distance: number }>;
};

export type FaceRecognitionResponse = {
  camera_id: number;
  zone_id: number | null;
  zone_name: string | null;
  start_time: string;
  end_time: string;
  detector_available: boolean;
  processed_frames: number;
  analyzed_faces: number;
  recognized_persons_counts: Record<string, number>;
  series: FaceRecognitionFrameResult[];
};

export async function runFaceRecognition(payload: {
  camera_id: number;
  zone_id?: number | null;
  start: string;
  end: string;
}) {
  return request<FaceRecognitionResponse>("/api/analyzer/face-recognition", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getFacialExpressionRealtime(input: { cameraId: number; zoneId?: number | null }) {
  return request<FacialExpressionRealtimeResponse>(
    withQuery(`/api/monitoring/cameras/${input.cameraId}/facial-expression/realtime`, {
      zone_id: input.zoneId,
    }),
  );
}

export async function getAnalyzerJob(jobId: number) {
  return request<AnalyzerJob>(`/api/analyzer/jobs/${jobId}`);
}

export async function listAnalyzerJobs(cameraId?: number | null) {
  return request<AnalyzerJob[]>(withQuery("/api/analyzer/jobs", { camera_id: cameraId }));
}

export async function getAnalyzerStats(jobId: number) {
  return request<AnalyzerStats>(`/api/analyzer/stats/${jobId}`);
}

export async function getAnalyzerTracks(jobId: number, zoneId?: number | null) {
  return request<AnalyzerTrack[]>(withQuery(`/api/analyzer/tracks/${jobId}`, { zone_id: zoneId }));
}

export async function getQueueStatus() {
  return request<QueueStatus>("/api/queue/status");
}

export async function listQueueCameras() {
  return request<QueueCamera[]>("/api/queue/cameras");
}

export async function listQueueZones(cameraId: number) {
  return request<QueueZone[]>(`/api/queue/zones/${cameraId}`);
}

export async function runQueueAnalysis(payload: {
  camera_id: number;
  zone_id: number;
  start: string;
  end: string;
}) {
  return request<{ job_id: number; status: string; message: string }>("/api/queue/analyze", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listQueueJobs(cameraId?: number | null, zoneId?: number | null) {
  return request<QueueJob[]>(withQuery("/api/queue/jobs", { camera_id: cameraId, zone_id: zoneId }));
}

export async function getQueueJob(jobId: number) {
  return request<QueueJob>(`/api/queue/jobs/${jobId}`);
}

export async function getQueueResult(jobId: number) {
  return request<QueueResult>(`/api/queue/results/${jobId}`);
}

export async function deleteQueueJob(jobId: number) {
  return request<void>(`/api/queue/jobs/${jobId}`, { method: "DELETE" });
}


export async function createReconstruction(payload: {
  camera_id: number;
  start_time: string;
  end_time: string;
  overlay_lines?: string[];
  analysis_job_id?: number | null;
  draw_tracking?: boolean;
  draw_facial_expression?: boolean;
  facial_expression_zone_id?: number | null;
  output_fps?: number | null;
}) {
  return request<ReconstructionJob>("/api/reconstructions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getReconstruction(jobId: number) {
  return request<ReconstructionJob>(`/api/reconstructions/${jobId}`);
}

export type ReidVideoJob = {
  job_id: string;
  camera_id: number;
  zone_id: number | null;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  unique_persons: number;
  error: string | null;
  download_url: string | null;
};

/** Returns the saved (or in-progress) result when one exists for the same video and Re-ID settings. */
export async function startReidVideo(cameraId: number, zoneId?: number | null, force = false) {
  return request<ReidVideoJob>("/api/analyzer/reid-video", {
    method: "POST",
    body: JSON.stringify({ camera_id: cameraId, zone_id: zoneId ?? null, force }),
  });
}

/** Saved or in-progress Re-ID video for this source, or null when none exists for the current settings. */
export async function getLatestReidVideo(cameraId: number, zoneId?: number | null): Promise<ReidVideoJob | null> {
  const response = await fetch(
    `/api/backend${withQuery("/api/analyzer/reid-video", { camera_id: cameraId, zone_id: zoneId ?? undefined })}`,
    { cache: "no-store" },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as ReidVideoJob;
}

export async function getReidVideo(jobId: string) {
  return request<ReidVideoJob>(`/api/analyzer/reid-video/${jobId}`);
}

export type FaceReferenceModel = {
  id: number;
  name: string;
  image_path: string;
  created_at: string;
};

export type FaceReferenceBatchResponse = {
  created: FaceReferenceModel[];
  errors: Array<{
    filename: string;
    detail: string;
  }>;
};

export async function listFaces() {
  return request<FaceReferenceModel[]>("/api/faces");
}

export async function createFace(name: string, files: File[]) {
  const formData = new FormData();
  formData.append("name", name);
  for (const file of files) {
    formData.append("files", file);
  }

  return request<FaceReferenceBatchResponse>("/api/faces", {
    method: "POST",
    body: formData,
  });
}

export async function deleteFace(faceId: number) {
  return request<{ status: string }>(`/api/faces/${faceId}`, {
    method: "DELETE",
  });
}

export type InsightsReid = {
  zone: string;
  unique_persons: number;
  total_detections: number;
  duration_seconds: number;
  identities: { id: number; seconds: number }[];
  timeline: [number, number][];
  processed_at: number | null;
  download_url: string;
  job_id: string;
};

export type InsightsCamera = {
  id: number;
  name: string;
  source_type: "Video File" | "RTSP" | "Webcam";
  live: boolean;
  zones: number;
  frames: number;
  entries: number;
  exits: number;
  unique_persons: number;
  detections: number;
  reid: InsightsReid | null;
};

export type InsightsOverview = {
  generated_at: string;
  totals: {
    cameras: number;
    live_cameras: number;
    zones: number;
    enrolled_faces: number;
    frames_captured: number;
    reid_videos: number;
    unique_persons: number;
    detections: number;
    entries: number;
    exits: number;
  };
  cameras: InsightsCamera[];
  activity: { hour: string; frames: number; entries: number; exits: number }[];
  queues: {
    camera_id: number;
    zone_id: number;
    zone_name: string;
    avg_wait_time_sec: number | null;
    avg_queue_count: number;
    max_queue_count: number;
  }[];
};

export async function getInsightsOverview() {
  return request<InsightsOverview>("/api/insights/overview");
}

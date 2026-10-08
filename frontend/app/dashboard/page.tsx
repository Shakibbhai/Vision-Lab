"use client";

import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Pause, Play, Plus, Search, Trash2, Filter } from "lucide-react";

import type { RealtimeZoneCount } from "@/lib/api";
import { type FootfallResponse, type Zone, getFootfall, getRuntimeConfig, listZones } from "@/lib/api";
import {
  DASHBOARD_OVERLAY_CONFIG_KEY,
  type DashboardOverlayConfig,
  getDashboardOverlayModes,
  normalizeDashboardOverlayConfig,
  readDashboardOverlayConfigFromStorage,
  writeDashboardOverlayConfigToStorage,
} from "@/lib/dashboard-overlay-config";
import { isZoneFilterSelected, parseSelectedZoneId } from "@/lib/zone-selection";
import { usePageVisibility } from "@/hooks/use-page-visibility";
import { useSourcesData } from "@/hooks/use-sources-data";
import { ReidDownloadButton } from "@/components/v2/reid-download-button";
import {
  CameraFeed,
  MiniLineChart,
  MultiZoneLineChart,
  PanelCard,
  StatTile,
  StatusPill
} from "@/components/v2/ui";
import { PersonReidAnalyticsPanel } from "@/components/v2/person-reid-analytics-panel";
import { ZoneQuickEditor } from "@/components/v2/zone-quick-editor";
import { QueueAnalyticsPanel } from "@/components/v2/queue-analytics-panel";
import { ZoneAnalysisPanel } from "@/components/v2/zone-analysis-panel";
import { FacialExpressionPanel } from "@/components/v2/facial-expression-panel";

type AnalyticsMode =
  | "person_count"
  | "person_reid"
  | "entry_exit_count"
  | "queue"
  | "facial_expression"
  | "trajectory_heatmap"
  | "face_recognition";

const ANALYTICS_MODES: { id: AnalyticsMode; label: string; desc: string }[] = [
  { id: "person_count", label: "Person Count", desc: "Standard detection and unique IDs" },
  { id: "trajectory_heatmap", label: "Trajectory Heatmap", desc: "Object tracking trajectory-density overlay" },
  { id: "facial_expression", label: "Facial Expression", desc: "Emotion recognition in a selected zone" },
  { id: "face_recognition", label: "Face Recognition", desc: "Known-face overlays on the live feed" },
  { id: "queue", label: "Queue Management", desc: "Waiting times & line length" },
  { id: "entry_exit_count", label: "Entry/Exit Count", desc: "Track line-crossing per zone" },
  { id: "person_reid", label: "Person ReID", desc: "Track identities across frames" },
];

const ANALYTICS_SEARCH_TERMS: Record<AnalyticsMode, string[]> = {
  person_count: ["person count", "person", "count", "crowd"],
  trajectory_heatmap: ["trajectory", "heatmap", "path heatmap", "tracking heatmap", "object tracking heatmap"],
  facial_expression: ["facial expression", "expression", "emotion", "facial", "face emotion"],
  face_recognition: ["face recognition", "face rec", "known face", "identity", "match"],
  queue: ["queue", "queue management", "line", "wait", "waiting"],
  entry_exit_count: ["entry", "exit", "entry exit", "entry/exit", "extry", "extry exit"],
  person_reid: ["person reid", "reid", "re-id", "identity", "track id"],
};

const ZONE_ANALYSIS_SEARCH_TERMS = ["zone analysis", "zone", "congestion", "path", "heatmap", "staff", "service"];
const OVERLAY_MODE_STORAGE_KEY = "dashboard_overlay_mode";

type TimelinePoint = {
  ts: number;
  person: number;
};

function ModeAnalyticsPanel({
  timeline,
  zones = [],
  liveZones = [],
  footfall = null,
  selectedZoneId = "",
  isPersonCountMode = false,
  sourceType = "RTSP",
}: {
  timeline: TimelinePoint[];
  zones?: Zone[];
  liveZones?: RealtimeZoneCount[];
  footfall?: FootfallResponse | null;
  selectedZoneId?: string;
  isPersonCountMode?: boolean;
  sourceType?: "RTSP" | "Video File" | string;
}) {
  const current = timeline[timeline.length - 1] ?? {
    ts: Date.now(),
    person: 0,
  };
  const selectedZoneNumber = useMemo(() => parseSelectedZoneId(selectedZoneId), [selectedZoneId]);

  const avgPerson =
    timeline.length > 0
      ? Math.round(timeline.reduce((sum, point) => sum + point.person, 0) / timeline.length)
      : 0;

  const availableVideoHours = footfall?.video_available_hours ?? 0;
  const peakHourPayload = footfall?.peak_person_count_hour ?? null;
  const mostCrowdedQuarter = footfall?.most_crowded_quarter ?? null;
  const isVideoSource = sourceType === "Video File";
  const peakWindowLabel = formatTimeOnlyRange(peakHourPayload?.hour_start, peakHourPayload?.hour_end);
  const peakWindowDate = formatDateLabel(peakHourPayload?.hour_start);
  const peakHint =
    peakWindowLabel === "N/A"
      ? "No hourly person-count history"
      : `${peakHourPayload?.zone_name ?? "Selected scope"} | ${peakWindowDate} | Max persons: ${peakHourPayload?.person_count ?? 0}`;
  const crowdedQuarterRange = formatDateTimeRange(
    mostCrowdedQuarter?.start_time,
    mostCrowdedQuarter?.end_time,
  );
  const crowdedQuarterHint =
    crowdedQuarterRange === "N/A"
      ? "No quarter crowding history"
      : `${mostCrowdedQuarter?.zone_name ?? "Selected scope"} | ${crowdedQuarterRange}`;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatTile label="People Now" value={current.person} hint="Detected in frame" />
        <StatTile label="Peak" value={Math.max(...timeline.map((point) => point.person), 0)} hint="Current window" />
        <StatTile label="Average" value={avgPerson} hint="Rolling average" />
        <StatTile
          label="Video Available"
          value={`${availableVideoHours.toFixed(2)}h`}
          hint="Captured in selected window"
        />
        {isPersonCountMode ? (
          isVideoSource ? (
            <StatTile
              label="Most Crowded Quarter"
              value={mostCrowdedQuarter?.quarter_label ?? "N/A"}
              hint={crowdedQuarterHint}
            />
          ) : (
            <StatTile
              label="Peak Hour Window"
              value={peakWindowLabel}
              hint={peakHint}
            />
          )
        ) : null}
      </div>
      <div className="rounded-xl border border-border/70 bg-white/80 p-3">
        <MiniLineChart values={timeline.map((point) => point.person)} />
      </div>
      
      {zones.length > 0 && selectedZoneNumber === null && (
        <div className="overflow-x-auto rounded-xl border border-border/70 bg-white/80 mt-3">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-muted/40 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Zone</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Live Count</th>
                <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Capacity / Status</th>
              </tr>
            </thead>
            <tbody>
              {zones.map((zone) => {
                const liveCount = liveZones.find((lz) => lz.zone_id === zone.id)?.person_count ?? 0;
                const capacity = zone.capacity;
                const statusStr = capacity 
                  ? (liveCount > capacity ? `+${liveCount - capacity} Extra Persons` : `${liveCount} / ${capacity} Capacity`) 
                  : "-";
                  
                return (
                  <tr key={zone.id} className="border-t border-border/50">
                    <td className="px-3 py-2 text-foreground">{zone.name}</td>
                    <td className="px-3 py-2 tabular-nums text-sky-700">{liveCount}</td>
                    <td className="px-3 py-2 tabular-nums font-semibold">
                      <span className={capacity && liveCount > capacity ? "text-rose-600" : "text-emerald-600"}>{statusStr}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function toTimelinePoint(currentPerson: number): TimelinePoint {
  return {
    ts: Date.now(),
    person: currentPerson,
  };
}

function EntryExitAnalyticsPanel({
  footfall,
  liveZones = [],
  searchQuery = "",
  selectedZoneId = "",
}: {
  footfall: FootfallResponse | null;
  liveZones?: RealtimeZoneCount[];
  searchQuery?: string;
  selectedZoneId?: string;
}) {
  const selectedZoneNumber = useMemo(() => parseSelectedZoneId(selectedZoneId), [selectedZoneId]);
  const hasRealEvents = useMemo(() => {
    if (!footfall) return false;
    if (footfall.total_entries > 0 || footfall.total_exits > 0) return true;
    return footfall.series?.some((point) => point.entry_count > 0 || point.exit_count > 0 || point.count > 0) ?? false;
  }, [footfall]);

  const filteredZoneTotals = useMemo(() => {
    if (!footfall) return [];

    // First apply explicit Zone ID filter if set
    let activeTotals = footfall.zone_totals || [];
    if (selectedZoneNumber !== null) {
      activeTotals = activeTotals.filter((t) => t.zone_id === selectedZoneNumber);
    }

    // Hide placeholder all-zero rows (configured zones without real transitions AND zero live presence).
    activeTotals = activeTotals.filter((z) => {
      const hasTransitions = z.total_entries > 0 || z.total_exits > 0;
      const liveCount = liveZones?.find((lz) => lz.zone_id === z.zone_id)?.person_count ?? 0;
      return hasTransitions || liveCount > 0;
    });

    // Then apply exact search formatting
    if (!searchQuery.trim()) return activeTotals;
    const lower = searchQuery.toLowerCase();
    return activeTotals.filter((z) => {
      const name = z.zone_name ?? `Zone ${z.zone_id ?? "-"}`;
      return name.toLowerCase().includes(lower);
    });
  }, [footfall, searchQuery, selectedZoneNumber, liveZones]);

  const activeZoneSeriesData = useMemo(() => {
    if (!footfall || selectedZoneNumber === null) return null;
    const zNum = selectedZoneNumber;

    const zName = footfall.zone_totals?.find(t => t.zone_id === zNum)?.zone_name ?? `Zone ${zNum}`;
    const pts = footfall.series.filter(pt => pt.zone_id === zNum);
    const recent = pts.slice(-36);

    return {
      name: zName,
      entries: { id: "in", name: "Entries", color: "#0f766e", values: recent.map(p => p.entry_count) },
      exits: { id: "out", name: "Exits", color: "#dc2626", values: recent.map(p => p.exit_count) },
    };
  }, [footfall, selectedZoneNumber]);

  const peakTrafficWindow = useMemo(
    () => formatTimeOnlyRange(footfall?.peak_traffic_hour?.hour_start, footfall?.peak_traffic_hour?.hour_end),
    [footfall],
  );
  const peakTrafficDate = useMemo(
    () => formatDateLabel(footfall?.peak_traffic_hour?.hour_start),
    [footfall],
  );

  if (!footfall) {
    return <p className="text-sm text-muted-foreground">No entry/exit analytics available yet for this camera.</p>;
  }
  if (!hasRealEvents && (!liveZones || liveZones.length === 0)) {
    return <p className="text-sm text-muted-foreground">No real entry/exit events recorded yet for this camera.</p>;
  }

  const liveByZoneId = new Map<number, number>();
  for (const zone of liveZones) {
    liveByZoneId.set(zone.zone_id, zone.person_count);
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile label="Total Entries" value={footfall.total_entries} hint="Tracked with ByteTrack IDs" />
        <StatTile label="Total Exits" value={footfall.total_exits} hint="Tracked with ByteTrack IDs" />
        <StatTile label="Net Flow" value={footfall.net_flow} hint="Entries minus exits" />
        <StatTile
          label="Peak Hour Window"
          value={peakTrafficWindow}
          hint={
            peakTrafficWindow === "N/A"
              ? "Highest traffic window"
              : `${peakTrafficDate} | Traffic: ${footfall.peak_traffic_hour?.traffic_count ?? 0}`
          }
        />
        <StatTile label="Estimated Occupancy" value={Math.max(footfall.net_flow, 0)} hint="Derived occupancy trend" />
      </div>

      {selectedZoneNumber !== null && activeZoneSeriesData ? (
        <div className="rounded-xl border border-border/70 bg-white/80 p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Traffic Trend ({activeZoneSeriesData.name})</p>
          <div className="mb-2 space-y-2">
            <MultiZoneLineChart series={[activeZoneSeriesData.entries, activeZoneSeriesData.exits]} />
            <div className="flex flex-wrap gap-2 text-[10px] font-semibold">
              <div className="flex items-center gap-1">
                <div className="h-2 w-2 rounded-full" style={{ backgroundColor: activeZoneSeriesData.entries.color }} />
                <span className="text-muted-foreground">Entries</span>
              </div>
              <div className="flex items-center gap-1">
                <div className="h-2 w-2 rounded-full" style={{ backgroundColor: activeZoneSeriesData.exits.color }} />
                <span className="text-muted-foreground">Exits</span>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground italic my-4 px-2">Select a specific zone from the primary feed controls to view its traffic trend.</p>
      )}

      <div className="overflow-x-auto rounded-xl border border-border/70 bg-white/80">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Zone</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Live</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Entries</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Exits</th>
              <th className="px-3 py-2 font-semibold uppercase tracking-[0.1em]">Net</th>
            </tr>
          </thead>
          <tbody>
            {filteredZoneTotals.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-muted-foreground" colSpan={5}>
                  {searchQuery.trim() ? "No zones match your search." : "No zone transitions recorded in the selected window."}
                </td>
              </tr>
            ) : (
              filteredZoneTotals.map((zone) => (
                <tr key={`${zone.zone_id ?? "none"}-${zone.zone_name ?? "zone"}`} className="border-t border-border/50">
                  <td className="px-3 py-2 text-foreground">{zone.zone_name ?? `Zone ${zone.zone_id ?? "-"}`}</td>
                  <td className="px-3 py-2 tabular-nums text-sky-700">
                    {zone.zone_id !== null ? (liveByZoneId.get(zone.zone_id) ?? 0) : "-"}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-emerald-700">{zone.total_entries}</td>
                  <td className="px-3 py-2 tabular-nums text-rose-700">{zone.total_exits}</td>
                  <td className="px-3 py-2 tabular-nums font-semibold text-foreground">{zone.net_flow}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function hoursAgoIso(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

function formatDateTimeRange(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return "N/A";
  const parsedStart = new Date(start);
  const parsedEnd = new Date(end);
  if (Number.isNaN(parsedStart.getTime()) || Number.isNaN(parsedEnd.getTime())) return "N/A";
  const s = parsedStart.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
  const e = parsedEnd.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
  return `${s} to ${e}`;
}

function formatTimeOnlyRange(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return "N/A";
  const parsedStart = new Date(start);
  const parsedEnd = new Date(end);
  if (Number.isNaN(parsedStart.getTime()) || Number.isNaN(parsedEnd.getTime())) return "N/A";
  const s = parsedStart.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true }).toLowerCase();
  const e = parsedEnd.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true }).toLowerCase();

  const sMatch = /^(\d{1,2}:\d{2})\s*([ap]m)$/.exec(s);
  const eMatch = /^(\d{1,2}:\d{2})\s*([ap]m)$/.exec(e);
  if (!sMatch || !eMatch) return `${s}-${e}`;

  const sTime = sMatch[1];
  const sPeriod = sMatch[2];
  const eTime = eMatch[1];
  const ePeriod = eMatch[2];

  if (sPeriod === ePeriod) {
    return `${sTime}-${eTime} ${ePeriod}`;
  }
  return `${sTime} ${sPeriod}-${eTime} ${ePeriod}`;
}

function formatDateLabel(value: string | null | undefined): string {
  if (!value) return "N/A";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "N/A";
  return parsed.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

export default function DashboardPage() {
  const router = useRouter();
  const isPageVisible = usePageVisibility();
  const [overlayMode, setOverlayMode] = useState<AnalyticsMode>("person_count");
  const enableRealtimeMonitoring = true;
  const {
    busy,
    error,
    notice,


    analyticsRunning,
    sources,
    sourceById,
    toggleStreamForCamera,
    deleteSourceByCamera,

  } = useSourcesData({ enableRealtime: enableRealtimeMonitoring });

  const [selectedCameraId, setSelectedCameraId] = useState<number | null>(null);
  const [queryCameraId, setQueryCameraId] = useState<number | null>(null);
  const [selectedZoneId, setSelectedZoneId] = useState<string>("");
  const [historyByCamera, setHistoryByCamera] = useState<Record<string, TimelinePoint[]>>({});
  const [zonesByCamera, setZonesByCamera] = useState<Record<number, Zone[]>>({});
  const [footfallByCamera, setFootfallByCamera] = useState<Record<number, FootfallResponse>>({});
  const [searchQuery, setSearchQuery] = useState("");
  const [analyticsSearchQuery, setAnalyticsSearchQuery] = useState("");
  const [timeRange, setTimeRange] = useState<"12h" | "today" | "24h">("today");
  const [showZoneEditor, setShowZoneEditor] = useState(false);
  const [dashboardOverlayConfig, setDashboardOverlayConfig] = useState<DashboardOverlayConfig>({});
  const [overlayConfigLoaded, setOverlayConfigLoaded] = useState(false);
  const deferredSelectedZoneId = useDeferredValue(selectedZoneId);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(OVERLAY_MODE_STORAGE_KEY);
    if (!stored) return;
    const valid = ANALYTICS_MODES.some((mode) => mode.id === stored);
    if (valid) {
      setOverlayMode(stored as AnalyticsMode);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(OVERLAY_MODE_STORAGE_KEY, overlayMode);
  }, [overlayMode]);

  const loadDashboardOverlayConfig = useCallback(async () => {
    try {
      const payload = await getRuntimeConfig();
      const runtimeConfig = normalizeDashboardOverlayConfig(payload.config?.[DASHBOARD_OVERLAY_CONFIG_KEY]);
      const nextConfig =
        Object.keys(runtimeConfig).length > 0 ? runtimeConfig : readDashboardOverlayConfigFromStorage();
      setDashboardOverlayConfig(nextConfig);
      writeDashboardOverlayConfigToStorage(nextConfig);
    } catch {
      setDashboardOverlayConfig(readDashboardOverlayConfigFromStorage());
    } finally {
      setOverlayConfigLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!isPageVisible) return;
    void loadDashboardOverlayConfig();
  }, [isPageVisible, loadDashboardOverlayConfig]);

  const getStartTimestamp = useCallback(() => {
    if (timeRange === "12h") return hoursAgoIso(12);
    if (timeRange === "24h") return hoursAgoIso(24);
    if (timeRange === "today") {
       const d = new Date();
       d.setHours(0, 0, 0, 0);
       return d.toISOString();
    }
    return hoursAgoIso(12);
  }, [timeRange]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const fromQuery = Number(new URLSearchParams(window.location.search).get("camera_id"));
    if (Number.isNaN(fromQuery) || fromQuery <= 0) return;
    setQueryCameraId(fromQuery);
  }, []);

  useEffect(() => {
    if (!queryCameraId) return;
    setSelectedCameraId(queryCameraId);
  }, [queryCameraId]);

  useEffect(() => {
    if (sources.length === 0) {
      setSelectedCameraId(null);
      return;
    }

    if (!selectedCameraId || !sourceById.has(selectedCameraId)) {
      setSelectedCameraId(sources[0].camera.id);
    }
  }, [selectedCameraId, sourceById, sources]);

  const selected = selectedCameraId ? sourceById.get(selectedCameraId) ?? null : null;
  const availableOverlayModes = useMemo(() => {
    if (!selected) return [];
    const zoneId = parseSelectedZoneId(selectedZoneId);
    const allowedModes = new Set(getDashboardOverlayModes(dashboardOverlayConfig, selected.camera.id, zoneId));
    return ANALYTICS_MODES.filter((mode) => allowedModes.has(mode.id));
  }, [dashboardOverlayConfig, selected, selectedZoneId]);
  const activeOverlayMode = useMemo<AnalyticsMode | null>(() => {
    if (availableOverlayModes.some((mode) => mode.id === overlayMode)) {
      return overlayMode;
    }
    return availableOverlayModes[0]?.id ?? null;
  }, [availableOverlayModes, overlayMode]);
  const deferredOverlayMode = useDeferredValue(activeOverlayMode);

  useEffect(() => {
    if (!activeOverlayMode || overlayMode === activeOverlayMode) return;
    setOverlayMode(activeOverlayMode);
  }, [activeOverlayMode, overlayMode]);

  useEffect(() => {
    if (!analyticsRunning || deferredOverlayMode !== "person_count") return;
    setHistoryByCamera((previous) => {
      const next = { ...previous };

      for (const source of sources) {
        if (!source.realtime) continue;
        // If a specific zone is selected, use that zone's live counts for the timeline
        let currentPerson = source.realtime.total_detected_person_count;
        const zoneId = parseSelectedZoneId(deferredSelectedZoneId);

        if (zoneId !== null) {
          const activeZone = source.realtime.zones?.find((z) => z.zone_id === zoneId);
          if (activeZone) {
            currentPerson = activeZone.person_count;
          } else {
            currentPerson = 0;
          }
        }

        const historyKey = `${source.camera.id}-${zoneId ?? "none"}`;
        const list = next[historyKey] ?? [];
        const point = toTimelinePoint(currentPerson);
        next[historyKey] = [...list.slice(-23), point];
      }

      return next;
    });
  }, [analyticsRunning, deferredOverlayMode, deferredSelectedZoneId, sources]);

  const loadZones = useCallback(async (cameraId: number) => {
    try {
      const rows = await listZones(cameraId);
      setZonesByCamera((previous) => ({ ...previous, [cameraId]: rows }));
    } catch {
      // Keep previous zones to avoid interrupting dashboard view on transient issues.
    }
  }, []);

  useEffect(() => {
    if (!selectedCameraId) return;
    void loadZones(selectedCameraId);
    if (!isPageVisible) return;
    const id = window.setInterval(() => {
      void loadZones(selectedCameraId);
    }, 5000);
    return () => window.clearInterval(id);
  }, [isPageVisible, loadZones, selectedCameraId]);

  useEffect(() => {
    if (!selectedZoneId) return;
    if (!selectedCameraId) {
      setSelectedZoneId("");
      return;
    }

    const cameraZones = zonesByCamera[selectedCameraId] ?? [];
    const zoneExists = cameraZones.some((zone) => zone.id.toString() === selectedZoneId);
    if (!zoneExists) {
      setSelectedZoneId("");
    }
  }, [selectedCameraId, selectedZoneId, zonesByCamera]);

  const loadFootfall = useCallback(async (cameraId: number, zoneId: string) => {
    try {
      const parsedZoneId = parseSelectedZoneId(zoneId);
      const payload = await getFootfall({
        cameraId,
        zoneId: parsedZoneId ?? undefined,
        start: getStartTimestamp(),
      });
      setFootfallByCamera((previous) => ({
        ...previous,
        [cameraId]: payload,
      }));
    } catch {
      // Keep latest successful analytics payload in the dashboard.
    }
  }, [getStartTimestamp]);

  useEffect(() => {
    const shouldPollFootfall = deferredOverlayMode === "person_count" || deferredOverlayMode === "entry_exit_count";
    if (!selectedCameraId || !shouldPollFootfall) return;
    void loadFootfall(selectedCameraId, deferredSelectedZoneId);
    if (!isPageVisible) return;
    const id = window.setInterval(() => {
      void loadFootfall(selectedCameraId, deferredSelectedZoneId);
    }, 5000);
    return () => window.clearInterval(id);
  }, [deferredOverlayMode, deferredSelectedZoneId, isPageVisible, loadFootfall, selectedCameraId]);

  const selectedTimeline = useMemo(() => {
    if (!selected) return [];
    const historyKey = `${selected.camera.id}-${parseSelectedZoneId(deferredSelectedZoneId) ?? "none"}`;
    const series = historyByCamera[historyKey] ?? [];
    if (series.length === 0) {
      return [toTimelinePoint(0)];
    }
    return series;
  }, [deferredSelectedZoneId, historyByCamera, selected]);
  const selectedFootfall = useMemo(() => {
    if (!selected) return null;
    return footfallByCamera[selected.camera.id] ?? null;
  }, [footfallByCamera, selected]);

  const analyticsQuery = analyticsSearchQuery.trim().toLowerCase();
  const hasAnalyticsQuery = analyticsQuery.length > 0;
  const matchesAnalyticsPanel = useCallback(
    (mode: AnalyticsMode): boolean => {
      if (!hasAnalyticsQuery) return true;
      const terms = ANALYTICS_SEARCH_TERMS[mode];
      return terms.some((term) => term.includes(analyticsQuery) || analyticsQuery.includes(term));
    },
    [analyticsQuery, hasAnalyticsQuery],
  );
  const showPersonCountPanel = deferredOverlayMode === "person_count" && matchesAnalyticsPanel("person_count");
  const showTrajectoryHeatmapPanel =
    deferredOverlayMode === "trajectory_heatmap" && matchesAnalyticsPanel("trajectory_heatmap");
  const showFacialExpressionPanel = deferredOverlayMode === "facial_expression" && matchesAnalyticsPanel("facial_expression");
  const showFaceRecognitionPanel = deferredOverlayMode === "face_recognition" && matchesAnalyticsPanel("face_recognition");
  const showEntryExitPanel = deferredOverlayMode === "entry_exit_count" && matchesAnalyticsPanel("entry_exit_count");
  const showPersonReidPanel = deferredOverlayMode === "person_reid" && matchesAnalyticsPanel("person_reid");
  const queueMatchedBySearch = matchesAnalyticsPanel("queue");
  const showZoneAnalysisPanel =
    deferredOverlayMode === "person_count" &&
    (!hasAnalyticsQuery ||
      ZONE_ANALYSIS_SEARCH_TERMS.some((term) => term.includes(analyticsQuery) || analyticsQuery.includes(term)));
  const showQueuePanel = queueMatchedBySearch && deferredOverlayMode === "queue";
  const showQueueOverlayHint = hasAnalyticsQuery && queueMatchedBySearch && deferredOverlayMode !== "queue";
  const hasMatchedAnalyticsPanel =
    showPersonCountPanel ||
    showTrajectoryHeatmapPanel ||
    showFacialExpressionPanel ||
    showFaceRecognitionPanel ||
    showEntryExitPanel ||
    showPersonReidPanel ||
    showQueuePanel ||
    showZoneAnalysisPanel ||
    showQueueOverlayHint;
  const showOverlayConfigurationHint = overlayConfigLoaded && selected && availableOverlayModes.length === 0;
  const entryExitZoneSearchQuery = "";



  const filteredSources = useMemo(() => {
    if (!searchQuery.trim()) return sources;
    const lower = searchQuery.toLowerCase();
    return sources.filter((s) => s.camera.name.toLowerCase().includes(lower) || s.endpoint.toLowerCase().includes(lower));
  }, [searchQuery, sources]);

  async function handleDelete(cameraId: number) {
    const confirmed = window.confirm(`Delete camera #${cameraId}?`);
    if (!confirmed) return;
    await deleteSourceByCamera(cameraId);
  }

  return (
    <div className="space-y-3">



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

      <div className="grid gap-3 xl:grid-cols-[290px_1fr]">
        <PanelCard
          title="Camera Sources"
          subtitle="Live preview and controls"
          right={
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => router.push("/configurations?tab=source")}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-3 text-xs font-semibold text-primary transition hover:bg-primary/20 whitespace-nowrap"
              >
                <Plus className="h-3.5 w-3.5" /> Add Source
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!selected) return;
                  void handleDelete(selected.camera.id);
                }}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 text-xs font-semibold text-destructive transition hover:bg-destructive/20 disabled:cursor-not-allowed disabled:opacity-60 whitespace-nowrap"
                disabled={!selected || busy}
              >
                <Trash2 className="h-3.5 w-3.5" /> Remove
              </button>
            </div>
          }
        >
          <div className="mb-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search cameras..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-white pl-9 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          </div>
          <div className="grid max-h-[calc(100vh-320px)] gap-3 overflow-y-auto pr-1">
            {filteredSources.length === 0 ? (
              <p className="text-sm text-muted-foreground">No cameras found.</p>
            ) : (
              filteredSources.map((source) => {
                const active = source.camera.id === selected?.camera.id;
                return (
                  <div
                    key={source.camera.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedCameraId(source.camera.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedCameraId(source.camera.id);
                      }
                    }}
                    className={cn(
                      "rounded-xl border p-3 cursor-pointer transition",
                      active
                        ? "border-primary bg-primary/10"
                        : "border-border/70 bg-white/75 hover:bg-accent/60",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-left text-sm font-semibold text-foreground" title={source.camera.name}>
                        {source.camera.name}
                      </p>
                      <StatusPill status={source.status} />
                    </div>
                    <p className="mt-2 truncate text-[11px] text-muted-foreground">
                      Last frame: {formatFrameTimestamp(source.realtime?.frame_timestamp)}
                    </p>
                    <div className="mt-2.5 flex items-center justify-end gap-2">
                      <div className="flex items-center gap-1.5">
                        {source.type === "Video File" ? (
                          <ReidDownloadButton cameraId={source.camera.id} cameraName={source.camera.name} />
                        ) : null}
                        <button
                          type="button"
                          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-primary/40 bg-primary/10 text-primary transition hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-60"
                          title={source.status === "live" ? "Pause stream" : "Start stream"}
                          onClick={(e) => {
                            e.stopPropagation();
                            void toggleStreamForCamera(source.camera.id);
                          }}
                          disabled={busy}
                        >
                          {source.status === "live" ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                        </button>
                        <button
                          type="button"
                          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-primary/40 bg-primary/10 text-primary transition hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-60"
                          title="Edit source"
                          onClick={(e) => {
                            e.stopPropagation();
                            router.push(`/configurations?tab=source&edit=${source.camera.id}`);
                          }}
                          disabled={busy}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-destructive/40 bg-destructive/10 text-destructive transition hover:bg-destructive/20 disabled:cursor-not-allowed disabled:opacity-60"
                          title="Delete source"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleDelete(source.camera.id);
                          }}
                          disabled={busy}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </PanelCard>

        <div className="space-y-3">
          <PanelCard
            title="Primary Feed"
            subtitle="Live feed and detection overlay while analytics is running"
            right={
              <div className="flex items-center gap-2">
                <select
                  value={activeOverlayMode ?? ""}
                  onChange={(e) => {
                    if (!e.target.value) return;
                    setOverlayMode(e.target.value as AnalyticsMode);
                  }}
                  disabled={!overlayConfigLoaded || availableOverlayModes.length === 0}
                  className="h-9 rounded-lg border border-input bg-white px-3 text-sm text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  {!overlayConfigLoaded ? (
                    <option value="">Loading configured overlays...</option>
                  ) : availableOverlayModes.length === 0 ? (
                    <option value="">No configured overlays</option>
                  ) : (
                    availableOverlayModes.map((mode) => (
                      <option key={mode.id} value={mode.id}>
                        Overlay: {mode.label}
                      </option>
                    ))
                  )}
                </select>
                <select
                  value={selectedZoneId}
                  onChange={(e) => setSelectedZoneId(e.target.value)}
                  className="h-9 rounded-lg border border-input bg-white px-3 text-sm text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">Select a Zone</option>
                  {selected && zonesByCamera[selected.camera.id]?.map((z) => (
                    <option key={z.id} value={z.id.toString()}>
                      {z.name}
                    </option>
                  ))}
                </select>
              </div>
            }
          >
            {selected ? (
              <>
                <CameraFeed
                  key={`primary-${selected.camera.id}`}
                  cameraId={selected.camera.id}
                  title={selected.camera.name}
                  status={selected.status}
                  sourceType={selected.type}
                  resolution={selected.resolution}
                  fps={selected.fps}
                  zones={zonesByCamera[selected.camera.id] ?? []}
                  detectionBoxes={selected.realtime?.detection_boxes ?? []}
                  hasLiveFrame={selected.hasLiveFrame}
                  staticFirstFrame={false}
                  analyticsMode={activeOverlayMode}
                  zoneId={selectedZoneId || undefined}
                />

              </>
            ) : (
              <p className="text-sm text-muted-foreground">No source selected.</p>
            )}
          </PanelCard>
        </div>
      </div>

      {selected ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between rounded-xl border border-border/70 bg-white p-3">
            <div className="flex items-center gap-2">
              <Filter className="h-5 w-5 text-primary" />
              <h3 className="font-semibold text-foreground">Analytics Data</h3>
            </div>
            <div className="flex items-center gap-3">
              <select
                value={timeRange}
                onChange={(e) => setTimeRange(e.target.value as "12h" | "today" | "24h")}
                className="h-9 rounded-lg border border-input bg-white px-3 text-xs text-foreground shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <option value="12h">Last 12 Hours</option>
                <option value="24h">Last 24 Hours</option>
                <option value="today">Today</option>
              </select>
              <div className="relative w-48 sm:w-64">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search analytics: person, emotion, queue, reid, entry/exit, zone..."
                  value={analyticsSearchQuery}
                  onChange={(e) => setAnalyticsSearchQuery(e.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-white pl-9 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            </div>
          </div>

          <div className="grid gap-3">
            {showOverlayConfigurationHint ? (
              <div className="rounded-xl border border-border/70 bg-white px-4 py-3 text-sm text-muted-foreground">
                No analytics overlays are configured for this selection. Use Zone Configuration and run analytics for this
                camera{isZoneFilterSelected(selectedZoneId) ? " and zone" : ""} to make overlays available here.
              </div>
            ) : null}

            {hasAnalyticsQuery && !hasMatchedAnalyticsPanel ? (
              <div className="rounded-xl border border-border/70 bg-white px-4 py-3 text-sm text-muted-foreground">
                No analytics section matches {analyticsSearchQuery.trim()}. Try: person count, facial expression, queue management, person reid, entry exit, zone analysis.
              </div>
            ) : null}

            {showPersonCountPanel ? (
              <PanelCard title="Zone Analytics" subtitle="Person Count">
                <ModeAnalyticsPanel
                  timeline={selectedTimeline}
                  zones={zonesByCamera[selected.camera.id] ?? []}
                  liveZones={selected?.realtime?.zones ?? []}
                  footfall={selectedFootfall}
                  selectedZoneId={deferredSelectedZoneId}
                  isPersonCountMode={deferredOverlayMode === "person_count"}
                  sourceType={selected.type}
                />
              </PanelCard>
            ) : null}

            {showTrajectoryHeatmapPanel ? (
              <PanelCard
                title="Object Tracking-Based Heatmaps"
                subtitle="Trajectory density overlay from YOLO + BoxMOT tracks"
              >
                <div className="rounded-xl border border-border/70 bg-white/80 px-4 py-3 text-sm text-slate-700">
                  Trajectory heatmap overlay is active on the video feed.
                  {isZoneFilterSelected(deferredSelectedZoneId)
                    ? " Density is computed from tracked object paths inside the selected zone."
                    : " Density is computed from tracked object paths across the full frame."}
                </div>
              </PanelCard>
            ) : null}

            {showFacialExpressionPanel ? (
              <PanelCard
                title="Facial Expression Recognition"
                subtitle={
                  isZoneFilterSelected(deferredSelectedZoneId)
                    ? "Emotion recognition for faces detected inside the selected zone"
                    : "Emotion recognition for faces detected inside configured zones"
                }
              >
                <FacialExpressionPanel
                  cameraId={selected.camera.id}
                  selectedZoneId={deferredSelectedZoneId}
                  start={getStartTimestamp()}
                />
              </PanelCard>
            ) : null}

            {showFaceRecognitionPanel ? (
              <PanelCard title="Face Recognition" subtitle="Known-face overlays are drawn directly on the live feed">
                <div className="rounded-xl border border-border/70 bg-white/80 px-4 py-3 text-sm text-slate-700">
                  Face recognition now renders known-face matches on the primary video feed. Add or manage references
                  in <a className="font-semibold text-primary underline-offset-4 hover:underline" href="/dashboard/faces">Face Management</a>.
                </div>
              </PanelCard>
            ) : null}

            {showZoneAnalysisPanel ? (
              <PanelCard
                title="Zone Analysis"
                subtitle="Monitor congestion, people/staff paths, heatmaps, and service presence"
              >
                <ZoneAnalysisPanel
                  cameraId={selected.camera.id}
                  selectedZoneId={deferredSelectedZoneId}
                  start={getStartTimestamp()}
                />
              </PanelCard>
            ) : null}

            {showEntryExitPanel ? (
              <PanelCard
                title="Entry / Exit Analytics"
                subtitle="YOLO + ByteTrack zone transitions"
              >
                <EntryExitAnalyticsPanel
                  footfall={selectedFootfall}
                  liveZones={selected?.realtime?.zones ?? []}
                  searchQuery={entryExitZoneSearchQuery}
                  selectedZoneId={deferredSelectedZoneId}
                />
              </PanelCard>
            ) : null}

            {showPersonReidPanel ? (
              <PanelCard title="Person ReID Analytics" subtitle="Track occurrences and dwell times across views">
                <PersonReidAnalyticsPanel cameraId={selected.camera.id} selectedZoneId={deferredSelectedZoneId} />
              </PanelCard>
            ) : null}

            {showQueuePanel ? (
              <PanelCard title="Queue Management" subtitle="Waiting times & line length">
                <QueueAnalyticsPanel
                  cameraId={selected.camera.id}
                  zones={zonesByCamera[selected.camera.id] ?? []}
                  selectedZoneId={deferredSelectedZoneId}
                />
              </PanelCard>
            ) : null}
            {showQueueOverlayHint ? (
              <div className="rounded-xl border border-border/70 bg-white px-4 py-3 text-xs text-muted-foreground">
                Queue Management analytics appears when overlay mode is set to Queue Management.
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {showZoneEditor && selected ? (
        <ZoneQuickEditor
          cameraId={selected.camera.id}
          cameraName={selected.camera.name}
          frameUrl={`/api/backend/api/monitoring/cameras/${selected.camera.id}/${selected.type === "Video File" ? "first-frame" : "source-frame"}`}
          onClose={() => setShowZoneEditor(false)}
          onZonesUpdated={() => void loadZones(selected.camera.id)}
        />
      ) : null}
    </div>
  );
}

function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function formatFrameTimestamp(value: string | null | undefined): string {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString();
}

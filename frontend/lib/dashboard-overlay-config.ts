export type DashboardOverlayMode =
  | "person_count"
  | "trajectory_heatmap"
  | "facial_expression"
  | "face_recognition"
  | "queue"
  | "entry_exit_count"
  | "person_reid";

export type DashboardOverlayConfig = Record<string, Record<string, DashboardOverlayMode[]>>;

export const DASHBOARD_OVERLAY_CONFIG_KEY = "dashboard_overlay_modes";

const DASHBOARD_OVERLAY_MODE_ORDER: DashboardOverlayMode[] = [
  "person_count",
  "trajectory_heatmap",
  "facial_expression",
  "face_recognition",
  "queue",
  "entry_exit_count",
  "person_reid",
];

function isDashboardOverlayMode(value: string): value is DashboardOverlayMode {
  return DASHBOARD_OVERLAY_MODE_ORDER.includes(value as DashboardOverlayMode);
}

function normalizeModeList(modes: unknown): DashboardOverlayMode[] {
  if (!Array.isArray(modes)) return [];

  const seen = new Set<DashboardOverlayMode>();
  const normalized: DashboardOverlayMode[] = [];
  for (const mode of modes) {
    const text = String(mode).trim();
    if (!isDashboardOverlayMode(text) || seen.has(text)) continue;
    seen.add(text);
    normalized.push(text);
  }

  return normalized.sort(
    (left, right) => DASHBOARD_OVERLAY_MODE_ORDER.indexOf(left) - DASHBOARD_OVERLAY_MODE_ORDER.indexOf(right),
  );
}

export function normalizeDashboardOverlayConfig(value: unknown): DashboardOverlayConfig {
  if (!value || typeof value !== "object") return {};

  const normalized: DashboardOverlayConfig = {};
  for (const [cameraId, zonePayload] of Object.entries(value as Record<string, unknown>)) {
    if (!zonePayload || typeof zonePayload !== "object") continue;

    const zoneMap: Record<string, DashboardOverlayMode[]> = {};
    for (const [zoneId, modes] of Object.entries(zonePayload as Record<string, unknown>)) {
      const normalizedModes = normalizeModeList(modes);
      if (normalizedModes.length === 0) continue;
      zoneMap[zoneId] = normalizedModes;
    }

    if (Object.keys(zoneMap).length > 0) {
      normalized[cameraId] = zoneMap;
    }
  }

  return normalized;
}

export function mergeDashboardOverlayModes(
  config: DashboardOverlayConfig,
  cameraId: number,
  zoneId: number,
  modes: DashboardOverlayMode[],
): DashboardOverlayConfig {
  const cameraKey = String(cameraId);
  const zoneKey = String(zoneId);
  const next = normalizeDashboardOverlayConfig(config);
  const existingModes = next[cameraKey]?.[zoneKey] ?? [];
  const mergedModes = normalizeModeList([...existingModes, ...modes]);

  return {
    ...next,
    [cameraKey]: {
      ...(next[cameraKey] ?? {}),
      [zoneKey]: mergedModes,
    },
  };
}

export function setDashboardOverlayModes(
  config: DashboardOverlayConfig,
  cameraId: number,
  zoneId: number,
  modes: DashboardOverlayMode[],
): DashboardOverlayConfig {
  const normalizedModes = normalizeModeList(modes);
  if (normalizedModes.length === 0) {
    return removeDashboardOverlayZone(config, cameraId, zoneId);
  }

  const cameraKey = String(cameraId);
  const zoneKey = String(zoneId);
  const next = normalizeDashboardOverlayConfig(config);

  return {
    ...next,
    [cameraKey]: {
      ...(next[cameraKey] ?? {}),
      [zoneKey]: normalizedModes,
    },
  };
}

export function removeDashboardOverlayZone(
  config: DashboardOverlayConfig,
  cameraId: number,
  zoneId: number,
): DashboardOverlayConfig {
  const cameraKey = String(cameraId);
  const zoneKey = String(zoneId);
  const next = normalizeDashboardOverlayConfig(config);
  if (!next[cameraKey]?.[zoneKey]) {
    return next;
  }

  const nextCameraZones = { ...(next[cameraKey] ?? {}) };
  delete nextCameraZones[zoneKey];

  if (Object.keys(nextCameraZones).length === 0) {
    const remaining = { ...next };
    delete remaining[cameraKey];
    return remaining;
  }

  return {
    ...next,
    [cameraKey]: nextCameraZones,
  };
}

export function getDashboardOverlayModes(
  config: DashboardOverlayConfig,
  cameraId: number,
  zoneId: number | null,
): DashboardOverlayMode[] {
  const cameraKey = String(cameraId);
  const zones = config[cameraKey] ?? {};

  if (zoneId !== null) {
    return zones[String(zoneId)] ?? [];
  }

  const merged = new Set<DashboardOverlayMode>();
  for (const modes of Object.values(zones)) {
    for (const mode of modes) {
      merged.add(mode);
    }
  }

  return DASHBOARD_OVERLAY_MODE_ORDER.filter((mode) => merged.has(mode));
}

export function readDashboardOverlayConfigFromStorage(): DashboardOverlayConfig {
  if (typeof window === "undefined") return {};

  try {
    const raw = window.localStorage.getItem(DASHBOARD_OVERLAY_CONFIG_KEY);
    if (!raw) return {};
    return normalizeDashboardOverlayConfig(JSON.parse(raw));
  } catch {
    return {};
  }
}

export function writeDashboardOverlayConfigToStorage(config: DashboardOverlayConfig): void {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(DASHBOARD_OVERLAY_CONFIG_KEY, JSON.stringify(normalizeDashboardOverlayConfig(config)));
  } catch {
    // Ignore storage failures and keep runtime-config as the primary source of truth.
  }
}

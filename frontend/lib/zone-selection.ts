export function isZoneFilterSelected(selectedZoneId?: string | null): boolean {
  return Boolean(selectedZoneId && selectedZoneId !== "all");
}

export function parseSelectedZoneId(selectedZoneId?: string | null): number | null {
  if (!isZoneFilterSelected(selectedZoneId)) {
    return null;
  }

  const parsed = Number(selectedZoneId);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }

  return parsed;
}

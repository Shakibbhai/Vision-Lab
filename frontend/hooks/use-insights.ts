"use client";

import { useCallback, useEffect, useState } from "react";
import { type InsightsOverview, getInsightsOverview } from "@/lib/api";

const REFRESH_MS = 20000;

/** Aggregated analytics, refreshed every 20 s while the page is open. */
export function useInsights() {
  const [data, setData] = useState<InsightsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getInsightsOverview());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  return { data, error, reload: load };
}

export function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

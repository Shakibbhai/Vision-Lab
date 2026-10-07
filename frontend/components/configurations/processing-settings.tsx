"use client";

import { useEffect, useState } from "react";
import { Loader2, Save, Activity } from "lucide-react";

import { PanelCard } from "@/components/v2/ui";
import { getRuntimeConfig, updateRuntimeConfig } from "@/lib/api";

export function ProcessingSettings() {
  const [ingestionFps, setIngestionFps] = useState<number>(25);
  const [processingFps, setProcessingFps] = useState<number>(5);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    async function loadConfig() {
      try {
        const response = await getRuntimeConfig();
        const interval = (response.config.frame_interval_seconds as number) || 0.04;
        const pFps = (response.config.analytics_processing_fps as number) || 5.0;
        
        setIngestionFps(Math.round(1 / interval));
        setProcessingFps(pFps);
      } catch (err) {
        console.error("Failed to load config:", err);
        setError("Failed to load processing settings");
      } finally {
        setLoading(false);
      }
    }
    void loadConfig();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await updateRuntimeConfig("analytics_processing_fps", processingFps);
      
      setNotice("Settings saved successfully. AI analysis rate has been updated.");
      setTimeout(() => setNotice(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const skipRatio = Math.max(1, ingestionFps / processingFps);

  if (loading) {
    return (
      <PanelCard title="Capture & Analysis Rates" subtitle="Global frame rate and ingestion parameters">
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </PanelCard>
    );
  }

  return (
    <PanelCard title="AI Analysis Rate" subtitle="Configure AI processing throughput independently of video capture">
      <div className="grid gap-6">
        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}
        {notice && (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700">
            {notice}
          </div>
        )}

        <div className="grid gap-6">
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              AI PROCESSING RATE (CPU/GPU)
            </div>
            <div className="flex gap-2">
              <input
                type="number"
                min={0.1}
                step={0.1}
                max={ingestionFps}
                value={processingFps}
                onChange={(e) => setProcessingFps(Number(e.target.value) || 0.1)}
                className="h-10 w-full max-w-[200px] rounded-lg border border-input bg-white px-3 text-sm text-foreground outline-none focus:border-primary"
              />
              <span className="flex items-center text-xs font-medium text-muted-foreground">FPS</span>
            </div>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              How many frames per second the AI analyzes. {skipRatio > 1 ? `Currently analyzing 1 of every ${skipRatio.toFixed(1)} frames based on ${ingestionFps} FPS ingestion.` : "Currently analyzing every captured frame."}
            </p>
          </div>
        </div>

        <div className="flex justify-end pt-2">
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Apply Changes
          </button>
        </div>
      </div>
    </PanelCard>
  );
}

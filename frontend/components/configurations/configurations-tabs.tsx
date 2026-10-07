"use client";

import { useSearchParams } from "next/navigation";

import { NewSourceWizard } from "@/components/configurations/new-source-wizard";
import { ZonesPageContent } from "@/components/configurations/zones-page-content";
import { ProcessingSettings } from "@/components/configurations/processing-settings";



type ConfigTab = "source" | "zones";



function resolvedTab(value: string | null): ConfigTab {
  if (value === "zones") return "zones";
  return "source";
}


export function ConfigurationsTabs() {
  const searchParams = useSearchParams();

  const activeTab = resolvedTab(searchParams.get("tab"));
  const rawCameraId = Number(searchParams.get("camera_id"));
  const cameraId = Number.isFinite(rawCameraId) && rawCameraId > 0 ? rawCameraId : null;

  return (
    <div className="space-y-3">
      {activeTab === "source" ? (
        <>
          <NewSourceWizard />
          <ProcessingSettings />
        </>
      ) : (
        <ZonesPageContent preferredCameraId={cameraId} />
      )}
    </div>
  );
}

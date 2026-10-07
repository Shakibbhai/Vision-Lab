import { Suspense } from "react";

import { ConfigurationsTabs } from "@/components/configurations/configurations-tabs";

export default function ConfigurationsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-3">
          <section className="dashboard-panel rounded-2xl p-4 text-sm text-muted-foreground">
            Loading configurations...
          </section>
        </div>
      }
    >
      <ConfigurationsTabs />
    </Suspense>
  );
}

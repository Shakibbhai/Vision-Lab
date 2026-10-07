"use client";

import { useCallback } from "react";

interface ZoneConfigDialogProps {
  onChoice: (configureZones: boolean) => void;
}

export function ZoneConfigDialog({ onChoice }: ZoneConfigDialogProps) {
  const handleChoice = useCallback((configureZones: boolean) => {
    onChoice(configureZones);
  }, [onChoice]);

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 animate-in fade-in duration-200">
      <div className="bg-background rounded-xl p-6 max-w-md w-full mx-4 shadow-xl border border-border">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-lg font-semibold text-foreground">Zone Configuration</h3>
          <button
            onClick={() => handleChoice(false)}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        
        <p className="text-muted-foreground mb-6 leading-relaxed text-sm">
          Do you want to configure specific zones for this camera?
          <br />
          <span className="text-xs text-muted-foreground/80 mt-2 block">
            • <strong className="text-foreground">Yes:</strong> Draw custom zones for specific areas
            <br />
            • <strong className="text-foreground">No:</strong> Use entire frame for all analytics
          </span>
        </p>
        
        <div className="flex gap-3 justify-end mt-6">
          <button
            onClick={() => handleChoice(false)}
            className="rounded-lg border border-border bg-white px-4 py-2 text-sm font-semibold text-foreground transition hover:bg-accent focus-visible:outline-none focus:ring-2 focus:ring-ring"
          >
            No - Use Full Frame
          </button>
          <button
            onClick={() => handleChoice(true)}
            className="rounded-lg border border-primary bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90 focus-visible:outline-none focus:ring-2 focus:ring-ring"
          >
            Yes - Configure Zones
          </button>
        </div>
      </div>
    </div>
  );
}

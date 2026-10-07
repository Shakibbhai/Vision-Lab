"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X, Trash2 } from "lucide-react";

import { type DrawMode, type PolygonCanvasHandle, type Pt, type Shape, PolygonCanvas } from "@/components/polygon-canvas";
import { type Zone, createZone, deleteZone, listZones } from "@/lib/api";

const ZONE_COLORS = [
  "#14b8a6", // teal-500
  "#3b82f6", // blue-500
  "#f59e0b", // amber-500
  "#ec4899", // pink-500
  "#8b5cf6", // violet-500
  "#ef4444", // red-500
];

function toNormalizedCoordinate(val: number, max: number): number {
  return Number(Math.max(0, Math.min(1, val / max)).toFixed(4));
}

export function ZoneQuickEditor({
  cameraId,
  frameUrl,
  cameraName,
  onClose,
  onZonesUpdated,
}: {
  cameraId: number;
  frameUrl: string;
  cameraName: string;
  onClose: () => void;
  onZonesUpdated: () => void;
}) {
  const canvasRef = useRef<PolygonCanvasHandle>(null);
  const [selectedZones, setSelectedZones] = useState<Zone[]>([]);
  const [selectedZoneId, setSelectedZoneId] = useState<number | null>(null);
  
  const [drawingTool, setDrawingTool] = useState<DrawMode>("polygon");
  const [editMode, setEditMode] = useState(false);
  const [polygons, setPolygons] = useState<Shape[]>([]);
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);
  
  const [zoneName, setZoneName] = useState("Zone A");
  const [zoneCapacity, setZoneCapacity] = useState("");
  const [zoneExpectedWaitTime, setZoneExpectedWaitTime] = useState("");
  const [zoneColor, setZoneColor] = useState(ZONE_COLORS[0]);
  const [isCreatingZone, setIsCreatingZone] = useState(true);
  
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshZones = useCallback(async () => {
    try {
      const rows = await listZones(cameraId);
      setSelectedZones(rows);
    } catch {
      setError("Failed to reload zones");
    }
  }, [cameraId]);

  useEffect(() => {
    void refreshZones();
  }, [refreshZones]);

  useEffect(() => {
    if (isCreatingZone) return;
    const active = selectedZones.find((z) => z.id === selectedZoneId);
    if (!active) return;
    setZoneName(active.name);
    setZoneCapacity(active.capacity ? active.capacity.toString() : "");
    setZoneExpectedWaitTime(active.expected_wait_time_sec ? active.expected_wait_time_sec.toString() : "");
    const polyRecord = active.polygon as Record<string, unknown> | undefined;
    const color = polyRecord?.color;
    setZoneColor(typeof color === "string" ? color : ZONE_COLORS[0]);
  }, [isCreatingZone, selectedZoneId, selectedZones]);

  const savedCanvasShapes = useMemo(() => {
    const list: Shape[] = [];
    for (const z of selectedZones) {
      if (!isCreatingZone && z.id === selectedZoneId) continue;
      
      const rawPolygons = z.polygon?.polygons;
      
      if (Array.isArray(rawPolygons)) {
        for (const poly of rawPolygons) {
           if (Array.isArray(poly) && poly.length >= 3) {
             const pts = poly.map((pt) => {
               let x = Number((pt as {x?: number}).x);
               let y = Number((pt as {y?: number}).y);
               if (x <= 2 && y <= 2 && imageSize) {
                 x = x * imageSize.width;
                 y = y * imageSize.height;
               }
               return [x, y] as Pt;
             });
             list.push({ mode: "polygon", points: pts });
           }
        }
      } else if (Array.isArray(z.polygon?.points) && z.polygon.points.length >= 3) {
        const pts = z.polygon.points.map((pt) => {
          let x = Number((pt as {x?: number}).x);
          let y = Number((pt as {y?: number}).y);
          if (x <= 2 && y <= 2 && imageSize) {
            x = x * imageSize.width;
            y = y * imageSize.height;
          }
          return [x, y] as Pt;
        });
        list.push({ mode: "polygon", points: pts });
      }

      if (Array.isArray(z.polygon?.entry_line?.points) && z.polygon.entry_line.points.length === 2) {
        const pts = z.polygon.entry_line.points.map((pt) => {
          let x = Number((pt as {x?: number}).x);
          let y = Number((pt as {y?: number}).y);
          if (x <= 2 && y <= 2 && imageSize) {
            x = x * imageSize.width;
            y = y * imageSize.height;
          }
          return [x, y] as Pt;
        });
        list.push({ mode: "entry_line", points: pts });
      }
      
      if (Array.isArray(z.polygon?.exit_line?.points) && z.polygon.exit_line.points.length === 2) {
        const pts = z.polygon.exit_line.points.map((pt) => {
          let x = Number((pt as {x?: number}).x);
          let y = Number((pt as {y?: number}).y);
          if (x <= 2 && y <= 2 && imageSize) {
            x = x * imageSize.width;
            y = y * imageSize.height;
          }
          return [x, y] as Pt;
        });
        list.push({ mode: "exit_line", points: pts });
      }
    }
    return list;
  }, [selectedZones, isCreatingZone, selectedZoneId, imageSize]);

  useEffect(() => {
    if (!imageSize) return;
    canvasRef.current?.loadPolygons(savedCanvasShapes);
    setPolygons(savedCanvasShapes);
  }, [savedCanvasShapes, imageSize]);

  const unsavedShapes = useMemo(() => {
    return polygons.filter(poly => !savedCanvasShapes.includes(poly));
  }, [polygons, savedCanvasShapes]);

  const canSaveDraft = Boolean(imageSize && unsavedShapes.length > 0);

  const activatePolygonMode = () => { setEditMode(false); setDrawingTool("polygon"); };
  const activateRectangleMode = () => { setEditMode(false); setDrawingTool("rectangle"); };
  const activateEntryLineMode = () => { setEditMode(false); setDrawingTool("entry_line"); };
  const activateExitLineMode = () => { setEditMode(false); setDrawingTool("exit_line"); };

  async function saveZone() {
    if (!zoneName.trim()) { setError("Zone name required"); return; }
    if (!imageSize) return;
    
    setError("");
    setBusy(true);
    try {
      const maxX = Math.max(imageSize.width - 1, 1);
      const maxY = Math.max(imageSize.height - 1, 1);
      
      const draftPolygonsNodes = unsavedShapes.filter(s => s.mode === "polygon" && s.points.length >= 3);
      if (draftPolygonsNodes.length === 0 && !unsavedShapes.some(s => s.mode === "entry_line" || s.mode === "exit_line")) {
        setError("Please draw a polygon or line.");
        setBusy(false);
        return;
      }
      
      const payloadPolygons = draftPolygonsNodes.map(shape => 
        shape.points.map(([x, y]: Pt) => ({ x: toNormalizedCoordinate(x, maxX), y: toNormalizedCoordinate(y, maxY) }))
      );
      
      const draftEntry = unsavedShapes.find(s => s.mode === "entry_line");
      const draftExit = unsavedShapes.find(s => s.mode === "exit_line");
      
      const parsedCapacity = zoneCapacity.trim() ? parseInt(zoneCapacity.trim(), 10) : null;
      const parsedWaitTime = zoneExpectedWaitTime.trim() ? parseInt(zoneExpectedWaitTime.trim(), 10) : null;
      await createZone(cameraId, zoneName.trim(), {
        color: zoneColor,
        points: payloadPolygons[0] || [], 
        polygons: payloadPolygons,
        entry_line: draftEntry ? { points: draftEntry.points.map(([x, y]) => ({ x: toNormalizedCoordinate(x, maxX), y: toNormalizedCoordinate(y, maxY) })) } : undefined,
        exit_line: draftExit ? { points: draftExit.points.map(([x, y]) => ({ x: toNormalizedCoordinate(x, maxX), y: toNormalizedCoordinate(y, maxY) })) } : undefined,
      }, parsedCapacity, parsedWaitTime);

      await refreshZones();
      onZonesUpdated();
      setIsCreatingZone(true);
      setZoneName((previous) => (previous.toLowerCase().endsWith("a") ? "Zone B" : "Zone A"));
      setZoneCapacity("");
      setZoneExpectedWaitTime("");
      activatePolygonMode();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save zone");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-5xl rounded-2xl bg-white shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between border-b px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold">Quick-Edit Zones</h2>
            <p className="text-xs text-muted-foreground">Camera: {cameraName}</p>
          </div>
          <button onClick={onClose} className="rounded-full p-2 hover:bg-slate-100">
            <X className="h-5 w-5 text-slate-500" />
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          <div className="w-64 border-r bg-slate-50 p-4 space-y-4 overflow-y-auto">
            {error && <div className="text-xs text-red-600 bg-red-50 p-2 rounded">{error}</div>}
            
            <div className="space-y-3">
              <input
                type="text"
                value={zoneName}
                onChange={(e) => setZoneName(e.target.value)}
                className="w-full rounded-md border p-2 text-sm"
                placeholder="Zone Name"
              />
              <input
                type="number"
                min="1"
                value={zoneCapacity}
                onChange={(e) => setZoneCapacity(e.target.value)}
                className="w-full rounded-md border p-2 text-sm"
                placeholder="Optional capacity limit"
              />
              <input
                type="number"
                min="1"
                value={zoneExpectedWaitTime}
                onChange={(e) => setZoneExpectedWaitTime(e.target.value)}
                className="w-full rounded-md border p-2 text-sm"
                placeholder="Wait Time Target (sec)"
              />
              <div className="flex flex-wrap gap-2">
                {ZONE_COLORS.map(c => (
                  <button key={c} onClick={() => setZoneColor(c)} className={`h-6 w-6 rounded-full border-2 ${zoneColor === c ? "border-slate-800" : "border-transparent"}`} style={{ backgroundColor: c }} />
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <button onClick={() => { setIsCreatingZone(true); setSelectedZoneId(null); setPolygons(savedCanvasShapes); activatePolygonMode(); setZoneCapacity(""); setZoneExpectedWaitTime(""); setZoneName("Zone A"); }} className="w-full rounded bg-primary/10 py-2 text-sm font-semibold text-primary">
                + New Zone
              </button>
              <button 
                onClick={saveZone} 
                disabled={!canSaveDraft || busy} 
                className="w-full rounded bg-primary py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                Save
              </button>
              <button 
                onClick={() => { canvasRef.current?.loadPolygons(savedCanvasShapes); setPolygons(savedCanvasShapes); }} 
                disabled={unsavedShapes.length === 0} 
                className="w-full rounded border py-2 text-sm font-semibold disabled:opacity-50"
              >
                Undo Draw
              </button>
            </div>

            <div className="pt-4 border-t">
              <p className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Saved Zones</p>
              {selectedZones.map(z => (
                 <div key={z.id} className="mb-2 flex items-center justify-between rounded border p-2 text-xs bg-white">
                   <span>{z.name}</span>
                   <button 
                     onClick={async () => { if(confirm("Delete?")) { await deleteZone(z.id); refreshZones(); onZonesUpdated(); } }}
                     className="text-red-500 hover:text-red-700"
                   >
                     <Trash2 className="h-4 w-4" />
                   </button>
                 </div>
              ))}
            </div>
          </div>

          <div className="flex-1 bg-slate-900 p-4 flex flex-col">
            <div className="flex items-center gap-2 mb-2">
              <button onClick={activatePolygonMode} className={`px-3 py-1.5 text-xs rounded font-semibold ${drawingTool === 'polygon' && !editMode ? 'bg-primary text-white' : 'bg-slate-800 text-slate-300'}`}>Polygon</button>
              <button onClick={activateRectangleMode} className={`px-3 py-1.5 text-xs rounded font-semibold ${drawingTool === 'rectangle' && !editMode ? 'bg-primary text-white' : 'bg-slate-800 text-slate-300'}`}>Rectangle</button>
              <button onClick={activateEntryLineMode} className={`px-3 py-1.5 text-xs rounded font-semibold ${drawingTool === 'entry_line' && !editMode ? 'bg-primary text-white' : 'bg-slate-800 text-slate-300'}`}>Entry Line</button>
              <button onClick={activateExitLineMode} className={`px-3 py-1.5 text-xs rounded font-semibold ${drawingTool === 'exit_line' && !editMode ? 'bg-primary text-white' : 'bg-slate-800 text-slate-300'}`}>Exit Line</button>
              <button onClick={() => setEditMode(!editMode)} className={`px-3 py-1.5 text-xs rounded font-semibold ml-auto border ${editMode ? 'bg-white text-slate-900' : 'bg-transparent text-white border-slate-700'}`}>Edit Mode</button>
            </div>
            <div className="flex-1 relative border border-slate-700 rounded overflow-hidden">
               <PolygonCanvas
                  ref={canvasRef}
                  imageUrl={frameUrl}
                  drawMode={drawingTool}
                  editMode={editMode}
                  onImageSize={(size: unknown, h?: number) => {
                    if (typeof size === 'object' && size !== null && 'width' in size) {
                      const typedSize = size as { width: number; height: number };
                      setImageSize({ width: typedSize.width, height: typedSize.height });
                    } else if (typeof size === 'number' && typeof h === 'number') {
                      setImageSize({ width: size, height: h });
                    }
                  }}
                  onImageError={() => setError("Failed to load camera frame")}
                  onPolygonsChange={(shapes) => setPolygons(shapes)}
               />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

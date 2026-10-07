"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

/* ── colour palette (from PolygonZone) ───────────────────────────── */
const COLORS = [
  "#FF00FF", "#8622FF", "#FE0056", "#00FFCE", "#FF8000",
  "#00B7EB", "#FFFF00", "#0E7AFE", "#FFABAB", "#0000FF", "#CCCCCC",
];
const FILL_COLOR = "rgba(0,0,0,0.35)";
const CLOSE_RADIUS = 6.5;
const NODE_RADIUS = 5;
const GRAB_RADIUS = 10;
const LINE_WIDTH = 5;
const RAD_45 = Math.PI / 4;

/* ── types ───────────────────────────────────────────────────────── */
export type DrawMode = "polygon" | "entry_line" | "exit_line" | "rectangle";
export type Pt = [number, number];

export type Shape = {
  mode: DrawMode;
  points: Pt[];
};

export type ContextShape = Shape & {
  color: string;
};

export interface PolygonCanvasHandle {
  undo: () => void;
  discardCurrent: () => void;
  clearAll: () => void;
  loadPolygons: (shapes: Shape[]) => void;
  getPolygons: () => Shape[];
  saveImage: (filename?: string) => void;
  toggleFullscreen: () => Promise<void>;
}

export interface PolygonCanvasProps {
  imageUrl: string;
  initialPolygons?: Shape[];
  contextPolygons?: ContextShape[];
  onPolygonsChange?: (shapes: Shape[]) => void;
  onImageSize?: (w: number, h: number) => void;
  onImageError?: (message: string) => void;
  drawMode?: DrawMode;
  editMode?: boolean;
  className?: string;
}

/* ── helpers ──────────────────────────────────────────────────────── */
function isClockwise(verts: Pt[]): boolean {
  let s = 0;
  for (let i = 0; i < verts.length; i++) {
    const [x1, y1] = verts[i];
    const [x2, y2] = verts[(i + 1) % verts.length];
    s += (x2 - x1) * (y2 + y1);
  }
  return s > 0;
}

function ptDist(a: Pt, b: Pt): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/* ── component ───────────────────────────────────────────────────── */
export const PolygonCanvas = forwardRef<PolygonCanvasHandle, PolygonCanvasProps>(
  function PolygonCanvas(
    {
      imageUrl,
      initialPolygons,
      contextPolygons = [],
      onPolygonsChange,
      onImageSize,
      onImageError,
      drawMode: extDrawMode,
      editMode: extEditMode,
      className = "",
    },
    ref
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const offRef = useRef<HTMLCanvasElement | null>(null);
    const imgObjRef = useRef<HTMLImageElement | null>(null);

    const [imgLoaded, setImgLoaded] = useState(false);
    const [imgError, setImgError] = useState("");
    const scaleRef = useRef(1);

    /* mutable drawing state (avoid re-render per frame) */
    const masterPts = useRef<Shape[]>(
      initialPolygons ? initialPolygons.map((s) => ({ mode: s.mode, points: [...s.points] })) : []
    );
    const masterClr = useRef<string[]>([]);
    const curPts = useRef<Pt[]>([]);
    const colorIdx = useRef(0);
    const drawModeRef = useRef<DrawMode>(extDrawMode ?? "polygon");
    const editModeRef = useRef(extEditMode ?? false);
    const selPoly = useRef(-1);
    const selPtIdx = useRef(-1);
    const constrainRef = useRef(false);
    const contextPtsRef = useRef<ContextShape[]>(contextPolygons);

    useEffect(() => {
      contextPtsRef.current = contextPolygons;
      if (imgObjRef.current?.complete && offRef.current) {
        drawAll(offRef.current.getContext("2d")!);
        blit();
      }
    }, [contextPolygons]);

    useEffect(() => {
      if (extDrawMode) drawModeRef.current = extDrawMode;
    }, [extDrawMode]);
    useEffect(() => {
      editModeRef.current = extEditMode ?? false;
    }, [extEditMode]);

    const emitChange = useCallback(() => {
      onPolygonsChange?.(masterPts.current.map((s) => ({ mode: s.mode, points: [...s.points] })));
    }, [onPolygonsChange]);

    /* ── canvas drawing primitives ──────────────────────────────── */
    function blit() {
      const cvs = canvasRef.current;
      const off = offRef.current;
      if (!cvs || !off) return;
      const ctx = cvs.getContext("2d")!;
      ctx.clearRect(0, 0, cvs.width, cvs.height);
      ctx.drawImage(off, 0, 0);
    }

    function drawNode(ctx: CanvasRenderingContext2D, x: number, y: number, stroke?: string) {
      ctx.beginPath();
      ctx.arc(x, y, NODE_RADIUS, 0, Math.PI * 2);
      ctx.closePath();
      ctx.fillStyle = "white";
      ctx.fill();
      if (stroke) ctx.strokeStyle = stroke;
      ctx.stroke();
    }

    function drawAll(ctx: CanvasRenderingContext2D) {
      const img = imgObjRef.current;
      if (!img) return;
      ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
      ctx.drawImage(img, 0, 0);

      const ctxShapes = contextPtsRef.current;
      if (ctxShapes.length > 0) {
        ctx.beginPath();
        ctx.fillStyle = "rgba(255, 255, 255, 0.15)";
        for (const shape of ctxShapes) {
          if (shape.mode !== "polygon" || shape.points.length < 2) continue;
          ctx.moveTo(shape.points[0][0], shape.points[0][1]);
          for (let j = 1; j < shape.points.length; j++) ctx.lineTo(shape.points[j][0], shape.points[j][1]);
          ctx.closePath();
        }
        ctx.fill();

        ctx.lineWidth = 2;
        ctx.lineJoin = "bevel";
        ctx.setLineDash([4, 4]);
        for (const shape of ctxShapes) {
          if (shape.points.length < 2) continue;
          ctx.strokeStyle = shape.color;
          ctx.beginPath();
          ctx.moveTo(shape.points[0][0], shape.points[0][1]);
          for (let j = 1; j < shape.points.length; j++) ctx.lineTo(shape.points[j][0], shape.points[j][1]);
          if (shape.mode === "polygon") ctx.closePath();
          ctx.stroke();
        }
        ctx.setLineDash([]);
      }

      /* fill active polygons as subpaths (avoids overlapping opacity) */
      ctx.beginPath();
      ctx.fillStyle = FILL_COLOR;
      for (const shape of masterPts.current) {
        const poly = shape.points;
        if (poly.length < 2) continue;
        ctx.moveTo(poly[0][0], poly[0][1]);
        for (let j = 1; j < poly.length; j++) ctx.lineTo(poly[j][0], poly[j][1]);
        ctx.closePath();
      }
      ctx.fill();

      /* stroke + nodes */
      ctx.lineWidth = LINE_WIDTH;
      ctx.lineJoin = "bevel";
      for (let i = 0; i < masterPts.current.length; i++) {
        const poly = masterPts.current[i].points;
        ctx.strokeStyle = masterClr.current[i] ?? COLORS[0];
        ctx.beginPath();
        ctx.moveTo(poly[0][0], poly[0][1]);
        for (let j = 1; j < poly.length; j++) ctx.lineTo(poly[j][0], poly[j][1]);
        ctx.closePath();
        ctx.stroke();
        for (const [px, py] of poly) drawNode(ctx, px, py);
      }
    }

    function scaledCoords(e: React.MouseEvent): Pt {
      const cvs = canvasRef.current!;
      const rect = cvs.getBoundingClientRect();
      const sf = scaleRef.current;
      return [
        Math.round((e.clientX - rect.left) / sf),
        Math.round((e.clientY - rect.top) / sf),
      ];
    }

    function snapAngle(x: number, y: number): Pt {
      const pts = curPts.current;
      if (!constrainRef.current || pts.length === 0) return [x, y];
      const last = pts[pts.length - 1];
      const dx = x - last[0];
      const dy = y - last[1];
      const len = Math.hypot(dx, dy);
      const angle = Math.atan2(dy, dx);
      const snapped = Math.round(angle / RAD_45) * RAD_45;
      return [
        Math.round(last[0] + len * Math.cos(snapped)),
        Math.round(last[1] + len * Math.sin(snapped)),
      ];
    }

    function closePath() {
      let pts = curPts.current;
      if (pts.length < 2) return;
      if (isClockwise(pts)) pts = pts.slice().reverse();
      masterPts.current.push({ mode: drawModeRef.current, points: pts });
      masterClr.current.push(COLORS[colorIdx.current % COLORS.length]);
      colorIdx.current++;
      curPts.current = [];
      const off = offRef.current;
      if (off) {
        drawAll(off.getContext("2d")!);
        blit();
      }
      emitChange();
    }

    function findClosest(x: number, y: number) {
      let minD = Infinity;
      let pi = -1;
      let pj = -1;
      const sf = scaleRef.current;
      for (let i = 0; i < masterPts.current.length; i++) {
        for (let j = 0; j < masterPts.current[i].points.length; j++) {
          const d = ptDist([x, y], masterPts.current[i].points[j]);
          if (d < minD && d < GRAB_RADIUS / sf) {
            minD = d;
            pi = i;
            pj = j;
          }
        }
      }
      return { pi, pj };
    }

    /* ── image loading ─────────────────────────────────────────── */
    useEffect(() => {
      setImgLoaded(false);
      setImgError("");

      let cancelled = false;
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.src = imageUrl;
      img.onload = () => {
        if (cancelled) return;
        imgObjRef.current = img;
        const cvs = canvasRef.current;
        if (!cvs) return;
        cvs.width = img.width;
        cvs.height = img.height;

        const off = document.createElement("canvas");
        off.width = img.width;
        off.height = img.height;
        offRef.current = off;

        const cont = containerRef.current;
        if (cont) {
          const maxW = cont.offsetWidth;
          const ratio = Math.min(maxW / img.width, 1);
          scaleRef.current = ratio;
          cvs.style.width = `${img.width * ratio}px`;
          cvs.style.height = `${img.height * ratio}px`;
        }

        masterClr.current = masterPts.current.map((_, i) => COLORS[i % COLORS.length]);
        colorIdx.current = masterPts.current.length % COLORS.length;

        drawAll(off.getContext("2d")!);
        blit();
        setImgLoaded(true);
        setImgError("");
        onImageSize?.(img.width, img.height);
      };

      img.onerror = () => {
        if (cancelled) return;
        const message = "Unable to load frame image";
        setImgLoaded(false);
        setImgError(message);
        onImageError?.(message);
      };

      return () => {
        cancelled = true;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [imageUrl, onImageError]);

    /* ── mouse handlers ────────────────────────────────────────── */
    function handleMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
      let [x, y] = scaledCoords(e);
      [x, y] = snapAngle(x, y);

      if (editModeRef.current) {
        const { pi, pj } = findClosest(x, y);
        if (pi >= 0) {
          selPoly.current = pi;
          selPtIdx.current = pj;
        }
        return;
      }

      const pts = curPts.current;
      const closeRadius = CLOSE_RADIUS / Math.max(scaleRef.current, 1e-6);

      if (drawModeRef.current === "rectangle") {
        if (pts.length === 0) {
          pts.push([x, y]);
        }
        return;
      }

      if (pts.length > 2 && drawModeRef.current === "polygon" && ptDist([x, y], pts[0]) <= closeRadius) {
        closePath();
        return;
      }

      pts.push([x, y]);

      if ((drawModeRef.current === "entry_line" || drawModeRef.current === "exit_line") && pts.length === 2) {
        closePath();
        return;
      }

      const ctx = canvasRef.current?.getContext("2d");
      if (ctx) drawNode(ctx, x, y, COLORS[colorIdx.current % COLORS.length]);
    }

    function handleMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
      let [x, y] = scaledCoords(e);
      [x, y] = snapAngle(x, y);

      if (editModeRef.current && selPtIdx.current >= 0) {
        masterPts.current[selPoly.current].points[selPtIdx.current] = [x, y];
        const off = offRef.current;
        if (off) {
          drawAll(off.getContext("2d")!);
          blit();
        }
        emitChange();
        return;
      }

      const pts = curPts.current;
      const cvs = canvasRef.current;
      if (!cvs || pts.length === 0) return;
      const ctx = cvs.getContext("2d")!;
      const clr = COLORS[colorIdx.current % COLORS.length];

      blit();
      ctx.lineWidth = LINE_WIDTH;
      ctx.lineJoin = "bevel";
      ctx.strokeStyle = clr;

      if (drawModeRef.current === "rectangle" && pts.length === 1) {
        const [startX, startY] = pts[0];
        ctx.beginPath();
        ctx.rect(startX, startY, x - startX, y - startY);
        ctx.stroke();
        drawNode(ctx, startX, startY, clr);
        drawNode(ctx, x, startY, clr);
        drawNode(ctx, x, y, clr);
        drawNode(ctx, startX, y, clr);
        return;
      }

      for (let i = 0; i < pts.length - 1; i++) {
        ctx.beginPath();
        ctx.moveTo(pts[i][0], pts[i][1]);
        ctx.lineTo(pts[i + 1][0], pts[i + 1][1]);
        ctx.stroke();
        drawNode(ctx, pts[i][0], pts[i][1], clr);
      }

      if (
        (pts.length > 0 && drawModeRef.current === "polygon") ||
        (pts.length > 0 && pts.length < 2 && (drawModeRef.current === "entry_line" || drawModeRef.current === "exit_line"))
      ) {
        ctx.beginPath();
        ctx.moveTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
        ctx.lineTo(x, y);
        ctx.stroke();
        drawNode(ctx, pts[pts.length - 1][0], pts[pts.length - 1][1], clr);
      }
    }

    function handleMouseUp(e: React.MouseEvent<HTMLCanvasElement>) {
      if (editModeRef.current) {
        selPoly.current = -1;
        selPtIdx.current = -1;
        return;
      }

      if (drawModeRef.current === "rectangle" && curPts.current.length === 1) {
        let [x, y] = scaledCoords(e);
        [x, y] = snapAngle(x, y);
        const [startX, startY] = curPts.current[0];
        
        if (Math.abs(x - startX) > 5 && Math.abs(y - startY) > 5) {
          const rectPts: Pt[] = [
            [startX, startY],
            [x, startY],
            [x, y],
            [startX, y],
          ];
          // We save it as "polygon" so standard analytics engine logic applies natively
          masterPts.current.push({ mode: "polygon", points: rectPts });
          masterClr.current.push(COLORS[colorIdx.current % COLORS.length]);
          colorIdx.current++;
          emitChange();
        }
        
        curPts.current = [];
        const off = offRef.current;
        if (off) {
          drawAll(off.getContext("2d")!);
          blit();
        }
      }
    }

    function handleWheel(e: React.WheelEvent<HTMLCanvasElement>) {
      e.preventDefault();
      const cvs = canvasRef.current;
      const img = imgObjRef.current;
      if (!cvs || !img) return;
      const delta = Math.sign(e.deltaY);
      const sf = Math.max(0.1, Math.min(scaleRef.current - delta * 0.04, 6));
      scaleRef.current = sf;
      cvs.style.width = `${img.width * sf}px`;
      cvs.style.height = `${img.height * sf}px`;
    }

    /* ── keyboard ──────────────────────────────────────────────── */
    useEffect(() => {
      function onKey(e: KeyboardEvent) {
        if (e.key === "Shift") {
          constrainRef.current = true;
          return;
        }
        if (e.key === "Enter") {
          if (curPts.current.length > 2) closePath();
          return;
        }
        if (e.key === "Escape") {
          curPts.current = [];
          blit();
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
          e.preventDefault();
          doUndo();
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "e") {
          e.preventDefault();
          doClearAll();
        }
      }
      function onKeyUp(e: KeyboardEvent) {
        if (e.key === "Shift") constrainRef.current = false;
      }
      window.addEventListener("keydown", onKey);
      window.addEventListener("keyup", onKeyUp);
      return () => {
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("keyup", onKeyUp);
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /* ── imperative API ────────────────────────────────────────── */
    function doUndo() {
      if (curPts.current.length > 0) {
        curPts.current.pop();
        blit();
      } else if (masterPts.current.length > 0) {
        masterPts.current.pop();
        masterClr.current.pop();
        colorIdx.current = masterPts.current.length % COLORS.length;
        const off = offRef.current;
        if (off) {
          drawAll(off.getContext("2d")!);
          blit();
        }
        emitChange();
      }
    }

    function doDiscardCurrent() {
      curPts.current = [];
      blit();
    }

    function doClearAll() {
      curPts.current = [];
      masterPts.current = [];
      masterClr.current = [];
      colorIdx.current = 0;
      const off = offRef.current;
      const img = imgObjRef.current;
      if (off && img) {
        const ctx = off.getContext("2d")!;
        ctx.clearRect(0, 0, off.width, off.height);
        ctx.drawImage(img, 0, 0);
        blit();
      }
      emitChange();
    }

    function doLoadPolygons(shapes: Shape[]) {
      masterPts.current = shapes.map((s) => ({ mode: s.mode, points: [...s.points] }));
      masterClr.current = shapes.map((_, i) => COLORS[i % COLORS.length]);
      colorIdx.current = shapes.length % COLORS.length;
      curPts.current = [];
      const off = offRef.current;
      if (off) {
        drawAll(off.getContext("2d")!);
        blit();
      }
      emitChange();
    }

    function doSaveImage(filename?: string) {
      const cvs = canvasRef.current;
      if (!cvs) return;
      const link = document.createElement("a");
      link.href = cvs.toDataURL("image/png");
      link.download = filename ?? `polygon-zone-${Date.now()}.png`;
      link.click();
    }

    async function doToggleFullscreen(): Promise<void> {
      const container = containerRef.current;
      if (!container) return;
      if (document.fullscreenElement === container) {
        await document.exitFullscreen();
        return;
      }
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      }
      await container.requestFullscreen();
    }

    useImperativeHandle(
      ref,
      () => ({
        undo: doUndo,
        discardCurrent: doDiscardCurrent,
        clearAll: doClearAll,
        loadPolygons: doLoadPolygons,
        getPolygons: () => masterPts.current.map((s) => ({ mode: s.mode, points: [...s.points] })),
        saveImage: doSaveImage,
        toggleFullscreen: doToggleFullscreen,
      }),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      []
    );

    /* ── render ─────────────────────────────────────────────────── */
    return (
      <div ref={containerRef} className={`polygon-canvas-container ${className}`}>
        <canvas
          ref={canvasRef}
          className="polygon-canvas"
          style={{
            cursor: (extEditMode ?? false) ? "move" : "crosshair",
            display: imgLoaded ? "block" : "none",
          }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onWheel={handleWheel}
        />
        {!imgLoaded && !imgError && (
          <div className="flex h-[420px] items-center justify-center text-sm text-muted-foreground">
            Loading image…
          </div>
        )}
        {!imgLoaded && imgError && (
          <div className="flex h-[420px] items-center justify-center text-sm text-destructive">
            {imgError}
          </div>
        )}
      </div>
    );
  }
);

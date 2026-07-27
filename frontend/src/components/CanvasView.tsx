import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, Group, Loader, Stack, Text, Tooltip } from "@mantine/core";
import { IconCrop, IconMinus, IconPlus, IconRefresh } from "@tabler/icons-react";
import type { CanvasRegion, HuntCanvas, Puzzle, RegionShape } from "../types";

/** Tile edge in CSS pixels. Must match image_tiler.TILE_SIZE. */
const TILE_SIZE = 512;

/** Progress tint. The seven puzzle statuses collapse to four colours so the
 *  picture stays readable — the point is to see at a glance what has been
 *  claimed, not to encode every state. Regions with no puzzle are left
 *  untinted, and untinted art is half the answer to "what have we not touched". */
function tintFor(puzzle: Puzzle | undefined): string | null {
  if (!puzzle) return null;
  if (puzzle.solved) return "#2f9e44";                 // green
  switch (puzzle.status) {
    case "solved":
    case "backsolved":
      return "#2f9e44";
    case "stuck":
      return "#e03131";                                // red
    case "in progress":
    case "needs extraction":
    case "needs submission":
      return "#f08c00";                                // amber
    default:
      return "#868e96";                                // grey: unstarted
  }
}

export const TINT_LEGEND: { colour: string; label: string }[] = [
  { colour: "#2f9e44", label: "Solved" },
  { colour: "#f08c00", label: "In progress" },
  { colour: "#e03131", label: "Stuck" },
  { colour: "#868e96", label: "Unstarted" },
];

type Props = {
  canvas: HuntCanvas;
  puzzlesById: Map<number, Puzzle>;
  editable: boolean;
  onOpenRegion: (region: CanvasRegion) => void;
  onDraw: (shape: RegionShape) => void;
};

export function CanvasView({ canvas, puzzlesById, editable, onOpenRegion, onDraw }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({ w: 0, h: 0 });
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [drawing, setDrawing] = useState(false);
  const [drawRect, setDrawRect] = useState<null | { x1: number; y1: number; x2: number; y2: number }>(null);
  const drag = useRef<null | { kind: "pan" | "draw"; sx: number; sy: number; ox: number; oy: number }>(null);
  // Active pointers by id, for pinch: two fingers down means pinch, never pan/draw.
  const pointers = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinch = useRef<null | { dist: number; mx: number; my: number }>(null);

  const width = canvas.width ?? 0;
  const height = canvas.height ?? 0;
  const levels = canvas.tile_levels ?? 0;
  const tiled = canvas.tile_status === "ready" && levels > 0 && width > 0;

  // Track the viewport so the visible-tile window can be computed. A
  // ResizeObserver rather than a window listener: this panel sits inside a
  // layout that can change width without the window resizing at all.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setViewport({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    setViewport({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const fit = useCallback(() => {
    if (!width || !height || !viewport.w) return;
    setScale(Math.min(viewport.w / width, viewport.h / height));
    setOffset({ x: 0, y: 0 });
  }, [width, height, viewport.w, viewport.h]);

  // Fit once the image dimensions and the viewport are both known.
  const fitted = useRef(false);
  useEffect(() => {
    if (!fitted.current && width && viewport.w) {
      fitted.current = true;
      fit();
    }
  }, [fit, width, viewport.w]);

  const dispW = width * scale;
  const dispH = height * scale;

  /** Which pyramid level to fetch.
   *
   *  Level `levels - 1` is native resolution and each step down halves. Pick the
   *  smallest level whose own width still covers the displayed width, so tiles
   *  are downscaled rather than blown up — upscaling a lower level is what makes
   *  a tiled viewer look soft.
   */
  const level = useMemo(() => {
    if (!tiled) return 0;
    const native = levels - 1;
    for (let z = 0; z <= native; z++) {
      if (width / Math.pow(2, native - z) >= dispW) return z;
    }
    return native;
  }, [tiled, levels, width, dispW]);

  // Level `level` represents the image at this many pixels across, and its tiles
  // are TILE_SIZE of THAT, so one tile covers this much of the displayed image.
  const levelWidth = tiled ? width / Math.pow(2, levels - 1 - level) : width;
  const levelScale = dispW / levelWidth;              // displayed px per level px
  const tileDisp = TILE_SIZE * levelScale;
  const cols = Math.ceil(levelWidth / TILE_SIZE);
  const rows = Math.ceil((height / Math.pow(2, levels - 1 - level)) / TILE_SIZE);

  /** Only the tiles intersecting the viewport, plus a one-tile margin so a pan
   *  does not reveal blank space before the next fetch lands. Mounting all 940
   *  tiles of a 14400px sheet would defeat the entire point of the pyramid. */
  const visible = useMemo(() => {
    if (!tiled || !tileDisp) return [];
    const x0 = Math.max(0, Math.floor((-offset.x) / tileDisp) - 1);
    const y0 = Math.max(0, Math.floor((-offset.y) / tileDisp) - 1);
    const x1 = Math.min(cols - 1, Math.ceil((-offset.x + viewport.w) / tileDisp) + 1);
    const y1 = Math.min(rows - 1, Math.ceil((-offset.y + viewport.h) / tileDisp) + 1);
    const out: { x: number; y: number }[] = [];
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) out.push({ x, y });
    return out;
  }, [tiled, tileDisp, offset.x, offset.y, viewport.w, viewport.h, cols, rows]);

  /** Zoom about an arbitrary anchor point (in wrapper-local pixels), keeping
   *  whatever is under the anchor fixed on screen. The button controls anchor
   *  on the viewport centre; wheel and pinch anchor on the cursor/fingers. */
  const zoomAt = useCallback((factor: number, anchorX: number, anchorY: number) => {
    setScale((s) => {
      const next = Math.min(4, Math.max(0.02, s * factor));
      setOffset((o) => ({
        x: anchorX - ((anchorX - o.x) * next) / s,
        y: anchorY - ((anchorY - o.y) * next) / s,
      }));
      return next;
    });
  }, []);

  const zoomBy = (factor: number) => zoomAt(factor, viewport.w / 2, viewport.h / 2);

  // Ctrl+wheel zooms about the cursor. Trackpad pinch is reported by browsers
  // as a wheel event with ctrlKey set, so this covers both gestures with one
  // handler; a plain scroll is left alone so the page can still scroll past
  // the canvas. A raw DOM listener, not React's onWheel: React's synthetic
  // wheel listener is passive, and preventDefault silently no-ops on a
  // passive listener — ctrl+wheel would still trigger the browser's own page
  // zoom underneath.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const factor = Math.exp(-e.deltaY * 0.01);
      zoomAt(factor, e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const onPointerDown = (e: React.PointerEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    pointers.current.set(e.pointerId, { x: px, y: py });
    (e.target as Element).setPointerCapture?.(e.pointerId);
    if (pointers.current.size === 2) {
      // A second finger landing always means pinch, cancelling any
      // single-finger pan/draw the first finger had started.
      drag.current = null;
      setDrawRect(null);
      const [a, b] = [...pointers.current.values()];
      pinch.current = {
        dist: Math.hypot(a.x - b.x, a.y - b.y),
        mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2,
      };
      return;
    }
    if (pointers.current.size > 2) return;   // ignore a third finger
    if (drawing) {
      drag.current = { kind: "draw", sx: px, sy: py, ox: offset.x, oy: offset.y };
      setDrawRect({ x1: px, y1: py, x2: px, y2: py });
    } else {
      drag.current = { kind: "pan", sx: px, sy: py, ox: offset.x, oy: offset.y };
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = e.clientX - rect.left, py = e.clientY - rect.top;
    if (pointers.current.has(e.pointerId)) {
      pointers.current.set(e.pointerId, { x: px, y: py });
    }
    if (pointers.current.size === 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const prev = pinch.current;
      // Two-finger pan: translate by the midpoint's own movement first, then
      // zoom anchored at the new midpoint — matching a finger moving apart
      // (zoom) or two fingers sliding together (pan) or both at once.
      const dx = mx - prev.mx, dy = my - prev.my;
      const factor = dist / prev.dist;
      setScale((s) => {
        const next = Math.min(4, Math.max(0.02, s * factor));
        setOffset((o) => ({
          x: mx - ((mx - (o.x + dx)) * next) / s,
          y: my - ((my - (o.y + dy)) * next) / s,
        }));
        return next;
      });
      pinch.current = { dist, mx, my };
      return;
    }
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pan") {
      setOffset({ x: d.ox + (px - d.sx), y: d.oy + (py - d.sy) });
    } else {
      setDrawRect({ x1: d.sx, y1: d.sy, x2: px, y2: py });
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size >= 1) {
      // A finger lifted out of a pinch: the remaining finger's drag state is
      // stale (it never started a pan/draw of its own), so drop it rather
      // than falling through into the single-pointer end-of-drag logic below.
      drag.current = null;
      setDrawRect(null);
      return;
    }
    const d = drag.current;
    drag.current = null;
    if (d?.kind !== "draw" || !drawRect) { setDrawRect(null); return; }
    // Viewport pixels -> normalised image coordinates. The service normalises
    // corner order and rejects zero-area, so a stray click is refused there
    // rather than needing a second rule here.
    const toNorm = (px: number, py: number) => ({
      x: Math.min(1, Math.max(0, (px - offset.x) / dispW)),
      y: Math.min(1, Math.max(0, (py - offset.y) / dispH)),
    });
    const a = toNorm(drawRect.x1, drawRect.y1);
    const b = toNorm(drawRect.x2, drawRect.y2);
    setDrawRect(null);
    setDrawing(false);
    if (Math.abs(a.x - b.x) < 0.001 || Math.abs(a.y - b.y) < 0.001) return;
    onDraw({ type: "rect", points: [[a.x, a.y], [b.x, b.y]] });
  };

  if (canvas.tile_status === "pending") {
    return (
      <Stack align="center" py="xl" gap="xs">
        <Loader />
        <Text size="sm" c="dimmed">Preparing image — a large sheet takes a few seconds.</Text>
      </Stack>
    );
  }

  const claimed = canvas.regions.filter((r) => r.puzzle_id).length;

  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Group gap="xs">
          <Button size="xs" variant="default" onClick={() => zoomBy(1 / 1.4)}
                  leftSection={<IconMinus size={14} />}>Out</Button>
          <Button size="xs" variant="default" onClick={() => zoomBy(1.4)}
                  leftSection={<IconPlus size={14} />}>In</Button>
          <Button size="xs" variant="default" onClick={fit}
                  leftSection={<IconRefresh size={14} />}>Fit</Button>
          {editable && (
            <Tooltip label="Drag a box around a puzzle on the image">
              <Button size="xs" variant={drawing ? "filled" : "light"}
                      onClick={() => setDrawing((d) => !d)}
                      leftSection={<IconCrop size={14} />}>
                {drawing ? "Drawing — drag a box" : "Draw a region"}
              </Button>
            </Tooltip>
          )}
        </Group>
        <Group gap="lg">
          <Text size="sm" c="dimmed">
            {claimed}/{canvas.regions.length} regions linked
          </Text>
          <Group gap="sm">
            {TINT_LEGEND.map((l) => (
              <Group key={l.label} gap={4}>
                <Box w={10} h={10} style={{ background: l.colour, borderRadius: 2 }} />
                <Text size="xs" c="dimmed">{l.label}</Text>
              </Group>
            ))}
          </Group>
        </Group>
      </Group>

      <Box
        ref={wrapRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        style={{
          position: "relative",
          overflow: "hidden",
          height: "70vh",
          border: "1px solid var(--mantine-color-default-border)",
          borderRadius: 4,
          background: "var(--mantine-color-body)",
          cursor: drawing ? "crosshair" : "grab",
          touchAction: "none",
        }}
      >
        {/* overflow hidden because the pyramid's last column and row are padded
            out to a full 512px tile with white, so those tiles render past the
            image's real edge unless they are clipped to it. */}
        <div style={{ position: "absolute", left: offset.x, top: offset.y, width: dispW, height: dispH, overflow: "hidden" }}>
          {tiled ? (
            visible.map((t) => {
              // Snap every tile to a shared integer grid: a tile's right edge is
              // computed from the SAME expression as its neighbour's left edge,
              // so the two are equal by construction and no seam can open up at
              // any fractional zoom.
              //
              // The obvious alternative — sizing each tile ceil(tileDisp) + 1 —
              // does hide the seams, but destructively. Tiles carry no shared
              // border pixels (libvips' "google" layout writes uniform 512px
              // tiles and ignores dzsave's `overlap` entirely, confirmed by
              // building a pyramid both ways and diffing the bytes), so the
              // extra pixel is not a real neighbouring pixel: it stretches the
              // whole tile raster, and because browsers paint later siblings on
              // top, the bottom/right neighbour's genuine edge content is
              // overwritten by the earlier tile's interpolated overflow — at
              // every one of hundreds of boundaries, worst on the hairline
              // rules and small print a puzzle sheet is made of.
              const left = Math.round(t.x * tileDisp);
              const top = Math.round(t.y * tileDisp);
              return (
                <img
                  key={`${level}/${t.y}/${t.x}`}
                  // z/y/x — row before column, libvips' "google" layout. Swapping
                  // y and x transposes the whole image about its diagonal and
                  // looks almost plausible, which is why the server-side helper
                  // pins the order in a test with y != x.
                  src={`/api/media/${canvas.media_id}/tiles/${level}/${t.y}/${t.x}`}
                  alt=""
                  draggable={false}
                  style={{
                    position: "absolute",
                    left,
                    top,
                    width: Math.round((t.x + 1) * tileDisp) - left,
                    height: Math.round((t.y + 1) * tileDisp) - top,
                  }}
                />
              );
            })
          ) : (
            // tile_status none or failed: the original image is still stored and
            // still served, so fall back to it rather than showing nothing.
            <img
              src={`/api/media/${canvas.media_id}`}
              alt={canvas.name}
              draggable={false}
              style={{ width: "100%", height: "100%" }}
            />
          )}

          {/* Normalised coordinates go straight in — this viewBox is the whole
              payoff of storing regions as 0-1 rather than as pixels. */}
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
          >
            {canvas.regions.map((r) => {
              const [[x1, y1], [x2, y2]] = r.shape.points;
              const tint = tintFor(r.puzzle_id ? puzzlesById.get(r.puzzle_id) : undefined);
              return (
                <rect
                  key={r.id}
                  x={x1} y={y1} width={x2 - x1} height={y2 - y1}
                  fill={tint ?? "transparent"}
                  fillOpacity={tint ? 0.28 : 0}
                  stroke={tint ?? "#adb5bd"}
                  strokeDasharray={r.puzzle_id ? undefined : "0.004"}
                  // Stroke is in viewBox units, so it must be divided by the
                  // display size or it scales with the zoom into a solid slab.
                  strokeWidth={2 / Math.max(dispW, 1)}
                  vectorEffect="non-scaling-stroke"
                  style={{ cursor: "pointer", pointerEvents: "all" }}
                  onPointerDown={(e) => {
                    if (drawing) return;             // drawing over a region wins
                    e.stopPropagation();             // don't start a pan
                    onOpenRegion(r);
                  }}
                />
              );
            })}
          </svg>
        </div>

        {drawRect && (
          <div
            style={{
              position: "absolute",
              left: Math.min(drawRect.x1, drawRect.x2),
              top: Math.min(drawRect.y1, drawRect.y2),
              width: Math.abs(drawRect.x2 - drawRect.x1),
              height: Math.abs(drawRect.y2 - drawRect.y1),
              border: "2px dashed var(--mantine-color-blue-6)",
              background: "rgba(34,139,230,0.12)",
              pointerEvents: "none",
            }}
          />
        )}
      </Box>
    </Stack>
  );
}

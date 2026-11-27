/** Draws an image and its mark-up onto a 2D canvas so the result matches what
 *  MarkupCanvas shows on screen. Pure: it only talks to the context it is
 *  handed, so it is testable without a DOM (encodeSnapshot is the one
 *  exception and takes a real canvas). */
import type { MediaMark } from "../types";
import { MARKUP_MARGIN, TEXT_DEFAULT_SIZE } from "./canvasMath";

export type SnapshotCtx = Pick<
  CanvasRenderingContext2D,
  | "drawImage" | "save" | "restore" | "scale" | "translate" | "rotate"
  | "beginPath" | "moveTo" | "lineTo" | "closePath" | "stroke" | "fill" | "fillText"
  | "measureText" | "fillStyle" | "strokeStyle" | "lineWidth" | "lineCap" | "lineJoin"
  | "globalAlpha" | "font" | "textBaseline" | "textAlign"
>;

/** Longest side a snapshot may have, in pixels. */
const MAX_SIDE = 4096;
/** Largest PNG we try to upload (Discord's attachment limit headroom). */
const MAX_PNG_BYTES = 9 * 1024 * 1024;

/** The arrowhead is the editor's SVG <marker>: viewBox 0 0 10 10, refX 8,
 *  refY 5, markerWidth/Height 6, markerUnits = strokeWidth, so it is drawn
 *  at 6/10 of the stroke width per marker unit. MUST match MarkupCanvas. */
const ARROW_REF_X = 8;
const ARROW_REF_Y = 5;
const ARROW_SCALE = 6 / 10;
const ARROW_PATH: [number, number][] = [[0, 0], [10, 5], [0, 10]];
/** Furthest an arrowhead reaches from the line's end, in stroke widths. */
const ARROW_REACH = 6;

/** Rough text size used for bounds only (no context there to measure with):
 *  half an em per character, the same estimate the eraser hit-test uses. */
const TEXT_EM_PER_CHAR = 0.5;
const TEXT_LINE_HEIGHT_BOUNDS = 1.2;

export function snapshotScale(w: number, h: number): number {
  return Math.min(1, MAX_SIDE / Math.max(w, h));
}

export function wrapLines(
  ctx: Pick<CanvasRenderingContext2D, "measureText">,
  text: string,
  maxWidth: number,
): string[] {
  const out: string[] = [];
  for (const para of text.split("\n")) {
    let line = "";
    for (const word of para.split(" ")) {
      const next = line === "" ? word : `${line} ${word}`;
      if (line !== "" && ctx.measureText(next).width > maxWidth) {
        out.push(line);
        line = word;
      } else {
        line = next;
      }
    }
    out.push(line);
  }
  return out;
}

/** The image rect united with every mark's extent, in image pixels, rounded
 *  out. Strokes are padded by half their width (an arrow by its head's reach);
 *  text by an estimated box wrapped at the margin's right edge. */
export function snapshotBounds(
  marks: MediaMark[],
  imgW: number,
  imgH: number,
): { x: number; y: number; w: number; h: number } {
  let minX = 0, minY = 0, maxX = imgW, maxY = imgH;
  const grow = (x0: number, y0: number, x1: number, y1: number) => {
    minX = Math.min(minX, x0); minY = Math.min(minY, y0);
    maxX = Math.max(maxX, x1); maxY = Math.max(maxY, y1);
  };
  for (const m of marks) {
    if (m.kind === "text") {
      const [x, y] = m.shape.points[0];
      const fs = (m.shape.size ?? TEXT_DEFAULT_SIZE) * imgH;
      const lines = (m.text ?? "").split("\n");
      const widest = Math.max(...lines.map((l) => l.length), 1) * fs * TEXT_EM_PER_CHAR;
      const w = Math.min(widest, Math.max(0, (1 + MARKUP_MARGIN - x) * imgW));
      grow(x * imgW, y * imgH, x * imgW + w, y * imgH + fs * TEXT_LINE_HEIGHT_BOUNDS * lines.length);
    } else {
      const pad = (m.style.arrow ? ARROW_REACH : 0.5) * m.style.width;
      for (const [x, y] of m.shape.points) {
        grow((x - pad) * imgW, (y - pad) * imgH, (x + pad) * imgW, (y + pad) * imgH);
      }
    }
  }
  // Snap float noise (-105.00000000000001) before rounding out.
  const snap = (v: number) => Math.round(v * 1e6) / 1e6;
  const x = Math.floor(snap(minX)), y = Math.floor(snap(minY));
  return { x, y, w: Math.ceil(snap(maxX)) - x, h: Math.ceil(snap(maxY)) - y };
}

function drawStroke(ctx: SnapshotCtx, m: MediaMark): void {
  const pts = m.shape.points;
  if (pts.length < 2) return; // the editor's polyline draws nothing for one point
  const { colour, width, opacity, arrow } = m.style;
  ctx.save();
  ctx.globalAlpha = opacity;
  ctx.strokeStyle = colour;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.stroke();
  if (arrow) {
    // Direction of the last non-degenerate segment, in normalised space (the
    // SVG orients the marker before its non-uniform scale).
    const [ex, ey] = pts[pts.length - 1];
    let angle = 0;
    for (let i = pts.length - 2; i >= 0; i--) {
      const dx = ex - pts[i][0], dy = ey - pts[i][1];
      if (dx !== 0 || dy !== 0) { angle = Math.atan2(dy, dx); break; }
    }
    ctx.globalAlpha = 1; // marker content is not affected by strokeOpacity
    ctx.fillStyle = colour;
    ctx.translate(ex, ey);
    ctx.rotate(angle);
    ctx.scale(width * ARROW_SCALE, width * ARROW_SCALE);
    ctx.translate(-ARROW_REF_X, -ARROW_REF_Y);
    ctx.beginPath();
    ctx.moveTo(ARROW_PATH[0][0], ARROW_PATH[0][1]);
    ctx.lineTo(ARROW_PATH[1][0], ARROW_PATH[1][1]);
    ctx.lineTo(ARROW_PATH[2][0], ARROW_PATH[2][1]);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

function drawText(
  ctx: SnapshotCtx, m: MediaMark, imgW: number, imgH: number,
  opts: { rightEdge: number; fontFamily: string; lineHeight: number },
): void {
  const [x, y] = m.shape.points[0];
  const fs = (m.shape.size ?? TEXT_DEFAULT_SIZE) * imgH;
  ctx.save();
  ctx.globalAlpha = m.style.opacity;
  ctx.fillStyle = m.style.colour;
  ctx.font = `${fs}px ${opts.fontFamily}`;
  ctx.textBaseline = "middle"; // the CSS line box centres the glyphs
  ctx.textAlign = "left";
  const lines = wrapLines(ctx, m.text ?? "", (opts.rightEdge - x) * imgW);
  const lh = fs * opts.lineHeight;
  lines.forEach((line, i) => ctx.fillText(line, x * imgW, y * imgH + lh * (i + 0.5)));
  ctx.restore();
}

/** Draw `img` at (0,0,imgW,imgH) then every mark in stored order. Strokes are
 *  drawn in normalised space under scale(imgW, imgH), exactly as the editor's
 *  viewBox="0 0 1 1" preserveAspectRatio="none" SVG scales them, so stroke
 *  thickness is non-uniform on a non-square image. `rightEdge` is the right
 *  edge of the box text wraps against, in image-normalised units. */
export function drawMarkup(
  ctx: SnapshotCtx,
  img: CanvasImageSource,
  marks: MediaMark[],
  imgW: number,
  imgH: number,
  opts: { rightEdge: number; fontFamily: string; lineHeight: number },
): void {
  ctx.drawImage(img, 0, 0, imgW, imgH);
  for (const m of marks) {
    if (m.kind === "text") {
      drawText(ctx, m, imgW, imgH, opts);
    } else {
      ctx.save();
      ctx.scale(imgW, imgH);
      drawStroke(ctx, m);
      ctx.restore();
    }
  }
}

function toBlob(canvas: HTMLCanvasElement, type: string, q?: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, q));
}

/** PNG if it fits under 9 MiB, else JPEG (0.9) flattened onto white, else an error. */
export async function encodeSnapshot(
  canvas: HTMLCanvasElement,
): Promise<{ blob: Blob; ext: "png" | "jpg" } | { error: "too_large" }> {
  const png = await toBlob(canvas, "image/png");
  if (png && png.size <= MAX_PNG_BYTES) return { blob: png, ext: "png" };
  const flat = document.createElement("canvas");
  flat.width = canvas.width;
  flat.height = canvas.height;
  const fctx = flat.getContext("2d");
  if (!fctx) return { error: "too_large" };
  fctx.fillStyle = "#fff";
  fctx.fillRect(0, 0, flat.width, flat.height);
  fctx.drawImage(canvas, 0, 0);
  const jpg = await toBlob(flat, "image/jpeg", 0.9);
  if (jpg && jpg.size <= MAX_PNG_BYTES) return { blob: jpg, ext: "jpg" };
  return { error: "too_large" };
}

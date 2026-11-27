import { describe, expect, it } from "vitest";
import type { MediaMark } from "../types";
import { MARKUP_MARGIN } from "./canvasMath";
import { drawMarkup, snapshotBounds, snapshotScale, wrapLines, type SnapshotCtx } from "./markupSnapshot";

type Call = { op: string; args: unknown[]; state: Record<string, unknown> };

/** Records every call with a snapshot of the interesting state at that moment.
 *  measureText is 10px per character. */
function fakeCtx() {
  const calls: Call[] = [];
  const stack: Record<string, unknown>[] = [];
  const st: Record<string, unknown> = { globalAlpha: 1 };
  const rec = (op: string) => (...args: unknown[]) => {
    calls.push({ op, args, state: { ...st } });
  };
  const ctx = {
    set fillStyle(v: string) { st.fillStyle = v; },
    set strokeStyle(v: string) { st.strokeStyle = v; },
    set lineWidth(v: number) { st.lineWidth = v; },
    set lineCap(v: string) { st.lineCap = v; },
    set lineJoin(v: string) { st.lineJoin = v; },
    set globalAlpha(v: number) { st.globalAlpha = v; },
    set font(v: string) { st.font = v; },
    set textBaseline(v: string) { st.textBaseline = v; },
    set textAlign(v: string) { st.textAlign = v; },
    save: () => { stack.push({ ...st }); rec("save")(); },
    restore: () => { Object.assign(st, stack.pop()); rec("restore")(); },
    drawImage: rec("drawImage"), beginPath: rec("beginPath"), moveTo: rec("moveTo"),
    lineTo: rec("lineTo"), closePath: rec("closePath"), stroke: rec("stroke"),
    fill: rec("fill"), fillText: rec("fillText"), scale: rec("scale"),
    translate: rec("translate"), rotate: rec("rotate"),
    measureText: (t: string) => ({ width: t.length * 10 }) as TextMetrics,
  };
  return { ctx: ctx as unknown as SnapshotCtx, calls };
}

const mark = (over: Partial<MediaMark>): MediaMark => ({
  id: 1, media_id: 1, kind: "pen", shape: { points: [[0.1, 0.1], [0.5, 0.5]] },
  style: { colour: "#e03131", width: 0.004, opacity: 1, arrow: false }, text: null, ...over,
});
const opts = { rightEdge: 1 + MARKUP_MARGIN, fontFamily: "Arial", lineHeight: 1.2 };

describe("snapshotScale", () => {
  it("caps the longest side at 4096", () => {
    expect(snapshotScale(8192, 100)).toBe(0.5);
    expect(snapshotScale(1000, 2000)).toBe(1);
  });
});

describe("wrapLines", () => {
  const m = { measureText: (t: string) => ({ width: t.length * 10 }) as TextMetrics };
  it("always breaks at newlines", () => {
    expect(wrapLines(m, "ab\ncd\n\nef", 1000)).toEqual(["ab", "cd", "", "ef"]);
  });
  it("wraps words at maxWidth", () => {
    expect(wrapLines(m, "aaa bbb ccc", 70)).toEqual(["aaa bbb", "ccc"]);
  });
  it("keeps an over-long word on its own line", () => {
    expect(wrapLines(m, "a verylongword b", 50)).toEqual(["a", "verylongword", "b"]);
  });
});

describe("snapshotBounds", () => {
  it("is the image when nothing leaves it", () => {
    const b = snapshotBounds(fakeCtx().ctx, [mark({})], 1000, 500, opts);
    expect(b).toEqual({ x: 0, y: 0, w: 1000, h: 500 });
  });
  it("grows left and up for a mark in the top-left margin", () => {
    const b = snapshotBounds(fakeCtx().ctx,
      [mark({ shape: { points: [[-0.2, -0.1], [0.3, 0.3]] }, style: { colour: "#000", width: 0, opacity: 1, arrow: false } })],
      1000, 500, opts);
    expect(b).toEqual({ x: -200, y: -50, w: 1200, h: 550 });
  });
  it("pads strokes by half their width and rounds outwards", () => {
    const b = snapshotBounds(fakeCtx().ctx,
      [mark({ shape: { points: [[-0.1, 0.5], [0.5, 0.5]] }, style: { colour: "#000", width: 0.01, opacity: 1, arrow: false } })],
      1000, 500, opts);
    // left = -100 - 0.005*1000 = -105
    expect(b.x).toBe(-105);
    expect(b.y).toBe(0);
    expect(b.w).toBe(1105);
    expect(b.h).toBe(500);
  });
  it("measures text as drawn: wrapped line count and measured width", () => {
    // x=1.0, y=1.1, size .04 -> fs 20, line step 24. Available 250px = 25 chars.
    // 6 words of 10 chars wrap two per line (21 chars) -> 3 lines, widest 210px.
    const words = Array.from({ length: 6 }, () => "x".repeat(10)).join(" ");
    const b = snapshotBounds(fakeCtx().ctx,
      [mark({ kind: "text", shape: { points: [[1.0, 1.1]], size: 0.04 }, text: words })],
      1000, 500, opts);
    expect(b.x).toBe(0);
    expect(b.w).toBe(1210); // 1000 + widest measured line (210)
    expect(b.y).toBe(0);
    expect(b.h).toBe(Math.ceil(550 + 3 * 24)); // all three wrapped lines
  });
  it("uses the measured width, so wide glyphs widen the box", () => {
    const { ctx } = fakeCtx();
    ctx.measureText = (t: string) => ({ width: t.length * 30 }) as TextMetrics;
    const b = snapshotBounds(ctx,
      [mark({ kind: "text", shape: { points: [[0.5, 0.1]], size: 0.04 }, text: "abcde" })],
      1000, 500, opts);
    expect(b.w).toBe(1000 + 0); // 500 + 150 stays inside the image
    const b2 = snapshotBounds(ctx,
      [mark({ kind: "text", shape: { points: [[0.9, 0.1]], size: 0.04 }, text: "abcde" })],
      1000, 500, opts);
    expect(b2.w).toBe(1050); // 900 + 150
  });
});

describe("drawMarkup", () => {
  it("draws the image first", () => {
    const { ctx, calls } = fakeCtx();
    const img = {} as CanvasImageSource;
    drawMarkup(ctx, img, [mark({})], 1000, 500, opts);
    expect(calls[0].op).toBe("drawImage");
    expect(calls[0].args).toEqual([img, 0, 0, 1000, 500]);
  });

  it("strokes under scale(imgW, imgH) with the normalised width", () => {
    const { ctx, calls } = fakeCtx();
    drawMarkup(ctx, {} as CanvasImageSource, [mark({ style: { colour: "#1971c2", width: 0.004, opacity: 0.35, arrow: false } })], 1000, 500, opts);
    const iScale = calls.findIndex((c) => c.op === "scale");
    expect(calls[iScale].args).toEqual([1000, 500]);
    const stroke = calls.find((c) => c.op === "stroke")!;
    expect(stroke.state).toMatchObject({
      lineWidth: 0.004, strokeStyle: "#1971c2", globalAlpha: 0.35, lineCap: "round", lineJoin: "round",
    });
    const lineTos = calls.filter((c) => c.op === "moveTo" || c.op === "lineTo");
    expect(lineTos.map((c) => c.args)).toEqual([[0.1, 0.1], [0.5, 0.5]]);
    expect(calls.findIndex((c) => c.op === "stroke")).toBeGreaterThan(iScale);
  });

  it("draws an arrowhead at the last point, along the last segment, as the SVG marker does", () => {
    const { ctx, calls } = fakeCtx();
    drawMarkup(ctx, {} as CanvasImageSource, [mark({
      kind: "line", shape: { points: [[0.2, 0.2], [0.4, 0.6], [0.4, 0.8]] },
      style: { colour: "#e03131", width: 0.01, opacity: 0.5, arrow: true },
    })], 1000, 500, opts);
    const ops = calls.map((c) => c.op);
    const iTr = ops.indexOf("translate");
    expect(calls[iTr].args).toEqual([0.4, 0.8]);
    const rot = calls.find((c) => c.op === "rotate")!;
    expect(rot.args[0]).toBeCloseTo(Math.PI / 2); // last segment points straight down
    const sc = calls.filter((c) => c.op === "scale")[1];
    expect(sc.args[0]).toBeCloseTo(0.6 * 0.01);
    expect(sc.args[1]).toBeCloseTo(0.6 * 0.01);
    const tr2 = calls.filter((c) => c.op === "translate")[1];
    expect(tr2.args).toEqual([-8, -5]);
    const fill = calls.find((c) => c.op === "fill")!;
    expect(fill.state).toMatchObject({ fillStyle: "#e03131", globalAlpha: 1 });
    const head = calls.slice(iTr).filter((c) => c.op === "moveTo" || c.op === "lineTo").map((c) => c.args);
    expect(head).toEqual([[0, 0], [10, 5], [0, 10]]);
  });

  it("does not draw an arrowhead when arrow is false", () => {
    const { ctx, calls } = fakeCtx();
    drawMarkup(ctx, {} as CanvasImageSource, [mark({})], 1000, 500, opts);
    expect(calls.some((c) => c.op === "fill")).toBe(false);
  });

  it("draws text at size*imgH and wraps an over-long line at rightEdge", () => {
    const { ctx, calls } = fakeCtx();
    // x=1.0: available = (1.25 - 1.0) * 1000 = 250px = 25 chars at 10px/char.
    drawMarkup(ctx, {} as CanvasImageSource, [mark({
      kind: "text", shape: { points: [[1.0, 0.5]], size: 0.05 },
      style: { colour: "#2f9e44", width: 0, opacity: 0.7, arrow: false },
      text: "aaaaaaaaaaaaaaa bbbbbbbbbbbbbbb",
    })], 1000, 500, opts);
    const ft = calls.filter((c) => c.op === "fillText");
    expect(ft.map((c) => c.args[0])).toEqual(["aaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbb"]);
    expect(ft[0].state).toMatchObject({ font: "25px Arial", fillStyle: "#2f9e44", globalAlpha: 0.7 });
    expect(ft[0].args[1]).toBe(1000);
    // second line is lineHeight * fontSize (25 * 1.2 = 30) lower
    expect((ft[1].args[2] as number) - (ft[0].args[2] as number)).toBeCloseTo(30);
  });

  it("draws every stroke first, then every text, each in stored order (editor layering)", () => {
    const { ctx, calls } = fakeCtx();
    const stroke = (id: number, x: number) => mark({ id, shape: { points: [[x, 0.1], [x, 0.9]] } });
    const text = (id: number, t: string) =>
      mark({ id, kind: "text", shape: { points: [[0.1, 0.1]] }, text: t });
    drawMarkup(ctx, {} as CanvasImageSource, [
      text(1, "first"), stroke(2, 0.2), text(3, "second"), stroke(4, 0.3),
    ], 1000, 500, opts);
    const seq = calls.filter((c) => c.op === "stroke" || c.op === "fillText").map((c) => c.args[0] ?? "S");
    expect(seq).toEqual(["S", "S", "first", "second"]);
    const xs = calls.filter((c) => c.op === "moveTo").map((c) => c.args[0]);
    expect(xs).toEqual([0.2, 0.3]);
  });
});

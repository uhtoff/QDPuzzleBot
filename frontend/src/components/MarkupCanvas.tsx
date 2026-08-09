import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Box, Button, ColorSwatch, Group, Slider, Stack, Textarea, Tooltip } from "@mantine/core";
import { useElementSize, useMergedRef } from "@mantine/hooks";
import {
  IconArrowUpRight, IconDeviceFloppy, IconEraser, IconHighlight, IconLine,
  IconPencil, IconTextSize, IconArrowBackUp,
} from "@tabler/icons-react";
import type { MarkKind, MarkStyle, MediaMark } from "../types";
import { useCreateMark, useDeleteMark, useMarkup } from "../api";
import { normPoint, polylineHitsCircle } from "../lib/canvasMath";
import { cycleIndex } from "../lib/cycleIndex";
import { useWheelZoom } from "../hooks/useZoomPan";
import { notifyError } from "../util";

type Tool = "pen" | "line" | "arrow" | "highlighter" | "text" | "eraser" | null;

const COLOURS = ["#e03131", "#1971c2", "#2f9e44", "#f08c00", "#212529"];

/** Drop points closer together than this. A drag emits hundreds of pointer
 *  events per second; without thinning, one stroke would store thousands of
 *  points and the server would refuse it outright (MAX_POINTS). */
const MIN_STEP = 0.001;

/** Round stored coordinates. Four decimal places is sub-pixel on a 10000px
 *  image and keeps the JSON blob small. */
const round4 = (n: number) => Math.round(n * 10000) / 10000;

/** The size slider's own bounds. Named so the text mapping below cannot drift
 *  out of step with the control that feeds it. */
const WIDTH_MIN = 0.001;
const WIDTH_MAX = 0.01;

/** The slider drives stroke width AND text size, because it is the one "how
 *  big" control — but they are different quantities: 0.003 is a fine line,
 *  while a font 0.3% of the image's height is unreadable. Text was previously
 *  fixed at 0.04, so the range is anchored around that: half of it at the
 *  bottom of the slider, half again as large at the top. */
const TEXT_MIN = 0.02;
const TEXT_MAX = 0.06;

/** The eraser's radius, also driven by the size slider — it is the same "how
 *  big" control. As a fraction of the image's height, like text: the eraser is
 *  a circle on screen, so its reach cannot be measured in normalised units on a
 *  non-square image. The floor is well above the thinnest pen, because erasing
 *  is aiming at a stroke rather than drawing one and a hairline target is
 *  unusable. */
const ERASER_MIN = 0.015;
const ERASER_MAX = 0.06;

/** Slider position → eraser radius as a fraction of the image's height. */
function eraserRadiusFor(width: number): number {
  const t = (width - WIDTH_MIN) / (WIDTH_MAX - WIDTH_MIN);
  return ERASER_MIN + Math.min(1, Math.max(0, t)) * (ERASER_MAX - ERASER_MIN);
}

/** Slider position → font size as a fraction of the image's height. */
function textSizeFor(width: number): number {
  const t = (width - WIDTH_MIN) / (WIDTH_MAX - WIDTH_MIN);
  return TEXT_MIN + Math.min(1, Math.max(0, t)) * (TEXT_MAX - TEXT_MIN);
}

/** True when the keystroke belongs to a text field rather than to the canvas —
 *  otherwise a digit typed into a text mark would swap tools mid-word, and Tab
 *  would recolour instead of indenting. */
function isTypingTarget(): boolean {
  const el = document.activeElement;
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
    || (el instanceof HTMLElement && el.isContentEditable);
}

function styleFor(tool: Tool, colour: string, width: number): Partial<MarkStyle> {
  // A highlighter is not a kind — it is a fat, low-opacity pen, and the server
  // stores exactly that.
  if (tool === "highlighter") return { colour, width: width * 4, opacity: 0.35 };
  return { colour, width, opacity: 1, arrow: tool === "arrow" };
}

function kindFor(tool: Tool): MarkKind {
  if (tool === "line" || tool === "arrow") return "line";
  if (tool === "text") return "text";
  return "pen";
}

/** The tool buttons, in toolbar order. One table so the number-key shortcut,
 *  the button and its tooltip cannot drift apart — the digit IS the position. */
const TOOLS: { tool: Exclude<Tool, null>; label: string; icon: ReactNode }[] = [
  { tool: "pen", label: "Pen", icon: <IconPencil size={14} /> },
  { tool: "line", label: "Line", icon: <IconLine size={14} /> },
  { tool: "arrow", label: "Arrow", icon: <IconArrowUpRight size={14} /> },
  { tool: "highlighter", label: "Highlight", icon: <IconHighlight size={14} /> },
  { tool: "text", label: "Text", icon: <IconTextSize size={14} /> },
  { tool: "eraser", label: "Erase", icon: <IconEraser size={14} /> },
];

type Props = {
  mediaId: number;
  alt: string;
  /** Called by the Save button. Marks are already stored as they are drawn;
   *  this commits any half-typed text box and hands control back to the
   *  surface that opened the canvas (the modal closes). */
  onSave?: () => void;
};

export function MarkupCanvas({ mediaId, alt, onSave }: Props) {
  const marks = useMarkup(mediaId);
  const createMark = useCreateMark(mediaId);
  const deleteMark = useDeleteMark(mediaId);

  const [tool, setTool] = useState<Tool>("pen");
  const [colour, setColour] = useState(COLOURS[0]);
  const [width_, setWidth] = useState(0.003);
  const [stroke, setStroke] = useState<[number, number][]>([]);
  /** Where an in-progress text box sits. Two coordinate systems on purpose:
   *  x/y are normalised (what gets stored), px/py are frame-relative pixels
   *  (where the editor renders). The editor lives outside the transformed div,
   *  so a percentage of the frame would not track the image once it is panned
   *  or the image is letterboxed inside the frame. */
  const [textAt, setTextAt] =
    useState<null | { x: number; y: number; px: number; py: number }>(null);
  const [draftText, setDraftText] = useState("");
  /** Ids created in this session, newest last — undo pops from here. */
  const mine = useRef<number[]>([]);
  const imgRef = useRef<HTMLImageElement>(null);
  /** The clipping viewport. Task 10 anchors zoom on it; declared here because
   *  the frame Box below already needs the ref. */
  const frameRef = useRef<HTMLDivElement>(null);
  const drawing = useRef(false);
  /** Whether the eraser is being dragged, and which marks it has already taken
   *  during this drag — the circle sweeps over a mark for many pointer events,
   *  and firing a second DELETE for an id already gone would surface a 404 as
   *  an error toast. */
  const erasing = useRef(false);
  const erased = useRef<Set<number>>(new Set());
  /** The eraser cursor's position, in frame-local pixels. Null when the pointer
   *  is outside the frame or another tool is selected. */
  const [eraserAt, setEraserAt] = useState<null | { px: number; py: number }>(null);

  const [zoom, setZoom] = useState(1);
  /** Mirrors `zoom` so zoomAt can read it without a setState updater; see the
   *  purity note there. Kept in step by every writer of setZoom. */
  const zoomRef = useRef(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const panning = useRef<null | { sx: number; sy: number; ox: number; oy: number }>(null);
  /** The in-progress text editor, so pointer events inside it can be ignored by
   *  the frame's handlers rather than starting a new mark under the cursor. */
  const editorRef = useRef<HTMLDivElement>(null);
  /** The image's rendered size in CSS pixels, before the zoom transform.
   *  Text marks are HTML, so their font size has to be a real pixel value; the
   *  transform then scales them with everything else. */
  const { ref: imgSizeRef, width: imgW, height: imgH } = useElementSize();
  const imgBox = { w: imgW, h: imgH };
  const imgRefs = useMergedRef(imgRef, imgSizeRef);
  /** The frame's own rendered height. The frame flexes to fill whatever the
   *  surface gives it, but the image sits inside a shrink-wrapped div whose
   *  height is auto — so a percentage max-height on the <img> has nothing to
   *  resolve against. Measuring the frame gives it a real pixel cap. */
  const { ref: frameSizeRef, height: frameH } = useElementSize();
  const frameRefs = useMergedRef(frameRef, frameSizeRef);

  /** Whether Ctrl is currently down, purely so the cursor advertises that a
   *  drag would pan. Window-level because the pointer may be nowhere near the
   *  frame when the key is pressed; the blur reset stops a held Ctrl sticking
   *  after an alt-tab. */
  const [ctrlHeld, setCtrlHeld] = useState(false);
  useEffect(() => {
    const sync = (e: KeyboardEvent) => setCtrlHeld(e.ctrlKey);
    const clear = () => setCtrlHeld(false);
    window.addEventListener("keydown", sync);
    window.addEventListener("keyup", sync);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("keydown", sync);
      window.removeEventListener("keyup", sync);
      window.removeEventListener("blur", clear);
    };
  }, []);

  /** Zoom about an anchor in frame-local pixels, keeping whatever is under the
   *  anchor fixed. Same arithmetic as CanvasView.zoomAt. */
  const zoomAt = useCallback((factor: number, ax: number, ay: number) => {
    // Both updaters must stay pure. Calling setPan from inside the setZoom
    // updater made that updater a side effect, and React invokes updaters twice
    // under StrictMode -- so the pan was queued twice and the second update
    // applied on top of the first, walking the image away from the anchor.
    // zoomRef carries the current zoom instead, leaving setPan a pure function
    // of the previous pan.
    const z = zoomRef.current;
    const next = Math.min(8, Math.max(1, z * factor));
    zoomRef.current = next;
    setZoom(next);
    setPan((p) => ({
      x: ax - ((ax - p.x) * next) / z,
      y: ay - ((ay - p.y) * next) / z,
    }));
  }, []);

  // Ctrl+wheel zooms about the cursor; a plain scroll still scrolls the modal.
  useWheelZoom(frameRef, zoomAt);

  // The buttons anchor on the viewport centre, not (0,0): zooming about a
  // corner walks the image away from whatever is being looked at.
  const zoomBy = (factor: number) => {
    const rect = frameRef.current?.getBoundingClientRect();
    zoomAt(factor, (rect?.width ?? 0) / 2, (rect?.height ?? 0) / 2);
  };

  /** Pointer position as normalised image coordinates. getBoundingClientRect
   *  already reflects any CSS transform, so this stays correct at every zoom. */
  const toNorm = useCallback((e: React.PointerEvent): [number, number] | null => {
    const rect = imgRef.current?.getBoundingClientRect();
    if (!rect) return null;
    return normPoint(e.clientX, e.clientY, rect);
  }, []);

  const commitStroke = useCallback(async (points: [number, number][]) => {
    if (points.length < 2) return;
    const rounded = points.map(([x, y]) => [round4(x), round4(y)]) as [number, number][];
    try {
      const mark = await createMark.mutateAsync({
        mark: { kind: kindFor(tool), points: rounded },
        style: styleFor(tool, colour, width_),
      });
      mine.current.push(mark.id);
    } catch (e) {
      notifyError(e);
    }
  }, [createMark, tool, colour, width_]);

  /** Delete every mark the eraser circle currently covers. Called on pointer
   *  down and on every move while dragging, so the eraser sweeps rather than
   *  only picking off whatever is under the exact click. */
  const eraseAt = useCallback((e: React.PointerEvent) => {
    const at = toNorm(e);
    if (!at || !imgBox.w || !imgBox.h) return;
    const r = eraserRadiusFor(width_) * imgBox.h;
    const cx = at[0] * imgBox.w;
    const cy = at[1] * imgBox.h;
    for (const m of marks.data ?? []) {
      if (erased.current.has(m.id)) continue;
      let hit: boolean;
      if (m.kind === "text") {
        // Text has no stored box — it is HTML, sized by its own glyphs. Its
        // anchor is the top-left, so approximate the run as one line tall and
        // roughly half an em per character, then test the circle against that
        // rectangle. Erring generous is the right way round: a text mark you
        // meant to keep is one Undo away.
        const fs = (m.shape.size ?? 0.04) * imgBox.h;
        const [[tx, ty]] = m.shape.points;
        const lines = (m.text ?? "").split("\n");
        const left = tx * imgBox.w;
        const top = ty * imgBox.h;
        const w = fs * 0.5 * Math.max(...lines.map((l) => l.length), 1);
        const h = fs * 1.2 * lines.length;
        const nx = Math.min(Math.max(cx, left), left + w);
        const ny = Math.min(Math.max(cy, top), top + h);
        hit = Math.hypot(cx - nx, cy - ny) <= r;
      } else {
        hit = polylineHitsCircle(m.shape.points, imgBox.w, imgBox.h, cx, cy, r);
      }
      if (!hit) continue;
      erased.current.add(m.id);
      // Erasing one of my own marks must also drop it from the undo stack.
      // Without this, Ctrl+Z later pops an id the server has already deleted:
      // the request 404s, the user gets an error toast, and the undo is spent
      // without undoing anything.
      mine.current = mine.current.filter((id) => id !== m.id);
      deleteMark.mutate(m.id, { onError: (err) => notifyError(err) });
    }
  }, [toNorm, imgBox.w, imgBox.h, width_, marks.data, deleteMark]);

  const onPointerDown = (e: React.PointerEvent) => {
    // A click inside the open text editor is the editor's own business. Without
    // this the frame would start a fresh mark under the cursor and move the
    // editor there mid-typing.
    if (editorRef.current?.contains(e.target as Node)) return;
    // Ctrl is the "move the image" modifier throughout: ctrl+wheel zooms,
    // ctrl+drag pans, whatever tool is selected. Saves a round trip to the Pan
    // button between every stroke.
    if (!tool || e.ctrlKey) {
      const rect = frameRef.current?.getBoundingClientRect();
      if (!rect) return;
      panning.current = { sx: e.clientX, sy: e.clientY, ox: pan.x, oy: pan.y };
      (e.target as Element).setPointerCapture?.(e.pointerId);
      return;
    }
    const at = toNorm(e);
    if (!at || !tool) return;
    if (tool === "eraser") {
      erasing.current = true;
      erased.current.clear();
      (e.target as Element).setPointerCapture?.(e.pointerId);
      eraseAt(e);
      return;
    }
    if (tool === "text") {
      // preventDefault is load-bearing, not tidiness. Without it the browser's
      // own mousedown default moves focus to the element under the cursor
      // AFTER React has mounted the autoFocused Textarea — the textarea blurs
      // immediately, onBlur commits an empty draft, and the editor vanishes the
      // instant it appears. That is why the text tool did nothing at all.
      e.preventDefault();
      const frame = frameRef.current?.getBoundingClientRect();
      setTextAt({
        x: at[0], y: at[1],
        px: e.clientX - (frame?.left ?? 0), py: e.clientY - (frame?.top ?? 0),
      });
      return;
    }
    drawing.current = true;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setStroke([at]);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (tool === "eraser") {
      const frame = frameRef.current?.getBoundingClientRect();
      setEraserAt({ px: e.clientX - (frame?.left ?? 0), py: e.clientY - (frame?.top ?? 0) });
      if (erasing.current) { eraseAt(e); return; }
    }
    if (panning.current) {
      const p = panning.current;
      setPan({ x: p.ox + (e.clientX - p.sx), y: p.oy + (e.clientY - p.sy) });
      return;
    }
    if (!drawing.current) return;
    const at = toNorm(e);
    if (!at) return;
    setStroke((s) => {
      if (tool === "line" || tool === "arrow") return [s[0], at];
      // Shift straightens a pen or highlighter stroke: the mark collapses to
      // start-point → cursor for as long as the key is held, and releasing it
      // resumes freehand from there. Held for the whole drag, this stores a
      // two-point "pen" mark, which the validator already accepts.
      if (e.shiftKey) return [s[0], at];
      const last = s[s.length - 1];
      // Thin as we capture, not on commit: the in-progress path is re-rendered
      // on every move, so an unthinned stroke costs frames as well as bytes.
      if (last && Math.hypot(at[0] - last[0], at[1] - last[1]) < MIN_STEP) return s;
      return [...s, at];
    });
  };

  const onPointerUp = () => {
    erasing.current = false;
    if (panning.current) { panning.current = null; return; }
    if (!drawing.current) return;
    drawing.current = false;
    const points = stroke;
    setStroke([]);
    void commitStroke(points);
  };

  // The browser reclaiming the pointer (alt-tab, a system gesture, the modal
  // losing the capture) must not leave a half-drawn stroke live: drop it
  // uncommitted rather than letting the next pointerdown extend it.
  const onPointerCancel = () => {
    panning.current = null;
    erasing.current = false;
    if (drawing.current) {
      drawing.current = false;
      setStroke([]);
    }
  };

  const commitText = async () => {
    const body = draftText.trim();
    const at = textAt;
    setTextAt(null);
    setDraftText("");
    if (!body || !at) return;
    try {
      const mark = await createMark.mutateAsync({
        mark: {
          kind: "text", points: [[round4(at.x), round4(at.y)]], text: body,
          size: round4(textSize),
        },
        style: styleFor("text", colour, width_),
      });
      mine.current.push(mark.id);
    } catch (e) {
      notifyError(e);
    }
  };

  /** Font size as a fraction of the image's height, from the size slider. */
  const textSize = textSizeFor(width_);

  const undo = useCallback(() => {
    const last = mine.current.pop();
    if (last != null) deleteMark.mutate(last, { onError: (e) => notifyError(e) });
  }, [deleteMark]);

  // Keyboard: 1-6 pick a tool (in toolbar order), Ctrl/⌘+Z undoes the last mark
  // made in this session. Both are ignored while something is being typed into
  // — otherwise a "1" in a text mark would swap the tool mid-word, and undo has
  // to stay the browser's own text undo inside the editor.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget()) return;
      const digit = Number(e.key);
      if (Number.isInteger(digit) && digit >= 1 && digit <= TOOLS.length
          && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setTool(TOOLS[digit - 1].tool);
        return;
      }
      if (e.key !== "z" && e.key !== "Z") return;
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      e.preventDefault();
      undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo]);

  /* Tab cycles the drawing colour, Shift+Tab goes back.
   *
   * Registered in the CAPTURE phase, and its own listener rather than a branch
   * of the handler above, because of what else wants Tab: this canvas lives in
   * a Mantine Modal, whose focus trap handles Tab on the way UP. A bubble-phase
   * listener would preventDefault too late to stop the trap having already
   * moved focus, so the colour would change AND a focus ring would wander the
   * toolbar. Capturing first, and stopping propagation, keeps Tab to one job.
   *
   * Taking Tab over is affordable here: the modal still closes on Escape, and
   * marks save as you draw, so Tab was never the way out. Inside a text mark
   * isTypingTarget() bows out and Tab means what it normally means. */
  useEffect(() => {
    const onTab = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget()) return;
      e.preventDefault();
      e.stopPropagation();
      // Functional update: `colour` is deliberately not a dep, so reading it
      // directly would pin the value captured when the effect last ran.
      setColour((prev) => COLOURS[
        cycleIndex(COLOURS.indexOf(prev), e.shiftKey ? -1 : 1, COLOURS.length)
      ]);
    };
    window.addEventListener("keydown", onTab, true);
    return () => window.removeEventListener("keydown", onTab, true);
  }, []);

  // No aspect ratio is computed and no dimensions are fetched: the <img> is
  // sized by max-width/max-height inside a shrink-wrapping div, so that div IS
  // the image's rectangle and the SVG overlay at inset:0 matches it exactly.
  // See Task 7 for why the server does not send dimensions.

  return (
    <Stack gap="xs" style={{ flex: 1, minHeight: 0 }}>
      <Group gap="xs">
        {TOOLS.map((t, i) => (
          <Tooltip key={t.tool} label={`${t.label} (${i + 1})`}>
            <Button size="xs" variant={tool === t.tool ? "filled" : "light"}
                    leftSection={t.icon}
                    onClick={() => setTool(t.tool)}>{t.label}</Button>
          </Tooltip>
        ))}
        <Tooltip label="Undo (Ctrl/⌘+Z)">
          <Button size="xs" variant="default" leftSection={<IconArrowBackUp size={14} />}
                  onClick={undo}>Undo</Button>
        </Tooltip>
        <Tooltip label="Pan (or hold Ctrl and drag)">
          <Button size="xs" variant={tool === null ? "filled" : "light"}
                  onClick={() => setTool(null)}>Pan</Button>
        </Tooltip>
        <Button size="xs" variant="default" onClick={() => zoomBy(1.4)}>+</Button>
        <Button size="xs" variant="default" onClick={() => zoomBy(1 / 1.4)}>−</Button>
        <Button size="xs" variant="default"
                onClick={() => { zoomRef.current = 1; setZoom(1); setPan({ x: 0, y: 0 }); }}>Fit</Button>
        {/* withShadow alone was too subtle to answer "which colour am I drawing
            in?" at a glance, which is the question the toolbar exists to answer.
            The ring is the actual indicator; the shadow just lifts it. */}
        <Tooltip label="Tab / Shift+Tab to switch colour">
          <Group gap={4}>
            {COLOURS.map((c) => (
              <ColorSwatch key={c} color={c} size={18}
                           style={{ cursor: "pointer",
                                    outline: c === colour ? "2px solid var(--mantine-color-text)" : undefined,
                                    outlineOffset: 2 }}
                           onClick={() => setColour(c)}
                           withShadow={c === colour} />
            ))}
          </Group>
        </Tooltip>
        <Slider w={90} min={WIDTH_MIN} max={WIDTH_MAX} step={0.001} value={width_}
                onChange={setWidth} label={null} />
        {onSave && (
          <Tooltip label="Marks are saved as you draw — this closes the image">
            <Button size="xs" leftSection={<IconDeviceFloppy size={14} />}
                    onClick={() => { void commitText(); onSave(); }}>
              Save
            </Button>
          </Tooltip>
        )}
      </Group>

      {/* Three nested boxes, and the nesting is load-bearing:
          - frame: fixed viewport height, clips, owns the pointer handlers;
          - fit:   shrink-wraps the image, so it IS the image's box;
          - img:   sized by max-width/max-height, NOT object-fit.

          object-fit: contain would letterbox INSIDE the element box, so
          getBoundingClientRect() would return the padded box and every stroke
          would land offset from the cursor by the letterbox. Sizing with
          max-width/max-height instead means the element box and the painted
          image are the same rectangle, which is what makes toNorm correct. */}
      <Box
        ref={frameRefs}
        style={{ position: "relative", width: "100%", flex: 1, minHeight: 200,
                 overflow: "hidden", display: "flex",
                 alignItems: "center", justifyContent: "center",
                 touchAction: "none",
                 // The eraser draws its own circular cursor below, so the
                 // system cursor gets out of the way.
                 cursor: tool === null || ctrlHeld
                   ? "grab" : tool === "eraser" ? "none" : "crosshair" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onPointerLeave={() => setEraserAt(null)}
      >
       <div style={{ position: "relative", display: "inline-block", lineHeight: 0,
                     transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                     transformOrigin: "0 0" }}>
        <img ref={imgRefs} src={`/api/media/${mediaId}`} alt={alt} draggable={false}
             style={{ display: "block", maxWidth: "100%",
                      maxHeight: frameH || "70vh",
                      width: "auto", height: "auto", userSelect: "none" }} />

        {/* Normalised coordinates go straight in — the same payoff the region
            overlay gets from viewBox="0 0 1 1". The parent shrink-wraps the
            image, so inset:0 is exactly the image's rectangle. */}
        <svg viewBox="0 0 1 1" preserveAspectRatio="none"
             style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                      pointerEvents: "none" }}>
          <defs>
            {COLOURS.map((c) => (
              <marker key={c} id={`arrow-${c.slice(1)}`} viewBox="0 0 10 10"
                      refX="8" refY="5" markerWidth="6" markerHeight="6"
                      orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
              </marker>
            ))}
          </defs>
          {(marks.data ?? []).filter((m) => m.kind !== "text").map((m) => (
            <MarkShape key={m.id} mark={m} />
          ))}
          {stroke.length > 1 && (
            <polyline points={stroke.map(([x, y]) => `${x},${y}`).join(" ")}
                      fill="none" stroke={colour}
                      strokeOpacity={tool === "highlighter" ? 0.35 : 1}
                      strokeWidth={tool === "highlighter" ? width_ * 4 : width_}
                      strokeLinecap="round" strokeLinejoin="round" />
          )}
        </svg>

        {/* Text marks are HTML, not SVG <text>. The overlay above is
            viewBox="0 0 1 1" with preserveAspectRatio="none", which scales x
            and y independently — fine for strokes, which are geometry that
            should follow the image, but it stretches glyphs by the image's
            aspect ratio. This layer sits in the same shrink-wrapped box, so it
            still lines up with the image and still scales with the zoom
            transform, while the type stays the shape it was drawn as. */}
        <div style={{ position: "absolute", inset: 0, pointerEvents: "none",
                      lineHeight: 1.2 }}>
          {(marks.data ?? []).filter((m) => m.kind === "text").map((m) => {
            const [[x, y]] = m.shape.points;
            return (
              <div
                key={m.id}
                style={{
                  position: "absolute",
                  left: `${x * 100}%`, top: `${y * 100}%`,
                  // Font size is stored normalised against the image's height,
                  // so ink scales with the picture rather than the viewport.
                  fontSize: (m.shape.size ?? 0.04) * (imgBox.h || 0),
                  color: m.style.colour, opacity: m.style.opacity,
                  whiteSpace: "pre-wrap", userSelect: "none",
                  // Erasing text goes through the frame's circle hit-test too,
                  // so this layer stays transparent to the pointer.
                  pointerEvents: "none",
                }}
              >
                {m.text}
              </div>
            );
          })}
        </div>
       </div>

        {/* The eraser cursor. It sits outside the transformed div — like the
            text editor — so its position is frame-local pixels, but its size is
            multiplied by the zoom so the ring always circles exactly the area
            eraseAt will clear. */}
        {tool === "eraser" && eraserAt && imgBox.h > 0 && (
          <div style={{
            position: "absolute",
            left: eraserAt.px, top: eraserAt.py,
            width: eraserRadiusFor(width_) * imgBox.h * 2 * zoom,
            height: eraserRadiusFor(width_) * imgBox.h * 2 * zoom,
            transform: "translate(-50%, -50%)",
            border: "1px solid rgba(0,0,0,0.65)",
            boxShadow: "0 0 0 1px rgba(255,255,255,0.65)",
            borderRadius: "50%", pointerEvents: "none", zIndex: 4,
          }} />
        )}

        {textAt && (
          <Box ref={editorRef}
               style={{ position: "absolute", left: textAt.px, top: textAt.py,
                        width: 220, zIndex: 5 }}>
            {/* The editor previews the size the mark will actually be: the
                image's rendered height times the chosen fraction, times the
                zoom (the editor sits outside the transformed div, so it does
                not inherit the scale). Floored at 11px so a small setting on a
                zoomed-out image is still typeable. */}
            <Textarea autoFocus autosize minRows={1} maxRows={4} value={draftText}
                      styles={{ input: {
                        fontSize: Math.max(11, textSize * (imgBox.h || 0) * zoom),
                        lineHeight: 1.2,
                      } }}
                      onChange={(e) => setDraftText(e.currentTarget.value)}
                      onKeyDown={(e) => {
                        // Matches EvidencePanel's multi-line notes: Enter breaks
                        // the line, mod+Enter commits, Escape cancels.
                        if (e.key === "Escape") { setTextAt(null); setDraftText(""); }
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void commitText();
                      }}
                      onBlur={() => void commitText()}
                      placeholder="Text, then Ctrl/⌘+Enter" />
          </Box>
        )}
      </Box>
    </Stack>
  );
}

function MarkShape({ mark }: { mark: MediaMark }) {
  const { colour, width, opacity, arrow } = mark.style;
  // Erasing is hit-tested geometrically by the frame (polylineHitsCircle), not
  // by the browser against the painted stroke: the eraser is an area, and a
  // hairline pen was almost impossible to click on.
  const hit = { pointerEvents: "none" as const };
  // Text marks never reach here — they render in the HTML layer, because this
  // SVG's preserveAspectRatio="none" would stretch glyphs by the image's
  // aspect ratio. The caller filters them out.
  return (
    <polyline
      points={mark.shape.points.map(([x, y]) => `${x},${y}`).join(" ")}
      fill="none" stroke={colour} strokeOpacity={opacity} strokeWidth={width}
      strokeLinecap="round" strokeLinejoin="round"
      markerEnd={arrow ? `url(#arrow-${colour.slice(1)})` : undefined}
      style={hit}
    />
  );
}

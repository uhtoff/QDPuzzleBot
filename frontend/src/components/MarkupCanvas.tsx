import { useCallback, useRef, useState } from "react";
import { Box, Button, ColorSwatch, Group, Slider, Stack, Text, Textarea } from "@mantine/core";
import {
  IconArrowUpRight, IconEraser, IconHighlight, IconLine, IconPencil,
  IconTextSize, IconArrowBackUp,
} from "@tabler/icons-react";
import type { MarkKind, MarkStyle, MediaMark } from "../types";
import { useCreateMark, useDeleteMark, useMarkup } from "../api";

type Tool = "pen" | "line" | "arrow" | "highlighter" | "text" | "eraser" | null;

const COLOURS = ["#e03131", "#1971c2", "#2f9e44", "#f08c00", "#212529"];

/** Drop points closer together than this. A drag emits hundreds of pointer
 *  events per second; without thinning, one stroke would store thousands of
 *  points and the server would refuse it outright (MAX_POINTS). */
const MIN_STEP = 0.001;

/** Round stored coordinates. Four decimal places is sub-pixel on a 10000px
 *  image and keeps the JSON blob small. */
const round4 = (n: number) => Math.round(n * 10000) / 10000;

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

type Props = {
  mediaId: number;
  alt: string;
};

export function MarkupCanvas({ mediaId, alt }: Props) {
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

  /** Pointer position as normalised image coordinates. getBoundingClientRect
   *  already reflects any CSS transform, so this stays correct at every zoom. */
  const toNorm = useCallback((e: React.PointerEvent): [number, number] | null => {
    const rect = imgRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return null;
    return [
      Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    ];
  }, []);

  const commitStroke = useCallback(async (points: [number, number][]) => {
    if (points.length < 2) return;
    const rounded = points.map(([x, y]) => [round4(x), round4(y)]) as [number, number][];
    const mark = await createMark.mutateAsync({
      mark: { kind: kindFor(tool), points: rounded },
      style: styleFor(tool, colour, width_),
    });
    mine.current.push(mark.id);
  }, [createMark, tool, colour, width_]);

  const onPointerDown = (e: React.PointerEvent) => {
    const at = toNorm(e);
    if (!at || !tool) return;
    if (tool === "eraser") return;                    // handled per-mark below
    if (tool === "text") {
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
    if (!drawing.current) return;
    const at = toNorm(e);
    if (!at) return;
    setStroke((s) => {
      if (tool === "line" || tool === "arrow") return [s[0], at];
      const last = s[s.length - 1];
      // Thin as we capture, not on commit: the in-progress path is re-rendered
      // on every move, so an unthinned stroke costs frames as well as bytes.
      if (last && Math.hypot(at[0] - last[0], at[1] - last[1]) < MIN_STEP) return s;
      return [...s, at];
    });
  };

  const onPointerUp = () => {
    if (!drawing.current) return;
    drawing.current = false;
    const points = stroke;
    setStroke([]);
    void commitStroke(points);
  };

  const commitText = async () => {
    const body = draftText.trim();
    const at = textAt;
    setTextAt(null);
    setDraftText("");
    if (!body || !at) return;
    const mark = await createMark.mutateAsync({
      mark: { kind: "text", points: [[round4(at.x), round4(at.y)]], text: body, size: 0.04 },
      style: styleFor("text", colour, width_),
    });
    mine.current.push(mark.id);
  };

  const undo = () => {
    const last = mine.current.pop();
    if (last != null) deleteMark.mutate(last);
  };

  // No aspect ratio is computed and no dimensions are fetched: the <img> is
  // sized by max-width/max-height inside a shrink-wrapping div, so that div IS
  // the image's rectangle and the SVG overlay at inset:0 matches it exactly.
  // See Task 7 for why the server does not send dimensions.

  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Button size="xs" variant={tool === "pen" ? "filled" : "light"}
                leftSection={<IconPencil size={14} />}
                onClick={() => setTool("pen")}>Pen</Button>
        <Button size="xs" variant={tool === "line" ? "filled" : "light"}
                leftSection={<IconLine size={14} />}
                onClick={() => setTool("line")}>Line</Button>
        <Button size="xs" variant={tool === "arrow" ? "filled" : "light"}
                leftSection={<IconArrowUpRight size={14} />}
                onClick={() => setTool("arrow")}>Arrow</Button>
        <Button size="xs" variant={tool === "highlighter" ? "filled" : "light"}
                leftSection={<IconHighlight size={14} />}
                onClick={() => setTool("highlighter")}>Highlight</Button>
        <Button size="xs" variant={tool === "text" ? "filled" : "light"}
                leftSection={<IconTextSize size={14} />}
                onClick={() => setTool("text")}>Text</Button>
        <Button size="xs" variant={tool === "eraser" ? "filled" : "light"}
                leftSection={<IconEraser size={14} />}
                onClick={() => setTool("eraser")}>Erase</Button>
        <Button size="xs" variant="default" leftSection={<IconArrowBackUp size={14} />}
                onClick={undo}>Undo</Button>
        <Group gap={4}>
          {COLOURS.map((c) => (
            <ColorSwatch key={c} color={c} size={18} style={{ cursor: "pointer" }}
                         onClick={() => setColour(c)}
                         withShadow={c === colour} />
          ))}
        </Group>
        <Slider w={90} min={0.001} max={0.01} step={0.001} value={width_}
                onChange={setWidth} label={null} />
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
        ref={frameRef}
        style={{ position: "relative", width: "100%", height: "70vh",
                 overflow: "hidden", display: "flex",
                 alignItems: "center", justifyContent: "center",
                 touchAction: "none", cursor: tool === "eraser" ? "pointer" : "crosshair" }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
       <div style={{ position: "relative", display: "inline-block", lineHeight: 0 }}>
        <img ref={imgRef} src={`/api/media/${mediaId}`} alt={alt} draggable={false}
             style={{ display: "block", maxWidth: "100%", maxHeight: "70vh",
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
          {(marks.data ?? []).map((m) => (
            <MarkShape key={m.id} mark={m} erasing={tool === "eraser"}
                       onErase={() => deleteMark.mutate(m.id)} />
          ))}
          {stroke.length > 1 && (
            <polyline points={stroke.map(([x, y]) => `${x},${y}`).join(" ")}
                      fill="none" stroke={colour}
                      strokeOpacity={tool === "highlighter" ? 0.35 : 1}
                      strokeWidth={tool === "highlighter" ? width_ * 4 : width_}
                      strokeLinecap="round" strokeLinejoin="round" />
          )}
        </svg>
       </div>

        {textAt && (
          <Box style={{ position: "absolute", left: textAt.px, top: textAt.py,
                        width: 220, zIndex: 5 }}>
            <Textarea autoFocus autosize minRows={1} maxRows={4} value={draftText}
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

      <Text size="xs" c="dimmed">
        Markup is visual only — it is not a clue and does not appear in the evidence table.
      </Text>
    </Stack>
  );
}

function MarkShape({ mark, erasing, onErase }:
  { mark: MediaMark; erasing: boolean; onErase: () => void }) {
  const { colour, width, opacity, arrow } = mark.style;
  const hit = erasing
    ? { pointerEvents: "all" as const, cursor: "pointer" }
    : { pointerEvents: "none" as const };
  if (mark.kind === "text") {
    const [[x, y]] = mark.shape.points;
    return (
      <text x={x} y={y} fill={colour} fillOpacity={opacity}
            fontSize={mark.shape.size ?? 0.04} style={hit}
            onPointerDown={erasing ? onErase : undefined}>
        {mark.text}
      </text>
    );
  }
  return (
    <polyline
      points={mark.shape.points.map(([x, y]) => `${x},${y}`).join(" ")}
      fill="none" stroke={colour} strokeOpacity={opacity} strokeWidth={width}
      strokeLinecap="round" strokeLinejoin="round"
      markerEnd={arrow ? `url(#arrow-${colour.slice(1)})` : undefined}
      style={hit}
      onPointerDown={erasing ? onErase : undefined}
    />
  );
}

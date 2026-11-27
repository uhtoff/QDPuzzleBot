# Image Overlay Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full-screen dashboard tool, opened from a puzzle's images, to lay images over a
background, move/scale/stretch/rotate/fade/blend/crop them, and copy or save the
flattened result to the puzzle.

**Architecture:** Pure TypeScript modules under `frontend/src/lib/overlay/` hold all
geometry, hit-testing, drawing and state (unit-tested in vitest's node environment). React
components under `frontend/src/components/overlay/` wire them to three stacked canvases
(underlay: frame + checkerboard; scene; chrome: selection, handles, export outline). One
`drawScene` function renders both the screen and the export. No server change.

**Tech Stack:** React 18, Mantine 7, TanStack Query, dnd-kit (already present), HTML
canvas 2D, vitest (node env — **no DOM in tests**; components are verified by
`npm run build` + manual check).

**Spec:** `docs/superpowers/specs/2026-11-27-image-overlay-design.md`

## Global Constraints

- No new npm dependencies. No server, DB or migration change.
- Desktop mouse + keyboard only; must not crash on a phone.
- One output pixel = one background pixel. Export bounds **grow to fit** the background plus
  every visible layer (rotated, cropped bounds), rounded out to whole pixels; transparent
  outside the background. Hidden layers excluded.
- Working frame = **25%** of the background's width/height each side (visual only).
- Canvas limits: **16384 px a side, 100 Mpx area**. Background over them is reduced on load
  with notice "Background reduced to W×H for editing"; export over them is refused naming
  the furthest-out layer(s).
- Blend modes and canvas ops: Normal `source-over`, Multiply `multiply`, Screen `screen`,
  Difference `difference`. UI labels: "Normal", "Multiply (hide white)", "Screen (hide black)", "Difference".
- Transforms are about the layer's centre (of the cropped area).
- New layer: centred on the view, longer side ≈ 1/3 of the background's shorter side,
  never enlarged above 100%.
- Checkerboard and frame shading are drawn **under** the scene canvas, never into it.
- Pasted/picked layers are not uploaded; only **Save to puzzle** uploads (as `overlay.png`)
  via `useUploadMedia(uid)` then `useAttachPuzzleImage(puzzleId)`.
- Overlay's paste listener runs in the capture phase and stops propagation so
  `PuzzleImages`' document paste handler does not also upload the layer.
- Images with mark-up are flagged in the picker: "Has mark-up — not included".
- Undo is session-only.

## Review Focus

1. **Preview and export disagree** (e.g. a Difference layer looks right on screen but the
   PNG differs) — the export must call the same `drawScene` with only the transform
   changed. Pinned by Task 4's test that screen and export draw-call sequences are
   identical apart from the transform.
2. **A rotated, stretched, cropped layer's handles drift from its picture** — handle
   positions, hit-testing and drawing must share `layerToBg`. Pinned by Task 2's
   round-trip test with all three applied at once.
3. **Pasting a layer also adds it to the puzzle's images** — pinned by the manual check in
   Task 9 (no DOM test runner).
4. **Huge image pasted as background** (a 20000 px screenshot) — reduced with notice, not a
   blank canvas or a crash. Pinned by Task 1's `fitCanvasLimits` tests + Task 7's notice.
5. **Keys typed into a numeric field move the layer** (typing "5" into rotation sets opacity
   to 50%) — keyboard handler must ignore text-entry targets via `isTextEntryTarget`.
   Manual check in Task 8.

---

## File structure

```
frontend/src/lib/overlay/
  types.ts        Layer, Background, Scene, Blend, Crop, Handle, Bounds
  geometry.ts     transforms, bounds, limits, placement, fit, handles, drags, crop, promote
  hitTest.ts      alpha-aware topmost-layer pick
  draw.ts         drawScene (screen + export), drawUnderlay, drawChrome
  state.ts        reducer: scene, selection, gestures, undo/redo, copy/paste size, dirty
  output.ts       exportCheck, renderExport, copyPng, downloadPng
  *.test.ts       one per module above except types
frontend/src/components/overlay/
  OverlayModal.tsx      full-screen modal; picker step, then editor
  OverlayPicker.tsx     choose background + layers from the puzzle's images
  OverlayEditor.tsx     canvases, view, pointer, keyboard, paste/drop/file input
  OverlayLayerList.tsx  thumbnails, reorder, hide, select
  OverlayLayerPanel.tsx opacity, blend, numeric fields, buttons, crop controls
frontend/src/components/PuzzleImages.tsx   + Overlay button
```

## Shared types (Task 1 creates them; every task uses them)

```ts
export type Blend = "normal" | "multiply" | "screen" | "difference";
export type Crop = { x: number; y: number; w: number; h: number };   // source pixels
export type Point = { x: number; y: number };                        // re-export from canvasMath
export type Bounds = { x: number; y: number; w: number; h: number };
export type Layer = {
  id: string; label: string;
  srcW: number; srcH: number;        // natural size of the source image
  cx: number; cy: number;            // centre of the cropped area, background px
  sx: number; sy: number;            // scale; 1 = one source px per background px
  rot: number;                       // degrees, clockwise
  opacity: number;                   // 0..1
  blend: Blend;
  crop: Crop;                        // full image = {x:0,y:0,w:srcW,h:srcH}
  flipX: boolean; visible: boolean;
};
export type Background = {
  id: string; label: string;
  srcW: number; srcH: number; crop: Crop; flipX: boolean;
  w: number; h: number;              // displayed size in background px (= crop size unless reduced)
  opacity: number; visible: boolean;
};
export type Scene = { background: Background | null; layers: Layer[] };  // layers bottom → top
export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "rotate" | "body";
```

Image pixel data is never in state: components keep `Map<string, CanvasImageSource>` keyed
by layer/background `id`.

---

### Task 1: Types and core geometry

**Files:** Create `frontend/src/lib/overlay/types.ts`, `frontend/src/lib/overlay/geometry.ts`,
`frontend/src/lib/overlay/geometry.test.ts`.

**Interfaces — Produces** (all in `geometry.ts`):
- `FRAME = 0.25`, `MAX_SIDE = 16384`, `MAX_AREA = 100_000_000`, `HANDLE_PX = 8`, `ROTATE_OFFSET_PX = 24`
- `layerToBg(l: Layer, local: Point): Point` — `local` is source px relative to the crop centre (before flip/scale/rotate); applies flipX, scale, rotation, translation.
- `bgToLayer(l: Layer, p: Point): Point` — exact inverse.
- `layerCorners(l: Layer): [Point, Point, Point, Point]` — nw, ne, se, sw in background px.
- `layerBounds(l: Layer): Bounds`
- `exportBounds(scene: Scene): Bounds` — background rect ∪ visible layers' bounds; `floor` min, `ceil` max.
- `fitCanvasLimits(w: number, h: number): { w: number; h: number; reduced: boolean }` — uniform scale-down to satisfy MAX_SIDE and MAX_AREA, integers.
- `initialPlacement(srcW: number, srcH: number, bg: Background | null, at: Point): Pick<Layer, "cx"|"cy"|"sx"|"sy">`
- `fitView(bg: Background, frameW: number, frameH: number): ViewTransform` — fits bg plus FRAME on each side, centred (`ViewTransform` from `../canvasMath`; screen = bg·scale + offset).

- [ ] **Step 1: Write failing tests** (`geometry.test.ts`). Use a helper `mk(partial): Layer` with defaults srcW 100, srcH 50, full crop, cx 0, cy 0, sx 1, sy 1, rot 0, opacity 1, normal, unflipped, visible. Assertions (use `toBeCloseTo` for floats):
  - `layerToBg(mk({cx:10,cy:20}), {x:50,y:25})` → `{x:60,y:45}`.
  - rot 90: `layerToBg(mk({rot:90}), {x:50,y:0})` → `{x:0,y:50}` (clockwise in screen coords, y down).
  - flipX: `layerToBg(mk({flipX:true}), {x:50,y:0})` → `{x:-50,y:0}`.
  - round trip: for a layer with `sx:2, sy:0.5, rot:33, flipX:true, cx:7, cy:-3, crop:{x:10,y:5,w:60,h:30}`, `layerToBg(l, bgToLayer(l, p))` ≈ `p` for three points.
  - `layerCorners(mk({sx:2}))[0]` → `{x:-100,y:-25}`; uses crop size, not src size, when cropped.
  - `exportBounds` with bg 100×50 and one layer 20×20 at (100,25): `{x:0,y:0,w:110,h:50}`; same layer hidden → `{x:0,y:0,w:100,h:50}`; layer at (-10.4, 25) → x is `-21` (floored).
  - `fitCanvasLimits(20000, 1000)` → `{w:16384, h:819, reduced:true}`; `fitCanvasLimits(12000, 12000)` → area ≤ 1e8, `reduced:true`; `fitCanvasLimits(800, 600)` → unchanged, `reduced:false`.
  - `initialPlacement(600, 300, bg 900×600, at {x:450,y:300})` → `sx = sy = 200/600`; `initialPlacement(50, 40, same bg, …)` → `sx = sy = 1` (never enlarged); with `bg: null` → scale 1, centre `at`.
  - `fitView(bg 100×100, 300, 300)`: bg + frame is 150 px wide → scale 2, and bg (0,0) maps to screen (50,50).

- [ ] **Step 2: Run, expect FAIL:** `cd frontend && timeout 120 npx vitest run src/lib/overlay > /tmp/ov1.log 2>&1`
- [ ] **Step 3: Implement** `types.ts` (as in Shared types) and the functions above.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(overlay): types and core geometry`

### Task 2: Handles, drags, nudge, crop, promote

**Files:** Modify `geometry.ts`, `geometry.test.ts`.

**Interfaces — Consumes:** Task 1. **Produces:**
- `handlePoints(l: Layer, view: ViewTransform): Record<Exclude<Handle,"body">, Point>` — screen px; rotate handle sits `ROTATE_OFFSET_PX` screen px beyond the top-edge midpoint, along the layer's "up".
- `handleAt(l: Layer, view: ViewTransform, screen: Point): Handle | null` — within `HANDLE_PX` of a handle (rotate checked first, then corners, then sides), else `"body"` if inside the rotated rect, else null.
- `dragLayer(start: Layer, handle: Handle, from: Point, to: Point, opts: { shift: boolean }): Layer` — `from`/`to` in background px.
- `nudge(l: Layer, dx: number, dy: number, view: ViewTransform): Layer` — dx/dy in **screen** px.
- `setCrop(l: Layer, crop: Crop): Layer` — keeps the visible content where it is on the background.
- `promoteToBackground(scene: Scene, layerId: string): Scene`

Drag algorithm (c = layer centre, u = local x-axis unit vector, v = local y-axis unit vector):
corners `k = |to−c| / |from−c|`, `sx,sy ×= k`; `e`/`w` `sx ×= |dot(to−c,u)| / |dot(from−c,u)|`; `n`/`s` same with v and sy;
rotate `rot += angle(to−c) − angle(from−c)` (degrees), Shift rounds the result to 15°; body `c += to − from`.
Floor scale at 0.01. `cx, cy` never change for scale/stretch/rotate.

Promote: new background = promoted layer's source with its crop and flipX, `w,h` = crop size (then `fitCanvasLimits`), opacity 1, visible.
Every other layer `L` (and the old background, turned into a layer with `cx=w/2, cy=h/2, sx=sy=w/crop.w`, rot 0) is re-projected:
centre `= bgToLayer(P, L.centre) + (crop.w/2, crop.h/2)`, `sx /= P.sx`, `sy /= P.sy`, `rot −= P.rot`, `flipX ^= P.flipX` (mirror the x of the centre too when P is flipped).
Exact when `P.sx == P.sy`; with stretch + rotation it is approximate — say so in a code comment. Old background goes to the bottom of the layer stack.

- [ ] **Step 1: Write failing tests:**
  - **Centre fixed:** for a layer at (40,30) with rot 30, every handle except body leaves `cx,cy` unchanged after `dragLayer`.
  - Corner: start sx=sy=1, `from` 10 px from centre, `to` 20 px from centre → sx=sy=2.
  - Side `e` on rot 0: `from {cx+50,cy}` `to {cx+100,cy+40}` → sx=2, sy unchanged (vertical motion ignored).
  - Rotate: from directly right of centre to directly below → rot +90; Shift with a 50° raw result → 45.
  - Body: moves centre by `to − from`.
  - `handleAt`: on a layer with `sx:2, sy:0.5, rot:33, crop` set, each point from `handlePoints` returns its own handle name; the centre returns `"body"`; a point far away returns null. (Pins Review Focus #2.)
  - `nudge(l, 1, 0, {scale:4, offset:{x:0,y:0}})` moves cx by 0.25; at scale 0.5 by 2.
  - `setCrop`: src 100×100 at (50,50), sx 1, crop to `{x:50,y:0,w:50,h:100}` → cx 75, cy 50. Same with sx 2 → cx 100.
  - `promoteToBackground`: bg 200×100; P src 50×50 at (100,50) sx=sy=2; M at (150,50) sx 1. Result: bg 50×50, id = P.id; M at (50,25) with sx 0.5; old bg is the bottom layer at (25,25) with sx=sy=0.5; P no longer in layers.
- [ ] **Step 2: Run, expect FAIL.**  **Step 3: Implement.**  **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(overlay): handles, drag maths, crop and promote`

### Task 3: Alpha-aware picking

**Files:** Create `frontend/src/lib/overlay/hitTest.ts`, `hitTest.test.ts`.

**Interfaces — Produces:**
`pickLayer(layers: Layer[], p: Point, alphaAt: (id: string, srcX: number, srcY: number) => number): string | null`
— topmost visible layer whose rect contains `p` and whose `alphaAt` at the corresponding source pixel (after crop offset and flip) is ≥ `ALPHA_HIT = 16` (0–255). The sampler is injected; the component implements it (Task 7).

- [ ] **Step 1: Failing tests:** two overlapping layers A (bottom) and B (top) covering `p`: B opaque → B; B's sampler returns 0 at that pixel → A; B hidden → A; point outside both → null; flipped B samples the mirrored source x (assert via a sampler that records its arguments).
- [ ] **Steps 2–4:** run FAIL, implement, run PASS.
- [ ] **Step 5: Commit** — `feat(overlay): alpha-aware layer picking`

### Task 4: Drawing

**Files:** Create `frontend/src/lib/overlay/draw.ts`, `draw.test.ts`.

**Interfaces — Produces:**
- `type DrawCtx = Pick<CanvasRenderingContext2D, "save"|"restore"|"setTransform"|"translate"|"rotate"|"scale"|"drawImage"|"clearRect"|"fillRect"|"strokeRect"|"beginPath"|"moveTo"|"lineTo"|"closePath"|"stroke"|"fill"|"arc"|"setLineDash"> & { globalAlpha: number; globalCompositeOperation: GlobalCompositeOperation; fillStyle: string|CanvasPattern; strokeStyle: string; lineWidth: number }`
- `BLEND_OP: Record<Blend, GlobalCompositeOperation>`
- `drawScene(ctx: DrawCtx, scene: Scene, images: Map<string, CanvasImageSource>, view: ViewTransform, size: { w: number; h: number }): void` — clears `size`, draws background then visible layers bottom → top; each layer: `setTransform` from view, translate to centre, rotate, scale (with flip), `globalAlpha`, `globalCompositeOperation`, `drawImage(img, crop.x, crop.y, crop.w, crop.h, −crop.w/2, −crop.h/2, crop.w, crop.h)`; restores state after each. Missing image → skip that layer.
- `drawUnderlay(ctx, bg: Background, view, size, checker: CanvasPattern | string): void` — frame shading over the FRAME area, checkerboard over the background rect.
- `drawChrome(ctx, scene, selectedId: string | null, view, size, opts: { cropMode: boolean }): void` — dashed `exportBounds` outline always; selected layer's box + handles from `handlePoints`.
- `exportView(bounds: Bounds): ViewTransform` → `{ scale: 1, offset: { x: -bounds.x, y: -bounds.y } }`.

- [ ] **Step 1: Failing tests** with a recording fake `DrawCtx` (records method calls and property sets in order):
  - Hidden layer → no `drawImage` with its image.
  - Order: background's `drawImage` before layer 1's before layer 2's.
  - A Difference layer at opacity 0.4: `globalCompositeOperation = "difference"` and `globalAlpha = 0.4` are set before its `drawImage`; the next Normal layer gets `"source-over"` back.
  - Crop `{x:10,y:5,w:60,h:30}` → `drawImage(img, 10, 5, 60, 30, -30, -15, 60, 30)`.
  - **Screen vs export (Review Focus #1):** recording `drawScene` with a screen view and with `exportView(bounds)` gives identical call sequences once `setTransform` arguments are ignored.
- [ ] **Steps 2–4:** run FAIL, implement, run PASS. `drawUnderlay`/`drawChrome` need only a smoke test that they run against the fake without throwing.
- [ ] **Step 5: Commit** — `feat(overlay): scene, underlay and chrome drawing`

### Task 5: State reducer with undo

**Files:** Create `frontend/src/lib/overlay/state.ts`, `state.test.ts`.

**Interfaces — Produces:**
```ts
export type OverlayState = {
  scene: Scene; selectedId: string | null; cropMode: boolean;
  past: Scene[]; future: Scene[]; gestureStart: Scene | null;
  copiedSize: { sx: number; sy: number } | null; dirty: boolean;
};
export const initialOverlayState: OverlayState;
export type OverlayAction =
  | { type: "addImage"; id: string; label: string; srcW: number; srcH: number; w: number; h: number; at: Point }
  | { type: "select"; id: string | null }
  | { type: "beginGesture" } | { type: "endGesture" }
  | { type: "updateLayer"; id: string; patch: Partial<Layer> }
  | { type: "updateBackground"; patch: Partial<Pick<Background, "opacity" | "visible">> }
  | { type: "remove"; id: string } | { type: "toggleVisible"; id: string }
  | { type: "reorder"; from: number; to: number }
  | { type: "promote"; id: string } | { type: "setCrop"; id: string; crop: Crop }
  | { type: "setCropMode"; on: boolean }
  | { type: "copySize" } | { type: "pasteSize" }
  | { type: "undo" } | { type: "redo" } | { type: "markSaved" };
export function overlayReducer(s: OverlayState, a: OverlayAction): OverlayState;
```
Rules: first `addImage` becomes the background (`w,h` given by the caller after `fitCanvasLimits`); later ones become layers placed with `initialPlacement` and selected. Every scene-changing action outside a gesture pushes one undo entry; inside `beginGesture…endGesture` the whole gesture is one entry (pushed at `endGesture` only if the scene changed). New change clears `future`. History cap 100. `dirty` true after any scene change, false after `markSaved`. `remove` of the selected layer clears selection. `select`, `setCropMode`, `copySize` do not touch history.

- [ ] **Step 1: Failing tests:** first image → background, second → selected layer; a gesture of 20 `updateLayer`s undoes in one step and redoes back; `copySize` on A then `pasteSize` on B sets B's sx/sy to A's; `remove` selected → `selectedId` null; history capped at 100; `dirty` set by `updateLayer`, cleared by `markSaved`, unaffected by `select`.
- [ ] **Steps 2–4:** run FAIL, implement, run PASS.
- [ ] **Step 5: Commit** — `feat(overlay): editor state with undo`

### Task 6: Output

**Files:** Create `frontend/src/lib/overlay/output.ts`, `output.test.ts`.

**Interfaces — Produces:**
- `exportCheck(scene: Scene): { ok: true; bounds: Bounds } | { ok: false; message: string }` — over MAX_SIDE/MAX_AREA → message `"Too large to save (W×H). Move or hide: <labels>"`, naming the visible layer(s) whose bounds lie furthest outside the background (up to 3).
- `renderExport(scene, images, bounds): Promise<Blob>` — `document.createElement("canvas")` sized to bounds, `drawScene` with `exportView(bounds)`, `toBlob("image/png")`.
- `copyPng(blob: Blob): Promise<"copied" | "unsupported">` — `navigator.clipboard.write([new ClipboardItem({"image/png": blob})])`; any failure → `"unsupported"`.
- `downloadPng(blob: Blob, filename: string): void`

- [ ] **Step 1: Failing tests** for `exportCheck` only (the rest needs a DOM): within limits → `ok` with `exportBounds`; a layer parked at x = 20000 → `ok:false`, message contains that layer's label and not the in-bounds layer's.
- [ ] **Steps 2–4:** run FAIL, implement all four, run PASS; `npm run build` passes.
- [ ] **Step 5: Commit** — `feat(overlay): export check, render, copy and download`

### Task 7: Editor canvas, view and input

**Files:** Create `frontend/src/components/overlay/OverlayEditor.tsx`.

**Interfaces — Consumes:** Tasks 1–6, `useWheelZoom` (`hooks/useZoomPan.ts`), `zoomAt` (`lib/canvasMath.ts`),
`imageFromClipboard` / `isTextEntryTarget` (`util.ts`). **Produces:**
`OverlayEditor({ initial, onDirtyChange, renderSidebar }: { initial: { id: string; label: string; url: string }[]; onDirtyChange: (dirty: boolean) => void; renderSidebar: (s: OverlayState, dispatch: React.Dispatch<OverlayAction>, images: Map<string, CanvasImageSource>) => React.ReactNode })`
— `initial[0]` (if any) is the background.

Behaviour (spec sections "Selecting and handles", "Keyboard", "View", "Paste handling"):
- Three absolutely stacked canvases at devicePixelRatio; redraw on state/view/size change via `requestAnimationFrame`.
- Image loading: `createImageBitmap(blob)`; for URLs `fetch(url)` → blob (same-origin). A source exceeding `fitCanvasLimits` is drawn into an offscreen canvas at the reduced size and that is stored instead; for the background show Mantine notification "Background reduced to W×H for editing".
- Alpha sampler for `pickLayer`: per image, a cached alpha map at ≤1024 px longest side (offscreen canvas `getImageData` once), sampled proportionally.
- Pointer: down → `handleAt` on selected layer first, else `pickLayer` → select + `beginGesture`; move → `dragLayer` from the gesture's start layer; up → `endGesture`. Space+drag or middle-drag pans. Cursor reflects the handle under the pointer.
- Wheel: Ctrl → `useWheelZoom`/`zoomAt`; plain → pan vertical; Shift → horizontal. `F` / Fit button → `fitView`.
- Keyboard (window listener; ignore when `isTextEntryTarget(e.target)`): exactly the spec's table, plus Tab / Shift+Tab cycling selection through visible layers. Ctrl+C only when `window.getSelection()?.toString()` is empty.
- Paste: `document.addEventListener("paste", h, true)`; on an image, `preventDefault()` + `stopPropagation()` and add it. Drag-drop of files onto the canvas and a multi-file `FileButton` add images too.
- Uploads nothing.

- [ ] **Step 1:** Implement. **Step 2:** `cd frontend && timeout 180 npm run build > /tmp/b.log 2>&1` passes; `npx vitest run` unchanged.
- [ ] **Step 3: Commit** — `feat(overlay): editor canvas, view and input`

### Task 8: Layer list and layer panel

**Files:** Create `OverlayLayerList.tsx`, `OverlayLayerPanel.tsx` in `frontend/src/components/overlay/`.

**Interfaces — Consumes:** `OverlayState`, `OverlayAction`, the images map.
- List: top of list = top of stack; dnd-kit sortable (pattern as in `PuzzleImages.tsx`) dispatching `reorder`; eye toggles `toggleVisible`; click selects; background row last, marked, not draggable, with its own opacity + visibility.
- Panel (selected layer): opacity slider 0–100 with number; `SegmentedControl` for blend (labels from Global Constraints); `NumberInput`s for X, Y, width %, height %, rotation, with a lock (default on) tying width/height %; buttons Flip horizontal, Crop / Reset crop, Reset transform, Use as background, Copy size, Paste size (disabled when nothing copied), Delete. Numeric edits dispatch inside a gesture so each field change is one undo step.
- Crop mode (`cropMode` true): editor draws the layer unrotated with the uncropped image dimmed and the crop rect bright, edge/corner drags adjust it; Enter or the button applies (`setCrop`), Esc cancels. Implement crop-mode pointer handling in `OverlayEditor.tsx` as part of this task.

- [ ] **Step 1:** Implement. **Step 2:** build passes. **Step 3: Manual check (Review Focus #5):** typing digits/arrows in a NumberInput edits the field only.
- [ ] **Step 4: Commit** — `feat(overlay): layer list, layer panel and crop`

### Task 9: Modal, picker, puzzle entry point, save/copy

**Files:** Create `OverlayModal.tsx`, `OverlayPicker.tsx`; modify `frontend/src/components/PuzzleImages.tsx`.

**Interfaces — Consumes:** everything above; `usePuzzleImages`, `useUploadMedia`, `useAttachPuzzleImage`, `useMarkup` (`api.ts`).
**Produces:** `OverlayModal({ opened, onClose, puzzleId, uid, images }: { opened: boolean; onClose: () => void; puzzleId: number; uid: string; images: PuzzleImage[] })`.

- `PuzzleImages`: an **Overlay** `ActionIcon` (64 px, beside add-image, `IconLayersSubtract`) shown only when `editable`.
- Modal: full-screen, flex body as `MarkupModal.tsx`. If `images.length > 0`, open on `OverlayPicker`: thumbnails, radio for background, checkboxes for layers, "Has mark-up — not included" badge where `useMarkup(media_id).data?.length` > 0, buttons Start and Skip. Then `OverlayEditor` with the chosen images (URLs = `media_url`).
- Toolbar: **Copy**, **Save to puzzle**, **Download**, Fit. Each runs `exportCheck` first and shows its message on failure. Copy → `copyPng`; on `"unsupported"` download instead and notify "Clipboard not available — downloaded instead". Save → upload `new File([blob], "overlay.png", {type:"image/png"})`, attach, notify "Saved to puzzle images", dispatch `markSaved`, stay open. Download → `overlay.png`.
- Close with `dirty` → `ConfirmModal` "Discard this overlay?".

- [ ] **Step 1:** Implement. **Step 2:** `npm run build` and `npx vitest run` pass; `pipenv run basedpyright bot/` unchanged (no Python touched).
- [ ] **Step 3: Manual checklist for the user (desktop):**
  - Westeros-style: map background, 3+ transparent stickers; scale one, Copy size → Paste size to others; rotate with handle and Shift; crosshair stays put while rotating; click through a sticker's transparent corner selects the one below.
  - Logo-style: paste a logo, paste a white-shape screenshot, crop it, Multiply, stretch with a side handle, rotate.
  - Park a layer off the edge: dashed outline grows; hide it: outline shrinks. Save → image appears in the strip, sized to the outline; open it in the mark-up editor.
  - **Paste a layer and confirm the puzzle's image strip did not gain it** (Review Focus #3).
  - Copy → paste into Discord. Undo/redo through a drag and a numeric edit. Close with changes → confirm.
- [ ] **Step 4: Commit** — `feat(overlay): overlay modal, picker and save to puzzle`

## Self-review notes

- Spec coverage: entry point, picker + mark-up flag (T9); background fixed + reduction (T1, T7); frame (T1 `fitView`, T4 underlay); grow-to-fit + limits (T1, T6); layers' properties and placement (T1, T5); handles + Shift snap (T2, T7); alpha click-through (T3, T7); keyboard + view (T7); side panel, copy/paste size, list, crop (T8); paste capture (T7, checked T9); output buttons, close confirm, archived hidden (T9); promote (T2, T8).
- Left to the builder per spec: handle visuals, panel layout, alpha-map resolution, multi-select vs copy/paste size (copy/paste chosen here).

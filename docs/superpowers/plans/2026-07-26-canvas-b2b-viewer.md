# Canvas B2b — Viewer, Region UI and Discord Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show a hunt's canvas as a pannable, zoomable tiled image with linked regions drawn
over it, tinted by puzzle progress, and let anyone draw a box to link or create a puzzle.

**Architecture:** `CanvasView` mounts only the tiles intersecting the viewport at the current
pyramid level, over an SVG overlay at `viewBox="0 0 1 1"` — so region coordinates, already
normalised 0–1 by B2a, need no arithmetic at any zoom. `CanvasPanel` owns canvas selection,
upload and the legend. `RegionModal` owns label/note/link/role and draw-to-create.

**Tech Stack:** React 18, TypeScript, Mantine 7, @tanstack/react-query, Vite.

**Authoritative spec:** `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md`.
B1 (tiled media) and B2a (model/service/API) are done.

**Note on this plan's density:** written more compactly than the B1/B2a plans, because it is
being executed immediately in the same session rather than handed to a fresh agent. Decisions,
files and gotchas are complete; not every step carries a full code block.

---

## Verification reality — read this first

**There is no frontend test runner.** `package.json` has no vitest/jest and no `test` script;
the only gate is `npm run build`, which is `tsc --noEmit && vite build`. That checks *shapes*,
not behaviour: it cannot catch a wrong tile URL, an inverted zoom, a region that draws in the
wrong place, or a mutation that posts the wrong body.

So for B2b:

- **Backend additions get real tests** (the create-puzzle route, the `!canvas` command).
- **Frontend is typecheck-plus-inspection only.** Say so when reporting; do not imply the
  viewer is test-covered. The honest verification is `npm run build` clean plus driving the
  real app against Edric's sheet.

---

## Task 1: Types and API hooks

**Files:** `frontend/src/types.ts`, `frontend/src/api.ts`

- [ ] **Step 1: Types**

```ts
export type RegionRole = "locates" | "informs";

export type RegionShape = {
  type: "rect";
  /** Normalised 0-1, top-left first: [[x1,y1],[x2,y2]]. */
  points: [[number, number], [number, number]];
};

export type CanvasRegion = {
  id: number;
  shape: RegionShape;
  label: string;
  note: string | null;
  puzzle_id: number | null;
  role: RegionRole | null;
};

export type HuntCanvas = {
  id: number;
  name: string;
  note: string | null;
  media_id: number;
  regions: CanvasRegion[];
  /** From the media row. Absent only if the media row vanished. */
  width?: number;
  height?: number;
  tile_status?: "none" | "pending" | "ready" | "failed";
  tile_levels?: number;
};
```

- [ ] **Step 2: Hooks**

`useCanvases(uid)` → `apiGet<{canvases: HuntCanvas[]}>`, query key `["canvases", uid]`.
Mutations `useCreateCanvas`, `useUpdateCanvas`, `useDeleteCanvas`, `useCreateRegion`,
`useUpdateRegion`, `useDeleteRegion`, `useCreatePuzzleFromRegion` — each invalidating
`["canvases", uid]`, and the puzzle-touching ones also `["hunt", uid]`.

`useUploadMedia`'s return type widens to include `width`, `height`, `tile_status` (B1 added
them to the response and the type still says `{id, url}`).

- [ ] **Step 3: Gate**

Run: `cd frontend && npm run build`
Expected: clean.

---

## Task 2: `CanvasView` — the tiled viewer

**Files:** Create `frontend/src/components/CanvasView.tsx`

The load-bearing decisions:

- **Level choice.** `levels - 1` is native resolution. Pick the level whose rendered image
  width is closest to (but not below) the on-screen width, clamped to `[0, levels-1]`.
- **Tile URL** is `/api/media/<id>/tiles/<z>/<y>/<x>` — **row before column**. Getting this
  backwards transposes the image about its diagonal and looks almost plausible, which is why
  B2a's `tile_relpath` test asserts it with `y != x`.
- **Only mount visible tiles.** Compute the visible tile range from the pan offset, the
  viewport size and the scale; render that window plus a one-tile margin. Mounting all 940
  would defeat the point of tiling.
- **A missing tile is not an error.** The route serves the blank tile, so `onError` needs no
  special handling — but do not add an error placeholder that would flash over blank paper.
- **Overlay.** One `<svg viewBox="0 0 1 1" preserveAspectRatio="none">` absolutely positioned
  over the tile layer, sized to the same scaled box. Region rects use the normalised
  coordinates directly — no arithmetic, which is the entire payoff of B2a's coordinate choice.
- **Fallback.** `tile_status` `none`/`failed` → a plain `<img src="/api/media/<id>">`.
  `pending` → a Loader with "Preparing image…". This is why the status is on the payload.
- **Pan** = pointer drag on the wrapper, translating the layer. **Zoom** = wheel and buttons,
  changing scale about the cursor. **Draw** = drag with the draw toggle on, producing a
  normalised rect handed to `onDraw`.

Tint, per the design (collapsing seven statuses into four so the picture stays readable):

| colour | statuses |
| --- | --- |
| green | `solved`, `backsolved` |
| amber | `in progress`, `needs extraction`, `needs submission` |
| red | `stuck` |
| grey | `unstarted` |

Unlinked regions get a neutral outline and no fill; **untinted image is art nobody has
claimed**, which is half the answer to "what have we done so far".

---

## Task 3: `RegionModal`

**Files:** Create `frontend/src/components/RegionModal.tsx`

Label, note (multiline — transcription is the headline use), a puzzle picker, a role
`SegmentedControl` (`locates` / `informs`), Save and Delete. Plus a "create a new puzzle"
branch: name + round picker + meta type, posting to the create-puzzle route from Task 5.

Two rules from the model:

- **Role and puzzle move together.** Clearing the puzzle clears the role; the API rejects a
  half-set link with a 400, so the UI must not offer one.
- **Explain the roles in the UI**, because the distinction is the whole feature: `locates` =
  "this part of the picture *is* that puzzle"; `informs` = "this is a *clue for* that puzzle".

---

## Task 4: `CanvasPanel` and the board entry

**Files:** Create `frontend/src/components/CanvasPanel.tsx`; modify
`frontend/src/pages/HuntBoard.tsx`

- Canvas `Select` when there are several; upload control (organiser-only, matching the API's
  bar); rename/delete (organiser-only); the legend; and a claimed count (`18/34`) from
  `regions.filter(r => r.puzzle_id).length` over `regions.length`.
- `HuntBoard.tsx`: add `{ label: "Canvas", value: "canvas" }` to the `SegmentedControl` and
  widen the hard-coded `w={360 + …}`. **The width is hard-coded and will clip the new entry if
  not updated** — the design calls this out explicitly.
- Add `"canvas"` to the `view` union in both the `useState` and the `onChange` cast.
- Invalidate `["canvases", uid]` in the existing `useActivityFeed` callback, so a second
  annotator's regions arrive over the websocket already wired for the board.

---

## Task 5: `create-puzzle-from-region` (backend, tested)

**Files:** `bot/web/actions.py`; test in `tests/test_canvas_web.py`

`POST /api/canvases/<id>/regions/<rid>/create-puzzle` with `{name, round_id, meta_type?, role}`.

- Delegates to the **existing** `puzzle_service` create path — no duplicate creation logic and
  no new "half-real puzzle" concept — then links the region to the returned puzzle via
  `canvas.update_region`.
- **Locking:** `bot/core` is lock-free by convention and callers lock. The create path takes
  its own locks internally, so this route must **not** wrap it in one — that is the documented
  self-deadlock shape (`bulk_re_edge` is the precedent).
- Permission: what the existing create path requires, not the looser region bar. Creating
  Discord channels and sheet tabs is not the same act as drawing a box.
- Tests: delegates to the create path with the right arguments; links the region on success;
  does not link if creation fails; rejects an unknown round.

---

## Task 6: `!canvas`

**Files:** `bot/cogs/puzzles.py`, `docs/commands.md`; test in `tests/test_canvas_commands.py`

Posts a link to the canvas view for the current hunt. Lists them if there are several, and
says so plainly if there are none. **Drawing stays web-only** — a deliberate parity exception
in the same class as the archiving family, so note it in `docs/commands.md` rather than leaving
a reader to wonder.

Per the house rule, the docstring and the `docs/commands.md` row change in the same commit.

---

## Task 7: Gates

- `cd frontend && npm run build` — clean.
- `timeout 600 pipenv run python -m pytest tests/ -q` — no new failures.
- `pipenv run ruff check`; `timeout 300 pipenv run basedpyright bot/` — clean, 0 errors.
- **Drive the real app**: upload Edric's 14400² sheet, confirm it tiles, pans and zooms; draw a
  region; link it both ways; confirm an `informs` region appears in the Evidence panel.

## Deliberately not in B2b

Polygons and freehand; a second drawing tool; server-side compositing of the marked-up canvas;
per-round canvas scoping; undo beyond delete; OCR. All excluded by the design.

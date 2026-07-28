# Current Spec — canvas hardening tranche

Tool: Claude Code (handoff)
Worktree: `.claude/worktrees/working-2` (persistent; branch `working-2`) — see Branching below
Target model per phase: Phase 1 (tests) = **Opus 4.8**; Phases 2–5 = **Sonnet 4.6**
Started: 2026-07-28
Status: ready for handoff — Phase 1 (tests)

## Before you start (executing model)

- Execute ONLY the current phase. Do not read ahead and start implementing during Phase 1.
- On any unexpected failure, invoke `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste
  the acceptance command output.
- **Two-strikes rule:** if one acceptance command fails twice without a clear understood fix,
  write `.ai/active/blocker.md` (what ran, what you saw, best hypothesis) and STOP.

## Branching (integration target: staging)

`staging` is currently a strict ancestor of `main` (main is ahead). Sequence:

1. In the main checkout: `git checkout staging && git merge --ff-only main && git checkout main`
   (brings staging level; a non-ff here means the world changed — stop and report).
2. In `.claude/worktrees/working-2`: `git merge --ff-only staging` (resync the worktree branch).
   It already has its pipenv venv, `config.json`, `google_secrets.json`, `frontend/node_modules`.
3. Do ALL work on `working-2`, commit per phase, and at the end merge `working-2` into `staging`
   (NOT main — main is pulled from staging later for live).

## Goal

Harden the canvas/media work that shipped in July: fix the four leftover review findings,
cap and repair tiling, and put the pure canvas coordinate maths under a real frontend test
runner (Vitest).

## In scope

1. `puzzle_images.reorder` accepts a subset/duplicates of the puzzle's image ids, leaving
   duplicate positions → reject anything that isn't an exact permutation.
2. `GET /api/attachments/<id>` (`bot/web/api.py` `attachment_bytes`) lacks the
   `X-Content-Type-Options: nosniff` header the newer media routes set.
3. `bot/web/data.py` `_get_connection` uses blocking `time.sleep` — VERIFY it can only run on
   a `to_thread` worker, and pin that with a comment (no behaviour change expected).
4. Unstable `useCallback` deps defeating `memo`: `EvidencePanel.tsx` `onReplaceImage`
   (~line 130) and `PuzzleImages.tsx` `onAddImage` (~line 136) depend on whole react-query
   mutation objects; depend on their stable `.mutateAsync` instead.
5. Tiling concurrency semaphore in `bot/core/media_tiling.py` (cap 2; each job peaks ~360 MB).
6. `POST /api/canvases/<id>/retile` — recover rows stuck `pending` (process died mid-tile) or
   `failed`, plus a "Regenerate tiles" button in `CanvasView.tsx`.
7. Vitest: extract the pure coordinate maths (`zoomAt` arithmetic, tile-grid snap, `toNorm`
   clamp) into `frontend/src/lib/canvasMath.ts` and test it.

## Out of scope — do NOT do these

- **#24 (event-loop-blocking Sheets calls) — ALREADY FIXED** (commits `8319a31`, `f8fcfe9`).
  Do not touch `bot/cogs/puzzles_gsheet.py` or `bot/utils/gsheets.py`.
- Media garbage collection, two-browser live-sync toast, markup-on-canvas, auto-OCR,
  `!images` Discord parity — future tranches.
- No schema/migration changes. Nothing in this tranche needs one.
- Do not restructure `MarkupCanvas`'s DOM (frame → shrink-wrap → img+svg) or "simplify" the
  `object-fit` sizing — its comments explain why. The Vitest task moves ARITHMETIC only;
  every DOM read (`getBoundingClientRect`, refs) stays in the component.
- Do not touch `tests/conftest.py` or existing passing tests.

## Relevant files

- `bot/core/puzzle_images.py` — `reorder` at line 36 (edit)
- `bot/web/actions.py` — `reorder_puzzle_images` at ~2531 (edit); new retile route near the
  canvas routes ~2269–2320; `_canvas_ctx` at 2135 (read only)
- `bot/web/api.py` — `attachment_bytes` ~line 191; `_tile_and_push` + `_tiling_tasks` at
  ~30–50 (read only, imported by the new route); upload route ~370–396 shows the task-spawn
  idiom (read only)
- `bot/web/data.py` — `_get_connection` ~line 50 (comment only)
- `bot/core/media_tiling.py` — whole file is 42 lines (edit)
- `bot/store/mysqldb.py` — `set_positions` ~1303 (read only)
- `frontend/src/components/CanvasView.tsx` — `zoomAt` ~148, tile snap ~424–441, pending
  branch ~295, failed fallback ~447 (edit)
- `frontend/src/components/MarkupCanvas.tsx` — `toNorm` ~146 (edit)
- `frontend/src/components/EvidencePanel.tsx` ~130, `frontend/src/components/PuzzleImages.tsx`
  ~136 (edit)
- `frontend/src/api.ts` — add the retile mutation next to the other canvas mutations (edit)
- `frontend/package.json` (edit), `frontend/src/lib/canvasMath.ts` + `canvasMath.test.ts` (new)
- Tests to extend: `tests/test_puzzle_images_service.py`, `tests/test_puzzle_images_web.py`,
  `tests/test_message_routes_visibility.py`, `tests/test_media_tiling.py`,
  `tests/test_canvas_web.py`

## Constraints

- Touch only the files listed. No refactors beyond the named extraction. No new `web_*` twins.
- Follow `docs/agents/python.md` (committed in the worktree) for runner/mock rules; the
  gating rules are inlined below regardless.
- basedpyright must stay at 0 errors on `bot/`; ruff (`F` rules) clean; `tsc --noEmit` clean.
- No new constructor signatures. The only new public names: `puzzle_images.BadReorder`,
  `media_tiling.MAX_CONCURRENT_TILING`, the retile route, and `canvasMath` exports.

## Test constraints (inlined — these gate acceptance)

- Runner: `timeout 180 pipenv run python -m pytest <paths> -q > /tmp/test.log 2>&1` — always
  `pipenv run python -m pytest`, always `timeout`, always redirect to a file, single
  statement (no `; echo` tail). Exit 124 = killed; read the log.
- Reuse the exact helpers/seams named per phase; invent none. Factories in `tests/conftest.py`
  are plain functions (`make_hunt()` etc.), NOT fixtures. `asyncio_mode = auto` — bare
  `async def test_*` works.
- Patch DAOs at the service's own import site (e.g. `patch.object(puzzle_images,
  "PuzzleImageJsonDb", ...)`), never `bot.store`.
- **Before writing any test, OPEN the fixture it uses** — the `web` fixture at the top of
  `tests/test_puzzle_images_web.py`, `tests/test_canvas_web.py`, and the
  `_call_attachment_bytes` helper in `tests/test_message_routes_visibility.py` — and use
  their exact attribute names. Do not guess mock attributes or payload keys; grep the route
  for the exact JSON keys it reads.
- NEVER write a loop that resolves state "until" a condition. If you think you need one,
  STOP and write `blocker.md`. (The semaphore test below uses a single bounded
  `asyncio.sleep`, not a poll loop.)
- Never modify test infrastructure or stub randomness unless a numbered step says to.
- A test file must define or import every helper it calls — no cross-file test helpers.
- Mock collections the code checks for truthiness must be set explicitly (`[]`), and
  anything awaited must be `AsyncMock`.
- When the change is about a header/rendered value, assert the exact value — and where
  relevant the absence of the wrong one.

## Phase 1 — write ALL the failing tests (Opus 4.8), commit, no production code

Confirm each new test fails for the RIGHT reason (a missing name/route/module or a failed
assertion on the new behaviour — not a fixture typo or import fatal). Then commit the tests
alone, e.g. `test: pin the canvas hardening tranche (failing)`.

### 1a. Reorder permutation guard — `tests/test_puzzle_images_service.py`

Reuse the file's existing `db` fixture and `_image` factory. `BadReorder` does not exist yet
— that AttributeError IS the right failure.

```python
def test_reorder_rejects_a_subset_of_the_puzzles_images(db):
    """A partial list would renumber only the listed rows, leaving duplicate
    positions with the unlisted ones — reject anything short of a permutation."""
    db.get_by_puzzle.return_value = [
        _image(1, position=0), _image(2, position=1), _image(3, position=2)]
    with pytest.raises(puzzle_images.BadReorder):
        puzzle_images.reorder(5, [3, 1])          # image 2 missing
    db.set_positions.assert_not_called()


def test_reorder_rejects_duplicate_ids(db):
    db.get_by_puzzle.return_value = [_image(1, position=0), _image(2, position=1)]
    with pytest.raises(puzzle_images.BadReorder):
        puzzle_images.reorder(5, [1, 1])          # dup, and image 2 missing
    db.set_positions.assert_not_called()
```

Note the existing `test_reorder_sets_positions_for_exactly_the_given_ids_in_order` already
passes a full permutation — it must stay green.

### 1b. Reorder route 400 — `tests/test_puzzle_images_web.py`

Mirror `test_reordering_with_a_foreign_id_is_a_404_not_a_500` (line ~132) exactly — same
`web` fixture, same request shape — but arrange a SUBSET of owned ids and assert
`resp.status_code == 400` and that `set_positions` was not called.

### 1c. nosniff on attachments — `tests/test_message_routes_visibility.py`

Extend the file's `_call_attachment_bytes` pattern. The current helper patches
`bot.web.api.send_file` returning `"RESP"`; for this test patch it with an AsyncMock
returning `MagicMock(headers={})` and capture the route's return value:

```python
async def test_attachment_bytes_sets_nosniff(tmp_path):
    resp = ...  # same arrangement as _call_attachment_bytes, but keep the returned response
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
```

Fails today because the route returns `await send_file(...)` directly and never touches
`.headers`.

### 1d. Tiling semaphore — `tests/test_media_tiling.py`

Reuse the file's `_media` factory and patch style (`patch.object(media_tiling, ...)`).
`MAX_CONCURRENT_TILING` does not exist yet — right reason.

```python
async def test_tiling_runs_at_most_the_cap_concurrently(tmp_path):
    """3 jobs, cap 2: the third pyramid must not start while two hold slots."""
    import asyncio
    started, release = [], threading.Event()

    def slow_build(src, dest):
        started.append(1)
        release.wait(timeout=10)
        return (100, 100, 1)

    db = MagicMock()
    with patch.object(media_tiling, "media_root", return_value=tmp_path), \
         patch.object(media_tiling, "build_pyramid", side_effect=slow_build):
        tasks = [asyncio.create_task(media_tiling.tile_media(_media(media_id=i), db))
                 for i in (1, 2, 3)]
        await asyncio.sleep(0.3)      # single bounded wait for the executor to spin up
        try:
            assert media_tiling.MAX_CONCURRENT_TILING == 2
            assert len(started) == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)
    assert len(started) == 3
    assert db.update_tiles.call_count == 3
```

### 1e. Retile route — `tests/test_canvas_web.py`

New class `TestCanvasRetile`, mirroring `TestCanvasOcrPreview` (line ~571): same `web`
fixture, same mock-arrangement style (open the fixture first and copy how it wires the
canvas + media DAOs). Route contract to pin:

- `POST /api/canvases/3/retile`, no body.
- Media `tile_status == "failed"` (or `"pending"`) → **202**, `web.mdb.update_tiles`
  called with `(media.id, "pending", 0)`, and the tiling task spawned — patch
  `bot.web.api._tile_and_push` with an `AsyncMock` and assert it was awaited/scheduled
  with the media row.
- Media `tile_status == "ready"` (and a second case `"none"`) → **409**
  `{"error": "not_retileable"}`, `update_tiles` NOT called.
- Unknown canvas id → 404 (the `_canvas_ctx` path — mirror how the existing tests get one).

### 1f. Vitest + canvasMath — frontend

1. `cd frontend && npm install -D vitest` and add `"test": "vitest run"` to
   `package.json` scripts. This is the ONE piece of infrastructure Phase 1 creates.
2. Write `frontend/src/lib/canvasMath.test.ts` importing from `./canvasMath` (which does
   not exist yet — the unresolved import is the right failure). Test these contracts,
   taken verbatim from today's inline code:

```ts
import { describe, expect, it } from "vitest";
import { clampScale, normPoint, tileRect, zoomAt } from "./canvasMath";

describe("zoomAt", () => {
  it("keeps the point under the anchor fixed on screen", () => {
    // screen = imagePoint * scale + offset. For any anchor, the image point
    // currently under it must map to the same screen position after the zoom.
    const r = zoomAt({ scale: 1, offset: { x: 10, y: 20 } }, 2, 100, 80);
    const imgX = (100 - 10) / 1;
    expect(imgX * r.scale + r.offset.x).toBeCloseTo(100);
    const imgY = (80 - 20) / 1;
    expect(imgY * r.scale + r.offset.y).toBeCloseTo(80);
  });
  it("clamps to [0.02, 4] and still anchors correctly at the clamp", () => {
    expect(zoomAt({ scale: 3, offset: { x: 0, y: 0 } }, 10, 0, 0).scale).toBe(4);
    expect(clampScale(0.0001)).toBe(0.02);
  });
});

describe("tileRect", () => {
  // The seam fix: a tile's far edge is computed from the same expression as
  // its neighbour's near edge — zero gap, zero overlap, at fractional scales.
  const scales = [511.9, 512, 512.7, 100.3, 33.34, 512.5, 700.001];
  it("adjacent tiles abut exactly at every fractional tile size", () => {
    for (const disp of scales)
      for (let x = 0; x < 40; x++) {
        const a = tileRect(x, 0, disp), b = tileRect(x + 1, 0, disp);
        expect(a.left + a.width).toBe(b.left);
      }
  });
});

describe("normPoint", () => {
  const rect = { left: 10, top: 20, width: 200, height: 100 };
  it("maps rect corners to 0 and 1", () => {
    expect(normPoint(10, 20, rect)).toEqual([0, 0]);
    expect(normPoint(210, 120, rect)).toEqual([1, 1]);
  });
  it("clamps outside points instead of going out of range", () => {
    expect(normPoint(0, 0, rect)).toEqual([0, 0]);
    expect(normPoint(999, 999, rect)).toEqual([1, 1]);
  });
  it("returns null for a zero-size rect", () => {
    expect(normPoint(5, 5, { left: 0, top: 0, width: 0, height: 0 })).toBeNull();
  });
});
```

### Phase 1 acceptance

```
timeout 180 pipenv run python -m pytest tests/test_puzzle_images_service.py tests/test_puzzle_images_web.py tests/test_message_routes_visibility.py tests/test_media_tiling.py tests/test_canvas_web.py -q > /tmp/p1.log 2>&1
```

Exit non-zero; the ONLY failures are the new tests, each for the reason named above
(missing `BadReorder` / missing header / missing `MAX_CONCURRENT_TILING` / 404-or-405 on
the missing route). Any import fatal or fixture error = wrong assumption in the test — fix
the test, do not scaffold.

```
cd frontend && timeout 120 npm test > /tmp/p1-fe.log 2>&1
```

Exit non-zero with a failed-to-resolve `./canvasMath` import (nothing else). Commit.

## Phase 2 — small backend fixes (Sonnet 4.6)

1. `bot/core/puzzle_images.py`: add `class BadReorder(Exception)` beside `ImageNotFound`.
   In `reorder`, after building `owned_ids`, keep the existing per-id `ImageNotFound`
   check for foreign ids, then add: if `len(ordered_ids) != len(owned_ids)` or
   `set(ordered_ids) != owned_ids`, raise `BadReorder("Reorder must list every image
   exactly once.")` before `set_positions`.
2. `bot/web/actions.py` `reorder_puzzle_images` (~2543): add
   `except puzzle_images.BadReorder as exc: return jsonify({"error": str(exc)}), 400`
   alongside the existing `ImageNotFound` handler.
3. `bot/web/api.py` `attachment_bytes`: capture `resp = await send_file(...)`, set
   `resp.headers["X-Content-Type-Options"] = "nosniff"`, return `resp`.
4. `bot/web/data.py`: verify with grep that `_get_connection` is called ONLY from `_run` /
   `_run_write`, and that those are invoked ONLY via `asyncio.to_thread` (lines ~89–99).
   Paste the grep output into the phase report. Then add one comment line above the
   `time.sleep` call: it runs on a `to_thread` worker, never the event loop. If the grep
   finds any other caller, STOP and write `blocker.md`.

Acceptance (new tests 1a–1c green, nothing else broken):

```
timeout 180 pipenv run python -m pytest tests/test_puzzle_images_service.py tests/test_puzzle_images_web.py tests/test_message_routes_visibility.py -q > /tmp/p2.log 2>&1
```

Commit.

## Phase 3 — tiling semaphore + retile route (Sonnet 4.6)

1. `bot/core/media_tiling.py`: add `MAX_CONCURRENT_TILING = 2` and a module-level
   `_tiling_slots = asyncio.Semaphore(MAX_CONCURRENT_TILING)` (safe at import in
   Py 3.12 — it binds a loop lazily). Hold it around ONLY the `run_in_executor` call
   (`async with _tiling_slots:`), inside the existing `try`. Add a short comment: each
   pyramid build peaks ~360 MB; two bounds worst-case memory while letting a small crop
   overlap a big sheet.
2. `bot/web/actions.py`: new route after `delete_canvas`:

```python
@actions_bp.route("/canvases/<int:canvas_id>/retile", methods=["POST"])
@login_required
async def retile_canvas(canvas_id):
    """Re-run tiling for a canvas stuck 'pending' (process died mid-build) or
    'failed'. Not organiser-gated — same bar as ocr-preview: a stuck canvas
    blocks everyone hunting, and retiling changes no content."""
    import asyncio
    from bot.store import MediaJsonDb
    ctx, canvas_row, err = await _canvas_ctx(canvas_id)
    if err:
        return err
    assert ctx is not None and canvas_row is not None
    media = MediaJsonDb.get_by_id(canvas_row.media_id)
    if media is None or media.tile_status not in ("pending", "failed"):
        return jsonify({"error": "not_retileable"}), 409
    MediaJsonDb.update_tiles(media.id, "pending", 0)
    from bot.web.api import _tile_and_push, _tiling_tasks
    task = asyncio.create_task(_tile_and_push(media, MediaJsonDb, ctx["hunt"]["id"]))
    _tiling_tasks.add(task)
    task.add_done_callback(_tiling_tasks.discard)
    await _push_board(ctx["hunt"]["id"])
    await _audit("retile_canvas", hunt_uid=ctx["hunt"]["uid"])
    return jsonify({"ok": True, "tile_status": "pending"}), 202
```

   Adjust names only if the Phase 1 tests (written against the real fixture) pinned
   different ones — the tests are the contract.

Acceptance:

```
timeout 180 pipenv run python -m pytest tests/test_media_tiling.py tests/test_canvas_web.py -q > /tmp/p3.log 2>&1
```

Commit.

## Phase 4 — frontend (Sonnet 4.6)

1. Create `frontend/src/lib/canvasMath.ts` with `clampScale`, `zoomAt`, `tileRect`,
   `normPoint` — the arithmetic lifted VERBATIM from `CanvasView.tsx` (`zoomAt` ~148, the
   `Math.round` snap ~424–441) and `MarkupCanvas.tsx` (`toNorm` ~146). Typed, pure, no DOM.
2. Rewire the three call sites to use them. `zoomAt` in the component keeps its
   setState shape — it calls the pure function to compute `{scale, offset}`; `toNorm`
   keeps its `getBoundingClientRect` read and passes the rect in. Behaviour must be
   identical; this is an extraction, not a redesign.
3. `cd frontend && timeout 120 npm test > /tmp/p4-fe.log 2>&1` — all canvasMath tests green.
4. useCallback fixes: in `EvidencePanel.tsx` destructure
   `const { mutateAsync: uploadAsync } = uploadMedia;` (and the same for
   `replaceClueImage`), use those in `onReplaceImage`'s body AND deps. Same pattern for
   `onAddImage` in `PuzzleImages.tsx` (`uploadMedia`, `attach`). react-query's
   `mutateAsync` is referentially stable; the mutation object is not.
5. Retile button: add a `useRetileCanvas` mutation in `frontend/src/api.ts` next to the
   other canvas mutations (POST `/api/canvases/${id}/retile`, invalidate the canvases
   query on success). In `CanvasView.tsx` render a small "Regenerate tiles" `Button`
   (match the file's existing Mantine usage) in BOTH: the `tile_status === "pending"`
   branch (~295, under the loader — this is the stuck case) and the failed-fallback
   region (~447) when `tile_status === "failed"`. On error, `notifyError` like the file's
   other mutations.
6. `cd frontend && timeout 240 npm run build > /tmp/p4-build.log 2>&1` (runs `tsc --noEmit`
   first) — clean.

Steps 4–5 have no test runner coverage (component layer): they are typecheck +
inspection only — say so in the phase report. Commit.

## Phase 5 — full gates and merge

```
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/p5.log 2>&1
timeout 120 pipenv run ruff check > /tmp/p5-ruff.log 2>&1
timeout 240 pipenv run basedpyright bot/ > /tmp/p5-pyright.log 2>&1
cd frontend && timeout 120 npm test > /tmp/p5-fe.log 2>&1
cd frontend && timeout 240 npm run build > /tmp/p5-build.log 2>&1
```

All clean (suite was 1508 passing before this tranche; expect that plus the new tests).
**Mutation-prove three guards** (break, watch the exact test go red, restore by editing
back — never `git checkout --`): (a) the permutation guard — delete the `BadReorder`
raise; (b) the semaphore — make `tile_media` skip `async with _tiling_slots`; (c) the
tile seam — reintroduce `Math.round(tileDisp)`-style independent width in `tileRect`.
Then update `.ai/active/changed-files.md` + `session-summary.md`, merge `working-2` into
`staging` (`git checkout staging && git merge working-2` in the main checkout), and report
per-phase commit hashes. Do NOT touch `main`.

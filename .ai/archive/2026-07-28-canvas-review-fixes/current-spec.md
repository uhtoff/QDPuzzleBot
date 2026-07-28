# Current Spec

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Target models: Phase 1 (tests) — Opus 4.8; Phases 2–3 — Sonnet 4.6
Started: 2026-07-28
Status: ready for handoff — all 3 phases complete @ 98c1f99. Final verification: full suite
1513 passed, ruff clean, basedpyright clean (0/0/0), frontend tsc --noEmit clean, npm run
build clean. Frontend fixes are typecheck-verified only — no browser pass done yet (see
session-summary.md for the exact manual checks still needed).

## Before you start (executing model)

- Execute ONLY the current phase. Do not read ahead and start implementing during Phase 1.
- On any unexpected failure, invoke `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste
  the acceptance command output into `.ai/active/session-summary.md`.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` describing what you saw and STOP.
- Commit at each phase boundary (`git status` first; do not sweep in unrelated files).

## Goal

Fix the six verified bugs found by the 2026-07-28 review of the canvas epic
(commit range `dfe1fb5^..HEAD`). Three backend (test-first), three frontend
(no test runner exists — inspection + typecheck only).

## In scope

1. `!clue` in a non-hunt channel crashes (`bot/cogs/puzzles.py` `clue()`).
2. `POST /api/evidence` accepts a `media_id` belonging to another hunt
   (`bot/web/api.py` `add_evidence`, ~line 534).
3. `bot/core/canvas.py` `update_region` passes the *unresolved* `media_id` to
   `puzzle_images.sync_region_image` (line ~122) while the DB write two lines up
   correctly resolves `existing.crop_media_id if media_id is None else media_id`.
4. One paste can fire two upload mutations: `PuzzleImages.tsx` document-level paste
   listener (~:159-167) does not bail when a more specific handler already consumed
   the event (`EvidencePanel.tsx` per-cell `onPaste` at ~:523).
5. `<CanvasView>` is not re-keyed per canvas (`CanvasPanel.tsx` ~:138), so its
   never-reset `fitted` ref (`CanvasView.tsx` ~:96) keeps the previous canvas's
   zoom/offset/drawing state when the user switches canvases.
6. `MarkupCanvas.tsx` swallows every mutation failure: `void commitStroke(...)` /
   `void commitText()` with no catch; `deleteMark.mutate` with no `onError`. Failures
   must surface via `notifyError` from `frontend/src/util.ts` like the rest of the app.

## Out of scope — do NOT do these

- Any of the duplication extractions in the review backlog (`session-summary.md`):
  `_load_visible_media`, `validate_image_bytes`, media `ingest()`, `useZoomPan`,
  tint/statusColor merge, conftest web-fixture factory, DAO base helpers. Separate task.
- `!clues` — verified safe (it requires a puzzle channel, which implies a hunt).
- Whether `_log_informs_evidence` should also receive the resolved crop id in
  `update_region` — recorded as an open question, not part of bug 3's fix.
- Any rename, refactor, or formatting change. Touch nothing outside the files listed.

## Relevant files

- `bot/cogs/puzzles.py` — `clue()` ~1617-1641; `canvas()` ~1378-1390 (read only, wording model)
- `bot/web/api.py` — `add_evidence` ~534-590
- `bot/web/actions.py` — `update_evidence` guard ~2050-2057 (read only — the guard to mirror)
- `bot/core/canvas.py` — `update_region` ~95-125
- `tests/test_evidence_commands.py`, `tests/test_add_evidence_web.py`, `tests/test_canvas_service.py`
- `tests/conftest.py` (read only — `make_cog`, `make_ctx` factories)
- `frontend/src/components/PuzzleImages.tsx`, `EvidencePanel.tsx`, `CanvasPanel.tsx`,
  `MarkupCanvas.tsx`; `frontend/src/util.ts` (read only — `notifyError`)

## Constraints

- Backend fixes are TDD: Phase 1 lands ALL failing tests as their own commit before any
  production code.
- Do not change any existing test. Do not touch `tests/conftest.py`.
- No new dependencies, no migrations, no route-shape changes beyond the one new 400 case.
- Frontend has NO test runner. The gate is `npx tsc --noEmit` + `npm run build` only —
  say so plainly in the final report; do not imply behavioural verification.

## Test constraints (inline — do not skip)

- Runner, always this exact single-statement shape (exit 124 = killed by timeout; read the log):
  `timeout 120 pipenv run python -m pytest tests/test_canvas_service.py -q > /tmp/test.log 2>&1`
  Bare `pytest`/`python` are not on PATH and don't put the repo root on `sys.path`.
- `pytest.ini` sets `asyncio_mode = auto` — `async def test_*` needs NO decorator.
  (`test_add_evidence_web.py` carries redundant `@pytest.mark.asyncio` markers; do not add more.)
- Reuse the exact factories: `make_cog`, `make_ctx` from `tests/conftest.py` (plain functions,
  NOT fixtures) and the existing `web` fixture / `dbs` fixture in the target files. Invent no
  harness machinery.
- **`make_cog(hunt=None)` does NOT give you a None hunt** — it returns `hunt or MagicMock()`.
  Override after construction: `cog.get_hunt = lambda ctx: None`.
- **Set collection attrs explicitly**: an unset `ctx.message.attachments` auto-vivifies to a
  truthy Mock and silently takes the has-attachment branch. Set `[]` (or a real list).
- Patch DAOs where they are LOOKED UP: `add_evidence` imports `MediaJsonDb` function-locally,
  so `patch("bot.store.MediaJsonDb", mdb)` engages. `canvas.py` binds names at module load,
  so the existing `dbs` fixture's `patch.object(canvas, ...)` is the seam — use it.
- Anything awaited is an `AsyncMock`; assert with `assert_awaited_once_with(...)` /
  `assert_not_called()`.
- NEVER write a loop that resolves state "until" a condition. NEVER modify test
  infrastructure. If you think you need either, STOP and write `blocker.md`.

## Phase 1 — write ALL failing tests (Opus 4.8), commit, no production code

### 1a. `tests/test_evidence_commands.py` — append a new class

```python
class TestClueOutsideHunt:
    """!clue outside any hunt channel: friendly refusal, never a crash (review 2026-07-28)."""

    async def test_clue_outside_a_hunt_sends_a_friendly_error(self):
        cog = make_cog()
        cog.get_hunt = lambda ctx: None   # make_cog would return MagicMock(); force no-hunt
        ctx = make_ctx()
        ctx.message.attachments = []      # unset Mock attrs are truthy — must be a real []
        cog.service.log_clues = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="some note")

        cog.service.log_clues.assert_not_called()
        msg = ctx.send.call_args[0][0]
        assert "hunt" in msg.lower()

    async def test_clue_with_attachment_outside_a_hunt_does_not_crash(self):
        cog = make_cog()
        cog.get_hunt = lambda ctx: None
        ctx = make_ctx()
        att = MagicMock()
        att.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"x" * 16)
        ctx.message.attachments = [att]
        cog.service.log_clues = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="")

        cog.service.log_clues.assert_not_called()
        assert "hunt" in ctx.send.call_args[0][0].lower()
```

Imports already present in the file (`make_cog`, `make_ctx`, `AsyncMock`, `MagicMock`, `Puzzles`);
add any that are missing at the top, none elsewhere.

**Expected failure modes (both are the RIGHT reason):** the no-attachment test fails on
`log_clues.assert_not_called()` (the mocked service masks the crash and the command "succeeds");
the attachment test fails with `AttributeError: 'NoneType' object has no attribute 'id'` raised
from `_store_clue_attachment` — that fatal IS the bug under test, not a wrong test assumption.
Any OTHER fatal (import error, fixture error) means the test is wrong — fix the test.

### 1b. `tests/test_add_evidence_web.py` — append

```python
@pytest.mark.asyncio
async def test_a_media_id_from_another_hunt_is_refused(web):
    """Create must hold the same invariant update_evidence documents: an item
    may never cite another hunt's private upload. (Review 2026-07-28.)"""
    mdb = MagicMock()
    mdb.get_by_id.return_value = SimpleNamespace(id=55, hunt_id=999)  # ctx hunt is id 10

    with patch("bot.store.MediaJsonDb", mdb):
        resp = await web.client.post(
            "/api/evidence",
            json={"hunt_uid": "U1", "notes": ["a clue"], "media_id": 55},
        )

    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "cross_hunt_media"
    web.cog.service.log_clues.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_media_id_is_refused(web):
    mdb = MagicMock()
    mdb.get_by_id.return_value = None

    with patch("bot.store.MediaJsonDb", mdb):
        resp = await web.client.post(
            "/api/evidence",
            json={"hunt_uid": "U1", "notes": ["a clue"], "media_id": 55},
        )

    assert resp.status_code == 400
    web.cog.service.log_clues.assert_not_awaited()
```

Uses the file's existing `web` fixture and `_hunt()` unchanged. The route currently passes
`media_id` straight to `log_clues`, so both tests fail on the status assertion (200-family
vs 400) — the right reason.

### 1c. `tests/test_canvas_service.py` — append (uses the existing `dbs` fixture)

```python
def test_a_suppressed_recrop_syncs_the_existing_crop(dbs):
    """The DB write resolves crop_media_id = existing when media_id is None;
    sync_region_image must receive that SAME resolved id. Otherwise a relinked
    locates region whose re-crop was suppressed (ink guard, or a failed crop)
    is deleted from the old puzzle's strip and never recreated on the new one,
    while its crop still exists in the DB. (Review 2026-07-28.)"""
    regions, evidence, images = dbs
    existing = _region(puzzle_id=9, role="locates")
    existing.crop_media_id = 42
    regions.get_by_id.return_value = existing

    canvas.update_region(region_id=5, hunt_id=1, shape=RECT,
                         puzzle_id=11, role="locates")   # relink 9 -> 11, no new crop

    images.sync_region_image.assert_called_once_with(5, 11, "locates", 42)
    assert regions.update.call_args.kwargs["crop_media_id"] == 42
```

Confirm `CanvasRegionData` declares `crop_media_id` before writing (grep
`bot/store/canvas_data.py`) — do not guess field names. Fails on the
`sync_region_image` args (`None` where `42` is expected) — the right reason.

### Phase 1 acceptance

Each file run individually with the runner shape above must exit non-zero, and `/tmp/test.log`
must show ONLY the failures described (5 new tests failing for the stated reasons, zero
errors in collection, zero pre-existing tests failing). Then commit the tests alone:
`git commit -m "test: pin the three backend canvas-review bugs (failing)"`.

## Phase 2 — backend fixes (Sonnet 4.6)

1. `bot/cogs/puzzles.py`, `clue()` (~1626): immediately after `hunt = self.get_hunt(ctx)` add:

   ```python
   if hunt is None:
       return await ctx.send(
           ":x: This channel isn't part of a hunt, so clues can't be logged here.")
   ```

   (Same shape and tone as `canvas()`'s guard at ~1387. Do not touch `_store_clue_attachment`.)

2. `bot/web/api.py`, `add_evidence`: after the `hunt["archived"]` check and before the
   source-exclusivity check, add the guard, mirroring `actions.py:2050-2057` exactly
   (same error key, same 400):

   ```python
   # The id comes from the client; without this an item could cite another
   # hunt's private upload. Same invariant update_evidence already holds.
   if media_id is not None:
       from bot.store import MediaJsonDb
       media = MediaJsonDb.get_by_id(media_id)
       if media is None or media.hunt_id != hunt["id"]:
           return jsonify({"error": "cross_hunt_media"}), 400
   ```

3. `bot/core/canvas.py`, `update_region`: hoist the already-written resolution into a local
   and use it in BOTH places:

   ```python
   effective_crop = existing.crop_media_id if media_id is None else media_id
   ```

   — pass `crop_media_id=effective_crop` to `CanvasRegionJsonDb.update(...)` (replacing the
   inline conditional) and `effective_crop` as `sync_region_image`'s last argument (replacing
   `media_id`). Leave `_log_informs_evidence(..., media_id=media_id)` UNCHANGED (out of scope).
   Keep the existing explanatory comment.

### Phase 2 acceptance (all must pass, in this order)

```
timeout 120 pipenv run python -m pytest tests/test_evidence_commands.py tests/test_add_evidence_web.py tests/test_canvas_service.py -q > /tmp/test.log 2>&1
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
```

Full suite baseline is 1508 passed; expect 1513. Commit:
`git commit -m "fix: !clue hunt guard, cross-hunt media on evidence create, resolved crop id to sync"`.

## Phase 3 — frontend fixes (Sonnet 4.6)

4. `frontend/src/components/PuzzleImages.tsx` (~:159): first line of the document-level
   `onPaste` handler: `if (e.defaultPrevented) return;`. Then open
   `frontend/src/components/EvidencePanel.tsx` (~:523) and confirm the per-cell `onPaste`
   calls `e.preventDefault()` when it consumes an image — if it does not, add it there
   (and in any other paste handler that consumes the image, e.g. the clue-box `onCluePaste`
   at ~:330 — check, don't assume).
5. `frontend/src/components/CanvasPanel.tsx` (~:138): add `key={canvas.id}` to `<CanvasView …>`.
6. `frontend/src/components/MarkupCanvas.tsx`: import `notifyError` from `../util`. Wrap the
   bodies of `commitStroke` and `commitText`'s `mutateAsync` awaits in `try/catch` calling
   `notifyError(e)`; give every `deleteMark.mutate(...)` call an
   `{ onError: (e) => notifyError(e) }` (match `notifyError`'s actual signature in `util.ts` —
   read it first). Do NOT restructure the component; the frame→shrink-wrap→img+svg structure
   and `toNorm` are load-bearing (see the component's own comments).

### Phase 3 acceptance

```
cd frontend && npx tsc --noEmit && npm run build
```

Both clean. Commit: `git commit -m "fix(frontend): paste double-fire, canvas re-key, surface markup save failures"`.
Report explicitly: these three fixes are typecheck-verified only; behavioural confirmation
needs the next browser pass (paste into drawer+evidence cell, switch canvases, draw on an
archived hunt).

## Verification (final)

Full suite + ruff + basedpyright + tsc + build, as above. Update
`.ai/active/session-summary.md` Status and paste the outputs.

## Rollback / recovery notes

Three independent backend fixes, one commit each phase — `git revert` any one cleanly.
No migrations, no data changes.

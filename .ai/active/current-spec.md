# Current Spec — canvas-review backlog: ingest bugs + duplication extractions

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Target model per phase: Phase 1 (tests) = **Opus 4.8**; Phases 2–8 = **Sonnet 4.6**
Started: 2026-07-28
Status: ready for handoff — Phase 1 (tests)

## Before you start (executing model)

- Execute **only the current phase**. Commit at each phase boundary.
- On any unexpected failure, invoke `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and paste
  the acceptance command output into your phase report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear understood fix,
  write `.ai/active/blocker.md` (what ran, what you saw, best hypothesis) and STOP.
- Work happens on branch `working-2` in this worktree. At the end merge `working-2` into
  `staging` (NOT `main` — main is pulled from staging later for live). Never commit to main.
  Do NOT push anything — the user is holding pushes back deliberately.

## Goal

1. Fix two real bugs hiding in duplicated media code:
   - `!clue` image attachments are stored **without dimensions and never tiled** (report 0×0),
     because the cog re-implements upload ingestion minus the probe/tiling steps.
   - The OCR-preview route re-implements upload validation and has already drifted: it checks
     only `sniff_image_type(...) is None` and ignores `ALLOWED_IMAGE_TYPES`.
2. Execute the duplication-extraction backlog from
   `.ai/archive/2026-07-28-canvas-review-fixes/session-summary.md` (backend, frontend, tests) —
   the **major items only**; the "smaller" grab-bag stays in the backlog.

## In scope

- `bot/core/media_store.py`: extract `validate_image_bytes()`.
- New `bot/core/media_ingest.py`: shared `ingest_image_bytes()`; `schedule_tiling()` moves the
  strong-ref task idiom into `bot/core/media_tiling.py`.
- Rewire the three ingest copies: `bot/web/api.py upload_media`, `bot/web/actions.py
  _crop_region_media`, `bot/cogs/puzzles.py _store_clue_attachment`.
- `bot/web/api.py`: shared `_visible_media()` helper for `media_bytes`/`media_tile`/`media_markup`.
- `bot/web/`: shared cross-hunt media guard `_require_hunt_media()` (4 copies today).
- `bot/web/actions.py`: replace `_push_board` with the pre-existing `_notify_board`.
- `bot/store/mysqldb.py`: `_exec`/`_fetch_one`/`_fetch_all` helpers; collapse the delete()
  overrides that exist only to call `self.mydb.commit()`.
- Frontend: `useZoomPan` hook extraction; `statusTone()` merge of `util.statusColor` +
  `CanvasView.tintFor`; `apiUpload()` in `api.ts`; shared OCR "replace notes?" flow.
- Tests: one conftest factory for the copy-pasted Quart `web` fixture (19 files);
  `try/finally` around patch cleanup in `tests/test_media_web.py` / `tests/test_markup_web.py`.

## Out of scope — do NOT do these

- The backlog's "smaller" frontend items: `puzzlesById/puzzleOptions` util, `<SheetList>`,
  `useElementSize`, `pointercancel`, drag threshold, zoom-button anchoring, `TINT_LEGEND`
  export tidy, `RegionLink` type splitting. They stay in the backlog.
- The "one error convention" item (`setErr` inline Alerts → `notifyError`): **needs a UX
  decision** (inline-in-modal vs toast) — leave it.
- The smaller test items (SimpleNamespace yields, redundant `@pytest.mark.asyncio`,
  `assert status in (403, 404)` pin) — fine to fix ONLY inside files you already rewrite for
  the conftest factory, otherwise leave.
- No schema/migration changes. No changes to `MarkupCanvas`'s DOM structure (frame →
  shrink-wrap → img+svg; its comments explain why). No new `web_*` twins in the cog.
- `bot/cogs/puzzles_gsheet.py`, `bot/utils/gsheets.py`, `migrations/`.
- **The base `delete()` "missing commit" is NOT a bug** — the connection sets
  `autocommit=True` (`bot/store/__init__.py:65`). Do not add commit calls to the base;
  Phase 5 *removes* the redundant ones.

## Relevant files

Write:
- `bot/core/media_store.py`, `bot/core/media_tiling.py`, new `bot/core/media_ingest.py`
- `bot/web/api.py`, `bot/web/actions.py`, `bot/cogs/puzzles.py`, `bot/store/mysqldb.py`
- `frontend/src/util.ts`, `frontend/src/api.ts`, new `frontend/src/hooks/useZoomPan.ts`,
  new `frontend/src/lib/statusTone.ts` (+ `.test.ts`),
  `frontend/src/components/{CanvasView,MarkupCanvas,EvidencePanel,RegionModal}.tsx`
- `tests/conftest.py` (factory ADDITION only), new `tests/test_media_ingest.py`,
  `tests/test_evidence_commands.py`, `tests/test_canvas_web.py`, and the 19 `web`-fixture files
  (list in Phase 7)

Read only:
- `bot/core/image_tiler.py` (`probe_dimensions`, `needs_tiling`), `bot/core/message_media.py`
  (`media_root`), `bot/store/__init__.py`, `bot/store/media_data.py`, `bot/web/notifier.py`,
  `docs/agents/python.md`, `AGENTS.md`

## Constraints

- Do not touch files outside the list above.
- Pure code-motion phases (4–7) must be behaviour-neutral; the existing green suite is their
  guard. Do not weaken any existing assertion. Any change to a pre-existing test needs a
  stated reason in the phase report (there are three planned ones, named below).
- If you change a function signature, grep every call site and list them in the report.
- `bot/` must stay basedpyright-clean (`standard` mode, 0 errors).
- Commit messages: one per phase, conventional style (`fix:`/`refactor:`/`test:`).

## Test constraints (inline — read before Phase 1)

- **Runner:** `timeout 180 pipenv run python -m pytest <files> -q > /tmp/test.log 2>&1` —
  single statement, no `; echo` tail. Exit 124 = killed; read the log. Bare `pytest` breaks
  `sys.path`.
- `pytest.ini` sets `asyncio_mode = auto` — `async def test_*` needs no decorator.
- **Factories, not fixtures:** `tests/conftest.py` exports plain helpers `make_hunt()`,
  `make_round()`, `make_puzzle()`, `make_ctx()`, `make_cog()` — import and call them.
- **Patch where the name is looked up**, not where it is defined (`docs/agents/python.md`).
  The cog does `from bot.core.media_ingest import ...` → patch `bot.cogs.puzzles.<name>`.
- Anything awaited is `AsyncMock`; set `.name`/`.id`/collection attrs explicitly
  (`attachments = []`, not left to auto-vivify).
- Reuse exact helpers/seams; invent none. Mirror the invocation idiom of the neighbouring
  tests in the same class (e.g. `TestClueWithAttachment` shows how to call the `clue`
  command and shape `ctx.message.attachments`).
- NEVER write a loop that resolves state "until" a condition. If you think you need one,
  STOP and write `blocker.md`.
- NEVER modify test infrastructure or stub randomness unless a numbered step says to.
- Confirm every column/attribute/payload key exists before use — `bot/store/media_data.py`
  is the `media` row shape (`width`, `height`, `tile_status`, `tile_levels`).
- A test file must define or import every helper it calls — no cross-file top-level helpers.
- Where a property is a relationship (a width/height pair, a mapping), use two distinct
  values so a transposition fails the test — e.g. `(120, 80)`, never `(100, 100)`.
- When the change is about what text renders/returns, assert the string — and the absence of
  the wrong one.

---

## Phase 1 — write ALL the failing tests (Opus 4.8). No production code.

Commit the red tests before Phase 2. Acceptance: each new test fails for the RIGHT reason —
a missing function/attribute (`ImportError`/`AttributeError` on `validate_image_bytes`,
`bot.core.media_ingest`, `bot.cogs.puzzles.ingest_image_bytes`) or a wrong-value assertion —
NOT a fixture/bootstrap fatal. Run each new file individually AND together with the plain
runner above.

### 1a. New `tests/test_media_ingest.py`

```python
"""validate_image_bytes + ingest_image_bytes (Phase 1 red: neither exists yet)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.core.media_store import MediaRejected

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class TestValidateImageBytes:
    def test_oversize_is_rejected(self):
        from bot.core.media_store import validate_image_bytes
        with patch("bot.core.media_store.max_upload_bytes", return_value=10):
            with pytest.raises(MediaRejected):
                validate_image_bytes(b"x" * 11)

    def test_unknown_bytes_are_rejected(self):
        from bot.core.media_store import validate_image_bytes
        with pytest.raises(MediaRejected):
            validate_image_bytes(b"definitely not an image")

    def test_type_outside_allowlist_is_rejected(self):
        # sniff says PNG, but the allowlist no longer contains it — the check
        # ocr-preview currently skips. Patch works because validate reads the
        # module global at call time.
        from bot.core.media_store import validate_image_bytes
        with patch("bot.core.media_store.ALLOWED_IMAGE_TYPES", ("image/gif",)):
            with pytest.raises(MediaRejected):
                validate_image_bytes(PNG)

    def test_valid_png_returns_its_type(self):
        from bot.core.media_store import validate_image_bytes
        assert validate_image_bytes(PNG) == "image/png"


class TestIngestImageBytes:
    def _db(self):
        db = MagicMock()
        db.create.side_effect = lambda **kw: SimpleNamespace(id=1, **kw)
        return db

    def _patches(self, tmp_path, dims, tiled):
        return [
            patch("bot.core.media_store.media_root", return_value=tmp_path),
            patch("bot.core.media_ingest.media_root", return_value=tmp_path),
            patch("bot.core.media_ingest.probe_dimensions", return_value=dims),
            patch("bot.core.media_ingest.needs_tiling", return_value=tiled),
        ]

    def test_small_image_gets_dimensions_and_no_pyramid(self, tmp_path):
        from bot.core.media_ingest import ingest_image_bytes
        db = self._db()
        ps = self._patches(tmp_path, dims=(120, 80), tiled=False)
        try:
            for p in ps: p.start()
            ingest_image_bytes(hunt_id=10, filename="a.png", raw=PNG,
                               origin="discord", db=db)
        finally:
            for p in ps: p.stop()
        kw = db.create.call_args.kwargs
        assert (kw["width"], kw["height"]) == (120, 80)   # distinct: catches transposition
        assert kw["tile_status"] == "none"
        assert kw["origin"] == "discord"

    def test_large_image_is_marked_pending(self, tmp_path):
        # as above with tiled=True → kw["tile_status"] == "pending"
        ...

    def test_unprobeable_image_keeps_zero_dims_untiled(self, tmp_path):
        # probe_dimensions patch: side_effect=RuntimeError("boom")
        # → (0, 0), tile_status "none", upload kept (db.create still called)
        ...

    def test_rejection_propagates_and_creates_no_row(self, tmp_path):
        # raw=b"junk" → pytest.raises(MediaRejected); db.create.assert_not_called()
        ...
```

Fill the `...` bodies following the first test's shape exactly.

### 1b. `tests/test_evidence_commands.py` — extend `TestClueWithAttachment`

Mirror the class's existing tests for cog construction, ctx/attachment shape, and command
invocation. Two new tests (red today with `AttributeError: bot.cogs.puzzles` has no
`ingest_image_bytes`):

```python
async def test_attachment_is_ingested_with_dimensions(self):
    # patch bot.cogs.puzzles.ingest_image_bytes → SimpleNamespace(id=7, tile_status="none")
    # patch bot.cogs.puzzles.schedule_tiling
    # invoke !clue with one attachment (mirror test_stores_an_attached_image_and_links_it)
    # assert ingest called once, kwargs origin == "discord", hunt_id == the hunt's id
    # assert schedule_tiling NOT called
    # assert log_clues received media_id=7 (same assertion the neighbour test makes)

async def test_attachment_needing_tiling_is_scheduled(self):
    # ingest returns SimpleNamespace(id=7, tile_status="pending")
    # assert schedule_tiling called once, with that media object
```

Note: the class's four EXISTING tests patch `bot.cogs.puzzles.save_image_bytes` /
`bot.cogs.puzzles.MediaJsonDb`. **Do not change them in Phase 1.** Phase 3 migrates their
patch targets (that is planned pre-existing-test change #1; reason: the cog's seam moves
from save+create to ingest).

### 1c. `tests/test_canvas_web.py` — extend `TestCanvasOcrPreview` (line ~571)

Mirror the class's existing fixture/client usage and multipart POST shape. One new test,
red today (route ignores the allowlist):

```python
async def test_type_outside_allowlist_is_refused(self, ...):
    # patch bot.core.media_store.ALLOWED_IMAGE_TYPES to ("image/gif",)
    # POST a valid PNG body → expect 400, and the response text mentions
    # "Not a supported image" (assert the string, not just the status)
```

### 1d. New `frontend/src/lib/statusTone.test.ts`

`statusTone(status: string | null)` returns one of
`"solved" | "stuck" | "working" | "unstarted" | "unknown"`. Semantics = the union of
`frontend/src/util.ts statusColor` (:148) and `CanvasView.tintFor` (:16), which have
re-diverged — read both first. Pin at minimum:

- `statusTone("Unsolved") !== "solved"` — the twice-fixed substring regression (green badge).
- `statusTone("backsolved") === "solved"` and `statusTone("Solved") === "solved"` (case-insens).
- `"In Progress"` and `"needs extraction"` → `"working"` (tintFor's `needs` rule wins — this
  deliberately changes statusColor: "needs …" badges go blue→yellow).
- `"unstarted"` → `"unstarted"`; `""`/`null`/`"weird"` → `"unknown"`.

Run: `cd frontend && npx vitest run` (red: module doesn't exist).

### Phase 1 acceptance

```
timeout 180 pipenv run python -m pytest tests/test_media_ingest.py tests/test_evidence_commands.py tests/test_canvas_web.py -q > /tmp/test.log 2>&1
```
Expect: ONLY the new tests fail, each with a missing-name error or wrong-value assertion.
Frontend: `npx vitest run` fails only on `statusTone.test.ts`. Commit: `test: pin the ingest/dedup tranche (failing)`.

---

## Phase 2 — `validate_image_bytes` + OCR route (bug fix)

1. `bot/core/media_store.py`: extract from `save_image_bytes` (lines 85–92) a function
   `validate_image_bytes(raw: bytes) -> str` — size check (`max_upload_bytes()`), sniff,
   allowlist check; raises `MediaRejected` with the EXISTING message strings verbatim;
   returns the content type. `save_image_bytes` calls it.
2. `bot/web/api.py ocr_evidence_preview` (~line 406): replace the hand-rolled size+sniff
   block with `try: validate_image_bytes(raw) except MediaRejected as exc: return
   jsonify({"error": str(exc)}), 400`. Keep the `max_upload_bytes() + 1` read.

Acceptance: 1a + 1c tests green; full suite green (command in Phase 8, scoped run first:
`tests/test_media_ingest.py tests/test_canvas_web.py tests/test_media_web.py tests/test_add_evidence_web.py`).
Commit: `fix: single image-validation seam; ocr-preview honours the allowlist`.

## Phase 3 — shared ingest + `schedule_tiling` (bug fix: !clue images 0×0/never tiled)

1. `bot/core/media_tiling.py`: add module-level `_tiling_tasks: set = set()` and
   `def schedule_tiling(media, db, after=None)` — `asyncio.create_task` over a coroutine that
   awaits `tile_media(media, db)` then, if `after` is not None, awaits `after()`; strong-ref
   idiom exactly as `bot/web/api.py:30-36` (add task to the set, discard on done). Move that
   comment block with it.
2. New `bot/core/media_ingest.py`: `def ingest_image_bytes(hunt_id, filename, raw, origin, db)`
   — `save_image_bytes` → `probe_dimensions(media_root() / saved["path"])` (except → log +
   `(0, 0)`, exactly the fallback comment in `upload_media` api.py:378-385) → `needs_tiling`
   → `db.create(...)` with the full kwargs set `upload_media` passes today (api.py:388-392),
   returning the media row. Imports: `probe_dimensions`, `needs_tiling` from
   `bot.core.image_tiler`; `media_root` from `bot.core.message_media`; `save_image_bytes`
   from `bot.core.media_store`. Quart-free and discord-free, like media_store.
3. Rewire the three copies:
   - `bot/web/api.py upload_media` (~341): body becomes visibility/file checks +
     `ingest_image_bytes(...)` + if `media.tile_status == "pending"`:
     `schedule_tiling(media, MediaJsonDb, after=lambda-free coroutine funcpartial pushing
     _push_board(hunt["id"]))` — use `functools.partial` or a small local `async def`,
     NOT a bare lambda returning a coroutine you never await. Delete `_tile_and_push` and
     api.py's `_tiling_tasks` (now owned by media_tiling). Response JSON unchanged.
   - `bot/cogs/puzzles.py _store_clue_attachment` (~1590): replace save+create with
     `ingest_image_bytes(..., origin="discord", db=MediaJsonDb)`; if
     `media.tile_status == "pending"`, `schedule_tiling(media, MediaJsonDb,
     after=functools.partial(self.service.notifier.board_changed, hunt))`.
   - `bot/web/actions.py _crop_region_media` (~2180): replace its save+create with
     `ingest_image_bytes(..., origin="canvas_crop", db=MediaJsonDb)` — dimensions now come
     from the probe (drop the explicit width/height/tile_status kwargs); if pending,
     `schedule_tiling(..., after=partial(_push_board, media.hunt_id))`. Behaviour change,
     deliberate: an oversize region crop now gets a pyramid instead of tile_status "none".
4. Migrate `TestClueWithAttachment`'s four existing tests' patch targets
   (`save_image_bytes`/`MediaJsonDb` → `ingest_image_bytes` at `bot.cogs.puzzles`), keeping
   every assertion's strength (planned change #1). Check `tests/test_canvas_web.py` /
   `tests/test_media_web.py` for patches on `bot.web.api._tile_and_push` or
   `bot.web.api._tiling_tasks` and migrate those the same way (state each in the report).

Acceptance: 1a + 1b green; then
`timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` fully green.
Commit: `fix: one media-ingest seam — !clue images get dimensions and tile`.

## Phase 4 — web-layer dedup (behaviour-neutral except one error key)

1. `bot/web/api.py`: extract `_visible_media(media_id)` → `(media, err)` from the identical
   media→hunt→visibility chains in `media_bytes` (:225), `media_tile` (:268), `media_markup`
   (:320) — mirror the shape of `actions._media_ctx` (:2154), which is the good version, but
   note the api.py copies must keep their exact current status codes/keys. All three call it.
2. Cross-hunt guard: add `_require_hunt_media(media_id, hunt_id)` → `(media, err)` in
   `bot/web/actions.py`; use at actions.py:2055 (`update_evidence`), :2259 (`create_canvas`),
   :2554 (`add puzzle image`), and api.py:587 (`add_evidence`) — api.py imports it or gets a
   local twin ONLY if a circular import forces it (say which in the report). Unify the error
   key to `cross_hunt_media`: `create_canvas` currently returns `unknown_media` — update
   `tests/test_canvas_web.py:116` to expect `cross_hunt_media` (planned change #2; reason:
   key unification, the guard is the same check).
3. Replace `_push_board(hunt_id)` (actions.py:2226) with the pre-existing
   `_notify_board(uid, guild_snowflake)` (:73) at every `_push_board` call site — each site's
   ctx already holds `ctx["hunt"]["uid"]` and `ctx["guild_snowflake"]`
   (see `_resolve_visible_hunt` :94). Delete `_push_board`. Grep tests for patches on
   `bot.web.actions._push_board` first and migrate them assertion-for-assertion (planned
   change #3). The event payload is identical (`_board_event` vs `WebNotifier.board_changed`
   — both publish `{"type": "board", "uid", "guild_id": snowflake}`), so no frontend change.
   EXCEPTION: Phase 3 gave `schedule_tiling`'s `after=` a `_push_board` partial — use
   `_notify_board` there too; the ingest call sites hold ctx.

Acceptance: full pytest green; `pipenv run basedpyright bot/` 0 errors.
Commit: `refactor(web): shared media visibility + cross-hunt guard, drop _push_board`.

## Phase 5 — `_MySQLBaseDb` helpers (behaviour-neutral; NO test coverage — say so)

1. Add to `_MySQLBaseDb` (`bot/store/mysqldb.py:29`):
   `_exec(sql, params=())` (dict_cursor, execute, close), `_fetch_one(sql, params=())`,
   `_fetch_all(sql, params=())`. Convert the ~25 copies of the 7-line cursor pattern in the
   subclasses mechanically — same SQL strings byte-for-byte.
2. Delete the `delete()` overrides whose ONLY delta vs the base is `self.mydb.commit()`:
   `MySQLHuntCanvasDb` (:1111), `MySQLCanvasRegionDb` (:1197), `MySQLPuzzleImageDb.delete`
   (:1316), `MySQLMediaMarkupDb` (:1393). Autocommit is on (`bot/store/__init__.py:65`), so
   the explicit commits are redundant — cite that in the commit message. KEEP overrides that
   do more (`MySQLTagGroupJsonDb` :203, `MySQLPuzzleJsonDb` :694 — tags cascade;
   `MySQLEvidenceDb.delete` :938 keeps its docstring/signature — collapse it only if it
   matches the base exactly after the helper conversion).
3. This layer has no test runner coverage (tests mock the DAOs). Report it as
   inspection + typecheck verified, not test-verified.

Acceptance: full pytest green (proves no import/signature breakage); basedpyright 0.
Commit: `refactor(store): cursor helpers, drop redundant commit-only delete overrides`.

## Phase 6 — frontend dedup (typecheck + vitest for the pure part)

1. `frontend/src/lib/statusTone.ts`: implement per the 1d tests. `util.statusColor` becomes a
   tone→Mantine-colour map over `statusTone()`; `CanvasView.tintFor` keeps its
   `puzzle.solved` boolean short-circuit, then maps tone→hex (working→`#f08c00`,
   unstarted+unknown→`#868e96`). Move the "unsolved contains solved" comment into
   statusTone; delete both duplicated rule blocks.
2. `frontend/src/hooks/useZoomPan.ts`: extract the duplicated zoom/pan/wheel/ResizeObserver
   logic from `CanvasView.tsx` and `MarkupCanvas.tsx` (comments copied verbatim between them
   mark the span). Pure code motion: same refs, same handlers, same `canvasMath` calls; the
   components keep their DOM structure untouched. If the two copies have diverged anywhere,
   STOP and list the divergences in the report before choosing — do not silently pick one.
3. `frontend/src/api.ts`: extract `apiUpload<T>(path, form: FormData)` from the two
   hand-rolled multipart paths (:206, :995) — keep the "not apiSend: multipart needs the
   browser's own boundary" comment once, on the helper.
4. Shared OCR "replace notes?" flow: extract the ~18 identical lines + twin ConfirmModals
   from `EvidencePanel.tsx` (~:105 and the ConfirmModal at ~:412) and `RegionModal.tsx`
   (~:110, ~:301) into one hook or small component under `frontend/src/components/` —
   whichever shape needs zero DOM change in the two hosts.

Acceptance: `cd frontend && npx vitest run` green (canvasMath 6 + statusTone) and
`npm run build` clean (that runs `tsc --noEmit` first). No component-level runner exists —
report steps 2–4 as typecheck + inspection only.
Commit: `refactor(frontend): statusTone, useZoomPan, apiUpload, shared OCR confirm`.

## Phase 7 — test-suite dedup

1. `tests/conftest.py`: add ONE factory (plain function, house style — NOT a pytest fixture)
   that builds the Quart `web` app the 19 files each hand-roll (~20 lines: app, secret key,
   session patch, ctx dict). Parameterise only what actually varies across the 19 copies —
   read 4–5 of them first (`test_media_web.py`, `test_canvas_web.py`, `test_edge_web.py`,
   `test_add_evidence_web.py`) to find the variance before designing the signature.
   Files: test_edge_web, test_add_evidence_web, test_change_group_type_web, test_canvas_web,
   test_convert_to_meta_web, test_delete_round_web, test_delete_hunt_web,
   test_member_create_web, test_evidence_web, test_puzzle_images_web,
   test_notes_archived_guard, test_hunt_layout_option, test_delete_puzzle_web,
   test_organiser_gate_web, test_markup_web, test_solve_puzzle_web_invariants,
   test_hunt_sheets_web, test_media_web, test_rename_group_web.
   Migrate mechanically; every existing assertion stays byte-identical.
2. While in there: wrap each fixture's `p.start()`…`yield`…`p.stop()` in `try/finally`
   (the leak: a failing `start()` mid-list leaks the earlier patches session-wide) —
   `tests/test_media_web.py:~72` and `tests/test_markup_web.py:~69` are the named offenders;
   the factory should make this structural for all 19.

Acceptance: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` green,
same test COUNT as before Phase 7 (state both numbers).
Commit: `test: one web-app factory, patch cleanup under try/finally`.

## Phase 8 — final gates + mutation proofs

1. Full gates, in order, each pasted into the report:
   - `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` (expect ≥1531:
     1525 at HEAD + the new tests; state the exact number)
   - `pipenv run ruff check`
   - `pipenv run basedpyright bot/`
   - `cd frontend && npx vitest run && npm run build`
2. Mutation-prove the three load-bearing new guards, one at a time, restore each by editing
   the line back (never `git checkout --`):
   - `validate_image_bytes`: drop the allowlist check → 1a/1c tests must go red.
   - `ingest_image_bytes`: swap width/height in `db.create` → 1a transposition test red.
   - `statusTone`: remove the `!includes("unsolved")` exclusion → statusTone test red.
3. Merge `working-2` into `staging` (ff or merge commit, matching how ae270de was done).
   Do not push.

## Acceptance criteria (whole task)

- `!clue` with an attached image stores real dimensions and schedules tiling for large images
  (1b tests); OCR-preview rejects allowlist-excluded types (1c).
- One copy each of: image validation, media ingest, media visibility chain, cross-hunt
  guard, board push, cursor pattern, zoom/pan logic, status→tone rules, multipart upload,
  OCR confirm flow, Quart web fixture.
- All gates green; test count not lower than before (minus nothing — dedup deletes no tests).

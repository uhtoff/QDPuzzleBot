# Changed Files

## Git status

Branch `staging`. Working tree clean apart from untracked `images/` (now gitignored).

Commits added this session, oldest first:

| commit | what |
|---|---|
| `a6d9248` | docs(spec): serve a canvas as a tile pyramid, and split Plan B |
| `05b14ea` | docs(plan): B1 tiled media pipeline |
| `ce5900a` | build: add pyvips for canvas image tiling |
| `9861abe` | migrate: pixel dimensions and tile state on media |
| `3d02e5d` | feat(store): media carries pixel dimensions and tile state |
| `968a82d` | feat(core): derive a WebP tile pyramid from a large image |
| `376d744` | feat(core): tile uploaded images off the event loop |
| `24b70bd` | feat(web): record image dimensions and tile large uploads |
| `53f9055` | feat(web): serve canvas tiles from an authenticated route |

## Files changed

Created:
- `migrations/032_media_tiles.sql`
- `bot/core/image_tiler.py` — pure tiler
- `bot/core/media_tiling.py` — async orchestration
- `tests/test_image_tiler.py`
- `tests/test_media_tiling.py`
- `docs/superpowers/plans/2026-07-26-canvas-b1-tiled-media.md`

Modified:
- `Pipfile`, `Pipfile.lock` — pyvips (plus a pre-existing uncommitted transitive pyasn1
  0.6.3→0.6.4 bump that was already in the tree and rode along)
- `.gitignore` — `images/`
- `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md` — tiling amendment
- `bot/store/media_data.py` — `width`, `height`, `tile_status`, `tile_levels`
- `bot/store/mysqldb.py` — `MySQLMediaDb.create` widened, `update_tiles` added
- `bot/web/api.py` — `media_tile` route; `upload_media` probes dimensions and schedules tiling
- `tests/test_media_web.py` — `TestMediaTiles`, plus three new upload tests
- `tests/test_media_store.py` — column-order expectation extended (see below)

## Diff summary

The only pre-existing test changed is
`test_media_store.py::test_create_sends_parameters_in_column_order`. Reason: the INSERT
genuinely writes four more columns. It was **strengthened** while open, not relaxed — it now
passes `width != height`, because those are adjacent same-typed columns and equal values would
have hidden exactly the transposition the test exists to catch.

## Commands run

```bash
timeout 900 pipenv install                     # pyvips
timeout 120 pipenv run python migrate.py       # applied 032
timeout 600 pipenv run python -m pytest tests/ -q
pipenv run ruff check
timeout 300 pipenv run basedpyright bot/
```

## Tests run

- Full suite: **1317 passed**, 0 failed.
- ruff: clean.
- basedpyright `bot/`: **0 errors**.
- Real-image end-to-end through `media_tiling.tile_media`: `update_tiles(7, 'ready', 6)`,
  940 tiles, 6 levels, 9.6 MB, 8.2 s, 359 MB peak.
- Four mutation checks, each confirmed red for the right reason then reverted: `depth=onetile`
  → `one` (level count), executor → direct call (off-loop), `MAX_DIRECT_PIXELS` ×1000 (tiling
  decision), blank-tile fallback removed (route).

## Tests not run

- **No browser check of the viewer** — there is no viewer until B2. B1 is verified by tests
  plus the real-image run, not by looking at the app.
- **No live HTTP fetch against the running app.** The tile route is covered by tests using a
  real generated pyramid; a manual `curl` through a logged-in session has not been done.
- **Not exercised on the Plesk host.** The `pyvips[binary]` wheel is expected to install
  without root there, but that is inference from the wheel bundling libvips, not a deploy
  that has happened.

## Current risks

- Peak RSS ~360 MB per concurrent tiling job. Two simultaneous uploads of a 200 MP sheet is
  ~720 MB, which a small VPS may not have. No queue or concurrency limit across uploads
  exists — only `VIPS_CONCURRENCY` within one job. Worth a semaphore if it bites.
- `MAX_DIRECT_PIXELS = 16_000_000` is a judgement call about mobile Safari, not a measured
  device limit.
- Tile directories are never swept: `media` rows cascade from `hunts`, and no single-media
  delete path exists. B2's canvas delete must handle it.

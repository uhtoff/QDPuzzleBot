# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Started: 2026-07-28
Status: in progress — refactor(frontend): statusTone, useWheelZoom, apiUpload, shared OCR confirm @ 7104fd6

## Goal

Execute the canvas-review "next quality pass" backlog: two real ingest bugs (`!clue` images
stored 0×0 and never tiled; OCR-preview ignoring `ALLOWED_IMAGE_TYPES`) plus the major
duplication extractions (backend, frontend, tests). Full phased plan:
`.ai/active/current-spec.md` (authoritative).

## Context the executing agent needs

- **Integration target is `staging`, not `main`**, and `staging` is currently level with
  `working-2` at `ae270de`. Do NOT push anything — the user is deliberately holding pushes.
- Backlog source (background only): `.ai/archive/2026-07-28-canvas-review-fixes/session-summary.md`.
  Its line numbers have drifted; the spec's refs were re-verified against HEAD on 2026-07-28.
- Model tiers (AGENTS.md): Phase 1 (test design) on **Opus 4.8**, Phases 2–8 on **Sonnet 4.6**.
- **The base-`delete()` "missing commit" backlog item turned out NOT to be a bug**: the
  connection sets `autocommit=True` (`bot/store/__init__.py:65`). Phase 5 therefore REMOVES
  the redundant commit-only overrides instead of adding commits. Do not "fix" the base.
- The cross-hunt guard has 4 copies with 2 error keys (`unknown_media` at
  `actions.py:2259` vs `cross_hunt_media` at the other three); unification changes one
  pre-existing test expectation (`tests/test_canvas_web.py:116`) — that change is planned
  and reasoned in the spec, don't treat it as a regression.
- Three planned pre-existing-test changes total, all named in the spec (clue-attachment
  patch-target migration, the error-key expectation, `_push_board` patch migration). Any
  OTHER pre-existing test you feel like changing is a stop-and-report.
- Frontend has NO component-level runner. Vitest covers only `frontend/src/lib/` pure
  modules (`canvasMath` today, `statusTone` after Phase 1). Hook/component extractions are
  typecheck + inspection only — report them as such.
- Env gotchas: fresh worktrees need `config.json` + `google_secrets.json` copied from the
  main checkout (this one has them), and `pipenv sync` if `TestCanvasOcrPreview` fails on a
  missing `pytesseract`.

## Final decisions

- New `bot/core/media_ingest.py` (Quart-free, discord-free) owns save→probe→create;
  `schedule_tiling(media, db, after=None)` + the strong-ref task set move into
  `bot/core/media_tiling.py`; api/cog/crop all call the pair.
- Region crops now tile when oversize (previously hard-coded `tile_status="none"`) —
  deliberate improvement, noted in the spec.
- `statusTone()` unifies `statusColor`/`tintFor`; "needs …" badges deliberately go
  blue→yellow to match the tint rules.
- Error-convention item (`setErr` → `notifyError`) EXCLUDED — needs a UX decision.
- "Smaller" backlog grab-bag items EXCLUDED — they stay in the archived backlog.

## Rejected approaches

- Adding `commit()` to the base `delete()` (the backlog's framing) — autocommit makes it
  moot; removing the redundant overrides is the truthful cleanup.
- Testing the OCR-preview allowlist bug via message-string comparison — not red today
  (the strings are byte-identical copies); patching `ALLOWED_IMAGE_TYPES` is what isolates
  the missing check.
- A subset-tolerant `reorder` and anything else already settled in the hardening tranche —
  don't revisit.

## Next steps

1. ~~Phase 1: write the red tests, commit.~~ DONE — 11 red python tests + 1 red vitest suite.
   **Spec deviation:** the 1c allowlist test went into
   `tests/test_media_web.py::TestEvidenceOcrPreview` (`test_type_outside_allowlist_is_refused`),
   NOT `tests/test_canvas_web.py::TestCanvasOcrPreview` as the spec said. The spec's own
   Phase 2 fix targets `bot/web/api.py ocr_evidence_preview`
   (`/api/hunts/<uid>/evidence/ocr-preview`), which is the route with upload validation and
   is covered by test_media_web.py; the canvas class covers a different route
   (`/api/canvases/<id>/ocr-preview`, actions.py:2719) that takes a JSON shape and does no
   upload validation, so the spec's multipart test was not writable there. Phase 2's
   acceptance run must therefore include `tests/test_media_web.py` (it already does).
2. Phases 2–8 on Sonnet 4.6, one commit per phase, gates pasted per phase.
3. Merge `working-2` → `staging` at the end; no pushes.

## Open questions

None blocking. If the two `useZoomPan` copies have semantically diverged (Phase 6 step 2),
stop and report the divergences rather than choosing silently.

## What NOT to touch

`migrations/`, `puzzles_gsheet.py`, `gsheets.py`, `MarkupCanvas` DOM structure, the
excluded backlog items, `main` branch, any push to origin.

## If you get stuck

Diagnose with `superpowers:systematic-debugging` first. If an acceptance command fails twice
without a clear understood fix, write `.ai/active/blocker.md` (what ran, what you saw, best
hypothesis) and STOP.

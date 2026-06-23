# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

## Goal

Implement an organiser-only `!reconcile` command that makes Discord channels + Google Sheets tabs
match the database (source of truth), healing partially-applied create/edit operations.

## Current status

Spec + plan written and committed on `main` (already in this worktree). No implementation started.
Execute `.ai/active/current-spec.md` phase by phase. The full verbatim code lives in
`docs/superpowers/plans/2026-06-23-reconcile.md` — copy from it.

## Context the files don't make obvious

- **Why this exists:** a `!puzzle` run during a Google network blip threw `KeyError: 'sheets'`
  mid-creation, leaving a tab created but the command aborted — a half-applied state. The crash
  itself was fixed separately (`sheet_list()` guard, commit `2d33ed5` on main). `!reconcile` is the
  general healer for such states.
- **One channel per puzzle:** a puzzle can belong to several rounds/metas (`puzzle.tags`), but
  Discord limits mean it gets exactly ONE channel, placed in the FIRST round's category
  (`tags[0]`). Reconcile must never flag a multi-round puzzle as "missing channel" for its other
  rounds. This is the single most important behavioural rule.
- **Channel resolution order:** relink-and-move beats recreate. Welcome/info is reposted ONLY when a
  brand-new channel is actually created — never when an existing channel is found and moved.
- **`PuzzleService` is ctx-free and testable** (`bot.get_cog` returns the GoogleSheets cog, or
  `None` in tests). All tests stub the Discord + Sheets seams with `unittest.mock`; no live services.
- **Model split:** Phase 1 (test design) on Opus; Phases 2–6 (mechanical implementation) on Sonnet.

## What NOT to touch

- No category recreation, no meta roll-up rebuild, no additional-sheet recreation (all out of scope
  — see spec).
- Do not refactor existing functions or touch files outside the spec's Relevant files list.
- Do not modify `conftest.py` or sibling test files; keep all test helpers local to
  `tests/test_reconcile.py`.

## If you get stuck

Diagnose with `superpowers:systematic-debugging` first. Two-strikes rule: if one acceptance command
fails twice without a clear, understood fix, write `.ai/active/blocker.md` (command, output,
diagnosis) and STOP — do not invent harness machinery or loop until green.

## Next steps

1. Phase 1 — write all failing tests + report model + stubs (Opus). Confirm behavioural tests fail
   with `NotImplementedError` and the file collects cleanly.
2. Phases 2–6 — implement per spec (Sonnet), each phase green before the next.
3. Final verification run, then bump Status and hand back for review.

## Open questions

None blocking. Two heuristics to validate during Phase 4 (orphan-channel `———` marker; orphan-tab
`hidden` property) — the spec tells you how to check and to write `blocker.md` if genuinely unclear.

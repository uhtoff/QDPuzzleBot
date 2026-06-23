# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Started: 2026-06-23
Status: ready for handoff — M9: remove leftover unused HuntJsonDb import in evidence PATCH route @ 1a425e9

## Goal

Implement M9 — a hunt-wide evidence board: `!clue` / `!clues` bang commands log freeform clues
against any puzzle channel; clues accumulate in a DB table, a Google Sheet tab, and a web dashboard
panel; target-puzzle routing is set later via web UI.

## Context the executing agent needs

**The spec is authoritative:** `docs/superpowers/specs/2026-06-23-m9-evidence-board-design.md`

Key decisions already made (do not re-litigate):
- One `evidence_items` table. No separate "capstone" puzzle type. No `reveals` edge on `tags`.
- Source puzzle = the channel where `!clue` was typed (auto-stamped, never updated).
- Target puzzle = any puzzle in the hunt; assigned later via web.
- Sheet is rendered by the existing `aggregate_to_tab()` engine; a new `create_blank_tab()` method
  is needed on the gsheet cog (NOT `add_new_sheet`, which duplicates a puzzle template).
- `HuntData` gets an `evidence_sheet_page_id` field; migration adds the column to `hunts`.

**What NOT to touch:**
- `migrations/` history (don't edit any existing migration, only add `008_evidence.sql`)
- `bot/cogs/puzzles_gsheet.py` beyond adding `create_blank_tab`
- Any existing bang commands
- Anything outside the file list in `current-spec.md`

**If you get stuck:** invoke `superpowers:systematic-debugging` before changing anything. If a
test fails twice with no clear understood fix, write `.ai/active/blocker.md` and STOP.

**Test infrastructure:** `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Filter a single file: `timeout 60 pipenv run python -m pytest tests/test_evidence_service.py -q > /tmp/test.log 2>&1`
DB is required (live MySQL `puzzlebot_test`) — tests that hit the DB are out of scope; mock the
store layer.

# M8 — Minimeta Clusters Design

**Date:** 2026-06-23
**Milestone:** M8 (deps: M5, M6)
**Status:** Approved for implementation

---

## Goal

Enable minimeta clusters: a parent puzzle (the minimeta) with N mini-puzzles underneath it,
each in its own Discord thread and Google Sheet tab. Solvers work in threads and answers roll
up to the minimeta's sheet like a regular meta. Everything doable via bang commands is also
doable via the web dashboard.

---

## Context

Minimeta clusters appear in puzzle hunts as a group of small puzzles feeding a local meta,
all logically part of one "puzzle" entity. Today teams work around this by creating additional
sheets manually and tracking answers informally. M8 makes this first-class.

The design rides on infrastructure already delivered:
- `additional_sheets` table with dead `solution`/`solved` columns (activated here)
- M6 `edge_type` on `tags` (mini-puzzles tag `feeds-meta` into the minimeta's round)
- M3 `bot/core/` service layer (all logic goes there, Discord is an adapter)
- M5 `refresh_meta` roll-up (drives the minimeta answer sheet)

---

## Architectural intent

The dashboard is the **primary interface**; Discord is an adapter. Every bang command must
have a dashboard equivalent. The longer-term direction is to decouple from Discord text
entirely — using Discord only for voice — and replace its text/command layer with the web UI.
Every feature should isolate Discord-specific code (channel/thread creates, message sends) in
the cog/adapter layer so the service layer can be driven from either surface. M3's `bot/core/`
extraction was explicitly to enable this swap.

---

## Data model

### New column: `puzzles.parent_puzzle_id`

```sql
ALTER TABLE puzzles ADD COLUMN parent_puzzle_id INT DEFAULT NULL;
ALTER TABLE puzzles ADD CONSTRAINT fk_parent_puzzle
    FOREIGN KEY (parent_puzzle_id) REFERENCES puzzles(id) ON DELETE SET NULL;
```

- `NULL` on all existing puzzles and regular new puzzles
- Set to the minimeta's `puzzles.id` on mini-puzzle rows
- Mini-puzzles are excluded from the main puzzle list (`WHERE parent_puzzle_id IS NULL`)

### `puzzle_type = "Minimeta"` on the parent puzzle

Reuses the existing `puzzle_type` field (already used for "Metapuzzle", "Round", etc.).
Set by `!make_minimeta` or auto-set by `!add_mini`.

### Mini-puzzle rows

Each mini-puzzle is a real `PuzzleData` row with:
- `channel_id` = Discord thread snowflake (in the minimeta's channel, not a new channel)
- `google_page_id` = its own tab in the hunt spreadsheet
- `parent_puzzle_id` = minimeta's puzzle id
- `puzzle_type` = `"Mini"` (distinguishable from regular puzzles and the parent)

No new schema beyond `parent_puzzle_id`. The `additional_sheets` table is **not** used for
mini-puzzles in this model — each mini is a first-class puzzle row.

---

## Commands

| Command | Context | Effect |
|---|---|---|
| `!make_minimeta` | any puzzle channel | Sets `puzzle_type="Minimeta"` on current puzzle |
| `!add_mini [name]` | minimeta channel | Creates thread + puzzle row + Google tab; auto-sets minimeta type if not already |
| `!list_minis` | minimeta channel | Lists minis with solve state |
| `!solve_mini N ANSWER` | minimeta channel | Solves mini #N; delegates to core solve path |
| `!unsolve_mini N` | minimeta channel | Clears mini #N solution; delegates to core unsolve |
| `!solve ANSWER` | mini thread | Solves by thread context (unchanged command) |
| `!unsolve` | mini thread | Clears solution by thread context (unchanged command) |

`!solve_mini` / `!unsolve_mini` resolve the puzzle from its number then call the same
`PuzzleService.solve` / `PuzzleService.unsolve` path as the thread-context versions.
Confirmation message acknowledges the number: "✅ Solved mini #2 (Puzzle Name): ANSWER".

### Thread prerequisite

`cog_before_invoke` (`puzzles.py:159-210`) and `activity.py:31` must become thread-aware
before any thread-context commands work. A thread's snowflake in `puzzles.channel_id`
already resolves via `get_channel_type`'s UNION (`mysqldb.py:408-425`); the gaps are
non-puzzle threads (use `message.channel.parent_id` to check) and activity attribution.
This is flagged as a hard prerequisite in PLANS.md for all thread work.

---

## Sheet behaviour

### Mini-puzzle tab creation

`!add_mini [name]` creates a new tab in the hunt spreadsheet for the mini-puzzle, copied
from the master template — same process as regular puzzle sheet creation. The mini gets its
own `google_page_id`.

### Roll-up on the minimeta sheet

Mini-puzzles tag `feeds-meta` into the minimeta's round (same mechanism as regular feeders).
The existing `refresh_meta` roll-up writes mini answers to the minimeta's sheet automatically
on each solve/unsolve. No new roll-up mechanism needed.

The minimeta sheet's answer section therefore shows mini-puzzle names + solutions as they
come in, exactly like a meta shows its feeders.

### Archiving

If an archive spreadsheet is configured, mini-puzzle tabs move to it on solve exactly as
additional sheets do today in `archive_puzzle_spreadsheet`. No special handling required.

The stale-pointer risk noted in PLANS.md does not apply here because the minimeta's roll-up
is driven from DB data (mini `solution` fields), not live sheet pointer references.

---

## Dashboard

### Puzzle list

Mini-puzzles (`parent_puzzle_id IS NOT NULL`) are excluded from the main puzzle list.
The minimeta row shows a **`N/M solved` badge** and an expand chevron.

### Expanded mini-puzzle panel

Clicking the chevron reveals a sub-list of minis:

- Name
- Solution (or "Unsolved")
- Solve input + button (same flow as the existing puzzle solve action)
- Unsolve button when solved

An **Add Mini** button at the bottom of the panel creates a new mini-puzzle (name input →
calls the same `PuzzleService.create_mini` path as `!add_mini`), creating the thread and
Google tab.

### API changes

- `GET /api/hunt/<uid>` puzzle payload for minimeta-type puzzles includes a `mini_puzzles`
  array: `[{id, name, solved, solution}, …]` (joined on `parent_puzzle_id`)
- `POST /api/puzzles/<id>/minis` — create a new mini under this minimeta; calls
  `PuzzleService.create_mini`; returns the new mini's id, name, thread id
- `POST /api/puzzles/<id>/solve` — new endpoint (or extend existing solve endpoint)
  reachable from the dashboard mini panel; calls `PuzzleService.solve`
- `POST /api/puzzles/<id>/unsolve` — same pattern

---

## Implementation scope

### In scope

- Migration: `parent_puzzle_id` column on `puzzles`
- `PuzzleService` methods: `create_mini`, `solve_mini_by_number`, `unsolve_mini_by_number`
- Thread-awareness fix in `cog_before_invoke` + `activity.py`
- Bang commands: `!make_minimeta`, `!add_mini`, `!list_minis`, `!solve_mini`, `!unsolve_mini`
- Dashboard: minimeta badge, expand panel, solve/unsolve actions from panel
- API: `mini_puzzles` in puzzle payload, solve/unsolve endpoints

### Out of scope

- `!delete_mini` (use existing `!remove_sheet` or a follow-on)
- Dashboard creation of minis ✓ (in scope — Add Mini button in the expand panel)
- M11 feeder-as-thread (separate milestone; thread-awareness fix here is shared groundwork)
- Any change to capstone / reveal logic (M9)

---

## Acceptance criteria

1. `!add_mini Puzzle Name` from a minimeta channel creates a thread named "Puzzle Name" and a
   Google tab; the minimeta auto-sets to `puzzle_type="Minimeta"` if not already.
2. `!solve ANSWER` from inside a mini thread marks the mini solved and refreshes the minimeta
   sheet.
3. `!solve_mini 2 ANSWER` from the minimeta channel has the same effect as (2) for mini #2.
4. `!unsolve` / `!unsolve_mini 2` reverses a solve and refreshes the minimeta sheet.
5. `!list_minis` shows all minis with solved/unsolved state.
6. Dashboard minimeta row shows `N/M solved` badge and expand chevron.
7. Expanded panel shows each mini's name + solution/unsolved + solve input.
8. Solve/unsolve from the dashboard panel calls the same service path as the bang commands.
9. Mini-puzzles do not appear as top-level rows in the dashboard puzzle list.
10. `!make_minimeta` on an existing regular puzzle sets its type without any other side effect.

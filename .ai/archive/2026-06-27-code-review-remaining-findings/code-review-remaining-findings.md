# Code-Review Remaining Findings (handoff seed for working-2)

**Source:** external code-reviewer report, evaluated + verified in working-1 on 2026-06-27.
**Scope of this file:** findings **#2–#7** and documentation-quality notes from that report.
**Explicitly NOT here:** finding **#1** (group-type-list staleness / `PUZZLE_GROUPS` drift) — that is
being designed + implemented in **working-1** (see
`docs/superpowers/specs/2026-06-27-group-type-consolidation-design.md` once committed there).

> This is *seed material*, not an in-progress session. Whoever picks this up in working-2 should
> create their own `.ai/active/session-summary.md` + `current-spec.md` and decide which findings to
> action. All line numbers verified against working-1 @ `ae5f63c` (2026-06-27); re-confirm before editing.

---

## ⚠️ Cross-worktree conflict warning (read first)

working-1 is **heavily rewriting** these files for finding #1:
- `bot/cogs/puzzles.py` — removes `PUZZLE_GROUPS` (line 83), edits `command_map`, the `!info`
  dispatch (~2258), the welcome builders (~2076–2187), and the import block (lines 14–15).
- `tests/test_group_channel_type.py` and `tests/test_forum_layout.py` — updates `Round`/`Metaless
  Round` assertions.
- `bot/web/actions.py` — edits the web create route (~1503–1530) only.

Therefore:

| Finding | File | Collides with #1? | Recommendation |
|---|---|---|---|
| #2 | `AGENTS.md` | No | **Safe to do now** |
| #7 | `README.md` | No | **Safe to do now** |
| #4 | `bot/web/actions.py:87–130` | Low (different region than #1's ~1503) | Safe-ish now; rebase-check before merge |
| #3 | `bot/cogs/puzzles.py:2022/2193/2205` | **Yes** | Defer until #1 merges, or let working-1 fold it in |
| #5 | the two test files above | **Yes** | Defer until #1 merges |
| #6 | `bot/cogs/puzzles.py:15` | **Yes — likely auto-fixed by #1** | Let working-1 drop it (it's editing that import block) |

**Suggested working-2 order:** #2 → #7 → #4 now; hold #3 / #5 / #6 until working-1's #1 lands.

---

## Finding #2 — Roadmap/docs disagree (REAL; agent-guidance drift)

- `AGENTS.md:60–65` "Roadmap & planning" says: *Delivered so far: M0 (schema+migrations), M1 (test
  seam), M2 (`aggregate_to_tab`), 7 quick wins. M3 (`bot/core/` service extraction) is in progress.*
- `docs/PLANS.md:398` (authoritative roadmap) shows the critical path struck through all the way to
  **M9 done**, with **M9b (next — capstones/reveal)**; also M10 done (`9951408`), M3.5 done, M7.5 done
  (`5cc71b3`), M11 deferred.
- **Fix:** update the `AGENTS.md` "Delivered so far" bullet to match PLANS.md (M0–M10 + M3.5 + M7.5
  delivered; **M9b next**). AGENTS.md already names PLANS.md as the source of truth — just resync the
  one-line summary. Trivial, doc-only.

## Finding #3 — Pinned welcome text: hardcode + typo + duplication (REAL bug + cosmetics)

- **Real bug:** `bot/cogs/puzzles.py:2193` hardcodes `f"""Welcome to the puzzle channel for
  {puzzle.name} in Test!"""` — every hunt's puzzle welcome literally says "in Test!". `hunt` is in
  scope in that builder (`send_initial_puzzle_channel_messages`); should interpolate the hunt name
  (e.g. `{hunt.name}`), matching how the other builders read `self.get_hunt(ctx)`.
- **Typo:** `seperated` → `separated` at `puzzles.py:2022` **and** `2205` (the line is duplicated
  verbatim across the meta and puzzle welcome builders).
- **Optional refactor:** the command-help block is duplicated across welcome builders; could be a
  shared render helper. Keep narrow — the typo + hardcode are the high-value fixes; refactor only if
  desired.
- **⚠️ Collides with #1** (working-1 edits the welcome builders + may delete the round builder). Do
  after #1 merges, or ask working-1 to fold the typo/hardcode in.

## Finding #4 — Web visibility-helper boilerplate (NO bug; optional dedup)

- `bot/web/actions.py:87` `_visible_hunt_for_puzzle`, `:105` `_visible_hunt_by_uid`, `:119`
  `_visible_hunt_for_tag_group` are near-identical: each does `current_user()` →
  `data.get_*(...)` → `resolve_membership` → `get_guilds_map` → `hunt_visible` → return
  `{hunt, guild_snowflake}` or an error tuple.
- They differ **only** in (a) the `data.get_*` lookup fn and (b) the not-found error key
  (`puzzle_not_found` / `hunt_not_found` / `round_not_found`).
- **No concrete parity bug found** — pure maintainability. Could collapse to one helper taking
  `(lookup_coro, error_key)`. Lowest priority; YAGNI-optional.

## Finding #5 — Stale "FAIL until Phase 2" test comments (REAL; trivial)

- `tests/test_group_channel_type.py:7–9` docstring: *"These FAIL until Phase 2: ..."* — suite passes
  now.
- `tests/test_forum_layout.py:165`: *"These FAIL until Phase 2 implements the Group path ..."* — also
  passes.
- **Fix:** delete/rewrite the stale "FAIL until Phase 2" comments so the files read as living
  examples.
- **⚠️ Collides with #1** (working-1 edits these same test files). Do after #1 merges.

## Finding #6 — Lint red: unused import (REAL; trivial)

- `ruff check` reports exactly one error: `F401 'bot.store.TagGroupData' imported but unused` at
  `bot/cogs/puzzles.py:15`. The only other `TagGroupData` mention (`:274`) is in a docstring, so it's
  genuinely unused (no test patches `bot.cogs.puzzles.TagGroupData`).
- **Fix:** drop `TagGroupData` from the multiline import (lines 14–15).
- **⚠️ Collides with #1 and is most likely fixed there** — working-1 is editing that exact import
  block. Confirm it's gone after #1 merges rather than fixing separately.

## Finding #7 — README test command stale (REAL; trivial)

- `README.md:176` tells contributors to run `python -m pytest`.
- Project guidance + actual behavior require the timeout-wrapped, pipenv, compact form, e.g.
  `timeout 120 pipenv run python -m pytest --compact` (add `--parallel` for file/suite runs). See
  AGENTS.md / global testing rules.
- **Fix:** update the README "Tests" section command. Doc-only.

## Documentation quality (informational; reviewer's note)

- `docs/commands.md` is **accurate** — AST comparison found 70 command functions / 114 command
  names+aliases, with only the `update_puzzle_setting(s)` aliases written in shorthand rather than as
  separate entries. No action needed.
- `README.md` is weaker (see #7). If polishing docs, README is where the drift is.

---

## Verification commands (working-2)

```bash
# Lint (expect the one F401 until #6/#1 fixes it)
pipenv run ruff check

# Confirm the typo + hardcode still present
grep -n "seperated" bot/cogs/puzzles.py
grep -n "in Test" bot/cogs/puzzles.py

# Confirm stale test comments
grep -rn "FAIL until Phase 2" tests/

# Tests (timeout-wrapped, compact — never unbounded)
timeout 180 pipenv run python -m pytest --compact > /tmp/test-output.log 2>&1
```

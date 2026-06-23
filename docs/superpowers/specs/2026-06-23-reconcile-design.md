# `!reconcile` — make Discord + Sheets match the DB

Date: 2026-06-23
Status: design approved, pending spec review

## Goal

When a puzzle/round creation or edit only partially goes through (a transient
Google/Discord failure mid-flow), the bot can be left with a DB row whose Discord
channel or Sheets tab is missing, broken, or out of sync. Provide an organiser
command that **treats the database as the source of truth** and makes Discord +
Google Sheets state match it: create what's missing, fix what's drifted, relink
broken pointers, and report (optionally remove) orphans.

Motivating incident: a `!puzzle` run during a network blip threw
`KeyError: 'sheets'` mid-creation (fixed separately in `sheet_list()`, commit
2d33ed5), leaving a tab created but the command aborted — exactly the kind of
half-applied state reconcile should heal.

## In scope

- A new organiser-only command, hunt-wide, that reconciles every puzzle and round
  in the hunt.
- Four classes of drift: missing artifacts, stale attributes, broken ID links,
  orphans (no DB row).
- Report-then-confirm safety model; orphan deletion behind an explicit opt-in.

## Out of scope

- Recreating Discord **categories** for rounds (report-only — heavier, reorders
  everything; organiser fixes manually).
- Rebuilding meta roll-up sheets — delegated to the existing `!rebuild` /
  `refresh_meta`; reconcile only flags "run `!rebuild`".
- A web/dashboard entry point (Discord command only for now).

## Command surface

`@commands.hybrid_command` named `reconcile`, gated
`@commands.has_any_role(*ORGANISER_ROLES)`, run in a hunt channel — same shape and
gating as `!rebuild`.

- `!reconcile` — **report only**. Read-only scan; prints the diff of what it would
  do. No mutations.
- `!reconcile apply` — applies **non-destructive** fixes (create missing
  channel/tab, relink broken IDs, fix stale attributes). Never deletes.
- `!reconcile apply orphans` — also removes orphans, but first pops a `ConfirmView`
  listing exactly what will be deleted. Deletion happens only after confirmation.

## Source-of-truth model

The DB row is authoritative. "First round/meta" for a puzzle's single channel =
`puzzle.tags[0]` (which round it lands in does not matter to the user, so first
entry is fine). A puzzle may be in several rounds/metas but, due to Discord
limits, has exactly **one** channel — placed in `tags[0]`'s category. It must
never be flagged "missing channel" for its other rounds.

## Per-puzzle reconciliation

For each `PuzzleData` in the hunt:

### Channel (resolution order — relink/move beats recreate)

Expected category = `category_id` of the round at `tags[0]` (None if no tags).

1. `channel_id` resolves to a live channel (`guild.get_channel`) → ensure it is in
   the expected category (**move** if drifted) and named `clean_name(name)`
   (**rename** if drifted). **No welcome repost.**
2. `channel_id` missing/broken, but a channel named `clean_name(name)` exists
   elsewhere in the guild → **relink** (write back `channel_id`, `channel_name`,
   `channel_mention`) and move to the expected category. **No welcome repost** — it
   already has content/history.
3. No existing channel found anywhere → **create** via `get_or_create_channel()`,
   write back the IDs, then `notifier.welcome(guild, hunt, tags[0]-round, puzzle,
   channel)` to post + pin the welcome/info message. Welcome is posted **only** on
   genuine creation.

### Sheets tab

- Choose spreadsheet by `puzzle.archived` (hunt vs archive spreadsheet).
- `google_page_id` set and `get_page_name_by_id(google_page_id, spreadsheet)`
  resolves → OK. Optional: if the tab title ≠ expected unique name, report; rename
  on apply.
- `google_page_id` set but tab gone, OR `google_page_id` missing → recreate via
  `add_new_puzzle_sheet`, write back the new `google_page_id`, re-run
  `update_puzzle_info`.
- Repeat for each `AdditionalSheetData` in `puzzle.additional_sheets`.

DB write-back via `PuzzleJsonDb.commit` after each external success (mirrors
`create_puzzle`'s write-back pattern).

## Per-round reconciliation

- Verify `round.category_id` resolves to a live Discord category → **report** if
  broken (no auto-recreate; see Out of scope).
- If the round is a meta (`meta_id`/`kind == "meta"`), note "run `!rebuild`" rather
  than rebuild the roll-up here.

## Orphans (opt-in delete only)

- **Channels:** any text channel under the hunt's round categories whose ID matches
  no puzzle `channel_id` and which is not itself a round category → orphan
  candidate. Exclude system channels (the hunt channel, `———solved———` markers).
- **Tabs:** any tab in the hunt spreadsheet not matched by any puzzle's
  `google_page_id` and not a template/system tab → orphan candidate.
- Reported by default. Deleted only via `!reconcile apply orphans` + `ConfirmView`.

## Safety, errors, output

- Every create path reuses idempotent helpers (`get_or_create_channel`; tab
  existence checks), so re-running is safe and convergent.
- Each puzzle/round wrapped in try/except; failures collected and reported at the
  end (like `!rebuild`), never aborting the whole run on one failure.
- Report is grouped and summarised to respect Discord message limits, e.g.:
  `Channels: 2 missing, 1 broken-link, 0 drift · Sheets: 1 tab missing · Orphans: 1 channel, 0 tabs`.
  Detail lines chunked if long.

## Required behaviour (acceptance criteria)

- `!reconcile` never mutates Discord, Sheets, or DB.
- `!reconcile apply` makes a puzzle whose `channel_id` is broken resolve to a live
  channel (relinked or recreated) and a puzzle whose tab is missing have a live
  tab, with IDs written back to the DB.
- A multi-round puzzle is reconciled to exactly one channel in `tags[0]`'s
  category and is not flagged for its other rounds.
- Welcome/info is reposted **only** when a brand-new channel is created, not when
  an existing channel is found and moved.
- Orphans are never deleted without `apply orphans` + confirmation.
- Re-running `!reconcile apply` immediately after a successful run reports no
  changes (idempotent/convergent).

## Constraints / things not to change

- Reuse existing seams: `get_or_create_channel`, `notifier.welcome` /
  `refresh_info`, `add_new_puzzle_sheet` / `get_page_name_by_id`,
  `PuzzleJsonDb` / `RoundJsonDb` lookups. Do not duplicate creation logic.
- Follow `!rebuild`'s structure (organiser gate, per-item try/except, summary
  message) for consistency.
- Do not auto-recreate round categories; do not rebuild meta roll-ups.

## Testing

Unit tests stubbing the Discord + Sheets seams (existing `_cog()` /
`AsyncMock` / `patch.object` style):

- broken `channel_id` with an existing same-named channel → relink + move, no
  welcome repost.
- broken `channel_id`, no existing channel → create + welcome repost.
- missing `google_page_id` → tab created and written back.
- multi-round puzzle → only `tags[0]` category expected; other rounds not flagged.
- orphan channel/tab → reported, not deleted, on the default and `apply` paths.
- `apply orphans` → deletes after confirm; report-only path leaves them.
- idempotency: second `apply` run reports no changes.

## Relevant files

- New command: `bot/cogs/puzzles.py` (alongside `rebuild`, ~line 618).
- Reconcile logic: likely a new method on `PuzzleService`
  (`bot/core/puzzle_service.py`) so it is testable without Discord, with the cog
  command as a thin wrapper (mirrors the service-extraction pattern, M3).
- Seams: `bot/core/discord_channels.py`, `bot/core/notifier.py`,
  `bot/cogs/puzzles_gsheet.py`, `bot/store/{puzzle_data,round_data,mysqldb}.py`.
- Tests: new `tests/test_reconcile.py`.

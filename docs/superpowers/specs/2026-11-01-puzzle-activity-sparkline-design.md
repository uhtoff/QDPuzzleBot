# Puzzle activity sparkline — design

Date: 2026-11-01. Status: approved by the user 2026-11-01.
Roadmap: item 3's "dashboard activity indicators (chat-only first, sheet-edit
activity on top of per-puzzle files)" in `docs/PLANS.md`.

## Intent

Give organisers a feel, at a glance down the dashboard puzzle list, for which
puzzles are being worked on and which have been abandoned — **without relying on
teammates to mark puzzles as stuck or abandoned**. The activity itself is the
signal.

A sparkline (not just a "last active" time) is wanted because its shape tells
apart three situations that a single timestamp cannot:

- **Never started** — flat since the puzzle was unlocked.
- **Given up** — a burst of activity, then flat.
- **Stuck** — a steady trickle that goes on without a solve.

## Goals

- A small sparkline per unsolved puzzle in the dashboard puzzle list, covering the
  **whole life of the puzzle**: from when it was added to the bot
  (`puzzles.start_time`, set at creation — the practical "unlock" time) to now.
- Two signals, shown together but **distinguishable**:
  - **Discord chat** in the puzzle's channel — a count per time slot. Works in
    both sheet modes.
  - **Sheet edits** to the puzzle's own spreadsheet file — yes/no per time slot.
    Files-mode hunts only (tab hunts share one spreadsheet, so per-puzzle edit
    activity is not available; they show chat only).
- Display: bar height = chat volume in the slot; a slot in which the sheet was
  edited is marked distinctly (different colour or a marker). This keeps
  "talking but not typing" and "typing quietly" apart. *(My call — the user did
  not object; one combined line was the alternative.)*
- Keep the existing "last activity" relative time beside the sparkline, so "gone
  quiet in the last hour" is readable even on a long-lived puzzle, where the last
  hour is a sliver of the sparkline. It should consider sheet edits as well as
  chat once both are recorded.

## Non-goals

- Discord surfaces (puzzle channel, hunt overview) — dashboard puzzle list only,
  initially.
- Who did what: no per-person attribution, no "N people worked on this".
- Backfilling history for puzzles that predate the feature. Activity is recorded
  from when this ships; earlier slots are simply empty. (Acceptable — hunts are
  short, and the feature ships between hunts.)
- The Google Drive Activity API. Not needed for the shapes wanted; would need an
  extra API enabled and an extra scope.
- Board / canvas views. List only.

## Anti-goals (would make it a failure even if it works)

- **The bot's own edits making a puzzle look active.** The bot writes to puzzle
  files (solve tidy-up, mini turned green, headers, roll-up tables). Those must
  not count as sheet activity. Bot and webhook messages must not count as chat
  activity (the existing listener already drops `message.author.bot`).
- **Hurting the bot.** Activity recording must stay off the message-handling
  path (same pattern as `bot/cogs/activity.py`: enqueue, single background
  writer) and must never raise into the bot. The Drive poll must not reintroduce
  the 429 trouble fixed in 3d550f3 — one `files.list` per hunt folder per poll,
  never one call per puzzle.
- **A slow puzzle list.** The list endpoint is hit often; sparkline data must
  come from a cheap indexed query, not per-puzzle work.

## What already exists (build on it)

- `bot/cogs/activity.py` — `ChannelActivity.on_message` records the latest human
  message per channel into `channel_activity` (migration 001) via a queued
  background writer, and publishes a live `"activity"` event to the dashboard
  WebSocket (`/ws/activity`, `bot/web/api.py`).
- `bot/web/data.py:get_hunt_puzzles` — joins `channel_activity` to give each
  puzzle `last_activity {at, author}`.
- `frontend/src/components/PuzzleTable.tsx` — "Activity" column showing
  `relativeTime` of the live event or `last_activity`; `useActivityFeed` supplies
  live events.
- Files mode (branch `working-3`, not yet merged): each puzzle has
  `puzzles.google_sheet_id`; each files hunt has `hunts.drive_folder_id` and
  `hunts.solved_folder_id`; `puzzles_gsheet.list_folder(folder_id)` lists a
  folder's spreadsheets (currently fields `id, name, mimeType`).

## Decided

- **Storage: 5-minute slots.** One row per (puzzle, slot) holding a chat message
  count and a sheet-edited flag. Only slots with activity get a row. A 48-hour
  hunt is at most ~576 rows per puzzle.
- **Display: squash to ~30–40 bars** spanning unlock → now, so every sparkline is
  the same width and each puzzle has its own time scale. Two sparklines side by
  side are therefore not on the same clock; that is accepted (shape matters more
  than cross-puzzle comparison), and the relative-time label carries recency.
- **Chat recording** extends the existing activity listener: the same human-only
  filter, the same queue, a second upsert into the slot table keyed by the
  puzzle whose channel it is. Messages in non-puzzle channels are not slotted.
- **Sheet-edit recording** is a periodic poll per active files-mode hunt: list
  the hunt folder and its Solved subfolder with `modifiedTime` and
  `lastModifyingUser`, and mark the current slot edited for each puzzle file whose
  `modifiedTime` advanced since the last poll **and whose last modifier is not
  the bot's service account**. Map file → puzzle by `puzzles.google_sheet_id`.
  - Known limit: if a human edits and then the bot edits within one poll
    interval, the human edit is missed for that slot. Accepted.
  - Minimetas: minis live as tabs in the minimeta's file, so a mini's sheet
    edits are attributed to its minimeta. Minis still get their own chat signal
    if they have a channel.
- **Solved puzzles**: no sparkline (or a faded one) — their shape no longer
  matters. Unsolve brings it back.
- **Live updates**: the sparkline need not be live to the second; refreshing with
  the puzzle list (and nudging the current slot from the existing live chat
  event, if cheap) is enough.

## Left to the builder

- The exact table name/columns and migration number (check for collisions with
  other worktrees before merging — see migration-number memory).
- Poll interval (suggest 5 minutes to match the slot size) and how "active hunt"
  is decided for polling (e.g. non-archived files-mode hunts).
- Where the poller lives (a cog task loop is the obvious home) and how it learns
  the service account's email for the "not the bot" check.
- Bucketing maths (slot → bar), and whether the API returns raw slots or
  pre-squashed bars. Pre-squashed keeps the payload small.
- Sparkline rendering (inline SVG is fine; no chart library needed), colours,
  tooltip content, and the empty / just-unlocked look.
- Retention / cleanup of old slot rows (e.g. drop with the puzzle on delete;
  archived hunts can keep theirs).

## Open questions for the user

None blocking at time of writing.

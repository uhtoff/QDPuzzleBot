# Puzzle Activity Sparkline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-puzzle activity sparkline (Discord chat volume + sheet-edit marks, in 5-minute slots, over the puzzle's whole life) in the dashboard puzzle list's Activity column.

**Architecture:** One table of 5-minute activity slots per puzzle. Chat is counted by the existing `ChannelActivity` listener's background writer; sheet edits come from a 5-minute Drive poll of each files-mode hunt folder (one `files.list` per hunt, filtered to recent changes, ignoring the bot's own edits). `get_hunt_puzzles` squashes each unsolved puzzle's slots into ≤36 bars; the frontend draws them as inline SVG next to the existing relative time.

**Tech Stack:** discord.py (`discord.ext.tasks`), MariaDB via `bot/store/mysqldb.py`, Google Drive v3, Quart (`bot/web/data.py`), React + Mantine + vitest.

**Spec:** `docs/superpowers/specs/2026-11-01-puzzle-activity-sparkline-design.md`

**Base:** branch `working-3` (files mode, unmerged). This plan depends on files mode's `hunts.sheet_mode`, `hunts.drive_folder_id`, `puzzles.google_sheet_id`.

## Global Constraints

- Slot size: **5 minutes**, slot start = UTC time floored to a multiple of 5 minutes, stored as naive UTC `DATETIME` (as elsewhere in the schema).
- Bars: at most **36** per puzzle.
- Poll interval: **5 minutes**; Drive look-back window: **10 minutes** (`modifiedTime >` now − 10 min), so one slow/missed tick loses nothing. Writes are idempotent.
- One Drive `files.list` call (plus pages) per files-mode hunt per tick — never per puzzle. Only the hunt's live folder (`drive_folder_id`), not the Solved folder (solved puzzles get no sparkline).
- A Drive edit counts unless `lastModifyingUser.me` is true (the bot's service account). Missing `lastModifyingUser` (anonymous "anyone with the link" editors) **counts**.
- Bot/webhook Discord messages never count (existing `message.author.bot` filter).
- Activity recording never raises into the bot and never runs blocking DB/Drive work on the event loop (`asyncio.to_thread` / existing async Drive wrappers).
- Migration number **052**; check `ls migrations` across worktrees for collisions before merging.
- Tests: `timeout 600 /home/russ/projects/puzzbot/.venv/bin/python -m pytest tests/ -q > <file> 2>&1`; ruff `/home/russ/projects/puzzbot/.venv/bin/ruff check bot tests`; types `~/.local/bin/basedpyright --pythonpath /home/russ/projects/puzzbot/.venv/bin/python bot/`; frontend `cd frontend && npx tsc --noEmit && npx vitest run`.
- Commit trailers:
  ```
  Co-Authored-By: <your model> <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016SNhh2vvsnJmQDNy2wbpBU
  ```

## Review Focus

1. **Bot edits counted as activity** — a file whose last modifier is the service account must not mark a slot; an anonymous editor must. (Task 3 tests both.)
2. **Non-puzzle channel chat** — messages in hunt/general channels must not create slot rows. (Task 1 live-DB test.)
3. **Very young / very old puzzles** — a 3-minute-old puzzle gets 1 bar, a 30-hour one 36, and no slot is dropped or double-counted in squashing. (Task 4 tests.)
4. **Existing `get_hunt_puzzles` tests** — the payload test's query dispatcher fails loudly on an unknown query; the new slots query must be added to it, not worked around. (Task 4.)
5. **Drive outage / 429** — a failing `files.list` for one hunt is logged and the tick moves on to the next hunt; the loop keeps running. (Task 3 test.)

---

### Task 1: Slot storage

**Files:**
- Create: `migrations/052_puzzle_activity_slots.sql`
- Modify: `bot/store/mysqldb.py` (new class after `MySQLChannelActivityDb`), `bot/store/__init__.py` (export + instances)
- Create: `bot/core/activity_slots.py`
- Test: `tests/test_activity_slots.py` (pure), `tests/test_activity_slots_live_db.py` (live, pattern of `tests/test_access_log_live_db.py`)

**Interfaces:**
- Produces: `slot_start(at: datetime.datetime) -> datetime.datetime` in `bot/core/activity_slots.py` (accepts naive-UTC or aware; returns naive UTC floored to 5 min); `SLOT_MINUTES = 5`.
- Produces: `MySQLPuzzleActivityDb` with
  - `add_chat(channel_id: int, at: datetime) -> None` — `INSERT INTO puzzle_activity_slots (puzzle_id, slot_start, chat_count) SELECT id, %s, 1 FROM puzzles WHERE channel_id = %s ON DUPLICATE KEY UPDATE chat_count = chat_count + 1`. Non-puzzle channels select no rows, so nothing is written.
  - `mark_sheet_edited(google_sheet_id: str, at: datetime) -> None` — same INSERT…SELECT shape on `puzzles.google_sheet_id = %s`, setting `sheet_edited = 1` (`ON DUPLICATE KEY UPDATE sheet_edited = 1`). Empty id is a no-op.
  - `files_hunt_folders() -> list[tuple[int, str]]` — `(hunt_id, drive_folder_id)` for hunts with `sheet_mode = 'files'`, `archived = 0`, non-empty `drive_folder_id`.
  - All three call `slot_start` on `at` themselves.
- Produces in `bot/store/__init__.py`: `PuzzleActivityDb` on the existing `activity_db` connection (used only by the ChannelActivity drain worker), and `SheetActivityDb` — a second `MySQLPuzzleActivityDb` on its **own** `MySQLReconnector` (used only by the Task 3 poller; connections are not shared across threads — see the comments beside `activity_db`).

Table (migration 052):
```sql
CREATE TABLE IF NOT EXISTS `puzzle_activity_slots` (
  `puzzle_id`    INT(11)  NOT NULL,
  `slot_start`   DATETIME NOT NULL,
  `chat_count`   INT UNSIGNED NOT NULL DEFAULT 0,
  `sheet_edited` TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (`puzzle_id`, `slot_start`),
  CONSTRAINT `PuzzleActivityPuzzleID` FOREIGN KEY (`puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

- [ ] **Step 1: Failing pure tests** — `test_slot_start_floors_to_five_minutes` (`12:07:59` → `12:05:00`; `12:05:00` → `12:05:00`; `12:04:59.9` → `12:00:00`), `test_slot_start_converts_aware_to_naive_utc` (`13:07+01:00` → naive `12:05`).
- [ ] **Step 2: Run, expect FAIL** (module missing).
- [ ] **Step 3: Implement `slot_start`.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Write and apply migration 052** to `puzzlebot_test` (sanctioned by AGENTS.md; `python migrate.py` from the worktree with the main checkout's venv).
- [ ] **Step 6: Failing live-DB test** `test_puzzle_activity_slots_against_the_real_schema`: insert guild, hunt (`sheet_mode='files'`, `drive_folder_id='FOLD'`), a second archived files hunt, a tabs hunt, and two puzzles in the first hunt (`channel_id` 1001/1002, `google_sheet_id` `'S1'`/`''`). Assert:
  - `add_chat(1001, 12:01)` twice and `add_chat(1001, 12:06)` → rows `(p1, 12:00, 2, 0)`, `(p1, 12:05, 1, 0)`.
  - `add_chat(424242, 12:01)` (no such puzzle channel) → no new rows anywhere for these puzzles.
  - `mark_sheet_edited('S1', 12:02)` twice → `(p1, 12:00, 2, 1)`; `mark_sheet_edited('', 12:02)` writes nothing for p2.
  - `files_hunt_folders()` contains `(hunt1, 'FOLD')` and neither the archived nor the tabs hunt.
  - Deleting p1 removes its slot rows (cascade).
  Tear everything down in `finally`.
- [ ] **Step 7: Implement the class and wiring; run Step 6, expect PASS.**
- [ ] **Step 8: Commit** — `feat(activity): 5-minute activity slots per puzzle (migration 052)`.

### Task 2: Count chat into slots

**Files:**
- Modify: `bot/cogs/activity.py`
- Test: `tests/test_activity_cog_slots.py`

**Interfaces:**
- Consumes: `PuzzleActivityDb.add_chat(channel_id, at)` (Task 1).

- [ ] **Step 1: Failing tests** (drive `_drain` directly with a queue of one record, patching `bot.cogs.activity.ChannelActivityDb` and `bot.cogs.activity.PuzzleActivityDb`; stop the loop after one item):
  - `test_drain_records_last_message_and_counts_slot` — both `ChannelActivityDb.record(**record)` and `PuzzleActivityDb.add_chat(record["channel_id"], record["at"])` are called once.
  - `test_slot_failure_does_not_block_last_message` — `add_chat` raising still leaves `record` called and the worker alive for the next item; and vice versa (`record` raising still calls `add_chat`).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** — after the existing `ChannelActivityDb.record` call in `_drain`, call `PuzzleActivityDb.add_chat` via `asyncio.to_thread` in its own `try/except Exception: logger.exception(...)`. Update the class docstring and the queue-full comment: a dropped record now also loses one chat count, which is acceptable at the 10 000 cap.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(activity): count human chat into puzzle activity slots`.

### Task 3: Sheet-edit poller (files hunts)

**Files:**
- Modify: `bot/cogs/puzzles_gsheet.py` (new method beside `list_folder`)
- Create: `bot/cogs/sheet_activity.py` (auto-discovered extension: module-level `async def setup(bot)`, see `bot/__init__.py:extensions`)
- Test: `tests/test_drive_requests.py` (add), `tests/test_sheet_activity_poller.py`

**Interfaces:**
- Consumes: `SheetActivityDb.files_hunt_folders()`, `SheetActivityDb.mark_sheet_edited(id, at)` (Task 1).
- Produces: `GoogleSheets.list_modified_since(folder_id: str, since: datetime.datetime) -> list[dict]` — non-trashed children with `modifiedTime > since` (RFC 3339 UTC in the `q`), fields `nextPageToken, files(id, mimeType, modifiedTime, lastModifyingUser(me))`, all pages, same retry wrapper as `list_folder`.
- Produces: cog `SheetActivity` with `@tasks.loop(minutes=5) poll` and `async def poll_once(self, now: datetime) -> None` (the testable body; the loop calls it with `datetime.now(timezone.utc)`). Start the loop in `cog_load`, cancel in `cog_unload`. Get the gsheet cog via `self.bot.get_cog("GoogleSheets")` at tick time; if absent, skip the tick.

`poll_once` rules: for each `(hunt_id, folder)` from `files_hunt_folders()` (via `asyncio.to_thread`): list files modified since `now − 10 min`; for each file with `mimeType == "application/vnd.google-apps.spreadsheet"` and not `file.get("lastModifyingUser", {}).get("me")`: `mark_sheet_edited(file["id"], <parsed modifiedTime>)` via `asyncio.to_thread`. Any exception for one hunt is logged (with hunt id) and the next hunt proceeds.

- [ ] **Step 1: Failing Drive test** `test_list_modified_since_filters_and_pages` (mirror `test_list_folder_follows_pages_and_excludes_trashed`): two pages concatenated; `q` contains `'FOLD' in parents`, `trashed=false`, and `modifiedTime > '2026-11-01T12:00:00Z'` for `since = 2026-11-01 12:00 UTC`; `fields` includes `modifiedTime` and `lastModifyingUser(me)`.
- [ ] **Step 2: Failing poller tests** (fake gsheet cog with an async `list_modified_since`; patch `bot.cogs.sheet_activity.SheetActivityDb`):
  - `test_marks_human_and_anonymous_edits_not_bot_edits` — files: `{lastModifyingUser: {me: False}}`, no `lastModifyingUser`, `{me: True}`, and a non-spreadsheet → `mark_sheet_edited` called exactly for the first two, with their parsed `modifiedTime`.
  - `test_looks_back_ten_minutes` — `since` passed is `now − 10 min`.
  - `test_one_hunt_failing_does_not_stop_the_next` — first folder's list raises `HttpError`/`Exception`; second hunt's edits still marked; nothing raised.
  - `test_no_files_hunts_makes_no_drive_calls`.
- [ ] **Step 3: Run, expect FAIL.**
- [ ] **Step 4: Implement** `list_modified_since` and the cog.
- [ ] **Step 5: Run, expect PASS.**
- [ ] **Step 6: Commit** — `feat(activity): poll files-hunt Drive folders for human sheet edits`.

### Task 4: Sparkline data in the puzzle payload

**Files:**
- Create: `bot/core/activity_sparkline.py`
- Modify: `bot/web/data.py:get_hunt_puzzles`
- Test: `tests/test_activity_sparkline.py`, `tests/test_hunt_puzzles_payload.py` (extend `_Dispatcher`)

**Interfaces:**
- Consumes: `SLOT_MINUTES` (Task 1).
- Produces: `squash(slots: list[tuple[datetime, int, bool]], start: datetime, now: datetime, max_bars: int = 36) -> dict` returning `{"bar_minutes": float, "bars": [{"chat": int, "edited": bool}, ...], "last_sheet_edit_at": datetime | None}`.
  - `n = clamp(ceil((now − start) / 5 min), 1, max_bars)`; bar `i` covers `[start + i·span/n, start + (i+1)·span/n)`; each slot goes to the bar containing its `slot_start`, clamped into `[0, n−1]` (a slot starting just before `start`, or at/after `now`, lands in the end bar). `bar_minutes = span_minutes / n`.
  - `last_sheet_edit_at` = latest edited `slot_start`, else `None`.
- Produces in each top-level puzzle of the `get_hunt_puzzles` payload: `"activity": {"bar_minutes": float, "bars": [...], "last_sheet_edit_at": iso | None} | None` — `None` when solved, or when `start_time` is null and there are no slots. If `start_time` is null but slots exist, use the earliest slot as start.

- [ ] **Step 1: Failing pure tests:**
  - `test_three_minute_old_puzzle_gets_one_bar` — start 12:00, now 12:03, slot (12:00, 2, False) → 1 bar `{chat: 2, edited: False}`.
  - `test_thirty_hour_puzzle_gets_36_bars_and_conserves_counts` — slots spread across the span: `sum(bar.chat) == sum(slot.chat)` and `len(bars) == 36`; a slot near the start lands in bar 0, one near the end in bar 35.
  - `test_shape_given_up` — 6-hour puzzle, chat only in the first hour → nonzero bars only at the front, zeros after.
  - `test_edited_flag_and_last_edit` — two edited slots at different times → their bars `edited: True`, `last_sheet_edit_at` = the later one.
  - `test_slot_before_start_clamps_to_first_bar` (start 12:02, slot 12:00).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement `squash`.**
- [ ] **Step 4: Extend `get_hunt_puzzles`** (top-level puzzles only — minis are recorded but not drawn; the mini panel is out of scope) with one extra `fetch_all`: `SELECT s.puzzle_id, s.slot_start, s.chat_count, s.sheet_edited FROM puzzle_activity_slots s JOIN puzzles p ON p.id = s.puzzle_id WHERE p.hunt_id = %s AND p.solved = 0 AND p.parent_puzzle_id IS NULL ORDER BY s.slot_start`; group by puzzle; attach `activity` (use naive-UTC `now`, matching stored times; `_iso` for the timestamp). In `tests/test_hunt_puzzles_payload.py`, add a `slots=()` argument and a `"FROM puzzle_activity_slots"` branch to `_Dispatcher`.
- [ ] **Step 5: Failing-then-passing payload tests** in `tests/test_hunt_puzzles_payload.py`:
  - `test_unsolved_puzzle_carries_activity_bars` — one puzzle with `start_time`, two slot rows → `activity.bars` non-empty, chat sum 3, `last_sheet_edit_at` set from the edited slot.
  - `test_solved_puzzle_has_no_activity` — solved row → `activity is None`.
  - `test_no_start_no_slots_is_none`.
- [ ] **Step 6: Run the payload file and full suite, expect PASS.**
- [ ] **Step 7: Commit** — `feat(activity): squash activity slots into sparkline bars in the puzzle payload`.

### Task 5: Draw it on the dashboard

**Files:**
- Modify: `frontend/src/types.ts` (`Puzzle.activity`), `frontend/src/components/PuzzleTable.tsx` (Activity column)
- Create: `frontend/src/lib/sparkline.ts`, `frontend/src/lib/sparkline.test.ts`, `frontend/src/components/ActivitySparkline.tsx`
- Modify: `docs/dashboard.md`, `docs/user-guide.md` (where the Activity column is described)

**Interfaces:**
- Consumes: payload `activity` (Task 4).
- Produces: `barHeight(chat: number, barMinutes: number): number` (0–1): `rate = chat / barMinutes` (messages per minute); `0` when `chat == 0`; else `Math.min(1, Math.sqrt(rate / 1.0))` — a shared scale across puzzles, so a trickle stays short and a busy burst is tall (per-puzzle normalisation would make a trickle look busy). `latestActivity(liveAt, lastChatAt, lastEditAt): string | null` — the latest of the non-null ISO strings.

- [ ] **Step 1: Failing vitest** — `barHeight(0, 5) === 0`; `barHeight(5, 5) === 1`; `barHeight(1, 60)` ≈ `Math.sqrt(1/60)`; `barHeight(1000, 5) === 1`; a 5-message bar over 60 min is shorter than over 5 min. `latestActivity` picks the latest of three, ignores nulls, returns null when all null.
- [ ] **Step 2: Run, expect FAIL.** Implement `sparkline.ts`. Run, expect PASS.
- [ ] **Step 3: `ActivitySparkline`** — inline SVG, fixed width ~72px × 16px, bars evenly spaced across the full width whatever their count. Chat bars in the theme's dimmed/blue colour; a bar with `edited` drawn in an orange accent, at a minimum visible height (2px) when its chat is 0. An SVG `<title>` summarising it (e.g. "12 messages · sheet edited in 3 slots · since 5h ago"). Renders nothing when `activity` is null.
- [ ] **Step 4: Activity column** — render `<ActivitySparkline>` before the relative time; compute the time with `latestActivity(liveHit?.at, p.last_activity?.at, p.activity?.last_sheet_edit_at)`.
- [ ] **Step 5: Docs** — one or two sentences where the Activity column is described: what the bars and the orange marks mean, that sheet edits are files-mode only, and that history starts when the feature shipped.
- [ ] **Step 6: Gate** — `npx tsc --noEmit && npx vitest run` clean; look at the component by eye (tsc-only).
- [ ] **Step 7: Commit** — `feat(dashboard): activity sparkline in the puzzle list`.

## Verification (after Task 5)

Full pytest, ruff, basedpyright, tsc, vitest — all clean. Manual (test instance, after merge and push): chat in a puzzle channel shows bars within ~30 s (list refetch); editing a files-mode puzzle sheet shows an orange mark within ~5 min; a bot solve does not; solved puzzles show no sparkline.

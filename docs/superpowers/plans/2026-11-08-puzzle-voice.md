# Puzzle Voice Channels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A voice channel per hunt, on-demand (or automatic) voice channels per puzzle, warned-and-moved clean-up after solve / on admin request, and live voice presence on the dashboard.

**Architecture:** One ctx-free core module `bot/core/voice.py` owns every Discord voice operation (ensure hunt channel, ensure voice category, create puzzle voice, close-with-warning, cleanup, solved sweep). An in-memory `bot/core/voice_presence.py` tracks member counts per voice channel and publishes `voice` events on the existing activity bus. A new cog `bot/cogs/voice.py` holds the bang commands, the voice-state listener and the sweep loop; `PuzzleService` calls the core at hunt/puzzle create and puzzle delete; web routes and the SPA call the same core.

**Tech Stack:** Python 3.12/3.13, discord.py, Quart, MySQL/MariaDB migrations, pytest (asyncio_mode=auto); React + Mantine + TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-11-08-puzzle-voice-design.md` (read it first; it is the authority).

## Global Constraints

- Branch `voice` (worktree `.claude/worktrees/voice`), based on local `staging`. Never push; the controller merges.
- Tests: `timeout 400 pipenv run python -m pytest tests/ -q > <file> 2>&1` (no `-n`; xdist is not installed). Type check: `pipenv run basedpyright bot/` → 0 errors. Lint: `pipenv run ruff check bot tests`. Frontend: `cd frontend && npx tsc --noEmit -p . && npx vitest run`.
- Migration number **059** (058 is reserved by branch `drop-dead-columns`). No MariaDB-only syntax (CI is MySQL 8): no `ADD COLUMN IF NOT EXISTS`.
- Hunt-membership/visibility for any new web route reuses the existing helpers (`_visible_hunt_by_uid`, `_route_ctx`, `require_admin`); every new route gets a census entry in `tests/test_web_route_gating.py`. Mutating routes are covered by the existing CSRF `before_request` guard (nothing to add).
- Admin-only actions (toggle, cleanup) use `is_admin()` on bang commands and `require_admin` on web routes. `!voice` / "Create voice channel" are open to any hunt member who can see the puzzle.
- Exact names (use verbatim): command `voice` alias `vc`; command `voice_cleanup`; command `voice_per_puzzle` (arg `on`/`off`, none = report); hunt columns `voice_channel_id`, `voice_per_puzzle`; event type `"voice"`.
- Timings (verbatim): clean-up due at `solve_time + 15 minutes`; warning lead `15` seconds; sweep loop every `20` seconds.
- Warning text posted in the voice channel's own chat (exact):
  `@here this voice channel closes in 15 seconds — {reason}. Anyone still here will be moved to {hunt_voice_mention}.`
  with `reason` = `"**{puzzle name}** was solved"` (sweep) or `"an admin is tidying up voice channels"` (cleanup); send with `allowed_mentions=discord.AllowedMentions(everyone=True)`.
- Voice category name: `"{hunt.name} Voice"`, then `"{hunt.name} Voice 2"`, `"… Voice 3"`; truncate to 100 chars. Hunt voice channel name: `"{hunt.name} voice"` (truncate 100). Puzzle voice channel name: the puzzle name (truncate 100).
- Discord-side failures in voice work never fail hunt or puzzle creation, solve, or delete: log and carry on (the same "best-effort" style as `DiscordNotifier.welcome`).
- Never create a voice category with no overwrites: it is always `hunt_category.clone(name=…)`.
- Don't touch the forum/dead-column work or anything outside this feature.

## Review Focus

1. **Restart inside the 15 s warning window** — the channel still has its id stored, so the next sweep must warn again before deleting; an occupied channel is never deleted without a warning first. Test in Task 3 (`test_close_warns_again_after_restart_mid_window` — close is idempotent and always warns when occupied).
2. **Double close** — the 20 s sweep firing again while a 15 s close is still in flight must not post a second warning or double-delete. Test in Task 3 (`test_sweep_skips_channels_already_closing`).
3. **Voice channel deleted by hand in Discord** — stored id resolves to nothing: `!voice` makes a fresh one; sweep/cleanup just clear the id. Tests in Task 2 and Task 3.
4. **Unsolve inside the 15 minutes, then re-solve** — unsolve clears `solve_time` (verified: `puzzle_service.unsolve` sets it to None), so the sweep query must require `solved = 1 AND solve_time IS NOT NULL`; a re-solve restarts the clock. Test in Task 3 (`test_sweep_query_ignores_unsolved_and_recent`).
5. **Move fails** (member left, lacks perms, bot lacks Move Members) — each move is individually try/except'd; deletion still happens. Test in Task 3 (`test_close_deletes_even_if_a_move_fails`).

---

### Task 1: Schema and store

**Files:**
- Create: `migrations/059_voice_channels.sql`
- Modify: `bot/store/hunt_data.py` (HuntData fields)
- Modify: `bot/store/mysqldb.py` (narrow setters + sweep query)
- Test: `tests/test_voice_store.py`

**Interfaces:**
- Produces:
  - `HuntData.voice_channel_id: int = 0`, `HuntData.voice_per_puzzle: bool = False`
  - `HuntJsonDb.set_voice_channel(hunt_id: int, channel_id: int | None) -> None`
  - `HuntJsonDb.set_voice_per_puzzle(hunt_id: int, on: bool) -> None`
  - `PuzzleJsonDb.set_voice_channel(puzzle_id: int, channel_id: int | None) -> None`
  - `PuzzleJsonDb.voice_due(cutoff: datetime.datetime) -> list[PuzzleData]` — puzzles in non-archived hunts with `voice_channel_id` non-null/non-zero, `solved = 1`, `solve_time IS NOT NULL AND solve_time <= cutoff`.
  - `PuzzleJsonDb.with_voice(hunt_id: int) -> list[PuzzleData]` — every puzzle of the hunt with a voice channel id.

- [ ] **Step 1: Write the migration.** Header comment in the style of `migrations/056_archive_category.sql` (what, why, `PRE-FLIGHT: none needed. Additive columns with defaults.`). Body:
  ```sql
  ALTER TABLE `hunts`
    ADD COLUMN `voice_channel_id` BIGINT UNSIGNED NULL DEFAULT NULL,
    ADD COLUMN `voice_per_puzzle` TINYINT(1) NOT NULL DEFAULT 0;
  ```
  (`puzzles.voice_channel_id` already exists in `000_base_schema.sql`.)
- [ ] **Step 2: Add the two HuntData fields** after `roster_role_id`. Check `HuntData.import_dict` maps a NULL `voice_channel_id` to `0`/None consistently with `role_id` (follow whatever `role_id` does).
- [ ] **Step 3: Write failing tests** in `tests/test_voice_store.py` against the real test DB the suite already uses (see how `tests/test_migrate.py` / other store tests reach it; follow `docs/agents/python.md` and AGENTS.md test conventions):
  - `test_set_voice_channel_round_trips_and_clears` — puzzle: set 123 → reread → 123; set None → reread → falsy.
  - `test_hunt_voice_fields_round_trip` — hunt: `set_voice_channel(…, 456)`, `set_voice_per_puzzle(…, True)` → reread both.
  - `test_sweep_query_ignores_unsolved_and_recent` — four puzzles in one live hunt, all with voice ids: solved 20 min ago (due), solved 5 min ago (not), unsolved with stale solve_time NULL (not), solved 20 min ago but no voice id (not); plus one due puzzle in an archived hunt (not). `voice_due(now - 15min)` returns exactly the first.
  - `test_with_voice_lists_only_this_hunts_voice_puzzles` — two hunts, assert ids.
- [ ] **Step 4: Run them** — expect failures (missing methods/columns). Apply the migration the way the test DB setup does (read `tests/conftest.py` / `migrate.py`).
- [ ] **Step 5: Implement** the setters as single `UPDATE … SET voice_channel_id=%s WHERE id=%s` statements (narrow on purpose: a full `commit()` of a stale object could overwrite concurrent edits — see the long docstring on `MySQLPuzzleJsonDb.commit`). Queries return objects through the same import path other `get_*` methods use.
- [ ] **Step 6: Run tests → pass; run the full suite, basedpyright, ruff.**
- [ ] **Step 7: Commit** `feat(voice): schema and store for hunt/puzzle voice channels`.

### Task 2: Core voice operations — create

**Files:**
- Create: `bot/core/voice.py`
- Test: `tests/test_voice_core.py`

**Interfaces:**
- Consumes: Task 1 store methods; `discord_channels.MAX_CATEGORY_CHANNELS`.
- Produces (all in `bot/core/voice.py`):
  - `class VoiceError(Exception)` with `.code: str` in {`"no_hunt_category"`, `"solved"`, `"archived"`, `"not_a_puzzle"`}.
  - `voice_category_name(hunt_name: str, n: int) -> str` (`n == 1` → `"{name} Voice"`, else `"{name} Voice {n}"`, ≤100 chars).
  - `async ensure_hunt_voice(guild, hunt) -> discord.VoiceChannel | None` — returns the stored channel if it resolves; else creates `"{hunt.name} voice"` in the hunt's main category (`guild.get_channel(hunt.category_id)`; if that category is missing or full (`len(channels) >= MAX_CATEGORY_CHANNELS`) return None and log), stores the id via `HuntJsonDb.set_voice_channel`, sets `hunt.voice_channel_id`, returns it.
  - `async ensure_voice_category(guild, hunt) -> discord.CategoryChannel` — the first existing category named `voice_category_name(hunt.name, n)` for n = 1, 2, … with room; else `await hunt_category.clone(name=voice_category_name(hunt.name, next_n))`. Raises `VoiceError("no_hunt_category")` if `guild.get_channel(hunt.category_id)` is None.
  - `async create_puzzle_voice(guild, hunt, puzzle) -> tuple[discord.VoiceChannel, bool]` — `(channel, created)`. Refuses: `hunt.archived` → `VoiceError("archived")`; `puzzle.solved` → `VoiceError("solved")`. Existing stored channel that resolves → `(it, False)`. Otherwise calls `ensure_hunt_voice` (first-need creation, spec goal 1), `ensure_voice_category`, `guild.create_voice_channel(name[:100], category=cat)`, `PuzzleJsonDb.set_voice_channel(puzzle.id, ch.id)`, sets `puzzle.voice_channel_id`, returns `(ch, True)`.
  - `def voice_url(guild_snowflake: int, channel_id: int) -> str` → `f"https://discord.com/channels/{guild_snowflake}/{channel_id}"`.

- [ ] **Step 1: Write failing tests** (fake guild/category/channel objects as `MagicMock`/`SimpleNamespace` with explicit `.id`, `.name`, `.channels = []`, `AsyncMock` for `create_voice_channel` / `clone`; patch store at `bot.core.voice.HuntJsonDb` / `bot.core.voice.PuzzleJsonDb`):
  - `test_category_names` — `("Big Hunt",1)` → `"Big Hunt Voice"`, `("Big Hunt",2)` → `"Big Hunt Voice 2"`, 200-char name → length 100.
  - `test_ensure_hunt_voice_reuses_a_live_channel` / `test_ensure_hunt_voice_recreates_when_deleted_by_hand` (stored id → `get_channel` None → creates, stores new id).
  - `test_voice_category_is_cloned_from_the_hunt_category` — asserts `hunt_category.clone.await_args.kwargs["name"] == "Big Hunt Voice"` and that `guild.create_category` is never called.
  - `test_full_voice_category_overflows_to_voice_2` — existing "Big Hunt Voice" with 50 channels → clone named "Big Hunt Voice 2"; and with "Big Hunt Voice 2" existing with room → reused, no clone.
  - `test_missing_hunt_category_refuses` — `VoiceError` code `no_hunt_category`, nothing created.
  - `test_create_puzzle_voice_refuses_solved_and_archived` (two cases, codes asserted).
  - `test_create_puzzle_voice_returns_existing` (no create call) and `test_create_puzzle_voice_creates_hunt_voice_first_if_missing` (hunt has no voice id → hunt voice created, then puzzle voice; both ids stored).
- [ ] **Step 2: Run → fail. Step 3: Implement. Step 4: Run → pass; full suite, basedpyright, ruff.**
- [ ] **Step 5: Commit** `feat(voice): core create operations`.

### Task 3: Core voice operations — close, cleanup, sweep

**Files:**
- Modify: `bot/core/voice.py`
- Test: `tests/test_voice_close.py`

**Interfaces:**
- Consumes: Task 1 (`voice_due`, `with_voice`, setters), Task 2 (`ensure_hunt_voice`).
- Produces:
  - `WARNING_SECONDS = 15`, `SOLVED_GRACE = datetime.timedelta(minutes=15)`.
  - `async close_voice_channel(guild, hunt, channel, reason: str, *, sleep=asyncio.sleep) -> None` — if `channel.members`: post the Global-Constraints warning in `channel.send(...)` (`hunt_voice_mention` = `target.mention` where `target = await ensure_hunt_voice(guild, hunt)`; if that returns None use the text `"the hunt's voice channel"` and skip the moves — the deletion disconnects them), `await sleep(WARNING_SECONDS)`, then for each member still in `channel.members` `await member.move_to(target)` each in its own try/except (log). Then `await channel.delete(reason=…)` (try/except `discord.NotFound` → fine). Does not touch the DB.
  - `_closing: set[int]` module-level guard of channel ids currently inside `close_voice_channel`; the function returns immediately if the id is already in it, and removes it in `finally`.
  - `async close_puzzle_voice(guild, hunt, puzzle, reason: str, *, sleep=asyncio.sleep) -> bool` — resolves `puzzle.voice_channel_id`; if gone, just clears the id; else closes then clears the id (`PuzzleJsonDb.set_voice_channel(puzzle.id, None)`). Returns True if a channel was deleted.
  - `async cleanup_hunt(guild, hunt, *, sleep=asyncio.sleep) -> list[str]` — closes every puzzle voice channel of the hunt (`with_voice`) **concurrently** (`asyncio.gather`, so occupied ones share one 15 s wait) with reason `"an admin is tidying up voice channels"`; also deletes channels left in the hunt's voice categories that aren't the hunt voice channel or any stored puzzle id (orphans from deleted rounds), via the same `close_voice_channel`; then deletes any of the hunt's voice categories left empty. Never touches `hunt.voice_channel_id`. Returns the names of removed channels.
  - `async sweep_solved(bot, *, now=None, sleep=asyncio.sleep) -> None` — `voice_due(now - SOLVED_GRACE)`, for each: resolve hunt and guild (skip if the guild isn't available), `close_puzzle_voice(..., reason=f"**{puzzle.name}** was solved")`, concurrently; each in its own try/except.
  - `async delete_puzzle_voice_now(guild, puzzle) -> None` — no warning; deletes the channel if it resolves (used when the puzzle itself is deleted).

- [ ] **Step 1: Write failing tests** (pass `sleep=AsyncMock()` so nothing really waits):
  - `test_empty_channel_is_deleted_without_a_warning` — no `send`, `delete` awaited.
  - `test_occupied_channel_warns_then_moves_then_deletes` — exact warning string (with `**Cap One** was solved` and the hunt voice mention), `sleep` awaited with 15, each member's `move_to` awaited with the hunt voice channel, then `delete`; assert call ORDER with a shared `MagicMock` manager (`attach_mock`) — warning before sleep before moves before delete.
  - `test_close_deletes_even_if_a_move_fails` — one member's `move_to` raises `discord.HTTPException`; the other is still moved; delete still awaited.
  - `test_close_warns_again_after_restart_mid_window` — call `close_puzzle_voice` twice on fresh state for the same occupied channel (simulating a restart; `_closing` empty each time) → each call warns before deleting.
  - `test_sweep_skips_channels_already_closing` — start one close with a `sleep` that blocks on an `asyncio.Event`, run `sweep_solved` for the same puzzle meanwhile → exactly one `send` and one `delete` after the event is released.
  - `test_close_puzzle_voice_clears_id_when_channel_was_deleted_by_hand`.
  - `test_cleanup_never_touches_the_hunt_voice_channel_and_removes_empty_categories` — hunt voice in main category untouched; two puzzle channels + one orphan in "Big Hunt Voice" deleted; the category's `delete` awaited; returns the three names.
  - `test_cleanup_occupied_channels_share_one_wait` — two occupied channels; with a counting `sleep`, the total elapsed simulated waits ran concurrently (assert both warnings were sent before either `sleep` returned — use an Event-gated sleep).
- [ ] **Step 2: Run → fail. Step 3: Implement. Step 4: Run → pass; full suite, basedpyright, ruff.**
- [ ] **Step 5: Commit** `feat(voice): warned close, cleanup and solved sweep`.

### Task 4: Presence tracking

**Files:**
- Create: `bot/core/voice_presence.py`
- Test: `tests/test_voice_presence.py`

**Interfaces:**
- Produces:
  - `count(channel_id: int) -> int`
  - `snapshot(guilds) -> None` — resets and fills counts from every `guild.voice_channels[*].members` (non-bot members only).
  - `on_state_change(member, before_channel, after_channel) -> list[int]` — updates counts, returns the voice channel ids whose count changed (0, 1 or 2 ids; none if `before_channel == after_channel`, e.g. mute toggles); bots ignored.
  - `event_for(voice_channel_id: int) -> dict | None` — resolves the owning row: a puzzle with that `voice_channel_id` → `{"type": "voice", "guild_id": <same guild key the activity events use>, "channel_id": str(puzzle.channel_id), "voice_count": n}`; a hunt with that `voice_channel_id` → same with `"channel_id": str(hunt.channel_id)` and `"hunt_uid": hunt.uid`; anything else → None. Look up `guild_id`'s form in `bot/cogs/activity.py`'s `events.publish({...})` and copy it exactly, so the websocket's membership filter in `bot/web/api.py` treats it the same.

- [ ] **Step 1: Failing tests:** `test_snapshot_counts_humans_only`; `test_move_between_channels_changes_both` (returns both ids; counts 1→0 and 0→1); `test_mute_toggle_changes_nothing` (same channel → `[]`); `test_event_for_puzzle_and_hunt_and_unknown` (store patched at `bot.core.voice_presence.*`).
- [ ] **Step 2–4: run/implement/run; full suite, basedpyright, ruff. Step 5: Commit** `feat(voice): in-memory voice presence`.

### Task 5: Voice cog — commands, listener, sweep loop

**Files:**
- Create: `bot/cogs/voice.py` (module-level `async def setup(bot)` so `bot/__init__.py:extensions()` loads it)
- Test: `tests/test_voice_cog.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: commands `!voice` (alias `!vc`), `!voice_cleanup`, `!voice_per_puzzle [on|off]`; listener `on_voice_state_update`; `on_ready` → `voice_presence.snapshot(bot.guilds)`; `tasks.loop(seconds=20)` calling `voice.sweep_solved(bot)` (start it in `cog_load`, cancel in `cog_unload`; follow `bot/cogs/sheet_activity.py`'s loop pattern incl. `before_loop` waiting for ready).

Behaviour:
- `!voice`/`!vc`: resolve hunt + puzzle from `ctx.channel` the same way existing puzzle-channel commands in `bot/cogs/puzzles.py` do (find the helper `!solve` uses; reuse it, don't write a new lookup). Not a puzzle channel → `":x: Run this in a puzzle's channel."`. `VoiceError` codes → `solved`: `":x: This puzzle is solved — no new voice channel."`, `archived`: `":x: This hunt is archived."`, `no_hunt_category`: `":x: I can't find this hunt's category to copy permissions from, so I won't make a voice channel."`. Success: `":loud_sound: Voice for **{name}**: {channel.mention}"` (same text whether created or existing).
- `!voice_cleanup` (`@is_admin()`, in any channel of the hunt — resolve the hunt from the channel like other hunt-scoped admin commands): reply `":broom: Clearing puzzle voice channels…"` first (occupied ones take 15 s), then `":white_check_mark: Removed {n} voice channel(s): {names}"` or `"No puzzle voice channels to remove."`.
- `!voice_per_puzzle` (`@is_admin()`, hunt resolved from channel): `on`/`off` → `HuntJsonDb.set_voice_per_puzzle`, reply `"Every new puzzle in **{hunt}** now gets a voice channel."` / `"New puzzles in **{hunt}** no longer get a voice channel automatically (existing ones stay; `!voice_cleanup` removes them)."`; no arg → report current state.
- `on_voice_state_update(member, before, after)`: `ids = voice_presence.on_state_change(member, before.channel, after.channel)`; for each id `ev = voice_presence.event_for(id)`; `events.publish(ev)` when not None. Never raises (log).

- [ ] **Step 1: Failing tests** (use `tests.conftest.make_ctx` and the cog-testing helpers AGENTS.md names): each command's success and refusal replies (exact strings above), `voice_per_puzzle` calls the setter with the right bool, the listener publishes one event per changed id and nothing for a mute toggle, a non-admin is refused (check decorator present via the existing pattern other tests use for `is_admin`).
- [ ] **Step 2–4; full suite, basedpyright, ruff. Step 5: Commit** `feat(voice): voice cog`.

### Task 6: Service hooks

**Files:**
- Modify: `bot/core/puzzle_service.py` (`create_hunt`, `create_puzzle`, `delete_puzzle`)
- Test: `tests/test_voice_service_hooks.py`

**Interfaces:**
- Consumes: `voice.ensure_hunt_voice`, `voice.create_puzzle_voice`, `voice.delete_puzzle_voice_now`.

Behaviour:
- `create_hunt`: after the hunt row is committed, set `voice_per_puzzle` from `GuildSettingsDb.get(guild.id).discord_use_voice_channels` (the guild toggle becomes "default for new hunts" — designer's call) via `HuntJsonDb.set_voice_per_puzzle` when true, then `await voice.ensure_hunt_voice(guild, new_hunt)` — both best-effort (try/except, log).
- `create_puzzle`: after the puzzle is committed and its text channel exists, if `hunt.voice_per_puzzle` and the puzzle isn't solved → `await voice.create_puzzle_voice(guild, hunt, new_puzzle)` best-effort. Also for metas.
- `delete_puzzle`: next to the existing `_safe_delete_channel(guild, puzzle.channel_id)`, `await voice.delete_puzzle_voice_now(guild, puzzle)` (best-effort). (Puzzles removed with a whole round leave an orphan voice channel; `cleanup_hunt` sweeps orphans — that is the accepted cover.)

- [ ] **Step 1: Failing tests** with the service test helpers existing `tests/test_create_hunt_service.py` uses: hunt creation calls `ensure_hunt_voice`; a raising `ensure_hunt_voice` doesn't fail creation; guild toggle on → `set_voice_per_puzzle(id, True)`; create_puzzle with `voice_per_puzzle` True → `create_puzzle_voice` awaited, False → not; delete_puzzle → `delete_puzzle_voice_now` awaited. Patch at `bot.core.puzzle_service.voice`.
- [ ] **Step 2–4** — existing service tests may break because mock guilds now reach the DB through the new calls (the organiser-reminder change, 184e84c, hit the same thing): if existing tests start failing because the new calls hit the DB, add an autouse fixture in those files patching `bot.core.puzzle_service.voice` with an `AsyncMock`-backed `MagicMock`, and state each file in the report. Do not weaken any existing assertion. Full suite, basedpyright, ruff.
- [ ] **Step 5: Commit** `feat(voice): hunt voice on creation, auto puzzle voice, delete with puzzle`.

### Task 7: Web API — board fields, routes, events

**Files:**
- Modify: `bot/web/data.py` (board payload), `bot/web/api.py` (ws visibility for `"voice"`; hunt detail fields), `bot/web/actions.py` (routes; `_SETTINGS_KEYS`), `tests/test_web_route_gating.py` (census)
- Test: `tests/test_voice_web.py`

**Interfaces:**
- Produces (JSON):
  - each board puzzle: `"voice_channel_id": str | None`, `"voice_url": str | None`, `"voice_count": int`.
  - `hunt` in hunt detail: `"voice_channel_id": str | None`, `"voice_url": str | None`, `"voice_count": int`, `"voice_per_puzzle": bool`.
  - `POST /api/puzzles/<int:puzzle_id>/voice` → `{"ok": true, "voice_channel_id": str, "voice_url": str, "created": bool}`; errors `{"error": "<VoiceError code>"}` with 409 (`solved`, `archived`, `no_hunt_category`). Same visibility gate as other member-level puzzle actions in `actions.py` (find the route a hunt member uses to e.g. set status; copy its gating).
  - `POST /api/hunts/<uid>/voice-cleanup` → admin (`require_admin`) → `{"ok": true, "removed": [names]}`.
  - `PATCH /api/hunts/<uid>/settings` accepts `"voice_per_puzzle": bool` (add to `_SETTINGS_KEYS`; route is already admin-only); persist via `HuntJsonDb.set_voice_per_puzzle` (or extend `update_hunt_settings` — follow whichever keeps the existing tests honest).
  - Websocket: events with `type == "voice"` are visible iff `_activity_event_visible(event, …)` is (it carries a text `channel_id`). Confirm `_activity_event_visible` accepts a hunt general channel id; if it only knows puzzle channels, extend it for the hunt case with a test.
- [ ] **Step 1: Failing tests:** payload fields present with counts from a patched `voice_presence.count`; create-voice route success/409 mapping; cleanup route refuses non-admin (403) and returns names; settings PATCH accepts `voice_per_puzzle`; ws: a `voice` event for a hidden channel is not sent, for a visible one is (follow existing ws tests' pattern). Census entries for the two new routes.
- [ ] **Step 2–4; full suite, basedpyright, ruff. Step 5: Commit** `feat(voice): dashboard API`.

### Task 8: Frontend

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/hooks/useActivityFeed.ts` (or a sibling `useVoiceFeed.ts`), `frontend/src/components/PuzzleDrawer.tsx`, the board row component(s) that render a puzzle on `HuntBoard` (find where `last_activity`/`ActivitySparkline` render), `frontend/src/components/AdminSettingsPanel.tsx`, `frontend/src/pages/GuildSettings.tsx`
- Test: co-located `*.test.tsx` following existing vitest patterns

Behaviour:
- Types: add the Task 7 fields to `Puzzle` and `HuntDetail["hunt"]`; `ActivityEvent` gains `voice_count?: number; hunt_uid?: string`.
- Live counts: a map `text channel_id -> voice_count` from `voice` events, overriding the payload's `voice_count` once an event arrives.
- Board: a puzzle with `voice_count > 0` is visibly marked — a headphones icon (`@tabler/icons-react` `IconHeadphones`, already a dependency if tabler is used; else Mantine-compatible icon set in use) with the count, and a tinted row/card background using the theme's teal light colour. Hunt-level count shown next to the hunt name/header when > 0, linking to `hunt.voice_url`.
- Drawer: no voice channel and not solved → button "Create voice channel" (calls the POST, shows a notification, invalidates `["hunt", uid]`); has one → anchor "Join voice" to `voice_url` (`target="_blank"`), with the live count if > 0. Error codes map to readable text in `api.ts`'s error switch: `solved` "This puzzle is solved — no new voice channel.", `no_hunt_category` "Can't find the hunt's category to copy permissions from."
- AdminSettingsPanel (admins only — it already is): checkbox "Voice channel for every new puzzle" bound to `voice_per_puzzle` via the settings PATCH; button "Remove puzzle voice channels" with a confirm modal ("Removes every puzzle's voice channel in this hunt. People still in one get a 15-second warning and are moved to the hunt's voice channel."), then calls voice-cleanup and shows the removed count.
- GuildSettings: relabel the existing `discord_use_voice_channels` control to "New hunts get a voice channel for every puzzle by default".
- [ ] **Step 1: Failing tests** for: drawer shows Create vs Join by state; board marks a puzzle with count > 0 and not one with 0; live event updates the count.
- [ ] **Step 2–4:** `npx tsc --noEmit -p . && npx vitest run` green.
- [ ] **Step 5: Commit** `feat(voice): dashboard voice presence and controls`.

### Task 9: Docs

**Files:** `docs/PLANS.md` (mark "Voice channels: finish or delete" ✅ DONE with this branch's summary), the command help/docs where other commands are listed (grep README.md / docs for `!sheet_mode` to find the list), `.ai/active` handoff untouched (controller-owned).

- [ ] Add `!voice`/`!vc`, `!voice_cleanup`, `!voice_per_puzzle` with one-line descriptions; note the Move Members permission the bot needs for moving people.
- [ ] Commit `docs(voice): commands and roadmap`.

## Verification (controller, after Task 9)

- Full suite, basedpyright, ruff, frontend tsc + vitest.
- Manual on test (after the user pushes staging): new hunt → voice channel; `!vc` in a puzzle → channel in "<Hunt> Voice" with hunt perms; join it → dashboard count/tint; solve → 15 min later warning + move + delete; unsolve within window keeps it; `!voice_cleanup` with someone inside.

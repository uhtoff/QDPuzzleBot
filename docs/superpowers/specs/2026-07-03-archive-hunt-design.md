# Design — `!archive_hunt`: one-shot unattended hunt archive + hardened archive routine

## Goal

A single admin command, `!archive_hunt`, that tidies a finished hunt with no
babysitting: condense **every** channel in the hunt into threads on the hunt's
general channel, delete all the hunt's categories and channels (including voice),
rename the surviving general channel after the hunt + team, and mark the hunt
archived — robustly, without the hangs/jollying the current archive commands need.

The reusable archive logic moves into a new ctx-free `bot/core/` module and the
existing `!archive_round` / `!archive_category` / `!archive_channel` commands are
repointed at it (the "A-full" option), so the fragility fixes apply everywhere and
the duplicated cog logic is removed.

## In scope

- New module `bot/core/hunt_archive.py` — the hardened per-channel archive routine +
  naming helpers (ctx-free, no Google Sheets).
- New `PuzzleService.archive_hunt(...)` orchestrator (sibling to `delete_hunt`).
- New cog command `!archive_hunt` (thin wrapper: confirm → call service → report).
- Migrate `!archive_round` / `!archive_category` / `!archive_channel` to the new
  routine.
- Generalise `_confirm_destructive` so archiving can show accurate (non-"sheets
  deleted") copy.
- Update `docs/commands.md` § Archiving and the migrated command docstrings.

## Out of scope

- `!archive_solved_manually` — it reshuffles the solved category, doesn't replay
  message history, and shares none of the per-channel routine. Left untouched.
- Any web-dashboard equivalent — channel archiving stays **bang-only, by design**
  (see AGENTS.md § Command reference; this command joins that carve-out).
- Deleting or moving Google Sheets. The hunt's sheets are kept.
- Deleting puzzle/tag_group DB rows. They're left inert under the archived hunt.

## Decisions (locked in brainstorming)

| Question | Decision |
|---|---|
| Team name in the rename | Name of the hunt's gating role (`hunt.role_id`); fall back to the invoker's display name when `role_id == 0` or the role can't be resolved. |
| Year in the rename | Prefix `YYYY-MM` (from `hunt.start_time`) to the name **only when it is set**. |
| Reactions | Not copied at all. |
| Voice channels | Deleted outright (no archiving). |
| Quiet | `allowed_mentions=AllowedMentions.none()` + `silent=True` on every send. No permission changes. |
| Data after | `hunt.archived = True` (same flag the web "Archive hunt" sets); rows + sheets kept. |
| Code location | `bot/core/` module + thin cog wrappers; migrate the existing 3 archive commands onto it. |
| Interaction | A single Confirm/Cancel prompt up front; the sweep itself is unattended. |

Final channel name:

- `start_time` set → `"{YYYY-MM} {hunt.name} - {team}"`
- `start_time` unset → `"{hunt.name} - {team}"`

(Discord normalises this via `service.safe_rename` → e.g. `2026-06-hunt-name-team-a`.)

## Constraints

- Follow the project rule "business logic belongs in `bot/core/`" (AGENTS.md §
  Architecture notes). The cog command is a thin, `ctx`-bound wrapper only.
- Reuse existing seams rather than reinventing: `delete_hunt`'s enumeration pattern
  (`puzzle_service.py:2383`), `category_layout.group_categories` /
  `delete_group_categories`, `service.safe_rename`, `_safe_delete_channel`,
  `_confirm_destructive` / `ConfirmView`.
- The sweep must be resilient: one bad channel/attachment/message must never abort the
  whole run.

## Architecture

```
!archive_hunt (cog)                      migrated: !archive_round / _category / _channel (cog)
  confirm (_confirm_destructive,              |
           sheets_deleted=False)              | per-channel replay delegated to ↓
        |                                     |
  PuzzleService.archive_hunt() ───────────────┴──────────────┐
        |  (enumerate + orchestrate, sibling of delete_hunt)  |
        v                                                     v
                    bot/core/hunt_archive.py  (ctx-free, no Sheets)
      resolve_team_name · archived_channel_name · ensure_webhook ·
      thread_name_for_channel · archive_channel_to_thread · archive_and_delete_channel
```

### `bot/core/hunt_archive.py` (new)

- `resolve_team_name(guild, hunt, invoker) -> str`
  - Role name of `hunt.role_id` if resolvable; else `invoker.display_name`.
- `archived_channel_name(hunt, team) -> str`
  - `f"{hunt.start_time:%Y-%m} {hunt.name} - {team}"` when `start_time` set,
    else `f"{hunt.name} - {team}"`.
- `async def ensure_webhook(channel) -> discord.Webhook`
  - Return an existing webhook on `channel`, else create one (name e.g. `"Archiver"`).
    Removes the current manual precondition.
- `thread_name_for_channel(guild, channel, dao) -> str`
  - Puzzle row (`PuzzleJsonDb.get_by_attr(channel_id=channel.id)`) → `"{puzzle.name}
    ({group/round name})"`; else `"{channel.name} ({category.name})"`; truncated to 100.
- `async def archive_channel_to_thread(dest, webhook, source, *, thread_name, on_event=None) -> ChannelArchiveResult`
  - **Archive only, no delete.** Steps (hardened version of today's `archive_channel`):
    1. Send a silent placeholder in `dest`; `create_thread` from it (`thread_name`).
    2. Gather messages: `source.history` + each active thread + each archived thread;
       sort by `created_at` (unchanged from today).
    3. For each message: skip bot messages; skip `!command` messages (except `!s `);
       download attachments with
       `await asyncio.wait_for(a.to_file(use_cached=True), timeout=ATTACH_TIMEOUT)`,
       catching `asyncio.TimeoutError`, `aiohttp.ClientError`, `discord.HTTPException`
       → skip that file only; skip a message that ends up with no content/files/embeds;
       send via `webhook.send(..., thread=thread, wait=True, silent=True,
       allowed_mentions=discord.AllowedMentions.none())`, catching `HTTPException`
       → skip that message only. **No reactions copied.**
    4. Return counts (messages moved / skipped, attachment failures).
- `async def archive_and_delete_channel(dest, webhook, source, *, thread_name) -> ChannelArchiveResult`
  - Calls `archive_channel_to_thread`, then `await source.delete(reason=DELETE_REASON)`
    on success. This is what the migrated `!archive_*` commands call.

Constants: `ATTACH_TIMEOUT` (e.g. 30s), inter-send pacing `SEND_DELAY` (small, e.g.
0.1s — kept as belt-and-braces on top of discord.py's own 429 handling; far fewer
sends now that reactions are gone).

### `PuzzleService.archive_hunt(guild, hunt, invoker, *, on_progress=None) -> HuntArchiveReport`

Sibling to `delete_hunt`; enumerates the same way but archives instead of deleting,
keeps the general channel + sheets, and sets the archived flag.

1. `dest = guild.get_channel(hunt.channel_id)`; `webhook = await ensure_webhook(dest)`.
2. Build the category set the hunt owns, de-duplicated by id:
   - main: `hunt.category_id`
   - solved: `discord.utils.get(guild.categories, name=category_layout.solved_category_name(hunt.name))`
   - every tag group's categories: for `tg in TagGroupJsonDb.get_all(hunt.id)`,
     `category_layout.group_categories(tg, TagGroupCategoryDb)` (handles split
     unsolved/solved/overflow categories).
3. **Sweep every category except the one holding `dest`**, then handle `dest`'s
   category last. For each channel (skipping `dest`):
   - Voice channel → `await _safe_delete_channel(...)`.
   - Text channel → **resume guard:** if a thread named `thread_name_for_channel(...)`
     already exists on `dest` (active or archived), just delete the source (finish an
     interrupted step) and skip re-archiving; else
     `await hunt_archive.archive_and_delete_channel(dest, webhook, ch, thread_name=...)`.
   - Wrap per channel in `try/except`; record failures in the report; never abort.
   - `on_progress(done, total, ch.name)` after each channel.
   - After its channels are gone, delete the category (best-effort, logged).
4. `dest` survives: `await dest.edit(category=None)` (leave it uncategorised — the
   single remaining channel), then delete the now-empty main category.
5. `await service.safe_rename(dest, archived_channel_name(hunt, resolve_team_name(...)))`.
6. `hunt.archived = True; HuntJsonDb.commit(hunt)`.
7. Return `HuntArchiveReport` (channels archived, messages moved, skipped, per-channel
   failures, final channel name).

### Cog `!archive_hunt`

```
@commands.command()
@commands.has_any_role(*ADMIN_ROLES)
async def archive_hunt(self, ctx):
    hunt = self.get_hunt(ctx)                       # guard: must be a hunt channel
    if forum layout: ":x: not available for forum hunts yet"; return
    team = hunt_archive.resolve_team_name(ctx.guild, hunt, ctx.author)
    final = hunt_archive.archived_channel_name(hunt, team)
    desc = (f"Archive hunt **{hunt.name}**? Condense every channel into threads on "
            f"this channel, delete all its categories/channels (including voice), "
            f"rename this channel to **{final}**, and mark the hunt archived. "
            f"Google sheets are kept. Not reversible.")
    if not await self._confirm_destructive(ctx, desc, sheets_deleted=False):
        return
    status = await ctx.send("⏳ Archiving…")
    async def on_progress(done, total, name):
        # throttled edit (e.g. every N or every few seconds)
        await status.edit(content=f"⏳ Archived {done}/{total} — {name}")
    report = await self.service.archive_hunt(ctx.guild, hunt, ctx.author, on_progress=on_progress)
    await ctx.send(report summary)     # channels archived, messages moved, skipped, failures
```

Admins/mods; run in any channel of the hunt; **bang-only** (docstring carries the
"no web-dashboard equivalent, by design" line). Runs inline (awaited) — every `await`
yields, so the bot stays responsive; no background-task machinery.

### Migrating the existing commands (A-full)

- `archive_channel` cog: compute `thread_name` via `thread_name_for_channel`,
  `ensure_webhook(dest)`, then `await hunt_archive.archive_and_delete_channel(...)`.
  Its old inline replay loop is deleted.
- `archive_category` / `archive_round`: unchanged structure (category loop + final
  category delete), but the inner per-channel call goes through
  `archive_and_delete_channel`, and the webhook is ensured once per run and passed down.
- Behaviour change to flag in the commit + docs: these now **auto-create the webhook**,
  **drop reactions**, and **time out stuck downloads** — matching the new routine.

### `_confirm_destructive` generalisation

Add `*, sheets_deleted: bool = True` (all four existing callers keep the default):

- `sheets_deleted=True` → current copy: title "⚠️ Confirm deletion", appends the
  "Google sheets will be permanently deleted" line, timeout says "nothing was deleted".
- `sheets_deleted=False` → title "⚠️ Confirm archive", no sheets warning, timeout says
  "nothing was archived".

## Error handling & resilience

- **Attachments:** per-file timeout + broad catch → never hang on a stuck CDN fetch
  (the current #1 hang). Skip the file, keep the message.
- **Messages:** per-send `HTTPException` catch → skip one message, not the channel.
- **Channels:** per-channel `try/except` in `archive_hunt` → collect failures, continue.
- **Resume/idempotency:** a successfully archived channel is already deleted, so a
  re-run naturally continues; the thread-exists guard prevents a duplicate thread when a
  prior run archived but failed to delete.
- **Reporting:** a final summary message (and `on_progress` status edits) instead of a
  silent hang; failures are named.

## Tests

Project AsyncMock style (`asyncio_mode=auto`; MagicMock for sync attrs, AsyncMock for
awaitables), modelled on `tests/test_delete_hunt_service.py` and
`tests/test_archive_channel.py`.

- `resolve_team_name`: role resolvable → role name; `role_id == 0` → invoker name;
  role id set but deleted → invoker name.
- `archived_channel_name`: `start_time` set → includes `YYYY-MM`; unset → omits it.
- `archive_channel_to_thread`: skips bot messages; skips `!commands`; **copies no
  reactions**; a `to_file` that raises `TimeoutError` skips the attachment but keeps the
  message; a `webhook.send` `HTTPException` skips just that message; sends use
  `allowed_mentions.none()` + `silent=True`.
- `ensure_webhook`: returns an existing webhook; creates one when none exists.
- `archive_hunt`: enumerates main + solved + group categories; archives text channels;
  deletes voice channels; deletes categories; moves `dest` to no category; renames it;
  sets `hunt.archived=True` and commits; a channel that raises is isolated and reported;
  thread-exists resume guard skips re-archiving.
- Update `tests/test_archive_channel.py` for the migrated no-reactions / auto-webhook
  behaviour.

## Staging (reviewable commits)

1. `feat(core): hunt_archive module — hardened per-channel archive routine + naming helpers` (+ unit tests).
2. `feat(core): PuzzleService.archive_hunt orchestrator` (+ tests).
3. `feat(cog): !archive_hunt command with confirm + progress`; generalise `_confirm_destructive`.
4. `refactor(cog): migrate archive_round/category/channel onto hunt_archive` (+ update `test_archive_channel.py`).
5. `docs(commands): add !archive_hunt; note migrated commands drop reactions / auto-webhook`.

## Verification

- `pipenv run python -m pytest --compact` for the new/updated test files (wrapped in
  `timeout`, per test guidance).
- Manual smoke on the test server (`puzzlebot_test`): a small hunt with a couple of
  rounds, a VC, an attachment, a custom-emoji reaction, and an @mention → run
  `!archive_hunt`, confirm, verify: single uncategorised channel remains, correctly
  renamed; threads hold the human messages; no ping fired; no reactions; VC + categories
  gone; hunt shows archived on the dashboard; sheets still present.

## Rollback / recovery notes

- The archive is **not reversible** (channels are deleted after archiving) — this is why
  the confirm exists. Rollback of the *code* is a straight revert of the commits above;
  the migrated commands return to their prior inline implementation.
- If a run fails partway, re-running `!archive_hunt` resumes (already-archived channels
  are gone; the thread-exists guard avoids duplicates).

## Acceptance criteria

- `!archive_hunt`, after a single confirm, turns a multi-category hunt into one
  uncategorised channel named `"{YYYY-MM} {hunt.name} - {team}"` (or without the `YYYY-MM`
  prefix when `start_time` is unset), with every human message preserved as threads, no pings,
  no reactions, voice channels and categories deleted, sheets kept, and
  `hunt.archived == True`.
- The run completes unattended on a large/attachment-heavy/odd-emoji hunt without
  hanging; failures are isolated and reported, not fatal.
- `!archive_round` / `!archive_category` / `!archive_channel` still work, now via the
  shared hardened routine (auto-webhook, no reactions, download timeouts).
- New and updated tests pass.

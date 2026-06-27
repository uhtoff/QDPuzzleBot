# Group channel + forum config — design

Date: 2026-06-27
Status: draft (awaiting review)

## Goal

Two related improvements to round/group creation, on top of the forum-parity fixes
already committed (`912d8e7`, `de6359b`, `4586d3b`):

1. **Group becomes a real, usable channel.** A `Group` has no target puzzle, so it must
   not create one. Instead it gets a general text channel with a welcome message
   explaining it is the group's home for bang commands. `Group` is the canonical
   successor to the clumsy `Metaless Round`.
2. **Forum channels get configured on creation.** When a round/meta/capstone/runaround
   forum is created (forum-mode hunts), set its post guidelines ("rules"), available
   tags, and default reaction emoji — clearing Discord's "set up your forum" prompt and
   making the forum easier to use. Auto-apply the tags to posts and keep both the
   guidelines and the tags in sync as state changes.

Both bang/slash and web routes must produce the same result (web/bang parity principle).

## Decisions (locked with user)

- **Group replaces Metaless Round.** `Group` is canonical. The bang aliases
  `!roundnometa` / `!rnm` and the `!group` command all create type `Group`. `Metaless
  Round` is dropped as a distinct type/welcome. Existing rows whose stored `type` is
  `"Metaless Round"` keep working — they are routed to the Group behaviour everywhere
  (classification, `!info`, welcome).
- **Group structure is mode-dependent:**
  - **Channel mode:** clone the hunt category (inherits the manually-set role overwrite —
    see the hunt-permission seam) so the group has its own category to hold member puzzle
    channels, plus **one general text channel** named after the group with the welcome
    pinned. `tag_group.category_id` = the cloned category id (classified via the existing
    category branch).
  - **Forum mode:** no clone. Create **one general text channel** directly in the hunt
    category (inherits the same overwrites); `tag_group.category_id` = that text channel's
    own id. No forum is created for a Group.
- **No schema migration.** Classification reuses `tag_group.category_id`. For the
  forum-mode group (where `category_id` is the text channel's own id),
  `get_channel_type(channel.id)` already returns `'Group'` because it is tried before the
  category fallback; the only code change is the `'Group'` branch in `cog_before_invoke`
  looking the group up by `ctx.channel.id` first, then `ctx.channel.category.id`.
- **Forum rules** = a plain-text render of the welcome message, re-synced whenever the
  welcome is regenerated.
- **Forum tags** = `solved, meta, capstone, metameta, runaround, minimeta`. Made available
  on every round forum **and auto-applied** to posts (forum mode only).
- **Default reaction emoji** = 👍.
- **Tag precedence** (one structural tag per post): `runaround → metameta → meta →
  minimeta → capstone`, plus `solved` when the puzzle is solved. Feeder (non-target)
  posts get only `solved`.

## Out of scope

- Membership/move semantics. Forum-mode group membership stays tag-based (DB `tags`
  table); `!move_to_*` behaviour is unchanged. The Discord API cannot reparent a thread
  into another channel. **Copy-and-delete is rejected** as a "move": recreating the thread
  in a target forum loses message history, changes the thread id (breaking the stored
  `channel_id`, pins, and links), and re-attributes authorship to the bot. So in forum
  mode a puzzle's *home* thread never moves — group membership is logical/tag-based only,
  which is exactly what the Phase 3 forum tags surface visually.
- Renaming/removing the `!roundnometa` / `!rnm` aliases (kept, just repointed to Group).
- Any change to the meta/round/capstone welcome wording beyond the plain-text render.

## Phase 1 — Group as canonical general channel (bang + web)

### Service — `create_tag_group` (`bot/core/puzzle_service.py`)
- Add a Group branch (`round_type == "Group"`, also matches legacy `"Metaless Round"`):
  - **Channel mode:** clone `ref_category` (as today), then create one text channel named
    `clean_name(name)` in it. `category_id` = cloned category id.
  - **Forum mode:** skip `create_forum`; create one text channel named `clean_name(name)`
    in `ref_category`. `category_id` = the text channel id.
  - Post + pin the group welcome via the injected `notifier` (when present).
  - `create_meta` stays `False`; no `PuzzleData`, no `target_puzzle_id`.
- Group rollback path: delete the created text channel (and the cloned category in channel
  mode) on failure, mirroring the existing round/forum rollback.

### Delete — `delete_round` (`bot/core/puzzle_service.py`)
- Ensure a Group tears down correctly: channel-mode deletes the category (existing path);
  forum-mode deletes the lone text channel referenced by `category_id` (it is a channel,
  not a category — reuse the existing forum `get_channel(...).delete()` shape).

### Welcome posting — single source of truth (the service)
The service becomes the **only** place welcomes are posted, for every type
(meta/round/capstone/group). This removes the web route's manual post-after block and
fixes a latent ordering bug.

- **Ordering fix:** `create_tag_group` currently generates `new_round.group_code`
  (`puzzle_service.py:1554`) *after* `create_puzzle` (`:1549`) already fired the meta
  welcome — so the bang meta welcome renders a **blank** `group_code` (the welcome copy
  embeds `{tag_group.group_code}`). Move `group_code` generation to *before* the
  `create_meta` block so the service-posted welcome always has it. (`target_puzzle_id`
  is not needed by the welcome handler, so it can still be set after `create_puzzle`.)
- After the fix, `create_puzzle` keeps posting the meta welcome (gated on `notifier`),
  now with a populated `group_code`; the Group path posts the group welcome the same way.

### Notifier — `welcome` (`bot/core/notifier.py`)
- Current handler choice keys off `puzzle.is_target`. Add a no-puzzle branch: when
  `puzzle is None` and `tag_group` is a Group → `send_initial_group_channel_messages`.

### Cog (`bot/cogs/puzzles.py`)
- Rename `send_initial_metaless_round_channel_messages` →
  `send_initial_group_channel_messages`; reword copy ("the group's home channel — bang
  commands live here", `!p`, `!list`, `!move_to_round`, `!info`, …). Keep behaviour
  (embed build, `update=True` edits the last pin).
- `cog_before_invoke` `'Group'` branch: look up the group by
  `category_id=ctx.channel.id` first, then `category_id=ctx.channel.category.id`.
- Normalise legacy `"Metaless Round"` → Group routing (treat both strings the same in the
  `'Group'`/type handling).
- `!info` dispatch: add a `Group` branch (also catches `Metaless Round`) →
  `send_initial_group_channel_messages`.
- Bang `command_map`: `roundnometa` / `rnm` → `("Group", False, None)`; `group` already
  `("Group", False, None)`.

### Web — `create_meta` (`bot/web/actions.py`)
- Pass a real `notifier` to `create_tag_group` for **all** types and **delete the manual
  post-welcome block** — the service now owns welcome posting (one source of truth).
- For `group_type == "Group"`: also pass `meta_puzzle_name=False` (no meta); the service
  posts the group welcome.
- The suffix/runaround parity fix already landed; this change replaces the
  `notifier=None` + manual-welcome pattern with `notifier=<DiscordNotifier>`.

### Tests (Phase 1)
- `test_forum_layout.py`: Group in forum mode creates a lone text channel (no
  `create_forum`), `category_id` = channel id; Group welcome posted.
- `test_create_service.py` (or sibling): Group in channel mode clones a category + creates
  one general channel; no `PuzzleData` committed.
- `cog_before_invoke` Group classification by channel id (forum) and by category
  (channel); legacy `"Metaless Round"` routes to the group handler.
- `test_edge_web.py`: web `type=Group` calls `create_tag_group` with
  `meta_puzzle_name=False` and posts the group welcome; no meta created.

## Phase 2 — Forum config at creation + guidelines sync (forum mode only)

### Forum creation (`bot/core/puzzle_service.py` / a small `forum_config` helper)
- When creating a round/meta/capstone/runaround forum, set on `create_forum(...)` (or a
  follow-up `forum.edit(...)`):
  - `available_tags` = six `discord.ForumTag(name=…)` for the tag list.
  - `default_reaction_emoji` = 👍 (`PartialEmoji`/unicode).
  - `topic` (guidelines) = `render_welcome_plaintext(type, group, puzzle, hunt)`.

### Plain-text render (`bot/cogs/puzzles.py` or a shared helper)
- `render_welcome_plaintext(...)` reuses the existing command-bullet copy to produce a
  ≤4096-char markdown string (no embed). Single source of truth shared with the embed
  builders where practical to avoid drift.

### Guidelines sync
- `sync_forum_guidelines(guild, hunt, group, puzzle)`: resolve the forum channel
  (`guild.get_channel(group.category_id)`), recompute the text, `forum.edit(topic=…)` only
  when changed. Call it at the same points the welcome regenerates: `notifier.refresh_info`
  / `!info update` / rename / solve-status change.

### Tests (Phase 2)
- Forum created with the 6 tags, 👍 default emoji, and a non-empty topic.
- `render_welcome_plaintext` snapshot per type (meta/round/capstone/group).
- `sync_forum_guidelines` edits the topic on welcome change and no-ops when unchanged.

## Phase 3 — Auto-apply forum tags + sync (forum mode only)

### Derivation (`bot/core/puzzle_service.py`)
- `desired_tags_for(group, puzzle) -> set[str]`:
  - structural (first match wins): `runaround` if `group.type == "Runaround"`;
    `metameta` if `is_metameta(group, …)`; `meta` if the target is fed; `minimeta` if the
    puzzle is a minimeta parent (has mini children); `capstone` if the target is informed.
  - `solved` if `puzzle.solved`.
  - Feeder (non-target) posts: `solved` only.

### Apply (`forum_config` helper)
- `apply_forum_tags(thread, forum, desired)`: map names → `forum.available_tags`,
  `thread.edit(applied_tags=…)` only when changed.
- Call sites: post create (initial tags), solve / unsolve (toggle `solved`), convert
  (meta↔capstone / runaround — structural tag changes), mini add/remove (`minimeta`).

### Tests (Phase 3)
- `desired_tags_for` truth table across the kinds + solved.
- `apply_forum_tags` maps names to the forum's tag objects and skips a no-op edit.
- Solve/convert flips the applied tag set.

## Risks / notes

- **Category cap (channel mode):** a Group cloning a category consumes one of the 50
  category slots; acceptable (groups are few) and unchanged from rounds.
- **`category_id` overloading:** forum-mode Group stores a *text channel* id in
  `category_id` (already true for forum rounds, which store the forum *channel* id). Delete
  and any `category_id`-based lookups must treat it as "a channel-or-category id", which
  the get-channel paths already do.
- **Guidelines length:** forum `topic` cap is 4096 chars; the render must stay within it
  (the welcome copy is well under).
- **Tag/emoji availability:** forum tags and `default_reaction_emoji` are forum-only;
  Phases 2–3 are strictly guarded by `hunt.layout_mode == "forum"`.

## Sequencing

Phase 1 → 2 → 3, each its own reviewable commit/PR. Phases 2–3 touch only forum-mode
paths and can land after Phase 1 is verified live.

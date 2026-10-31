# Bot command reference

The canonical list of the Discord bot's chat commands — what each one does, its
arguments, where to run it, and who may use it. It mirrors the per-command help text
(the docstrings in `bot/cogs/*.py`, shown by `!help <command>` in Discord); when they
disagree, **the code is the source of truth** — fix both.

- **Prefix:** `!` (configurable via `config.json` → `prefix`). `!help` lists everything;
  `!help <command>` shows usage and examples for one command.
- **Definitions:** commands live in two cogs —
  - `bot/cogs/puzzles.py` — hunts, rounds, puzzles, solving, grouping, archiving.
  - `bot/cogs/utility.py` — bot info/utility.
- **Hybrid commands** (marked *slash* below) also work as Discord slash commands.
- **Permissions** — a guild either *pins* each tier to specific role **ids**
  (`!set_admin_roles` / `!set_organiser_roles`, resolved by `bot/core/role_gate.py`) or,
  until it does, falls back to matching role **names** from `bot/core/constants.py`:
  - *Admins/mods* = roles named `Moderator`, `mod`, `admin`.
  - *Organisers/admins* = the above plus `Organisers`.
  - While a tier is matched by name, anyone who can create a Discord role can grant
    themselves that tier — `!show_auth_roles` says which state you are in.
  - Commands with no role note are open to everyone.
- **`meta_code`** is a TagGroup's 6-character group code (shown on the group's pinned
  info; slash commands autocomplete it). On the bang path you may also pass the group's
  **name** (or a unique part of it) — the resolver matches the code, then the name, scoped
  to the current hunt, and lists candidates when ambiguous. See [AGENTS.md](../AGENTS.md)
  for the TagGroup / feeds / informs data model.

## Permissions at a glance

Every command, grouped by the tier that gates it. The per-command tables below repeat this
in their *Who* column; this one is for answering "what can a solver actually do?" without
reading all of them. Tiers nest — `ORGANISER_ROLES` includes the admin role names
(`bot/core/constants.py`), so an admin passes every organiser check.

| Tier | Area | Commands |
|---|---|---|
| **Anyone** | Create | `!puzzle` (`!p`) · `!make_minimeta` · `!add_mini` · `!add_sheet` |
| | Solve | `!solve` (`!s`) · `!unsolve` (`!u`) · `!mark_as_complete` · `!add_solution` · `!change_solution` · `!partial` (`!ps`) · `!solve_mini` · `!unsolve_mini` |
| | Tag | `!feeds` · `!feeds_only` · `!informs` · `!informs_only` · `!groups_with` · `!groups_only_with` · `!remove_from` |
| | Annotate | `!note` · `!erase_note` · `!clue` · `!clues` · `!link` · `!status` · `!priority` · `!type` |
| | Read | `!info` · `!list_puzzles` (`!list`) · `!list_minis` · `!list_additional_sheets` · `!canvas` · `!info_bot` · `!invite` · `!starttime` · `!uptime` |
| **Organisers/admins** | Create groups | `!round` (`!r`, `!mp`, `!metapuzzle`, `!capstone`, `!cs`, `!runaround`, `!ra`, `!group`) |
| | Rename & delete | `!rename_puzzle` · `!rename_group` · `!delete_puzzle` · `!delete_round` · `!delete_mini` · `!remove_minimeta` · `!remove_sheet` |
| | Restructure | `!convert_to_meta` · `!convert_to_capstone` · `!convert_to_runaround` · `!convert_to_metapuzzle` · `!convert_to_group` · `!convert_to_puzzle` · `!sync` |
| | Category layout | `!extract_target_to_category` · `!consolidate_to` · `!backfill_glyphs` · `!absorb_category` · `!move_to_top` · `!move_to_bottom` · `!move_to_solved` |
| | Maintenance | `!reconcile` · `!rebuild` · `!rebuild_unassigned` · `!list_tags` · `!media_stats` · `!media_sweep` · `!start_reminders` · `!stop_reminders` |
| **Admins/mods** | Hunt lifecycle | `!hunt` (`!h`) · `!delete_hunt` · `!use_solved_sheet` · `!set_hunt_role` · `!setup_server` · `!post_role_panel` · `!send_invites` · `!invite_player` · `!mod_invite` |
| | Settings | `!set_login` · `!set_start` · `!update_settings` · `!show_settings` · `!show_puzzle_settings` |
| | Roles | `!set_admin_roles` · `!set_organiser_roles` · `!show_auth_roles` |
| | Archive | `!archive_hunt` · `!archive_hunt_to_hub` · `!archive_round` · `!archive_category` · `!archive_channel` · `!archive_solved_manually` |
| | Other | `!debug_puzzle_channel` |

Two things the table can't show:

- **The Organiser tier switches itself off.** On a guild with nothing configured or named for
  the Organiser tier, `organiser_gate_active()` is false and every organiser check passes for
  every member — a deliberate convenience for small single-team guilds. The Admin tier is
  never bypassed.
- **The dashboard agrees with this table.** Every operation available on both surfaces
  carries the same tier on each; where only one surface has it, its row says so.

## Hunt management

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!hunt` | `!h` | `name:url[, role][, forum]` | bot channel | admins/mods | Create a hunt: category, general channel, sheet. Optional role gates visibility; append `, forum` for forum-backed layout (a forum channel per round) — **legacy**, being left to go stale in favor of the category-based layout; don't build new group features against it. |
| `!setup_server` | | `hub-id hub-role-id hunt-url hunt name` | a fresh team server | server owner + hub admin | Set up a server just made from the team-server template: checks it matches the template, copies the hub's settings, pins `mod` as the admin role, creates the hunt gated to a team role named after the hub role, records the hub role as the hunt's roster, and posts the `#lobby` welcome with its Join/Leave panel. Re-run to finish a run that stopped partway; once complete it refuses for good on that server. See the [new server playbook](new-server-playbook.md#team-servers-one-per-team-per-hunt). |
| `!post_role_panel` | | `hunt name` | bot channel | admins/mods | Post the `#lobby` Join/Leave panel for a hunt made by `!setup_server` — `!setup_server` already posts one; use this if it was deleted or the server predates the panel. Join is for people holding the hunt's hub role (hub admins also get `mod`); Leave removes both. Old panels keep working. |
| `!send_invites` | | `hunt name` | bot channel | admins/mods | DM a private, single-use invite to this team server to everyone holding the hunt's hub role; they get the team role on arrival. Skips anyone already here or already invited, so re-running is safe. After the first run, anyone given the hub role is DM'd automatically. Names (never links) anyone whose DMs are closed. |
| `!invite_player` | | `user id or name` | bot channel | admins/mods | DM one hub-role holder a fresh invite to this team server; if their DMs are closed, it's DM'd to you to pass on privately. (`!invite` is the bot's own invite link.) |
| `!mod_invite` | | `hunt name` | hub bot channel | admins | Post a reusable, non-expiring invite to that hunt's team server in the hub's admin channel (the hub's `discord_bot_channel`) — for mods who aren't on the team server yet. Refuses if `@everyone` can see that channel (the bot posts nothing there while it is visible). The reply never contains the link; re-running re-posts the same one (or a fresh one if it was deleted). `!setup_server` posts it on a clean finish; `!archive_hunt_to_hub` deletes it when it leaves the team server. |
| `!set_template_link` | | `[link]` | bot channel | admins | Store the hub's team-server template link (accepted forms: `https://discord.new/<code>`, `discord.new/<code>`, `https://discord.com/template/<code>`; stored as the canonical `https://discord.new/<code>`). With no argument it clears the link. Also editable on the dashboard's Server settings. Guild-only. |
| `!show_template_link` | | — | any channel | admins | Post the stored template link, plus a browser fallback (`https://discord.com/template/<code>`) for when the desktop app ignores `discord.new` links, in the hub's admin channel (the hub's `discord_bot_channel`) and nowhere else. Refuses if `@everyone` can see that channel. The reply never contains the link. Guild-only. |
| `!use_solved_sheet` *(slash)* | `!set_to_archive` | — | Hunt channel | admins/mods | Use a separate "solved puzzles sheet": solved feeders move there, metas stay on the main sheet. |
| `!set_login` | | `username:password` | hunt channel | admins/mods | Store the hunt login shown on hunt info. |
| `!set_start` | | `timestamp` (UNIX seconds) | hunt channel | admins/mods | Set the hunt start time. |
| `!set_hunt_role` | | `hunt-name, role-name` | bot channel | admins/mods | Gate an existing OPEN hunt behind a team role (applies to web + Discord); confirms if the role already exists. |
| `!delete_hunt` | | — | Hunt channel | admins/mods | Permanently delete the whole hunt (confirms). Not recoverable. |

## Rounds & groups

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!round` | `!r` | `name` | Hunt channel | organisers/admins | Create a round (also makes a `name - meta` meta puzzle). |
| `!metapuzzle` | `!mp` | `name` | Hunt channel | organisers/admins | Create a metapuzzle. |
| `!capstone` | `!cs` | `name` | Hunt channel | organisers/admins | Create a capstone. |
| `!runaround` | `!ra` | `name` | Hunt channel | organisers/admins | Create a runaround. |
| `!group` | | `name` | Hunt channel | organisers/admins | Create a group (no meta puzzle). |
| `!rename_group` *(slash)* | `!rename_round`, `!rename_metapuzzle` | `new name` | group's target-puzzle or regular group channel | organisers/admins | Rename the group and its Discord category/channel. Does not rename the target puzzle. |
| `!delete_round` | `!delete_metapuzzle`, `!delete_group` | — | group's main channel | organisers/admins | Delete a round/group, its puzzles, channels and sheets (confirms). |
| `!extract_target_to_category` | | — | meta's target-puzzle channel | organisers/admins | Give this meta its own Discord category, moving its target puzzle and the puzzles that feed **only** it into a fresh category. Shared feeders and nested metas stay put. Useful when a meta promoted in place has outgrown the category it was sharing. Bang-only, by design. |
| `!consolidate_to` | | `destination-code` | meta's target-puzzle channel | organisers/admins | Fold this meta back into `<destination>`'s category and delete this meta's own category (frees a category slot). Confirms first if the destination would overflow. Inverse of `!extract_target_to_category`. |

> **Automatic category relief:** when adding a puzzle would fill a category past its limit,
> the bot automatically gives a promoted-in-place meta its own category (rather than failing
> or splitting) — the automatic counterpart to `!extract_target_to_category`. It extracts at
> most one meta per puzzle, and only when doing so frees more than one channel. Toggle/tune
> via `AUTO_EXTRACT_ON_OVERFLOW` and `EXTRACT_MIN_VALUE` in `bot/core/category_layout.py`.

> `!round`/`!r`, `!mp`, `!cs`, `!ra`, `!group` are all aliases of one command;
> the alias you type selects the type created.

## Converting a puzzle's role

All convert commands are *slash* hybrids, run in the puzzle's channel,
organisers/admins only. (They were admins/mods + Manage Channels until 2026-08-08,
levelled to match the dashboard's convert routes — the two surfaces do the same thing
through the same service.)

| Command | Aliases | Does |
|---|---|---|
| `!convert_to_meta` | | Turn a feeder into a metapuzzle others can be tagged to. |
| `!convert_to_capstone` | | Convert a meta into a capstone. |
| `!convert_to_runaround` | | Convert a meta into a runaround. |
| `!convert_to_group` | | Convert a meta into a group (category without a meta puzzle). |
| `!convert_to_metapuzzle` | | Convert a capstone/runaround/group back into a plain metapuzzle (feeders revert to the `feeds` edge). |
| `!convert_to_puzzle` | `!convert_to_feeder` | Convert a meta back into an ordinary feeder. |

## Puzzles

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!puzzle` | `!p` | `name` | hunt/round | anyone | Create a puzzle channel and sheet. |
| `!rename_puzzle` *(slash)* | `!rename_meta` | `new name` | puzzle channel | organisers/admins | Rename the puzzle, its channel and sheet tab. |
| `!link` *(slash)* | | `url` | puzzle channel | anyone | Set/update the puzzle's hunt URL. |
| `!status` *(slash)* | | `value` | puzzle channel | anyone | Set status: Unstarted, In Progress, Stuck, Needs Extraction, Needs Submission, Solved, Backsolved. Stored lowercase; the case you type does not matter. |
| `!type` *(slash)* | | `puzzle_type` | puzzle channel | anyone | Set a freeform type label (e.g. crossword); the slash form autocompletes from types already used in the hunt. |
| `!priority` *(slash)* | | `value` | puzzle channel | anyone | Set priority: low, medium, high, very high. |
| `!info` | | — | any hunt channel | anyone | Re-post/refresh the channel's pinned info message. |
| `!list_puzzles` *(slash)* | `!list` | — | puzzle/round channel | anyone | List puzzles in the current round with status/solution. |
| `!delete_puzzle` | | — | puzzle channel | organisers/admins | Delete the puzzle, its channel and sheet (confirms). |

## Solving

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!solve` *(slash)* | `!s` | `answer` | unsolved puzzle | anyone | Mark fully solved, record the answer, move to solved section. |
| `!partial` *(slash)* | `!ps` | `partial answer` | unsolved puzzle | anyone | Record a partial answer (repeats append, joined with `/`). |
| `!change_solution` *(slash)* | `!update_solution`, `!update_answer` | `answer` | puzzle channel | anyone | Replace the recorded solution (solves if not yet solved). |
| `!add_solution` *(slash)* | `!add_to_solution` | `answer` | solved puzzle | anyone | Add an extra valid answer (records a partial if not solved). |
| `!mark_as_complete` *(slash)* | `!mark_as_solved`, `!complete` | — | unsolved puzzle | anyone | Solve with a ✅ when there's no text answer. |
| `!unsolve` *(slash)* | `!u` | — | solved puzzle | anyone | Undo a solve; clear solution and partials, move back to unsolved. |

## Grouping (edges: feeds / informs / none)

Run in the member puzzle's channel; `meta_code` is the **target** group's code. The
`*_only` variants clear the puzzle's other tags of the same edge type first;
`feeds_only` also moves the channel into the target's category.

| Command | Aliases | Arguments | Edge | Does |
|---|---|---|---|---|
| `!feeds` *(slash)* | `!add_to_meta`, `!add_to_round` | `meta_code` | feeds | Tag as a feeder (don't move). |
| `!feeds_only` *(slash)* | `!move_to_meta`, `!move_to_round` | `meta_code` | feeds | Assign as feeder, clear other feeders, move channel. |
| `!informs` *(slash)* | | `meta_code` | informs | Tag as informing the group (capstone). |
| `!informs_only` *(slash)* | | `meta_code` | informs | Assign as informer, clear other informer tags. |
| `!groups_with` *(slash)* | | `meta_code` | none | Add to a group without contributing to its meta. |
| `!groups_only_with` *(slash)* | | `meta_code` | none | Assign with a `none` edge, clear other `none` tags. |
| `!remove_from` *(slash)* | `!remove_from_meta`, `!remove_from_round` | `meta_code` | — | Remove the puzzle from a group. |
| `!sync` | `!sync_meta`, `!sync_feeders`, `!sync_round` | — | — | Tag every puzzle in this category to the group (organisers/admins; run in the group's main channel). Bang-only, by design. |

## Minimetas

A minimeta is a puzzle holding mini-puzzles, each in its own thread + sheet. Run mini
commands in the minimeta's channel; numbers come from `!list_minis`.

| Command | Arguments | Does |
|---|---|---|
| `!make_minimeta` | — | Convert this puzzle into a minimeta cluster. |
| `!remove_minimeta` | — | Revert a minimeta to an ordinary puzzle, deleting its minis (threads + sheets) and rollup (organisers/admins; asks to confirm). |
| `!add_mini` | `name` | Add a mini-puzzle. |
| `!list_minis` | — | List minis with numbers and solve state. |
| `!solve_mini` | `number answer` | Record a mini's answer. |
| `!unsolve_mini` | `number` | Clear a mini's answer. |
| `!delete_mini` | `number` (optional) | Permanently delete one mini (thread + sheet tab), asking to confirm. Organisers/admins. Give the number in the minimeta's channel, or run it with no number inside the mini's own thread. |

## Sheets

Run in a puzzle channel for that puzzle's sheets, or anywhere else in the hunt for the
hunt's own — somewhere to work on something found in the hunt's alt text before you know
which puzzle it belongs to.

| Command | Aliases | Arguments | Does |
|---|---|---|---|
| `!add_sheet` | | `sheet-name` | Add an extra working sheet. In a puzzle channel it belongs to that puzzle (which must be unsolved); anywhere else in the hunt it belongs to the hunt. |
| `!list_sheets` | `!list_additional_sheets` | — | List the puzzle's sheets, or the hunt's own, with links and numbers. |
| `!remove_sheet` | `!delete_sheet` | `number` | Remove a sheet by its number, from this puzzle or from the hunt. **Organisers/admins only** — it deletes the Google tab. |

## Canvas

A canvas is one of the hunt's own images with boxes drawn round the puzzles on
it. **Drawing is web-only** — a deliberate parity exception, in the same class as
the archiving family: marking up a picture needs a picture in front of you.

| Command | Aliases | Arguments | Does |
|---|---|---|---|
| `!canvas` | `!canvases` | — | Link to the hunt's Canvas view, and name the canvases it has. |

## Notes, clues & metadata

Run in a puzzle channel — except `!clue`, which also works elsewhere (see its row).

| Command | Aliases | Arguments | Does |
|---|---|---|---|
| `!note` *(slash)* | `!notes` | `[text]` | Show notes, or add one if text is given. |
| `!erase_note` *(slash)* | `!delete_note` | `note_index` | Remove a note by number (from `!notes`). |
| `!clue` *(slash)* | | `text` *(optional if an image is attached)* | Log a freeform clue to the evidence sheet. Attach an image to log a picture. Outside a puzzle channel the clue is logged with no source puzzle. |
| `!clues` *(slash)* | | `item ... "multi word" ...` | Log several clues at once (quote those with spaces). |

## Channel ordering

*Slash* hybrids, organisers/admins, run in the channel to move.

| Command | Does |
|---|---|
| `!move_to_top` | Move to the top of the category's unsolved section. |
| `!move_to_bottom` | Move to the bottom of the unsolved section. |
| `!move_to_solved` | Move into the solved section (mini-puzzle threads are archived). |

## Archiving

Admins/mods. **Channel archiving is bang-only** — copying a Discord channel's history into a
thread and deleting the channel has **no web-dashboard equivalent, by design**. Don't confuse it
with two unrelated features that also say "archive": the **solved puzzles sheet** (see
`!use_solved_sheet` — a second spreadsheet solved feeders move to) and the **hunt archived flag**
(the dashboard "Archive hunt" action that makes a hunt read-only). Neither of those touches Discord
channels.

Channel archiving auto-creates the destination webhook, does not copy reactions, and
times out stuck attachment downloads (so a busy channel can't hang the run). `!archive_hunt`
holds locks on every puzzle in the hunt for the whole run, so solving is blocked hunt-wide
until it finishes.

| Command | Where | Does |
|---|---|---|
| `!archive_hunt` | any hunt channel | Archive the **whole hunt**: condense every channel into threads here, delete all categories/channels (voice included), rename this channel to `{YYYY-MM} {hunt name} - {team}`, mark the hunt archived. Confirms first, then runs unattended. Sheets kept. |
| `!archive_hunt_to_hub [hub server id]` | any hunt channel | Archive the **whole hunt onto another server** (the hub): condense every channel, this one included, into threads on a new hub channel `{YYYY-MM} {hunt name} - {team}`, delete all categories/channels, move the hunt to the hub on the dashboard, and remove the bot from this server if no other hunt is on it. You must be an admin on the hub as well. Refuses before confirming if the bot isn't in the hub or you aren't a hub admin. If any channel fails, the bot stays in the server; re-run from the hunt's general channel. Sheets kept. On a team server made by `!setup_server` leave the id out — the hub is found from the hunt's hub role; an id that disagrees with it is refused. |
| `!archive_round` | round channel | Copy the round's channels into threads, then delete the category. |
| `!archive_category` | category channel | Archive every channel in the category into threads. Usually via `!archive_round`. |
| `!archive_channel` | channel | Archive one channel's messages into a thread, then delete it (auto-creates the destination webhook if missing). |
| `!archive_solved_manually` | hunt channel | Tidy the hunt's `solved` category: keep busy channels (prefixed `solved-`), delete quiet ones. |

## Maintenance & troubleshooting

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!rebuild` *(slash)* | | — | round/meta or hunt | organisers/admins | Rebuild meta roll-up sheets (targeted in a round, else hunt-wide). |
| `!rebuild_unassigned` *(slash)* | | `[show_round]` | hunt channel | organisers/admins | Reconcile the "Unassigned Feeders" sheet tab. |
| `!backfill_glyphs` | | — | any hunt channel | organisers/admins | Prefix every target channel with its emoji glyph (🧩 meta, 🧩🧩 metameta, 👑 capstone, 🏃 runaround). Idempotent one-off; newly created or converted targets get their glyph automatically. |
| `!absorb_category` | | — | a puzzle channel of the category to absorb | organisers/admins | Move every puzzle channel in this hand-made category into the hunt's general layout (auto-overflow), then delete the emptied category. Cleanup for categories split by hand before the overflow engine existed. |
| `!reconcile` *(slash)* | | `[apply [orphans]]` | hunt channel | organisers/admins | Heal Discord + Sheets to match the DB; `apply` fixes, `apply orphans` deletes orphans (confirms). |
| `!media_stats` *(slash)* | | — | hunt channel | organisers/admins | Report images nothing references any more (detached, replaced, or left by a deleted canvas) and the disk they hold. Nothing is deleted. |
| `!media_sweep` *(slash)* | | — | hunt channel | organisers/admins | Delete those orphaned images and their files (confirms first). Only images older than 24 hours and referenced by nothing are touched. |
| `!start_reminders` *(slash)* | | — | hunt channel | organisers/admins | Start the periodic reminder/tips loop. |
| `!stop_reminders` *(slash)* | | — | any | organisers/admins | Stop the reminder loop. |
| `!list_tags` *(slash)* | | — | puzzle channel | organisers/admins | List a puzzle's tag IDs and all available group tags. |
| `!show_settings` *(slash)* | | — | any | admins/mods | Dump this channel's object settings as JSON. Guild-level values are also editable on the web: **Server settings**. |
| `!show_auth_roles` *(slash)* | | — | any | admins/mods | Report which roles grant the admin and organiser tiers, and whether each is pinned by role id or still matched by name. Web: **Server settings**. |
| `!set_admin_roles` | | `[@Role ...]` | any | admins/mods | Pin the admin tier to specific roles, by id. No roles clears the list and reverts to name matching. Refuses if you hold none of the roles given (it would lock you out of this command). Web: **Server settings**. |
| `!set_organiser_roles` | | `[@Role ...]` | any | admins/mods | Pin the organiser tier to specific roles, by id. No roles clears the list and reverts to name matching. Web: **Server settings**. |
| `!show_puzzle_settings` *(slash)* | | — | puzzle channel | admins/mods | Dump the puzzle's fields/values. |
| `!update_settings` | `!update_setting`, `!update_puzzle_setting(s)` | `key value` | any | admins/mods | Set one setting field (quote values with spaces). Refuses the authorisation and guild-identity keys (`admin_role_ids`, `organiser_role_ids`, `guild_id`, `guild_name`) — use `!set_admin_roles` / `!set_organiser_roles`, which validate the roles and guard against locking you out. |
| `!debug_puzzle_channel` | | — | puzzle channel | admins/mods | Dump the puzzle's full record as JSON. |

## Utility (bot)

Defined in `bot/cogs/utility.py`. Open to everyone.

| Command | Aliases | Does |
|---|---|---|
| `!uptime` | | Show how long the bot has been running. |
| `!starttime` | | Show when the bot last started. |
| `!info_bot` | | Show bot stats, prefix and software versions. |
| `!invite` | `!socials`, `!links`, `!support` | Show the invite and social/support links. |

---

## Keeping this current

When you add, remove, rename, or change the arguments/permissions of a command:

1. Update the command's **docstring** in `bot/cogs/*.py` — keep the
   *summary → `Usage:` → `Example:` → aliases/permissions* shape so `!help` stays useful.
2. Update the matching row in **this file**.
3. If the command has a `description=` (hybrid/slash) keep it consistent with the summary
   (≤100 chars for the slash UI).

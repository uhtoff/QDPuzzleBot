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
- **Permissions** (from `bot/core/constants.py`):
  - *Admins/mods* = roles `Moderator`, `mod`, `admin`.
  - *Organisers/admins* = the above plus `Organisers`.
  - Commands with no role note are open to everyone.
- **`meta_code`** is a TagGroup's 6-character group code (shown on the group's pinned
  info; slash commands autocomplete it). On the bang path you may also pass the group's
  **name** (or a unique part of it) — the resolver matches the code, then the name, scoped
  to the current hunt, and lists candidates when ambiguous. See [AGENTS.md](../AGENTS.md)
  for the TagGroup / feeds / informs data model.

## Hunt management

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!hunt` | `!h` | `name:url[, role][, forum]` | bot channel | admins/mods | Create a hunt: category, general channel, sheet. Optional role gates visibility; append `, forum` for forum-backed layout (a forum channel per round). |
| `!use_solved_sheet` *(slash)* | `!set_to_archive` | — | Hunt channel | admins/mods | Use a separate "solved puzzles sheet": solved feeders move there, metas stay on the main sheet. |
| `!set_login` | | `username:password` | hunt channel | admins/mods | Store the hunt login shown on hunt info. |
| `!set_start` | | `timestamp` (UNIX seconds) | hunt channel | admins/mods | Set the hunt start time. |
| `!delete_hunt` | | — | Hunt channel | admins/mods | Permanently delete the whole hunt (confirms). Not recoverable. |

## Rounds & groups

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!round` | `!r` | `name` | Hunt channel | anyone | Create a round (also makes a `name - meta` meta puzzle). |
| `!metapuzzle` | `!mp` | `name` | Hunt channel | anyone | Create a metapuzzle. |
| `!capstone` | `!cs` | `name` | Hunt channel | anyone | Create a capstone. |
| `!runaround` | `!ra` | `name` | Hunt channel | anyone | Create a runaround. |
| `!group` | | `name` | Hunt channel | anyone | Create a group (no meta puzzle). |
| `!rename_group` *(slash)* | `!rename_round`, `!rename_metapuzzle` | `new name` | group's target-puzzle or regular group channel | organisers/admins | Rename the group and its Discord category/channel. Does not rename the target puzzle. |
| `!delete_round` | `!delete_metapuzzle`, `!delete_group` | — | group's main channel | organisers/admins | Delete a round/group, its puzzles, channels and sheets (confirms). |

> `!round`/`!r`, `!mp`, `!cs`, `!ra`, `!group` are all aliases of one command;
> the alias you type selects the type created.

## Converting a puzzle's role

All convert commands are *slash* hybrids, run in the puzzle's channel, admins/mods only
(need Manage Channels).

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
| `!status` *(slash)* | | `value` | puzzle channel | anyone | Set status: Unstarted, In Progress, Stuck, Needs Extraction, Solved, Backsolved. |
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
| `!remove_minimeta` | — | Revert a minimeta to an ordinary puzzle, deleting its minis (threads + sheets) and rollup. |
| `!add_mini` | `name` | Add a mini-puzzle. |
| `!list_minis` | — | List minis with numbers and solve state. |
| `!solve_mini` | `number answer` | Record a mini's answer. |
| `!unsolve_mini` | `number` | Clear a mini's answer. |

## Sheets

Run in a puzzle channel.

| Command | Aliases | Arguments | Does |
|---|---|---|---|
| `!add_sheet` | | `sheet-name` | Add an extra working sheet (puzzle must be unsolved). |
| `!list_sheets` | `!list_additional_sheets` | — | List additional sheets with links and numbers. |
| `!remove_sheet` | `!delete_sheet` | `number` | Remove an additional sheet by number. |

## Notes, clues & metadata

Run in a puzzle channel.

| Command | Aliases | Arguments | Does |
|---|---|---|---|
| `!note` *(slash)* | `!notes` | `[text]` | Show notes, or add one if text is given. |
| `!erase_note` *(slash)* | `!delete_note` | `note_index` | Remove a note by number (from `!notes`). |
| `!clue` *(slash)* | | `text` | Log a freeform clue to the evidence sheet. |
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

| Command | Where | Does |
|---|---|---|
| `!archive_round` | round channel | Copy the round's channels into threads, then delete the category. |
| `!archive_category` | category channel | Archive every channel in the category into threads. Usually via `!archive_round`. |
| `!archive_channel` | channel | Archive one channel's messages into a thread, then delete it (destination needs a webhook). |
| `!archive_solved_manually` | hunt channel | Tidy the hunt's `solved` category: keep busy channels (prefixed `solved-`), delete quiet ones. |

## Maintenance & troubleshooting

| Command | Aliases | Arguments | Where | Who | Does |
|---|---|---|---|---|---|
| `!rebuild` *(slash)* | | — | round/meta or hunt | organisers/admins | Rebuild meta roll-up sheets (targeted in a round, else hunt-wide). |
| `!rebuild_unassigned` *(slash)* | | `[show_round]` | hunt channel | organisers/admins | Reconcile the "Unassigned Feeders" sheet tab. |
| `!reconcile` *(slash)* | | `[apply [orphans]]` | hunt channel | organisers/admins | Heal Discord + Sheets to match the DB; `apply` fixes, `apply orphans` deletes orphans (confirms). |
| `!start_reminders` *(slash)* | | — | hunt channel | organisers/admins | Start the periodic reminder/tips loop. |
| `!stop_reminders` *(slash)* | | — | any | organisers/admins | Stop the reminder loop. |
| `!list_tags` *(slash)* | | — | puzzle channel | organisers/admins | List a puzzle's tag IDs and all available group tags. |
| `!add_tag` | | `tag_id` | puzzle channel | organisers/admins | Manually add a tag ID (prefer `!feeds`/`!informs`). |
| `!remove_tag` | | `tag_id` | puzzle channel | organisers/admins | Manually remove a tag ID (prefer `!remove_from`). |
| `!show_settings` *(slash)* | | — | any | admins/mods | Dump this channel's object settings as JSON. |
| `!show_puzzle_settings` *(slash)* | | — | puzzle channel | admins/mods | Dump the puzzle's fields/values. |
| `!update_settings` | `!update_setting`, `!update_puzzle_setting(s)` | `key value` | any | admins/mods | Set one setting field (quote values with spaces). |
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

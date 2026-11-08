# Puzzle voice channels — design

Date: 2026-11-08. Status: draft for review.

## Intent

Teams talk puzzles through in voice, but today there is nowhere obvious to go: the bot makes
no voice channels, and someone has to make one by hand and tell everyone. We want a voice
channel for the hunt as a whole, a voice channel for any puzzle that wants one (on request, or
for every puzzle when a hunt chooses that), tidy clean-up once a puzzle is solved, and the
dashboard showing where people are talking so others can find live conversations.

This also settles the open `docs/PLANS.md` item "Voice channels: finish or delete" — finish.
Existing half-wired pieces to build on, not around: `puzzles.voice_channel_id` (never
populated), `guilds.discord_use_voice_channels` (exposed on the Guild settings page, never
read), `discord_channels.get_or_create_channel` (can already create voice channels), and the
archive/delete-hunt paths, which already remove voice channels.

## Goals

1. **Hunt voice channel.** Every hunt created from now on gets one voice channel in its main
   category (named after the hunt, e.g. "<hunt name> voice" — builder picks exact wording).
   A hunt that has none (hunts created before this feature) gets it created on first need:
   `!voice`, a puzzle auto-creating its voice channel, or any clean-up that would move people
   (user, 2026-11-08). So every clean-up can always move people rather than disconnect them.
   No bulk back-fill of all existing hunts.
2. **`!voice` (alias `!vc`)**, run in a puzzle's text channel by any hunt member, creates that
   puzzle's voice channel, named after the puzzle, and replies with a link to it. If the puzzle
   already has one, it just replies with the link. Refused (with a reason) outside a puzzle
   channel, for a solved puzzle, and in an archived hunt.
3. **Voice category.** Puzzle voice channels live in a per-hunt category "<hunt name> Voice",
   created on first use, carrying the hunt's permissions the same way new round categories do
   today (cloned from the hunt's initial category — see the "hunt permission manual seam":
   the overwrite is set by hand on the initial category and code only ever clones it). When a
   voice category reaches Discord's 50-channel limit, a "<hunt name> Voice 2" follows
   (designer's call).
4. **Per-hunt "voice channel for every puzzle" setting**, off by default, changeable mid-hunt
   by admins only (user, 2026-11-08). When on, each puzzle created afterwards gets its voice
   channel automatically (metas included — designer's guess). Turning it on does NOT back-fill
   existing puzzles; turning it off does NOT delete anything (user, 2026-11-08).
5. **`!voice_cleanup`** (admin only, plus a dashboard equivalent): removes every puzzle voice
   channel in the hunt — the "I regret turning it on" reconcile (user, 2026-11-08). It never
   touches the hunt voice channel, which always exists (user). Empty puzzle voice channels go
   at once; occupied ones get the same treatment as the post-solve clean-up — a 15 s `@here`
   warning in the channel's text chat, then occupants moved to the hunt voice channel, then
   deletion (user, 2026-11-08). The reply reports what was removed. Empty voice categories it
   leaves behind are removed. It does not change the per-hunt setting.
6. **Solve clean-up.** 15 minutes after a puzzle is solved its voice channel is deleted. If
   anyone is in it at 15 s before deletion, an `@here` warning is posted in the voice
   channel's own text chat ("this channel closes in 15 seconds — <puzzle> was solved; you'll
   be moved to <hunt voice channel>"). At deletion, anyone still in it is first moved into the
   hunt's voice channel (user, 2026-11-08; created first if missing — see goal 1), then the
   channel is deleted. If a move fails, that person is disconnected by the deletion. Moving needs the bot to have Move Members on the hunt's
   channels — check it at hunt/voice creation and say so in the reply if missing. Unsolving
   within the 15 minutes cancels it (the channel stays).
7. **Dashboard presence.** The board shows, per puzzle, how many people are in its voice
   channel, live, as part of the existing activity display — and a puzzle with people in voice
   is visibly marked (e.g. a different colour/tint or a headphones badge with a count;
   builder's call on the exact look, user wants it noticeable at a glance). The hunt voice
   channel's count is shown at hunt level.
8. **Drawer controls.** The puzzle drawer offers "Create voice channel" (the `!voice`
   equivalent) when the puzzle has none, and a "Join voice" link when it has one. Hunt
   management offers the per-hunt toggle and the cleanup action.

"Join voice" is a plain Discord link (`discord.com/channels/<guild>/<voice channel>` and the
existing deep-link form), so it does NOT need the extension; Discord decides whether that opens
the channel or joins it directly.

## Non-goals (this version)

- Voice in the Chrome extension (presence/join in the side panel) — later.
- Bulk back-filling hunt voice channels for every existing hunt (they are created on first
  need instead), or puzzle voice channels when the setting is switched on.
- Recording, transcription, moving people between channels, stage channels.
- Per-user voice presence (names) on the dashboard — counts only (designer's guess; names
  would be easy to add later).

## Anti-goals

- Dropping someone's call without warning. The only automatic deletion is the post-solve one,
  and the admin cleanup, both of which warn and move people to the hunt voice channel first.
- A voice channel visible to people who can't see the hunt. Permissions always come from the
  hunt's own category by cloning.
- Losing a pending clean-up on restart. Every push deploys and restarts the bot, often
  mid-hunt; the 15-minute clean-up must be derived from stored state (the solve time), not an
  in-memory timer alone.
- Breaking the two solve entrypoints' parity: Discord bang solve and dashboard solve behave
  identically (both go through `PuzzleService.solve`/`unsolve`).

## Constraints and existing pieces

- Solve/unsolve/delete each have a Discord path and a web path; voice behaviour hangs off the
  shared service seam (`bot/core/puzzle_service.py`: `solve`, `unsolve`, `delete_puzzle`,
  `create_puzzle`, `create_hunt`), never one cog.
- Deleting a puzzle deletes its voice channel immediately. Archiving/deleting a hunt already
  removes voice channels. A voice channel deleted by hand in Discord is forgotten (its id
  cleared), and `!voice` then makes a fresh one.
- Command-parity principle: every bang command has a dashboard equivalent (create voice,
  toggle setting, cleanup).
- Guild category limit is 50 server-wide and already tight (one category per round); a voice
  category per hunt (two only past 50 voice channels) is one more per hunt — acceptable.
- The bot runs with `Intents.all()`, so voice-state events are available; presence can be
  built from `on_voice_state_update` plus a snapshot of current voice members on startup.
- Live dashboard updates go over the existing activity event bus / `/api/ws/activity`.
- Migrations: the next number (058) is reserved by branch `drop-dead-columns`; this work uses
  059+ (check for collisions before merging — see migration-number-collisions).
- MariaDB in prod, MySQL 8 in CI: no MariaDB-only syntax.
- Normal flow: build on a branch, test on staging (puzzbot.com), ship to live only when the
  user says.

## Decided

- Hunt voice channel on creation, new hunts only (user).
- `!voice` / `!vc` per puzzle, in a per-hunt voice category with the hunt's permissions (user).
- Per-hunt setting, changeable mid-hunt, no back-fill on, no deletion on off (user).
- `!voice_cleanup`, admin only, hunt-wide reconcile (user).
- 15-minute post-solve deletion with a 15 s `@here` warning when occupied, then occupants
  moved to the hunt voice channel before deletion (user).
- Dashboard shows voice presence as part of activity; puzzles with people in voice stand out
  (user). Create-voice lives in the drawer (user).

## Left to the builder

- Storage: a per-hunt boolean column for the setting; whether to store the hunt voice
  channel's id (needed at least for its presence count) and the voice category id(s) or find
  them by name. `puzzles.voice_channel_id` is the puzzle's voice channel.
- What to do with the unused guild toggle `guilds.discord_use_voice_channels`: suggested —
  use it as the default for new hunts' per-hunt setting, and relabel it on the Guild settings
  page accordingly; or remove it. Either way it must stop being a dead control.
- The bang command for the per-hunt toggle (name/shape), and the exact dashboard placement of
  the toggle and cleanup.
- Clean-up mechanics: a periodic sweep over solved puzzles with a voice channel and
  `solve_time + 15 min` due, a scheduled task re-armed on startup, or both. Behaviour on a bot
  restart that lands inside the 15 s warning window (late deletion is fine; deletion without
  any warning to an occupied channel should be avoided).
- Presence bookkeeping, the board payload field, the websocket event shape, and the visual
  treatment.
- Wording of replies, the warning and the cleanup report.

## Settled after the builder check (designer's calls unless marked)

- **Which server.** A hunt row lives in exactly one guild (`hunts.guild_id`); on multi-server
  hunts each team server has its own hunt row, so voice is per hunt row and the "move to hunt
  voice" target is always in the same guild. The hub's multi-team channels get no voice.
- **What arms the clean-up.** Any path that leaves the puzzle solved with a fresh
  `solve_time` (`solve`, `add_solution`, `change_solution`). The sweep keys on
  `solve_time + 15 min` with a non-null voice channel; unsolve clears `solve_time`, and a
  re-solve or answer change restarts the clock. A meta/capstone solve does nothing to its
  feeders' voice channels. No separate `voice_cleanup_at` column unless the builder finds a
  path where `solve_time` is not trustworthy.
- **Who may do what.** Reuse the existing checks: the per-hunt toggle (user, 2026-11-08),
  `!voice_cleanup` and their dashboard controls all use `can_administer` (admin tier, not
  organiser); `!voice` and dashboard "Create voice channel" use the same
  gate as any puzzle-channel action a hunt member can take.
- **Permission source missing.** If the hunt's initial category can't be found to clone
  from, refuse to create the voice category and say why — never create one with no
  overwrites.
- **Presence storage.** In memory only, rebuilt from the guilds' current voice members on
  startup; nothing persisted.

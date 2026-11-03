# Multi-team hunts: hub no-spoiler and finishers channels — design

Status: draft for review (2026-11-03). Builds on the team-server work
(`2026-07-31-fresh-server-per-hunt-team-design.md`, `!setup_server`, the dashboard setup page,
the Team servers page, `!archive_hunt_to_hub`).

## Why

When we field several teams in one hunt, each team plays on its own team server. Today the
hub has nowhere organised for those teams to talk to each other, and when a team finishes an
admin hand-makes a "finishers" channel and hand-adds people to it. Two problems:

- During the hunt, players from different teams need a **no-spoiler** place to chat, and a
  place to **get a mod's attention**.
- Finishing a hunt is not the same as archiving it. A finished team often wants its server
  left as it is for a while (to look back at sheets, chat), but should be able to talk
  spoilers with other finished teams.

## Goals

- When a team server is set up, the admin answers **"Are we fielding more than one team?"**.
  Yes → the team joins a **multi-team hunt** on the hub: either one already running there or a
  new one.
- Each multi-team hunt has two hub channels:
  - **No-spoiler chat** — visible to every team on the hunt **plus the hub mods** (the hub's
    Admin tier). It doubles as the place to get mod attention.
  - **Finishers** — visible **only to teams marked complete**. Hub mods are NOT added.
- **Mark complete** is a deliberate admin action per team. It adds that team's hub role to the
  finishers channel and posts a short "🏁 <team> has finished!" line there. It does nothing
  else: the team server and its hunt stay live and editable. It can be undone.
- When the **last** team on the multi-team hunt is archived to the hub, the bot automatically
  condenses both channels into one open archive channel `{YYYY-MM} {hunt name} - all teams`
  (two threads: no-spoiler and finishers) and deletes the live channels. The team waits to
  archive until chat has died down anyway, so automatic is right.

## Non-goals

- Hunts with a single team: no hub channels, behaviour exactly as today (answer "No").
- Changing Join/Leave, invites, the `mod` role, or the per-team archive-to-hub.
- Any automatic "finished" detection (e.g. final meta solved). Completion is always a choice.
- Linking a team hunt that is **already set up** to a multi-team hunt after the fact. (Worth a
  later small follow-up from the Team servers page; not in this build.)

## Anti-goals (would be a failure even if it "works")

- Marking complete archives, locks or otherwise changes the team server or its hunt.
- An unfinished team can see the finishers channel — by an inherited permission, a category
  sync, a stale overwrite, or `@everyone`.
- Anything pings (posts use no mentions, like the rest of the bot's announcements).
- The term "shared" in any user-facing text. User-facing: the question
  "Are we fielding more than one team?", lists show the hunt's name (e.g.
  "Mystery Hunt 2027 (3 teams)"); "multi-team hunt" only where a term is unavoidable
  (headings, docs).

## Decisions (the user's)

- Grouping is chosen **at setup**, Yes/No, then pick a running multi-team hunt on that hub or
  make a new one.
- No-spoiler: all teams + hub mods. Finishers: completed teams only, no mods.
- Completion is an explicit admin action; finishing ≠ archiving.
- At the end, the channels are condensed into the archive (open channel, like all archives),
  automatically when the last team archives.

## Calls I made (the user can overrule)

- **Who can mark complete:** hub admins only (same tier that can archive to hub).
- **Where:** a per-team **Mark complete / Mark not complete** control on the hub's Team servers
  page (running team hunts that belong to a multi-team hunt), plus a matching bang command run
  in the team server's main hunt channel, per the project's dashboard/command parity rule.
  Command names are the builder's call (e.g. `!hunt_complete` / `!hunt_not_complete`).
- **Undo** removes the team's finishers access and posts nothing.
- **Archiving counts as finishing:** a team archived to the hub without being marked complete
  is marked complete as part of the archive (gets finishers access, gets the 🏁 line).
- **New team joining later:** each later setup that picks the multi-team hunt adds that team's
  hub role to the no-spoiler channel.
- **Mods** = the hub's Admin-tier roles as resolved at channel creation (pinned
  `admin_role_ids`, else name match — the same rule Join uses to grant `mod`). Not re-synced
  if the tier changes later.
- **Default name** of a new multi-team hunt = the hunt name typed on the setup form; editable
  there.
- **Last team** = no linked team hunt remains un-archived. A linked hunt that is deleted rather
  than archived simply drops out of the count (if that leaves none, the auto-archive runs).
- **🏁 line:** posted on every change from not-complete to complete (so a re-mark after an
  undo posts again); archiving a team that is already complete posts nothing more.
- **Which hub:** always the hub the team server was set up from (its roster role's hub). A
  team server's archive-to-hub already refuses any other hub, so the multi-team hunt and the
  team's archive can never disagree.
- **Deleted teams and failures:** deleting a team hunt does NOT trigger the auto-archive (no new
  hook in the delete paths). Instead the multi-team hunt gets an admin **Archive now** action
  (Team servers page, plus a command), which also serves as the retry after a failed
  auto-archive and covers the case where the last team was deleted rather than archived.
- **Hand-deleted or half-made channels:** the live channels are found by a topic marker, like
  `ensure_hub_archive_channel`. A missing channel is recreated (with the right overwrites) the
  next time something needs it (a setup joining, mark complete); the archive step skips a
  channel that is gone.
- **Setup re-runs:** once a team hunt is linked to a multi-team hunt, the link is stored on the
  hunt, and a re-run of setup reuses it rather than asking again; a partial run that made the
  hunt but not the link finishes the link on re-run with the answer given then.
- **Roster role:** a team hunt can only join a multi-team hunt through setup, which always
  records its hub role, so every linked hunt has one; mark complete refuses a hunt without one
  rather than recording a completion that grants nothing.
- **`!setup_server`:** gains an optional way to say "multi-team, join <name> / new <name>".
  The exact syntax is the builder's call; it must express both join-existing and create-new,
  and reject a name that matches nothing when "join" is meant.

## Shape (the parts that are decided)

- A new hub-level record for a multi-team hunt: hub guild, name, no-spoiler channel id,
  finishers channel id, archived state. Each team hunt optionally points at one. Each team
  hunt records whether (and when) it was marked complete.
- Hub channel permissions are explicit overwrites on the channels themselves:
  `@everyone` view off on both; team hub roles (`hunts.roster_role_id`) and mod roles allowed
  on no-spoiler; only completed teams' hub roles allowed on finishers. Overwrites must survive
  any category placement (no permission sync that could leak finishers).
- Archiving reuses the existing hardened primitives in `bot/core/hunt_archive.py`
  (`archive_and_delete_channel`, webhook, no pings) and the open-channel convention of
  `ensure_hub_archive_channel`.

## Left to the builder

- Table/column names and the migration (CI runs MySQL 8.0 — no MariaDB-only syntax).
- Channel names and whether they sit at top level or in a per-hunt hub category (category
  limit is 50 per guild; keep it tidy, and deleted at archive if one is made).
- Exact wording of the 🏁 line and dashboard labels (within the anti-goal on "shared").
- How a failed auto-archive is reported and retried (it must not fail the team's own
  archive; report in the hub admin channel like other archive problems, and give admins a way
  to re-run it).
- Which board/dashboard payloads carry the multi-team hunt and completion state.

## Acceptance (manual, on the test bot)

1. Set up team server A answering Yes → New "Test Hunt": hub gets both channels; A's role and
   mods see no-spoiler; nobody sees finishers.
2. Set up team server B answering Yes → pick "Test Hunt": B's role now sees no-spoiler.
3. Mark A complete (dashboard) → A sees finishers, 🏁 line posted, A's server untouched. Undo →
   A loses finishers. Mark complete again via the command.
4. Archive A to hub → A's own archive as today; multi-team channels stay (B still live).
5. Archive B (never marked complete) → B is marked complete, then `{YYYY-MM} Test Hunt - all
   teams` appears with two threads, and the two live channels are gone.
6. A one-team setup (answer No) behaves exactly as before.

# Slice 5 — access log, hub admin channel, mod invites

**Date:** 2026-10-31
**Status:** Implemented — plan `docs/superpowers/plans/2026-10-31-access-log.md`.
**Parent spec:** `docs/superpowers/specs/2026-07-31-fresh-server-per-hunt-team-design.md`
(the *Access audit trail* section and acceptance criterion 4). Where this document and the
parent disagree, this one wins; the parent gets a dated *Changed* note pointing here.

## Intent

Slices 1–4 put each team of a multi-team hunt on its own throwaway Discord server. Two
things are still missing:

1. **A way in for mods.** Mid-hunt, a team member pings `@mod`/`@admin` on the hub
   asking for help that needs mod powers on their team server. Today a hub admin has no
   way onto that server: invites only go to holders of the team's hub role. Once there,
   the existing flow already works — they land in `#lobby`, click **Join**, and as a hub
   admin get the team role plus `mod`.
2. **A durable record of who had access.** A team server's own audit log dies when the
   server is discarded, so the bot keeps its own (`hunt_access_log`), as the parent spec
   requires.

Both are delivered through one **hub admin channel** that stays **quiet**: it carries only
what a mod needs to see or act on. The complete history lives in the database, and the
next slice (5b, below) gives it a web page.

## Goals

- A reusable invite per team server, posted in the hub admin channel, so any hub mod can
  get onto any team server when asked.
- Every grant and removal of a team server's team role or `mod` — by button, by the bot,
  or by hand in Discord — recorded in `hunt_access_log`, with `mod` changes distinct.
- A quiet hub channel: exceptions and actionable items only.
- A startup warning when a live team hunt's server is missing or the bot has lost the
  permissions it needs there.

## Non-goals

- **The web page** for the log (slice 5b, next). This slice only has to make the data
  that page needs exist and accumulate.
- Mirroring every change into Discord. *Superseded:* the parent spec's "one line per
  grant/removal into an organiser-visible hub channel" is replaced by the quiet channel
  plus the 5b page (user decision, 2026-10-31: too noisy at hunt start, when a whole team
  arrives at once).
- Dashboard equivalents of `!setup_server` / `!send_invites` (later, per the parity
  principle).

## Anti-goals

- **A noisy channel.** If `!send_invites` for a 30-person team produces 30 lines in the
  hub channel, this has failed even if every line is correct.
- **An invite in a channel players can see.** The bot must never post a team-server
  invite somewhere `@everyone` on the hub can read.
- **A log with holes.** Logging only button clicks misses exactly the cases that matter
  (roles changed by hand). The log must be fed from member updates, not from the code
  paths that change roles.

## Decided

### The hub admin channel

- It is the **hub's configured command channel** — the hub's `discord_bot_channel`
  setting (`#bot-set-up` on the test hub). No new setting. (User decision.)
- **Safety check (my call):** before posting, the bot confirms the channel is hidden from
  the hub's `@everyone` (no View Channel for the default role, after overwrites). If it is
  visible, or the setting is empty or names no channel, the bot posts nothing there and
  says so where it can: in the team server's `#bot-commands` when the trigger was a command
  there, and in the log file otherwise.
- The hub is found from the hunt's roster role (`role_panel.find_roster_role(...).guild`),
  as slice 4 does.

### What goes in the channel (the quiet list)

Posted:

- **The mod invite** for each team server (below).
- **Removals by the bot** — slice 4's hub-role-loss rule (kicked, or roles stripped from
  the owner). These already post in the team server's `#bot-commands`; they also post here.
- **Anything not done by the button or the bot's own grant on arrival:** `mod` given or
  taken, and the team role given or taken by hand in Discord.
- **Failures the bot couldn't fix:** a late joiner it couldn't DM, someone it couldn't
  remove. (Also still posted in the team server's `#bot-commands`, as now.)
- **The startup warning** (below).

Not posted (database only): the team role granted on arrival, **Join**, **Leave**,
someone leaving the server themselves, and invite DMs that succeeded.

### The mod invite

- One per team server: **reusable** (`max_uses=0`), **non-expiring** (`max_age=0`), on
  `#lobby`. Safe to reuse because arriving grants nothing — someone with neither the hub
  role nor hub admin stays in `#lobby` (slice 3 Join gate, slice 4 arrival rule).
- **Posted when `!setup_server` reports complete**, naming the team and hunt, e.g.
  "Mod way into **Team Rocket**'s server for **Big Hunt**: <link> — click **Join** in
  #lobby when you arrive."
- **Re-post command** (my call on name and placement): `!mod_invite <hunt name>`, Admin
  tier, run **on the hub** (a mod who needs the link isn't on the team server yet). It finds
  the live team hunt by name among hunts whose roster role belongs to this hub, reuses the
  stored invite if Discord still has it, otherwise makes a new one, and posts it in the
  admin channel.
- `!mod_invite` matches the name case-insensitively against **live** team hunts on this
  hub only; if two match it refuses and names their team servers (my call). The team
  server is the hunt's guild (`hunts.guild_id` → `guilds.guild_id`, as
  `HuntJsonDb.live_team_hunts()` already returns). The stored invite is looked up by
  scanning that guild's invites for the code; if it is gone, a new one is made on
  `#lobby`; if `#lobby` is gone, it says so (slice 4's `InviteUnavailable` wording).
- The invite's code is stored on the hunt (new column `hunts.mod_invite_code`, written
  only by its own UPDATE like `invites_sent_time`, so the generic `commit()` never clears
  it). **`!archive_hunt_to_hub` deletes the invite** before the bot leaves the team server.

### `hunt_access_log`

New table (migration `049_hunt_access_log.sql`), shaped on `audit_log`:

| Column | Notes |
|---|---|
| `id` | BIGINT auto-increment PK |
| `created_at` | TIMESTAMP, default now |
| `hunt_id` | INT, the hunt (plain column, **no FK cascade** — the record must outlive a deleted hunt; my call) |
| `user_id` | BIGINT, whose access changed |
| `username` | VARCHAR(255), display name at the time |
| `action` | `grant` / `revoke` (team role), `elevate` / `demote` (`mod`), `arrive` / `depart` (joined / left the team server) |
| `via` | `button` / `arrival` / `hub_role_lost` / `hub_admin_lost` / `hand` / `self` — how it happened |
| `actor_id` | BIGINT NULL — who did it, when it wasn't the person themselves or the bot (from Discord's audit log, for `hand`) |
| `detail` | VARCHAR(255) NULL |

- **Fed from `on_member_update` on team servers** (and `on_member_join` / `on_member_remove`
  for arrive/depart), comparing before/after roles against the hunt's `role_id`
  (`grant`/`revoke`) and the team server's Admin-tier roles (`elevate`/`demote`). On a team
  server the Admin tier is what `!setup_server` pinned — the `mod` role — read with
  `role_gate.tier_role_ids(guild, ADMIN_ROLES)`, the same lookup `role_panel.admin_roles`
  uses. This catches every route.
- **Classifying `via` — the bot announces its own changes (my call).** Just before the
  bot itself adds or removes one of these roles, or kicks someone, it notes the expected
  change in memory, keyed by (guild, user, role or kick), with its `via`. The listener
  consumes a matching note and records that `via` with no audit-log read. Any change with
  no note was not made by the bot's role code, so it is `via='hand'`: only then does the
  listener read Discord's audit log, and only to fill `actor_id` (one retry if the entry
  hasn't appeared; NULL if still missing). The row is always written. This keeps a
  30-person arrival free of audit-log reads, and a missing audit entry can't turn a bot
  action into a noisy "by hand" line. A note left unconsumed (event lost) expires after a
  minute. Notes don't survive a restart, which only matters for a change in flight at
  that moment.
- **The `via` for each existing bot path:** Join → team role `grant`/`via='button'`, plus
  `mod` `elevate`/`via='button'` for a hub admin; Leave → `revoke`/`demote`,
  `via='button'`; arrival grant → `grant`/`via='arrival'`; slice 4's removal →
  `revoke`/`demote` (owner) or `depart` (kick), `via='hub_role_lost'`; `_drop_mod` →
  `demote`/`via='hub_admin_lost'`; someone leaving by themselves → `depart`/`via='self'`.
- `arrive` / `depart` are recorded so 5b can show who is on the server now.
- Rows are written only for team servers (guilds with a live team hunt —
  `HuntJsonDb.live_team_hunt_for_guild`). The hub's own role changes are not logged here.

### The startup warning

On `on_ready`, for each live team hunt (`HuntJsonDb.live_team_hunts()`):

- the bot is no longer in its guild → warn;
- the bot lacks Administrator there, or its top role is not above `mod` → warn.

Warnings go to that hunt's hub admin channel, one message per hub listing every problem.
If the hub can't be found (roster role gone, or the bot not on the hub), the warning goes
to the log file only (my call).
Nothing is posted when all is well. Because every push to `staging`/`main` deploys and
restarts the bot, a persisting problem re-warns on each deploy; that is acceptable (it is
a real problem) and needs no dedupe.

## Slice 5b — the web page (next, not this slice)

Recorded here so this slice collects what it needs. An admin-only dashboard page per
hunt, next to Server settings, showing: who is on the team server now; who was invited
but hasn't arrived (`hunt_invites`); and the full access history (`hunt_access_log`).
Read-only first. It gets its own design and plan.

## Constraints

- **Migration → deploy → code** (`migrate.py` golden rule). A push deploys: `deploy.sh`
  runs `migrate.py` and restarts the bot, on the test instance for `staging` and live for
  `main`.
- Listener errors never escape into discord.py (pattern of `bot/cogs/team_servers.py`).
- Invite links never appear in a channel players can see.
- Discord's audit log needs View Audit Log; the bot has Administrator on team servers.

## Left to the builder

- How to match a by-hand change to its audit-log entry (timing window, the one retry),
  within the rule above: never drop a row.
- Where the in-memory notes live (a small module beside `team_invites`) and their exact
  shape.
- Message wording, following the existing style (`:warning:`, `:door:`, names in bold,
  never a link outside the admin channel).
- Whether `!setup_server`'s completion posts the invite itself or calls the same helper
  as `!mod_invite` (preferably the latter).
- Store method names and test layout, following slice 4 (`tests/test_team_invites*`,
  live-DB tests for SQL).

## Acceptance

1. After `!setup_server` completes, the hub admin channel holds a reusable invite to the
   team server; a hub admin who follows it and clicks **Join** gets the team role and `mod`.
2. `!mod_invite <hunt name>` on the hub re-posts a working invite.
3. The admin channel is refused (nothing posted, reason given) if `@everyone` can see it.
4. A 30-person `!send_invites` arrival puts **nothing** in the hub admin channel, and 30
   `arrive` + 30 `grant` rows in `hunt_access_log`.
5. `mod` given by hand on a team server produces an `elevate` row with `via='hand'` and the
   giver as `actor_id`, and a line in the admin channel.
6. Slice 4's hub-role-loss removal produces `revoke` (and `depart`) rows and a line in the
   admin channel.
7. Restarting the bot with a team hunt whose server the bot has left posts one warning in
   that hub's admin channel; with nothing wrong, it posts nothing.
8. `!archive_hunt_to_hub` deletes the mod invite.

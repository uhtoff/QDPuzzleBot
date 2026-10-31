# Slice 5b — Team servers page, and the hub's template link

**Date:** 2026-10-31
**Status:** Implemented 2026-10-31 — plan docs/superpowers/plans/2026-10-31-team-servers-page.md
**Builds on:** slice 5 (`docs/superpowers/specs/2026-10-31-access-log-and-hub-admin-channel-design.md`),
which collects the data this page shows.

## Intent

Slice 5 records every change to a team server's team role and `mod` in `hunt_access_log`,
but nothing shows it. A hub admin also can't see a team hunt on the dashboard at all
unless they're on that team server (`bot/web/auth.py: hunt_visible` needs membership of
the hunt's guild). So the record needs a home **on the hub**.

Separately, making a team server starts from the guild template's link, which today lives
in someone's notes. The hub should hold it.

## Goals

1. A read-only **Team servers** page on the dashboard, per hub, for hub admins, listing every
   team hunt that uses this hub — **running and archived** (user decision: "otherwise why
   persist the history in the first place if you can't see it") — and for each:
   - **On the server now** (running hunts only): who is on the team server, and whether
     they hold the team role and/or `mod`. Read live from the bot's member cache, not
     reconstructed from the log (the log misses the setup owner's grant — see slice 5's
     known gap — and `arrive` can land after `grant`).
   - **Invited, not arrived**: `hunt_invites` rows with no `joined_at` for people not on the
     server now.
   - **History**: every `hunt_access_log` row, newest first — when, who, what (gained/lost
     team role or `mod`, arrived, left), how (`via` in plain words), and by whom for hand
     changes.
2. **`!set_template_link <link>`** and **`!show_template_link`**, Admin tier, on the hub,
   storing the template link in that server's settings. The link is also editable on the
   dashboard's Server settings page (command-parity principle).

## Non-goals

- Editing access from the page (no grant/revoke/kick buttons). Read-only.
- Dashboard equivalents of `!setup_server`, `!send_invites`, `!mod_invite`.
- Creating team servers from the bot (the user notes bot-made servers are a bad idea; the
  template link is only stored and shown).
- Changing `hunt_visible`. Team hunts stay invisible to non-members on the hunt list; the
  Team servers page is the hub admin's view.

## Anti-goals

- **Showing the page to non-admins** of the hub, or exposing one hub's team hunts on
  another hub's page.
- **The template link posted where players can see it.** `!show_template_link` posts
  **only in the hub admin channel** (user decision), through `hub_channel.post`, so it gets
  the same `@everyone` safety check as the mod invite.
- **Leaking invite links.** The page shows no invite URLs or codes (`hunt_invites.code`,
  `hunts.mod_invite_code` stay server-side).

## Decided

### Which hunts a hub's page lists

A hunt belongs to hub H when its `roster_role_id` is a role on H (`H.get_role(id)` is not
None) — the same rule `!mod_invite` and slice 4 use. That covers:

- **running** team hunts: `archived = 0`, homed on their team server;
- **archived** team hunts: `archived = 1`, re-homed to the hub by `!archive_hunt_to_hub`,
  `roster_role_id` kept.

A hunt whose roster role has since been deleted from the hub drops off the list (no way to
tie it to a hub). Accepted (my call); its rows remain in the database.

Ordering: running first, then archived; within each, newest hunt first (by `hunts.id`
descending). (My call.)

### The page

- Route `/guilds/:guildId/teams` (same `guilds.id` row id as `/guilds/:guildId/settings`),
  linked from the Server settings page and wherever that page is linked (the avatar menu).
- Gate: **Admin tier on that hub**, exactly as Server settings (`_guild_settings_ctx` /
  `require_admin`).
- Layout (my call): a list of hunts — name, team (the hub role's name), running/archived,
  counts (on server / invited-not-arrived) — each expanding to the three sections above.
  History is shown in full; no paging in this slice (a hunt's log is hundreds of rows at
  most).
- Display names: the stored `username` at the time; for actors, the current display name
  from the hub if the bot can see them, else the id.
- Plain words for `via`: button → "by the Join/Leave button", arrival → "on arrival",
  hub_role_lost → "lost the hub role", hub_admin_lost → "lost hub admin", hand → "by hand
  (by <actor>)", self → "left on their own".
- Times shown in the viewer's local time, like the rest of the dashboard.

### API

- `GET /api/guilds/<guild_id>/team-hunts` → the hub's team hunts (uid, name, team name,
  archived, team server name or null, counts).
- `GET /api/guilds/<guild_id>/team-hunts/<hunt_uid>` → one hunt's three sections. Refuses
  (404) a hunt that doesn't belong to this hub by the rule above.
- Both admin-gated as above. All Discord ids leave as strings (the existing rule in
  `bot/web/actions.py: _role_ids_out`).
- "On the server now" comes from `bot.get_guild(team guild).members` (the bot has the
  Server Members intent); if the bot isn't on the team server, the section is empty with a
  note "I'm not on that server", not an error.

### Details settled after the builder check (my calls)

- **"`mod`" on the page** means any of the team server's Admin-tier roles
  (`role_panel.admin_roles(team_guild)`), the same set the access log's `elevate`/`demote`
  use. On a template server that is the one `mod` role.
- **The team server and team role** of a running hunt are the hunt's own guild and
  `hunt.role_id` (as `access_log._watched` uses). If the team role no longer exists there,
  the section says so and shows nobody as holding it.
- **"On the server now" lists every non-bot member** of the team server (team servers are
  tens of people), each flagged team role / `mod` / neither, sorted `mod` first, then team
  role, then neither — the "neither" group shows who is stuck in `#lobby`.
- **Hunts are addressed by `hunts.uid`** (unique since migration 040, unchanged by
  archiving, which only re-homes the row); the route resolves uid → id for the
  `hunt_invites` / `hunt_access_log` lookups.
- **`!show_template_link`** calls `hub_channel.find` for the refusal reason and then
  `hub_channel.post`; the tiny window between them is acceptable. It runs in any channel of
  the hub (Admin tier) and only ever posts in the admin channel. `!set_template_link` keeps
  the bot-channel check as a hard gate.
- **Empty and error states:** a hub with no team hunts shows the page with an empty state;
  the list route uses the same admin context as Server settings, so a hub the bot isn't on
  gets the same `bot_unavailable` error. In the detail view, a team server the bot has left
  shows "I'm not on that server" in place of the members list.

### The template link

- New column `guilds.template_link` (VARCHAR(255) NULL), migration `050_template_link.sql`;
  a normal `GuildSettings` field `template_link: str = ""`, written by the ordinary
  settings `commit()` and added to the dashboard's editable settings keys.
- Accepted forms: `https://discord.new/<code>`, `discord.new/<code>`,
  `https://discord.com/template/<code>`; stored canonically as
  `https://discord.new/<code>`. Anything else is refused with the accepted forms listed.
  One shared validator used by the command and the dashboard.
- `!set_template_link <link>` — Admin tier, guild-only, bot-channel check. With no link it
  clears the setting. Replies with what was stored (the link itself is fine in the reply
  — it's the admin running the command in the bot channel; *my call — see below*).
- `!show_template_link` — Admin tier, guild-only. Posts the link and the browser fallback
  `https://discord.com/template/<code>` **in the hub admin channel only**; replies "Posted
  in #<channel>" or the refusal reason; never puts the link in the reply.
- Dashboard: a "Team-server template link" field on Server settings, same validation.

## Next: 5c — dashboard setup (not this slice)

User idea, 2026-10-31: the template-server greeting links to a dashboard setup page instead
of asking for ids. The page (for the team server's owner, logged into the dashboard) offers
a dropdown of hubs where the viewer is a hub admin and the bot is present, then a dropdown
of that hub's roles (excluding `@everyone`, bot-managed and admin roles), plus hunt URL and
name, and runs the same `server_setup` code as `!setup_server`. Inviting the bot and
dragging its role above `mod` stay manual; the page checks the role order first.

5c also offers, as an optional "advanced" section, the hunt settings an organiser sets at
the start — the fields the hunt's `AdminSettingsPanel` already edits (website URL, start
time + timezone, shared username/password, URL separator, puzzle prefix) — plus a
solved-puzzle-sheet option (`!use_solved_sheet` has no dashboard equivalent yet; 5c adds
it there and to the hunt panel) and, once the roadmap's per-puzzle sheets exist, their
option too (user request, 2026-10-31). The hunt part of the form should reuse the dashboard's create-hunt
form (`POST /api/hunts`, `frontend/src/components/CreateControls.tsx`), which is already
richer than `!hunt` because Discord commands can't comfortably stack many options (user
note, 2026-10-31).

5c also fixes the hunt panel's **Archive hunt** button for team hunts: today it does the
in-place archive, which strands the history on a server that is about to be thrown away.
For a hunt on a template-made team server (one with a `roster_role_id`) the dashboard must
archive **to the hub**, as `!archive_hunt_to_hub` does (user decision, 2026-10-31).

## Constraints

- Migration → deploy → code; a push deploys (`deploy.sh` runs `migrate.py`). Check 050 is
  free before merging.
- Every bang command has a web equivalent where practical (project principle) — the
  template link does; the page itself is web-only by nature.
- Frontend tests cover pure helpers only (no DOM test runner exists); backend routes get
  pytest tests in the style of `tests/test_guild_settings_web.py`.

## Calls I made

- `!set_template_link`'s confirmation repeats the stored link in the bot channel. If you'd
  rather it never appear outside the admin channel, it can reply "Stored." instead.
- Hunts whose hub role was deleted drop off the page.
- History is unpaged.

## Left to the builder

- Component structure and styling, following `GuildSettings.tsx` and the existing API
  client (`frontend/src/api.ts`, react-query).
- Store method names and SQL, following slice 4/5 (`live_team_hunts`, `for_hunt`).
- Exact empty-state wording.

## Acceptance

1. A hub admin opens Team servers on the hub and sees every running and archived team hunt
   whose hub role is on that hub; a non-admin gets the same refusal as Server settings.
2. A running hunt shows who is on its team server now (with team role / `mod`), who was
   invited but hasn't arrived, and its full history with plain-words `via` and hand actors.
3. An archived hunt shows invited-not-arrived and history, and no "on the server now".
4. No invite URL or code appears in any API response.
5. `!set_template_link discord.new/abc123` stores `https://discord.new/abc123`; a bad link
   is refused; no argument clears it; the dashboard field reads and writes the same value.
6. `!show_template_link` posts the link and the browser fallback in the hub admin channel
   only, and refuses (posting nothing) if that channel is visible to `@everyone`.

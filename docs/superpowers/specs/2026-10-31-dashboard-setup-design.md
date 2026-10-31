# Slice 5c — dashboard setup for team servers, and archiving them from the dashboard

**Date:** 2026-10-31
**Status:** Implemented 2026-10-31 — plan docs/superpowers/plans/2026-10-31-dashboard-setup.md
**Builds on:** slices 2–5b (`!setup_server`, invites, access log, Team servers page). The
idea is recorded in `docs/superpowers/specs/2026-10-31-team-servers-page-and-template-link-design.md`
("Next: 5c").

## Intent

Setting up a team server today means copying two Discord ids into
`!setup_server <hub id> <hub role id> <hunt url> <hunt name>` — fiddly, and the command
line can't comfortably carry the other options an organiser sets at the start (the
dashboard's create-hunt form is already richer than `!hunt` for that reason — user note).
Separately, the hunt panel's **Archive hunt** button does an in-place archive, which for a
team hunt strands its history on a server that's about to be thrown away.

User's words, 2026-10-31: the template greeting should "direct to the dashboard to do the
setup rather than find the IDs and run the command … a dropdown of server and then a
dropdown of roles"; include "the other things we'd usually set up at start … time start,
username password etc. Plus options like archiving and separate sheets for puzzles";
"template made throwaway servers should archive to the hub"; "Could the setup modal have
a 'Send invites' toggle?"

## Goals

1. **A setup page** on the dashboard for a freshly cloned team server: pick the hub, pick the
   hub role, fill in the hunt, optionally send invites, press Set up — the same result as
   `!setup_server` (and `!send_invites`), with progress and any "fix this and re-run" shown on
   the page.
2. **The template greeting links to it**, keeping the command as the fallback.
3. **Archiving a team hunt from the dashboard archives it to the hub**, as
   `!archive_hunt_to_hub` does.

## Non-goals

- Inviting the bot or dragging its role above `mod` (Discord only lets a person do these; the
  page checks the role order and says so).
- Per-puzzle sheets (not built yet; when they exist, their option joins this form).
- A web `!send_invites` outside setup, `!invite_player`, or `!mod_invite` buttons.
- Forum layout (left to go stale; the setup form doesn't offer it).

## Anti-goals

- **Two setup implementations.** The bang command and the page must run one shared function
  (the project has been bitten by divergent bang/web paths before — see the memory note on
  dual entrypoints). Same for archive-to-hub.
- **A weaker gate on the web.** The page enforces exactly what `!setup_server` enforces: the
  viewer owns the team server or holds its Admin tier, and is an admin on the chosen hub.
- **An irreversible click by accident.** Archive-to-hub leaves the server; it needs the hunt
  name typed to confirm.

## Decided

### The shared setup function

Extract the body of `!setup_server` (`bot/cogs/puzzles.py: setup_server`) into
`server_setup.perform_setup(bot, service, guild, invoker, hub_id, roster_role_id, hunt_name,
hunt_url, *, start_timestamp=0, username="", password="", create_solved_sheet=False) ->
SetupOutcome` (name and exact shape are the builder's), covering: settings-row and
run-once checks, owner/admin check, `resolve_hub`, `preflight`, `run_setup`, the hunt welcome,
the mod invite (slice 5), and the completion lines. `!setup_server` becomes a thin caller that
sends the outcome's lines. The new hunt fields pass through to `service.create_hunt` (which
already takes them); `create_solved_sheet` uses the existing solved-sheet path.
`invoker` is a `discord.Member` of the team server (the web resolves it from the logged-in
user's id; not a member → refused).

### The setup page

- Route `/guilds/:guildId/setup` (the team server's `guilds.id` row id, as the other guild
  pages use).
- Offered only while the server is unset-up: `setup_complete_time` is null. After setup it
  shows "Already set up on <date>" and links to the hunt.
- **API** (both admin-gated *for the team server* as above):
  - `GET /api/guilds/<id>/setup` → `{template_ok: bool, template_problem: str|null,
    bot_above_mod: bool, already_set_up: str|null, hubs: [{id, name, roles: [{id, name}]}]}`.
    `template_ok` / `template_problem` come from `preflight`'s checks (run without a hub
    where possible, or a dedicated check — builder's call); `hubs` lists every guild the bot
    is on, other than this one, where the viewer holds the Admin tier; `roles` excludes
    `@everyone`, managed (bot/integration) roles and the hub's Admin-tier roles, sorted by
    position descending. Ids as strings.
  - `POST /api/guilds/<id>/setup` with `{hub_id, roster_role_id, hunt_name, hunt_url,
    start_timestamp?, username?, password?, create_solved_sheet?, send_invites?}` → runs
    `perform_setup`; if it completed and `send_invites` is true, runs
    `team_invites.send_invites` and includes `report_text`. Returns `{ok, steps: [str],
    error: str|null, hunt_uid: str|null, invites: str|null}`. Synchronous (setup takes
    seconds). A re-POST after an error finishes the job, as re-running the command does.
- **Form** (*my call* on layout): status box first (template ✓/✗ with the problem; "Drag my
  role above `mod`" ✗ when `bot_above_mod` is false, with the Set up button disabled);
  Hub (Select) → Hub role (Select, filtered to that hub); Hunt name; Hunt website; an
  "Advanced" section: start time + timezone, shared username/password, solved-puzzle sheet
  (Switch) — reusing `CreateControls.tsx`'s field components and validation where they can
  be shared; **Send invites when setup finishes** (Switch, **off by default** — the walk-through
  asks the mod to look the server over first; the description says so).
- Result: the step list, the error (red) with "Fix that and press Set up again", or success
  with the invites report and links to the hunt board and the Team servers page.

### The greeting

`TEMPLATE_SETUP_MESSAGE` leads with "Set me up on the dashboard: <link>" where the link is
`config.web_frontend_url` + `/guilds/<row id>/setup`; the command form stays below as the
alternative. If `web_frontend_url` is unset, the greeting is unchanged.

### Archiving a team hunt from the dashboard

- Extract the locked part of `!archive_hunt_to_hub` (from `puzzles_lock` to the summary posts)
  into one shared coroutine used by the command and the web.
- `POST /api/hunts/<uid>/archive` (the in-place toggle) refuses to archive a hunt that has a
  `roster_role_id` and is not archived: `409 {"error": "use_hub_archive"}`. Unarchiving is
  unchanged.
- New `POST /api/hunts/<uid>/archive-to-hub` with `{confirm_name}`: Admin tier on the team
  server, `confirm_name` must equal the hunt's name exactly (else `400 confirm_mismatch`), the
  same pre-checks as the command (not forum, not archived, roster role findable), then
  `resolve_hub` with the viewer as invoker (must be a hub admin). It **starts** the archive as
  a background task and returns `202 {"started": true}` — archiving can take minutes, longer
  than a request should wait. Progress goes where the command's does: a status message in the
  hub (see "Settled after the builder check", 1). A second POST while one is running → `409 archive_running`.
- **Frontend:** for a team hunt, the hunt panel's Archive button opens a dialog that says what
  happens (history moves to the hub, the bot leaves the server, not reversible), asks for the
  hunt name, and on 202 shows "Archiving started — progress and the result will appear in
  #<progress_channel> on the hub."

### Settled after the builder check (my calls)

1. **Archive progress goes to the hub, not the team server** — the archive deletes the team
   server's channels, `#bot-commands` included. The status message (edited as it goes) is
   posted in the hub admin channel when `hub_channel.find(hub)` gives one, else in the new
   hub archive channel; the summary goes where the command sends it plus the hub admin
   channel. The dashboard is fire-and-forget: the dialog's success text names the hub admin
   channel ("Progress and the result will appear in #<name> on <hub>"), so the route returns
   `{started: true, progress_channel: <name or null>}`.
2. **The setup routes use their own gate: owner OR Admin tier on the team server** (the
   command's rule), not `require_admin` — a fresh clone's owner usually doesn't hold `mod`
   yet. Compare `guild.owner_id` with the logged-in user's id, else check the tier.
3. **Hubs listed** are guilds the bot is on that (a) have a settings row, (b) are not this
   server, (c) are not themselves team servers (no `setup_complete_time` and no live team
   hunt), and (d) where the viewer holds the Admin tier (`hunt_archive.hub_member` +
   `role_gate.member_holds_tier`, per guild — the bot shares few guilds).
4. **A retry after a partial failure is prefilled**: GET includes `existing: {hub_id,
   roster_role_id, hunt_name, hunt_url} | null` when a hunt already exists on this server, and
   the form starts from it (so `preflight`'s "already tied to another hub role" can't be hit by
   accident).
5. **`preflight` is split** into a hub-free `template_problems(guild, settings) -> list[str]`
   (template shape, bot role above `mod`, run-once lock) and the hub-dependent rest; the GET
   uses the first, `perform_setup` uses both. Messages unchanged.
6. **Web archive-to-hub is only for hunts with a hub role.** Other hunts keep the in-place
   toggle; the command's explicit `hub_id` path stays bang-only.
7. **Error texts** for not-a-hub-member / not-a-hub-admin are the command's (`HubUnavailable`
   messages), passed through as `{error: "hub_unavailable", message}`.

## Constraints

- Migration → deploy → code; a push deploys. (No migration is expected.)
- Every web route that mutates is audited (`_audit`) and in `tests/test_web_route_gating.py`.
- Lock ordering: the archive keeps the command's `puzzles_lock(member_puzzle_ids)` around the
  service call; confirm happens before locking (the typed name *is* the confirmation).
- Frontend tests are pure helpers only; backend routes get pytest tests.

## Left to the builder

- Names and exact signatures of the shared functions; how the template check runs before a
  hub is chosen.
- Reusing `CreateControls.tsx` pieces versus a small local form, following existing style.
- How the background archive task is tracked (a module-level set of running hunt ids is
  enough).

## Acceptance

1. A fresh template server's greeting links to the setup page.
2. The page lists only hubs where the viewer is an admin and only assignable roles; it shows
   the role-order problem and disables Set up until fixed.
3. Set up with advanced fields produces the same server state as `!setup_server`, with the
   start time, login and solved sheet applied; `!setup_server` still works and runs the same
   code.
4. With Send invites on, the invites go out and the report shows on the page; with it off,
   nothing is sent.
5. A viewer who isn't the owner/admin of the team server, or isn't a hub admin, is refused
   with the same messages the command gives.
6. The dashboard Archive button on a team hunt asks for the hunt name and archives to the hub
   (progress in Discord); the in-place archive route refuses team hunts.

# Fresh Discord server per hunt-team — design

**Date:** 2026-07-31
**Status:** Design approved; slice 1 (cross-server archive, as !archive_hunt_to_hub) implemented — see plan 2026-09-24.
**Supersedes:** the *reusable pool* decision in
`docs/future/2026-07-05-multi-server-hunt-architecture-design.md`. That doc's problem
statement, Discord constraints, and rejected approaches all still stand and are not
repeated here; only the pool-of-vessels choice is replaced.

## Goal

Run each team of a multi-team hunt in its own **freshly created, then discarded** Discord
server, cloned by a human from a **guild template**, with the bot doing all structural
setup via a single `!setup_server` command and inviting the team privately by DM.

This keeps the two properties the July design was built for — a fresh 50-category budget
per team, and no cross-team visibility — while dropping the long-lived vessel pool in
favour of a clean server each hunt.

## In scope

- The guild template's contents, and the split between template / human / bot.
- `!setup_server` — what it does, and its idempotency requirements.
- Private invite delivery by DM, with auto-grant of the team role on join.
- The `#lobby` join/leave role panel for existing members.
- A durable access audit trail that outlives the discarded server.
- Data model additions and the archive path back to the hub.

## Out of scope

- Single-team hunts. They stay on the hub exactly as today; this applies only to
  multi-team hunts, where both the category cap and the peeking risk appear.
- OAuth `guilds.join` auto-add (July doc's optional friction-killer). Still deferred.
- Any change to how hunts, rounds, puzzles or sheets are modelled.

## Architecture

### The hub (permanent, unchanged)

The hunt is created on the hub as it is today: one **roster role per team per hunt**,
created fresh each hunt because teams differ between hunts. Players self-assign there
through the existing flow. The hub also receives the archived record at hunt end and
holds the durable audit mirror.

The roster role **is** the DM list. No separate roster concept, no new persistent state.

### The template (built once by hand, re-synced when it drifts)

Created from a model server via Server Settings → Template, giving a `discord.new/<code>`
link. A template carries server settings, roles (name, colour, hoist, mentionable,
permissions — including `@everyone`), and channels/categories with their permission
overwrites wired to those templated roles. It carries **no** bots, emojis, stickers,
members, messages, webhooks, invites, integrations, AutoMod rules, scheduled events,
Onboarding, or Community configuration.

Contents:

| Item | Configuration | Why |
| --- | --- | --- |
| `@everyone` | `View Channels` removed | Clone is locked down by default |
| `hunt-team` role | No special permissions | Gates the hunt; the `clone()` seed's subject |
| `mod` role | Manage Channels + Manage Messages, **not** Administrator | Organisers get no puzzle sight by default. **Must be named to match `ADMIN_ROLES`** (`bot/core/constants.py:10`) — the bot's own command gates check role *names* |
| `#lobby` | `@everyone: allow View` | Only visible channel; holds welcome + role panel |
| `#bot-commands` | `hunt-team: allow View` | Matches `GuildSettings.discord_bot_channel` |
| `Hunt` category | `@everyone: deny View`, `hunt-team: allow View` | **Replaces the manual overwrite seam** |
| `#general` (in `Hunt`) | Inherits | Where `!archive_hunt` writes threads today |
| Server settings | Notifications = mentions only; join messages suppressed; verification low | Puzzle channels are noisy |

The pre-gated `Hunt` category is the single biggest win: today the hunt-role view
overwrite is set **by hand** on the first hunt category and later categories `clone()`
from it. The template ships it pre-wired, so the manual step disappears.

### The human, per team per hunt

Two clicks, both irreducible — `POST /guilds/templates/{code}` is blocked at ≥10 guilds
just as `create_guild` is, so the bot cannot self-serve either:

1. Click the template link to create the server.
2. Authorize the bot via OAuth (`scope=bot`, `permissions=8`), which creates its managed
   role.

**Who clicks: a shared group account**, never an individual's. Once setup succeeds an
optional third step follows — **transfer ownership to a member of that team**, after which
the group account leaves. Whether to transfer is decided per team; see *Ownership and admin*.

### The bot: `!setup_server <hunt-uid> <team-name> [--organisers]`

`on_guild_join` already creates the `guilds` row (`bot/cogs/settings.py:28`), so the
command fills in the rest:

1. **Preflight, refuse loudly on failure:** bot has the needed permissions; the bot's
   managed role sits **above** both `hunt-team` and `mod` (it cannot assign a role above
   its own, and it must be able to grant `mod` for elevation); the expected template roles
   and channels exist, with the mod role named to match `ADMIN_ROLES`.
2. Populate `GuildSettings` — `discord_bot_channel`, `drive_parent_id`,
   `drive_resources_id`, `website_url`.
3. Call the existing `create_hunt` (`bot/cogs/puzzles.py:628`, already takes a
   `role_name`) to build the hunt row, team role and category tree.
4. Record `roster_role_id` against the hunt.
5. Upload the standard emoji set (bots may create emojis; templates cannot carry them). With
   `--organisers`, also create the `Organiser` role, which arms the organiser-tier gate.
6. Post the welcome message and the join/leave role panel in `#lobby`.
7. Create one **single-use invite per rostered player** and DM it, recording each in
   `hunt_invites`.
8. Report per-step status, including every player whose DM failed.

## Invite delivery

Private by construction: a `max_uses=1` invite per player, delivered by DM. No link
exists in any channel. The bot can DM them because it shares the permanent hub with them.

`on_member_join` looks the used invite code up in `hunt_invites` and **grants the team
role automatically**, so a DM'd player goes from click to playing with no further action.
Invited players therefore never touch the role panel.

Related commands:

- `!invite <user>` — re-issue one invite (for DM failures handed over by an organiser).
- Automatic late-joiner handling: an `on_member_update` listener on the hub DMs an invite
  when someone gains a roster role for a live hunt. No organiser action needed for a
  player joining a team mid-hunt.

## The `#lobby` role panel

A **persistent** view (`timeout=None`, fixed `custom_id`s of the form
`hunt_role:join:<hunt_uid>` / `hunt_role:leave:<hunt_uid>`), registered with
`bot.add_view()` on startup so the buttons survive a restart. This is the one difference
from the existing `ConfirmView` (`bot/cogs/ui.py:7`), which is ephemeral and author-locked.
No view state is stored: the `custom_id` carries the hunt, the role comes from
`hunt.role_id`.

**Two buttons, not a toggle** — a component's label is identical for every viewer, so a
state-aware toggle would read wrong for half the channel. Clicking the inapplicable one
gets an ephemeral no-op reply.

Who it serves: mods opting in and out, and anyone the auto-grant missed. Behaviour:

- Grants and removes the team role. Never kicks anyone from the server.
- **Mod elevation:** on join, the bot checks whether the clicker holds an `ADMIN_ROLES`
  role **in the hub** (it is a hub member, so it can fetch them) and, if so, also grants
  the local `mod` role. Leave removes both. Elevation therefore derives from hub mod
  status — no per-server configuration.
- Ephemeral replies, so `#lobby` stays clean.
- Leave is offered to everyone. A player who drops their role loses dashboard sight until
  they rejoin — reversible and harmless.

### Why mods get `mod`, not Administrator

One click has to be enough for an organiser to go in and, say, archive the hunt at the end
— but that needs no Discord privilege at all beyond seeing the channels:

- `archive_hunt` is gated by `@commands.has_any_role(*ADMIN_ROLES)`
  (`bot/cogs/puzzles.py:3696`) — a **role-name** check. A role named `mod` satisfies it.
- The rest of the structural surface uses `@commands.has_permissions(manage_channels=True)`,
  which **Manage Channels** alone satisfies.

The `mod` role clears **every** permission tier in the bot — verified by enumerating the
gates in `bot/cogs/`:

| Gate | Commands | Satisfied by `mod` |
| --- | --- | --- |
| `has_any_role(*ADMIN_ROLES)` | 22 | Yes — the role is *named* `mod` |
| `has_permissions(manage_channels=True)` | 12 | Yes — Manage Channels |
| `@is_organiser()` (`bot/cogs/puzzles.py:42`) | 21 | Yes — `ORGANISER_ROLES = ADMIN_ROLES + ('Organiser', …)` |

All 55 gated commands, with no Administrator involved. Dashboard parity comes free:
`user_can_manage` checks `ADMIN_ROLES` against the user's roles **in that guild**
(`bot/web/auth.py:242`), so `mod` in the team server unlocks every admin action on the web
side too.

**Decision — the template ships no `Organiser` role.** `organiser_gate_active`
(`bot/core/constants.py:15`) returns False when a guild has no `Organiser`-named role, and
when False the 21 organiser-tier commands pass for **everyone**. That is the wanted
behaviour here: a server holds one team, so players should manage their own puzzles, rounds
and tags without waiting on anyone. The 22 admin-tier commands remain locked to `mod`
regardless — that tier is never affected by the gate.

**Opting in per hunt.** The gate is armed by the mere *existence* of a role named
`Organiser` / `Organisers` / `Organizer` / `Organizers` — `organiser_gate_active` tests
`any(r.name in ORGANISER_ROLE_NAMES for r in guild.roles)`, not whether anyone holds it, and
hierarchy position is irrelevant. Since this is a per-hunt choice (expected to be used for
MIT only) rather than a property of the template, `!setup_server` takes an `--organisers`
flag that creates the role. One template then serves both cases.

**Trap to avoid:** creating the role and assigning it to nobody locks all 21 commands to
`mod` holders, leaving the team unable to do routine puzzle management. When the flag is
used, the role must be populated in the same session — `!setup_server` should say so in its
completion report.

The bot holds Administrator and does the real work either way, so granting a human
Administrator adds nothing: with one team per server, admin reveals only the team whose
role they just took. What it *would* add is the ability to delete channels, undo the
setup, or remove the bot.

It also breaks the audit property. Manage Channels *can* be abused to see another team,
but only by editing a permission overwrite — a **mutation**, which the audit log records.
Administrator bypasses every overwrite **silently**. That is the same reason organisers
are denied admin in the first place, so it would be inconsistent to hand it back here.

Why the role exists at all, given the server already holds exactly one team:

- **The dashboard requires it.** `hunt_visible` returns False for an active hunt unless
  `hunt.role_id` is among the user's roles in that guild (`bot/web/auth.py:255-272`);
  guild membership alone is not enough.
- **Invites only fire on join.** Anyone *already* in the server gains nothing from
  clicking a link, so mods and organisers need an in-server path.

## Access audit trail

Role grants and removals do land in Discord's guild audit log (annotatable via the
`reason=` parameter), but that log lives in the **team server**, retains 45 days, and is
**destroyed when the server is discarded**. So it cannot be the record. The bot keeps its
own, in two places:

1. **Durable rows in the database** — a new `hunt_access_log` table, shaped on the
   existing `web_audit_log` (`migrations/006_web_audit_log.sql`): timestamp, `user_id`,
   `username`, action (`grant` / `revoke` / `elevate` / `demote`), `hunt_uid`, detail.
   Mod elevation is logged distinctly from a plain team-role grant, so organiser access is
   as traceable as player access.
2. **A mirror in the hub** — one line per grant/removal into an organiser-visible hub
   channel. Survives the discard, needs no schema, and is passively visible to the people
   it holds accountable.

**Hook `on_member_update`, not the button handler.** Logging only button clicks leaves a
hole exactly where it counts: a role added by hand through the Discord UI would not
appear. Watching member updates captures every route to the role.

Because access is *self*-assigned, the target of each role change is also its initiator,
so the record is unambiguous. Had an organiser granted roles instead, Discord's native log
would show only the bot as executor and lose who asked.

*Naming — decided and done:* `web_audit_log` was a general "which human did what" record
whose `web_` prefix stopped describing its contents once Discord-side role grants join the
dashboard mutations. Renamed to **`audit_log`** by `migrations/036_rename_audit_log.sql`,
with the write path at `bot/web/actions.py:60` updated. Migration 006 is left untouched, as
applied history. `hunt_access_log` therefore lands alongside a correctly-named sibling.

## Ownership and admin

**Human Administrator: nobody gets it, ever.** Organisers take the `mod` role — granted
automatically by the lobby panel to hub mods, see *Why mods get `mod`, not Administrator* —
which covers everything they need including archiving. Administrator bypasses every channel
overwrite silently and would undo the whole model.

**Bot Administrator** comes only from the OAuth URL (`permissions=8`). Strictly it needs
Manage Channels, Manage Roles, Manage Webhooks, Manage Emojis, Create Invite, Manage
Messages and View Audit Log, but the bot is the sole privileged actor by design, so
Administrator is the simpler and less error-prone ask.

**Guild owner** is unavoidable: whoever clicks the template link owns the server, bypasses
everything, and cannot be demoted.

**Decision: a shared group account creates the server and authorizes the bot, then
transfers ownership to a member of that team and leaves.**

Two reasons, in order:

1. **The group account does the technical setup**, because it is the account the organisers
   use constantly and the steps are fiddly. No individual's personal account is involved,
   so no real person acquires standing sight of a team they are not on.
2. **Ownership then lands on someone already entitled to see that team.** Owner bypass is
   unavoidable and cannot be revoked, so the goal is to point it at someone for whom it
   grants nothing they lack. User-to-user ownership transfer is a normal supported action
   (unlike bot ownership transfer, which July ruled out).

After the transfer the group account holds keys to nothing, which matters: had it retained
ownership of all N servers, anyone with its credentials could see every team's puzzles, and
since Discord logs mutations but never views, that peek would be undetectable. Transferring
converts a procedural control — hold the credentials narrowly, do not log in during a hunt
— into a structural one.

**Transfer is per-server and optional.** It is a judgement call for each team, not a global
switch, because the two choices optimise different things:

- **Transfer** minimises the residual bypass, and is the default. Only the owner can delete
  a guild, but that barely matters here: by the time the hunt ends the history has been
  threaded into the hub and the server is **empty**, so an abandoned one is harmless.
  Deletion is tidiness, not a requirement.
- **Retention** is for a team of brand-new members, where the reluctance is simply to hand
  ownership of a server to someone not yet known. It costs little: the group account owning
  *one* server exposes one team, a far smaller residual than owning all of them.

Ownership can move at any time, so a team that turns out to have a reliable regular can be
transferred mid-hunt.

**Residual:** between creation and transfer the group account owns the server — minutes,
before any puzzle content exists. For any server left untransferred the procedural control
applies (credentials held narrowly, 2FA, nobody logging in during a live hunt) for that
server alone.

## Data model

`hunts.guild_id` and `hunts.role_id` keep their current meaning — the hunt lives in the
guild where it is played, gated by a role in that guild — which is exactly what
`hunt_visible` checks. No rename, no auth change. What is new:

- **No stored hub or archive channel.** *Changed 2026-09-24:* the hub is named on the
  command, `!archive_hunt_to_hub <hub server id>`, which checks that the invoker is an
  admin on that hub. The bot creates a new uncategorised hub channel per hunt, named as
  archived channels are today, and tags it with the topic `puzzbot-archive:<hunt uid>` so
  a re-run resumes into it. `!setup_server` may later store a default hub. Implemented by
  `docs/superpowers/plans/2026-09-24-cross-server-hunt-archive.md`.
- **`hunts.roster_role_id`** — the hub-side role that is the DM list.
- **`hunt_invites`** — `code`, `hunt_id`, `role_id`, `user_id`, `used_at`. Must be
  persistent: the code→role map is what `on_member_join` consults, and an in-memory map
  dies with the next restart mid-hunt. It also records who was invited versus who arrived.
- **`hunt_access_log`** — see *Access audit trail*.

## Archiving and teardown

`!archive_hunt_to_hub <hub id>` writes the hunt's history threads into a new per-hunt
channel on the hub, the team server's general channel included. `!archive_hunt` is
unchanged and still archives in place.

**No webhook.** The July doc reached for a cross-guild webhook, but the bot is a member of
the hub and can fetch that channel and create threads directly — one less moving part, and
no webhook URL sitting in the database as a credential.

### Re-homing the hunt row to the hub

**`archive_hunt_to_hub` must also re-point the hunt row at the hub** — `guild_id` to the hub's
`guilds` row, `channel_id` to the new hub channel — otherwise archived hunts disappear
from the dashboard entirely. *the bot leaves only when no hunt row, archived or not, still
references the team server's `guilds` row, and only after a run with no failures.*

`hunt_visible` resolves the hunt's guild snowflake and returns False when it is absent from
the user's `membership`, **before** the `archived` short-circuit runs
(`bot/web/auth.py:261-267`). Today that is effectively open, because archived hunts sit on
the hub and everyone is a hub member. Left unchanged here, an archived hunt would point at an
abandoned or deleted team server that nobody is a member of, so it would be invisible to
every user including its own team.

Re-homing is the correct fix rather than a workaround: `guild_id` denotes the guild the hunt
lives in, and after archiving the hunt genuinely lives on the hub. Archived hunts then behave
exactly as they do today — open to every hub member.

Notes:

- **`role_id` is left as-is**, pointing at a role in the dead guild, as a historical record.
  Archived hunts never reach the role check, so it is inert. (Do not zero it — `role_id == 0`
  means "legacy hunt" in `hunt_visible` and is meaningful for active rows.)
- Puzzle rows keep channel IDs from the dead guild. That is already the case today, since
  archiving deletes the channels; `get_hunt_for_channel` (`bot/web/data.py:272`) is
  documented to work for archived hunts regardless.
- The dead server's own `guilds` row stays, simply unreferenced by any hunt.
- Re-homing must happen **before** the bot leaves the guild.

After that the server is **empty** — every puzzle channel is gone and the history lives on
the hub. So there is no required teardown step: the bot marks the hunt archived and
**leaves**, and the server can simply be abandoned. Deleting it is optional tidiness, and
only its owner can do so (a bot cannot delete a guild it does not own).

The bot leaving is the part that matters. Left in place, dead guilds accumulate in its guild
list and in `guilds` rows, cluttering the dashboard's guild picker.

## Failure cases

| Case | Handling |
| --- | --- |
| Player has DMs closed | Unavoidable. `!setup_server` reports every failure so an organiser can pass the invite along; `!invite <user>` re-issues. |
| Player joins a team mid-hunt | `on_member_update` on the hub DMs an invite automatically when a roster role is gained for a live hunt. |
| `!setup_server` dies halfway | Discord side-effects cannot roll back, so every step is check-then-create — the pattern `_ensure_guild_settings` already uses (`bot/cogs/settings.py:32`). Re-running finishes the job instead of duplicating it. |
| Bot's role sits below `hunt-team` | Caught in preflight and reported as a one-drag fix, rather than surfacing as an inscrutable permissions error mid-build. |
| Bot loses Administrator or is removed | The hunt is stranded and only a human can restore it. A startup check warns when a live hunt's guild is missing or the bot's permissions are insufficient. |
| Someone joins with no tracked invite | Lands in `#lobby` with no role; the panel is their way in. |

## Acceptance criteria

1. A human can clone the template, authorize the bot, run `!setup_server`, and end up with
   a working gated hunt without setting a single permission overwrite by hand.
2. Every rostered player receives a DM whose single-use invite drops them into the hunt
   with the team role already granted, and with dashboard access working.
3. No invite link for a team server ever appears in a channel.
4. Every grant and removal of a team role — by button, by command, or by hand in the
   Discord UI — appears in `hunt_access_log` and in the hub mirror, with mod elevation
   recorded distinctly.
5. A hub mod who clicks join on a team server can immediately see the hunt channels and run
   `!archive_hunt` there, without any human holding Administrator in that server.
6. Re-running `!setup_server` after a partial failure completes the setup without creating
   duplicate roles, channels, categories or invites.
7. `!archive_hunt` lands the hunt's history in the hub, intact, before the server is
   discarded.
8. The role panel's buttons still work after a bot restart.
9. After the team server is abandoned or deleted, its archived hunt is still visible on the
   dashboard to every hub member, exactly as archived hunts are today.

## Implementation notes

Existing seams this leans on, all confirmed present:

- `bot/cogs/settings.py:28` — `on_guild_join` already registers the guild.
- `bot/cogs/settings.py:32` — `_ensure_guild_settings`, the check-then-create pattern.
- `bot/cogs/puzzles.py:628` — `create_hunt`, already accepting a `role_name`.
- `bot/cogs/puzzles.py:3698` — `archive_hunt`, needing a cross-guild target.
- `bot/web/auth.py:255` — `hunt_visible`, unchanged by this design.
- `bot/cogs/ui.py` — home for the new persistent role-panel view.

Per the project's command-parity principle, `!setup_server` should have a dashboard
equivalent, but the bot must already be in the guild for either to work, so the bang
command is the primary path and the web route can follow.

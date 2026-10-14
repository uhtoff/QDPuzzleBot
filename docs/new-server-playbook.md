# New Discord server playbook

The ordered checklist for standing up a Discord server for a hunt. It assumes the bot
itself is already deployed and running — that is the README's
[Deploying on a Linux server](../README.md#deploying-on-a-linux-server) section, done once
per *bot instance*, not per server. Everything here is done once per *Discord server*.

One bot instance serves many servers, so running **a server per hunt** means running this
list per hunt. Two steps in it are manual and nothing in the code performs them — step 4
(pin the authorisation roles) and step 5 (the hunt role overwrite). Both are easy to miss
and neither fails loudly.

---

## 1. Invite the bot

From the [Discord Developer Portal](https://discord.com/developers/applications) →
**OAuth2 → URL Generator**, with the `bot` scope and: Manage Channels, Manage Roles, Send
Messages, Read Message History, Manage Messages, and — only if you use voice channels —
Connect / Move Members.

The three privileged gateway intents (Presence, Server Members, Message Content) are set
on the *application*, not per server, so they are already on if the bot works elsewhere.
**Server Members** is what makes the dashboard's role-based hunt visibility work; without
it every hunt looks invisible to everyone.

The bot creates the server's settings row automatically when it joins (`on_guild_join` in
`bot/cogs/settings.py`), so there is nothing to run by hand for that.

## 2. Create the roles

Three kinds, and they are separate things:

| Role | What it gates |
|---|---|
| **Team role** (e.g. `Team A`) | Which hunt a member can see on the dashboard, via the hunt's `role_id`. |
| **Organiser role** | The organiser tier: retagging, group changes, reconcile, the destructive routes. |
| **Admin role** | The admin tier: hunt lifecycle (`!hunt`, `!delete_hunt`, `!set_hunt_role`), settings, dashboard admin. |

Name them whatever suits the server — step 4 pins the tiers by role **id**, so the names
carry no meaning once that is done.

## 3. Point the bot at the right channel and Drive folder

On the dashboard under **Server settings**, or from any channel the bot can see:

```
!update_settings discord_bot_channel bot-commands
!update_settings drive_parent_id <Google Drive folder id>
!update_settings drive_resources_id <Google Doc id>      # optional
!update_settings discord_use_voice_channels true         # optional, off by default
```

`!show_settings` dumps the current values. Once `discord_bot_channel` is set, most commands
must be run in that channel.

## 4. Pin the authorisation roles — **do not skip this**

The bot posts this as a message when it joins, and the dashboard shows a banner to
admins until the **admin** tier is pinned, so you should not be able to miss it. Pinning
the *organiser* tier is optional (see the note below); pinning the *admin* tier is not.

Either from Discord:

```
!set_admin_roles @Your Admin Role
!set_organiser_roles @Your Organiser Role
!show_auth_roles
```

…or on the dashboard: the avatar menu → **Server settings**
(`/guilds/<id>/settings`), which shows both tiers, whether each is pinned or still
matched by name, and refuses a save that would lock you out.

Until you do this, the tiers are matched by role **name** — `Moderator`, `mod`, `admin` for
the admin tier; `Organiser`/`Organisers`/`Organizer`/`Organizers` for the organiser tier.
That means **anyone in the server who can create a role can create one called `admin`,
self-assign it, and hold full bot and dashboard admin**, including `!delete_hunt`. The names
can also pre-exist for unrelated reasons and hand the tier to people who were never meant to
have it.

`!show_auth_roles` tells you which state the server is in. Run it and confirm both tiers read
*pinned by id* before the hunt starts.

Notes:

- `!set_admin_roles` refuses to store roles you do not hold yourself — that would lock you
  out of the only command that could undo it. Include one of your own roles.
- Running either command with **no** roles clears the list and reverts that tier to name
  matching. That is the escape hatch, not a normal state.
- If the organiser tier is left unpinned **and** the server has no Organiser-named role, the
  organiser tier is open to every member — including `delete_puzzle`, `delete_tag_group`,
  `media_sweep` and `reconcile apply-orphans`. This is deliberate (a convenience for small
  single-team servers; M9 in the 2026-08-07 review) and pinning an organiser role by id is
  what shuts it.

## 5. Create the hunt, then set the role overwrite by hand — **also manual**

```
!hunt hunt-name:https://hunt.example.com, Team A
```

The trailing `, Team A` gates the hunt to that team role (`!set_hunt_role hunt-name, Team A`
sets it afterwards). Append `, forum` for the forum-backed layout — note the forum path is
being left to go stale; prefer the default category layout.

**Then, manually, in Discord:** set the team role's permission overwrite on the hunt's
**initial category**. Nothing in the bot does this. Every later category the bot creates
`clone()`s that first one, and channels inherit from their category, so the whole hunt
inherits whatever you set here — and if you set nothing, the whole hunt is ungated in
Discord no matter what `role_id` says on the dashboard.

Because it is a one-time manual seam, searching the code for where the overwrite gets
applied turns up nothing and looks like a missing implementation. It is not.

## 6. Hunt settings

```
!set_start <unix timestamp>
!set_login username:password      # if the hunt has a shared login
!use_solved_sheet                 # move solved puzzles to a second spreadsheet
```

`!use_solved_sheet` is worth it for a big hunt — it keeps the main spreadsheet fast.

## 7. Sanity check before the hunt opens

- `!show_auth_roles` (or **Server settings** on the dashboard) → both tiers
  *pinned by id*.
- `!show_settings` → bot channel and Drive ids as intended.
- The team role's overwrite is on the hunt category (step 5), and someone *without* the
  role genuinely cannot see it.
- Log in to the dashboard as a normal team member and confirm the hunt is visible, and as
  someone outside the team and confirm it is not.
- `!reconcile` (report mode, no `apply`) → no surprises.

## Team servers (one per team per hunt)

For a multi-team hunt each team plays on its own server, made fresh from a guild
template. Sections 1–6 above do not apply: `!setup_server` does them.

**Once: build the template.** Make a model server with exactly:

- `@everyone`: **View Channels off** (the server is locked down by default).
- A `mod` role: Manage Channels + Manage Messages, **not** Administrator.
- `#lobby`: `@everyone` allowed to view (the only channel newcomers see).
- `#bot-commands`: `mod` allowed to view (only admin commands need it).
- Notifications = mentions only, join messages off, verification low.

Then Server Settings → Server Template → generate the `discord.new/…` link. No hunt
category: the bot creates and gates it. Re-sync the template when the model drifts.

**Per team, per hunt:**

1. On the hub, make the team's role by hand (e.g. `Team Rocket`) and copy its id.
2. Using the shared group account, open the template link to create the server.
3. Invite the bot with Administrator (`permissions=8`).
4. Delete the bot's join greeting from `#lobby` — it describes the hub's manual setup,
   which `!setup_server` does for you.
5. In Server Settings → Roles, drag the bot's role above `mod`.
6. In the new server: `!setup_server <hub id> <hub role id> <hunt url> <hunt name>`.
   You must be the server's owner and an admin on the hub. If it stops, fix what it says
   and re-run the same command; once it reports complete it is locked on that server.
7. Optionally transfer server ownership to a member of that team and have the group
   account leave.
8. Give players the team role (until invite DMs land in a later release). Give `mod` to
   whoever will run admin commands on this server (e.g. `!archive_hunt_to_hub` at hunt
   end) — after setup nobody holds it.

At hunt end, `!archive_hunt_to_hub <hub id>` moves the history to the hub.

## Capacity, when planning a server per hunt

A Discord server holds **50 categories**, and the layout is one category per round or
metapuzzle, so one server tops out around **50 concurrent rounds**. Categories also hold
50 channels each (`max_channels_per_category` in `config.json`, defaulting to Discord's
own limit of 50); the bot auto-splits and overflows past that on its own, so the channel
cap is not the one to plan around — the category cap is.

---

## See also

- [Bot command reference](commands.md) — every command, its arguments and its tier.
- [User guide](user-guide.md) — day-to-day use once the server is up.
- [README → Deploying on a Linux server](../README.md#deploying-on-a-linux-server) —
  standing up the bot instance itself.

# Multi-server hunt architecture — design

**Date:** 2026-07-05
**Status:** Exploratory design (approved as "a good start" — not yet an implementation plan)
**Builds on:** `2026-07-04-hunt-role-gating-design.md` (per-hunt team-role gating, already shipped)

## Problem / motivation

Two goals, solved by one architectural move:

1. **Category capacity.** A Discord guild caps at **50 categories** (also 50 channels/category, 500
   channels/guild — categories count toward the 500). The bot models multiple teams on the same event
   as **multiple hunt rows, each with its own team role and its own cloned category tree** (general
   category + one category per round/meta). So N teams = N× the category footprint. The home server is
   currently at **38 categories**; a 4-team multi-round hunt tips it over 50. Channels are *not* the
   problem (currently ~270/500). **The 50-category cap is the binding constraint.**

2. **Answer integrity.** In a single shared server, whoever is owner/admin can see every team's
   puzzles/answers, undetectably (viewing leaves no audit trail). This has actually happened (owner
   peeked at another team's answers). Cheap capacity fixes do nothing for this; only physical
   separation does.

## Discord facts that shaped the design (so they aren't relitigated later)

- **`create_guild` is blocked once the bot is in ≥10 guilds.** At this scale (2–3 concurrent hunts ×
  4 teams ≈ up to ~12 live team servers) the bot *cannot* create the servers itself. → Servers must be
  **human-created and the bot invited in** (a bot can be added to unlimited guilds).
- **Administrator permission bypasses all channel view-overwrites.** Any human with Admin sees every
  gated channel regardless of gating. → **The bot is the only privileged actor; humans are non-admin.**
- **Bots cannot invite other bots** — each extra bot needs a human OAuth click (Manage Server) per
  server. **Templates don't carry bots, emojis, members, or messages** either.
- **Bots cannot force-add humans** via a plain invite; the human clicks. *Exception:* OAuth
  `guilds.join` scope + stored user token lets the app add a consented user programmatically.
- **Invites only act on join.** An existing server member clicking an invite gets nothing (no join
  event fires). → Existing members need an **in-server self-assign** mechanism to pick up a role.
- **Bot→human ownership transfer is unreliable** (not a documented guarantee; normally needs the
  owner's 2FA, which a bot can't provide). Don't architect around it.
- **Login/viewing cannot be detected** by a bot (audit logs record only *mutating* actions). So "alert
  when someone peeks" is not buildable — the fix for peeking is *removing visibility*, not detecting it.
- **Usernames/avatars travel with the user account.** Only per-server nickname + roles are per-guild,
  so joining a fresh server costs a user nothing but the join + getting their role.

## Chosen architecture

**A permanent hub server + a small pool of reusable "hunt" servers.**

- **Hub server (permanent):** holds per-team landing/help channels (with the self-assign entry point
  and, where needed, an invite link), receives the **archived record** at hunt end, and hosts
  cross-team coordination / the web dashboard's cross-team view. It **stops accumulating per-team
  category trees**, so the 50-category pressure at the hub disappears.
- **Pool of reusable hunt servers:** a couple of **generic vessel servers**, **human-owned** (the
  shared/joint account), with the **bot invited as admin**. Renamed per hunt as needed. Each team's
  hunt instance runs in its own pool server, so each gets a **fresh 50-category / 500-channel budget**.
  Reused hunt-to-hunt (the existing `!archive_hunt` already tears a server's hunt structure back down,
  which resets a vessel for reuse). Generic vessels — *not* fixed per-team-identity servers, because
  teams differ per hunt.
- **Tiering:** single-team / low-stakes hunts stay on the hub as today (role-gated; organiser sight is
  acceptable there). Per-team servers are used **only for multi-team hunts** — exactly when both the
  category cap and the peeking risk appear. This confines the extra setup to hunts that need it.

## Permissions & visibility model

- **Bot:** sole privileged actor — holds manage-channels/roles/webhooks and does all structural work.
- **Players:** hold the current hunt's **team role** → see that team's gated channels.
- **Mods / organisers:** hold a **curated non-admin mod role**. They do **not** see puzzles by default
  (no team role + no Admin bypass). To help, a mod **opts into a team's role on request** — a user
  `@mods` in a help channel, a mod self-assigns the team role. That single action reveals that team's
  channels **and** grants dashboard access (dashboard visibility is already role-gated:
  `hunt_visible` checks role membership, `bot/web/auth.py:213`). They can drop the role when done.
- **Residual (accepted):** the pool-server *owner* (joint account) still bypasses gating, but only for
  the single team living in that server. Cross-team sight only exists if someone logs into the joint
  account and visits each server — de-prioritised by the user.

## Onboarding / role assignment

Two distinct steps; the reusable pool makes step 1 rare:

1. **Join a pool server** you're not yet in → invite link (one-time; skipped for regulars already in
   the vessel).
2. **Get *this* hunt's role** → an **in-server self-assign** (a button / reaction-role message, or a
   `!join <hunt>` command) in a landing/"current hunts" channel. This is the recurring per-hunt action
   (teams/roles differ each hunt) and collapses to one click inside a server they're already in.

One mechanism (the self-assign button) serves both players grabbing their team role and mods opting in.

*Optional friction-killer:* add OAuth `guilds.join` scope to the existing dashboard login and store
tokens, so the bot can auto-add consented players straight into their team server (removes even the
join-click). More machinery + token storage — deferred unless wanted.

## Archiving

**One substantive change: cross-guild archive.** Today `!archive_hunt` copies channel history into
threads on the *same* guild's surviving `general` channel, then deletes the hunt's categories. In this
design the record must land in the **hub** (the team's landing/archive channel) so it survives the
vessel being reused/wiped. Feasible: webhooks can post into any channel the bot can reach across
guilds. Everything else reuses the existing `!archive_hunt` teardown (which also does the pool reset).

## Server setup automation

- **Server template** (created-from once by a human) provides the static baseline: channels, base
  roles, permission structure, server settings. *(Templates do not include bots, emojis, members, or
  messages.)*
- **Bot** does the dynamic per-hunt build: hunt categories, team role + gating, mod role, help channel,
  webhooks, invite links, optionally re-uploading a standard emoji set, welcome message.
- **Human, one-time per pool server:** authorise any additional bots via OAuth (persists thanks to
  reuse — a key reason the pool beats dedicated-per-hunt). Current expectation: few/no extra bots.

## Implementation surface

The bot is **already multi-guild** at the data/auth/routing layers (rows keyed by `guilds.id`, every
command operates on `ctx.guild`, web layer is per-guild). No hardcoded single-guild constant. Main new
work:

1. **Cross-guild archive target** — write `!archive_hunt` threads to a hub channel in another guild.
2. **Self-assign role button/command** — in-server, auditable, grants team role (+ dashboard access).
3. *(Optional)* invite-link posting into the hub landing channel (bot doesn't create invites today).
4. *(Optional)* OAuth `guilds.join` auto-add to remove the join-click.
5. Confirm Google Drive/Sheets creation isn't coupled to a specific guild (expected fine).

## Open questions / decisions still to make

- **Pool size** — sized to peak concurrent team-count (roughly max teams in the largest single hunt,
  plus headroom for overlapping multi-team hunts).
- **Stale-member hygiene** — whether/when to prune previous-hunt members from a reused vessel (they see
  nothing without a current role, so it's cosmetic, not a leak).
- **Tiering threshold** — exact rule for "multi-team enough to split off the hub."
- **`guilds.join`** — adopt it to kill the join-click, or keep the simple invite/button flow.

## Rejected approaches (and why)

- **Bot-created ephemeral servers (`create_guild` + delete to stay under 10):** the <10-guild ceiling
  can't support ~12 concurrent team servers; plus bot-account-ban/abuse risk and unreliable
  ownership transfer. Human-created + bot-invited scales without limit.
- **One shared server per hunt (all teams role-gated in it):** solves capacity but **not** integrity —
  an owner/admin still sees every team. Fails the "both" requirement.
- **Persistent per-team-identity servers:** teams differ per hunt, so fixed-team servers make no sense.
  Pool = generic renameable vessels instead.
- **Blanket organiser Admin sight ("sudo for admins"):** rejected — Admin bypasses gating, defeating
  integrity. Replaced with opt-in-per-team role assignment.
- **Invite link that grants a role to existing members:** impossible — invites act only on join.
- **Transfer ownership to the joint account + login alert:** transfer unreliable; login/view detection
  impossible. Not pursued.

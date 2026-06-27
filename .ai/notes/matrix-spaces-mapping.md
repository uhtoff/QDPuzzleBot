# Design note: mapping the puzzle hierarchy onto Matrix Spaces

Status: exploration only — no code, no commitment. Captures a conversation on
moving off (or partially off) Discord. Dated 2026-06-26.

## Why this came up

Two pressures push against Discord:

1. **Hard structural caps.** Discord = 500 channels/server (categories count toward
   it), 50 channels/category, and categories are a single flat level (no
   category-in-category). A channel-per-puzzle bot with multi-round hunts approaches
   that ceiling and cannot express nesting.
2. **Our model already wants more than Discord allows.** The schema carries dormant
   `tag_group.parent_group_id` (group-in-group) and `puzzle.parent_puzzle_id`
   (mini-puzzles), plus puzzles that belong to *multiple* groups via typed edges.
   Discord forces all of that flat, so `build_board()` fakes multi-membership at
   render time.

Matrix Spaces remove the caps **and** match the shape the schema was built for.

## The options space (context)

- **DIY bridge** — bot reposts Discord messages into a homegrown web chat. Least infra,
  but we own every hard problem forever (history, sync, mobile, notifications).
- **Matrix + mautrix-discord bridge** — run a homeserver (Synapse) + Postgres + the
  maintained Discord bridge. More infra up front; a real protocol + maintained bridge;
  credible path to actually leaving Discord later.
  - **Flavor A:** Matrix is the *backend*; the dashboard SPA stays the face (talks to
    Synapse via the client-server API / matrix-js-sdk). Users never see "Matrix." This
    is the natural halfway house.
  - **Flavor B:** Matrix is the *user-facing* chat too (Element web/mobile). The actual
    exit door from Discord.

Rate-limit risk is low **if** built on the official bot API + webhooks (never
user-account self-botting, which gets banned). Steady hunt chatter sits well inside
limits; the one watch-out is **bulk history backfill** when a room first bridges
(per-channel webhook buckets) — mautrix-discord throttles it; expect a slow initial
backfill, not instant. NB: our prior `rate-limiting channel creation ~1440 min` scare
was a self-inflicted discord.py setting (`max_ratelimit_timeout=60`), not a real
Discord quota — see memory `bot-channel-rate-limits`.

## Matrix primitives (the mechanics)

- A **Space is just a room** with `type: m.space`, carrying `m.space.child` state events
  that point at member room IDs. Children point back with `m.space.parent`.
- Each child link can carry an **`order`** string (lexicographic sort) and a
  **`suggested`** flag.
- **A room can have many parents** — native multi-membership.
- No per-server room cap (bounded by Postgres/disk, not a product limit). Spaces nest
  arbitrarily.

## Entity mapping

| Entity | Discord today | Matrix |
|---|---|---|
| Guild | server | top-level Space (or homeserver root) |
| Hunt (`category_id` + general `channel_id`) | 1 category + 1 channel | a Space; "general" becomes a room inside it |
| Tag_group (`category_id`, `type`, `parent_group_id`) | 1 flat category | a Space per group |
| Puzzle (`channel_id`, `voice_channel_id`) | text + voice channel | a room (Matrix rooms have native voice — voice channel can collapse in) |
| Mini-puzzle (`parent_puzzle_id`) | sibling channel in same category | child room under the parent (parent becomes a small Space) |
| Tags / edges (`feeds`/`informs`/`none`) | implicit via which category holds the channel | an `m.space.child` link — **typed semantics stay app-side** (see caveats) |

## What this unlocks (latent model becomes real)

1. **`parent_group_id` finally works.** Dormant today because Discord categories can't
   nest. In Matrix it's a real parent→child Space link, so
   `Hunt → Round → Sub-round → Puzzle` is native and recursive.
2. **Multi-group membership stops being a board illusion.** Today the channel physically
   lives in exactly one category and `build_board()` projects it into multiple columns.
   In Matrix the room is genuinely a child of multiple Spaces — a feeder that `feeds`
   Meta-A and `informs` Meta-B is a real child of both. Storage matches the edge model
   instead of reconstructing it per render.
3. **Mini-puzzles nest cleanly** via `parent_puzzle_id` → parent puzzle becomes a tiny
   Space holding its own chat room + the mini rooms.

## What's lossy / stays app-side

- **Typed edges don't survive into the Space graph.** `m.space.child` is plain
  containment — no native "feeds vs informs." Keep `edge_type` in our DB (needed for
  rollups anyway) and *use* it to decide which Spaces a room links into and in what
  order. Can stash `edge_type` as a custom key in the child event content, but Element
  ignores unknown keys. Spaces give containment + multi-parent; edge semantics remain a
  puzzbot-layer concern.
- **Meta/target puzzle** (`target_puzzle_id`) is just another room in the group's Space —
  flag `suggested: true` and/or low `order` to pin it to the top (mirrors board ranking).
- **Solved / ordering** (`status`, SOLVE_DIVIDER, board "solved sinks to bottom") maps to
  the `order` field per child link, or a dedicated "Solved" sub-Space per group. The
  `build_board()` rank logic becomes "compute the `order` strings."
- **Archived hunts** → detach the Space or re-parent under an "Archive" Space.

## Concrete shape

```
Space: Hunt 2026
├─ #general                      (room — was hunts.channel_id)
├─ Space: Round 1                (tag_group, type=Round)
│   ├─ #round-1-meta  ★suggested (the target_puzzle_id room)
│   ├─ #puzzle-a                 (feeds Round 1)
│   ├─ #puzzle-b                 (feeds Round 1)
│   └─ Space: Sub-round 1a       (parent_group_id = Round 1)  ← impossible in Discord
│       ├─ #sub-meta  ★
│       └─ #puzzle-c
├─ Space: Capstone               (tag_group, type=Capstone)
│   └─ #puzzle-b   ⟵ same room, second parent  ← native multi-membership
└─ Space: Solved                 (or via order strings)
```

## Source-of-truth during transition

Existing creation logic already knows the full tree:
- `bot/core/puzzle_service.py` ~1680–1740 (hunt create: category + general channel)
- `bot/core/puzzle_service.py` ~1441–1522 (tag_group create: clones category)
- `bot/core/discord_channels.py` ~49–78 (`get_or_create_channel`)
- `bot/web/data.py` ~364–469 (`build_board`, the column/ordering logic)

In a bridged world that same code emits **space-child relationships** alongside (or
instead of) category placement. Discord stays authoritative while bridged; the bot
writes the Space graph from data it already holds. Close to mechanical because the model
is *more* expressive than Discord, not less — we're un-flattening, not redesigning.

## Access control / view restriction

**No fresh homeserver per hunt.** In Matrix "server" = homeserver; run **one** for
everything. Isolation is per-room and per-Space, not per-server. A new hunt gets a new
**Space**, not a new server. (Federating a separate homeserver per hunt is possible but
massive overkill and breaks the single-bridge story.)

### Current gate (the thing we'd be mapping)

The team unit today is a **Discord role**. `bot/web/auth.py:167` `hunt_visible()` gates
in three tiers:
- **Active** hunt → user must hold the hunt's `role_id` (the team role).
- **Legacy** hunt (`role_id 0`) → any guild member.
- **Archived** hunt → guild membership only.

The dashboard reads the user's Discord roles via OAuth and checks them. So the source of
truth for "who's on the team" lives in **Discord identity + Discord roles**. That fact
drives everything below.

### Matrix's native primitives (two independent axes)

- **Join rules** (can you see/enter), per room: `public`, `invite`, `knock`,
  **`restricted`**, `knock_restricted`. `restricted` = "anyone who is a member of Space X
  may join" — and its `allow` list is an **array**, so multiple Spaces can grant access.
  This is Matrix's answer to Discord role-gated category permissions.
- **Power levels** (what you can do once in) — per-room numeric privilege ladder = the
  role/admin axis. Separate from visibility.

### Mapping the three tiers

| `hunt_visible` gate | Matrix |
|---|---|
| Active → must hold `role_id` | hunt Space membership = the team; rooms `join_rule: restricted` allowing that hunt/team Space |
| Legacy (`role_id 0`) → any guild member | rooms `restricted` to the top-level **org Space**, or `public` within the org |
| Archived → guild membership only | `restricted` to the org Space |
| Hunt visible to **multiple** teams | list several Spaces in the `allow` array — cleaner than Discord's stacked role overwrites |

Team boundary becomes "who is a member of the hunt's Space." Add the right people to the
Space; its rooms follow.

### The real catch: identity & sync

Because the team unit is a *Discord role*, the hard part isn't expressing access in
Matrix — it's who decides membership:

- **Flavor A (Matrix = backend, dashboard is the face):** existing `hunt_visible()`
  stays the gate. SPA talks to Synapse via a service account; no per-user Matrix accounts,
  no reliance on Matrix join rules. Authz model untouched; reuses the `auth.py` work.
  **Lowest friction by far.**
- **Flavor B (users in Element directly):** every hunter needs a Matrix identity, and
  Matrix's own join-rules/power-levels enforce access — which requires a process
  continuously **syncing Discord roles → Matrix Space membership** (gain/lose the role →
  add/remove from the hunt Space). mautrix-discord puppets *users* across but does **not**
  translate Discord role-gated category perms into Matrix join rules. That sync loop is
  the real cost of going user-facing.

While bridged, **two** permission systems (Discord category overwrites + Matrix join
rules) must agree — both driven from the one source of truth (the hunt's team role).
Nothing keeps them in lockstep automatically; the bot would.

## Ops cost (honest)

Synapse + Postgres + mautrix-discord = three long-lived processes to host, back up,
update. Synapse is the heaviest but runs fine on a modest VM for a community of this
size. Message history + media grow over time → want a retention policy. Bridge fidelity
isn't 100% (reactions/threads/replies mostly fine, occasional rough edges).

## Hosting & sizing

**You host Synapse.** The bridge (mautrix-discord) is a Matrix *application service* that
must be registered in the homeserver config → you need admin control of the homeserver.
That rules out pointing at a public homeserver (matrix.org won't let you register
arbitrary appservices). Realistic routes:
- **Self-host** — most control, cheapest cash, highest ops burden.
- **Managed** (Element Matrix Services / etke.cc) — they run Synapse + bridges; pay
  monthly; trade cash for ops time. Both support custom bridges.

**Run it closed / non-federated.** For a closed hunt community, disable federation: no
`.well-known` delegation, no `8448`, only `443 → 8008`. Smaller attack surface and keeps
Synapse's RAM/CPU modest (federation is what makes Synapse balloon). Do this unless we
specifically want to interoperate with outside Matrix users.

### What actually runs

- **Synapse** (homeserver) — the memory hog.
- **Postgres** — Synapse's DB (separate engine; SQLite is testing-only). Note: distinct
  from the bot's MySQL/MariaDB.
- **mautrix-discord** — the bridge process + its registration.
- **coturn** — only if voice/video must traverse NAT; skip for chat-only.

Cleanest packaging: run Synapse + Postgres + bridge as a **Docker Compose stack** and
reverse-proxy nginx into the container's `8008`. Keeps the Matrix side isolated from any
co-resident app's system packages / DB engine.

### Sizing (closed, non-federated, small community)

Rough resident memory:

| Process | ~RAM |
|---|---|
| Synapse | 0.8–1.5 GB |
| Postgres (Synapse) | ~0.3 GB |
| mautrix-discord | ~0.15 GB |
| puzzbot (bot + dashboard) | ~0.3 GB |
| bot DB (MariaDB, if co-located) | ~0.4 GB |
| OS + nginx | ~0.3 GB |

- **Co-hosted with the Laravel Forge site (shared box):** budget ~1.5–2 GB *on top of*
  current usage. A 4 GB box is tight (lean on swap); 8 GB comfortable. 2 GB: give Matrix
  its own VPS instead.
- **Dedicated box (Synapse + bridge + bot, no Laravel):** total ~2.4–2.8 GB → a **CX22
  (2 vCPU / 4 GB / 40 GB) is okay** for a small closed community with ~1–1.5 GB headroom.

Hetzner plan reference (current Intel line): **CX22** = 2 vCPU / 4 GB / 40 GB; **CX32** =
4 vCPU / 8 GB / 80 GB. ("CX23" is not a real plan name — confirm the spec in the console.)

### Two things to watch (neither is RAM)

1. **Disk** is the real constraint, not memory. Synapse media/history + Postgres + bot DB
   accrete on one volume (40 GB on a CX22). Set a **Synapse retention policy from day
   one** and monitor.
2. **One-time Discord backfill.** First bridge pulls history → a single CPU/IO burst on
   the 2 vCPUs. Throttled and one-off; steady chatter afterward barely registers.

**Size up to CX32 only** for very large *simultaneous* hunts (hundreds of concurrent
posters) — Synapse scales with concurrent active users. Typical puzzle-team sizes (tens
per hunt) are fine on a CX22.

### Laravel Forge co-location specifics (if sharing that box)

- Install **Postgres beside MySQL** (ports 5432 vs 3306; Forge won't manage it — fine).
- Expose Matrix as its **own Forge "site"** for a `matrix.<domain>` subdomain (Forge
  handles its TLS), with a reverse-proxy block → `8008`. Keeping it a separate Forge site
  avoids Forge's automated nginx rewrites clobbering a hand-edited vhost.
- Dockerizing the Matrix stack keeps it clear of Forge's PHP/MySQL provisioning.

## See the UI

- https://app.element.io — real hosted client; Spaces = left icon rail, nested tree beside it.
- https://matrix.org/blog/2021/05/17/the-matrix-space-beta/ — annotated nested-Spaces screenshots.
- https://element.io — marketing hero shots.

## Possible next step (not taken)

A spike-sized writeup: docker-compose for Synapse + Postgres + mautrix-discord, plus the
exact hook points in the creation logic above. Deferred until there's appetite to
prototype.

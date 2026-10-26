# QDPuzzleBot — Plans & Roadmap

> Living planning doc. Produced 2026-06-21/22 from a two-pass multi-agent review of the
> codebase (subsystem map → meta-meta design → minimeta-cluster + capstone design), with
> every claim adversarially re-checked against the actual code. File:line refs are accurate
> as of branch `feature/dashboard-spa` and may drift as code changes — re-verify before acting.

---

## 0. Orientation (what this bot is, in model terms)

- Discord puzzle-hunt manager: Python / discord.py + MySQL + Google Sheets, plus a new
  React/Mantine SPA over an embedded Quart API (`bot/web/`, `frontend/`).
- **Core data model today:** `hunts → rounds → puzzles`, where the *feeder→meta* link is the
  many-to-many `tags(puzzle_id, round_id)` join table. A "meta" is **not** a puzzle attribute —
  it is a **round** (`rounds.meta_id` points at the puzzle that *is* that round's meta;
  `rounds.meta_code` is a 6-char handle). A puzzle is a meta when `puzzles.metapuzzle == 1`.
- **Key limitation:** hierarchy is hardcoded to **exactly two levels** (feeder → meta). There is
  **no parent edge anywhere** (no `parent_round_id`, no puzzle→puzzle link table). The
  `puzzles.metameta` column exists but is **read in 4 places and never written by any code** —
  true meta-meta only exists via manual DB edit. The meta-meta sheet renderer still hardcodes
  two round codes from a past hunt (`RETAWR`/`LAOFNO`, `bot/cogs/puzzles_gsheet.py:589`).

---

## PART 1 — Structural features (the four asks) → one engine

The maintainer wants four things. They are **not four features** — they are four shapes of one
engine: a typed relationship graph + one generic roll-up writer.

| Ask | Same engine, expressed as |
|---|---|
| **A. Flexible meta-meta + pull many titles/answers into one sheet** | a `feeds-meta` edge to a higher meta + a roll-up over the subtree |
| **B. Minipuzzle / minimeta cluster with linked sheets** | minipuzzles as `additional_sheets` tabs under one puzzle + a roll-up scoped to that cluster |
| **C. Capstone / progressive reveal** | a **different** edge type — `reveals` — feeding a "dossier" tab; solved only by manual `!solve` |
| **D. Flexible answer aggregation (selection / round / subtree)** | the same writer with a `source_mode` of `round` / `subtree` / `explicit` |

### ✅ RESOLVED — "round" vs "collections of tags" → typed groups on the existing tables (M6)

The multi-agent design proposed keeping the `round` concept and bolting on `rounds.parent_round_id`
(round→round containment) + a `puzzle_edges` table (typed puzzle/round edges).

**The maintainer's alternative (preferred direction, under investigation):** drop "round" from the
internal model entirely and treat **everything as collections of tags (groups)**. A group is just a
named tag with a **type/status** (round, meta, metameta, cluster, capstone, pool, ad-hoc-selection).
Membership is the existing many-to-many `tags` table — which *already* makes "round" a tag in
disguise. This subsumes the agent proposal:

- `parent_round_id` → "a group can be a **member of another group**" (group-in-group membership).
- `puzzle_edges` typed edges → membership rows carry an **`edge_type`** (`feeds-meta` / `part-of` /
  `reveals` / `aggregates-into`).
- `round_kind` / `metapuzzle` / `metameta` flags → a single **`kind`/`status`** on the group.

Why this is likely better: it is one uniform abstraction instead of three (round + parent edge +
typed-edge table); any puzzle can be in any number of groups; groups nest to arbitrary depth; and
behavior is driven by the group's *type*, not a rigid "round". All four features above become "a
group of a given kind, aggregated by `aggregate_to_tab`".

Caveats to resolve (this is what the feasibility pass checks):
1. A round is also a **physical Discord category** (a real channel-cost grouping). "Group" needs two
   facets: the logical tag-group **and** an *optional* Discord category. Not every group gets a
   category (clusters / pools / ad-hoc selections don't) — which is consistent with the channel-quota
   rule below.
2. Footprint of the rename: `round` / `hunt_round` is wired through commands (`!r`/`!round`/`!rnm`),
   `build_board`, `types.ts`, `meta_id`, the `tags` table, etc. Decide **rename-in-code vs
   keep-table-name-`rounds`-treat-as-groups** (additive, gradual) — likely the latter to de-risk.
3. `meta_code` is typed `int` but holds a 6-char `str` (`round_data.py:20`) — latent bug either way.

**→ RESOLVED (verified): keep the table named `rounds`, treat rows as typed groups; rename only at
the UX edges.** *(Superseded by migration `011_taggroup_rename.sql`: `rounds`→`tag_group`,
`meta_id`→`target_puzzle_id`, `round_id`→`tag_group_id` were renamed in-DB after all — see
AGENTS.md's domain vocabulary section for the current names.)* `round_id` is the load-bearing
membership key in raw SQL across
`mysqldb.py:204/279-288/317-360` — a full rename is ~360 edits across 19 files for *zero* feature
payoff. Instead add columns **additively**: `rounds.kind` (backfilled from `rounds.type` + the puzzle
flags), `rounds.status` (gives the solved-section a DB home it lacks today — currently only Discord
channel *position*), `rounds.parent_group_id` (nullable; the group-in-group / parent edge),
`rounds.category_id` made nullable (enables channel-less groups), and `tags.edge_type` (default
`feeds-meta`). This one additive shape **is simultaneously** the conservative
`parent_round_id`+`puzzle_edges` proposal **and** the bold tag-group model — bold reasoning (one
graph), conservative risk (no rename, no parallel structure), door left open to full tag-group
terminology later. (Fix the `meta_code` int-holds-str bug, `round_data.py:20`, while there.) → **M6.**

**Clarified intent (the real point):** the reason to relabel is *cognitive* — to **stop us reasoning
in "rounds"**, the framing that's been limiting the design — not to tidy the schema. The word in the
code you touch daily is what shapes the mental model, so the rename that matters is the **domain
vocabulary you code against, not the physical table name.** Plan: the new `bot/core/` layer (**M3**,
greenfield → cheap) introduces a `TagGroup` domain object (`kind` field) and speaks group / tag /
membership; the new commands (clusters, capstones, aggregation) and the dashboard use that language.
The `rounds` table + `round_id` SQL survive purely as a **storage detail behind the repository**
(invisible to day-to-day reasoning), and `RoundData` / `!round` / `hunt_round` get retired
**opportunistically** as the strangler-fig (M3) replaces each old path — so the "round" vocabulary
leaves the code you work in without a big-bang rename. The ~360-edit storage-identifier rename is the
part that *doesn't* shape thinking, so skipping it costs nothing cognitively; renaming the physical
table later becomes a pure mechanical migration, decoupled from the conceptual win.

**Name: `TagGroup`** — a named, *typed* group of **puzzles**, identified by a tag (NOT "a group of
tags"); membership is a row in the existing `tags` table carrying an `edge_type`, so a puzzle is
*tagged into* a TagGroup. Entity `TagGroup` (was `rounds`) + `tags` membership edges
(`tags.round_id` → conceptually `tags.group_id`). Reads naturally for every `kind` (meta / metameta /
cluster / capstone / pool / ad-hoc). "Group" is fine informally; the type is `TagGroup`. (`Group`
alone collides with Discord roles/categories; `TagGroup` + `tags`-as-membership is the cleaner split.)

### ✅ RESOLVED — model-first in *direction*, not scope (Discord stays the bang-command + auth surface)

Observation: the design has been **held back by what works on Discord**. Today **Discord *is* the
model** — a puzzle = a text channel, a round = a category, a meta = a channel; identity and some
state live in channel/category IDs and an in-memory `environment[ctx.message.id]` dict; business
logic is welded to a discord.py `ctx` (precisely why the dashboard needed 11 hand-synced `web_*`
twins). So the channel-create quota, rename buckets, the two-level limit, and "one channel per
puzzle" aren't only constraints — they have been *shaping the schema itself*.

Proposed direction (the tag-group reframe, one level up): **make the bot's own model (DB tag-graph +
Sheets + dashboard) the source of truth, and treat Discord as ONE projection of it.** Then:
- Rich structure (deep nesting, clusters, capstones, arbitrary tag groups) lives in the DB,
  unconstrained by Discord limits; **Discord surfaces are created selectively/lazily** only where a
  human discussion space is actually wanted (the channel-quota tiering becomes a natural consequence).
- The **dashboard becomes a first-class (potentially primary) surface**, not a read-mostly mirror.
- Sheets, Discord, and the SPA are interchangeable **adapters over one service layer** — the *same*
  refactor as "kill the `web_*` duplication" (Part 2), so these align rather than compete.

This **subsumes the tag-group decision**: "round" stops being a Discord category and becomes a logical
tag-group that *may* have a Discord projection. It also resolves capability D (ad-hoc selection = a
group) and the meta-meta round-vs-puzzle ambiguity (it's all membership in one graph).

**→ RESOLVED (verified): go model-first in *direction* but not *scope*.** The cheap half is already
free — `bot/web/data.py` imports no `discord` and `build_board` rebuilds the whole board (incl.
Discord URLs) from SQL alone, and the dashboard already addresses entities by **primary key**
(`actions.py:254`), so "channel-as-addressing" is only the *bang-command* scheme, not a real blocker.
**Do now:** extract a ctx-free service layer (`bot/core/`) and delete the 11 diverged `web_*` twins
one at a time (the win both stances share), thread the per-puzzle mutex through it, and let the
service own `spreadsheet_id` selection. **Defer** (buys little for a hunt run a few times a year by a
few admins): promoting the dashboard to *primary*, de-ctx-ing the `environment[ctx.message.id]` spine
+ the fake-ctx hack (`puzzles.py:653-681`), a DB↔guild reconciler, and a DB role mirror. Keep Discord
driving bang-command UX and **live** access control. → **M3 / M4.**

### ✅ Reversibility — stop early, and the Discord bits are config, not commitment

The guild may not want the full thread / decoupling story, and today's model works (imperfectly). The
roadmap is deliberately structured so that's fine — the escape hatch is the *default*:

- **The early milestones are pure bug-fixes/hardening to the model you already have**, no new concepts
  forced on anyone: M0 (schema+migrations), M1 (tests), **M2 (the meta-sheet ✅ bug fix + bulk
  writes)**, M4 (mutex), M5 (loud failures), M10 (destructive-command safety). **Stopping after
  M5/M10 leaves a strictly better version of today's bot** — same mental model, fewer bugs. Tag a
  release there.
- **The divisive parts are last and opt-in.** Feeder-as-thread (M11) lives behind a per-hunt/round
  **thread-vs-channel policy hook defaulting to *channel* (current behaviour)** — threads are a
  runtime toggle, never a fork, and the data model is identical either way (a feeder's `channel_id`
  holds a channel *or* a thread snowflake — same column). New `kind`s (cluster/capstone) are opt-in by
  simply not creating them; the existing feeder→meta flow is untouched.
- **This is exactly the "Discord is just the interface expression" intuition.** After the service
  extraction (M3), Discord is an *adapter*, so "go back to channels" or "use threads only for clusters"
  is a config/adapter tweak, not a re-architecture. M3 itself changes **no behaviour and no data** — it
  only deletes the `web_*` duplication — and M1's equivalence tests prove current behaviour is
  preserved through it. **M3 is the thing that *grants* the tweak-it-back property.**
- **Additive, backward-compatible schema:** the M6 columns are nullable with `kind` defaulting to
  current behaviour, so old code paths keep working and unused `kind`s sit harmless. You never
  maintain two versions — the new capability is dormant until invoked, so "revert a feature" = "stop
  using that `kind`", not a rollback.

### The channel-quota rule (drives the cluster + capstone designs)

Channel **creation** hits a real account/guild quota (measured ~81628s/82317s ≈ ~24h) that surfaces
as a hard error via `max_ratelimit_timeout=60` (see `memory/bot-channel-rate-limits`). Therefore
**fine-grained sub-puzzles must never get their own Discord channel.** Cheapest sufficient tier wins:

| Tier | Use for | Cost |
|---|---|---|
| **Discord channel** | rounds/zones (category), top-level puzzles, metas, capstones | rate-limited ⚠️ |
| **Google sheet tab** | every minipuzzle, every aggregate/dossier tab, ad-hoc aggregation targets | zero Discord (`create_additional_spreadsheet`) |
| **Tracker row** | pure relationships / placeholders / pool nodes | none |
| **Discord thread** | **feeder puzzles** under a round channel, per-reveal clue posts, per-mini chatter | not the create bucket (~10–55/min) |

Net: an **N-minipuzzle cluster = 1 channel + (N+1) tabs = one channel-create**; a reveal-zone
capstone = 1 channel + 1 dossier tab + N thread posts = one channel-create; and a **round of N
feeders-as-threads = 1 channel + N thread-creates = one channel-create** (vs N+1 today). Feeder-as-
thread is the biggest single win against the quota — see the capabilities section below.

**The `additional_sheets` primitive you already have IS the "linked sheets" substrate** — it even
has dead `solution`/`solved` columns (`bot/store/puzzle_data.py:21-22`) ready to activate per
minipuzzle.

### 🆕 New platform capabilities the original code never had (SPIKED — verified vs pinned deps)

**Bottom line: nothing is dependency-gated.** discord.py is pinned **2.7.1** (`Pipfile.lock`) — full
thread / forum / `discord.ui` API present on disk. google-api-python-client is pinned **2.197.0** —
its bundled static `sheets.v4.json` discovery doc already wires `addTable`/`updateTable` **and**
`createDeveloperMetadata` into `batchUpdate`, and `build()` uses static discovery (`gsheets.py:37`),
so it's offline-safe. **Zero dep bumps required; adoption cost is design/code effort only.** The bot
uses almost none of this today (0 `discord.ui` imports; threads in exactly one place at
`puzzles.py:2467`; Sheets writes only via ~750 per-cell `update_cell`).

**① Discord threads — the single biggest lever (adopt in structural work).** Threads are NOT in the
~24h channel-create bucket (real-world ~10–55/min). **Feeder-as-thread turns a round of N feeders
from N+1 channel-creates into 1 channel-create + N thread-creates** — the biggest possible relief of
the quota that's been shaping the schema, and it decouples puzzle==channel (a feeder's `channel_id`
just holds a thread snowflake — no schema change). Confirmed reuse:
- `safe_rename`/`_deferred_rename` (`puzzles.py:114-157`) work **unchanged** for `Thread.edit(name=)`
  (threads share the same ~2-renames/10-min bucket — don't build separate rename infra).
- Archive feeders on solve via `thread.edit(archived=True)` (cheaper than message-replay archive; also
  respects the **per-guild 1000 active-thread cap** — archived threads are unlimited, history kept).
- **Hard prerequisite (adopt now):** make `cog_before_invoke` (`puzzles.py:159-210`) and
  `activity.py:31` thread-aware. A feeder-thread's id in `puzzles.channel_id` already resolves via
  `get_channel_type`'s UNION (`mysqldb.py:408-425`); the gaps are non-feeder chatter/reveal threads
  (use `message.channel.parent_id`) and activity attribution.
- New: a `create_puzzle_thread` sibling to `create_puzzle_channel` (`puzzles.py:1128`);
  `get_or_create_channel` (`:1667`) raises on non-text/voice (`:1675`) so it needs a thread branch +
  a per-node thread-vs-channel policy hook (the channel-quota tiering decision point).

**② Bulk `values()` writes — the real Sheets win (adopt now), not Tables.** The load-bearing
aggregation primitive is `spreadsheets.values().update` + `values().clear`, reachable today via the
existing `batch_update` path. Collapses the ~750 per-cell `update_cell` calls
(`add_metapuzzle_data:548`, `add_metametapuzzle_data:574`) into 1–2 requests — the actual enabler for
"dozens of answers in one sheet". (The ✅ row-shift fix and removing the `RETAWR`/`LAOFNO` skip are
orthogonal — still required in the row-building step; Tables don't fix them.)

**③ Native Sheets Tables — optional polish, roll-up tabs only (adopt in structural work).** Use as
the *output container* for `aggregate_to_tab` roll-up/dossier tabs: `addTable` once (keyed by stored
`tableId`), `updateTable` to resize — retiring the fixed-50-row blanking and shrinking the rollups
record from `last_rows/last_cols` to a `table_id`. **Keep per-puzzle tabs and the B1/I1/B3 +
`=HYPERLINK` template table-free.** ⚠️ Tables' interaction with `copyTo`/`duplicateSheet` (archival,
`move_puzzle_spreadsheet:388`) is **undocumented** — probe before relying on `tableId` survival.

**④ `createDeveloperMetadata` — fixes the stale-pointer risk (adopt with rollups, Phase 2).** Attach
searchable metadata to aggregate/cluster tabs so the rollups record keys on a stable id that survives
the sheet-ID reassignment in `archive_puzzle_spreadsheet` (`:452-481`) — a cleaner fix for the
stale-`tab_page_id` risk than re-pointing on move.

**⑤ Buttons + Modals (`discord.ui`) — high value, net-new code (adopt in structural work).** Makes
two roadmap items trivial: **confirm/undo on destructive commands** (`delete_hunt:2629`,
`delete_round:2602`, `_delete_puzzles:2584`) and **in-Discord status/priority pickers** (replace the
free-text validation at `:2063`/`:2085`). Couple to the ctx-free service-layer extraction. Caveat:
adds interaction-token/timeout/persistence handling the bot has never done.

**Deferred / rejected:** Forum-channel-per-round (stronger than threads-as-feeders via `applied_tags`
status/meta tags, but high coupling — pair with the Discord-as-projection refactor, not standalone);
Components V2 panels (the SPA is the rich surface; also mutually exclusive with embeds on a message);
protected ranges / filter views (nice-to-have to lock bot-written nexus rows); application emoji +
scheduled events (out of scope). **Reject** cross-tab/structured Table *formulas* — reaffirms "always
snapshot" (archival → `#REF`).

### The shared engine

- One generic **`aggregate_to_tab(spreadsheet_id, tab_page_id, columns_spec, rows)`** replacing the
  two copy-pasted renderers (`add_metapuzzle_data` `:548`, `add_metametapuzzle_data` `:574` in
  `puzzles_gsheet.py`), writing via the bulk `values().update` + `values().clear` API (1–2 requests
  vs ~750 single-cell ops — the real blocker for "dozens of answers in one sheet"), routed through
  the existing `execute_with_retry` backoff. **Snapshot values, never `=Tab!I1` formulas** (archived
  puzzles move to a separate archive spreadsheet → cross-tab formulas go `#REF`).
- A **`rollups`** record per aggregation target (owner, hunt, `tab_page_id`, `source_mode`,
  `edge_type_filter`, `last_ok`, `updated_at`). Extent is the native Table's own range (store a
  `table_id`, not `last_rows/cols`); key the tab pointer on a `createDeveloperMetadata` id so it
  survives the sheet-ID reassignment on archive.
- **Debounced propagation on solve**, plus a manual `!rebuild`, plus **surfaced failures**
  (`web_refresh_meta` currently swallows sheet errors at `puzzles.py:605`, so the dashboard shows
  success while the sheet is stale).

### `edge_type` semantics + worked example: the runaround

Every membership is a `tags` row (**puzzle → TagGroup**); `edge_type` answers *"when this puzzle is
solved, what happens to the group it's tagged into?"*

| `edge_type` | On solve… | the group's "result" | counts toward meta progress? |
|---|---|---|---|
| `feeds-meta` | answer is an **ingredient** the group's meta consumes; shown on the meta's answer tab | a meta puzzle (old `meta_id`) | yes |
| `part-of` | internal piece of a **cluster**; answer rolls into the cluster's **minimeta** tab | the minimeta | yes (within the cluster) |
| `reveals` | **surfaces info** onto the group's **dossier / evidence board** — never solves anything | a capstone (stands alone) | no — "N of M revealed" |
| `aggregates-into` | answer is **collected** into a roll-up sheet as a pure view | none (pool / ad-hoc) | no |

Edges are always **puzzle → group** — *puzzles never attach to puzzles*. Meta-meta works because a
child group's **result puzzle** is itself tagged into the parent group, and/or the child group nests
via `parent_group_id`.

**Two dials, not one** — this is the bit that's easy to conflate: `parent_group_id` = *what's in the
branch*; the roll-up's `source_mode` = *how far down it reaches*. `source_mode=subtree` pulls the
meta's answer **+ all its feeders'**; `edges` (filtered to `feeds-meta`) pulls **direct members only**
(metas-only). They combine, and aren't exclusive — a meta can both be nested *and* carry a
`feeds-meta` edge.

**Worked example — the runaround / endgame** (the canonical "many answers in one sheet"):

- *Runaround consumes each round's **meta** answer* → tag every round-meta's result puzzle
  `feeds-meta` into the runaround group → one row per round-meta.
- *Runaround consumes **every** answer in the hunt* → nest the rounds under the runaround group
  (`parent_group_id`) and give its roll-up `source_mode=subtree` (or `hunt`) → it harvests every
  solved puzzle automatically; you don't tag each one.
- *Runaround consumes a **curated** pool* → tag just those puzzles `aggregates-into` the runaround
  group.

All three are the same `aggregate_to_tab` engine pointed at different membership — and case 2 *is* the
original "pull a large number of titles+answers into one sheet, fresh as they come in" ask.

**✅ Decision (pins the open `subtree`-vs-`edges` question):** make them mean *different* things, not
two spellings of one. **`feeds-meta` edges = "these specific answers go up"; `parent_group_id` +
`source_mode=subtree` = "this whole branch goes up."** A group's roll-up uses exactly **one**
`source_mode`; never support two ways to express "metas-only." Default a meta-meta group to `edges`
filtered to `feeds-meta`; switch it to `subtree` only when you want the full branch (e.g. an
all-answers runaround). → applies at **M7**.

### Worked example: capstone evidence board (PB10 "Detectives")

A capstone whose reveals are **structured, routable clues** (not free-text narrative) — the case that
decides opaque-payload vs a structured clue store.

Structure: a suspect **roster** (seeded thin, e.g. DOB); some feeders, on solve, yield a fact
`(suspect, attribute, value)` — e.g. `(Suspect X, favourite cookie, "11D")`, where the value is often a
pointer into another puzzle. You don't initially know which **detective puzzle** a fact belongs to, so
facts are held against the suspect and *routed* to a detective once known.

Model — an **evidence/clue store**, one row per fact:

| field | when filled | how |
|---|---|---|
| `source_puzzle` | **always, auto** | the `reveals` edge's source (the puzzle you logged it from) — provenance you can't lose |
| `suspect` | intake | subject of the fact |
| `attribute` / `value` | intake | "favourite cookie" / "11D" |
| `assigned_detective` | later | the routing; nullable until known |

The **suspects sheet** and the **per-detective boards** are two `aggregate_to_tab` roll-ups of the
*same* rows, grouped by `suspect` vs by `assigned_detective` — so you **tag/route, never copy**, and a
fact shows in both views with its source intact.

Fixes the two real pains: provenance is a required auto-stamped column (no more "where did this come
from?"), and completeness becomes three queries instead of a gut feeling — (1) solved clue-bearing
puzzles with **no clue logged**, (2) clues **not yet routed**, (3) empty suspect/detective slots.

**Two independent channels, same puzzles** (the easy orthogonal bit): whether the detective puzzles
feed a meta is the standard model (the round is a TagGroup, `kind=meta` or metaless `!rnm`). The
**answer channel** (`feeds-meta` → meta) and the **evidence channel** (`reveals`/clues → boards)
coexist via different `edge_type`s — one detective puzzle can feed the round meta with its *answer*,
be the routing target for *clues*, and on solve *reveal* a clue, all at once. Three edges, no conflict
— the payoff of typed edges.

**Structure is discovered, not declared.** Real intake is progressive, so the evidence store is just
the **tag-graph again, applied to _evidence_ instead of puzzles** — one mechanism at three stages of
knowledge:
1. **Freeform** — a "this is sus" board: each item is a note + auto `source_puzzle`, no subject/target.
2. **Emergent labels** — apply ad-hoc tags as hunches form (`red` / `blue` / "vigenère key?"); a label
   is a TagGroup of evidence, overlapping allowed (an item can carry several).
3. **Bind a label to a target** — bind `red → Traitor puzzle`; because `red` is a TagGroup, it routes
   every red item at once *and stays live* (tag a new item `red` later → it flows automatically).

Invariant at every stage: `source_puzzle` auto-stamped (provenance), tag/route never copy (re-labeling
must be cheap — early hypotheses are wrong a lot), and the gap views generalize ("unlabeled evidence",
"labels not yet bound", "solved puzzles with no evidence captured").

**✅ Decision (refined):** the **base** is a flexible **evidence tag-graph** — items + ad-hoc labels
(TagGroups) + later-bound targets, `reveals` edge carrying provenance throughout. The PB10 "typed clue
store" (`suspect` / `attribute` / `value` / `assigned_detective` columns) is **one specialization** —
an optional **template laid over the graph when the axes are known up front**, *not* a separate schema.
Known axes (Detectives) → pre-seed labels + a structured column; unknown (start-of-hunt "this is sus")
→ stay freeform and let labels emerge. Free-text narrative reveals (MonArch) are just the freeform end
with an opaque note. → applies at **M9**.

### Suggested build order (structural-feature view)

> **Canonical sequence is Part 2's M0–M11** (it folds these phases into the full plan, with the
> service-extraction + test seam interleaved). This is just the structural-feature slice for context:
> phase 0→**M0**, 1→**M2**, 2→**M5**, 3→**M7**, 4→**M8**, 5→**M9**.

0. **Migration framework + checked-in base schema.** ← do first. There is currently **no
   `CREATE TABLE` for core tables in the repo at all**, and the one migration file
   (`scripts/sql/001_dashboard.sql`) is gitignored. Everything below needs it.
1. **Generic `aggregate_to_tab()`** replacing both hand-rolled renderers. Fixes a **live bug** today:
   the `✅` checkmark causes a `continue`-before-`row+=1` row-shift that silently misaligns
   titles/answers (`puzzles_gsheet.py:557-558`, and the sibling in `add_metametapuzzle_data`), and
   kills the hardcoded `RETAWR`/`LAOFNO` skips.
2. **`rollups` + debounced on-solve propagation + surfaced failures.**
3. **Meta-meta + flexible aggregation (A + D)** — data + thin command/UI on top of 1–2.
4. **Minimeta clusters (B)** — cheapest standalone feature; rides on `additional_sheets`.
5. **Capstones (C)** — richest; needs the edge/group-membership table, so last.

### Verified risks (do not skip)

- **Solving a cluster archives its own sub-tabs.** `archive_puzzle_spreadsheet`
  (`puzzles_gsheet.py:452-481`) moves every `additional_sheet` to the *archive* spreadsheet on solve,
  reassigning sheet IDs → the cluster's roll-up `tab_page_id` pointer goes stale; a later
  `!rebuild_cluster` writes to a missing tab. **Currently in no phase's plan** — needs an explicit fix
  (skip archiving cluster sub-tabs, or re-point the roll-up on move).
- **Capability D is the thinnest-specified** — "explicit selection" needs a membership store; in the
  tag-group model this falls out naturally (an `ad-hoc` group), which is another point for that model.
- **Meta-meta round-vs-puzzle ambiguity** *(RESOLVED — see "edge_type semantics + the runaround"
  above)*: a sub-meta feeding a supermeta is a *puzzle→puzzle* relationship, expressed as the
  sub-meta's result puzzle carrying a `feeds-meta` edge into the parent group. Pinned rule:
  `feeds-meta` edges = "these specific answers up"; `parent_group_id`+`subtree` = "whole branch up";
  one `source_mode` per roll-up.
- **Deploy order is load-bearing:** the generic `commit()` writes every dataclass field, so adding
  new columns in code *before* the migration runs crashes existing `add_sheet`. Migration-before-code.
- **Solve hook location:** `_solve_impl` does **not** call `_update_all_metas`; it inlines its meta
  loop at `puzzles.py:2240-2245`. Any reveal/meta-meta hook goes there.
- **Out of scope by design:** MITMH-2026-style Research-Points unlock economy + milestone rewards are
  orthogonal unlock-gating, not relationship-graph concerns. (PB10 "Detectives" specifics are
  paywalled; the capstone design leans on the well-documented MITMH 2026 MonArch/capstone pattern.)

---

## PART 2 — Sequenced roadmap (M0–M11)

Integrates the structural features (A–D), the architecture verdict, and the broader fixes into one
dependency-ordered sequence. **Critical path:** ~~M0~~ → ~~M1~~ → ~~M2~~ → ~~M3~~ → ~~M4~~ → ~~M5~~ → ~~M6~~ → ~~M7~~ → ~~M8~~ → ~~M9 (evidence board only)~~ → **M9b (next — capstones/reveal)**.
~~M10~~ (✅ done, `9951408`) / ~~M3.5~~ (✅ done) / M11 (deferred — feeder-as-thread) hang off the M3 seam / M6 group-model and land opportunistically.
~~M7.5~~ (✅ aggregated-answers read view + copy-to-clipboard, done `5cc71b3`). Part 3 holds the file:line
detail behind each item. **Open follow-ups (small):** M9 EvidencePanel UI gaps (Note/Target filters, bulk-assign, batch-paste).

**M0 — Foundation: checked-in base schema + migration runner.** ✅ **DONE** (`7453661`) *(deps: none)* Tracked `CREATE TABLE`
DDL for all core tables (none in repo today; the lone `001_dashboard.sql` is gitignored), a
`schema_migrations` runner applying `NNN_*.sql` in order, README DB bring-up. **Rule: migration →
deploy → code**, because `commit()` writes every dataclass field by name (`mysqldb.py:39-49`) and
crashes on a missing column.

**M1 — Test seam before any extraction.** ✅ **DONE** (`917e173`) *(deps: M0)* `pytest.ini`/`conftest.py` + CI; pure tests for
`build_board` and `hunt_visible`; **bang-vs-core equivalence tests** so M3 can delete twins safely.
(Only one 22-line test exists today.)

**M2 — Generic `aggregate_to_tab()`.** ✅ **DONE** (`063132c`) *(deps: M0)* One ctx-free bulk writer (`values().update`+`clear`)
replacing both hand-rolled renderers (~750 per-cell ops → 1–2 requests); **fixes the live ✅ row-shift
bug** (`puzzles_gsheet.py:558/598`) and drops the `RETAWR`/`LAOFNO` skip (`:589`); retires the 50-row
blank. Native Tables optional, gated by a `tableId`-survival probe. Snapshot values, never formulas.

**M3 — `bot/core/` service extraction (kills the `web_*` duplication).** ✅ **DONE** (branch
`feature/m3-service-extraction`) *(deps: M1, M2)* Promote the ctx-free twins, strangle bang-commands
onto them, delete each twin; service owns `spreadsheet_id`; centralise the duplicated
`ADMIN_ROLES`/`STATUSES` constants. The seam everything hangs off. **Delivered:** `bot/core/`
(`constants.py` + `PuzzleService`); **solve, retag (move/add/remove + `refresh_meta`), and
add/remove-sheet** unified in the service (core raises, edges keep presentation + error-tolerance);
the divergent twins (`web_solve_puzzle`, `web_*_to_meta`, `web_*_sheet`) deleted; dead `web_update_field`
removed; regression test added that the meta-sheet rebuild actually fires. **Deferred → M3.5 (see
below):** `web_create_puzzle`/`web_create_round` and `web_refresh_info` are Discord-presentation-heavy
(channel/category creation, pinned-message render) and stay as ctx-free cog edge-helpers for now —
relocating them cleanly needs the Discord channel-management helpers (`get_or_create_channel`,
`clean_name`, divider/welcome logic) extracted first.

**M3.5 — Discord channel-adapter extraction.** ✅ **DONE** (branch `feature/m3.5-channel-adapter`)
*(off critical path; deps: M3)* Extract the Discord channel/category-management helpers into core, then
relocate `create_puzzle`/`create_round` and the pinned-info render onto `PuzzleService` (finishing the
"all operations live in `bot/core/`" goal). **Delivered:** ctx-free `bot/core/discord_channels.py`
(`clean_name`, `get_or_create_channel`, divider/position helpers, channel/category limits); injected
`bot/core/notifier.py` (`Notifier` ABC + `NullNotifier`/`DiscordNotifier`/`CompositeNotifier` fan-out
seam) so per-channel welcome/pinned-info/solve notifications fire regardless of caller;
`PuzzleService.create_puzzle`/`create_round` with relocated orphan-cleanup/rollback; both the bang
create paths (`!p`/`!r`/`!mp`/`!rnm`, via `_round_impl`) and the web dashboard strangled onto the
service with the divergent twins (`create_puzzle_channel`, cog-level `create_puzzle`/`create_metapuzzle`/
`_cleanup_orphan_puzzle`) deleted; bang≡web create equivalence test added. **Note:** `CompositeNotifier`
is the build-only seam for the deferred Phase B bang-command board-push (no `board_changed`/`WebNotifier`
emit here — `bot/core/*` stays free of `bot/web/*` imports).

**M4 — Per-puzzle mutex on the web/service path.** ✅ **DONE** (`4df63e8`) *(deps: M3)* Web actions took
no `_PUZZLE_LOCKS` → dashboard solve/retag could race bang-commands and lose updates. **Delivered:** a
shared per-puzzle async mutex in `bot/core/locks.py` (`puzzle_lock(puzzle_id)` + one `_PUZZLE_LOCKS`
registry used by **both** the `with_puzzle_mutex` bang decorator and the web edge); every
puzzle-mutating web handler (notes, status/priority PATCH, sheets, solve, retag) wraps its fetch+mutate
in the lock. Locking lives at the **edges** — `PuzzleService` stays lock-free because `asyncio.Lock` is
non-reentrant (an edge holding the lock calls the service without deadlock). Create endpoints are not
locked (no existing puzzle to race; single-channel create position-churn is already handled in Discord).

**M5 — `rollups` + on-solve propagation + surfaced failures.** ✅ **DONE** (`ca8c0c3`) *(deps: M3, M4)*
First-class `rollups` record per meta round (migration `002`, `round_id`-keyed) tracking
`last_ok`/`last_error`; refresh now **fails loudly** — a dashboard "sheet stale" badge + warning toast
and a Discord `:warning:` instead of the swallowed `web_refresh_meta` error. Propagation is **inline**
(the planned debounce was dropped as premature at this scale) and serialized by a per-`round_id` rollup
mutex (`bot/core/locks.py`, separate registry from M4). **Failure isolation** across *every* meta-refresh
path (solve, retag, the bang solution edits, `sync_meta`, rename/unsolve/delete) via `SheetRefreshError`
+ `service.refresh_metas`. `!rebuild` (targeted/hunt-wide) + `POST /api/rollups/<round_id>/rebuild` clear
stale sheets. `createDeveloperMetadata` tab pointers are recorded as **M7 substrate**, with the meta
puzzle's `google_page_id` as the trusted render pointer (never dev-metadata alone). `source_mode`/
`edge_type_filter` columns land inert (M6/M7). Spec:
`docs/superpowers/specs/2026-06-22-m5-rollups-design.md`.

**M6 — Additive typed-group columns on `rounds`+`tags`** (resolves the OPEN decision). ✅ **DONE** *(deps: M0, M5)*
`kind`, `status`, nullable `parent_group_id`, nullable `category_id`, `tags.edge_type`; fix `meta_code`
typing; delete the dead `RoundJsonDb.get` (queries `WHERE channel_id` though `RoundData` has none);
cycle-guard the membership writer. ⚠️ Land `category_id` nullable but **don't exercise channel-less
groups yet** — ~12 `get_by_attr(category_id)` lookups assume a category exists; wait for a resolution
path off `ctx.channel.category`. ("Solved section" today is *only* Discord channel position —
`get_solve_divider_position` `:972-978` + the `———solved———` divider channel — which is what `status`
replaces.)

**M7 — Meta-meta + flexible aggregation (Features A + D).** ✅ **DONE** (`28e8abd`; aggregated-answers
read view + copy-to-clipboard follow-on **M7.5** `5cc71b3`) *(deps: M6)* `parent_group_id` containment
+ `edge_type` edges; `source_mode` (round/subtree/explicit; explicit = an ad-hoc group) — apply the
pinned `feeds-meta`-vs-`subtree` rule + the runaround worked example in Part 1; thin command+UI;
**aggregated-answers view + copy-to-clipboard** in the SPA. Also add **`UNIQUE (round_id, puzzle_id)`
on `tags`** (migration after a defensive dedup, replacing the non-unique `tag_index`): membership is
one edge per puzzle/group pair — matches the `commit()` set-difference model and the per-pair
`edge_type` — so the `aggregates-into` writer is a clean `ON DUPLICATE KEY UPDATE` upsert. Doing it in
M7 is information-safe because every existing `tags` row is still the default `feeds-meta` (no
edge_type variation to lose in the dedup); land it before any non-default `edge_type` is written.

**M8 — Minimeta clusters (Feature B).** ✅ **DONE** (mini feature `8ff6549`/`601dd72`/`d08f727`; defect
fixes `feature/m8-minimeta-fixes`; mini-thread solve/move/list polish `8422d2e`) *(deps: M5, M6)*
Minipuzzles = threads under the minimeta + their own `additional_sheets`-style tab (dead
`solution`/`solved` columns activated); puzzle-keyed cluster roll-up onto the minimeta's own sheet.
**Cluster-archive stale-pointer risk is moot** — the roll-up render path resolves its target tab live
every refresh (`puzzles_gsheet.py:601-603`/`715-717`), `rollup.tab_page_id` is M7 substrate only, so a
solved minimeta's roll-up can't dangle (no skip-archive guard needed). 1 channel + (N+1) tabs.
**Open follow-up (small):** the web dashboard mini create/solve/unsolve now refresh the parent
minimeta's pinned list (fixed) — earlier they only updated the sheet, not the Discord pin.

**M9 — Evidence board (Feature C, base).** ✅ **DONE** (M9 series → `0ad872c`) *(deps: M5, M6)* Hunt-wide
evidence board: `!clue`/`!clues` log freeform clues against any puzzle (auto-stamped `source_puzzle`),
accumulating in `evidence_items` (migration `009`), a Google "Evidence" tab (rendered via
`aggregate_to_tab`), and a web `EvidencePanel`; **manual** target-puzzle routing via the web PATCH.
Web POST/PATCH now also re-render the sheet (fixed). **Spec:**
`docs/superpowers/specs/2026-06-23-m9-evidence-board-design.md`. **NOTE — the M9 spec deferred the
capstone half to M9b (below); it is NOT in `0ad872c`.**

**M9b — Capstones / progressive reveal (Feature C, full).** **NOT STARTED** *(deps: M9)* The capstone
scope the original M9 promised but didn't ship: `reveals` edge → dossier tab; "meta matching" — route clues to a
target *and* match against the hunt's metas/answers (today routing is a plain manual dropdown of all
puzzles, no matching); grouped-by-target/suspect roll-up **views** (only the flat evidence tab exists);
the PB10 "Detectives" typed clue store (`suspect`/`attribute`/`assigned_detective`) as an *optional
template*; the three completeness queries. Answer-channel (`feeds-meta`→meta) and evidence-channel
(`reveals`→boards) coexist. **Needs a design pass before build.**

**M10 — Destructive-command safety + bulk-delete pacing.** ✅ **DONE** (`9951408`) *(off critical path;
needs M3)*. Delivered: **confirm-before** (not undo — a deleted sheet/channel isn't recoverable) on
`delete_puzzle`/`delete_round`/`delete_hunt` via a reusable `discord.ui` `ConfirmView` (`bot/cogs/ui.py`)
+ `_confirm_destructive`; the shared `_delete_puzzles` loop is None-guarded + rate-limit/NotFound-tolerant
via `_safe_delete_channel` and surfaces the un-deleted remainder via `_warn_failed_channels` (a half-delete
is resumable, not a crash); orphan cleanup via a shared `_cleanup_orphan_puzzle(hunt, puzzle)` reused by
`web_create_puzzle`, the `web_create_round` rollback (now also drops the nested meta puzzle), and the bang
`!p` path (`_create_puzzle_with_cleanup`). Spec/plan:
`docs/superpowers/specs/2026-06-22-m10-destructive-safety-design.md`. The original scope below is kept for
reference.

**Original M10 scope** *(needs M3)*. Confirm/undo
on `delete_hunt`/`delete_round`/`_delete_puzzles` via `discord.ui` buttons; None-guard
`_delete_puzzles:2588`; pace bulk-delete loops against mid-loop `RateLimited`. **Also: orphan cleanup
on create failure.** Today create safety is ordering-only (sheet-create-then-DB-commit-then-channel),
so a puzzle **channel** create that fails *after* the sheet+DB row commit leaves an orphan (DB row +
Google tab, no channel) with no cleanup; `web_create_round`'s rollback deletes the round row + category
but can still strand the nested meta puzzle's DB row/sheet. No reconciler exists (the DB↔guild reconciler
was deferred at M3/M4). Add targeted orphan cleanup on the puzzle channel-create failure path and complete
the `web_create_round` rollback to also drop the meta puzzle row + sheet.

**M11 — Feeder-as-thread (the channel-quota lever)** *(off critical path; needs M6's nullable
category_id)*. Round of N feeders → 1 channel-create + N thread-creates. Make `cog_before_invoke` +
`activity.py` thread-aware; add `create_puzzle_thread` + a thread branch to `get_or_create_channel`;
reuse `safe_rename` unchanged; archive on solve via `thread.edit(archived=True)`.

### Parallelization / wavefront

The dependency DAG is **front-loaded (quick wins) and back-loaded (the M7/M8/M11 fan-out)** with a
mostly-serial middle. Deps: M1,M2←M0; M3←M1,M2; M4←M3; M5←M3,M4; M6←M0,M5; M7←M6; M8←M5,M6; M9←M5,M6,M8;
M10←M3; M11←M6.

**Serial backbone (can't be parallelized away):** `M0 → (M1 ∥ M2) → M3 → M4 → M5 → M6` — ~6 stages,
each genuinely consuming the prior, with one fork (M1 ∥ M2). Staffing *this* is what shortens the
timeline; parallelizing the leaves only helps once you're past M6.

**What can run concurrently, by wave:**

| When | In parallel |
|---|---|
| **Day one** (need nothing, not even M0) | ✅ **DELIVERED** (`feature/quick-wins` @ `6da1752`): `ErrorBoundary`, defensive notes parsing, prune-deps+README, per-message DB offload, multi-meta drag marker, friendly perm/arg errors, WebSocket auth refresh |
| After **M0** | ✅ **DELIVERED** — **M1 (tests, `917e173`) ∥ M2 (`aggregate_to_tab`, `063132c`)** |
| After **M3** | ✅ **DELIVERED** — **M4 (mutex, `4df63e8`) ∥ M10 (destructive safety, `9951408`)** + audit-log quick win (`f2d97e3`) |
| After **M6** | ✅ **M7 DELIVERED** (`28e8abd` + M7.5 `5cc71b3`) with optimistic-updates quick win (`7502454`); ✅ **M8 DELIVERED** (minimeta clusters); ✅ **M9 evidence board DELIVERED** (`0ad872c`). **Still open: M9b (capstones/reveal — needs design); M11 (feeder-as-thread, deferred)** |

**Practical caveat — DAG-independent ≠ conflict-free.** `bot/cogs/puzzles.py` (2,700 lines) is a
contention magnet (M3 rewrites it; M10 + M7/M8/M9/M11 all add to it). So: the *truly* safe concurrent
work is the frontend / store / Pipfile / `activity.py` quick wins (they barely touch the cog); and
**land M3 cleanly before the fan-out** — once logic lives in `bot/core/`, M7/M8/M9/M11 add there
instead of knife-fighting in the god-cog, turning the post-M6 fan-out from "parallel-but-merge-painful"
into "parallel-and-clean."

### Quick wins — shippable in parallel, no hard dependency

> **✅ 7 of these 9 are DELIVERED on branch `feature/quick-wins` (commit `6da1752`, off `main`)** —
> verified by `py_compile` + the existing `pytest` suite + `tsc --noEmit` (not yet exercised against a
> live DB/Discord). The 2 unmarked items stay deferred behind their noted milestones.

- ✅ **DONE — SPA `ErrorBoundary`** — render throws no longer blank the app; recoverable fallback.
  *(new `frontend/src/components/ErrorBoundary.tsx`, wraps `<App/>` in `main.tsx`.)*
- ✅ **DONE — Friendly permission/argument errors** — `on_command_error` handles `MissingAnyRole`/
  `MissingRole`/`UserInputError`/`CheckFailure` cleanly, ignores `CommandNotFound`, and logs
  unexpected errors server-side instead of dumping a raw traceback.
- ✅ **DONE — Defensive notes parsing** — `json.loads(None)` on a NULL/empty notes column no longer
  crashes the store read (`puzzle_data.import_dict`).
- ✅ **DONE — Prune dead deps / fix manifest / rewrite README** — dropped `gino`/`oauth2client`/
  `google-cloud-datastore`/`gitpython`, added the actually-imported `aiohttp`; fixed the misleading
  postgres `database` default in `config.py`; refreshed the stale README (MySQL + dashboard).
- ✅ **DONE — Move per-message DB writes off the loop** — activity cog enqueues records and a single
  background worker drains them via `asyncio.to_thread` on a **dedicated** DB connection, so a slow/
  reconnecting MySQL no longer stalls the event loop (`activity.py`, `store/__init__.py`).
- ✅ **DONE — Mark multi-meta puzzles on the drag board** — multi-meta feeders are badged and a
  confirm warns before a "move" drop detaches them from their other metas (`DragBoard.tsx`).
- ✅ **DONE — Refresh auth on the activity WebSocket** — membership re-resolved every 60s; a kicked/
  role-changed user is dropped (`api.py`).
- ✅ **DONE — Audit log for web actions** (`f2d97e3`) — `web_audit_log` table + `_audit` on every dashboard mutation.
- ✅ **DONE — Optimistic updates on retag/solve** (`7502454`) — optimistic solve + per-card retag pending (no board blanking).

---

## PART 3 — Improvement catalogue (file:line detail behind the milestones)

58 candidates were generated; these survived adversarial verification. Already-implemented items
(Sheets retry/backoff, deferred renames, cursor closing, narrowed excepts, slash-command prod sync,
read-path pool offload, FK cascades) were filtered out — **do not redo them.**

### Ship now — cheap, high-confidence

| Item | Why | Effort | Where |
|---|---|---|---|
| ✅ **DONE** Friendly permission/argument errors | 30+ role-gated cmds dump raw `MissingAnyRole`/`BadArgument` tracebacks to players | S | `on_command_error` `puzzles.py` *(delivered `feature/quick-wins`)* |
| Web write-actions take the per-puzzle mutex | `!` cmds hold `_PUZZLE_LOCKS`; web `web_*` twins take **no lock** → dashboard solve racing `!s` loses updates | S | `web_*` in `puzzles.py:584-963`, `bot/web/actions.py` |
| ✅ **DONE** Top-level `ErrorBoundary` in the SPA | any render throw blanks the whole app | S | `frontend/src/components/ErrorBoundary.tsx` + `main.tsx` *(delivered `feature/quick-wins`)* |
| ✅ **DONE** Real pytest suite + CI | one 22-line test exists; start with pure units: `build_board`, `hunt_visible`, SQL builder, `web_* vs !` equivalence | L | `tests/`, new `conftest.py`/`pytest.ini` *(delivered M1 `917e173`)* |

### Correctness & reliability

| Item | Why | Effort | Where |
|---|---|---|---|
| Confirm/undo on destructive commands | `delete_hunt`/`delete_round` wipe channels + Google sheets irreversibly behind only a role gate | M | `puzzles.py:2558/2602/2629` |
| `_delete_puzzles` None-guard | crashes on an already-missing channel, aborting a hunt-delete halfway | S | `puzzles.py:2588` |
| ✅ **DONE** Move per-message DB writes off the loop | activity writer fires sync on **every** message on the single shared MySQL conn; slow/reconnecting DB stalls the whole bot | M (high impact) | `bot/cogs/activity.py`, `bot/store/__init__.py` *(queue + worker + dedicated conn; delivered `feature/quick-wins`)* |
| Surface sheet-refresh failures | dashboard shows success while sheet is stale | M | `web_refresh_meta` `puzzles.py:605` |
| Pace/guard bulk channel delete loops | a `delete_hunt` mid-loop `RateLimited` (max_ratelimit_timeout=60) leaves a half-deleted hunt | S | `_delete_puzzles`/`_delete_round_category` |

### Foundation & maintainability

| Item | Why | Effort | Where |
|---|---|---|---|
| ✅ **DONE** Base schema + migration runner | **no `CREATE TABLE` for core tables in the repo**; lone migration is gitignored — a clone can't stand up a DB. (= Structural Phase 0.) | M (high impact) | `migrate.py`, `migrations/`, README *(delivered M0 `7453661`)* |
| Kill the `web_*` duplication (= M3) | 11 hand-synced ctx-free twins with "keep in sync" comments — **already diverged** (`web_solve_puzzle` posts a different embed and *swallows* sheet errors where `_solve_impl` raises). Extract a ctx-free `bot/core/` service layer | L (high impact) | `puzzles.py:584-963` vs the `_impl` methods |
| Centralise shared constants | `ADMIN_ROLES`/`STATUSES` defined in both cog and `web/auth.py`/`web/actions.py` — a security gate that can drift | S | `puzzles.py:26-27,80-81` vs `web/*` |
| ✅ **DONE** Prune dead deps / fix manifest | `gino`,`oauth2client`,`google-cloud-datastore`,`gitpython` unused; `aiohttp` imported but absent from Pipfile; postgres default in `config.py` | S | `Pipfile`, `bot/utils/config.py` *(delivered `feature/quick-wins`)* |
| ✅ **DONE** Rewrite stale README | still describes JSON-file/Postgres storage; bot is all-MySQL | S | `README.md` *(delivered `feature/quick-wins`)* |
| ✅ **DONE** Defensive `import_dict`/notes parsing | `json.loads(None)` on a NULL notes column crashes store-layer reads (dashboard already guards) | S | `puzzle_data.py` *(delivered `feature/quick-wins`)* |

### Dashboard / UX

| Item | Why | Effort | Where |
|---|---|---|---|
| ✅ **DONE** Aggregated answers view + copy-to-clipboard (`5cc71b3`, M7.5) | directly serves the meta-meta goal; mostly pure frontend (`build_board` already groups feeders) | S | new SPA tab; `bot/web/data.py:224` |
| ✅ **DONE** Mark multi-meta puzzles on the drag board | a plain drag calls `tags.clear()` and silently detaches the puzzle from its *other* metas, no warning | S | `DragBoard.tsx` (badge + confirm), `web_move_to_meta` `puzzles.py` *(delivered `feature/quick-wins`)* |
| ✅ **DONE** Optimistic updates on retag/solve (`7502454`) | retag blanks the whole board with a loading overlay for the full rate-limited Discord write | M | `frontend/src/api.ts`, `DragBoard.tsx:202` |
| ✅ **DONE** Live board push on web mutations (`897bcfb`; Phase B bang-command coverage deferred to post-M3.5) | the SPA only polls the board every 30s (`useHunt` `refetchInterval: 30_000`); a solve/retag/create done **on the bot** (or by another admin) isn't reflected until the next poll. Phase A: board-invalidation event emitted on every web mutation → SPA refetches immediately. Phase B (deferred): emit from `PuzzleService` for bang-command coverage — coordinate with M3.5 | M | `bot/web/events.py` + `actions.py` (emit), `frontend/src/hooks/useActivityFeed.ts` + `api.ts` (invalidate `["hunt", uid]`) |
| ✅ **DONE** Sheet-count badge on the sheets icon (`7e0ee59`) | the sheets icon doesn't show how many additional sheets a puzzle has; wrap it in a Mantine `<Indicator label={…} disabled={count===0}>` exactly like the notes icon already does (`PuzzleTable.tsx:217`, `p.notes.length`) for at-a-glance visibility without opening the puzzle. Pure frontend — `puzzle.additional_sheets` is already in the board payload | S | `frontend/src/components/PuzzleLinks.tsx` (sheets icon, mirror the notes `Indicator`); data already via `bot/web/data.py` |
| ✅ **DONE** Audit log for web actions (`f2d97e3`) | web solves/creates run as the single bot identity, no record of which human acted | S | `bot/web/actions.py` |
| ✅ **DONE** Refresh auth on the activity WebSocket | membership resolved once at connect; a kicked user keeps streaming until disconnect | S | `bot/web/api.py` *(re-resolves every 60s; delivered `feature/quick-wins`)* |

### Ideas borrowed from Jolly Roger (2026-10-16)

[Jolly Roger](https://github.com/deathandmayhem/jolly-roger) (Death and Mayhem's hunt tool) overlaps
heavily with this bot; its main difference is self-hosted per-puzzle chat + WebRTC audio, which we
deliberately leave to Discord. Checked against its code (shallow clone, head `cd3446a`): it creates a
**Google Sheet per puzzle** in a **folder per hunt** by copying one template, but never writes cell
values — no meta roll-ups, contribution tables or evidence tabs, which stay ours. Candidates, in
rough order of value:

| Item | Why | Effort | Where |
|---|---|---|---|
| Per-puzzle spreadsheets (per-hunt option) | Faster sheets for solvers, fewer "busy spreadsheet" 500s, and solve/unsolve becomes one Drive folder move that keeps the file id (kills the page-id-changes-on-move bug class: H3, M19, migrations 045/046). **Spiked 2026-10-16:** the service account has its own 15 GB quota (331 MB used); copying the hunt template into a folder works and keeps all 105 named functions (none reference a tab, so trimming tabs is safe); copy ≈ 2.4–3.0 s back-to-back regardless of template size (trimming to REFERENCE only saved ~0.05 s), occasional cold-start spikes to ~12 s; trim + header in ONE batchUpdate (0.8 s); move to a Solved subfolder is one Drive call (1.3 s). Folder is storage only — the summary sheet/dashboard is the index (Drive has no custom sort). Set `importFunctionsExternalUrlAccessAllowed` on each copy (Jolly Roger does) so `IMAGE()`-based functions like `COUNTRY_FLAG` don't prompt per file | L | **Phase 0 prerequisite:** replace the global `_execute_lock` (`bot/utils/gsheets.py:36`) with a per-thread `Http`, or one 2.5 s copy stalls every hunt's Sheets calls. **Phase 1:** a "where does this puzzle's sheet live" seam replacing the request-scoped single spreadsheet (`set_spreadsheet_id`, `puzzles_gsheet.py`; `google_page_id` 90 refs / 7 files, `solved_sheet_id` 45 refs / 7 files) — surface as a structure change before building. Then create/solve/unsolve, minis + extras as tabs inside their puzzle's file, sheet links (`bot/web/api.py:34`), `!reconcile`. Beware the per-hunt-mode rot the forum layout shows — plan for files to become the default |
| Activity indicators on the dashboard | Viewer/message activity per puzzle ("hot"/"quiet") so people find where help is needed; Jolly Roger shows viewer counts + sparklines. Its sheet activity comes from the **Drive Activity API** polled server-side every ~5 s for everything under its Drive root (`gdriveActivityFetcher.ts`) — which reports edits per FILE, so it only works because each puzzle is its own file. With our tabs, start from Discord chat activity (already recorded); per-tab sheet edits would need an installable Apps Script `onEdit` trigger in the hunt template (authorised once per hunt; verify it fires for every editor). With per-puzzle files, mirror Jolly Roger — first verify Drive Activity works for our service account (they use a real user's credential) and whether anyone-with-link editors show up as known or anonymous actors | M | data already recorded by `MySQLChannelActivityDb` (`bot/store/mysqldb.py`) via `bot/cogs/activity.py`; mostly SPA work (`frontend/src/components/PuzzleTable.tsx`) plus a board-payload field in `bot/web/data.py` |
| Announce feeder solves in the meta's channel | When a puzzle is solved, post "<feeder> solved: ANSWER" in the Discord channel of every target it feeds/informs, so meta solvers see answers arrive without watching the board. Jolly Roger does this into its per-meta chat (`ChatHooks.onPuzzleSolved`). Mind the dual solve entrypoints (bang + web) — post from the shared service seam, not one cog path | S | `PuzzleService.solve` (`bot/core/puzzle_service.py`), which already walks the feeder's targets to refresh roll-ups; post via the notifier so web solves announce too |
| Puzzle chat beside the sheet (browser extension) | The thing Jolly Roger's split puzzle page does well — you can't ignore the chat while on the sheet — without iframing Google Sheets (its `DocumentDisplay.tsx` is mostly focus-steal and iOS Safari workarounds). A desktop Chrome/Edge extension: on a puzzle sheet it reads spreadsheet id + gid from the URL, asks our API which puzzle/channel that is, and shows a collapsible overlay panel (Shadow DOM, independent of Sheets' own DOM) with the channel's messages and an unread badge; replies are posted to Discord via a webhook under the sender's name/avatar. Rides the dashboard login session. Desktop only — mobile stays Discord app + Sheets app. Rejected alternative: an Apps Script sidebar (per-file authorisation for a bound script, or a Marketplace add-on to maintain; polling only; must be reopened each time). Pairs with "who's working on this puzzle" (see Bookmarks). **Also: open a puzzle as a tab group** — a dashboard "Work on this" button messages the extension, which opens the puzzle page, its sheet and its Discord channel (`discord.com/channels/…`, already built in `bot/web/data.py:337`) and groups them named after the puzzle (`chrome.tabs.group` + `chrome.tabGroups.update`, extension-only — a plain page can't group tabs, and the pop-up blocker usually lets one click open only one tab). A second click focuses the existing group; on solve the group can collapse/close; a per-user option uses the `discord://` app link instead of a Discord tab. Chrome/Edge; Firefox tab-group API support unverified. Without the extension the button falls back to opening the sheet. **Layout:** put the sheet and the Discord tab in a native Chrome **Split View** (`tabs.createSplit([sheetTabId, discordTabId])`, Chrome 155+, no permission needed — [announced 2026-09-16](https://developer.chrome.com/blog/split-view-api-extensions)); the user drags the divider (no API for pane widths). Split View is two tabs only, and the two must be adjacent and share window + tab group — so the puzzle page stays a third tab in the group. Optional for big screens: tile a separate puzzle-page window beside the split window (`chrome.windows` positioning; screen-size dependent). With a split, the chat overlay panel matters mainly for older Chrome / other browsers and Discord-app users | M | most of the read side exists: messages captured in `messages` (`MySQLMessageDb`), `/api/channels/<id>/messages` (`bot/web/api.py`), `ChatViewer.tsx`. New: sheet→puzzle lookup route, a post-message route (webhook send, as `hunt_archive` already does), live feed via `/ws/activity` (confirm it carries messages) or polling; new `extension/` package |
| Dingwords | A user registers words; the bot DMs them when one appears in a puzzle channel of a hunt they can see | S–M | new listener on `on_message` (the activity cog already sees every message); per-user word list table + `!dingword add/remove/list` (+ dashboard equivalent per the command-parity principle) |
| Bookmarks | Per-user watch list of puzzles, pinned to the top of the dashboard, optionally notified on solve/status change | S–M | per-user table keyed by dashboard user id; SPA toggle + filter; notify via the existing board-push events |

Further candidates from the same discussion:

| Item | Why | Effort | Where |
|---|---|---|---|
| Link puzzle names in every roll-up table to that puzzle's sheet | **User priority (2026-10-26):** in every roll-up table, a puzzle's name links to its own sheet, so the sheets link to each other — the contribution tables on metas, capstones and metametas (`render_contribution`), minimeta tables (`render_minimeta_contribution`), the Unassigned tab, and Evidence where a row names a puzzle. (`add_metapuzzle_data` / `add_metametapuzzle_data` in `puzzles_gsheet.py` are no longer called from anywhere — the old snapshot renderers; don't extend them, they're deletion candidates.) On a meta's contribution table, clicking a feeder/informer jumps to its sheet. Write the name as plain text **with a rich-text link** (`textFormatRuns` → `link.uri`, fields `userEnteredValue,textFormatRuns`), not a `=HYPERLINK()` formula — the reconciler keeps reading cells back as text and a name starting with `=` can't become a formula. Target: `#gid=<page_id>` for a tab on the hunt spreadsheet; the full solved-spreadsheet URL once the feeder has moved there (targets themselves never move); the file URL under per-puzzle sheets. **Catch (tabs only):** the reconciler only rewrites a row whose text changed — a feeds row changes on solve (answer appears) but an informs row doesn't, so its link would go stale when the feeder's tab moves to the archive; include the link in the change comparison. Per-puzzle files remove the catch (a file keeps its URL when moved). Same treatment suits the Unassigned tab's name column, possibly Evidence | S | `contribution_rows` (`bot/core/contribution.py`), `unassigned_row` (`bot/core/unassigned.py`), `reconcile_table._cell` / `diff_rows` (`bot/cogs/puzzles_gsheet.py`, `bot/core/unassigned.py`) |
| Multiple answers, step 1: an expected-answer count | Jolly Roger's model (`solvedness.ts`): `answers[]` + `expectedAnswerCount`, solved is DERIVED when the count is met. Today `puzzles.solution` is one string with answers `/`-joined (`puzzle_service.py:1412`, `:2019`, `:2061`), "solved" is a manual decision and "partially solved" a label, so the bot can't say "2 of 3". Add `puzzles.expected_answers` (default 1 — existing puzzles unchanged) + `!answers N` (and dashboard equivalent); `!solve ANSWER` adds an answer and solves when the count is met; `!partial` folds into it; show "1 of 3" on dashboard/Discord/sheet; expected 0 can replace `!mark_as_complete`'s ✅ placeholder | S–M | one migration; `PuzzleService.solve` / `add_partial` / `add_solution`; `PARTIAL_STATUS` (`bot/core/constants.py`) becomes derived |
| Multiple answers, step 2: answers as rows | A `puzzle_answers` table (one row per answer, who/when): remove or fix ONE answer, answer history, no `/` ambiguity. Optional per-answer routing to a specific meta, so a multi-answer puzzle can feed different metas different answers (the `informs` "distinct per target" idea applied to answers). Keep the `/`-joined string as a derived read-only copy at first so readers migrate one at a time — the string is read in 9 bot files (`notifier`, `contribution`, `unassigned`, `puzzles_gsheet`, `web/api`, `web/data`, `web/actions`, `cogs/puzzles`, `puzzle_service`) and 6 SPA components | M–L | defer until a hunt needs it; the deciding question is how often different answers of one puzzle feed different metas |

**Agreed order (2026-10-16):** finish the multi-server slices 3–5 → per-puzzle sheets (with the
external-data setting) → the small batch: feeder-solve announcements, roll-up name links between sheets,
multiple answers step 1, activity indicators (chat first). Multiple answers step 2 and the
browser extension follow when wanted.

Considered and not adopted: self-hosted chat/audio (Discord already provides chat, voice, mobile,
notifications; audio would need a hosted mediasoup SFU + TURN), and an operator guess queue (most
hunts take submissions on their own site).

### Security hardening (lower priority — internal, role-gated site)

- ✅ **DONE** (`d45a74e`) Sanitize raw `str(exc)` in error responses (`actions.py`); reject the
  placeholder/short `session_secret` at startup.
- ✅ **DONE** (`897bcfb`) CSRF double-submit cookie — non-HttpOnly `csrf_token` cookie echoed as
  `X-CSRF-Token` header; `before_request` guard on all mutating `/api/` requests.

---

## Appendix — provenance

Four background multi-agent workflows (71 + 12 + 4 + 7 agents) produced and verified this:
- `puzzlebot-improvements` — subsystem map, meta-meta deep-dive, 3-way design panel + judge, 58
  candidate improvements each adversarially verified vs the code.
- `puzzlebot-clusters-capstones` — web research (MITMH 2026, Puzzle Boat 10, hunt-tracker patterns),
  unified typed-edge model, minimeta-cluster + capstone designs, integration plan, verification.
- `puzzlebot-capability-spike` — threads / GSheets Tables / other postdating features, verified
  against the pinned deps (discord.py 2.7.1, google-api-python-client 2.197.0). **Zero dep bumps.**
- `puzzlebot-architecture-roadmap` — Discord-coupling footprint, model-first vs conservative + judge,
  the M0–M11 sequence.

Resolved: round-vs-tag-group (→ typed groups on existing tables, M6); model-first scope (direction
not scope, M3/M4); capability gating (zero dep bumps); snapshot-vs-formula (always snapshot).
Still live: explicit-selection membership (folded into M6/M7 as an ad-hoc group); cycle-guard design
(M6); native-Table `copyTo`/`duplicateSheet` survival (probe in M2); cluster-archive stale-pointer
(folded into M8).

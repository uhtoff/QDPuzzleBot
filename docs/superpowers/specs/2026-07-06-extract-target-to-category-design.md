# Extract / Consolidate Target Categories — Design

Date: 2026-07-06
Branch: staging
Status: design approved (brainstorming complete); ready for implementation planning

## Goal

Give organisers explicit control over whether a meta (a `TagGroup` target) owns its
own Discord category, so the two scarce Discord resources can be traded deliberately:

- **Categories** — ~50 per guild (`MAX_CATEGORIES`); the scarcest resource.
- **Channels** — 50 per category (`MAX_CHANNELS_PER_CATEGORY`), 500 per guild.

Today a meta that was **promoted in place** (`convert_to_meta` / promote-in-place set
`category_id = puzzle.channel_id`) is *channel-home*: it shares its host category and
cannot use the split/overflow machinery, because that machinery is keyed on a real
category id. This design adds the tools to move such a meta into its own category (and
back), and — as a scoped follow-on — to do the first automatically under overflow
pressure.

Three deliverables:

1. **`!extract_target_to_category`** — manual command: channel-home meta → its own
   category. **(BUILD)**
2. **Auto-extract on overflow** — a greedy selection policy layered on (1)'s
   primitives; fires when a create would overflow. **(SCOPED follow-on, not built in
   this pass)**
3. **`!consolidate_to <destination>`** — manual command: category-owning meta →
   channel-home inside a named destination category, freeing the vacated category.
   **(BUILD)**

Plus a cross-cutting **target-identification scheme** (emoji channel-name prefixes)
and a **channel-ordering invariant**, because separating targets into their own
categories makes "which channel is the target?" a more frequent question.

## Background — why this exists (justifying analysis)

### `category_id` is the group's identity, and it is only *sometimes* a category

`TagGroupData.category_id` (`bot/store/tag_group_data.py:27-37`) is the group's *home
container*. It points at:

- a real **category** for a normally-created round/meta/capstone (channel layout),
- a **forum channel** in forum layout,
- a **text channel** for a metaless Group (forum) or an **in-place-converted** puzzle
  (`convert_to_meta` / promote-in-place, `bot/core/puzzle_service.py:1772`, `:862`).

The layout engine resolves "which group owns this channel" by category:
`TagGroupJsonDb.get_by_attr(category_id=channel.category.id)`
(`bot/cogs/puzzles.py:184,291,1024,1028,1527`). A channel-home meta's `category_id` is a
channel id, so **it is invisible to this reverse lookup** — the host category's owner
(a round, or the hunt) is treated as owning every channel in the category, including the
channel-home meta's.

### Mixed-category consequences (verified)

When a channel-home meta shares a category with another owner:

- **Split is owner-blind and positional.** `split_group` moves *every text channel
  below the divider* into `"<owner> — solved"` regardless of which target it belongs to
  (`bot/core/category_layout.py:188-198`). So when the host owner overflows, the nested
  meta's solved feeders are dragged into the host's solved category.
- **Consolidation counts are owner-blind.** `puzzle_count` counts all channels in a
  category (`:63-65`); `consolidate` / `_maybe_unsplit` decide split/fold on the
  combined count keyed to the host owner's rows — so the host won't fold back while the
  nested meta's channels inflate it, and the nested meta is never consolidated in its
  own right.
- **One hard failure.** Adding a feeder to a channel-home meta whose host category is at
  the soft cap: `pick_unsolved_category` → `split_group` no-ops (channel-home, resolves
  via `_category_by_id` → None) → `_allocate` raises `RuntimeError("primary_category_missing")`
  (`bot/core/category_layout.py:217-219`), surfaced to the user as the misleading
  ":x: Failed to create puzzle (server full?)."

Key property: **none of this bites below the soft cap** — no split happens, solved
feeders just pool below the single divider in the same category (cosmetic). So the
correct fix is *demand-driven* separation, not eager category creation.

### The engine already tolerates channel-home groups

`_home_category` falls back to the channel's parent category (`:72-80`);
`primary_category` returns `None` for a channel-home group (`:83-97`);
`move_channel_to_owner` explicitly skips splitting a host category that is "not ours to
split" (`:347-354`). This design does **not** teach the engine multi-owner categories;
it keeps the single-owner-per-category invariant and simply flips `category_id` between
a channel id (channel-home) and a category id (category-owning).

## Shared primitives

Add to `bot/core/category_layout.py` (or a small sibling module) two primitives that
both the manual command and the auto policy call:

### `extract_set(guild, hunt, meta) -> list[channel]`

The channels to move when extracting a **channel-home** meta:

- the meta's own **target channel**, plus
- puzzles `P` where **all** of: `P` feeds/informs `meta`; `P` has **no** feeds/informs
  edge to any *other* group; `P` is **not itself a target** (no group targets `P` — i.e.
  sub-metas are excluded).

Selection is **edge-based** (puzzle records), so it finds exclusive feeders wherever
they physically sit — including any that were earlier dragged into the host owner's
`— solved` / overflow categories by an owner-blind split.

### `extract(guild, hunt, meta) -> discord.CategoryChannel`

1. **Guards.** If `meta` already owns a real category (`_category_by_id(category_id)`
   resolves) → no-op / signal "already extracted". If
   `len(guild.categories) >= MAX_CATEGORIES` → `RuntimeError("no_category_space")`.
2. **Create the category by cloning the hunt template** (`hunt.category_id`), **not**
   the host category — this inherits the manually-set hunt role overwrite (the hunt
   permission seam: overwrite is set by an admin on the initial hunt category and cloned
   forward; code never sets it). Name via `clean_category_name(meta.name)` with the same
   collision-suffix logic as `create_tag_group` (`bot/core/puzzle_service.py:2145-2151`).
3. **Place the set** honouring the ordering invariant (below): target at top, then any
   nested targets, then feeders, then the `———solved———` divider (recreated if
   `SOLVE_CATEGORY is False`), then solved channels.
4. **Record the distinct source categories** the moved channels vacated.
5. **Flip identity:** `meta.category_id = new_category.id`; commit. The meta is now an
   ordinary single-category owner — split/overflow/consolidate all work for it forever
   after.
6. **Trigger consolidation on each vacated source** via
   `maybe_consolidate_category(guild, source)` (`:627-641`) so the previous owner
   (round/hunt) folds/un-splits and hands categories back. In the split case this makes
   extraction a **net category reclaim**.
7. If the extracted set exceeds the soft cap, the meta — now a real owner — splits via
   the normal machinery. No special case.

All of the above runs under `category_layout.layout_lock`.

## Feature 1 — `!extract_target_to_category` (BUILD)

- **Bang-only, organiser roles.** Deliberate web-parity exception (Discord plumbing);
  note it against the web-dashboard-command-parity principle.
- **Run in the channel-home meta's own target-puzzle channel.** Resolved from context:
  `channel_type == 'Puzzle'` → `tag_group` via `target_puzzle_id`
  (`bot/cogs/puzzles.py:199`).
- Calls `extract()`. Acks success; friendly refusal on each guard (not a target channel;
  already owns a category; no category space).
- **Board / roll-up untouched** — those are edge-based, so moving channels and flipping
  `category_id` changes nothing there.
- **Teardown improves for free:** after the flip, `delete_round` deletes the owned
  category like any round instead of a bare channel.

## Feature 2 — auto-extract on overflow (SCOPED follow-on)

A **selection policy** over `extract()` — no new plumbing. Hook: the placement path
(`pick_unsolved_category` / `create_puzzle`) at the soft cap.

- **Case A — the new feeder's target is a channel-home meta** (no own category):
  **always** extract that meta, then place the feeder in its new category. This is the
  only non-failing option (a channel-home meta cannot split) and the logical home;
  replaces the `primary_category_missing` hard-fail.

- **Case B — the new feeder's target is the category's own (real-category) owner:**
  1. Enumerate the **nested channel-home metas** living in this category.
  2. `value(m) = 1 (m's own channel) + m's exclusive plain feeders currently in this
     category` — the channels extracting `m` would remove from *this* category.
  3. If `max value > 1` → extract the highest-value meta, then place the feeder
     normally (usually no round split needed).
  4. If `max value <= 1` (or no nested metas) → split/overflow the round as today.

- **Bounded:** at most **one** extraction per overflow event, **non-recursive**. If the
  category is still over the cap afterwards, the normal placement path splits the owner
  as usual.
- **Backstop:** extraction needs a free category slot. At `MAX_CATEGORIES`, Case A →
  honest refusal, Case B → normal split (existing guild-full path). Auto-extract never
  silently consumes the last category slot.
- **Knob:** the `value` threshold (`> 1`) is the single tunable.

Economics: a round split *also* costs one category (the `— solved` category), so
extracting a fat nested meta costs the same *and* heals the mixed-category confusion —
a clear win when `value > 1`. At `value == 1` extraction frees one slot for one category
and the round would still need splitting (two categories total), so falling back to the
plain split is correct.

## Feature 3 — `!consolidate_to <destination>` (BUILD)

The inverse of extract: fold a category-owning meta back into another category and
delete the vacated one, trading a category slot for channel density. "Not done lightly."

- **Bang-only, organiser roles.** Run in the **moving meta's own target-puzzle
  channel**; name the **destination** (a round/target/category, resolved to a category).
  Symmetric ergonomics with extract — both act on "the meta I'm standing in."

- **What moves (asymmetric by current home):**
  - **Meta owns a category:** drain **everything** across its owned category set
    (primary + `— solved` + overflow), *including any nested channel-home metas riding
    along*, into the destination; delete the emptied categories. The meta reverts to
    **channel-home** (`category_id → its target channel id`) now living in the
    destination.
  - **Meta is channel-home:** no category to free — move the `extract_set` (target +
    exclusive plain feeders) into the destination. A rehome, not a category reclaim.

- **Placement:** route moved channels through the destination owner's layout — solved
  channels into the destination's solved category/region, unsolved into unsolved,
  honouring the ordering invariant — allocating overflow as needed. Merging multiple
  sources this way collapses their solved channels into the destination's single solved
  category (part of the saving).

- **Overflow handling:**
  - If the move keeps the destination under the soft cap, **or** the destination is
    **already split**, proceed with no prompt. We deliberately do **not** restrict moves
    into an already-overflowed destination.
  - If the move would newly push the destination **past the soft cap** (i.e. it would
    split and hand back a category, partly defeating the point): **"Are you sure?"**
    confirmation. On **yes** → proactively trigger the destination's split/overflow
    (`split_group`, allocating further overflow categories as needed) **first**, then
    move the channels through solved/unsolved placement so they land correctly.
  - **Hard refuse** only if the categories required to place the move would exceed
    `MAX_CATEGORIES` (`no_category_space`).

- **Confirmation UX:** reuse the cog's existing confirmation pattern if one exists;
  otherwise a react-to-confirm.

## Target identification — emoji channel-name prefixes

Separating targets into their own categories makes "which channel is the target?" a
frequent question, and Discord offers **no** per-channel colour/badge in the sidebar —
only the name string. A **divider channel** is rejected (every divider is a real channel
counting toward the 50/category cap — it fights the very thing this design conserves).
Pure ordering is aspirational, not load-bearing (observed flaky). So: an **emoji prefix
on target channel names**.

### Why prefixes are rename-safe

Discord's strict name/topic edit limit (2 / 10 min) is **per channel** (keyed on channel
id) and applies **only to name/topic edits** — reparenting and repositioning are on the
normal bucket. So:

- A prefix costs a rename **only when a puzzle's structural kind changes** (promote to
  meta, convert back, meta↔metameta transition) — one rename on one channel, well within
  budget.
- **Moves never rename** — the glyph travels with the channel. All the extract/
  consolidate shuffling is rename-free.
- **Solved is never encoded in the name** (it changes on every solve — too churny); it
  stays positional (below the divider), as today.

### Vocabulary

Chosen by the **same structural-kind precedence** as `forum_config.desired_tags_for`
(`runaround > metameta > meta > … > capstone`), so forum tags and channel glyphs share
one source of truth and can't drift.

| Kind | Glyph | Source of the label |
|---|---|---|
| Runaround | 🏃 | stored `TagGroup.type == "Runaround"` |
| Metameta | 🧩🧩 | derived (a meta whose feeders include other targets) |
| Meta | 🧩 | stored `type in ("Round","Metapuzzle")` with a target |
| Capstone | 👑 | stored `type == "Capstone"` |
| Minimeta | *(none)* | intentionally unlabelled |
| Feeder | *(none)* | — |
| Solved | *(none — positional)* | too churny to encode |

- **No separate primary/nested marker.** In a well-organised category there is exactly
  one target (one glyph) — which already answers "which is the target." Mixed categories
  showing several glyphs are precisely what extract/consolidate cleans up.
- The glyph is a prefix on the channel name: `glyph + clean_channel_name(name)`. The
  stored `puzzle.name` stays clean (no glyph); only the Discord `channel_name` carries
  it.

### Helpers and where the prefix is applied

- `target_channel_name(name, kind)` → the prefixed Discord name;
  `strip_target_prefix(channel_name)` → inverse, so any name-based matching normalises
  first. Audit name-based lookups (`group_chat_channel` matches
  `clean_channel_name(tag_group.name)`; the solved divider matches its literal name) and
  route them through the stripped form where a target channel could be matched.
- Apply / update the prefix at: meta creation (`create_puzzle is_meta=True` — set at
  channel-create, no extra rename), `convert_to_meta` / promote-in-place (add), unconvert
  to feeder (strip), a puzzle rename (rebuild the prefixed name if it is a target), and a
  meta↔metameta transition (swap 🧩↔🧩🧩).
- **Optional one-time backfill** to prefix existing targets: many name edits, but each on
  a different channel (per-channel bucket is fine); pace it for discord.py's global limit
  and the `max_ratelimit_timeout` gotcha (removing that setting fixed earlier spurious
  create rate-limit errors — do not reintroduce a small value).

## Channel-ordering invariant (unsolved category)

Within a category's unsolved region, order channels:

1. the category's **target puzzle** (top),
2. any **nested target puzzles** (sub-metas),
3. **regular feeders**,
4. the `———solved———` divider,
5. **solved** channels.

Applied by `extract()` and `!consolidate_to` placement, and preserved by normal
create/solve moves where practical. Treated as **best-effort/aspirational** for existing
flows (ordering has proven flaky); the emoji prefix — not position — is the load-bearing
identification signal.

## Non-goals

- No recursive / subtree extraction — always a single flat level, at most one new
  category per action.
- No web-dashboard equivalents for these two commands (Discord plumbing — explicit
  parity exception).
- Not teaching the layout engine multi-owner categories; channel-home ⇆ category-owning
  via the `category_id` flip is the whole model.
- `convert_to_meta` / promote-in-place remain unchanged — promotion deliberately does
  **not** create a category (category space is the scarce resource); a meta only gains a
  category on explicit extract or under auto-extract pressure.
- Minimeta and solved are intentionally not encoded in channel names.

## Testing (service-level, no live Discord — consistent with existing tests)

- `extract_set`: exclusive plain feeders only; shared feeders and sub-metas excluded;
  finds exclusive feeders regardless of their current category.
- `extract()`: `category_id` flip; clone-from-template (permission inheritance);
  already-owns-category no-op; `no_category_space` guard; vacated-source consolidation
  trigger; ordering of placed channels.
- `!consolidate_to`: full owned-category-set drain including nested metas
  (category-owning case); `extract_set` move (channel-home case); revert to channel-home;
  overflow confirm → pre-split-then-place path; `MAX_CATEGORIES` hard refuse.
- Feature 2 selection policy: Case A always extracts; Case B picks the highest-value
  meta; `value <= 1` falls back to split; one extraction per event.
- Emoji scheme: `target_channel_name` / `strip_target_prefix` round-trip; glyph chosen
  by structural-kind precedence (runaround/metameta/meta/capstone; minimeta/feeder none);
  name-based lookups still resolve a prefixed target channel.

## Open decisions / knobs

- Feature 2 `value` threshold — default `> 1`.
- `!consolidate_to` overflow-confirmation mechanism — reuse existing cog confirm vs
  react-to-confirm.
- Whether to ship the one-time emoji backfill for pre-existing targets, or only prefix
  targets created/converted from here on.

# In-place target creation — design

**Date:** 2026-08-11
**Status:** **Phase 1 landed. Phases 2–5 DESIGNED BUT DEFERRED — not implemented, and no
decision to build them.** Recorded so the design work isn't lost; pick it up from Phase 2.

Before building Phase 2, settle the open question at the bottom ("Nesting semantics"): it
decides whether the new target is tagged into its host at all, and the current Phase 2 text
assumes one answer.

## Goal

Create a metapuzzle / capstone / runaround **inside an existing Discord category**, in one
step, from both the bang edge and the web dashboard — without cloning a new category.

Today the only route to a channel-home target is two manual steps: `!p <name>` in a channel
of the host round, then `!convert_to_meta` in the new channel. Every first-class creator
(`!round`, `!mp`, `!capstone`, `!runaround`, `!group`, and the dashboard's "New group")
goes through `create_tag_group`, which always clones a category and raises
`no_category_space` at the 50-category cap.

## Background — what already exists

- `create_tag_group` (`bot/core/puzzle_service.py:2779`) — category clone + group row +
  optional target puzzle. Always makes a category in channel layout mode.
- `create_puzzle` (`:2631`) — places a puzzle channel via
  `category_layout.pick_unsolved_category` (auto-split/overflow handled) and tags it into
  the host group with `default_edge_for_group(host)` — `feeds` for a meta/runaround host,
  `informs` for a capstone host, `none` for a bare Group host.
- `convert_to_type` (`:1004`) — promote-in-place branch mints a `TagGroupData` with
  `category_id = puzzle.channel_id`, adds the `target` self-edge, restructures the sheet tab
  via `promote_tab_to_group`, sets `source_mode` from `TYPE_DEFAULTS`, and refreshes the
  channel glyph.
- `target_glyph.marked_name` — the `⭐` primary marker is added only when
  `category_layout.primary_category` resolves the owning group's `category_id` to a real
  category. A channel-home target therefore correctly gets its kind glyph (`🧩`/`👑`/`🏃`)
  and no `⭐`, with no extra work.

The new work is a composition of `create_puzzle` + `convert_to_type`, plus one placement
rule, plus the surfaces.

## Out of scope

- Bare `Group` type in place. A Group's home is a chat channel it creates inside its own
  category; there is no channel-home form for it today.
- Changing how `create_tag_group` works, or the `- meta` naming convention on `!r`/`!round`.
- Reordering channels the command was not asked to create (see Phase 2, placement).
- Any change to `!consolidate_to` / `!extract_target_to_category`, which remain bang-only
  (Discord category-layout plumbing; documented parity exception).

---

## Phase 1 — pinned welcome + host glyphs on convert ✅ LANDED

Implemented ahead of the rest, since it fixes existing commands. Scope grew by one item
during implementation (host glyphs) at the user's request.

**Problem 1 — stale pinned welcome on the web edge.** `convert_to_type` never refreshed the
channel's pinned info message, so a converted puzzle kept its plain-puzzle welcome: no group
code, no meta/capstone/runaround tagging hints. The **bang** commands were already fine —
`_run_convert_to_type` calls `set_channel_type(ctx, ...)` then `info(ctx, update=True)`,
which dispatches to the metapuzzle renderer — so the gap was the **web** convert routes,
which have no equivalent. (An earlier draft of this spec claimed the bang path was affected
too; it wasn't.) Putting the refresh in the service covers both edges from one seam; the
bang's own `info` call is now redundant but harmless, and was left alone rather than
restructuring a working path.

**Problem 2 — host glyphs never refreshed.** Converting a puzzle that *feeds* its round into
a meta makes that round a metameta (`is_metameta`: a member that both feeds the group and is
some group's target), so the round's own target channel should gain the second 🧩 — and
demoting takes it away again. Nothing did this: every `_refresh_channel_glyph` call site
refreshes the puzzle it was handed, never a host, so a host kept a stale glyph until someone
ran `!backfill_glyphs`.

**Fix as shipped.** In `convert_to_type`, under the existing `if not torn_down:` guard (the
torn-down puzzle row is deleted, so there is nothing to re-render):

```python
if not torn_down:
    await self._refresh_channel_glyph(guild, hunt, puzzle)
    await self.notifier.refresh_info(guild, hunt, puzzle)
await self._refresh_host_target_glyphs(
    guild, hunt, [gid for gid in host_group_ids if gid != tag_group.id])
```

`host_group_ids` is captured at the *top* of the method, before the promote branch tags the
puzzle into its own new group. The host sweep runs on both branches — a teardown removes a
sub-target, so its hosts need re-glyphing just as much as a promotion's do. The new
`_refresh_host_target_glyphs(guild, hunt, group_ids)` resolves each group's target puzzle and
re-glyphs it, per-host best-effort: the conversion is already committed and a stale glyph
must not fail it.

`convert_to_puzzle` (the demote seam behind `!convert_to_puzzle`/`!convert_to_feeder` and the
web route) gets the same two calls, excluding the group it just deleted.

Nothing else is needed for the welcome. `Notifier.refresh_info` (`bot/core/notifier.py:130`) already routes
`puzzle.is_target` to `send_initial_metapuzzle_channel_messages` with `update=True`,
resolving the group whose `target_puzzle_id` matches. That renderer
(`bot/cogs/puzzles.py:3016`) is already type-aware — it words the channel kind and the
recommended tagging command from `tag_group.type` (capstone members get `!informs`, metas
get `!feeds_only`/`!feeds`), embeds `tag_group.group_code` in every hint, and lists the
meta-relevant commands. `_refresh_pinned_info` (`:2787`) edits the bot's own pin by author
and re-sends + re-pins if it has gone.

**Tests** (`tests/test_convert_service.py`, class `TestConvertRefreshesPinAndHostGlyphs`):
promote-in-place awaits `notifier.refresh_info` once with the converted puzzle and re-glyphs
exactly `[puzzle, host_target]`; a bare-Group host with no target is skipped; the
convert-to-`Group` teardown re-glyphs the host but refreshes no pin; the demote path does
both; and a retype re-glyphs the converted puzzle **once**, proving the sweep skips the
puzzle's own group (a duplicate would burn the strict rename bucket). Mutation-checked:
dropping the `refresh_info` call, the host sweep, or the own-group exclusion each turns one
of these red.

Nine notifier doubles in that suite gained `refresh_info = AsyncMock()`. Stub completeness
only — no assertion was changed or weakened.

---

## Phase 2 — service seam: `create_target_in_place`

New method on `PuzzleService`, next to `create_tag_group`, returning `(tag_group, puzzle)` —
the same shape `create_tag_group` returns, so both cog branches are handled uniformly.

```python
async def create_target_in_place(self, guild, hunt, host_group, name, group_type,
                                 notifier=None):
    """Create a target puzzle inside *host_group*'s existing category.

    The new puzzle becomes the target of its OWN group, whose category_id is the
    puzzle's channel (channel-home) — no category is created. Returns
    (tag_group, puzzle). Raises RuntimeError("<code>") on recoverable problems.
    """
```

**Order of operations**

1. `host_group is None` → `RuntimeError("no_host_group")`.
2. Duplicate guards, **before anything is created**:
   - `PuzzleJsonDb.check_duplicates_in_hunt(name, hunt.id)` → `RuntimeError("puzzle_name_clash")`
   - `TagGroupJsonDb.check_duplicates_in_hunt(name, hunt)` → `RuntimeError("round_exists")`

   The second guard is the one place this is more than sugar. `convert_to_type` mints a
   group named `puzzle.name` with no duplicate check at all, so the manual two-step can
   silently create a second group sharing an existing group's name. `create_tag_group`
   checks both up front; this mirrors it.
3. `puzzle = await self.create_puzzle(guild, hunt, host_group, name, is_meta=False, notifier=notifier)`
4. Placement (below), best-effort. Done here, before the convert, so a `SheetRefreshError`
   out of `convert_to_type`'s closing `set_source_mode` can't leave the channel in the
   wrong slot.
5. `tag_group, _summary = await self.convert_to_type(guild, hunt, puzzle, group_type)`
6. Refresh the **host** target's glyph, best-effort (below).
7. Return `(tag_group, puzzle)`.

Runaround needs no follow-up `set_source_mode`: `convert_to_type` already applies
`TYPE_DEFAULTS["Runaround"]["default_source_mode"] == "subtree"`. (The non-in-place path in
`_round_impl` and the web `create_meta` route both set it explicitly because
`create_tag_group` does not.)

**Placement — `_place_below_host_target(guild, host_group, channel)`**

New private helper. The new channel goes directly below the host category's own target
puzzle, which is assumed to be at the top of the category:

```python
target = (PuzzleJsonDb.get_by_attr(id=host_group.target_puzzle_id)
          if host_group.target_puzzle_id else None)
anchor = guild.get_channel(target.channel_id) if target and target.channel_id else None
if anchor is None or getattr(anchor, "category_id", None) != getattr(channel, "category_id", None):
    return False                      # leave create_puzzle's divider placement
async with category_layout.layout_lock:
    await channel.edit(position=anchor.position + 1)
return True
```

Decisions baked in:

- **Directly below the host target**, literally `anchor.position + 1`. When the category
  already holds other in-place targets, the newest sits immediately under the host target
  and pushes earlier ones down (newest-first).
- **Assume the host target is at the top.** The helper never repositions an existing
  channel. If the anchor can't be resolved, or it's in a different category (a split round
  where the new channel landed in an overflow category), placement is skipped and
  `create_puzzle`'s existing divider placement stands.
- A bare-Group host has no `target_puzzle_id`, so placement is skipped — correct.
- Failures are logged and swallowed: a wrong position must not fail a create whose DB,
  sheet and channel are already committed.

**Host glyph refresh.** The host group has just gained another group's target among its
members, so `_is_metameta_group` now reports it as a metameta and its channel should read
`⭐🧩🧩-x`, not `⭐🧩-x`. Nothing else would do this: every `_refresh_channel_glyph` call site
refreshes the puzzle it was given (`convert_to_type`, `extract_target_to_category`,
`consolidate_to`), and none refreshes a *host* when a nested target appears — so without
this the host's glyph goes stale until someone runs `!backfill_glyphs`. So, after the
convert, resolve `host_group.target_puzzle_id` and call
`self._refresh_channel_glyph(guild, hunt, host_target)`, wrapped in try/except: a stale host
glyph must not fail a completed create.

This does not fix the general case — nesting via `!convert_to_meta` on a puzzle that is
already in a round leaves the host stale exactly as it does today. Closing that properly
belongs with the reconcile glyph work, not here.

**Known limitation.** `anchor.position` comes from discord.py's cache, which lags the
gateway's position recalculation after an insert. A create racing another create in the same
category can leave the new channel one slot off. This matches the existing behaviour of
`move_channel_to_solved` / `_place_channel_in_category`, which take the same lock for the
same reason; it is not made worse here.

---

## Phase 3 — bang surface

`command_map` in `_round_impl` (`bot/cogs/puzzles.py:2087`) gains a fourth field,
`in_place`; existing entries get `False`:

```python
command_map = {
    "r":                   ("Metapuzzle", True,  " - meta", False),
    "round":               ("Metapuzzle", True,  " - meta", False),
    "mp":                  ("Metapuzzle", True,  None,      False),
    "metapuzzle":          ("Metapuzzle", True,  None,      False),
    "capstone":            ("Capstone",   True,  None,      False),
    "cs":                  ("Capstone",   True,  None,      False),
    "runaround":           ("Runaround",  True,  None,      False),
    "ra":                  ("Runaround",  True,  None,      False),
    "group":               ("Group",      False, None,      False),
    "metapuzzle_in_place": ("Metapuzzle", True,  None,      True),
    "capstone_in_place":   ("Capstone",   True,  None,      True),
    "runaround_in_place":  ("Runaround",  True,  None,      True),
}
```

The three new keys are added to the `round` command's `aliases` list, and its docstring's
alias table gains them with "creates the target inside the current category — no new
category" wording.

**No name suffix** for the in-place variants. `convert_to_type` names the group after the
puzzle, so a `" - meta"` suffix would leave a group called "X - meta".

**Branch in `_round_impl`:**

- Host group: `host = self.get_tag_group(ctx)`; `None` → the same error surface as
  `no_host_group`.
- Capacity: `await self.check_available_space(ctx, required_channels=1)` for the in-place
  branch (the non-in-place path's category headroom check lives inside `create_tag_group`).
- Call `self.service.create_target_in_place(ctx.guild, hunt, host, round_name, group_type, notifier=notifier)`.
- Error map gains `no_host_group` → ":x: Run this in a channel of the round you want the new
  meta to live in." `puzzle_name_clash` and `round_exists` reuse the existing messages.

**Permission gate — `@is_organiser()` on the whole `round` command (own commit, precedes
the aliases).** `!round` and its aliases carry no check today, but `!convert_to_meta` is
`@is_organiser()`, so a one-shot built on the current gating would quietly let a solver do
what the two-step gates. Rather than special-case the new aliases, tag-group creation
becomes organiser-only across the board: `!round` is effectively an alias for
`create_tag_group`, and every other tag-group operation (`!rename_group`, `!delete_round`,
`!convert_to_*`, `!extract_target_to_category`, `!consolidate_to`) is already
organisers/admins. One decorator then covers all nine existing aliases and the three new
ones.

```python
@commands.command(aliases=[...])
@is_organiser()
async def round(self, ctx, *, arg):
```

**This is a deliberate behaviour change**, not a side effect: non-organisers lose `!round`,
`!r`, `!mp`, `!metapuzzle`, `!capstone`, `!cs`, `!runaround`, `!ra` and `!group`. Two things
bound the blast radius — `is_organiser()` passes unconditionally on a guild with nothing
standing for the Organiser tier (`role_gate.organiser_gate_active`), so unconfigured servers
see no change at all; and puzzle creation is untouched (see below).

**Puzzle creation stays open.** `!puzzle`/`!p` and `POST /hunts/<uid>/puzzles` keep their
current "anyone" gating — solvers adding puzzles as they find them is the intended flow. The
line is: creating or reshaping a *tag group* is an organiser action; adding a *puzzle* to one
is not.

Docs to update in the same commit: `docs/commands.md` rows for `!round`, `!metapuzzle`,
`!capstone`, `!runaround`, `!group` move from "anyone" to "organisers/admins", and each
command docstring gains the house "Organisers/admins only." line.

---

## Phase 4 — web route

Extend `POST /hunts/<uid>/metas` (`bot/web/actions.py:2125`) rather than adding a route —
it is the same user intent with one more option.

- Body gains optional `host_group_code` (string). Absent or empty → today's
  `create_tag_group` path, unchanged.
- Present → resolve with `TagGroupJsonDb.get_by_attr(group_code=...)`, mirroring the
  create-puzzle route at `:2096`; unknown → `{"error": "bad_group_code"}, 400`.
- `type == "Group"` together with a `host_group_code` → `{"error": "bad_type_for_host"}, 400`
  (out of scope, above).
- `await require_organiser(ctx["guild_snowflake"])` **for the whole route**, matching the
  bang gate. Gating `!round` while leaving `POST /hunts/<uid>/metas` open would make the
  dashboard a bypass of the gate. `require_organiser` carries the same unconfigured-tier
  bypass as `is_organiser()`, so the two edges agree on every guild.
  `POST /hunts/<uid>/puzzles` stays ungated, matching `!p`.
- Keep the existing `hunt_create_lock(hunt_obj.id)` around the whole service call.
- Audit: `detail` records the new group code and the host code.
- Response unchanged: `{"ok": True, "tag_group_id": ..., "group_code": ...}`.

## Phase 5 — frontend

`frontend/src/components/CreateControls.tsx`, "New group" modal:

- Mantine `Switch`, label **"Own Discord category"**, default **on**.
- Off → reveal a `Select` labelled **"Category"**, reusing the same `detail.board` options
  the "New puzzle" modal builds (`group_code != null`), **without** the `UNASSIGNED`
  sentinel. No payload change: picking a channel-home group still works, because
  `create_puzzle` → `pick_unsolved_category` → `_home_category` falls back to that channel's
  parent category.
- Off → the `Group` type option is disabled in the Type select, with a hint that a group
  always needs its own category.
- The name field's description switches from "Creates a new category with a channel and
  Google sheet." to "Creates a channel and Google sheet inside the chosen category."
- `useCreateMeta` (`frontend/src/api.ts:455`) takes an optional `hostGroupCode` and sends
  `host_group_code` when set.
- Success toast names the host: `Created meta "X" in Y`.
- The **"New Group"** button is hidden when `!detail.hunt.can_manage` — the same organiser
  signal `BoardGroup.tsx:49` and `DragBoard.tsx:484` already use — so the control matches the
  new route gate instead of failing with a 403. "New puzzle" stays visible to everyone.

---

## Testing

Runner: `timeout 180 pipenv run python -m pytest <paths> -q > /tmp/test.log 2>&1`.

**`tests/test_convert_service.py`** (Phase 1)
- promote-in-place awaits `notifier.refresh_info` once with the converted puzzle
- the convert-to-`Group` teardown path does not

**`tests/test_create_service.py`** (Phase 2)
- no category is created: `ref_category.clone` never awaited; the channel is created in the
  host's category
- `tag_group.category_id == puzzle.channel_id`, and the group's type matches the requested one
- **two hosts of different type** (Metapuzzle host vs Capstone host) so a swapped-edge
  implementation fails: the new target carries `feeds` / `informs` into the host
  respectively, plus a `target` self-edge to its own group
- a name colliding with an existing **group** name raises `round_exists` before any channel
  or sheet work (assert `create_puzzle` never awaited)
- a name colliding with an existing **puzzle** name raises `puzzle_name_clash`, same
- Runaround variant lands `source_mode == "subtree"`
- channel name is `🧩-x` with **no** `⭐` (also closes part of the reconcile glyph coverage gap)
- placement: `channel.edit` awaited with `position == anchor.position + 1`; with the anchor
  in a different category, `edit` is not called with a position and the create still succeeds
- `host_group=None` raises `no_host_group`

**`tests/test_create_meta_in_place_web.py`** (Phase 4, new file, following
`tests/test_convert_to_meta_web.py`'s harness)
- `host_group_code` present → `create_target_in_place` called with the resolved group;
  absent → `create_tag_group`, unchanged
- unknown code → 400 `bad_group_code`; `type: "Group"` + host → 400 `bad_type_for_host`
- non-organiser → 403 on **both** the plain and the host path; `POST /hunts/<uid>/puzzles`
  is still allowed for the same user (proves the gate landed on tag-group creation only)

Mutation-check the two that matter most: swap `feeds`/`informs` in
`default_edge_for_group`, and drop the `+ 1` in the placement helper — both must go red.

Frontend has no test runner: the modal, switch and payload wiring are typecheck-and-
inspection only. Stated plainly rather than implied to be gate-verified.

## Acceptance criteria

1. `!metapuzzle_in_place <name>`, `!capstone_in_place <name>`, `!runaround_in_place <name>`
   in any channel of a round create a target puzzle in that category, with no new category,
   directly below the category's own target puzzle.
2. The new channel carries its kind glyph and no `⭐`, and the host's own target channel is
   re-glyphed to the metameta `🧩🧩` in the same operation.
3. The new channel's pinned message is the meta/capstone/runaround welcome, with its own
   group code in the tagging hints — and so is every channel converted by
   `!convert_to_meta` and the dashboard route.
4. A name clashing with an existing group or puzzle is rejected before anything is created.
5. Tag-group creation is organiser-only on both edges: `!round` and all its aliases refuse a
   non-organiser exactly as `!convert_to_meta` does, `POST /hunts/<uid>/metas` returns 403,
   and the dashboard hides "New Group". On a guild with no Organiser tier configured,
   nothing changes.
6. `!puzzle`/`!p` and `POST /hunts/<uid>/puzzles` are still open to anyone.
7. The dashboard "New group" modal creates in place when "Own Discord category" is off,
   with the same result as the bang command.
8. `!round` / `!mp` / `!capstone` / `!runaround` / `!group` and the dashboard's default path
   behave exactly as before, apart from the gate.

## Open question — nesting semantics (settle before Phase 2)

`consolidate_to` does **not** tag anything: its only DB write is
`meta.category_id = target_puzzle.channel_id`. So the two routes into a shared category
produce different structures, and Phase 2 has to pick one:

- **A — `!p` + `!convert_to_meta`** (what Phase 2 above is written against). `create_puzzle`
  tags the new puzzle into the host with `default_edge_for_group` — `feeds` for a
  Metapuzzle/Runaround host, `informs` for a Capstone host. The new meta is a member of the
  host, the host becomes a metameta, and the submeta's answer rolls up into the host's sheet.
- **B — `!mp` + `!consolidate_to`.** No tag, no edge: an independent group that merely shares
  a Discord category. Nothing rolls up; on the board they are siblings.

B costs more to build (`create_puzzle` tags as a side effect of placing the channel, so it
means creating tagged then removing the edge, or a placement-only path) and would drop the
host-glyph refresh from Phase 2's scope. A is cheaper and is what the approved "one-shot
sugar" framing implies. Not settled.

## Rejected alternatives

- **A separate standalone command** (hybrid, with a type dropdown). Rejected: a second
  creation entry point to keep in sync with `_round_impl`, for no gain — every other creator
  is an alias on one command.
- **A `position` / `place_below_target` parameter on `create_puzzle`.** Rejected: it is a
  hot shared path used by every create; a post-create reposition in the new method keeps the
  change local, at the cost of one extra Discord edit.
- **Normalising the host target to position 0** before placing. Rejected: a create command
  should not silently reorder channels it was not asked about.
- **A bot-resolved category list in the hunt payload** for the picker. Rejected for now:
  needs a new resolve + payload field, and the group list already reads as the category list
  in the common case.
- **Gating only the new in-place aliases**, via a module-level `organiser_check(ctx)`
  extracted from `is_organiser()`'s predicate (a decorator can't discriminate between
  aliases of one command). Rejected by the user: `!round` is effectively an alias for
  `create_tag_group`, so the gate belongs on tag-group creation as a whole, not on one
  branch of it. The extraction is no longer needed.
- **Doing nothing** (documenting the two-step instead). Rejected by the user: the one-shot
  is wanted, and the missing group-name duplicate check makes the two-step genuinely unsafe.

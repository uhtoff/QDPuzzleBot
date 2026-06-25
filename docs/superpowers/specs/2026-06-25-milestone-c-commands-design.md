# Milestone C — Command surface design

**Date:** 2026-06-25
**Status:** Design agreed; ready for implementation planning.
**Parent design:** `docs/superpowers/specs/2026-06-24-taggroup-edges-and-commands-design.md`
**Schema spec:** `docs/superpowers/specs/2026-06-25-taggroup-schema-changes.md`

Milestones A (vocabulary rename) and B (edge model: feeds/informs/none, drop kind) are
complete. This spec covers Milestone C: the command surface — new bang commands, the
convert family, and web API parity for model-changing operations.

No new DB migrations. The schema from A+B is the substrate this work builds on.

---

## Scope

**In scope:**
- Type derivation map (`TYPE_DEFAULTS`) in the service layer
- New edge-aware service primitives replacing the old tag-based methods
- New bang commands: create (`!capstone`, `!runaround`, `!group`), membership
  (`!feeds`, `!feeds_only`, `!informs`, `!informs_only`, `!groups_with`,
  `!groups_only_with`, `!remove_from`), convert family, edge-aware `!sync`
- Legacy bang aliases at the command level (no service-layer wrappers)
- Removal of `!pool`, `!nest`, `!source_mode` bang commands (service internals kept)
- Web API parity: extended create, membership, and convert endpoints
- Service + web tests for new primitives and endpoints

**Out of scope:**
- Frontend board UI changes — board stays Metapuzzle-only; new types/edges are
  bang-command-only for now
- `!sync` web parity — Discord-ergonomics command, bang-only by design
- Sheet generation changes (Milestone D)

---

## Service layer

### Type derivation map

Module-level constant in `bot/core/puzzle_service.py`:

```python
TYPE_DEFAULTS = {
    "Round":      {"default_edge": "feeds",   "default_source_mode": "round"},
    "Metapuzzle": {"default_edge": "feeds",   "default_source_mode": "round"},
    "Capstone":   {"default_edge": "informs", "default_source_mode": "round"},
    "Runaround":  {"default_edge": "feeds",   "default_source_mode": "subtree"},
    "Group":      {"default_edge": "none",    "default_source_mode": "round"},
}
```

This is the single source for three paths: (1) tagging newly-created puzzles,
(2) `!convert_to_<type>` flipping existing members, (3) edge-aware `!sync`.

### New edge primitives

These replace `add_to_meta`, `move_to_meta`, `remove_from_meta`, and `clear_metas`.
Old methods are **removed**; all callers updated.

**`tag_with_edge(puzzle, group, edge)`**
Upserts the `tags` row for (puzzle, group) with the given edge type. Appends
`group.id` to `puzzle.tags` if absent. Commits, refreshes affected groups, board-push.
No channel move. Idempotent — re-calling with a different edge updates in place.

**`tag_edge_only(puzzle, group, edge, guild=None)`**
Same upsert, then removes every other edge *of the same type* for this puzzle
(e.g. clears all other `feeds` edges, leaving `informs`/`none` edges untouched).
If `guild` is provided **and** `edge == "feeds"`, moves the puzzle's channel into
the group's category (mirrors old `move_to_meta` geography behaviour). Commits,
refreshes all affected groups, board-push.

**`untag_from(puzzle, group, edge=None)`**
Deletes the `tags` row for (puzzle, group). When `edge` is specified, is a no-op
if `puzzle.tag_edges.get(group.id) != edge` (type-guarded remove). When `edge` is
`None`, unconditional. Removes `group.id` from `puzzle.tags`. Commits, refreshes,
board-push. Retains the metapuzzle-cannot-be-orphaned guard.

**`bulk_re_edge(tag_group, new_edge, hunt) -> str`**
`UPDATE tags SET edge_type = new_edge WHERE tag_group_id = <id>`. Returns a
human-readable summary: `"flipped 7 feeds + 2 none → informs"` (counts per old
edge type). Returns `"0 members"` for an empty group. Used by all convert commands
to report what was swept.

### Convert service methods

**`convert_to_type(guild, hunt, puzzle, new_type) -> (TagGroupData, str)`**

Unified convert path for `Metapuzzle`, `Capstone`, `Group`, `Runaround`:

1. **Promote-in-place** if the puzzle has no group yet: creates a new group around
   it (like existing `convert_to_meta`) using `new_type` for `tag_group.type`.
   Idempotent if the group already exists and type is already correct.
2. Sets `tag_group.type = new_type`.
3. Re-derives `default_source_mode` from `TYPE_DEFAULTS[new_type]` and writes it
   to the group's rollup (calls `set_source_mode` internally).
4. **Edge flip** (except Runaround): calls `bulk_re_edge(tag_group, default_edge, hunt)`
   where `default_edge = TYPE_DEFAULTS[new_type]["default_edge"]`.
   - `Runaround`: no edge flip; only sets `source_mode=subtree`. Nesting child rounds
     under the runaround (`parent_group_id`) is not automatic — there is no obvious
     parent to infer at convert-time, and no bang command exposes nesting in C.
5. Commits, refreshes, board-push.
6. Returns `(tag_group, summary_str)` — summary from `bulk_re_edge`.

**`convert_to_puzzle(guild, hunt, puzzle) -> int`**

Existing `convert_to_feeder` renamed. Destructive: deletes the group, orphaning
every member. Confirmation is the caller's responsibility (bang two-step, web
preview). Returns orphan count. Behaviour unchanged from current implementation.

---

## Bang command surface

### Create commands

Extend `_round_impl` and `command_map` in `bot/cogs/puzzles.py`:

| command | aliases | makes target | `type` value | notes |
|---|---|---|---|---|
| `!r` | `round` | `"name - meta"` | `Round` | unchanged |
| `!mp` | `metapuzzle` | `"name"` | `Metapuzzle` | unchanged |
| `!capstone` | `!cs` | capstone puzzle | `Capstone` | new |
| `!runaround` | `!ra` | endgame puzzle | `Runaround` | new; sets source_mode=subtree post-create |
| `!group` | `!rnm` | — (no target) | `Group` | `!rnm` becomes alias; label was "Metaless Round" |

`!runaround` post-create: calls `set_source_mode(hunt, tag_group, "subtree")` internally.
The `!nest` and `!source_mode` bang commands are **removed**; `set_parent_group` and
`set_source_mode` remain as internal service methods.

### Membership commands

All take `<group_code>` (autocompleted). All require a puzzle channel context.

| command | aliases | service call | channel move? |
|---|---|---|---|
| `!feeds <code>` | `add_to_meta`, `add_to_round` | `tag_with_edge(puzzle, group, "feeds")` | no |
| `!feeds_only <code>` | `move_to_meta`, `move_to_round` | `tag_edge_only(puzzle, group, "feeds", guild)` | yes |
| `!informs <code>` | — | `tag_with_edge(puzzle, group, "informs")` | no |
| `!informs_only <code>` | — | `tag_edge_only(puzzle, group, "informs")` | no |
| `!groups_with <code>` | — | `tag_with_edge(puzzle, group, "none")` | no |
| `!groups_only_with <code>` | — | `tag_edge_only(puzzle, group, "none")` | no |
| `!remove_from <code>` | `remove_from_meta`, `remove_from_round` | `untag_from(puzzle, group)` | n/a |

Guards preserved: metapuzzle cannot be left with zero memberships
(`!remove_from` / `_only` keep today's protection).

### Edge-aware `!sync`

**Aliases:** `sync_meta`, `sync_feeders`, `sync_round`. Bang-only (no web parity).

Behaviour: reads `default_edge = TYPE_DEFAULTS[tag_group.type]["default_edge"]`
for the current channel's group, then applies `tag_edge_only(puzzle, group, default_edge)`
to every puzzle in the category, skipping the group's own target puzzle.
Reports what it swept (aggregate summary across all puzzles). Requires
organiser role (same as old `sync_meta`).

This means `!sync` on a Round feeds the category, on a Capstone informs it,
on a Group groups it — and therefore `!convert_to_capstone` + `!sync` does the
obviously-right thing.

### Convert commands

All run in the channel of the target puzzle (puzzle channel context required).
Admin role required (same as existing convert commands).

| command | aliases | effect | confirm? |
|---|---|---|---|
| `!convert_to_meta` | — | `convert_to_type(..., "Metapuzzle")` | no |
| `!convert_to_capstone` | — | `convert_to_type(..., "Capstone")` — flips feeds→informs | no |
| `!convert_to_group` | — | `convert_to_type(..., "Group")` — flips all→none | no |
| `!convert_to_runaround` | — | `convert_to_type(..., "Runaround")` — no edge flip, sets subtree | no |
| `!convert_to_puzzle` | `convert_to_feeder` | `convert_to_puzzle(...)` — deletes group, orphans members | yes — two-step |

**Two-step confirmation for `!convert_to_puzzle`:** first invocation reports
orphan count and asks for confirmation; user re-runs with `!convert_to_puzzle confirm`
(or equivalent) to proceed. Same UX as existing `convert_to_feeder`.

**Report what was swept:** every convert command (except `!convert_to_puzzle`) sends
the `summary_str` from `convert_to_type` in its success reply:
`"✅ Converted to Capstone. flipped 7 feeds + 2 none → informs."`

### Removed commands

`!pool` — dropped entirely, no legacy alias.
`!nest` — removed; `set_parent_group` stays as internal service method.
`!source_mode` — removed; `set_source_mode` stays as internal service method.

---

## Web API parity

All endpoint changes are **backward compatible**: existing callers that omit new
fields get the same behaviour as before.

### Create endpoint — `/hunts/<uid>/metas` (POST)

Add optional `type` body field (`"Round"`, `"Metapuzzle"`, `"Capstone"`,
`"Runaround"`, `"Group"`). Defaults to `"Metapuzzle"` when absent. Passed as
`round_type` to `create_tag_group`.

Response unchanged: `{"ok": true, "tag_group_id": ..., "group_code": ...}`.

### Membership endpoint — `/puzzles/<id>/meta` (POST)

Add optional body fields:
- `edge` — `"feeds"` | `"informs"` | `"none"` (default: `"feeds"`)
- `only` — boolean (default: `false`)

`mode=move` becomes an alias for `mode=add, only=true` for backward compat.

Dispatch table:

| mode | edge | only | service call |
|---|---|---|---|
| `add` | `feeds` | false | `tag_with_edge(puzzle, group, "feeds")` |
| `add` | `feeds` | true | `tag_edge_only(puzzle, group, "feeds", guild)` |
| `move` | any | any | `tag_edge_only(puzzle, group, "feeds", guild)` |
| `add` | `informs` | false | `tag_with_edge(puzzle, group, "informs")` |
| `add` | `informs` | true | `tag_edge_only(puzzle, group, "informs")` |
| `add` | `none` | false/true | same pattern |
| `remove` | — | — | `untag_from(puzzle, group)` |
| `clear` | — | — | `untag_from` loop (edge-agnostic) |

### Convert endpoints

**Existing (updated internally):**
- `/puzzles/<id>/convert-to-meta` (POST) — now calls `convert_to_type(..., "Metapuzzle")`
- `/puzzles/<id>/convert-to-feeder` (POST) — kept at old URL; alias for `/convert-to-puzzle`
- `/puzzles/<id>/convert-to-feeder/preview` (GET) — kept at old URL

**New:**
- `/puzzles/<id>/convert-to-capstone` (POST) — `convert_to_type(..., "Capstone")`
- `/puzzles/<id>/convert-to-group` (POST) — `convert_to_type(..., "Group")`
- `/puzzles/<id>/convert-to-runaround` (POST) — `convert_to_type(..., "Runaround")`
- `/puzzles/<id>/convert-to-puzzle` (POST) — `convert_to_puzzle(...)` (destructive)
- `/puzzles/<id>/convert-to-puzzle/preview` (GET) — same as existing feeder preview

All convert endpoints return `{"ok": true, "summary": "<sweep report string>"}`.
The `summary` field is omitted for `convert-to-puzzle` (destructive, nothing to sweep).

---

## Testing

**Service layer** (new test file `tests/test_edge_primitives.py`):
- `tag_with_edge` — creates row, updates `puzzle.tags`, idempotent edge-in-place update
- `tag_edge_only` — sets edge on target, removes other same-type edges, leaves
  different-type edges intact; `guild=None` skips channel move
- `untag_from` — unconditional remove; type-guarded no-op when edge doesn't match
- `bulk_re_edge` — correct summary string; empty group; all-same-type group
- `convert_to_type` — one test per type; promote-in-place on bare puzzle;
  idempotent on already-correct group; Runaround: no edge flip, source_mode=subtree
- `convert_to_puzzle` — removes all members, returns orphan count

**Web endpoints** (extend existing web test classes):
- Create with `type` param — Capstone, Runaround, Group created correctly
- Retag with `edge` + `only` — correct service method dispatched
- `mode=move` backward compat — still maps to feeds_only
- New convert routes — `summary` field present in response

**Acceptance bar:** full suite green (`timeout 180 pipenv run python -m pytest tests/ -q`),
`ruff check` exit 0. Bang commands not directly tested (Discord mock overhead);
covered by service tests.

---

## Constraints

- **No migration.** Schema from A+B is the substrate.
- **No frontend changes.** Board stays Metapuzzle-only.
- **No new test infrastructure.** Reuse existing DB fixtures and web test patterns.
- **Old service methods removed outright** (`add_to_meta`, `move_to_meta`,
  `remove_from_meta`, `clear_metas`). Legacy aliases exist only at the bang command
  level — `add_to_meta` as a command calls `!feeds` implementation.
- **`!pool` dropped entirely** — no legacy alias, no deprecation message.
- **`!nest` / `!source_mode` bang commands removed** — service internals kept.
- **`untag_from` type guard** — when `edge` param is specified, no-op if the
  puzzle's current edge to that group doesn't match.
- **Promote-in-place geography-neutral** — `convert_to_type` on a bare puzzle
  creates the group but does not create or move Discord channels/categories.
  Channel-creation is `create_tag_group`'s job; convert only wraps an existing puzzle.

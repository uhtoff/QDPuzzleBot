# Group-level convert & delete — design

Make tag-group **type changes** and **deletion** first-class *group-level* operations, driven
from a kebab menu on each group in the dashboard Arrange view. Two motivating bugs:

1. **Convert-to-Group leaves the meta behind.** `convert_to_type(..., "Group")` flips member
   edges to `none` but never removes the target metapuzzle: its DB record, its Google sheet, and
   its metapuzzle channel/pin all survive. The result is a "Group" that still has a meta.
2. **Empty groups can't be deleted from the web.** The By-group board hides groups with no
   puzzles (`HuntBoard.tsx` filters `puzzles.length > 0`), and the only delete control lives on
   `BoardGroup`. A bare Group (or any group with no puzzles) surfaces only in the Arrange view,
   which today has no delete action.

The unifying idea: **type and lifecycle belong to the TagGroup, not to its target puzzle.** The
target-puzzle card stops carrying group semantics; the group carries them.

## Decisions (locked with user)

1. Convert-to-Group **tears down the meta**: delete the meta puzzle record + its Google sheet,
   rename its Discord channel into the group's general chat channel, refresh the pin to the group
   welcome.
2. A **bare Group → targeted type** is supported (new capability): it **creates** a meta puzzle —
   the symmetric inverse of the teardown, repurposing the group's general chat channel into the
   meta channel (rename + create sheet + meta welcome pin).
3. Convert operations are **keyed on the TagGroup**, not the target puzzle.
4. A group kebab menu (Arrange view, per-column header) carries: change type, convert-to-feeder
   (delete group but keep the target as a plain feeder), and delete group.
5. The **target-puzzle card** in Arrange is reduced to just **"Add to group…"** — convert-\* and
   delete-puzzle are removed from it.
6. The kebab is added **only to the Arrange view** for now. The By-group view keeps its existing
   `BoardGroup` trash-delete for non-empty groups.
7. **Bang commands keep working as today** (channel/puzzle context). They gain only the
   Group-teardown fix via the shared service seam; no bang re-keying.

## Out of scope

- Forum-layout parity for the new channel repurposing. Forum layout is being left to go stale;
  the channel repurpose/create paths target the category-based layout and must be safe no-ops (or
  best-effort) under forum mode, not break it.
- Any change to the By-group `BoardGroup` delete UI.
- Minimeta / feeder card actions (retag, make/remove minimeta, convert-to-meta promote,
  delete-puzzle) — those stay on the feeder puzzle card unchanged.

## Terminology

- **Targeted group** — a TagGroup whose `target_puzzle_id` is set (type Metapuzzle / Capstone /
  Runaround). Its target is "the meta".
- **Bare Group** — a TagGroup of type `Group` with `target_puzzle_id == 0`. Its members pool with
  the `none` edge; it has a dedicated general chat channel (`category_layout.group_chat_channel`).
- **General chat channel** — the target-less Group's text channel, named
  `clean_channel_name(group.name)`.

---

## Backend

### New service seam — `convert_tag_group_type`

`bot/core/puzzle_service.py`, a group-keyed method:

```python
async def convert_tag_group_type(self, guild, hunt, tag_group, new_type):
    """Change a TagGroup's type, keyed on the group (not its target puzzle).
    Returns (tag_group, summary_string). Raises RuntimeError("<code>") on
    recoverable problems."""
```

Dispatch by (current has-target, new_type):

| From \ To            | Metapuzzle / Capstone / Runaround        | Group                                   |
|----------------------|------------------------------------------|-----------------------------------------|
| **Targeted group**   | retype + re-edge (existing behaviour)    | retype + re-edge `none` + **teardown**  |
| **Bare Group**       | **create meta** + re-edge to type default| n/a (already Group; menu omits current) |

It reuses the target puzzle it resolves (`PuzzleJsonDb.get_by_attr(id=tag_group.target_puzzle_id)`)
and delegates the existing retype/re-edge to today's `convert_to_type` logic where possible. The two
new branches:

**(a) Teardown — targeted → Group** (`_teardown_group_target`):
1. `gsheet_cog.delete_puzzle_spreadsheet(target)` — best-effort, failure isolated + logged.
2. `PuzzleJsonDb.delete(target.id)`; clear `tag_group.target_puzzle_id = 0`; drop the target's
   self-edge (`tag_edges.pop(tag_group.id)`), commit the group.
3. Non-forum + guild present: rename the target's channel to
   `clean_channel_name(tag_group.name)` via `self.safe_rename(channel, name)`, then refresh the
   pin to the group welcome via a new `notifier.refresh_group_info(guild, hunt, tag_group)`.
4. Order: run the edge flip (`bulk_re_edge(..., "none")`) **while the target still exists** so its
   self-edge exclusion/refresh is correct, then tear down.

**(b) Promote — bare Group → targeted** (`_promote_group_to_target`):
1. Retype the group (`tag_group.type = new_type`; commit) so `create_puzzle`'s welcome renders
   the right kind.
2. **Delete the old general chat channel first**, then create the meta via
   `self.create_puzzle(guild, hunt, tag_group, tag_group.name, is_meta=True, notifier=self.notifier)`.
   Reason: `create_puzzle` calls `get_or_create_channel(clean_channel_name(name))`; because the meta
   name equals the group name, an *existing* general channel would be found with `created=False`,
   skipping the channel-link + welcome and leaving the meta channel-less. Deleting it first makes
   `create_puzzle` create a fresh meta channel (`created=True`), fully linked with a meta welcome
   pin, and its sheet tab. `create_puzzle` also sets `is_target=True` and tags the `target`
   self-edge, so no re-tag is needed. *(Accepted tradeoff: a bare Group's chat history is lost;
   bare groups rarely carry meaningful discussion. Teardown, by contrast, repurposes the meta
   channel in place to preserve the meta's discussion.)*
3. Set `tag_group.target_puzzle_id = puzzle.id`; commit. Set source_mode + re-edge members to
   `TYPE_DEFAULTS[new_type]["default_edge"]` via `set_source_mode` + `bulk_re_edge`.
4. Guarded to non-forum for the channel delete/create; forum-mode promote is out of scope.

`convert_to_type` (puzzle-keyed) stays for the bang path and the bare-feeder promote-in-place; it
**also** gains the Group teardown so `!convert_to_group` is fixed. The shared teardown/promote
helpers avoid drift between the two seams.

### Web routes

- **New** `POST /api/tag_groups/<id>/change-type`, body `{"type": "<Metapuzzle|Capstone|Runaround|Group>"}`.
  Group-keyed (mirrors `delete_tag_group` / `rename_tag_group`): `_visible_hunt_for_tag_group`,
  `require_organiser`, `puzzles_lock` over members, calls `convert_tag_group_type`, audits
  `change_group_type`, refreshes unassigned, returns `{ok, summary, sheet_warning?}`.
  Organiser-gated to match delete/rename (not admin — the puzzle-keyed convert bang commands
  need Manage-Channels, but the group-menu equivalents follow the group-lifecycle gate).
- **Reuse** `DELETE /api/tag_groups/<id>` (`delete_tag_group`, `keep_puzzles` body) for delete group.
- **Reuse** `POST /api/puzzles/<id>/convert-to-feeder` (+ its `convert-to-feeder-preview`) for
  "delete group, keep target as feeder", resolving the group's `target_puzzle_id` on the frontend.

### Board payload additions

`bot/web/data.py build_board`: add to each group dict
- `group_type` — from `group_info[tag_group_id]["type"]`
- `target_puzzle_id` — from `group_info[...]` (0 when bare)

so the frontend kebab knows the current type (to omit it) and whether the group is targeted.

---

## Frontend (Arrange view — `DragBoard.tsx`)

### `types.ts`
Add to `BoardGroup`: `group_type: string | null;` and `target_puzzle_id: number | null;`.

### `api.ts`
Add `useChangeGroupType(uid)` → `POST /tag_groups/${id}/change-type`. Existing hooks reused:
`useDeleteRound`, `useConvertToFeeder`, `useConvertToFeederPreview`.

### `Column` header kebab
Add an `IconDotsVertical` `Menu` to the column header (next to the name / count). Contents depend on
`group.group_type` / `target_puzzle_id`:

- **Change type →** submenu of {Metapuzzle, Capstone, Runaround, Group} minus the current type.
  - `→ Group` on a targeted group and `→ targeted` on a bare Group each open a confirm describing
    the teardown / meta creation before firing `useChangeGroupType`.
- **Convert to feeder** *(targeted only)* — confirm (with orphaned-feeder count from
  `useConvertToFeederPreview`), then `useConvertToFeeder(target_puzzle_id)`.
- **Delete group…** — opens the existing `TypedConfirmModal` delete-round flow (keep / delete-all
  puzzles), `useDeleteRound`. Available for empty groups too. The Unassigned column
  (`group_code == null`) shows no kebab.

### Target-puzzle card (`PuzzleCard` in `DragBoard`)
When `puzzle.is_target`, render only **"Add to group…"**. Remove the convert-to-\*, convert-to-feeder,
and delete-puzzle items (and the now-unused `onConvert*` / `onDeletePuzzle` props on that branch).
Non-target feeder cards are unchanged.

---

## Error handling

- Sheet failures during teardown/promote are non-fatal warnings (mirrors `solve`/`delete_round`):
  the DB mutation commits; the response carries `sheet_warning`.
- Discord rate-limited renames go through `safe_rename`'s deferred-rename queue (no user-facing
  failure).
- `convert_tag_group_type` raises `RuntimeError("<code>")` for recoverable problems (e.g. name
  clash creating a meta); the route maps it to a 400.
- Forum layout: channel repurpose/create steps are guarded to `hunt.layout_mode != "forum"` and are
  best-effort no-ops otherwise.

## Testing

- **`tests/test_convert_service.py`** (extend): converting a targeted group → Group deletes the
  target puzzle (`PuzzleJsonDb.delete` with the target id), deletes its sheet
  (`gsheet.delete_puzzle_spreadsheet`), clears `target_puzzle_id`, and refreshes the group pin.
  Non-Group conversions do **not** delete the target (regression guard). Bare Group → Metapuzzle
  creates a meta (`create_puzzle` awaited) and sets `target_puzzle_id`.
- **New `convert_tag_group_type` unit tests** mirroring the dispatch table, with DAOs patched at
  the `puzzle_service` import site and Discord mocked by hand (per `AGENTS.md` harness rules).
- **`notifier.refresh_group_info`**: added to `Notifier`/`NullNotifier`/`DiscordNotifier`/
  `CompositeNotifier`; `DiscordNotifier` refreshes via
  `send_initial_group_channel_messages(update=True)`.
- Frontend: manual verification in the Arrange view (kebab actions on targeted / bare / empty
  groups; target card shows only Add-to).

## Rollback / recovery

Backend changes are additive (new seam + route + payload fields); the bang-path fix is contained in
`convert_to_type`. Frontend changes are contained to `DragBoard.tsx`, `api.ts`, `types.ts`. No
migration. Revert is by reverting the commits.

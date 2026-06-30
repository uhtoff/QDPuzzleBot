# Rename Group — Design

Date: 2026-06-30
Status: approved (brainstorming) — ready for implementation plan
Branch: `feature/rename-group` (off `staging`)

## Goal

Let organisers rename a **group** (a `TagGroupData` / `tag_group` row — round / meta /
capstone / runaround / metaless Group) from both surfaces:

- **Discord:** `!rename_group <new name>`, run from inside one of the group's own channels.
- **Web:** an inline edit affordance on the group/column header in the **Arrange** tab.

This closes a real gap: today `!rename_puzzle` renames the *target meta puzzle* but **does not**
touch `tag_group.name`, and the board label is `tag_group.name` (`bot/web/data.py:239`). There is
currently no way to rename a group at all.

## Scope decision: "group only"

Renaming a group changes **only the group**, never the target puzzle:

- Update `tag_group.name` (DB row).
- Rename the group's Discord **container channel(s)** (see "What gets renamed" below).
- The **target puzzle** — its display name, its Discord channel, and its Google Sheet tab — is
  **never touched**. Group name and target-meta name remain independently editable.
- `group_code` is unchanged (only the display name changes).
- No Google Sheet writes (a group has no sheet tab of its own; the target tab is left alone).

## What gets renamed, by layout

Forum layout is explicitly **out of scope** — the forum-based layout is being left to go stale
(project decision, 2026-06-30). Design and test only the **category-based** layouts below.

A group's `category_id` is its "home container" (`bot/store/tag_group_data.py:27-38`). The Discord
objects a group owns differ by type:

- **Meta group** (has a target puzzle — `has_target == True`): owns the cloned **category**, and
  inside it the **target puzzle's own channel**. There is *no* separate "group channel".
  - Rename: the **category** (and, for split rounds, every owned category — see below).
  - Leave the target puzzle's channel/name/sheet tab **untouched**.
- **Regular / metaless group** (`type == "Group"`, no target): owns BOTH a cloned **category** AND
  a dedicated **group text channel** (`group_text_channel`), both named after the group
  (`bot/core/puzzle_service.py:1691-1700`).
  - Rename: the **category** AND the **group text channel**.

### Split-round categories

A round can own more than one category: the primary (unsolved, seq 0) category plus a **solved**
category and any **overflow** categories, recorded in `tag_group_category`
(`bot/core/category_layout.py`). A correct rename must re-derive **all** owned category names from
the new base, using the existing helpers `solved_category_name` / `overflow_category_name`
(`category_layout.py:30-38`). Renaming only the primary category would leave the solved/overflow
categories with the old base name.

## Architecture

One shared service method, called by both surfaces (command-parity principle). Mirrors the existing
`rename_puzzle` / `delete_round` seams.

### 1. Service — `PuzzleService.rename_group(guild, hunt, tag_group, name)`

New method in `bot/core/puzzle_service.py`, modelled on `rename_puzzle`
(`puzzle_service.py:1357-1391`) and decorated with `@pushes_board()` for live dashboard refresh.

Steps:

1. `tag_group.name = name`; `TagGroupJsonDb.commit(tag_group)`.
2. If `guild is not None` (web/test paths pass `guild=None` and skip the Discord write, exactly as
   `rename_puzzle` does):
   - Rename **all owned categories** re-derived from the new base name (primary + solved +
     overflow), via a new `category_layout` helper (see below), using the rate-limit-safe
     `safe_rename` (`puzzle_service.py:178-225`).
   - For a **metaless group only**, also `safe_rename` the dedicated **group text channel**.
   - The target puzzle channel is **not** touched.
3. `@pushes_board()` pushes the updated board.

Factor the container-renaming into a small, testable helper in `bot/core/category_layout.py`, e.g.
`rename_group_containers(guild, tag_group, new_base)`, that:
- renames the primary category + solved + overflow categories derived from `new_base`;
- renames the group text channel when the group is metaless;
- is a no-op for layouts with no category (forum / in-place) — i.e. degrades safely rather than
  erroring, even though forum is out of scope for testing.

### 2. Discord — `!rename_group <new name>` (cog command)

New hybrid command in `bot/cogs/puzzles.py`, modelled on `rename_puzzle`
(`puzzles.py:2295-2318`) and `delete_round` (`puzzles.py:2970-2997`):

- `@commands.has_any_role(*ORGANISER_ROLES)`, `@with_puzzle_mutex(wait=True)`.
- Suggested alias: `rename_round` (and/or `rename_metapuzzle`) for discoverability.
- **Dual context resolution** — find the group from the channel the command is run in:
  1. If `get_channel_type(ctx) == "Group"` (a regular group's main text channel — already resolved
     by `cog_before_invoke`, `puzzles.py:158-170`): use `tag_group = self.get_tag_group(ctx)`.
  2. Else if the current channel is a group's **target puzzle** — i.e. there is a group whose
     `target_puzzle_id == self.get_puzzle(ctx).id` (look up via
     `TagGroupJsonDb.get_by_attr(target_puzzle_id=...)` / equivalent): use that group.
  3. Else: reply with a clear error, e.g. ":x: Run this from a group's target-puzzle channel, or
     from a regular group's channel."
- On success, send a confirmation mirroring `rename_puzzle`'s
  (":white_check_mark: I've renamed the group …").
- Calls `await self.service.rename_group(ctx.guild, self.get_hunt(ctx), tag_group, new_name)`.

### 3. Web — `POST /api/tag_groups/<tag_group_id>/rename`

New endpoint in `bot/web/actions.py`, modelled on `delete_tag_group`
(`actions.py:1244-1284`) and `rename_puzzle` (`actions.py:544-595`):

- `@login_required`; gate with `require_organiser(...)`; 403 on archived hunt.
- Resolve hunt via `_visible_hunt_for_tag_group(tag_group_id)`; load the group via
  `TagGroupJsonDb.get_by_attr(id=...)`.
- Validate `name`: reject empty (`400 empty_name`).
- Resolve `guild` from the hunt's guild snowflake (so the Discord category/channel is actually
  renamed), then call the **same** `cog.service.rename_group(guild, hunt_obj, tag_group, new_name)`.
- `_audit("rename_group", tag_group_id=..., hunt_uid=...)`.
- Works for **all** group types, including metaless groups.

### 4. Frontend — inline editor in Arrange

In `frontend/src/components/DragBoard.tsx`, add a click-to-edit affordance on the `Column` header
(`DragBoard.tsx:346-360`, where `group.name` and the `group_code` badge render). Template: the
`PuzzleDrawer` title editor (`PuzzleDrawer.tsx:117-151`) — a `TextInput` that commits on Enter /
blur and reverts on Escape.

- New React-Query hook `useRenameGroup` in `frontend/src/api.ts`, mirroring `useRenamePuzzle`
  (`api.ts:204-213`) and `useDeleteRound` (`api.ts:434-441`); POSTs to the new endpoint and
  invalidates the board query.
- Gated by `can_manage` (the Arrange tab already is — `HuntBoard.tsx:112`).
- Available on **all** group columns, including metaless groups (the web's reach advantage).

## Duplicate names

`rename_puzzle` rejects duplicate puzzle names within a hunt
(`PuzzleJsonDb.check_duplicates_in_hunt`). For groups:

- **Default:** apply an analogous soft check **if** a suitable `tag_group`-level helper exists;
  otherwise **allow** duplicates. Group resolution keys on the unique `group_code`, so duplicate
  display names are not fatal (only mildly ambiguous in `resolve_group`'s name-matching path).
- Implementation should confirm whether such a helper exists and choose accordingly; do not add a
  new schema constraint.

## Error handling

- Empty / whitespace-only name: rejected (Discord: error reply; web: `400 empty_name`).
- Permission: organiser-gated on both surfaces; web also 403s on archived hunt.
- Discord rename rate limits: handled by the existing `safe_rename` coalescing path — no new logic.
- Non-category layouts (forum / in-place target == home): the container helper degrades to a no-op
  for the category step rather than erroring (still updates `tag_group.name` + pushes the board).

## Testing

- **Service:** unit tests for `rename_group` with `guild=None`:
  - metaless group → `tag_group.name` updated, board push fired.
  - meta group → `tag_group.name` updated; target puzzle row (name/channel/tab) **unchanged**.
- **`category_layout.rename_group_containers`:** with a fake/mock guild, assert primary + solved +
  overflow categories are renamed from the new base, and the group text channel is renamed for
  metaless groups only; target puzzle channel is never renamed.
- **Discord command:** resolution tests for (a) run in a target-puzzle channel, (b) run in a
  regular group's channel, (c) run somewhere invalid → error; organiser gating.
- **Web endpoint:** organiser gate, empty-name 400, archived-hunt 403, success path calls
  `service.rename_group` and audits; metaless-group success path.
- **Frontend:** the inline editor commits via `useRenameGroup` and reverts on Escape (follow the
  existing PuzzleDrawer-title test pattern if one exists).

## Out of scope

- Renaming the target meta puzzle (use the existing `!rename_puzzle` / web puzzle rename).
- Renaming via forum-layout containers (forum is going stale).
- Any change to `group_code`, edges, or sheet-tab names.
- Bulk / multi-group rename.

## Acceptance criteria

1. `!rename_group <name>` run in a meta group's **target puzzle** channel renames the group's
   category(s) and `tag_group.name`; the target puzzle is untouched; the board updates.
2. `!rename_group <name>` run in a **regular group's** text channel renames that group's category
   and its group text channel and `tag_group.name`.
3. `!rename_group` run in an unrelated channel returns a helpful error.
4. The Arrange tab shows an inline editor on every group column; editing renames the group via the
   new endpoint and the board reflects it live.
5. Split rounds (solved/overflow categories) are all renamed from the new base.
6. All new tests pass; existing tests unaffected.

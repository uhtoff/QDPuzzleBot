# Group-type consolidation — design

Addresses code-review finding #1: multiple divergent hard-coded "group type" lists that drift,
causing `delete_round`, channel settings, `!list_puzzles`, and `!info` to mishandle the newer group
types (`Capstone`, `Runaround`, `Group`).

## Goal

Collapse the set of stored `tag_group.type` values to a single canonical set, and route every
"is this a group channel / which group type is this" decision through one source of truth so the
lists can no longer drift.

Stored `tag_group.type` reduces from six values to **four**:

`Metapuzzle`, `Capstone`, `Runaround`, `Group`

`Round` and `Metaless Round` are eliminated as stored types.

## Decisions (locked with user)

1. **`Round` → alias of `Metapuzzle`** (no substantive behavioural difference). `Round` is accepted
   only as a create-time *input label* (web dialogue + bang `!r`/`!round`); it is stored as
   `Metapuzzle` and never appears downstream.
2. **Keep the `" - meta"` meta-name suffix** for the `Round` input: creating via `Round` names the
   meta puzzle `"<name> - meta"` (a bare `Metapuzzle` keeps the plain name). The suffix is decoupled
   from the stored type.
3. **`Metaless Round` removed entirely** — including the `!roundnometa` / `!rnm` command aliases.
   `!group` already creates a no-meta group, so no capability is lost.
4. **Welcome wording:** a `Round`-labelled channel (stored `Metapuzzle`) uses the existing
   *metapuzzle* welcome. The now-dead `send_initial_round_channel_messages` builder is removed. This
   also fixes a latent bug where `Capstone`/`Runaround` hit `!info`'s else-branch.
5. **Data migration** converts existing rows; **migration-only**, no permanent legacy-value
   normaliser in code (migrations are mandatory via `migrate.py`).

## Out of scope

- Code-review findings #2–#7 and doc-quality notes (handed off to working-2 in
  `.ai/active/code-review-remaining-findings.md`).
- Web visibility-helper boilerplate (#4).
- Any change to `Capstone` / `Runaround` / `Group` *behaviour* — only their routing through the
  canonical list and `!info` welcome.

## Canonical source of truth

In `bot/core/puzzle_service.py`, next to `TYPE_DEFAULTS`:

```python
# Every stored tag_group.type value (== TYPE_DEFAULTS keys once "Round" is dropped).
GROUP_TYPES = ("Metapuzzle", "Capstone", "Runaround", "Group")

# Create-time input labels that are not stored types; map label -> stored type.
GROUP_TYPE_INPUT_ALIASES = {"Round": "Metapuzzle"}
```

`TYPE_DEFAULTS` loses its `"Round"` key (it duplicated `"Metapuzzle"`). After that,
`GROUP_TYPES == tuple(TYPE_DEFAULTS)` — keep them consistent (derive or assert).

## Changes by file

### `bot/core/puzzle_service.py`

- Add `GROUP_TYPES` + `GROUP_TYPE_INPUT_ALIASES`; remove the `"Round"` entry from `TYPE_DEFAULTS`
  (lines 87–91).
- `is_group` (line 1596): `round_type in ("Group", "Metaless Round")` → `round_type == "Group"`.
- Confirm `default_edge_for_group` still behaves (it uses `.get(type, {}).get("default_edge",
  "feeds")`, so a removed `"Round"` key is harmless — but no stored row will be `Round` post-migration
  anyway).
- No convert-to-`Round` path exists (convert targets are Metapuzzle/Capstone/Runaround/Group), so
  dropping the `TYPE_DEFAULTS["Round"]` key is safe.

### `bot/cogs/puzzles.py`

- **Delete** `PUZZLE_GROUPS` (line 83). Import `GROUP_TYPES` from `puzzle_service`.
- Route through `GROUP_TYPES`:
  - `get_settings` / `save_settings` (1739, 1750)
  - `delete_round` guard (2963)
  - `sync` guard (1254–1255) — was already complete; switch to the constant
  - `list_puzzles` guard (1901) — was `["Round","Puzzle","Metaless Round","Metapuzzle"]`; becomes
    `(*GROUP_TYPES, "Puzzle")` (it intentionally also accepts puzzle channels)
- `command_map` (1492–1502):
  - `"r"`, `"round"` → `("Metapuzzle", True, " - meta")` (type changes to `Metapuzzle`, suffix kept)
  - **remove** `"roundnometa"` and `"rnm"` entries
- `!info` dispatch (2258–2264): collapse to
  - `Hunt` → hunt welcome
  - `Metapuzzle` | `Capstone` | `Runaround` → `send_initial_metapuzzle_channel_messages`
  - `Group` → `send_initial_group_channel_messages`
  - `Puzzle` → puzzle welcome
  This removes the `Round` and `Metaless Round` branches and **adds** `Capstone`/`Runaround`
  (previously fell through to the error else-branch).
- **Remove** `send_initial_round_channel_messages` (2148–2187) — dead after the dispatch change.
- Remove `"Metaless Round"` everywhere it remains as a string literal.
- Drop the unused `TagGroupData` import (line 15) while editing this import block — incidentally
  clears finding #6 (lint F401).

### `bot/web/actions.py`

- `create_meta` route (≈1503–1530):
  - Accept input that is in `TYPE_DEFAULTS` **or** in `GROUP_TYPE_INPUT_ALIASES` (currently rejects
    anything not in `TYPE_DEFAULTS`, line 1505).
  - `stored_type = GROUP_TYPE_INPUT_ALIASES.get(group_type, group_type)`.
  - Meta-name suffix keyed on the *incoming label*: `Group` → `meta_puzzle_name=False`; `Round` →
    `name + " - meta"`; else `name`. (Same observable behaviour as today.)
  - Pass `round_type=stored_type` to `create_tag_group`.
- Verify the `Runaround` post-create `set_source_mode` (1534) is unaffected.

### `frontend/src/components/CreateControls.tsx` + `frontend/src/api.ts`

- **No change required.** The dialogue already offers all five labels and defaults to `Round`
  (`CreateControls.tsx:13–18,38`); `MetaType` (`api.ts:306`) keeps `"Round"` as a valid *input*
  value. The backend now translates `Round` → stored `Metapuzzle`.

### Migration (`migrations/NNN_*.sql`)

```sql
UPDATE tag_group SET type='Metapuzzle' WHERE type='Round';
UPDATE tag_group SET type='Group'      WHERE type='Metaless Round';
```

- Use the next sequential number (currently `016`); **verify no collision** with parallel worktrees
  before merge (known gotcha).
- **Verify storage backend before relying on SQL alone:** confirm MySQL (`tag_group` table) is the
  live store and that no JSON-file store (`TagGroupJsonDb`, cf. `$LADDER_SPOT_DATA_DIR` in the README)
  holds live `Round` / `Metaless Round` rows. If a JSON store is live, migrate those rows too.

## Tests

- **Regression (the finding):** `delete_round` and `show_settings`/`update_settings` succeed in
  `Capstone`, `Runaround`, and `Group` channels (previously rejected/misrouted).
- **`list_puzzles`** lists puzzles in `Capstone`/`Runaround`/`Group` channels (previously bounced to
  the website).
- **`!info`** routes `Capstone`/`Runaround` to the metapuzzle welcome (previously the error
  else-branch).
- **`Round` alias:** bang `!r`/`!round` **and** the web create route both persist `type="Metapuzzle"`
  and a meta puzzle named `"<name> - meta"`.
- **Metaless retired:** `!roundnometa` / `!rnm` are no longer registered commands; `!group` still
  creates a no-meta `Group`.
- **Migration:** a row seeded as `Round` becomes `Metapuzzle`; `Metaless Round` becomes `Group`.
- **Update existing tests** that assert `Round` / `Metaless Round` as stored types
  (`tests/test_group_channel_type.py:94,111,141`, `tests/test_forum_layout.py`). These overlap with
  code-review finding #5 (stale "FAIL until Phase 2" comments) — clean those up here too while in the
  files.

## Risks / notes

- **`is_target` / forum-tag derivation:** `Capstone`/`Runaround` keep their own behaviour; only their
  *routing* through `GROUP_TYPES` and `!info` changes. Re-run forum/board tests
  (`test_forum_tags.py`, `test_build_board.py`, `test_meta_kind.py`) to confirm no regression.
- **Welcome wording trade-off:** Round-labelled channels now read "meta channel" in their pinned
  message. Accepted (decision #4). Revisit only if users object.
- **No permanent normaliser:** relies on the migration running. Acceptable because migrations are
  mandatory; the alternative (a store-read normaliser) was rejected to keep downstream code free of
  the legacy strings.

## Sequencing

1. Add canonical constants in `puzzle_service.py`; drop `TYPE_DEFAULTS["Round"]`; simplify `is_group`.
2. Repoint all `puzzles.py` lists/branches at `GROUP_TYPES`; fix `command_map`; collapse `!info`;
   remove dead round welcome builder + unused import.
3. Update `actions.py` create route for the input-alias mapping.
4. Add the SQL migration (verify number + backend).
5. Update/extend tests; clean stale Phase-2 comments in the touched test files.
6. Full focused test run (timeout-wrapped, compact), then commit.

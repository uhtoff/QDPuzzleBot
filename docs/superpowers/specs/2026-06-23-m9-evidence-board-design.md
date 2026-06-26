# M9 — Evidence Board Design

**Date:** 2026-06-23
**Status:** Approved for implementation

---

## Goal

Let solvers log freeform clues against any puzzle channel during a hunt, accumulate them in a
hunt-wide evidence board (Google Sheet tab + web dashboard panel), and later route them to a target
puzzle via the web UI. Covers the MonArch "PuzzMonDex entries" workflow, the Detectives "suspect
info" workflow, and the mini-meta "given list of answers" assignment workflow — all with the same
data model and tooling.

---

## In scope

- `evidence_items` DB table (new migration `009_evidence.sql`)
- `!clue "text"` bang command — freeform clue, source puzzle auto-stamped
- `!clues ITEM1 ITEM2 "MULTI WORD"` bang command — batch add, shell-style tokenising via `shlex.split`
- Hunt-wide Evidence sheet tab (auto-created on first `!clue` per hunt, rendered by `aggregate_to_tab`)
- Web dashboard Evidence panel — list, filter, inline target-puzzle assignment
- API: `GET /api/evidence`, `POST /api/evidence`, `PATCH /api/evidence/<id>`

## Out of scope (M9b and beyond)

- Labels / categories on evidence items
- Structured clue fields (suspect / attribute / value Detectives template)
- Completeness queries ("solved puzzles with no clue logged")
- `reveals` edge type on `tags` table
- Auto-solve behaviour of any kind (no puzzle is auto-solved today — capstones are not unique in this respect, so there is no capstone-specific guard to add)
- Meta-matching / orphaned-answers pool (separate feature)
- Per-capstone evidence boards (can be done by filtering on `target_puzzle_id` in the dashboard)

---

## Data model

### New table: `evidence_items`

```sql
CREATE TABLE `evidence_items` (
  `id`               INT(11)      NOT NULL AUTO_INCREMENT,
  `hunt_id`          INT(11)      NOT NULL,
  `source_puzzle_id` INT(11)      NOT NULL,
  `target_puzzle_id` INT(11)      DEFAULT NULL,
  `note`             TEXT         NOT NULL,
  `created_at`       DATETIME     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_evidence_hunt`   (`hunt_id`),
  KEY `idx_evidence_source` (`source_puzzle_id`),
  KEY `idx_evidence_target` (`target_puzzle_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`source_puzzle_id` — the puzzle channel where `!clue` was typed. Always set; never updated.
`target_puzzle_id` — any puzzle in the hunt the clue is routed to. Nullable; set/changed via web.

No foreign-key cascades: puzzle deletes are rare and a dangling clue is better than silent data loss.

### Schema addition: `hunts.evidence_sheet_page_id`

```sql
ALTER TABLE `hunts`
  ADD COLUMN `evidence_sheet_page_id` INT(11) DEFAULT NULL;
```

Stores the Google Sheet page id of the Evidence tab once created. NULL until the first `!clue` in
that hunt fires the lazy-create path.

---

## Bang commands

### `!clue "text"`

Logs one evidence item for the current puzzle.

```
!clue "Suspect 3's favourite cookie is Bourbon"
!clue PuzzMon entry: Rattata / Normal / Speed 65
```

Unquoted text after `!clue` is joined as-is (no shell splitting — the whole remainder is the note).
Replies with a short confirmation: `:pencil: Clue logged from **{puzzle.name}**.`

### `!clues ITEM1 ITEM2 "MULTI WORD ITEM" ITEM4`

Batch-adds a list of evidence items, all with the same source puzzle. Uses `shlex.split()` for
tokenising, so unquoted tokens split on whitespace and quoted strings (single or double) are treated
as one item.

```
!clues APPLE BANANA "CHERRY BOMB" DURIAN
```

→ four items, source = current puzzle.

Replies: `:pencil: 4 clues logged from **{puzzle.name}**.`

Both commands write to the DB synchronously (fast), then fire-and-forget a sheet refresh (same
pattern as meta roll-ups).

---

## Google Sheet

### Evidence tab

- Created lazily on the first `!clue` in a hunt via a new `gsheet_cog.create_blank_tab(spreadsheet_id, name)` method (plain `addSheet` Sheets API request — not `add_new_sheet`, which duplicates a puzzle template). Page id returned and stored in `hunt.evidence_sheet_page_id`.
- Rendered by `aggregate_to_tab()` — same engine as meta roll-ups.
- Columns: `Source Puzzle | Note | Target Puzzle | Logged At`
- `Target Puzzle` is blank until `target_puzzle_id` is set; updated on the next sheet refresh.
- Refreshes asynchronously after any `!clue` / `!clues` call and after any target assignment via the web.
- Tab lives in the hunt's **live** spreadsheet (not the archive). If the hunt is archived, clues are still readable but no further writes are made.

### Refresh path

New `PuzzleService.refresh_evidence(hunt)` method:
1. Fetches all `evidence_items` for the hunt (ordered by `created_at`).
2. Resolves puzzle names for `source_puzzle_id` and `target_puzzle_id`.
3. Calls `aggregate_to_tab(hunt.google_sheet_id, hunt.evidence_sheet_page_id, columns, rows)`.

Called from the bang edge (fire-and-forget) and from the PATCH web handler.

---

## Web dashboard

### Evidence panel

Location: new tab or collapsible panel on the hunt page (alongside Puzzles, Rounds, etc.).

Columns: Source Puzzle | Note | Target Puzzle | Logged At

- **Filter bar:** free-text search over Note; dropdowns for Source Puzzle and Target Puzzle
  (including "Unassigned" to find clues with no target).
- **Inline target assignment:** clicking the Target Puzzle cell opens a searchable puzzle dropdown
  (all puzzles in the hunt). Selecting one PATCHes `target_puzzle_id` and refreshes the row.
  Setting it blank clears the assignment.
- **Bulk assign:** checkbox column; select N rows → "Assign to…" dropdown above the table. Useful
  for the Detectives step where you're routing a batch of suspect clues at once.
- **Batch paste:** "Add clues" button opens a modal with a textarea (one item per line). Submits as
  `POST /api/evidence` with `source_puzzle_id` picked from a dropdown in the modal. Good for
  pasting a given list of mini-meta answers without typing `!clues` in Discord.

---

## API

### `GET /api/evidence`

Query params: `hunt_id` (required), `source_puzzle_id` (optional), `target_puzzle_id` (optional,
`"unassigned"` to filter nulls).

Returns list of items:
```json
[{
  "id": 42,
  "source_puzzle_id": 7,
  "source_puzzle_name": "Rattata's Revenge",
  "target_puzzle_id": null,
  "target_puzzle_name": null,
  "note": "PuzzMon entry: Rattata / Normal / Speed 65",
  "created_at": "2026-06-23T14:32:00Z"
}]
```

### `POST /api/evidence`

Body: `{ hunt_id, source_puzzle_id, notes: ["item1", "item2", ...] }`

Accepts a list so the batch-paste web modal and `!clues` both share one endpoint. Fires sheet
refresh after insert. Returns created items.

### `PATCH /api/evidence/<id>`

Body: `{ target_puzzle_id: <int|null> }` — set or clear the target. Fires sheet refresh after
update. Returns updated item.

---

## Service layer

New `PuzzleService` methods:

```python
def log_clues(self, hunt, puzzle, notes: list[str]) -> list[EvidenceData]
    """Insert one evidence_item per note. Returns created items."""

async def refresh_evidence(self, hunt)
    """Render the Evidence sheet tab via aggregate_to_tab. Creates the tab lazily."""
```

`log_clues` is sync (DB only). `refresh_evidence` is async (Sheets IO). The bang edge calls
`log_clues` then `asyncio.create_task(refresh_evidence(...))` (fire-and-forget, matching the
`board_changed` pattern).

---

## New store

`EvidenceJsonDb` (MySQL-backed, following existing store conventions):

- `log(hunt_id, source_puzzle_id, note) -> EvidenceData`
- `get_all(hunt_id, source_puzzle_id=None, target_puzzle_id=None) -> list[EvidenceData]`
- `set_target(id, target_puzzle_id) -> EvidenceData`

`EvidenceData` dataclass: `id`, `hunt_id`, `source_puzzle_id`, `target_puzzle_id`, `note`,
`created_at`.

---

## Architecture notes

- No per-puzzle mutex needed for clue logging (appends only; no read-modify-write).
- The web PATCH (target assignment) uses a lightweight row-level update; no hunt-wide lock needed.
- Sheet refresh is best-effort — a Sheets failure does not roll back the DB write. Same contract
  as meta roll-ups: DB is truth, sheet is a view.
- `mysqldb.py`'s generic `commit()` is **not** used for evidence items — they are append-only with
  a dedicated `log()` writer, consistent with how `web_audit_log` works.

---

## Relevant files

| File | Change |
|---|---|
| `migrations/009_evidence.sql` | New table + `hunts.evidence_sheet_page_id` column |
| `bot/store/evidence_data.py` | `EvidenceData` dataclass + `_EvidenceJsonDb` |
| `bot/store/__init__.py` | Wire `EvidenceJsonDb` |
| `bot/store/mysqldb.py` | `MySQLEvidenceDb` implementation |
| `bot/core/puzzle_service.py` | `log_clues()`, `refresh_evidence()` |
| `bot/cogs/puzzles_gsheet.py` | `create_blank_tab(spreadsheet_id, name)` method |
| `bot/cogs/puzzles.py` | `!clue` and `!clues` bang commands |
| `bot/web/data.py` | Evidence query helper |
| `bot/web/actions.py` | PATCH handler |
| `bot/web/routes.py` | GET + POST + PATCH routes |
| `frontend/src/` | Evidence panel component + API client |

---

## Acceptance criteria

- `!clue "text"` in any puzzle channel logs an item and confirms in Discord.
- `!clues APPLE "CHERRY BOMB" DURIAN` logs three items from the same source puzzle.
- The Evidence tab appears in the hunt spreadsheet after the first clue is logged.
- The Evidence tab refreshes (source, note, target, timestamp) after any clue add or assignment.
- The dashboard Evidence panel lists all clues for the hunt with correct source puzzle names.
- Filtering by "Unassigned" shows only clues with no target.
- Assigning a target via the dashboard updates the DB and triggers a sheet refresh.
- Bulk assign works for multiple selected rows.
- Batch paste via modal adds N items with a chosen source puzzle.
- Clues survive a bot restart (DB-backed).

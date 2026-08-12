# Feeder Pool — Core Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a Minimeta puzzle a pool of given-feeder strings that its Minis (and the Minimeta itself) can claim, with claims confirmed automatically when a mini is solved — all reachable from Discord, with no Google Sheets involvement.

**Architecture:** Two new tables (`feeder_pool_items`, `feeder_claims`) behind two DAOs following the `MySQLEvidenceDb` pattern. All decision logic lives in a new pure module `bot/core/feeder_pool.py` with no Discord/Sheets/DB imports, so the interesting behaviour is unit-testable. Service methods on `PuzzleService` own the writes; the confirm-on-solve hook goes in `service.solve()`/`service.unsolve()`, the seam both the Discord and web edges already call.

**Tech Stack:** Python 3.12, discord.py 2.7.1, MySQL/MariaDB via `mysql-connector-python`, pytest (`asyncio_mode = auto`), ruff (pyflakes only), basedpyright.

**Spec:** `docs/superpowers/specs/2026-08-12-feeder-pool-design.md` — authoritative. Where this plan and the spec disagree, the spec wins.

**Out of scope for Plan 1 (do not build):** any Google Sheets rendering, the `Feeder Pool` / `Feeder Claims` tables, the checkbox column, `!sync_feeders`, provided rows on the contribution table, web routes, the dashboard panel.

---

## Scope note — one addition beyond the spec

The spec's command table has add / list / edit / remove / sync, with claiming done by ticking a sheet checkbox or clicking in the dashboard. Both of those arrive in Plans 2 and 3, which would leave Plan 1 with no way to create a claim and therefore nothing for confirm-on-solve to act on.

**Task 10 therefore adds `!claim_feeder` / `!unclaim_feeder`.** It is a genuine (small) widening of the specified surface. It is also useful on its own — claiming from Discord without opening the sheet — and it is what makes this plan standalone working software.

---

## File structure

| File | Responsibility |
|---|---|
| `migrations/043_feeder_pool.sql` (create) | The two tables and their FK cascades. |
| `bot/store/feeder_pool_data.py` (create) | `FeederPoolItemData`, `FeederClaimData` dataclasses + `import_dict`. |
| `bot/store/mysqldb.py` (modify) | `MySQLFeederPoolDb`, `MySQLFeederClaimDb`. |
| `bot/store/__init__.py` (modify) | Re-export the dataclasses; instantiate `FeederPoolDb`, `FeederClaimDb`. |
| `bot/core/feeder_pool.py` (create) | **Pure.** `claim_state`, `rival_names`, `provided_item_ids`, `diff_ticks`. No imports from `bot.store`, `discord`, or the Sheets cog. |
| `bot/core/puzzle_service.py` (modify) | Pool CRUD, claim/unclaim, and the confirm-on-solve hook. |
| `bot/cogs/puzzles.py` (modify) | The six bang commands. |
| `tests/test_feeder_pool_pure.py` (create) | Unit tests for the pure module. |
| `tests/test_feeder_pool_service.py` (create) | Service tests, DAOs mocked. |
| `tests/test_feeder_pool_confirm.py` (create) | Confirm-on-solve through both edges. |
| `tests/test_feeder_pool_cascade.py` (create) | **Live-DB**: FK cascades against `puzzlebot_test`. |
| `tests/test_command_gating.py` (modify) | Tier assertions for the six new commands. |

`bot/core/feeder_pool.py` stays pure and duck-typed — it accepts any object with `.item_id`, `.target_id`, `.confirmed`, never importing the dataclasses. This matches `bot/core/unassigned.py` and `bot/core/contribution.py`, and it is what keeps `bot/store/__init__.py` (which opens a MySQL connection on first use) out of the unit tests.

---

## Task 1: Migration 043

**Files:**
- Create: `migrations/043_feeder_pool.sql`

`043` was verified free across every live worktree (`matrix-spike`, `working-1/2/3` are all behind) — but re-check before committing, because a parallel session can claim it and git will merge the collision cleanly without noticing.

- [ ] **Step 1: Re-check the number is still free**

```bash
ls migrations/ | tail -3
for w in .claude/worktrees/*/; do echo "$w: $(ls $w/migrations/ 2>/dev/null | tail -1)"; done
```

Expected: nothing named `043_*` anywhere. If there is, renumber this migration to the next free number and use that number consistently for the rest of the plan.

- [ ] **Step 2: Write the migration**

Create `migrations/043_feeder_pool.sql`:

```sql
-- Feeder pool: the given-feeder list for a meta matching puzzle.
--
-- A Minimeta owns a pool of finished answer strings the puzzle handed the solvers.
-- Its Minis (and the Minimeta itself) CLAIM items out of the pool; a claim becomes
-- confirmed when the claiming mini is solved. See
-- docs/superpowers/specs/2026-08-12-feeder-pool-design.md.
--
-- Both tables are new, so there is no existing data to verify against and no
-- backfill: the pre-flight check other migrations carry does not apply here.
--
-- Down: DROP TABLE feeder_claims; DROP TABLE feeder_pool_items;

CREATE TABLE `feeder_pool_items` (
  `id`                 int(11) NOT NULL AUTO_INCREMENT,
  `minimeta_puzzle_id` int(11) NOT NULL,
  `text`               varchar(255) NOT NULL,
  `note`               text DEFAULT NULL,
  `position`           int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_feeder_pool_items_text` (`minimeta_puzzle_id`, `text`),
  CONSTRAINT `FeederPoolMinimetaID` FOREIGN KEY (`minimeta_puzzle_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `feeder_claims` (
  `id`        int(11) NOT NULL AUTO_INCREMENT,
  `item_id`   int(11) NOT NULL,
  `target_id` int(11) NOT NULL,
  `confirmed` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_feeder_claims_item_target` (`item_id`, `target_id`),
  KEY `ix_feeder_claims_target` (`target_id`),
  CONSTRAINT `FeederClaimItemID` FOREIGN KEY (`item_id`)
      REFERENCES `feeder_pool_items` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `FeederClaimTargetID` FOREIGN KEY (`target_id`)
      REFERENCES `puzzles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`int(11)` is deliberate on both FK columns — it must match `puzzles.id` exactly or the FK fails with errno 150 ("incorrectly formed"), which is the trap migration 008 documents.

- [ ] **Step 3: Apply it to the test database**

```bash
timeout 120 pipenv run python migrate.py migrate > /tmp/migrate.log 2>&1
```

Expected: exit 0. Read `/tmp/migrate.log` and confirm it names `043_feeder_pool.sql` as applied.

- [ ] **Step 4: Confirm the tables are really there**

```bash
timeout 60 pipenv run python -c "
from bot.store import mydb
c = mydb.dict_cursor()
c.execute('SHOW TABLES LIKE \"feeder_%\"')
print(c.fetchall())
c.close()"
```

Expected: both `feeder_pool_items` and `feeder_claims` listed.

- [ ] **Step 5: Commit**

```bash
git add migrations/043_feeder_pool.sql
git commit -m "feat(db): feeder pool tables for meta matching puzzles"
```

The house rule is **migration → deploy → code**: this commit ships alone, before any code reads the tables, because the generic `commit()` in `mysqldb.py` writes every dataclass field by name and would crash against a database that has not been migrated.

---

## Task 2: Live-DB cascade test

The FK cascades are exactly the class of behaviour a mock cannot verify, so this follows the live-DB convention documented in `AGENTS.md` — real rows in `puzzlebot_test`, torn down in `finally`.

**Files:**
- Create: `tests/test_feeder_pool_cascade.py`

- [ ] **Step 1: Write the failing test**

```python
"""Live-DB: feeder pool FK cascades. Inserts real rows into puzzlebot_test.

Follows the convention in AGENTS.md — `mydb` for a cursor, `live_hunt_id()` for a
real hunts.id to FK against, teardown in `finally`.
"""
from bot.store import mydb
from tests.conftest import live_hunt_id


def _mk_puzzle(cursor, hunt_id, name):
    cursor.execute(
        "INSERT INTO puzzles (name, hunt_id, status) VALUES (%s, %s, 'unstarted')",
        (name, hunt_id),
    )
    return cursor.lastrowid


def test_deleting_the_minimeta_cascades_pool_and_claims():
    hunt_id = live_hunt_id()
    cursor = mydb.dict_cursor()
    minimeta_id = mini_id = None
    try:
        minimeta_id = _mk_puzzle(cursor, hunt_id, "cascade-minimeta")
        mini_id = _mk_puzzle(cursor, hunt_id, "cascade-mini")

        cursor.execute(
            "INSERT INTO feeder_pool_items (minimeta_puzzle_id, text, position)"
            " VALUES (%s, 'ALBATROSS', 0)", (minimeta_id,))
        item_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO feeder_claims (item_id, target_id, confirmed)"
            " VALUES (%s, %s, 0)", (item_id, mini_id))

        cursor.execute("DELETE FROM puzzles WHERE id = %s", (minimeta_id,))
        minimeta_id = None

        cursor.execute(
            "SELECT COUNT(*) AS n FROM feeder_pool_items WHERE id = %s", (item_id,))
        assert cursor.fetchone()["n"] == 0, "pool item survived its minimeta"
        cursor.execute(
            "SELECT COUNT(*) AS n FROM feeder_claims WHERE item_id = %s", (item_id,))
        assert cursor.fetchone()["n"] == 0, "claim survived its pool item"
    finally:
        for pid in (minimeta_id, mini_id):
            if pid is not None:
                cursor.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
        cursor.close()


def test_deleting_a_mini_cascades_only_its_own_claims():
    """Two minis, two claims — so a cascade that deleted everything, or deleted by
    the wrong key, is distinguishable from one that deleted the right row."""
    hunt_id = live_hunt_id()
    cursor = mydb.dict_cursor()
    minimeta_id = mini_a = mini_b = None
    try:
        minimeta_id = _mk_puzzle(cursor, hunt_id, "cascade-minimeta-2")
        mini_a = _mk_puzzle(cursor, hunt_id, "cascade-mini-a")
        mini_b = _mk_puzzle(cursor, hunt_id, "cascade-mini-b")

        cursor.execute(
            "INSERT INTO feeder_pool_items (minimeta_puzzle_id, text, position)"
            " VALUES (%s, 'MARZIPAN', 0)", (minimeta_id,))
        item_id = cursor.lastrowid
        cursor.executemany(
            "INSERT INTO feeder_claims (item_id, target_id, confirmed)"
            " VALUES (%s, %s, 0)",
            [(item_id, mini_a), (item_id, mini_b)])

        cursor.execute("DELETE FROM puzzles WHERE id = %s", (mini_a,))
        mini_a = None

        cursor.execute(
            "SELECT target_id FROM feeder_claims WHERE item_id = %s", (item_id,))
        survivors = [r["target_id"] for r in cursor.fetchall()]
        assert survivors == [mini_b], f"expected only mini_b's claim, got {survivors}"

        cursor.execute(
            "SELECT COUNT(*) AS n FROM feeder_pool_items WHERE id = %s", (item_id,))
        assert cursor.fetchone()["n"] == 1, "the pool item should outlive one claimant"
    finally:
        for pid in (minimeta_id, mini_a, mini_b):
            if pid is not None:
                cursor.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
        cursor.close()


def test_the_same_target_cannot_claim_one_item_twice():
    hunt_id = live_hunt_id()
    cursor = mydb.dict_cursor()
    minimeta_id = mini_id = None
    try:
        minimeta_id = _mk_puzzle(cursor, hunt_id, "cascade-minimeta-3")
        mini_id = _mk_puzzle(cursor, hunt_id, "cascade-mini-c")
        cursor.execute(
            "INSERT INTO feeder_pool_items (minimeta_puzzle_id, text, position)"
            " VALUES (%s, 'QUAY', 0)", (minimeta_id,))
        item_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO feeder_claims (item_id, target_id, confirmed)"
            " VALUES (%s, %s, 0)", (item_id, mini_id))

        import mysql.connector
        try:
            cursor.execute(
                "INSERT INTO feeder_claims (item_id, target_id, confirmed)"
                " VALUES (%s, %s, 0)", (item_id, mini_id))
            raise AssertionError("duplicate claim was accepted")
        except mysql.connector.IntegrityError:
            pass
    finally:
        for pid in (minimeta_id, mini_id):
            if pid is not None:
                cursor.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
        cursor.close()
```

- [ ] **Step 2: Run it**

```bash
timeout 120 pipenv run python -m pytest tests/test_feeder_pool_cascade.py -q > /tmp/test.log 2>&1
```

Expected: 3 passed. Read `/tmp/test.log`.

If `live_hunt_id` is not importable from `tests/conftest.py`, or the `puzzles` insert rejects those columns, **stop and read `tests/test_tag_edges.py`** for the exact live-DB shape this codebase uses, and match it rather than inventing a variant.

- [ ] **Step 3: Prove the cascade test can fail**

Temporarily edit `migrations/043_feeder_pool.sql`, changing `FeederClaimItemID`'s `ON DELETE CASCADE` to `ON DELETE RESTRICT`, then:

```bash
timeout 60 pipenv run python -c "
from bot.store import mydb
c = mydb.cursor()
c.execute('DROP TABLE feeder_claims'); c.execute('DROP TABLE feeder_pool_items')
c.close(); mydb.commit()"
timeout 120 pipenv run python migrate.py migrate > /tmp/migrate.log 2>&1
timeout 120 pipenv run python -m pytest tests/test_feeder_pool_cascade.py -q > /tmp/test.log 2>&1
```

Expected: failures, because the delete is now refused. **Restore the file by editing `RESTRICT` back to `CASCADE`** (not `git checkout --`, which would discard the whole file), drop and re-migrate again, and confirm 3 passed.

This is the mutation check — without it the cascade tests could be passing because nothing was ever deleted.

- [ ] **Step 4: Commit**

```bash
git add tests/test_feeder_pool_cascade.py
git commit -m "test(db): live-DB cascade coverage for the feeder pool tables"
```

---

## Task 3: Dataclasses and DAOs

**Files:**
- Create: `bot/store/feeder_pool_data.py`
- Modify: `bot/store/mysqldb.py`
- Modify: `bot/store/__init__.py`

- [ ] **Step 1: Write the dataclasses**

Create `bot/store/feeder_pool_data.py`:

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class FeederPoolItemData:
    """One given feeder — a finished answer string the puzzle handed the solvers.

    It is never a puzzle: it has no channel, no thread and no sheet tab. `text` is
    the identity a solver sees; `id` is the identity the sheets diff on.
    """
    id: int = 0
    minimeta_puzzle_id: int = 0
    text: str = ""
    note: Optional[str] = None
    position: int = 0

    @classmethod
    def import_dict(cls, row: dict):
        item = cls()
        for attr in item.__dict__:
            if attr in row:
                setattr(item, attr, row[attr])
        return item


@dataclass
class FeederClaimData:
    """A claim that `item_id` belongs to `target_id` (a Mini, or the Minimeta itself).

    `confirmed` is set when the claiming mini is solved — solving is the proof. A
    claim is never deleted to resolve a contest: a losing claim is information, and
    deleting it would make unsolve lossy.
    """
    id: int = 0
    item_id: int = 0
    target_id: int = 0
    confirmed: bool = False

    @classmethod
    def import_dict(cls, row: dict):
        claim = cls()
        for attr in claim.__dict__:
            if attr in row:
                setattr(claim, attr, row[attr])
        claim.confirmed = bool(claim.confirmed)   # MySQL hands back 0/1
        return claim
```

The `bool()` coercion matters: `tinyint(1)` reads back as `0`/`1`, and the pure module's `state` derivation compares against real booleans in its tests. Without it a test using `confirmed=True` and production reading `1` would exercise different paths.

- [ ] **Step 2: Write the DAOs**

In `bot/store/mysqldb.py`, after `class MySQLEvidenceDb` (whose shape these follow), add:

```python
class MySQLFeederPoolDb(_MySQLBaseDb):
    TABLE_NAME = 'feeder_pool_items'

    def get_for_minimeta(self, minimeta_puzzle_id: int):
        """Every pool item for a minimeta, in paste order."""
        rows = self._fetch_all(
            "SELECT * FROM feeder_pool_items WHERE minimeta_puzzle_id = %s"
            " ORDER BY position ASC, id ASC",
            (minimeta_puzzle_id,))
        return [FeederPoolItemData.import_dict(r) for r in rows]

    def get_by_id(self, item_id: int):
        row = self._fetch_one(
            "SELECT * FROM feeder_pool_items WHERE id = %s", (item_id,))
        return FeederPoolItemData.import_dict(row) if row else None

    def next_position(self, minimeta_puzzle_id: int) -> int:
        row = self._fetch_one(
            "SELECT COALESCE(MAX(position), -1) AS p FROM feeder_pool_items"
            " WHERE minimeta_puzzle_id = %s", (minimeta_puzzle_id,))
        return int(row["p"]) + 1 if row else 0

    def add_many(self, minimeta_puzzle_id: int, texts):
        """Append `texts` that are not already in the pool. Returns the added ones.

        Re-pasting the same list is therefore harmless — an already-present text is
        skipped rather than duplicated or errored.
        """
        existing = {i.text for i in self.get_for_minimeta(minimeta_puzzle_id)}
        position = self.next_position(minimeta_puzzle_id)
        added = []
        for text in texts:
            if text in existing:
                continue
            existing.add(text)
            item = FeederPoolItemData(
                minimeta_puzzle_id=minimeta_puzzle_id, text=text, position=position)
            self.commit(item)
            added.append(item)
            position += 1
        return added

    def set_text(self, item_id: int, text: str) -> None:
        self._exec(
            "UPDATE feeder_pool_items SET text = %s WHERE id = %s", (text, item_id))

    def set_note(self, item_id: int, note) -> None:
        self._exec(
            "UPDATE feeder_pool_items SET note = %s WHERE id = %s", (note, item_id))

    def delete(self, item_id: int, /) -> None:
        """Delete a pool item. Its claims cascade (migration 043)."""
        self._exec("DELETE FROM feeder_pool_items WHERE id = %s", (item_id,))


class MySQLFeederClaimDb(_MySQLBaseDb):
    TABLE_NAME = 'feeder_claims'

    def get_for_minimeta(self, minimeta_puzzle_id: int):
        """Every claim against every item in one minimeta's pool.

        The pure module derives all its per-item/per-target state from this one
        list, so callers fetch once and pass it around rather than querying per item.
        """
        rows = self._fetch_all(
            "SELECT c.* FROM feeder_claims c"
            " JOIN feeder_pool_items i ON i.id = c.item_id"
            " WHERE i.minimeta_puzzle_id = %s"
            " ORDER BY c.item_id ASC, c.target_id ASC",
            (minimeta_puzzle_id,))
        return [FeederClaimData.import_dict(r) for r in rows]

    def get_for_target(self, target_id: int):
        rows = self._fetch_all(
            "SELECT * FROM feeder_claims WHERE target_id = %s ORDER BY item_id ASC",
            (target_id,))
        return [FeederClaimData.import_dict(r) for r in rows]

    def add(self, item_id: int, target_id: int) -> None:
        """Claim an item for a target. Idempotent — re-claiming is a no-op."""
        self._exec(
            "INSERT IGNORE INTO feeder_claims (item_id, target_id, confirmed)"
            " VALUES (%s, %s, 0)", (item_id, target_id))

    def remove(self, item_id: int, target_id: int) -> None:
        self._exec(
            "DELETE FROM feeder_claims WHERE item_id = %s AND target_id = %s",
            (item_id, target_id))

    def set_confirmed_for_target(self, target_id: int, confirmed: bool) -> None:
        """Confirm (or demote) every claim held by one target — the solve/unsolve hook."""
        self._exec(
            "UPDATE feeder_claims SET confirmed = %s WHERE target_id = %s",
            (1 if confirmed else 0, target_id))
```

`INSERT IGNORE` leans on `uq_feeder_claims_item_target` so a double-claim is a no-op rather than an `IntegrityError` the caller has to catch — the same fact the live-DB test in Task 2 pins.

- [ ] **Step 3: Wire up the exports**

In `bot/store/__init__.py`, add to the dataclass imports (near `from .evidence_data import EvidenceData`):

```python
from .feeder_pool_data import FeederPoolItemData, FeederClaimData
```

Add `MySQLFeederPoolDb, MySQLFeederClaimDb` to the existing `from .mysqldb import ...` line, and alongside `EvidenceJsonDb = MySQLEvidenceDb(mydb=mydb)`:

```python
    FeederPoolDb = MySQLFeederPoolDb(mydb=mydb)
    FeederClaimDb = MySQLFeederClaimDb(mydb=mydb)
```

- [ ] **Step 4: Verify imports and typing**

```bash
timeout 60 pipenv run python -c "from bot.store import FeederPoolDb, FeederClaimDb, FeederPoolItemData, FeederClaimData; print('ok')"
timeout 180 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1
timeout 60 pipenv run ruff check > /tmp/ruff.log 2>&1
```

Expected: `ok`; basedpyright reports **0 errors** (CI enforces this); ruff clean. Read the logs.

- [ ] **Step 5: Commit**

```bash
git add bot/store/feeder_pool_data.py bot/store/mysqldb.py bot/store/__init__.py
git commit -m "feat(store): feeder pool dataclasses and DAOs"
```

---

## Task 4: Pure module — `claim_state`

This is the highest-value test surface in the feature. Every case uses **two items and two targets**, so a transposed id or a swapped argument cannot pass.

**Files:**
- Create: `bot/core/feeder_pool.py`
- Create: `tests/test_feeder_pool_pure.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_feeder_pool_pure.py`:

```python
"""Unit tests for the pure feeder-pool helpers. No DB, no Discord, no Sheets."""
from dataclasses import dataclass

from bot.core.feeder_pool import claim_state


@dataclass
class Claim:
    """Duck-typed stand-in — the module never imports the real dataclass."""
    item_id: int
    target_id: int
    confirmed: bool = False


MINI_1, MINI_2 = 101, 102
ALBATROSS, MARZIPAN = 12, 13


def test_free_when_no_other_target_claims_it():
    claims = [Claim(MARZIPAN, MINI_1)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "free"


def test_free_when_only_the_viewer_claims_it():
    """My own unconfirmed claim does not make the item look contested to me."""
    claims = [Claim(ALBATROSS, MINI_2)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "free"


def test_claimed_when_another_target_has_an_unconfirmed_claim():
    claims = [Claim(ALBATROSS, MINI_1), Claim(MARZIPAN, MINI_2)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "claimed"
    # ...and the other item, viewed the other way, is NOT claimed — a swapped
    # item/target argument would make both of these agree.
    assert claim_state(MARZIPAN, MINI_1, claims) == "claimed"
    assert claim_state(MARZIPAN, MINI_2, claims) == "free"


def test_taken_when_another_target_has_a_confirmed_claim():
    claims = [Claim(ALBATROSS, MINI_1, confirmed=True)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "taken"


def test_mine_when_the_viewer_holds_the_confirmed_claim():
    claims = [Claim(ALBATROSS, MINI_2, confirmed=True)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "mine"


def test_mine_wins_over_a_rival_unconfirmed_claim():
    claims = [Claim(ALBATROSS, MINI_2, confirmed=True), Claim(ALBATROSS, MINI_1)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "mine"
    assert claim_state(ALBATROSS, MINI_1, claims) == "taken"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: collection error — `ModuleNotFoundError: No module named 'bot.core.feeder_pool'`.

- [ ] **Step 3: Write the implementation**

Create `bot/core/feeder_pool.py`:

```python
"""Pure helpers for the feeder pool (meta matching puzzles).

No Discord, Sheets or DB imports — everything here is derivation over plain data,
so it is cheap to test exhaustively. Claims are duck-typed: any object with
`.item_id`, `.target_id` and `.confirmed` will do, which keeps
`bot/store/__init__.py` (and its MySQL connection) out of the unit tests.

See docs/superpowers/specs/2026-08-12-feeder-pool-design.md.
"""


def claim_state(item_id, viewing_target_id, claims):
    """The state of `item_id` as seen by `viewing_target_id`.

    'free'    — no OTHER target claims it (the viewer's own unconfirmed claim
                does not make it contested from the viewer's point of view)
    'claimed' — at least one other target holds an unconfirmed claim
    'taken'   — another target holds a CONFIRMED claim, so it is settled elsewhere
    'mine'    — the viewer holds the confirmed claim
    """
    mine_confirmed = False
    rival_confirmed = False
    rivals = 0
    for claim in claims:
        if claim.item_id != item_id:
            continue
        if claim.target_id == viewing_target_id:
            mine_confirmed = mine_confirmed or bool(claim.confirmed)
        else:
            rivals += 1
            rival_confirmed = rival_confirmed or bool(claim.confirmed)

    if mine_confirmed:
        return "mine"
    if rival_confirmed:
        return "taken"
    if rivals:
        return "claimed"
    return "free"
```

- [ ] **Step 4: Run it to verify it passes**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: 6 passed.

- [ ] **Step 5: Prove the tests bite**

In `bot/core/feeder_pool.py`, temporarily swap the `mine_confirmed` and `rival_confirmed` checks so `rival_confirmed` is tested first. Re-run.

Expected: `test_mine_wins_over_a_rival_unconfirmed_claim` fails. **Edit the lines back** (do not `git checkout --`) and confirm 6 passed again.

- [ ] **Step 6: Commit**

```bash
git add bot/core/feeder_pool.py tests/test_feeder_pool_pure.py
git commit -m "feat(core): derive feeder claim state"
```

---

## Task 5: Pure module — `rival_names`

**Files:**
- Modify: `bot/core/feeder_pool.py`
- Modify: `tests/test_feeder_pool_pure.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeder_pool_pure.py` (and add `rival_names` to the import at the top):

```python
NAMES = {MINI_1: "mini1", MINI_2: "mini2", 103: "mini3"}


def test_rival_names_lists_other_claimants_only():
    claims = [Claim(ALBATROSS, MINI_1), Claim(ALBATROSS, MINI_2)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == "mini1"


def test_rival_names_is_empty_when_nobody_else_claims_it():
    claims = [Claim(ALBATROSS, MINI_2)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == ""


def test_rival_names_are_sorted_not_insertion_ordered():
    """Order must be stable across syncs, or every reconcile rewrites the row.

    Two rivals inserted in the 'wrong' order — a function returning insertion
    order would disagree with one returning sorted order.
    """
    claims = [Claim(ALBATROSS, 103), Claim(ALBATROSS, MINI_1)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == "mini1, mini3"


def test_rival_names_skips_targets_with_no_known_name():
    claims = [Claim(ALBATROSS, MINI_1), Claim(ALBATROSS, 999)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == "mini1"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: `ImportError: cannot import name 'rival_names'`.

- [ ] **Step 3: Write the implementation**

Append to `bot/core/feeder_pool.py`:

```python
def rival_names(item_id, viewing_target_id, claims, names):
    """Comma-joined names of the OTHER targets claiming `item_id`.

    `names` maps target id to display name; a target with no name is skipped
    (it has been deleted, or is not part of this pool's rendering). Sorted, not
    insertion-ordered: an unstable order would rewrite the sheet row on every
    reconcile even when nothing changed.
    """
    rivals = sorted(
        names[claim.target_id]
        for claim in claims
        if claim.item_id == item_id
        and claim.target_id != viewing_target_id
        and claim.target_id in names
    )
    return ", ".join(rivals)
```

- [ ] **Step 4: Run it to verify it passes**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/feeder_pool.py tests/test_feeder_pool_pure.py
git commit -m "feat(core): name rival claimants for a feeder"
```

---

## Task 6: Pure module — `provided_item_ids`

This is the supersede rule from the spec: a claim to the Minimeta renders a provided row *unless* another target holds a confirmed claim on the same feeder.

Plan 2 consumes this when it renders provided rows. It is written and tested here because it is pure derivation and belongs with the rest of the module.

**Files:**
- Modify: `bot/core/feeder_pool.py`
- Modify: `tests/test_feeder_pool_pure.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeder_pool_pure.py` (and add `provided_item_ids` to the import):

```python
MINIMETA = 100


def test_a_claim_to_the_minimeta_yields_a_provided_row():
    claims = [Claim(MARZIPAN, MINIMETA)]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_it_renders_while_unconfirmed():
    """The confirming event for a main-meta claim is solving the main meta, which
    is the last thing that happens — so waiting for confirmation would mean the row
    never appeared while it was useful."""
    claims = [Claim(MARZIPAN, MINIMETA, confirmed=False)]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_a_confirmed_claim_elsewhere_supersedes_it():
    claims = [Claim(MARZIPAN, MINIMETA), Claim(MARZIPAN, MINI_1, confirmed=True)]
    assert provided_item_ids(MINIMETA, claims) == []


def test_an_unconfirmed_rival_does_not_supersede_it():
    claims = [Claim(MARZIPAN, MINIMETA), Claim(MARZIPAN, MINI_1, confirmed=False)]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_superseding_is_per_item_not_wholesale():
    """Two items, one superseded and one not — a rule that dropped everything (or
    nothing) as soon as any confirmed claim existed would pass with only one item."""
    claims = [
        Claim(ALBATROSS, MINIMETA), Claim(ALBATROSS, MINI_1, confirmed=True),
        Claim(MARZIPAN, MINIMETA), Claim(MARZIPAN, MINI_2, confirmed=False),
    ]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_claims_that_are_not_the_minimetas_are_ignored():
    claims = [Claim(ALBATROSS, MINI_1), Claim(MARZIPAN, MINI_2)]
    assert provided_item_ids(MINIMETA, claims) == []
```

- [ ] **Step 2: Run it to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: `ImportError: cannot import name 'provided_item_ids'`.

- [ ] **Step 3: Write the implementation**

Append to `bot/core/feeder_pool.py`:

```python
def provided_item_ids(minimeta_id, claims):
    """Item ids to render as `provided:` rows on the minimeta's contribution table.

    A claim held by the minimeta ITSELF yields a row — rendered on the claim, not on
    confirmation, because the confirming event for a main-meta claim would be solving
    the main meta, i.e. the end of the puzzle.

    Unless another target holds a CONFIRMED claim on the same item: solving that mini
    proved the feeder belongs there, so the main-meta guess is superseded and the row
    drops out. Nothing is deleted — the claim survives, rendering as 'taken', and
    unsolving that mini restores the row.

    Returned in claim order (item_id ascending, as the DAO fetches them).
    """
    confirmed_elsewhere = {
        claim.item_id for claim in claims
        if claim.confirmed and claim.target_id != minimeta_id
    }
    return [
        claim.item_id for claim in claims
        if claim.target_id == minimeta_id and claim.item_id not in confirmed_elsewhere
    ]
```

- [ ] **Step 4: Run it to verify it passes**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: 16 passed.

- [ ] **Step 5: Prove the supersede rule bites**

Temporarily delete the `and claim.target_id != minimeta_id` clause from `confirmed_elsewhere`. Re-run.

Expected: failures — a minimeta's own confirmed claim would now supersede itself. **Edit the clause back** and confirm 16 passed.

- [ ] **Step 6: Commit**

```bash
git add bot/core/feeder_pool.py tests/test_feeder_pool_pure.py
git commit -m "feat(core): select provided rows, dropping superseded claims"
```

---

## Task 7: Pure module — `diff_ticks`

Plan 2's sync consumes this. It is written here for the same reason as Task 6: pure derivation, and the two rules it encodes (stale ticks ignored, confirmed claims not droppable) are spec requirements that deserve tests regardless of when the sheet code lands.

**Files:**
- Modify: `bot/core/feeder_pool.py`
- Modify: `tests/test_feeder_pool_pure.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_feeder_pool_pure.py` (and add `diff_ticks` to the import):

```python
QUAY, TERN = 14, 15


def test_diff_ticks_adds_new_ticks_and_removes_cleared_ones():
    add, remove = diff_ticks(
        pool_item_ids=[ALBATROSS, MARZIPAN, QUAY],
        current_item_ids=[ALBATROSS],
        ticked_item_ids=[MARZIPAN, QUAY],
        confirmed_item_ids=[],
    )
    assert add == [MARZIPAN, QUAY]
    assert remove == [ALBATROSS]


def test_diff_ticks_ignores_a_tick_on_an_item_no_longer_in_the_pool():
    """reconcile_table BLANKS pruned rows rather than deleting them, so a solver's
    tick can survive on a removed item's row. Adopting it would resurrect a dead
    claim."""
    add, remove = diff_ticks(
        pool_item_ids=[ALBATROSS],
        current_item_ids=[],
        ticked_item_ids=[ALBATROSS, TERN],
        confirmed_item_ids=[],
    )
    assert add == [ALBATROSS]
    assert TERN not in add


def test_diff_ticks_never_removes_a_confirmed_claim():
    """Confirmed claims are bot-owned: unticking one does not undo it (unsolving
    the mini does), and the next render restores the tick."""
    add, remove = diff_ticks(
        pool_item_ids=[ALBATROSS, MARZIPAN],
        current_item_ids=[ALBATROSS, MARZIPAN],
        ticked_item_ids=[],
        confirmed_item_ids=[ALBATROSS],
    )
    assert remove == [MARZIPAN]
    assert ALBATROSS not in remove


def test_diff_ticks_is_a_no_op_when_nothing_changed():
    add, remove = diff_ticks(
        pool_item_ids=[ALBATROSS, MARZIPAN],
        current_item_ids=[ALBATROSS],
        ticked_item_ids=[ALBATROSS],
        confirmed_item_ids=[],
    )
    assert add == []
    assert remove == []
```

- [ ] **Step 2: Run it to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: `ImportError: cannot import name 'diff_ticks'`.

- [ ] **Step 3: Write the implementation**

Append to `bot/core/feeder_pool.py`:

```python
def diff_ticks(pool_item_ids, current_item_ids, ticked_item_ids, confirmed_item_ids):
    """What to change for ONE target after reading its tick column off a sheet.

    Returns (to_add, to_remove), both sorted item-id lists.

    Two rules the sheet forces:
      * A tick on an item that is no longer in the pool is ignored. reconcile_table
        blanks pruned rows instead of deleting them, so a tick outlives its item.
      * A confirmed claim is never removed by an untick — it is bot-owned, and the
        next render puts the tick back. Unsolving the mini is how it is undone.
    """
    pool = set(pool_item_ids)
    ticked = {item_id for item_id in ticked_item_ids if item_id in pool}
    current = set(current_item_ids)
    confirmed = set(confirmed_item_ids)

    to_add = sorted(ticked - current)
    to_remove = sorted(current - ticked - confirmed)
    return to_add, to_remove
```

- [ ] **Step 4: Run it to verify it passes**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_pure.py -q > /tmp/test.log 2>&1
```

Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/core/feeder_pool.py tests/test_feeder_pool_pure.py
git commit -m "feat(core): diff sheet ticks into claim changes"
```

---

## Task 8: Service — pool CRUD and claims

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Create: `tests/test_feeder_pool_service.py`

Patch the DAOs **at the service import site**, never at `bot.store` — `puzzle_service` imports the names into its own namespace, so patching the source module leaves the service's own reference untouched and the mock silently never engages.

- [ ] **Step 1: Write the failing test**

Create `tests/test_feeder_pool_service.py`:

```python
"""Service-level feeder pool tests. DAOs mocked at the puzzle_service import site.

NOTE: build a real PuzzleService. `make_cog()` sets `cog.service = MagicMock()`, so
asserting against `make_cog().service` would pass no matter what the service did.
`PuzzleService(MagicMock())` is the house pattern — see tests/test_create_service.py.
"""
from unittest.mock import MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from bot.store import FeederPoolItemData, FeederClaimData
from tests.conftest import make_puzzle


def _service():
    return PuzzleService(MagicMock())


def test_add_feeder_items_splits_a_paste_and_strips_blanks():
    svc = _service()
    minimeta = make_puzzle(id=100, name="Matching Meta")
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.add_many.return_value = []
        svc.add_feeder_items(minimeta, "ALBATROSS\n\nMARZIPAN\n  QUAY  \n")
    pool_db.add_many.assert_called_once_with(100, ["ALBATROSS", "MARZIPAN", "QUAY"])


def test_add_feeder_items_uppercases_nothing_and_keeps_order():
    """Given feeders are quoted verbatim from the puzzle — the bot must not
    normalise them, and paste order is the order the puzzle presented them."""
    svc = _service()
    minimeta = make_puzzle(id=100)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.add_many.return_value = []
        svc.add_feeder_items(minimeta, "Seventh Heaven\nalbatross")
    pool_db.add_many.assert_called_once_with(100, ["Seventh Heaven", "albatross"])


def test_claim_feeder_records_the_claim():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        svc.claim_feeder(item_id=12, target_id=101)
    claim_db.add.assert_called_once_with(12, 101)


def test_unclaim_feeder_removes_only_that_targets_claim():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        svc.unclaim_feeder(item_id=12, target_id=101)
    claim_db.remove.assert_called_once_with(12, 101)


def test_get_feeder_pool_returns_items_with_derived_state_per_target():
    """Two items and two minis, so a transposed target id cannot pass."""
    svc = _service()
    minimeta = make_puzzle(id=100, name="Matching Meta")
    items = [
        FeederPoolItemData(id=12, minimeta_puzzle_id=100, text="ALBATROSS", position=0),
        FeederPoolItemData(id=13, minimeta_puzzle_id=100, text="MARZIPAN", position=1),
    ]
    claims = [
        FeederClaimData(id=1, item_id=12, target_id=101, confirmed=True),
        FeederClaimData(id=2, item_id=13, target_id=102, confirmed=False),
    ]
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db, \
         patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        pool_db.get_for_minimeta.return_value = items
        claim_db.get_for_minimeta.return_value = claims
        got_items, got_claims = svc.get_feeder_pool(minimeta)

    assert [i.text for i in got_items] == ["ALBATROSS", "MARZIPAN"]
    assert got_claims == claims
    pool_db.get_for_minimeta.assert_called_once_with(100)
    claim_db.get_for_minimeta.assert_called_once_with(100)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_service.py -q > /tmp/test.log 2>&1
```

Expected: `AttributeError: ... has no attribute 'add_feeder_items'`.

If `PuzzleService(MagicMock())` does not construct cleanly, read `tests/test_create_service.py` — it is the canonical example named in `AGENTS.md` — and match how it builds a service.

- [ ] **Step 3: Add the DAO imports**

In `bot/core/puzzle_service.py`, add `FeederPoolDb` and `FeederClaimDb` to the existing `from bot.store import ...` statement, keeping the existing style.

- [ ] **Step 4: Write the service methods**

Add to `PuzzleService` in `bot/core/puzzle_service.py`, near the minimeta methods (`make_minimeta` / `create_mini`):

```python
    # --- Feeder pool (meta matching puzzles) ------------------------------
    # A Minimeta owns a pool of given feeder strings; its Minis (and the Minimeta
    # itself) claim items out of it. See
    # docs/superpowers/specs/2026-08-12-feeder-pool-design.md.

    def add_feeder_items(self, minimeta, pasted):
        """Append the non-blank lines of `pasted` to `minimeta`'s pool.

        Text is stored verbatim — a given feeder is quoted from the puzzle, so
        normalising case would misrepresent it. Re-pasting the same list is
        harmless: add_many skips texts already present.

        Returns the FeederPoolItemData rows actually added.
        """
        texts = [line.strip() for line in pasted.splitlines()]
        texts = [t for t in texts if t]
        return FeederPoolDb.add_many(minimeta.id, texts)

    def edit_feeder_item(self, item_id, text):
        """Retype a feeder. Its claims and note are untouched — which is why this
        exists rather than remove-and-re-add."""
        FeederPoolDb.set_text(item_id, text)

    def remove_feeder_item(self, item_id):
        """Delete a feeder. Its claims cascade (migration 043)."""
        FeederPoolDb.delete(item_id)

    def claim_feeder(self, item_id, target_id):
        """Claim a feeder for a target (a Mini, or the Minimeta itself).

        Overlapping claims are legal and deliberate: a rival claim is information,
        and contests are surfaced rather than rejected.
        """
        FeederClaimDb.add(item_id, target_id)

    def unclaim_feeder(self, item_id, target_id):
        FeederClaimDb.remove(item_id, target_id)

    def get_feeder_pool(self, minimeta):
        """(items, claims) for a minimeta's pool — one fetch each.

        Every derived view (state, rivals, provided rows) is computed from these two
        lists by bot/core/feeder_pool.py, so callers fetch once rather than querying
        per item.
        """
        return (
            FeederPoolDb.get_for_minimeta(minimeta.id),
            FeederClaimDb.get_for_minimeta(minimeta.id),
        )
```

None of these is decorated with `@pushes_board()` — the dashboard does not render the pool until Plan 3, so pushing now would emit board updates carrying nothing new.

- [ ] **Step 5: Run it to verify it passes**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_service.py -q > /tmp/test.log 2>&1
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_feeder_pool_service.py
git commit -m "feat(service): feeder pool CRUD and claims"
```

---

## Task 9: Confirm on solve, demote on unsolve

The hook goes in `service.solve()` (`bot/core/puzzle_service.py:1286`) and `service.unsolve()` (`:1370`) — the seam both `bot/cogs/puzzles.py:3488` and `bot/web/actions.py:523` already call, so both edges are covered by construction rather than by remembering to.

**Files:**
- Modify: `bot/core/puzzle_service.py`
- Create: `tests/test_feeder_pool_confirm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_feeder_pool_confirm.py`:

```python
"""Confirm-on-solve for the feeder pool, exercised through the shared seam.

Uses a real PuzzleService — `make_cog()` stubs `.service` with a MagicMock, which
would make every assertion here pass regardless of the implementation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from tests.conftest import make_hunt, make_puzzle


def _service():
    return PuzzleService(MagicMock())


def _mini(pid=101, parent_id=100):
    return make_puzzle(id=pid, name=f"Mini {pid}",
                       puzzle_type="Mini", parent_puzzle_id=parent_id)


async def test_solving_a_mini_confirms_its_claims():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(_mini(), confirmed=True)
    claim_db.set_confirmed_for_target.assert_called_once_with(101, True)


async def test_unsolving_a_mini_demotes_its_claims():
    svc = _service()
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(_mini(), confirmed=False)
    claim_db.set_confirmed_for_target.assert_called_once_with(101, False)


async def test_a_non_mini_puzzle_confirms_nothing():
    """An ordinary puzzle solving must not touch any pool — only a Mini's solve is
    proof about a feeder."""
    svc = _service()
    ordinary = make_puzzle(id=200, name="Ordinary")
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(ordinary, confirmed=True)
    claim_db.set_confirmed_for_target.assert_not_called()


async def test_a_mini_with_no_parent_confirms_nothing():
    """Type alone is not enough — an orphaned Mini has no pool to confirm against."""
    svc = _service()
    orphan = make_puzzle(id=201, name="Orphan", puzzle_type="Mini")
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(orphan, confirmed=True)
    claim_db.set_confirmed_for_target.assert_not_called()


async def test_confirming_targets_the_solved_mini_and_not_its_sibling():
    """Two minis under the same parent — a hook that confirmed by POOL rather than
    by TARGET would pass with only one mini in the test."""
    svc = _service()
    mini_a, mini_b = _mini(pid=101), _mini(pid=102)
    with patch("bot.core.puzzle_service.FeederClaimDb") as claim_db:
        await svc.confirm_feeder_claims(mini_a, confirmed=True)
    claim_db.set_confirmed_for_target.assert_called_once_with(mini_a.id, True)
    assert claim_db.set_confirmed_for_target.call_args.args[0] != mini_b.id


async def test_solve_calls_the_hook():
    """Through service.solve() itself, not the helper — this is the seam both the
    Discord and web edges call, and the whole point of hooking here."""
    svc = _service()
    mini = _mini()
    with patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch.object(svc, "confirm_feeder_claims", new=AsyncMock()) as hook:
        try:
            await svc.solve(MagicMock(), make_hunt(), mini, "SEABIRDS")
        except Exception:
            pass   # sheet/archive plumbing is not what this test is about
    hook.assert_awaited_once()
    assert hook.await_args.args[0] is mini
```

`test_solve_calls_the_hook` deliberately tolerates the surrounding solve machinery
failing under mocks — it asserts only that the hook fires from the shared seam. If
`solve()`'s internals make even that unworkable, read `tests/test_solve_equivalence.py`
and follow how it drives `solve()`.

**Then prove this test bites**, since a `try/except Exception` around the call could
be hiding a hook that never runs: comment out the `await self.confirm_feeder_claims(...)`
line you add in Step 4, re-run, and confirm it fails on `assert_awaited_once`. Restore
the line by editing it back.

- [ ] **Step 2: Run it to verify it fails**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_confirm.py -q > /tmp/test.log 2>&1
```

Expected: `AttributeError: ... no attribute 'confirm_feeder_claims'`.

- [ ] **Step 3: Write the hook**

Add to `PuzzleService` beside the other feeder-pool methods:

```python
    async def confirm_feeder_claims(self, puzzle, confirmed):
        """Confirm (or demote) every feeder claim held by `puzzle`.

        Solving a Mini is proof that the feeders it claimed belong to it, so
        confirmation is automatic rather than a manual tick; unsolving is the exact
        inverse. Only a Mini qualifies — an ordinary puzzle's solve says nothing
        about any pool.

        Rival claims on the same feeders are deliberately left alone. A losing claim
        is information worth keeping, and deleting it would make unsolve lossy.

        Sheet rendering is Plan 2's job; this only moves the DB state.
        """
        if getattr(puzzle, "puzzle_type", None) != "Mini":
            return
        if getattr(puzzle, "parent_puzzle_id", None) is None:
            return
        FeederClaimDb.set_confirmed_for_target(puzzle.id, confirmed)
```

- [ ] **Step 4: Call it from both sides of the seam**

In `solve()`, after `PuzzleJsonDb.commit(puzzle)` has persisted the solve and before the meta-refresh block:

```python
        # A Mini solving proves which feeders it used — confirm its pool claims.
        await self.confirm_feeder_claims(puzzle, confirmed=True)
```

In `unsolve()`, at the matching point after the unsolve is persisted:

```python
        # The inverse: the claims go back to contested, losing nothing.
        await self.confirm_feeder_claims(puzzle, confirmed=False)
```

- [ ] **Step 5: Run it to verify it passes**

```bash
timeout 60 pipenv run python -m pytest tests/test_feeder_pool_confirm.py -q > /tmp/test.log 2>&1
```

Expected: 6 passed.

- [ ] **Step 6: Check nothing else broke**

`solve()` and `unsolve()` are among the most-covered paths in the suite, so run the whole thing:

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```

Expected: the full suite green (1894 passed at `dc957bd`, plus the new tests). Read `/tmp/test.log`; a killed run exits 124.

- [ ] **Step 7: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_feeder_pool_confirm.py
git commit -m "feat(service): confirm feeder claims when a mini is solved"
```

---

## Task 10: Bang commands

**Files:**
- Modify: `bot/cogs/puzzles.py`
- Modify: `docs/commands.md`

Read the neighbouring `!make_minimeta` / `!remove_minimeta` commands first (`bot/cogs/puzzles.py`) and match their shape. Do not invent a new command style.

Gating, per the 2026-08-11 audit's area-based principle: everything here is ungated except `!remove_feeder`, which destroys claims and notes and is therefore `@is_organiser()`.

- [ ] **Step 1: Write the channel-context helper**

Every feeder command needs the same two things: whose pool, and who is claiming. Add
this to the Puzzles cog beside the other minimeta helpers:

```python
    def _feeder_context(self, ctx):
        """(minimeta, claim_target) for a feeder command, or (None, None).

        In a Mini's thread the pool belongs to the PARENT and the claim target is the
        Mini. In the Minimeta's own channel both are the Minimeta — which can itself
        claim a feeder, that being how "the main meta uses a given feeder directly"
        is expressed.
        """
        puzzle = self.get_puzzle(ctx)
        if not puzzle:
            return None, None
        if puzzle.puzzle_type == "Mini" and puzzle.parent_puzzle_id:
            parent = PuzzleJsonDb.get_by_attr(id=puzzle.parent_puzzle_id)
            if parent is None:
                return None, None
            return parent, puzzle
        return puzzle, puzzle
```

- [ ] **Step 2: Write the commands**

Add to the Puzzles cog, following the `!make_minimeta` / `!remove_minimeta` shape
exactly — `@commands.command()`, an italic one-line summary, `Usage:`/`Example:` lines,
`self.get_puzzle(ctx)` guarded by `send_not_puzzle_channel`, and a `:white_check_mark:`
reply.

```python
    @commands.command()
    async def add_feeders(self, ctx, *, pasted: str = ""):
        """*Add given feeders to this minimeta's pool, one per line.*

        Usage: `!add_feeders <one feeder per line>`
        Example: `!add_feeders ALBATROSS`
        Run in the minimeta's channel or one of its mini threads. Re-pasting the same
        list is harmless — feeders already in the pool are skipped."""
        minimeta, _target = self._feeder_context(ctx)
        if minimeta is None:
            return await self.send_not_puzzle_channel(ctx)
        if not pasted.strip():
            return await ctx.send(":x: Give me the feeders, one per line.")
        added = self.service.add_feeder_items(minimeta, pasted)
        offered = len([ln for ln in pasted.splitlines() if ln.strip()])
        msg = (f":white_check_mark: Added {len(added)} feeder(s) to "
               f"**{minimeta.name}**'s pool.")
        if offered > len(added):
            msg += f" {offered - len(added)} already there."
        await ctx.send(msg)

    @commands.command()
    async def feeders(self, ctx):
        """*Show the given-feeder pool and who has claimed what.*

        Usage: `!feeders`
        Example: `!feeders`
        Run in the minimeta's channel or one of its mini threads. State is shown from
        THIS channel's point of view."""
        minimeta, target = self._feeder_context(ctx)
        if minimeta is None:
            return await self.send_not_puzzle_channel(ctx)
        items, claims = self.service.get_feeder_pool(minimeta)
        if not items:
            return await ctx.send(
                f"**{minimeta.name}** has no feeder pool yet — add one with "
                f"`!add_feeders`.")
        names = {m.id: m.name for m in self.service.get_minis(minimeta.id)}
        names[minimeta.id] = minimeta.name
        lines = []
        for item in items:
            state = claim_state(item.id, target.id, claims)
            rivals = rival_names(item.id, target.id, claims, names)
            line = f"`{item.id}` **{item.text}** — {state}"
            if rivals:
                line += f" ({rivals})"
            if item.note:
                line += f" — _{item.note}_"
            lines.append(line)
        await self.send_feeder_pool_embed(ctx, minimeta, lines)

    @commands.command()
    async def claim_feeder(self, ctx, item_id: int):
        """*Claim a given feeder for this puzzle.*

        Usage: `!claim_feeder <id>`
        Example: `!claim_feeder 13`
        The id is the number `!feeders` shows. Overlapping claims are allowed — a rival
        claim is useful information, not an error."""
        minimeta, target = self._feeder_context(ctx)
        if minimeta is None:
            return await self.send_not_puzzle_channel(ctx)
        item = self.service.get_feeder_item(minimeta, item_id)
        if item is None:
            return await ctx.send(f":x: No feeder `{item_id}` in this pool.")
        self.service.claim_feeder(item.id, target.id)
        await ctx.send(
            f":white_check_mark: **{target.name}** claims **{item.text}**.")

    @commands.command()
    async def unclaim_feeder(self, ctx, item_id: int):
        """*Drop this puzzle's claim on a given feeder.*

        Usage: `!unclaim_feeder <id>`
        Example: `!unclaim_feeder 13`
        Only drops THIS puzzle's claim; other claims are untouched."""
        minimeta, target = self._feeder_context(ctx)
        if minimeta is None:
            return await self.send_not_puzzle_channel(ctx)
        item = self.service.get_feeder_item(minimeta, item_id)
        if item is None:
            return await ctx.send(f":x: No feeder `{item_id}` in this pool.")
        self.service.unclaim_feeder(item.id, target.id)
        await ctx.send(
            f":white_check_mark: **{target.name}** no longer claims **{item.text}**.")

    @commands.command()
    async def edit_feeder(self, ctx, item_id: int, *, text: str):
        """*Fix a feeder's text, keeping its claims and note.*

        Usage: `!edit_feeder <id> <new text>`
        Example: `!edit_feeder 13 ALBATROSS`
        Use this for a typo — `!remove_feeder` would throw away the claims and the note
        attached to the feeder."""
        minimeta, _target = self._feeder_context(ctx)
        if minimeta is None:
            return await self.send_not_puzzle_channel(ctx)
        item = self.service.get_feeder_item(minimeta, item_id)
        if item is None:
            return await ctx.send(f":x: No feeder `{item_id}` in this pool.")
        self.service.edit_feeder_item(item.id, text.strip())
        await ctx.send(
            f":white_check_mark: `{item.id}` **{item.text}** → **{text.strip()}**.")

    @commands.command()
    @is_organiser()
    async def remove_feeder(self, ctx, item_id: int):
        """*Delete a feeder from the pool, with its claims and note.*

        Usage: `!remove_feeder <id>`
        Example: `!remove_feeder 13`
        For a feeder that should never have been in the pool. To fix a typo use
        `!edit_feeder`, which keeps the claims. Organisers/admins only; asks to
        confirm. Not recoverable."""
        minimeta, _target = self._feeder_context(ctx)
        if minimeta is None:
            return await self.send_not_puzzle_channel(ctx)
        item = self.service.get_feeder_item(minimeta, item_id)
        if item is None:
            return await ctx.send(f":x: No feeder `{item_id}` in this pool.")
        if not await self._confirm_destructive(
            ctx,
            f"Delete feeder **{item.text}** and every claim on it? "
            f"To fix a typo use `!edit_feeder` instead.",
        ):
            return
        self.service.remove_feeder_item(item.id)
        await ctx.send(f":white_check_mark: Removed **{item.text}** from the pool.")
```

Add the imports these need at the top of `bot/cogs/puzzles.py`:

```python
from bot.core.feeder_pool import claim_state, rival_names
```

`PuzzleJsonDb` and `is_organiser` are already imported there. Read `_confirm_destructive`
(`bot/cogs/puzzles.py:998` is a caller) and match its real signature — the call above
assumes `(ctx, description) -> bool`; if it differs, follow the real one.

- [ ] **Step 3: Add the service lookup the commands need**

The commands resolve an id **within the invoking pool**, so a stray id from another
minimeta cannot be edited or deleted by someone in the wrong channel. Add to
`PuzzleService` beside the other feeder methods:

```python
    def get_feeder_item(self, minimeta, item_id):
        """One pool item by id, or None if it is not in THIS minimeta's pool.

        Scoped deliberately: an id is a small integer a solver types, so an unscoped
        lookup would let a command in one minimeta's channel edit or delete another
        minimeta's feeder.
        """
        item = FeederPoolDb.get_by_id(item_id)
        if item is None or item.minimeta_puzzle_id != minimeta.id:
            return None
        return item
```

- [ ] **Step 4: Write the scoping test**

Append to `tests/test_feeder_pool_service.py`:

```python
def test_get_feeder_item_refuses_an_item_from_another_pool():
    """Two minimetas — an unscoped lookup would return the foreign item and let a
    command in the wrong channel edit or delete it."""
    svc = _service()
    mine = make_puzzle(id=100, name="Mine")
    foreign_item = FeederPoolItemData(
        id=12, minimeta_puzzle_id=999, text="ALBATROSS", position=0)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.get_by_id.return_value = foreign_item
        assert svc.get_feeder_item(mine, 12) is None


def test_get_feeder_item_returns_an_item_from_this_pool():
    svc = _service()
    mine = make_puzzle(id=100, name="Mine")
    item = FeederPoolItemData(
        id=12, minimeta_puzzle_id=100, text="ALBATROSS", position=0)
    with patch("bot.core.puzzle_service.FeederPoolDb") as pool_db:
        pool_db.get_by_id.return_value = item
        assert svc.get_feeder_item(mine, 12) is item
```

Run: `timeout 60 pipenv run python -m pytest tests/test_feeder_pool_service.py -q > /tmp/test.log 2>&1`
Expected: 8 passed.

- [ ] **Step 5: Check the embed cannot exceed Discord's limits**

`send_feeder_pool_embed` is referenced by `!feeders` above and still has to be written. A pool can hold dozens of items and Discord caps an embed field at 1024 characters, so it must truncate. `tests/test_mini_list_embed_limits.py` already covers exactly this problem for the mini list — read it, follow the same truncation approach, and add an equivalent test for the pool rather than inventing a second style.

- [ ] **Step 6: Add gating assertions**

`tests/test_command_gating.py` asserts a whole command→tier map, so it will flag the six new commands as unlisted. Add them: `remove_feeder` organiser, the other five ungated.

- [ ] **Step 7: Run the gating and command tests**

```bash
timeout 120 pipenv run python -m pytest tests/test_command_gating.py tests/test_minimeta_commands.py -q > /tmp/test.log 2>&1
```

Expected: all pass.

- [ ] **Step 8: Document the commands**

Add the six to `docs/commands.md`, matching the existing table's columns and the "Who" wording (`organisers/admins` for `!remove_feeder`, whatever the file uses for open commands elsewhere).

- [ ] **Step 9: Full suite, lint, types**

```bash
timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
timeout 60 pipenv run ruff check > /tmp/ruff.log 2>&1
timeout 180 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1
```

Expected: suite green, ruff clean, basedpyright 0 errors. Read all three logs.

- [ ] **Step 10: Commit**

```bash
git add bot/cogs/puzzles.py bot/core/puzzle_service.py docs/commands.md tests/test_command_gating.py tests/test_feeder_pool_service.py
git commit -m "feat(commands): manage the feeder pool from Discord"
```

---

## Verification

Plan 1 is done when all of the following hold:

- [ ] `timeout 300 pipenv run python -m pytest tests/ -q` is green, including the live-DB cascade file.
- [ ] `pipenv run ruff check` is clean.
- [ ] `pipenv run basedpyright bot/` reports 0 errors.
- [ ] The mutation checks in Tasks 2, 4 and 6 were each run and each went red for the stated reason, then were restored.
- [ ] `bot/core/feeder_pool.py` imports nothing from `bot.store`, `discord`, or the Sheets cog:
      `grep -nE "^(from|import) " bot/core/feeder_pool.py` returns nothing.
- [ ] A Minimeta with no pool behaves exactly as before — `tests/test_minimeta_commands.py`,
      `tests/test_refresh_minimeta.py` and `tests/test_web_mini_refresh.py` all still pass untouched.

**Acceptance criteria from the spec covered by Plan 1:** 2 (paste is additive), 5 (solving confirms and leaves rivals intact), 6 (unsolve restores the contested set), 8 (edit preserves, remove cascades).

**Deliberately not covered here** — they need the sheet surfaces (Plan 2) or the dashboard (Plan 3): 1, 3, 4, 7, 9, 10, 11, 12, 13, 14, 15, 16.

## Rollback / recovery notes

- Every task commits on its own, so `git revert` of a single commit is the unit of undo.
- The migration is separately revertable: `DROP TABLE feeder_claims; DROP TABLE feeder_pool_items;` (claims first — its FK points at the items).
- Nothing in Plan 1 changes existing behaviour except the two hook calls in `solve()`/`unsolve()`, both of which return immediately for any puzzle that is not a Mini. If the full suite goes red after Task 9, that hook is the first place to look.

# Atomicity Work Implementation Plan

> **STATUS: SHIPPED 2026-08-08.** All 12 tasks done and merged to `staging`
> (`ba0df74..f472a83`, 17 commits), pushed to `origin/staging`. Gates at merge: 1686
> tests passing, ruff clean, `basedpyright bot/` 0 errors, `scripts/diagnose_atomicity.py`
> 6/6. Kept as a record — do not re-execute. Migration 037 was applied to
> `puzzlebot_test` only; it reaches production on the next deploy via `migrate.py`.
> H5 (the convert/delete lock-ordering deadlock) remains open and is the next item.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the silent-lost-update and half-applied-write paths in the store layer and at the Discord bang-command edge, and make the code's claims about atomicity true.

**Architecture:** No transactions are introduced anywhere. Each multi-statement store operation is made un-half-appliable by *statement shape* — reordered so a mid-op failure is recoverable, collapsed to a single statement, or replaced by an FK cascade. At the bang edge, the per-puzzle lock is made to cover the read as well as the write, matching the shape the web layer already uses. Two background link paths take the lock they were missing.

**Tech Stack:** Python 3.12, discord.py, Quart, MariaDB via mysql-connector-python, pytest, ruff, basedpyright. Environment manager is **pipenv**.

**Spec:** `docs/superpowers/specs/2026-08-08-atomicity-design.md` — authoritative. If this plan contradicts it, the spec wins.

---

## Before you start

Read these first; they explain conventions this plan assumes.

- `docs/superpowers/specs/2026-08-08-atomicity-design.md` — the spec.
- `bot/core/locks.py` — the whole locking model, in one short file. Key rule: **`asyncio.Lock` is not reentrant**; a given puzzle's lock is acquired at most once per task, at the *edges* (cog decorator, web handler). `PuzzleService` is deliberately lock-free so an edge that already holds the lock can call it.
- `tests/test_tag_edges.py` — the live-MySQL store-test convention. Some store behaviour is not mockable and is tested against the real test database (`puzzlebot_test`), inserting FK-valid rows and tearing them down in a `finally`.

### Environment facts

- Tests need a live MySQL/MariaDB and a `config.json`; `tests/conftest.py` reaches the DB at import. A fresh worktree without `config.json` + `google_secrets.json` copied in **cannot run tests at all** (pytest exits 4).
- Run the full suite as a single statement, wrapped in `timeout`, writing to a file:

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1
```

Exit 124 means it was killed on time; read `/tmp/test-output.log` either way. Expected baseline is ~1658 passing in about 12 seconds — if it takes minutes, treat it as a hang, not slowness.

- Single-test runs during iteration:

```bash
timeout 120 pipenv run python -m pytest tests/test_foo.py::test_bar -q > /tmp/test-output.log 2>&1
```

- The other two gates:

```bash
pipenv run ruff check
pipenv run basedpyright bot/
```

Both must be clean (`All checks passed`, `0 errors, 0 warnings`) before every commit.

### The mutation-proof rule — applies to every task

For each test you write, after it passes: **deliberately break the implementation, re-run the test, confirm it goes red for the right reason, then restore.** A test that stays green against broken code is worse than no test. Restore by editing the line back — **never** `git checkout --` a file with other uncommitted changes in it. Do not commit the mutation. Each task below names the exact mutation to apply.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `migrations/037_message_attachment_fk.sql` | create | Orphan sweep + FK so message deletion cascades to attachments |
| `bot/store/mysqldb.py` | modify | `bulk_update_edge_type` reorder; `set_positions` single statement; `delete_message` single statement; `_MySQLBaseDb.commit` rowcount log; remove 33 no-op `commit()` calls |
| `bot/store/__init__.py` | modify | Honest comment on `MySQLReconnector.commit` |
| `bot/web/data.py` | modify | Remove the no-op `conn.commit()` and fix its docstring |
| `bot/cogs/puzzles.py` | modify | `with_puzzle_mutex` re-reads under the lock; drop `cog_after_invoke` commits; delete `!add_tag` / `!remove_tag` |
| `bot/core/puzzle_service.py` | modify | Lock the two evidence link/unlink paths |
| `docs/commands.md` | modify | Drop the two deleted command rows |
| `scripts/diagnose_atomicity.py` | create | Hand test script against a real database |
| `tests/test_bulk_edge_atomicity.py` | create | Live-DB test for the edge-conversion reorder |
| `tests/test_puzzle_image_positions.py` | create | Live-DB test for `set_positions` ordering |
| `tests/test_message_delete_cascade.py` | create | Live-DB test for the FK cascade |
| `tests/test_missing_row_update_logs.py` | create | Test for the rowcount ERROR log |
| `tests/test_bang_edge_atomicity.py` | create | Concurrency test for the re-read-under-lock fix |
| `tests/test_evidence_link_locking.py` | create | Test that the background link paths take the lock |

**Task order matters.** Tasks 1–5 are independent store fixes. Task 6 depends on Task 1's migration existing. Tasks 7–9 are the cog edge. Task 10 is the service. Task 11 is the mechanical deletion, deliberately last so it does not collide with the earlier edits to the same file. Task 12 is the script, which exercises everything before it.

---

## Task 1: Migration 037 — the `message_attachments` FK

**Files:**
- Create: `migrations/037_message_attachment_fk.sql`

**Why:** `027_message_bridge.sql` created `messages` and `message_attachments` with no foreign key between them (`:20-31`), so `delete_message` has to delete both by hand — and a failure between the two statements destroys the attachment rows while leaving the message. An `ON DELETE CASCADE` lets the code delete one row and have the database guarantee the rest.

Both columns are already `BIGINT UNSIGNED` (`messages.id` at `027:5`, `message_attachments.message_id` at `027:22`), so unlike migration 022 no `MODIFY` is needed to make the types match.

- [ ] **Step 1: Check whether a migration 037 already exists**

Parallel worktrees can each grab the same migration number; git merges both cleanly and hides the collision.

Run: `ls migrations/ | tail -5`

Expected: highest is `036_rename_audit_log.sql`. **If a 037 already exists, stop and ask** — do not silently renumber.

- [ ] **Step 2: Write the migration**

Create `migrations/037_message_attachment_fk.sql`:

```sql
-- 037_message_attachment_fk.sql
-- T1 (docs/reviews/2026-08-07-comprehensive-review.md): message_attachments has no FK to
-- messages (027_message_bridge.sql declares neither), so MessageDb.delete_message has to
-- delete the attachment rows and the message row as two separate autocommitted statements.
-- A failure between them destroys the attachment rows while leaving the message. Add the
-- cascade so the code can delete one row and let the database guarantee the rest.
--
-- Existing orphan rows would violate the FK, so purge them first -- they are already
-- unreachable dead data (their owning message is gone). Both columns are already
-- BIGINT UNSIGNED, so no MODIFY is needed (unlike 022, which had to narrow a type first).

DELETE a FROM message_attachments a
  LEFT JOIN messages m ON a.message_id = m.id
  WHERE m.id IS NULL;

ALTER TABLE `message_attachments`
  ADD CONSTRAINT `MessageAttachmentMessageID` FOREIGN KEY (`message_id`)
      REFERENCES `messages` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;
```

- [ ] **Step 3: Count the orphans before applying**

Knowing the number matters: a large count means the bridge has been leaking rows and is worth reporting.

```bash
pipenv run python -c "
from bot.store import mydb
c = mydb.cursor()
c.execute('SELECT COUNT(*) FROM message_attachments a LEFT JOIN messages m ON a.message_id = m.id WHERE m.id IS NULL')
print('orphans:', c.fetchone()[0])
c.close()
"
```

Record the number in the commit message.

- [ ] **Step 4: Apply the migration to the test database**

Use the project's normal migration path. If unsure how migrations are applied here, check `deploy.sh` and `migrations/` for a runner before inventing one — **do not** hand-pipe SQL into production.

- [ ] **Step 5: Verify the constraint exists**

```bash
pipenv run python -c "
from bot.store import mydb
c = mydb.cursor()
c.execute('''SELECT CONSTRAINT_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
             WHERE CONSTRAINT_NAME = 'MessageAttachmentMessageID' ''')
print(c.fetchall())
c.close()
"
```

Expected: `[('MessageAttachmentMessageID', 'CASCADE')]`

- [ ] **Step 6: Commit**

```bash
git add migrations/037_message_attachment_fk.sql
git commit -m "fix(db): FK message_attachments -> messages with ON DELETE CASCADE (T1)"
```

---

## Task 2: `delete_message` — one statement behind the cascade

**Files:**
- Modify: `bot/store/mysqldb.py:662-674`
- Test: `tests/test_message_delete_cascade.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_message_delete_cascade.py`:

```python
"""T1: message deletion cascades to attachments via the FK (migration 037).

LIVE MySQL (puzzlebot_test) -- follows the live-DB convention of
tests/test_tag_edges.py. The cascade is a database guarantee, so it cannot be
mocked: the point of the test is that the DATABASE removes the attachment rows,
not that Python issued a second DELETE.
"""

from bot.store import MessageDb, mydb


def _insert_message(message_id):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO messages (id, channel_id, guild_id, author_id, author_name, "
        "kind, content, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(3))",
        (message_id, 900001, 900002, 900003, "cascade-test", 0, "hello"),
    )
    cur.close()


def _insert_attachment(attachment_id, message_id, local_path):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO message_attachments (id, message_id, filename, size, "
        "source_url, local_path) VALUES (%s,%s,%s,%s,%s,%s)",
        (attachment_id, message_id, "a.png", 1, "http://x/a.png", local_path),
    )
    cur.close()


def _attachment_count(message_id):
    cur = mydb.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM message_attachments WHERE message_id = %s", (message_id,)
    )
    n = cur.fetchone()[0]
    cur.close()
    return n


def test_delete_message_cascades_to_attachments_and_returns_paths():
    message_id = 99000000000000001
    try:
        _insert_message(message_id)
        # TWO attachments: one row cannot prove the delete removed "all" of them,
        # and a truncating implementation would pass with one.
        _insert_attachment(99000000000000011, message_id, "a/one.png")
        _insert_attachment(99000000000000012, message_id, "a/two.png")
        assert _attachment_count(message_id) == 2

        paths = MessageDb.delete_message(message_id)

        assert sorted(paths) == ["a/one.png", "a/two.png"]
        assert _attachment_count(message_id) == 0
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM message_attachments WHERE message_id = %s", (message_id,))
        cur.execute("DELETE FROM messages WHERE id = %s", (message_id,))
        cur.close()


def test_delete_message_leaves_other_messages_attachments_alone():
    """A cascade keyed on the wrong column would take everything with it."""
    doomed_id = 99000000000000002
    survivor_id = 99000000000000003
    try:
        _insert_message(doomed_id)
        _insert_message(survivor_id)
        _insert_attachment(99000000000000021, doomed_id, "a/doomed.png")
        _insert_attachment(99000000000000022, survivor_id, "a/survivor.png")

        MessageDb.delete_message(doomed_id)

        assert _attachment_count(doomed_id) == 0
        assert _attachment_count(survivor_id) == 1
    finally:
        cur = mydb.cursor()
        for mid in (doomed_id, survivor_id):
            cur.execute("DELETE FROM message_attachments WHERE message_id = %s", (mid,))
            cur.execute("DELETE FROM messages WHERE id = %s", (mid,))
        cur.close()
```

- [ ] **Step 2: Run it against the CURRENT code to see it pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_message_delete_cascade.py -q > /tmp/test-output.log 2>&1`

Expected: **PASS**. This is deliberate and is not a mistake — the current two-statement implementation produces the same *end state*. This test pins the observable contract so the rewrite in Step 3 cannot regress it. The half-apply it guards against is a crash *between* the two statements, which is why the real proof is the mutation in Step 5, not this run.

- [ ] **Step 3: Rewrite `delete_message`**

In `bot/store/mysqldb.py`, replace the body of `delete_message` (`:662-674`) with:

```python
    def delete_message(self, id) -> list:
        """Hard-delete a message. Returns the local_path values its attachments had
        (relative paths) so the caller can unlink the bytes.

        One statement: migration 037's FK (message_attachments.message_id ->
        messages.id ON DELETE CASCADE) removes the attachment rows, so there is no
        two-statement window in which a failure could destroy them and leave the
        message. The paths are read first because the rows are gone afterwards, and
        returned only if the DELETE succeeded -- if it raises, the caller never gets
        paths for files whose rows still exist.
        """
        rows = self._fetch_all(
            "SELECT local_path FROM message_attachments WHERE message_id=%s", (id,)
        )
        self._exec("DELETE FROM messages WHERE id=%s", (id,))
        return [r["local_path"] for r in rows if r["local_path"]]
```

Check the original's return value before you delete it: match its shape exactly (whether it filtered falsy paths, and whether it returned dicts or strings). If the original did **not** filter falsy `local_path` values, drop the `if r["local_path"]` and note the difference — a `NULL` path means a pending download, and unlinking `None` would raise in the caller.

- [ ] **Step 4: Run the test**

Run: `timeout 120 pipenv run python -m pytest tests/test_message_delete_cascade.py -q > /tmp/test-output.log 2>&1`

Expected: PASS (2 passed).

- [ ] **Step 5: Mutation-proof it**

Temporarily change the DELETE to target a non-existent id:

```python
        self._exec("DELETE FROM messages WHERE id=%s", (id + 1,))
```

Run the test. Expected: **FAIL** on `_attachment_count(message_id) == 0` — proving the assertion is driven by the cascade and not by leftover state. Restore the line by editing it back.

- [ ] **Step 6: Commit**

```bash
git add bot/store/mysqldb.py tests/test_message_delete_cascade.py
git commit -m "fix(store): delete_message is one statement behind the 037 cascade (T1)"
```

---

## Task 3: `bulk_update_edge_type` — reorder to INSERT-then-DELETE

**Files:**
- Modify: `bot/store/mysqldb.py:532-560`
- Test: `tests/test_bulk_edge_atomicity.py` (create)

**Why this is the most important task in the plan:** today the method SELECTs the contributing puzzle ids, DELETEs their `feeds`/`informs`/`none` rows, then re-INSERTs them as `new_edge`. Under `autocommit=True` each statement commits on its own, so a failure between the DELETE and the INSERT **destroys the whole group's membership** with no way to recover it. Inserting first makes the worst case "a puzzle temporarily holds both edges" — visible, and fixed by re-running the convert.

It cannot become a single `UPDATE`: migration 020's `UNIQUE (tag_group_id, puzzle_id, edge_type)` means a puzzle already holding `new_edge` alongside an old edge would violate the constraint.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bulk_edge_atomicity.py`:

```python
"""T1: bulk_update_edge_type inserts the new edges before deleting the old ones.

LIVE MySQL (puzzlebot_test) -- follows tests/test_tag_edges.py. The behaviour under
test is the ORDER of two SQL statements against a real UNIQUE constraint, which a
mock cannot reproduce.

The fixture deliberately has TWO members, one of which ALREADY holds the target edge
type. A single-member fixture, or two members neither of which holds the target edge,
passes against the old DELETE-then-INSERT code and so proves nothing.
"""

from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_tag_group():
    cur = mydb.cursor()
    cur.execute("INSERT INTO tag_group (hunt_id) VALUES (%s)", (live_hunt_id(),))
    gid = cur.lastrowid
    cur.close()
    return gid


def _insert_puzzle(name):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (live_hunt_id(), name)
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _insert_edge(tag_group_id, puzzle_id, edge_type):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s,%s,%s)",
        (tag_group_id, puzzle_id, edge_type),
    )
    cur.close()


def _edges(tag_group_id, puzzle_id):
    cur = mydb.cursor()
    cur.execute(
        "SELECT edge_type FROM tags WHERE tag_group_id=%s AND puzzle_id=%s",
        (tag_group_id, puzzle_id),
    )
    edges = sorted(r[0] for r in cur.fetchall())
    cur.close()
    return edges


def _cleanup(group_id, puzzle_ids):
    cur = mydb.cursor()
    for pid in puzzle_ids:
        cur.execute("DELETE FROM puzzles WHERE id = %s", (pid,))
    cur.execute("DELETE FROM tag_group WHERE id = %s", (group_id,))
    cur.close()


def test_conversion_keeps_every_member_and_dedupes_the_pre_existing_edge():
    gid = _insert_tag_group()
    feeder = _insert_puzzle("BulkFeeder")
    already = _insert_puzzle("BulkAlready")
    try:
        _insert_edge(gid, feeder, "feeds")
        # This member already holds the edge we are converting TO, alongside an old
        # one. It is the case that makes a naive single-UPDATE violate the UNIQUE
        # constraint, and the case the delete's edge_type<>new_edge clause protects.
        _insert_edge(gid, already, "feeds")
        _insert_edge(gid, already, "informs")

        PuzzleJsonDb.bulk_update_edge_type(gid, "informs")

        # Nobody lost their membership...
        assert _edges(gid, feeder) == ["informs"]
        # ...and the member that already had the target edge holds it exactly once.
        assert _edges(gid, already) == ["informs"]
    finally:
        _cleanup(gid, [feeder, already])


def test_conversion_leaves_the_target_self_edge_untouched():
    """bulk_re_edge re-pins the 'target' self-edge itself; this method must not
    touch it, or a converted group loses its target."""
    gid = _insert_tag_group()
    target = _insert_puzzle("BulkTarget")
    try:
        _insert_edge(gid, target, "target")

        PuzzleJsonDb.bulk_update_edge_type(gid, "informs")

        assert _edges(gid, target) == ["target"]
    finally:
        _cleanup(gid, [target])
```

- [ ] **Step 2: Run it against the current code**

Run: `timeout 120 pipenv run python -m pytest tests/test_bulk_edge_atomicity.py -q > /tmp/test-output.log 2>&1`

Expected: **PASS** on both, against the *unfixed* code — the end state is the same; only the crash window differs. These tests exist to pin the contract so the reorder cannot change behaviour. The reorder's actual proof is Step 4.

- [ ] **Step 3: Reorder the implementation**

In `bot/store/mysqldb.py`, replace `bulk_update_edge_type` (`:532-560`) with:

```python
    def bulk_update_edge_type(self, tag_group_id, new_edge):
        """Convert members' feeds/informs/none edges to new_edge for a group, without
        creating duplicate (tag_group_id, puzzle_id, new_edge) rows. The target's
        'target' self-edge is left untouched here (bulk_re_edge re-pins it).

        INSERT first, DELETE second, deliberately. Every statement autocommits on its
        own (see MySQLReconnector), so with the old DELETE-then-INSERT order a failure
        between the two destroyed the group's entire membership. In this order the
        worst case is a member briefly holding both its old edge and the new one --
        visible, harmless, and fixed by re-running the conversion.

        Not expressible as a single UPDATE: migration 020's
        UNIQUE (tag_group_id, puzzle_id, edge_type) is violated when a member already
        holds new_edge alongside an old edge -- hence also the edge_type <> new_edge
        clause on the DELETE, which stops it removing what the INSERT just wrote.
        """
        cursor = self.mydb.dict_cursor()
        try:
            # Puzzles that currently contribute (feeds/informs/none) to this group.
            cursor.execute(
                "SELECT DISTINCT puzzle_id FROM tags "
                "WHERE tag_group_id = %s AND edge_type IN ('feeds','informs','none')",
                (tag_group_id,),
            )
            pids = [r["puzzle_id"] for r in cursor.fetchall() or []]
            if pids:
                placeholders = ",".join(["%s"] * len(pids))
                cursor.executemany(
                    "INSERT INTO tags (tag_group_id, puzzle_id, edge_type) VALUES (%s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE edge_type = VALUES(edge_type)",
                    [(tag_group_id, pid, new_edge) for pid in pids],
                )
                cursor.execute(
                    f"DELETE FROM tags WHERE tag_group_id = %s "
                    f"AND edge_type IN ('feeds','informs','none') "
                    f"AND edge_type <> %s "
                    f"AND puzzle_id IN ({placeholders})",
                    (tag_group_id, new_edge, *pids),
                )
        finally:
            cursor.close()
```

Note the `self.mydb.commit()` that was on the last line is gone — it was a no-op (Task 11 removes the rest).

- [ ] **Step 4: Mutation-proof the ordering**

The tests above pin the end state; this step proves the *order*, which is the actual fix. Temporarily make the DELETE unconditional by removing the guard clause:

```python
                    f"AND edge_type IN ('feeds','informs','none') "
```

(i.e. delete the `AND edge_type <> %s` line and its `new_edge` parameter).

Run: `timeout 120 pipenv run python -m pytest tests/test_bulk_edge_atomicity.py -q > /tmp/test-output.log 2>&1`

Expected: **FAIL** — `_edges(gid, already)` comes back `[]` because the DELETE removed the `informs` row the INSERT had just written. That failure is the whole point of the clause. Restore the line and the parameter.

- [ ] **Step 5: Run the tests plus the existing edge suite**

Run: `timeout 180 pipenv run python -m pytest tests/test_bulk_edge_atomicity.py tests/test_edge_primitives.py tests/test_convert_service.py -q > /tmp/test-output.log 2>&1`

Expected: all pass. `test_edge_primitives.py` mocks `bulk_update_edge_type`, so it should be unaffected — if it fails, the signature changed and it should not have.

- [ ] **Step 6: Commit**

```bash
git add bot/store/mysqldb.py tests/test_bulk_edge_atomicity.py
git commit -m "fix(store): bulk_update_edge_type inserts before deleting (T1)

A failure between the old DELETE and re-INSERT destroyed a group's whole
membership. Inserting first makes the worst case a duplicate edge."
```

---

## Task 4: `set_positions` — collapse to one statement

**Files:**
- Modify: `bot/store/mysqldb.py:1101-1112`
- Test: `tests/test_puzzle_image_positions.py` (create)

**Why:** the method's docstring claims "a half-applied reorder cannot leave the list in an order nobody chose", but the body is a Python loop of per-row `UPDATE`s, each autocommitting separately — exactly the half-apply it disclaims. A single `CASE` statement makes the docstring true.

- [ ] **Step 1: Write the failing test**

Create `tests/test_puzzle_image_positions.py`:

```python
"""T1: set_positions writes a whole ordering in one statement.

LIVE MySQL (puzzlebot_test) -- follows tests/test_tag_edges.py.

THREE rows, scrambled. Order is a relationship between items: with one or two rows a
transposition or a truncation is indistinguishable from success, so a smaller fixture
cannot fail against a wrong implementation.
"""

import pytest

from bot.store import PuzzleImageJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_puzzle(name):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)", (live_hunt_id(), name)
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _insert_image(puzzle_id, position):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzle_images (puzzle_id, position) VALUES (%s, %s)",
        (puzzle_id, position),
    )
    iid = cur.lastrowid
    cur.close()
    return iid


def _positions(image_ids):
    cur = mydb.cursor()
    placeholders = ",".join(["%s"] * len(image_ids))
    cur.execute(
        f"SELECT id, position FROM puzzle_images WHERE id IN ({placeholders})",
        tuple(image_ids),
    )
    got = {r[0]: r[1] for r in cur.fetchall()}
    cur.close()
    return got


def _cleanup(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzle_images WHERE puzzle_id = %s", (puzzle_id,))
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()
```

The `puzzle_images` schema is in `migrations/035_puzzle_images.sql` — **read it first** and add any NOT NULL columns the inserts above are missing (they are elided here because only `puzzle_id` and `position` matter to the test).

```python
def test_set_positions_writes_the_exact_scrambled_order():
    pid = _insert_puzzle("PositionTest")
    try:
        a = _insert_image(pid, 0)
        b = _insert_image(pid, 1)
        c = _insert_image(pid, 2)

        # Reverse-ish: a genuine permutation of three, so neither a no-op nor a
        # simple swap of the first two would satisfy it.
        PuzzleImageJsonDb.set_positions([c, a, b])

        assert _positions([a, b, c]) == {c: 0, a: 1, b: 2}
    finally:
        _cleanup(pid)


def test_set_positions_with_an_empty_list_is_a_no_op():
    """An empty ordering must not build a malformed CASE/IN statement."""
    PuzzleImageJsonDb.set_positions([])


def test_set_positions_does_not_touch_other_puzzles_images():
    mine = _insert_puzzle("PositionMine")
    theirs = _insert_puzzle("PositionTheirs")
    try:
        a = _insert_image(mine, 0)
        b = _insert_image(mine, 1)
        other = _insert_image(theirs, 7)

        PuzzleImageJsonDb.set_positions([b, a])

        assert _positions([other]) == {other: 7}
    finally:
        _cleanup(mine)
        _cleanup(theirs)
```

- [ ] **Step 2: Run against current code**

Run: `timeout 120 pipenv run python -m pytest tests/test_puzzle_image_positions.py -q > /tmp/test-output.log 2>&1`

Expected: `test_set_positions_writes_the_exact_scrambled_order` and the third test **PASS**; `test_set_positions_with_an_empty_list_is_a_no_op` also passes (the current loop simply does not execute). If the first test fails, the fixture is wrong — fix the fixture, not the assertion.

- [ ] **Step 3: Collapse to one statement**

Replace `set_positions` (`:1101-1112`) with:

```python
    def set_positions(self, ordered_ids) -> None:
        """Write a whole ordering at once. Whole-list rather than per-item so a
        half-applied reorder cannot leave the list in an order nobody chose.

        Genuinely one statement, which is what makes that guarantee true: every
        statement autocommits on its own (see MySQLReconnector), so the previous
        per-row UPDATE loop committed each row separately and could leave exactly the
        half-applied ordering this docstring disclaims.
        """
        ordered_ids = list(ordered_ids)
        if not ordered_ids:
            return
        cases = " ".join("WHEN %s THEN %s" for _ in ordered_ids)
        placeholders = ",".join(["%s"] * len(ordered_ids))
        params = []
        for index, image_id in enumerate(ordered_ids):
            params.extend((image_id, index))
        params.extend(ordered_ids)
        self._exec(
            f"UPDATE puzzle_images SET position = CASE id {cases} END "
            f"WHERE id IN ({placeholders})",
            tuple(params),
        )
```

- [ ] **Step 4: Run the tests**

Run: `timeout 120 pipenv run python -m pytest tests/test_puzzle_image_positions.py -q > /tmp/test-output.log 2>&1`

Expected: 3 passed.

- [ ] **Step 5: Mutation-proof it**

Temporarily reverse the index so positions are assigned backwards:

```python
        for index, image_id in enumerate(reversed(ordered_ids)):
```

Run the tests. Expected: **FAIL** on the exact-order assertion, with the observed dict showing reversed positions. Restore.

- [ ] **Step 6: Commit**

```bash
git add bot/store/mysqldb.py tests/test_puzzle_image_positions.py
git commit -m "fix(store): set_positions is a single statement, making its docstring true (T1)"
```

---

## Task 5: `_MySQLBaseDb.commit` — log a missing row (T2)

**Files:**
- Modify: `bot/store/mysqldb.py:36-72`
- Test: `tests/test_missing_row_update_logs.py` (create)

**Why:** the generic id-addressed UPDATE never checks `rowcount`, so a write to a row that was cascade-deleted underneath a stale object vanishes silently. Log it; do **not** raise — the stale-object path is legitimate (the row is genuinely gone) and raising would abort a bang command after its Discord-side work is already done.

- [ ] **Step 1: Write the failing test**

Create `tests/test_missing_row_update_logs.py`:

```python
"""T2: an id-addressed UPDATE that matches no row is logged, not silently dropped.

LIVE MySQL (puzzlebot_test): the behaviour under test is cursor.rowcount from a real
UPDATE, which a mocked cursor would simply assert back at us.
"""

import logging

from bot.store import PuzzleJsonDb, mydb
from bot.store.puzzle_data import PuzzleData
from tests.conftest import live_hunt_id


def test_commit_to_a_deleted_row_logs_an_error(caplog):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (live_hunt_id(), "MissingRowTest"),
    )
    puzzle_id = cur.lastrowid
    cur.close()

    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    assert puzzle is not None

    # The row goes away underneath the object we are holding -- exactly what a
    # concurrent delete_round does to a bang command's snapshot.
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()

    puzzle.status = "Critical"
    with caplog.at_level(logging.ERROR, logger="bot.store.mysqldb"):
        PuzzleJsonDb.commit(puzzle)

    assert any(
        "puzzles" in r.message and str(puzzle_id) in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ), f"no ERROR log naming the table and id; got {[r.message for r in caplog.records]}"


def test_commit_to_a_live_row_logs_nothing(caplog):
    """The guard must not cry wolf on the normal path."""
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name) VALUES (%s, %s)",
        (live_hunt_id(), "LiveRowTest"),
    )
    puzzle_id = cur.lastrowid
    cur.close()
    try:
        puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        assert puzzle is not None
        puzzle.status = "Critical"

        with caplog.at_level(logging.ERROR, logger="bot.store.mysqldb"):
            PuzzleJsonDb.commit(puzzle)

        assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []
    finally:
        cur = mydb.cursor()
        cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
        cur.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_missing_row_update_logs.py -q > /tmp/test-output.log 2>&1`

Expected: `test_commit_to_a_deleted_row_logs_an_error` **FAILS** with "no ERROR log naming the table and id"; the second test passes.

- [ ] **Step 3: Add the rowcount check**

In `_MySQLBaseDb.commit`, in the `if database_id > 0:` branch, after `cursor.execute(update_stmt, data)` and **before** `cursor.close()`:

```python
            cursor.execute(update_stmt, data)
            if cursor.rowcount == 0:
                # The row is gone -- typically cascade-deleted underneath a stale
                # object (a snapshot held across a delete_round, or a web delete
                # landing mid bang-command). Deliberately a log, not a raise: that
                # path is legitimate, and raising would abort a command after its
                # Discord-side work is already done. Note MySQL also reports 0 for an
                # UPDATE that matched a row but changed no value, so this can be a
                # false positive -- another reason it is diagnostic, not fatal.
                logger.error(
                    "UPDATE on `%s` id=%s matched no row; the write was dropped",
                    self.TABLE_NAME, database_id,
                )
```

`logger` already exists at `mysqldb.py:26`.

- [ ] **Step 4: Run the tests**

Run: `timeout 120 pipenv run python -m pytest tests/test_missing_row_update_logs.py -q > /tmp/test-output.log 2>&1`

Expected: 2 passed.

- [ ] **Step 5: Mutation-proof it**

Temporarily change the condition to `if cursor.rowcount == -999:`. Run the tests. Expected: the first test **FAILS** again. Restore `== 0`.

- [ ] **Step 6: Run the full suite — this one touches every write path**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1`

Expected: ~1658 passed. If unrelated tests now emit ERROR logs, that is a *finding* — it means those tests commit objects with no backing row. Report it; do not silence the log.

- [ ] **Step 7: Commit**

```bash
git add bot/store/mysqldb.py tests/test_missing_row_update_logs.py
git commit -m "fix(store): log an id-addressed UPDATE that matches no row (T2)"
```

---

## Task 6: `PuzzleJsonDb.commit` — document the real guarantee

**Files:**
- Modify: `bot/store/mysqldb.py:562-587`

No test: this task changes only a docstring. It is a task rather than a footnote because the spec requires the false claim to go, and a comment that lies is the defect being fixed.

- [ ] **Step 1: Add the docstring**

`PuzzleJsonDb.commit` currently has none. Add:

```python
    def commit(self, puzzle, /):
        """Write the puzzle row, then reconcile its membership edges to match
        puzzle.tag_edges.

        NOT atomic, and cannot be made so without a transaction: the base-row UPDATE
        and the tag INSERT/DELETEs are separate autocommitting statements (see
        MySQLReconnector -- autocommit is on, so commit() is a no-op). A failure
        between them leaves the row updated and the edges stale.

        That is tolerated deliberately. The failure mode is self-correcting -- the
        next commit of the same puzzle re-diffs the edges and converges -- unlike
        bulk_update_edge_type, whose old ordering could destroy membership outright
        and which was reordered rather than left to chance.

        Note tags/tag_edges are in SPECIAL_ATTR, so the generic UPDATE skips them:
        the diff below is the ONLY thing that persists membership. Mutating
        puzzle.tags without puzzle.tag_edges (via add_edge/remove_edge) writes
        nothing.
        """
```

- [ ] **Step 2: Verify nothing else claims atomicity here**

Run: `grep -n "atomic" bot/store/mysqldb.py bot/store/__init__.py`

Expected: only the honest mentions added by this plan. Any remaining claim that the code does not deliver must be corrected.

- [ ] **Step 3: Gates and commit**

```bash
pipenv run ruff check
pipenv run basedpyright bot/
git add bot/store/mysqldb.py
git commit -m "docs(store): state PuzzleJsonDb.commit's real (non-atomic) guarantee (T1)"
```

---

## Task 7: `with_puzzle_mutex` re-reads under the lock (H4)

**Files:**
- Modify: `bot/cogs/puzzles.py:63-98`
- Test: `tests/test_bang_edge_atomicity.py` (create)

**Why:** `cog_before_invoke` (`:197`) loads puzzle/tag_group/hunt into `self.environment` **before** the decorator takes the per-puzzle lock. Services commit the object they are handed (`PuzzleJsonDb.commit(puzzle)`, ~20 sites in `puzzle_service.py`) and the generic commit writes every field by name — so a snapshot taken before the lock clobbers whatever a concurrent writer changed in the meantime. `!note` racing `!status` loses the note.

The web layer already does this correctly (`bot/web/actions.py:584`: lock, *then* read). This task gives the bang edge the same shape.

- [ ] **Step 1: Write the failing test**

The test targets the decorator directly rather than driving two real commands. That is deliberate: `!note` and `!status` fan out through `PuzzleService` into the Discord notifier and Google Sheets, and a test that mocks all of that ends up asserting against its own mocks. The defect is precisely "the command body sees a row read before the lock", and the decorator is where that is decided. The full two-command race is covered end to end by `scripts/diagnose_atomicity.py` case 5 (Task 12), where the real service is available.

`pytest.ini` sets `asyncio_mode = auto` and `pytest-asyncio` 1.4.0 is in the Pipfile, so `@pytest.mark.asyncio` works as written.

Create `tests/test_bang_edge_atomicity.py`:

```python
"""H4: the bang edge reads its puzzle INSIDE the per-puzzle lock.

cog_before_invoke resolves the channel's puzzle and snapshots it BEFORE
with_puzzle_mutex acquires the lock, and services commit the object they are handed
field by field -- so a command acting on that snapshot silently clobbers whatever a
concurrent writer changed while it waited.

LIVE MySQL (puzzlebot_test) -- follows tests/test_tag_edges.py. The whole point is
what a real re-read returns, so PuzzleJsonDb is deliberately NOT patched here.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.puzzles import Puzzles, with_puzzle_mutex
from bot.core.locks import _PUZZLE_LOCKS
from bot.store import PuzzleJsonDb, mydb
from tests.conftest import live_hunt_id


def _insert_puzzle(name="H4Test", status=""):
    cur = mydb.cursor()
    cur.execute(
        "INSERT INTO puzzles (hunt_id, name, status) VALUES (%s, %s, %s)",
        (live_hunt_id(), name, status),
    )
    pid = cur.lastrowid
    cur.close()
    return pid


def _set_status(puzzle_id, status):
    """A concurrent writer -- a web action or another bang command."""
    cur = mydb.cursor()
    cur.execute("UPDATE puzzles SET status = %s WHERE id = %s", (status, puzzle_id))
    cur.close()


def _delete_puzzle(puzzle_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM puzzles WHERE id = %s", (puzzle_id,))
    cur.close()


def _make_ctx(message_id):
    ctx = MagicMock()
    ctx.message.id = message_id
    ctx.send = AsyncMock()
    return ctx


def _seed_environment(cog, ctx, puzzle):
    cog.environment[ctx.message.id] = {
        "channel_type": "Puzzle",
        "hunt": None,
        "tag_group": None,
        "puzzle": puzzle,
        "guild": None,
        "guild_data": None,
        "gsheet_cog": None,
    }


@pytest.mark.asyncio
async def test_command_body_sees_the_row_as_it_is_when_the_lock_is_taken():
    puzzle_id = _insert_puzzle(status="")
    try:
        cog = Puzzles(MagicMock())
        seen = {}

        @with_puzzle_mutex(wait=True)
        async def probe(self, ctx):
            seen["status"] = self.environment[ctx.message.id]["puzzle"].status

        ctx = _make_ctx(1001)
        # The pre-lock snapshot, exactly as cog_before_invoke would leave it.
        _seed_environment(cog, ctx, PuzzleJsonDb.get_by_attr(id=puzzle_id))

        # Someone else writes the row after that snapshot and before we run --
        # the window the per-puzzle lock was supposed to close.
        _set_status(puzzle_id, "Critical")

        await probe(cog, ctx)

        assert seen["status"] == "Critical", (
            "the command body acted on a snapshot read before the lock"
        )
    finally:
        _delete_puzzle(puzzle_id)


@pytest.mark.asyncio
async def test_a_waiting_command_re_reads_what_the_holder_wrote():
    """The realistic ordering: B blocks on A's lock, and must see A's write."""
    puzzle_id = _insert_puzzle(status="")
    try:
        cog = Puzzles(MagicMock())
        seen = {}
        released = asyncio.Event()

        @with_puzzle_mutex(wait=True)
        async def holder(self, ctx):
            _set_status(puzzle_id, "Critical")   # A's write, under the lock
            await asyncio.sleep(0)

        @with_puzzle_mutex(wait=True)
        async def waiter(self, ctx):
            seen["status"] = self.environment[ctx.message.id]["puzzle"].status

        ctx_a, ctx_b = _make_ctx(2001), _make_ctx(2002)
        # BOTH snapshots are taken up front -- distinct ctx.message.id values,
        # because self.environment is keyed on it and a shared id would make the
        # two commands overwrite each other's entry and prove nothing.
        snapshot = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        _seed_environment(cog, ctx_a, snapshot)
        _seed_environment(cog, ctx_b, PuzzleJsonDb.get_by_attr(id=puzzle_id))

        await asyncio.gather(holder(cog, ctx_a), waiter(cog, ctx_b))

        assert seen["status"] == "Critical", (
            "the waiting command used its pre-lock snapshot and lost A's write"
        )
    finally:
        _PUZZLE_LOCKS.pop(puzzle_id, None)
        _delete_puzzle(puzzle_id)
```

Note the `_PUZZLE_LOCKS.pop` in the second teardown: `_PUZZLE_LOCKS` is a module-level `defaultdict` that persists across tests, and leaving a lock keyed on a now-deleted id is harmless but untidy. Do **not** pop it while a task might still hold it.

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_bang_edge_atomicity.py -q > /tmp/test-output.log 2>&1`

Expected: **both tests FAIL**, with `seen["status"] == ""` — the command body saw the pre-lock snapshot.

If either passes against the unfixed code, the test is not reproducing the defect and must be fixed before you continue. **A green test here is a broken test.**

- [ ] **Step 3: Make the decorator re-read under the lock**

In `bot/cogs/puzzles.py`, add a helper next to `with_puzzle_mutex` and call it after each acquisition:

```python
def _refresh_environment(self, ctx, puzzle_id):
    """Re-read the command's puzzle/group/hunt from the database, now that the
    per-puzzle lock is held.

    cog_before_invoke resolves which puzzle a channel maps to -- which is how we
    know which lock to take -- but its snapshot is read BEFORE the lock. Services
    commit the object they are given, field by field, so acting on that snapshot
    clobbers whatever a concurrent bang or web write changed while we were waiting
    (H4). Re-reading here gives the bang edge the same lock-then-read shape the web
    handlers already have (bot/web/actions.py).
    """
    env = self.environment.get(ctx.message.id)
    if env is None:
        return
    puzzle = PuzzleJsonDb.get_by_attr(id=puzzle_id)
    if puzzle is None:
        # Deleted while we waited for the lock. Leave the stale object in place;
        # the command's own not-found handling deals with it, and T2's rowcount
        # log will record any write that lands on the missing row.
        return
    env["puzzle"] = puzzle
    if env.get("tag_group") is not None:
        refreshed_group = TagGroupJsonDb.get_by_attr(id=env["tag_group"].id)
        if refreshed_group is not None:
            env["tag_group"] = refreshed_group
    if env.get("hunt") is not None:
        refreshed_hunt = HuntJsonDb.get_by_attr(id=env["hunt"].id)
        if refreshed_hunt is not None:
            env["hunt"] = refreshed_hunt
```

Then in `with_puzzle_mutex`'s wrapper, call it immediately after each acquisition — **both** paths:

```python
            if wait:
                if lock.locked():
                    await ctx.send("⏳ Hold your horses!  Another command is running for this puzzle, I'll get to you in a second.")
                async with lock:
                    self._refresh_environment(ctx, puzzle.id)
                    return await func(self, ctx, *args, **kwargs)

            try:
                await asyncio.wait_for(lock.acquire(), timeout=0.001)
            except asyncio.TimeoutError:
                await ctx.send("⏳ Woah there, I'm busy running another command on that puzzle. Patience is a virtue, try again later.")
                return

            try:
                self._refresh_environment(ctx, puzzle.id)
                return await func(self, ctx, *args, **kwargs)
            finally:
                lock.release()
```

`puzzle.id` is read from the pre-lock snapshot deliberately — the *identity* of the puzzle is what the channel resolution established, and that does not change while we wait. Only its field values do.

Since `_refresh_environment` is a method on the cog, define it inside the `Puzzles` class, not at module level next to the decorator.

- [ ] **Step 4: Run the test**

Run: `timeout 120 pipenv run python -m pytest tests/test_bang_edge_atomicity.py -q > /tmp/test-output.log 2>&1`

Expected: PASS.

- [ ] **Step 5: Mutation-proof it**

Temporarily comment out the `self._refresh_environment(ctx, puzzle.id)` call in the `wait=True` branch. Run the test. Expected: **FAIL** with a lost field, exactly as in Step 2. Restore it.

- [ ] **Step 6: Full suite — this changes every bang command's data source**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1`

Expected: ~1658 + your new tests, all passing. Failures here are likely tests that mock `PuzzleJsonDb.get_by_attr` and now see an extra call. Fix the *tests'* expectations only where the extra read is genuinely correct; if a test breaks because the refreshed object is a `MagicMock` with no real fields, that is a test-double problem, not a reason to skip the refresh.

- [ ] **Step 7: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_bang_edge_atomicity.py
git commit -m "fix(cogs): re-read puzzle/group/hunt inside the per-puzzle lock (H4)

The bang edge snapshotted before acquiring the lock, so a stale row clobbered
a concurrent writer's field. Matches the web edge's lock-then-read shape."
```

---

## Task 8: Drop the blanket after-invoke commit (H4)

**Files:**
- Modify: `bot/cogs/puzzles.py:286-297`
- Test: `tests/test_bang_edge_atomicity.py` (extend)

**Why:** `cog_after_invoke` commits puzzle, tag_group, hunt and guild_data after **every** command, whether or not it changed anything — and it runs inside discord.py's `finally`, so a command that mutates and then raises persists the partial mutation. Services already commit their own writes.

A full mutation sweep of the cog finds three in-place mutation sites, none of which needs a replacement commit: `hunt.solved_sheet_id` (`:747`) is already followed by an explicit `HuntJsonDb.commit(hunt)` at `:749`; the other two are in `!add_tag`/`!remove_tag`, which Task 9 deletes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bang_edge_atomicity.py`:

It reuses the helpers already defined in that file by Task 7 (`_insert_puzzle`, `_delete_puzzle`, `_make_ctx`, `_seed_environment`).

Calling `cog_after_invoke` directly is the honest trigger: discord.py invokes it from a `finally` after every command, so a test that mutates the cached object and then calls it reproduces exactly what the framework does — without needing to push a deliberately-raising command through the real dispatch machinery.

```python
@pytest.mark.asyncio
async def test_after_invoke_does_not_persist_an_uncommitted_mutation():
    """discord.py calls cog_after_invoke from a `finally`, so the blanket commit
    persisted the half-finished state of a command that raised partway through --
    and rewrote every field of a stale snapshot on the way past."""
    puzzle_id = _insert_puzzle(status="")
    try:
        cog = Puzzles(MagicMock())
        ctx = _make_ctx(3001)
        _seed_environment(cog, ctx, PuzzleJsonDb.get_by_attr(id=puzzle_id))

        # A mutation no command ever committed -- what a command that blew up
        # halfway leaves behind in the cached object.
        cog.environment[ctx.message.id]["puzzle"].status = "Critical"

        await cog.cog_after_invoke(ctx)

        fresh = PuzzleJsonDb.get_by_attr(id=puzzle_id)
        assert fresh is not None
        assert fresh.status != "Critical", (
            "after_invoke persisted a mutation no command committed"
        )
    finally:
        _delete_puzzle(puzzle_id)


@pytest.mark.asyncio
async def test_after_invoke_still_clears_the_cached_environment():
    """The cleanup is the method's remaining job -- a leak here grows unboundedly
    over a hunt, one entry per command."""
    puzzle_id = _insert_puzzle()
    try:
        cog = Puzzles(MagicMock())
        ctx = _make_ctx(3002)
        _seed_environment(cog, ctx, PuzzleJsonDb.get_by_attr(id=puzzle_id))

        await cog.cog_after_invoke(ctx)

        assert ctx.message.id not in cog.environment
    finally:
        _delete_puzzle(puzzle_id)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_bang_edge_atomicity.py -q > /tmp/test-output.log 2>&1`

Expected: the new test **FAILS** — `fresh.status == "Critical"`, because the blanket commit wrote it.

- [ ] **Step 3: Remove the commits**

Replace `cog_after_invoke` (`:286-297`) with:

```python
    async def cog_after_invoke(self, ctx):
        """Drop the command's cached environment.

        Deliberately does NOT commit. It used to write puzzle, tag_group, hunt and
        guild_data after every command, whether or not anything changed -- which
        re-wrote every field of a possibly stale snapshot (H4), and, because
        discord.py calls this from a `finally`, persisted the partial mutation of a
        command that raised partway through. Every mutating path commits its own
        write: the service does it for command flows, and the one in-cog mutation
        (hunt.solved_sheet_id) commits explicitly at its own call site.
        """
        self.environment.pop(ctx.message.id, None)
```

- [ ] **Step 4: Run the test**

Run: `timeout 120 pipenv run python -m pytest tests/test_bang_edge_atomicity.py -q > /tmp/test-output.log 2>&1`

Expected: PASS.

- [ ] **Step 5: Mutation-proof it**

Temporarily restore one line — `PuzzleJsonDb.commit(self.get_puzzle(ctx))` — inside `cog_after_invoke`. Run the test. Expected: **FAIL**. Remove it again.

- [ ] **Step 6: Full suite — this is the highest-risk change in the plan**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1`

Any test that fails here is telling you about a command that relied on the blanket commit — i.e. a real persistence path this change would break. **For each failure, find the command and check whether it mutates without committing.** If one does, the correct fix is an explicit commit at that command's own call site, inside the lock — and you must report it, because the spec's sweep found only three mutation sites and a fourth means the sweep was incomplete.

Do not make a failing test pass by restoring the blanket commit.

- [ ] **Step 7: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_bang_edge_atomicity.py
git commit -m "fix(cogs): drop the blanket after-invoke commit (H4)

It rewrote every field of a stale snapshot after every command, and ran from
discord.py's finally, persisting the partial mutation of a command that raised."
```

---

## Task 9: Delete `!add_tag` and `!remove_tag`

**Files:**
- Modify: `bot/cogs/puzzles.py:1347-1388` (delete both commands)
- Modify: `docs/commands.md:209-210` (delete both rows)

**Why:** both are troubleshooting-only commands that **already persist nothing**. `MySQLPuzzleJsonDb.SPECIAL_ATTR` (`mysqldb.py:343`) lists `tags` and `tag_edges`, so the generic UPDATE skips them; membership is persisted only by the tag-diff in `PuzzleJsonDb.commit`, which reads **`tag_edges`**. Both commands mutate only the plain `tags` list and never `tag_edges`, so nothing is written or removed. The supported paths (`!feeds`, `!informs`, `!add_to`, `!remove_from`) work correctly, and a manual tag-id escape hatch that bypasses the edge model is a liability rather than a fallback.

- [ ] **Step 1: Confirm nothing depends on them**

Run: `grep -rn "add_tag\|remove_tag" bot/ tests/ docs/ web/ --include=* | grep -v "^docs/reviews\|^docs/superpowers"`

Expected hits, and only these:
- `bot/cogs/puzzles.py` — the two commands being deleted, plus `!list_tags`'s help text referring to them.
- `bot/store/puzzle_data.py:96` — `PuzzleData.add_tag`, a **different thing**, called by `bot/core/puzzle_service.py:1024,2024`. **Keep it.**
- `docs/commands.md:209-210` — the two rows being deleted.

If anything else turns up, stop and report it.

- [ ] **Step 2: Delete the two commands**

Remove `bot/cogs/puzzles.py:1347-1388` in full — both `@commands.command()` blocks, decorators included. Keep `!list_tags` immediately above (`:1311`), which is read-only and still useful. Check whether `!list_tags`'s help text mentions the deleted commands and reword it if so.

- [ ] **Step 3: Delete the two documentation rows**

Remove these lines from `docs/commands.md`:

```
| `!add_tag` | | `tag_id` | puzzle channel | organisers/admins | Manually add a tag ID (prefer `!feeds`/`!informs`). |
| `!remove_tag` | | `tag_id` | puzzle channel | organisers/admins | Manually remove a tag ID (prefer `!remove_from`). |
```

- [ ] **Step 4: Verify the commands are gone and `PuzzleData.add_tag` survives**

```bash
pipenv run python -c "
from bot.cogs.puzzles import Puzzles
names = {c.name for c in Puzzles.__cog_commands__}
assert 'add_tag' not in names, 'add_tag still registered'
assert 'remove_tag' not in names, 'remove_tag still registered'
assert 'list_tags' in names, 'list_tags was removed by mistake'
print('commands ok')
"
pipenv run python -c "
from bot.store.puzzle_data import PuzzleData
assert hasattr(PuzzleData, 'add_tag')
print('PuzzleData.add_tag intact')
"
```

Expected: `commands ok` and `PuzzleData.add_tag intact`.

If `Puzzles.__cog_commands__` is not populated at class level in this discord.py version, instantiate the cog with a mocked bot as the existing cog tests do.

- [ ] **Step 5: Full suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1`

Expected: all pass. No test referenced either command.

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py docs/commands.md
git commit -m "refactor(cogs): delete !add_tag and !remove_tag

Both mutated puzzle.tags without tag_edges, which SPECIAL_ATTR excludes from the
generic UPDATE and the tag-diff never sees -- so neither has persisted anything
since the multi-edge work. The supported edge commands cover the use case."
```

---

## Task 10: Lock the background evidence link paths (M5)

**Files:**
- Modify: `bot/core/puzzle_service.py` — `link_informs_for_evidence` and `unlink_informs_for_evidence`
- Test: `tests/test_evidence_link_locking.py` (create)

**Why:** both are launched as bare `asyncio.create_task` from `bot/web/actions.py:2212,2215`, so they run concurrently with anything else and commit a puzzle with no lock held. The bidirectional tag-sync in `PuzzleJsonDb.commit` can silently revert an `informs` edge that a concurrent locked command just wrote.

**Lock discipline:** `PuzzleService` is deliberately lock-free so an edge holding the lock can call it without deadlocking (`bot/core/locks.py:8-11`). These two methods are the exception, and the reason they can be is that **their only callers are bare `create_task` launches that hold no lock**. Before adding the lock, re-verify that: if any caller already holds the source puzzle's lock, this change deadlocks the bot permanently — `asyncio.Lock` is not reentrant and never times out.

- [ ] **Step 1: Verify no caller holds the lock**

Run: `grep -rn "link_informs_for_evidence\|unlink_informs_for_evidence" bot/`

Expected: definitions in `puzzle_service.py`, `*_safe` wrappers if present, and `create_task` launches in `bot/web/actions.py`. Read each call site and confirm none is inside an `async with puzzle_lock(...)` block for the **source** puzzle. If one is, **stop and report** — do not proceed.

- [ ] **Step 2: Write the failing test**

Create `tests/test_evidence_link_locking.py`:

```python
"""M5: the background evidence link/unlink paths hold the source puzzle's lock.

They are launched as bare create_task from the web layer, so without the lock they
commit concurrently with a locked bang command, and the bidirectional tag-sync in
PuzzleJsonDb.commit can silently revert the informs edge that command just wrote.

The stores are patched to return None so the methods return as soon as they get
past the lock: the behaviour under test is purely "does it wait", and resolving a
real group/source would only add ways for the test to fail for other reasons.
Patch targets are the names as looked up in bot.core.puzzle_service, not where they
are defined -- patching the source module would leave the service's own reference
untouched and the mock would never engage.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from bot.core.locks import _PUZZLE_LOCKS
from bot.core.puzzle_service import PuzzleService

SOURCE_ID = 424242
TARGET_ID = 424243


def _service():
    return PuzzleService(MagicMock(), notifier=MagicMock())


async def _assert_blocks_until_released(coro_factory):
    """Launch the coroutine while the source lock is held; it must not progress."""
    service_call = None
    async with _PUZZLE_LOCKS[SOURCE_ID]:
        service_call = asyncio.create_task(coro_factory())
        # Two yields, not one: a single yield may not be enough for the task to
        # reach its first real await, which would make `not done` true for the
        # wrong reason.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not service_call.done(), (
            "the background link ran while the source puzzle's lock was held"
        )

    await asyncio.wait_for(service_call, timeout=1)


@pytest.mark.asyncio
async def test_link_informs_waits_for_the_source_puzzle_lock():
    service = _service()
    hunt = MagicMock()
    try:
        with patch("bot.core.puzzle_service.TagGroupJsonDb") as GroupDb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as PuzzleDb:
            GroupDb.get_by_attr.return_value = None
            PuzzleDb.get_by_attr.return_value = None
            await _assert_blocks_until_released(
                lambda: service.link_informs_for_evidence(hunt, SOURCE_ID, TARGET_ID)
            )
    finally:
        _PUZZLE_LOCKS.pop(SOURCE_ID, None)


@pytest.mark.asyncio
async def test_unlink_informs_waits_for_the_source_puzzle_lock():
    service = _service()
    hunt = MagicMock()
    try:
        with patch("bot.core.puzzle_service.TagGroupJsonDb") as GroupDb, \
             patch("bot.core.puzzle_service.PuzzleJsonDb") as PuzzleDb, \
             patch("bot.core.puzzle_service.EvidenceJsonDb") as EvidenceDb:
            EvidenceDb.get_all.return_value = []
            GroupDb.get_by_attr.return_value = None
            PuzzleDb.get_by_attr.return_value = None
            await _assert_blocks_until_released(
                lambda: service.unlink_informs_for_evidence(hunt, SOURCE_ID, TARGET_ID)
            )
    finally:
        _PUZZLE_LOCKS.pop(SOURCE_ID, None)


@pytest.mark.asyncio
async def test_a_sourceless_evidence_item_returns_without_locking():
    """source_puzzle_id is None for hunt-level evidence -- there is no puzzle to
    lock on, so the early return must stay OUTSIDE the lock."""
    service = _service()
    await asyncio.wait_for(
        service.link_informs_for_evidence(MagicMock(), None, TARGET_ID), timeout=1
    )
    assert None not in _PUZZLE_LOCKS, "a lock was created for a sourceless item"
```

The third test is what stops the obvious wrong fix — wrapping the whole method body, early return included, so `puzzle_lock(None)` creates a junk lock keyed on `None`.

- [ ] **Step 3: Run it to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_link_locking.py -q > /tmp/test-output.log 2>&1`

Expected: the first two tests **FAIL** on `assert not service_call.done()` — the unlocked implementation runs straight through. The third (sourceless) test passes both before and after; it exists to constrain the fix, not to detect the bug.

- [ ] **Step 4: Add the lock**

In both methods, keep the existing falsy-id early return **outside** the lock — a sourceless (hunt-level) evidence item has no puzzle id to lock on — then wrap the rest:

```python
    async def link_informs_for_evidence(self, hunt, source_puzzle_id, target_puzzle_id):
        """... (keep the existing docstring, and add:)

        Holds the source puzzle's lock for the whole body (M5). This method is the
        documented exception to PuzzleService being lock-free: it is only ever
        launched as a bare create_task from the web layer, so no caller already holds
        that lock. asyncio.Lock is not reentrant -- if a future caller acquires the
        lock before calling this, it deadlocks permanently.
        """
        if not (source_puzzle_id and target_puzzle_id):
            return
        async with puzzle_lock(source_puzzle_id):
            ...  # the existing body, unchanged
```

Apply the same shape to `unlink_informs_for_evidence`. Add `puzzle_lock` to the imports from `bot.core.locks` if it is not already there.

- [ ] **Step 5: Run the test**

Run: `timeout 120 pipenv run python -m pytest tests/test_evidence_link_locking.py -q > /tmp/test-output.log 2>&1`

Expected: PASS.

- [ ] **Step 6: Mutation-proof it**

Temporarily de-indent the body back out of the `async with`. Run the test. Expected: **FAIL** on `assert not task.done()`. Restore.

- [ ] **Step 7: Full suite, and watch the clock**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1`

Expected: all pass, in roughly the usual ~12 seconds. **A timeout (exit 124) here most likely means a deadlock**, not slowness — re-check Step 1. Isolate per file under `timeout` to find the wedged test.

- [ ] **Step 8: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_evidence_link_locking.py
git commit -m "fix(service): hold the source puzzle's lock in the evidence link paths (M5)"
```

---

## Task 11: Remove the no-op `commit()` calls

**Files:**
- Modify: `bot/store/mysqldb.py` (33 call sites, fewer if earlier tasks removed some)
- Modify: `bot/store/__init__.py:88-91`
- Modify: `bot/web/data.py:81-92`

**Deliberately last**, so this mechanical sweep does not collide with the behaviour changes earlier tasks make to the same file. **Pure deletion — no behaviour change.** Every `MySQLReconnector` sets `autocommit = True` in both `_connect` (`:65`) and `_ensure` (`:77`), and the dashboard pool sets it at `bot/web/data.py:45`, so none of these calls does anything. They go because a `commit()` call signals a durability boundary that does not exist, at 33 sites.

- [ ] **Step 1: Record the baseline**

```bash
grep -c "self.mydb.commit()" bot/store/mysqldb.py
```

Note the number (33 before Tasks 3 and 4, which each remove one).

- [ ] **Step 2: Prove there is no non-autocommit path**

```bash
grep -rn "autocommit" bot/ --include=*.py
```

Expected: `bot/store/__init__.py:65,77` and `bot/web/data.py:45`, all setting `True`. **If any path sets it False, stop** — the deletion is then not behaviour-preserving.

- [ ] **Step 3: Delete the DAO-level calls**

Remove every `self.mydb.commit()` line in `bot/store/mysqldb.py`. Do not remove `MySQLReconnector.commit` itself, and do not touch `mydb.commit()` calls in `tests/` or `scripts/` in this task.

Then check for now-empty `try`/`finally` blocks or functions whose body was only that call — ruff will catch syntax problems, but not a function that has become a silent no-op. If one has, report it.

- [ ] **Step 4: Fix the `MySQLReconnector.commit` comment**

In `bot/store/__init__.py`, replace the comment at `:89`:

```python
    def commit(self):
        # No-op while autocommit is on (set in _connect and _ensure): every statement
        # commits itself, so there is no open transaction for this to close. Kept
        # because it is part of the connection surface a real transaction would need.
        #
        # Atomicity in this codebase comes from STATEMENT SHAPE, not from this call --
        # multi-statement operations are ordered so a mid-op failure is recoverable
        # (bulk_update_edge_type), collapsed into one statement (set_positions), or
        # backed by an FK cascade (delete_message). If a future operation genuinely
        # needs a transaction, run it on a DEDICATED autocommit=False connection: this
        # one is shared by several DAOs on the event loop, so an open transaction here
        # would span their interleaved statements.
        self._ensure()
        return self._conn.commit()
```

- [ ] **Step 5: Fix `_run_write` in `bot/web/data.py`**

```python
def _run_write(sql, params=()):
    """Run a single writing statement on a pooled connection.

    No commit: the pool is created with autocommit=True (see _get_pool), so the
    statement commits itself and an explicit commit() would be a no-op.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
        finally:
            cursor.close()
    finally:
        conn.close()
```

- [ ] **Step 6: Verify the sweep is complete**

```bash
grep -rn "mydb.commit()\|conn.commit()" bot/ --include=*.py
```

Expected: exactly one hit — `self._conn.commit()` inside `MySQLReconnector.commit` in `bot/store/__init__.py`.

- [ ] **Step 7: Full suite and gates**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
```

Expected: all green, identical pass count to before this task. **Any behaviour change here is a bug in the sweep** — a deleted line that was not a `commit()`, most likely.

- [ ] **Step 8: Commit**

```bash
git add bot/store/mysqldb.py bot/store/__init__.py bot/web/data.py
git commit -m "refactor(store): remove the no-op commit() calls (T1)

autocommit is on for every connection, so each of these committed nothing while
signalling a durability boundary that does not exist. MySQLReconnector.commit is
kept and now documents where atomicity actually comes from."
```

---

## Task 12: `scripts/diagnose_atomicity.py` — the hand test script

**Files:**
- Create: `scripts/diagnose_atomicity.py`

**Why:** the pytest suite proves the code's shape. This proves the deployed **database** agrees — that the migration applied, the FK actually cascades, and two genuinely concurrent writers under the real locks do not lose an update.

Follow `scripts/diagnose_addtable.py` exactly for style: module docstring stating purpose and the run line, repo-root `sys.path` bootstrap so `import bot` works from anywhere, PASS/FAIL printed per case, `--keep` to skip cleanup.

- [ ] **Step 1: Read the reference script**

Read `scripts/diagnose_addtable.py` in full — particularly its header bootstrap and its per-case PASS/FAIL reporting.

- [ ] **Step 2: Write the script**

```python
"""Verify the atomicity fixes against a real database.

The pytest suite proves the code's shape; this proves the DATABASE agrees -- that
migration 037 applied, that the FK genuinely cascades, and that two concurrent
writers under the real locks do not lose an update.

Run from the repo root, with the bot's environment:

    pipenv run python scripts/diagnose_atomicity.py [--keep] [--force]

Every fixture row it creates is named or prefixed DIAG- and deleted in a finally
block, even on failure. --keep skips the cleanup for post-mortem inspection.

REFUSES TO RUN against a database whose name is not the test database unless
--force is given: cases 3 and 5 create and mutate real rows.
"""
```

Then, in order:

1. **Bootstrap** — repo root onto `sys.path`, `os.chdir(_REPO_ROOT)`, as `diagnose_addtable.py` does.
2. **Guard** — read the database name from `bot.utils.config`; if it is not the test database (`puzzlebot_test`) and `--force` was not passed, print the name and exit non-zero.
3. **A `case(name)` helper** that runs a callable, catches exceptions, and prints `PASS`/`FAIL <detail>`/`SKIP <reason>`. A case that cannot run must print SKIP with a reason — **never** a silent PASS.
4. **The six cases**, each creating its own fixtures and cleaning up in `finally` (unless `--keep`):
   - **Case 1 — schema.** Migration 037 is recorded as applied (check however this repo records migrations); the `MessageAttachmentMessageID` constraint exists with `DELETE_RULE = 'CASCADE'`; zero orphan `message_attachments` rows.
   - **Case 2 — FK cascade.** Insert a `DIAG-` message plus two attachments; `MessageDb.delete_message`; assert both attachment rows are gone and the returned paths match.
   - **Case 3 — edge conversion.** A `DIAG-` group with two members, one already holding the target edge; `bulk_update_edge_type`; assert every member still holds exactly one edge and nobody lost membership.
   - **Case 4 — positions.** Three `DIAG-` image rows in a scrambled order; `set_positions`; assert the readback order matches exactly.
   - **Case 5 — concurrent writers.** Two `asyncio` tasks against one `DIAG-` puzzle through the **real** `puzzle_lock` and the **real** service, one writing a note and one a status; assert both survive. This is the case that exercises the H4 fix end to end.
   - **Case 6 — missing row.** Commit an object whose row has been deleted; assert the ERROR log fired (attach a `logging.Handler` to `bot.store.mysqldb`) and that nothing was written.
5. **A final summary line** — counts of PASS/FAIL/SKIP — and a non-zero exit if anything failed, so it can be used as a gate.

**No Discord and no Google Sheets calls.** Where a service method fans out to a notifier, pass a no-op notifier. If a case cannot avoid the network, make it SKIP with that as the reason.

- [ ] **Step 3: Run it against the test database**

Run: `timeout 300 pipenv run python scripts/diagnose_atomicity.py > /tmp/diagnose.log 2>&1`

Expected: every case PASS, and a summary line. Read `/tmp/diagnose.log`.

- [ ] **Step 4: Prove the guard works**

Temporarily change the expected test-database name in the guard to something else, run the script, and confirm it **refuses to run** and exits non-zero. Restore.

A safety guard nobody has ever seen fire is not known to work.

- [ ] **Step 5: Prove the cases can fail**

Pick **case 3** and temporarily break `bulk_update_edge_type` again (remove the `AND edge_type <> %s` clause, as in Task 3 Step 4). Run the script. Expected: case 3 reports **FAIL** with detail, the summary reports one failure, and the exit code is non-zero. Restore.

A script that prints PASS unconditionally is worse than no script.

- [ ] **Step 6: Confirm cleanup left nothing behind**

```bash
pipenv run python -c "
from bot.store import mydb
c = mydb.cursor()
for table, col in (('puzzles','name'), ('tag_group','name')):
    try:
        c.execute(f\"SELECT COUNT(*) FROM {table} WHERE {col} LIKE 'DIAG-%'\")
        print(table, c.fetchone()[0])
    except Exception as e:
        print(table, 'skipped:', e)
c.close()
"
```

Expected: `0` for each. Anything else means a cleanup path is missing a `finally`.

- [ ] **Step 7: Commit**

```bash
git add scripts/diagnose_atomicity.py
git commit -m "test(scripts): hand test script for the atomicity fixes"
```

---

## Final verification

- [ ] **Step 1: Full gates, from a clean tree**

```bash
git status --short
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
```

Expected: clean tree; ~1658 + new tests passing; `All checks passed`; `0 errors, 0 warnings`.

- [ ] **Step 2: Hand test script green**

```bash
timeout 300 pipenv run python scripts/diagnose_atomicity.py > /tmp/diagnose.log 2>&1
```

Expected: every case PASS, exit 0.

- [ ] **Step 3: Check the spec's acceptance criteria one by one**

Open `docs/superpowers/specs/2026-08-08-atomicity-design.md` and walk its nine acceptance criteria against what was actually built. Report any that are unmet — do not quietly mark them done.

- [ ] **Step 4: Check the migration number did not collide**

```bash
git fetch origin
git log --oneline origin/staging -- migrations/ | head -5
ls migrations/ | tail -5
```

If another branch also added a 037, renumber **before** merging. Git merges the collision cleanly and hides it.

- [ ] **Step 5: Review the whole diff**

```bash
git diff main...HEAD --stat
git diff main...HEAD
```

Confirm: no stray debug prints, no leftover mutations from the mutation-proof steps, no unrelated files. Every mutation-proof step must have been restored — grep the diff for the specific broken lines named in Tasks 2, 3, 4, 5, 7, 8, 10 and 12 to be sure none survived.

- [ ] **Step 6: Report**

State: what changed, files changed, commands run, test results, anything **not** done, and the commit hashes. If any acceptance criterion is unmet, say so plainly rather than reporting completion.

---

## Out of scope — do not do these

- **H5**, the convert-vs-delete lock-ordering deadlock. It is real and it is next, but it needs its own repro test and a structural change to how target+member locks are acquired. Nothing in this plan should change lock *ordering*.
- **Dirty-field tracking** in `_MySQLBaseDb.commit`. Considered and rejected in the spec; revisit only on evidence that lost updates persist after Task 7.
- **Turning off `autocommit`**, anywhere, for any reason.
- The **naming items** (`round_id` et al.) — evidence-only, pending sign-off.
- Repairing `!add_tag` / `!remove_tag` instead of deleting them. That decision is made.

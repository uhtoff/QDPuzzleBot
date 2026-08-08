# Atomicity work — design

> **STATUS: SHIPPED 2026-08-08** on `staging` (`ba0df74..f472a83`). One correction to
> this spec was found in review and fixed in `9f5cda7`: §4 judged the `rowcount == 0`
> false positive "acceptable at ERROR volume", which was wrong. Because tags/tag_edges
> are in `SPECIAL_ATTR`, a membership-only commit changes no scalar column and ALWAYS
> reports 0, so the log fired on every successful retag. It now confirms the row is
> really gone with an indexed SELECT before logging.

Date: 2026-08-08. Source: `docs/reviews/2026-08-07-comprehensive-review.md`, item 4 of
its suggested order of attack — "H4 + T1 + M5 (the atomicity class) — the biggest design
fix; scope it deliberately." T2 is folded in because it compounds the same class.

## Goal

Remove the silent-lost-update and half-applied-write paths in the store and at the
bang-command edge, and make the code's claims about atomicity true.

## In scope

- **T1** — `autocommit=True` makes every `commit()` a no-op; multi-statement store ops
  are non-atomic.
- **H4** — the bang edge reads its snapshot before taking the per-puzzle lock and
  commits the whole row after releasing it.
- **M5** — two background `create_task` link/unlink paths commit a puzzle unlocked.
- **T2** — the generic id-addressed UPDATE silently no-ops on a missing row.

## Out of scope

- **H5** (convert/delete lock-ordering deadlock). Sequenced separately by the review;
  needs its own repro test and a structural change to lock acquisition. Do not attempt
  it here.
- The naming items (`round_id` et al.) — evidence-only, pending sign-off.
- Any other review finding.

## Relevant files

| File | Why |
|---|---|
| `bot/store/__init__.py` | `MySQLReconnector` — `autocommit=True` at `:65` and `:77`; `commit()` at `:88` |
| `bot/store/mysqldb.py` | `_MySQLBaseDb.commit` `:36`; `bulk_update_edge_type` `:532`; `PuzzleJsonDb.commit` `:562`; `delete_message` `:662`; `set_positions` `:1101`; 33 `self.mydb.commit()` calls |
| `bot/cogs/puzzles.py` | `with_puzzle_mutex` `:63`; `cog_before_invoke` `:197`; `cog_after_invoke` `:286`; mutation sites `:747`, `:1370` |
| `bot/core/puzzle_service.py` | `link_informs_for_evidence` / `unlink_informs_for_evidence` (~`:3052`) |
| `bot/web/actions.py` | `:2212`, `:2215` — the bare `create_task` callers; `:584` — the correct lock-then-read pattern to mirror |
| `bot/core/locks.py` | `puzzle_lock` |
| `migrations/037_*.sql` | new — `message_attachments` FK |

## Required behaviour

### 1. Store layer (T1)

Each multi-statement op becomes un-half-appliable by **statement shape**, not by
transactions. No transaction is introduced.

**1a. `bulk_update_edge_type` — reorder to INSERT-then-DELETE.**
Today: SELECT contributing puzzle ids → DELETE their `feeds`/`informs`/`none` rows →
re-INSERT as `new_edge`. A failure between the DELETE and the INSERT **destroys the
group's membership**.
Required: INSERT the `new_edge` rows first (the statement is already
`ON DUPLICATE KEY UPDATE`, so it is idempotent), *then* DELETE the old rows, excluding
the new edge type:

```sql
DELETE FROM tags WHERE tag_group_id = %s
  AND edge_type IN ('feeds','informs','none')
  AND edge_type <> %s          -- new_edge; it may itself be one of the three
  AND puzzle_id IN (...)
```

A failure between the two now leaves a puzzle holding **both** the old and the new edge
— visible, recoverable, and fixed by re-running the convert — instead of holding none.

This cannot be collapsed to a single `UPDATE`: migration 020's
`UNIQUE (tag_group_id, puzzle_id, edge_type)` means a puzzle that already holds
`new_edge` alongside an old edge would violate the constraint on update.

**1b. `set_positions` — collapse to one statement.**
Today: a Python loop of per-row `UPDATE`s, under a docstring claiming "a half-applied
reorder cannot leave the list in an order nobody chose". Required: one statement —

```sql
UPDATE puzzle_images SET position = CASE id WHEN %s THEN %s ... END WHERE id IN (...)
```

One statement is genuinely atomic under autocommit, so the existing docstring becomes
true and stays. An empty `ordered_ids` must be a no-op, not a malformed statement.

**1c. `delete_message` — add the FK, then delete one row.**
Today: DELETE attachments → DELETE message, with no FK between them
(`027_message_bridge.sql` declares neither). A failure between destroys the attachment
rows while leaving the message.
Required: new migration `037_message_attachment_fk.sql`, in the house style of
`022_delete_cascades.sql` — sweep orphans first, then add the constraint:

```sql
DELETE a FROM message_attachments a
  LEFT JOIN messages m ON a.message_id = m.id
  WHERE m.id IS NULL;

ALTER TABLE `message_attachments`
  ADD CONSTRAINT `MessageAttachmentMessageID` FOREIGN KEY (`message_id`)
      REFERENCES `messages` (`id`) ON DELETE CASCADE ON UPDATE CASCADE;
```

No `MODIFY` is needed: `messages.id` and `message_attachments.message_id` are both
`BIGINT UNSIGNED` already (unlike the 022 case, which needed a narrowing MODIFY first).

`delete_message` then reads the `local_path` values (still needed — the caller unlinks
the bytes) and issues a single `DELETE FROM messages WHERE id = %s`. The attachment rows
go with it via the cascade. The paths are returned only on a successful delete: if the
DELETE raises, the exception propagates and the caller never unlinks files whose rows
still exist.

**1d. `PuzzleJsonDb.commit` — document the real guarantee.**
Base row then tag diff; this genuinely cannot collapse to one statement. The consequence
of a half-apply is mild and self-correcting (a stale tag set, fixed by the next commit).
Leave the sequence as is; replace any claim of atomicity with a note stating what is
actually guaranteed and why the failure mode is tolerable.

**1e. Delete the 33 no-op `self.mydb.commit()` calls in `bot/store/mysqldb.py`.**
Zero behaviour change: every `MySQLReconnector` sets `autocommit = True` in both
`_connect` (`:65`) and `_ensure` (`:77`), and `bot/web/data.py:45` sets it on its own
connection, so there is no path where one of these calls does anything. They are deleted
because a `commit()` call signals a durability boundary that does not exist, at 33 sites,
and one clarifying comment elsewhere does not undo that signal at the point a reader
meets it.

`MySQLReconnector.commit()` itself is **kept** — it is part of the connection surface and
a real transaction would need it. Its comment at `:89` ("even with autocommit=True, your
code calls commit()", which reads as endorsement) is replaced with: this is a no-op while
autocommit is on; atomicity comes from statement shape; any future transactional op must
run on a **dedicated** `autocommit=False` connection, never the shared `mydb`, because
the shared connection is used by several DAOs on the event loop and an open transaction
would span their interleaved statements.

The same applies to `bot/web/data.py:90`: `_run_write` runs a single statement on the
dashboard pool, which is created with `autocommit=True` (`:45`), so its `conn.commit()`
is a no-op and its docstring ("Run a writing statement on a pooled connection and
commit") is misleading. Delete the call and correct the docstring — the statement is
already atomic on its own.

This lands as **its own commit**, separate from 1a–1d, so the mechanical diff does not
obscure the behaviour changes in the same file.

### 2. Bang edge (H4)

**2a. Re-read under the lock.** `cog_before_invoke` keeps doing channel→puzzle
*resolution* — it is needed to know which lock to take — but stops being the source of
the data the command mutates. After `with_puzzle_mutex` (`puzzles.py:63`) acquires the
per-puzzle lock, it **re-reads** puzzle, tag_group and hunt by id and replaces the
corresponding entries in `self.environment[ctx.message.id]`.

This gives the bang edge the same lock-then-read shape the web edge already has
(`actions.py:584`). It matters because services commit the object they are passed
(`PuzzleJsonDb.commit(puzzle)`, ~20 sites in `puzzle_service.py`) and the generic commit
writes every field by name — so a snapshot read before the lock clobbers a concurrent
write to an unrelated field.

Commands with no puzzle in context are unaffected: the decorator already short-circuits
at `:75`.

**2b. Drop the blanket after-invoke commit.** Delete the four commits in
`cog_after_invoke` (`:286`). Everything else already routes through the service, which
commits itself.

A full mutation sweep of the cog finds exactly three in-place mutation sites, and none
of them needs a replacement commit:

- `hunt.solved_sheet_id = google_drive_id` (`:747`) — **already** followed by an
  explicit `HuntJsonDb.commit(hunt)` at `:749`. Nothing to do.
- `puzzle.tags.append(new_tag)` in `!add_tag` (`:1370`) and
  `puzzle.tags.remove(remove_tag)` in `!remove_tag` (`:1387`) — **these commands are
  being deleted**, see 2c.

**2c. Delete `!add_tag` and `!remove_tag`** (`puzzles.py:1347-1388`) and their two rows
in `docs/commands.md:209-210`.

Both are documented troubleshooting-only commands that already persist nothing, and have
since the multi-edge work. `MySQLPuzzleJsonDb.SPECIAL_ATTR` (`mysqldb.py:343`) lists both
`tags` and `tag_edges`, so the generic UPDATE skips them; membership is persisted solely
by the tag-diff in `PuzzleJsonDb.commit` (`:562`), which builds `mem_pairs` from
**`tag_edges`**. Both commands mutate only the plain `tags` list (`puzzle_data.py:51`)
and never touch `tag_edges`, so the diff sees no change and no row is written or removed.
The 2026-07-02 review recorded the same fact in passing ("`!add_tag`'s append never
persists"). `PuzzleData` has the correct seams — `add_edge` / `remove_edge` (`:108`,
`:114`) keep both structures in sync — and these commands do not use them.

Deleting them is preferred over repairing them: the supported paths (`!feeds`,
`!informs`, `!add_to`, `!remove_from`) are solid, and a manual tag-id escape hatch that
bypasses the edge model is a liability rather than a fallback.

Keep, explicitly:
- `!list_tags` (`:1311`) — read-only, still useful.
- `PuzzleData.add_tag` (`puzzle_data.py:96`) — a different thing entirely, used by
  `puzzle_service.py:1024,2024`.

This lands as **its own commit**, separate from 2a and 2b.

This also fixes a second defect: `cog_after_invoke` runs in discord.py's `finally`, so a
command that mutates and then raises currently persists the partial mutation.

The `self.environment.pop(ctx.message.id, None)` cleanup at `:297` stays.

### 3. Background link paths (M5)

Wrap the bodies of `link_informs_for_evidence` and `unlink_informs_for_evidence` in
`puzzle_lock(source_puzzle_id)`. Both are called only as bare `create_task` from
`bot/web/actions.py:2212,2215`, so no caller holds that lock — there is no reentrancy
hazard (`asyncio.Lock` is not reentrant; see `bot/core/locks.py:8`).

The existing early return for a falsy `source_puzzle_id` / `target_puzzle_id` must stay
**outside** the lock — a sourceless hunt-level evidence item has no puzzle id to lock on.

### 4. Missing-row UPDATE (T2)

`_MySQLBaseDb.commit` (`mysqldb.py:36`) checks `cursor.rowcount == 0` on the UPDATE
branch — before `cursor.close()`, on the same cursor — and logs at ERROR with the table
name and the id. **Log only; do not raise.** The stale-object-after-cascade-delete path
is legitimate (the row is genuinely gone), and raising would convert a benign no-op into
a user-facing traceback that aborts a bang command after its Discord-side work is done.
Revisit raising once the logs show whether this fires in practice and on which paths.

Note MySQL reports `rowcount = 0` for an UPDATE that matches a row but changes no value.
That is a false positive for this log line; it is acceptable at ERROR volume for a
diagnostic, and is the reason this is a log and not a raise.

### 5. Hand test script (deliverable)

`scripts/diagnose_atomicity.py`, in the house style of `scripts/diagnose_addtable.py`:
module docstring stating purpose and the run line, repo-root `sys.path` bootstrap,
PASS/FAIL printed per case, throwaway fixtures cleaned up afterwards.

Purpose: verify against a **real MariaDB** the things the unit suite cannot — that the
migration actually applied, that the FK actually cascades, and that two genuinely
concurrent writers under the real locks do not lose an update. The pytest suite proves
the code shape; this proves the deployed database agrees.

    pipenv run python scripts/diagnose_atomicity.py [--keep]

Cases, each printing PASS/FAIL with the observed values on failure:

1. **Schema** — migration 037 is recorded as applied; the
   `MessageAttachmentMessageID` constraint exists on `message_attachments`; no orphan
   attachment rows remain.
2. **FK cascade** — insert a throwaway message + attachment, delete the message via
   `MessageDb.delete_message`, assert the attachment row is gone and the returned
   `local_path` list contains the fixture's path.
3. **`bulk_update_edge_type`** — build a throwaway group with two members, one already
   holding the target edge type; convert; assert every member still holds exactly one
   edge and none lost membership. The membership-destroying failure mode is the whole
   reason this work exists, so it gets a live check.
4. **`set_positions`** — three throwaway rows in a scrambled order; assert the readback
   order matches exactly.
5. **Concurrent writers (H4)** — two `asyncio` tasks against one throwaway puzzle,
   through the real `puzzle_lock` and the real service, one writing a note and one a
   status; assert both survive. This is the load-bearing case: it exercises the actual
   lock and the actual commit path, not a mocked one.
6. **T2** — commit an object whose row has been deleted; assert the ERROR line was
   emitted and nothing was written.

Safety requirements, non-negotiable:

- **Refuse to run against the production database.** Read the database name from
  `config.json` and abort with a clear message unless it is the test database, or
  `--force` is passed explicitly.
- Every fixture row is created by the script and deleted by it, in a `finally`, even on
  failure. `--keep` skips the cleanup for post-mortem inspection, matching
  `diagnose_addtable.py`.
- Fixture rows are prefixed/identifiable (e.g. `DIAG-`) so a `--keep` run or a crash
  leaves obviously-throwaway data, not something mistakable for real hunt state.
- No Discord and no Google Sheets calls: this is a store-layer and lock-layer probe
  only. Where a service method would fan out to a notifier, pass a no-op notifier.

## Constraints

- No transactions, and no change to `autocommit`, anywhere in this work.
- Do not touch H5 or the lock-ordering structure.
- Preserve the existing lock discipline: locks live at the edges, `PuzzleService` stays
  lock-free, no puzzle lock is acquired twice in one task.
- Migration 037 must not collide with a parallel worktree's number — check before
  merging.
- No behaviour change in 1e; it is a pure deletion.

## Acceptance criteria

Every item below gets a failing test written first and **mutation-proven**: break the
fix, confirm the test goes red for the right reason, restore. Do not commit the mutation.

1. **`bulk_update_edge_type`** — fixture has **two** member puzzles, one of which already
   holds the new edge type. Proves both the reorder and the `edge_type <> new_edge`
   exclusion. A single-member fixture passes under the broken version and is therefore
   not acceptable.
2. **`set_positions`** — at least **three** ids in a scrambled order. Order is a
   relationship; one or two items cannot catch a truncation or a transposition. Plus an
   empty-list case.
3. **H4** — two concurrent tasks on one puzzle (a `!note` racing a `!status`) assert that
   neither field is lost. This is the load-bearing test for the package.
4. **H4, after-invoke** — a command that mutates and then raises does not persist the
   mutation.
4b. **`!add_tag` / `!remove_tag` are gone** — the bot registers no command by either
   name, and `docs/commands.md` no longer lists them. `!list_tags` still works and
   `PuzzleData.add_tag` still has its two service callers.
5. **M5** — a link/unlink racing a locked command does not revert the concurrent write.
6. **T2** — committing an object whose row was deleted emits the ERROR log.
7. **Migration 037** — applies against a copy of the production schema; the orphan sweep
   runs clean and the constraint takes.
8. **`delete_message`** — deleting a message removes its attachment rows via the cascade,
   and returns their `local_path` values.
9. **`scripts/diagnose_atomicity.py`** exists per §5 and reports PASS on every case
   against the test database, with the run recorded in the final summary. A case that
   cannot be checked must print SKIP with a reason, never a silent PASS.

Gates, all green before each stage commit:

```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-output.log 2>&1
pipenv run ruff check
pipenv run basedpyright bot/
```

## Rejected approaches

- **Turning off `autocommit` connection-wide, or a `transaction()` context manager on the
  shared `mydb`.** The shared connection is used by several DAOs on the event loop; a
  transaction opened by one caller would span any interleaved statement from another.
  Genuinely dangerous, and unnecessary once the statement shapes are fixed.
- **Building the dedicated-connection transactional escape hatch now.** After 1a–1d,
  nothing in scope needs it. The agreed shape is recorded in the `MySQLReconnector`
  comment so a future op does not have to re-derive it.
- **Dirty-field tracking in `_MySQLBaseDb.commit`** (write only changed fields). It would
  fix the lost-update class everywhere at once, including background tasks and the web
  layer, and make the remaining races benign rather than merely narrower. Rejected for
  now: it touches the base DAO and every data class, and it silently changes what
  "commit" means at ~20 service call sites. Revisit on evidence if lost updates persist
  after H4 lands.
- **Keeping the no-op `commit()` calls with a clarifying comment at `MySQLReconnector`.**
  Rejected: it leaves 33 sites signalling a durability boundary that does not exist.
- **Raising on `rowcount == 0`** — see §4.

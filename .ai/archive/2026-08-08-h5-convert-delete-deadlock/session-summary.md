# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-2
Started: 2026-08-08
Status: ready for handoff — docs(handoff): record H5 state in .ai/active @ 67a582b

## Goal

Fix **H5** from `docs/reviews/2026-08-07-comprehensive-review.md`: the cross-task
deadlock where convert and delete/archive acquire overlapping puzzle locks in opposite
orders. Scope agreed with the user at the start: the deadlock **plus** the bang convert
gap **plus** the confirm-under-lock aggravator. No spec/plan doc — straight to
implementation, TDD, reviewable commits.

## Current status

**Done and committed.** Three commits on `working-2`, on top of `f3df818` (staging):

| Commit | What |
|---|---|
| `0173802` | The deadlock fix + the bang convert gap + regression tests |
| `cd05c68` | Confirm-before-lock in the three destructive bang commands |
| `658ff54` | Docs: mark H5 resolved in the review + clear the atomicity plan's pointer |

Gates, all run on the final tree:

| Check | Result |
|---|---|
| `timeout 400 pipenv run python -m pytest tests/ -q` | **1700 passed**, 28 warnings |
| `timeout 60 pipenv run ruff check` | All checks passed |
| `timeout 300 pipenv run basedpyright bot/` | 0 errors, 0 warnings |

The 28 warnings are the pre-existing `datetime.utcnow()` deprecations — not ours.

## The bug, as confirmed in code

`bulk_re_edge` acquired every **member's** `puzzle_lock` internally (sorted), while its
convert callers held the **target's** lock → order `T, then sorted(members)`.
`delete_round`/`delete_hunt`/`archive_hunt` take `puzzles_lock(member_ids)`, and that
list **includes the target** (it carries a `target` self-edge row in its own group) →
order `sorted(members ∪ {T})`. Any member id sorting below the target id put the two in
a cycle; `asyncio.Lock` never times out, so both tasks wedged until restart.

## Final decisions

1. **Structural fix, not another ordering rule.** `bulk_re_edge` takes no lock at all;
   every convert edge acquires target + members in ONE sorted `puzzles_lock` over the
   new `convert_lock_ids()`. With no nested acquisition the cycle is unconstructible.
2. **`convert_lock_ids()` lives in `bot/core/locks.py`**, with function-local
   `from bot.store import ...` — locks.py is imported by nearly everything and must not
   pull the DB layer in at import time. The local import also means it honours the
   `bot.store.*` patch seam the web tests already use.
3. **Correction to the review:** the bang converts (`!convert_to_meta`/`capstone`/
   `runaround`/`group`/`metapuzzle`) do NOT enter the cycle via `with_puzzle_mutex` —
   they carry no mutex decorator and took **no** puzzle lock at all. That was a separate
   lost-update hole, closed by the same change. Recorded in the review doc.
4. **Two pre-existing tests inverted, not weakened** (stated in the commit message):
   - `test_edge_primitives.py::test_flip_waits_for_in_flight_member_command` →
     `test_flip_takes_no_lock_of_its_own`. The #27 guarantee is unchanged — the members'
     locks are still held across the flip, now by the caller — and is asserted
     end-to-end in `tests/test_lock_ordering.py`.
   - `test_change_group_type_web.py::test_change_type_does_not_deadlock_when_service_locks_a_feeder`
     → `test_change_type_locks_target_and_members_in_one_acquisition`. It now asserts
     *more*: both locks held inside the service call, still against real locks.
5. **Confirm before lock**, with the member id list re-read after the click so the set
   locked matches what is actually torn down. The count in the confirm text is still
   read up front.

## Rejected approaches

- **Reentrant / task-local lock tracking.** Would fix the non-reentrancy half but not
  the ordering half — convert holding `T` and wanting `a` while delete holds `a` and
  wants `T` still deadlocks with reentrancy.
- **Keeping the locking in `bulk_re_edge` and having callers lock "only the target".**
  That *was* the design, and it is exactly what created the cycle.
- **An end-to-end two-task deadlock repro test.** Attempted and dropped as
  unfalsifiable: to reproduce, the fake convert service must emulate the *old*
  `bulk_re_edge` internal locking, which self-deadlocks under the fixed code — so the
  fake differs pre/post fix and the test proves nothing. Replaced by the two halves of
  the invariant, each independently falsifiable (see Tests below).

## Known limits (documented in code, deliberately not fixed)

- `convert_lock_ids` reads the member set just **before** the lock is taken, so a puzzle
  tagged into the group in that window is not covered. This is the same pre-existing
  window the delete edges have; noted on the function.
- `convert_tag_group_type` creates its new target puzzle *inside* the critical section,
  after the ids are fixed. Nothing else can reach that row before it is committed.

## Next steps

1. Decide whether to merge `working-2` → `staging` (nothing has been merged or pushed).
   Check for migration-number collisions against other worktrees first if any migrations
   land — this branch adds none.
2. `node_modules/` is untracked in this worktree and was left alone (pre-existing).
3. Remaining review items, unchanged by this work: H4/T1/M5 are already shipped
   (atomicity series); still open are M6–M9, the T-series DB items, the web-hardening
   Lows, and the frontend items. The naming items (`round_id` et al.) remain
   evidence-only pending sign-off.

## Open questions

None blocking. The only decision left is the merge in step 1 above.

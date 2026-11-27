# Puzzbot 48-hour commit review

Reviewed 25 November 2026 at 17:17 GMT through 27 November 2026 at 17:17 GMT, covering the release range `620c71a..ff3eb59`. Local `main` and the checked-out `staging` at `be2d093` had identical file trees. The locally recorded `origin/main` was at `ab3e2f8`, the Puzzle Chat 1.2.1 promotion.

The release range covers multiple answers stage 1, notes and bridge changes, team-server setup, image tools, and Puzzle Chat 1.2.1 and 1.2.2. The review also inspected the pending multiple-answers and Google Drive branches.

## Release findings

1. **High — Adding an answer can corrupt an existing answer containing `/`.** Migration 065 preserves six known whole answers containing slashes. Stage 1 reparses the saved solution on every add. An in-memory reproduction using the Spacewalk meta answer showed that adding `PEAR` produced three rows instead of two and changed the original answer text. The pending repair migration recognises the six exact original strings, so it cannot reliably recover an answer whose text has already changed. References: [answer reconciliation](../../bot/core/answers.py#L57), [migration 065](../../migrations/065_puzzle_answers_backfill.sql#L5).

2. **Medium — Firefox tab reuse can close a tab after the user navigates away.** The background worker reads the new tab's URL, waits for the handoff to the existing board, then closes the new tab by ID. A navigation during the handoff can therefore lose the user's new page. Recheck the source tab immediately before closing it. References: [background worker](../../extension/src/background.js#L773), [tab handoff](../../extension/src/lib/boardReuse.js#L38).

## Pending branch findings

1. **High — PR #4 can leave Drive writer access in place after revocation fails.** At `999be59`, `bot/core/drive_sharing.py:322–338` deletes the share record before calling Drive. The helper at line 152 logs and swallows a failed deletion. If Drive still has the permission, a later retry has lost the record it needs. Keep the record until revocation succeeds or Drive confirms the permission is already gone.

2. **Medium — Multiple-answers migration 066 can discard attribution.** At `7dc2a1c`, `migrations/066_puzzle_answers_repair.sql:42–85` can select legacy lowercase solutions after uppercasing their answer rows. It then deletes and recreates those rows without `created_by` and uses `solve_time` in place of the original creation time. Preserve metadata where the only difference is normalisation.

## Verification

- Python suite (`pipenv run python -m pytest tests/ -q`) passed 3,721 tests. Dashboard and extension suites (`npm test` in each package) passed 308 and 315 tests respectively.
- Ruff, dashboard TypeScript, the dashboard production build and `git diff --check` passed.
- Focused branch suites passed 110 multiple-answers tests and 42 PR #4 tests.
- Both Firefox XPI hashes matched the update manifest. The 1.2.2 package passed `web-ext lint` with zero errors and two warnings covering Android compatibility metadata and the existing signature.
- BasedPyright reported the same 11 `pytz.UTC` diagnostics on the baseline and release trees after the project and virtual environment import paths were supplied explicitly.
- Real browser tab reuse, deployed behaviour, live Discord and Drive operations, and a production-data migration rehearsal remain unverified.

## Verdicts (27 November 2026, checked against the code)

- **Release 1 (answers containing `/`): real, treated as theoretical.** `ensure_loaded` does re-split the solution and rewrite a whole-answer row, but on live only the six 065-listed puzzles (Spacewalk meta, five Ocarina "Barrier" puzzles, all in finished hunts) can trigger it, and only through a new answer write before stage 2 ships. Stage 2 (`multiple-answers`) makes the rows the truth and never re-splits, removing the cause. No action.
- **Release 2 (tab reuse closes a moved-on tab): real, Low, not Firefox-only.** Fixed for 1.2.3: `handOverBoardLink` re-reads the new tab once the handoff ends and only focuses the board and closes the new tab if it is still on the link (`extension/src/lib/boardReuse.js`, tests in `extension/test/boardReuse.test.js`).
- **Pending 1 (PR #4 revoke order): real, High; already known from an earlier review of the PR.** `revoke_user_active_shares` deletes the record before `_unshare_if_unneeded`, which swallows the failure. Must be fixed before PR #4 merges.
- **Pending 2 (066 attribution): does not hold in practice.** A lower-case solution survives only where stage 1 never wrote the puzzle (every stage 1 write derives `solution` from upper-cased rows), so its rows came from 065, which also set no `created_by` and used `solve_time`. The rebuilt rows carry the same metadata.

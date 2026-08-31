# Comprehensive codebase review — 2026-08-30

Review-only. No code was modified. Branch `staging` @ `58980a5` (prior review baseline
`3251336`, 2026-08-07; 105 commits landed between the two).

Method: two independent multi-agent sweeps (10 + 8 finder workstreams, including a
dedicated horizontal cross-sibling pass), with **every medium-and-above finding
adversarially verified** by a separate agent briefed to refute it against current
source. 51 findings were traced end-to-end: 50 confirmed (4 High, 37 Medium, 9 Low),
1 refuted and excluded. A further 86 low-severity items are reported unverified
(leads, not verdicts). Findings the 2026-08-07 review fixed, and its two documented
won't-fix decisions (keyboard-only a11y, member-route rate limiting), are excluded by
construction. Full evidence including the verification traces:
<https://claude.ai/code/artifact/65e6b4c8-f726-4428-b2ad-bda559c21ddb>.

## Status — what has since been fixed

Fixes land in tranches grouped by shared fix shape (plan: `.ai/active/current-spec.md`).
Every entry below was written as a failing test first, verified red *for the right
reason*, then mutation-proved (guard broken again, confirmed red, restored).

**Tranche 1 — sharp singletons, merged to `staging` 2026-08-30.** Gates after the
tranche: 1926 passed (was 1894), ruff clean, basedpyright 0 errors.

| Finding | Status | Commit |
|---|---|---|
| H1 `bridge_channels_conn` shared across threads | fixed (one shared lock spanning `dict_cursor()`) | `4ae8112` |
| H2 cross-hunt `puzzle_id` on region routes | fixed (`_require_hunt_puzzle`, `cross_hunt_puzzle`) | `953df4b` |
| M4 `!update_settings` admin-lockout side door | fixed (auth/identity keys refused) | `8312a66` |
| M37 live-DB family had no test-database guard | fixed (`pytest_sessionstart` refusal) | `93e8ced` |

Notes from the tranche:

- **H2 is object scoping, not a tier change** — `test_a_non_organiser_can_draw` still
  passes; a member still draws on their own hunt's canvas. `update_region` checks the
  *effective* `puzzle_id`, since a stored link is re-asserted by every PATCH that keeps
  it; clearing a bad link still works via `puzzle_id: null`.
- **`create_puzzle_from_region` has no equivalent hole** (checked while fixing H2): the
  only puzzle id it passes is one it just created in the visible hunt, and its body
  inputs (`name`, `role`, `round_id`) are each separately validated. Fix deliberately
  scoped to the two region routes.
- **M4's refusal also covers the `!update_puzzle_setting(s)` alias**, which is a strict
  improvement: puzzle rows carry none of these fields, so that path previously fell
  through to "Unrecognized key".
- **M37 also covers the `DB_DSN` env override**, since `Config` resolves it into the same
  `config.database`. Verified end to end: `DB_DSN=puzzlebot_prod` aborts at exit 4 before
  collection, writing nothing.

**Tranche 3 — frontend state & targeting, merged to `staging` 2026-08-31.** Gates after
the tranche: 1926 backend tests, 52 vitest (was 39), tsc + vite build clean, ruff clean,
basedpyright 0 errors.

| Finding | Status | Commit |
|---|---|---|
| H4 convert-to-feeder targeted the wrong puzzle | fixed (`lib/groupTarget`) | `27ce164` |
| M23 `"very high"` sorted and rendered as `"medium"` | fixed (both halves) | `aded3d4` |
| M24 SolveModal answer text crossed puzzles | fixed (reset on id change) | `49b44ab` |
| M25 PuzzleDrawer drafts crossed puzzles | fixed (reset + two keyed children) | `49b44ab` |
| C6 ctrl+wheel zoom dead on an in-place pending→ready | fixed (callback ref) | `7a17373` |
| — third hand-rolled target lookup, stale docstring count | tidied | `1636e19` |

Notes from the tranche:

- **M24/M25/C6 are inspection-only, by necessity.** The vitest setup is node-only — no
  jsdom or happy-dom — so component behaviour cannot be tested in this repo without a
  dependency decision that is not a reviewer's to make. Typecheck, vitest and the vite
  build all pass, but **nothing in the suite goes red if any of those three regresses.**
  Adding a DOM test environment is the open question; see `.ai/active/open-questions.md`.
- **M23 had a second site the review missed:** `priorityColor` switched only on
  `high`/`low`, so `"very high"` fell to the `default: "blue"` — the same calm blue as
  medium. That is the "renders" half of the finding. `"very high"` now shares the red
  band with `"high"`; giving it its own shade would be a palette decision, not a defect
  fix, and was deliberately not taken.
- **C6's cause was deeper than reported.** The wrapper is not merely un-re-attached: while
  `tile_status` is `pending` it is **not in the tree at all**, so `ref.current` is null
  when the effect runs and never runs again. `key={canvas.id}` genuinely does not cover
  it — that handles switching canvases, not an in-place transition.
- **H4's fix found two more copies** of the same hand-rolled target lookup (`Column`,
  `BoardGroup`), both now on the shared helper. Every other `is_meta` use in the SPA was
  checked and is legitimately "is a target of anything".
- **Corroborated (already logged as a low, no new finding):** the SPA's `PRIORITY_OPTIONS`
  is hardcoded at `PuzzleEditCells.tsx:28`, duplicating `bot/core/constants.py:37` by hand
  with no endpoint serving it. Correct today, hand-synced.

**Tranche 6 — the gating guardrails, merged to `staging` 2026-08-31.** Gates after the
tranche: 2024 backend tests (was 1926), ruff clean, basedpyright 0 errors.

| Finding | Status | Commit |
|---|---|---|
| M7 route map misclassified 6 canvas routes; resolver blind to ungating | fixed (map + guard-aware resolver) | `02d4358` |
| C8 new ungated mutating routes joined the residue silently | fixed (every route must be listed) | `02d4358` |
| M34 bang tier map was a sample (21 of 84) | fixed (census, 84 of 84) | `594903f` |
| M36 admin mutating routes had no behavioural test | fixed (10 routes, both directions) | `e8288e3` |
| — five stale "(admin only)" strings on organiser commands | fixed (low, rode along) | `a2691d4` |

Notes from the tranche:

- **Per D1 no route's tier changed** — the map was wrong, not the code. The six canvas
  *content* routes moved to the file's existing ungated list with the rationale recorded
  at both sites, citing the three docstrings that already state the design.
- **The resolver was blind to the exact regression it exists to catch.** Flipping
  `require_manage=True`→`False` in `update_canvas`/`delete_canvas` — silently ungating a
  destructive route — left the resolved map unchanged (47 passed). It now fails naming the
  route. The resolver's docstring records what it still cannot see (guards on anything but
  a bare parameter, non-literal arguments, gates not reached by a direct call) and notes
  that both assumptions err towards claiming *more* protection than the code has.
- **M36 proved the "nothing was written" half is load-bearing on its own**, by making
  handlers mutate *and then* refuse: the status stayed 403 and the tests still went red.
  That is exactly the status-code-only weakness this review flagged elsewhere.
- **`link` and `make-minimeta` are not admin-tier** — both are deliberately ungated, so
  what M36 pins for them is the archived-hunt refusal plus a member being allowed, which
  is what would fail on a later silent tightening.
- **Zero enforced-vs-documented tier disagreements** across all 84 commands against
  `docs/commands.md` — the 2026-08-08 levelling held. Only the user-facing copy had
  drifted.

**Tranche 5 — lock-regime stragglers, merged to `staging` 2026-08-31.** Gates after the
tranche: 2031 backend tests (was 2024), ruff clean, basedpyright 0 errors.

| Finding | Status | Commit |
|---|---|---|
| M2 `!sync` wrote member edges under only the context lock | fixed (one sorted acquisition) | `80e2875` |
| M5 bang `!convert_to_puzzle` took no lock at all | fixed | `80e2875` |
| M28 bang creates had no `hunt_create_lock` | fixed (both paths) | `80e2875` |
| M26 web demote-to-feeder outside the sorted acquisition | fixed | `a14ee26` |

Notes from the tranche:

- **All four were real, and two were worse than reported.** M26 and M5 do not merely lose
  an update: the concurrent command's pre-change snapshot re-INSERTs an edge against a
  `tag_group` row that has just been deleted, and MariaDB rejects it — **FK 1452 on
  `tags.RoundID`**, reproduced in both cases. `TagGroupJsonDb.delete` drops the group's
  rows from `tags` as well, which is what makes these demotes group-wide.
- **M5's mutation is precisely the M26 defect** (locking only the context puzzle), found
  independently by the other agent working in parallel — mutual corroboration.
- **A deadlock was avoided, not introduced.** `!sync`'s fix required *removing*
  `with_puzzle_mutex`: adding an acquisition inside it would have nested target-then-members,
  the exact H5 cycle against the delete/archive edges. Every path was checked against
  `bulk_re_edge`'s documented internal locking first; none reaches it.
- **Every mutation check deliberately still takes *a* lock** — a wrong lock set (M2), the
  context puzzle only (M5), a per-message key (M28), a nested second acquisition (M26) — so
  none of these tests can be satisfied by merely locking something.
- `hunt_create_lock`'s docstring now records the bang edge and the one-way
  `puzzle lock → hunt_create_lock → layout/rollup` order that keeps it cycle-free.

**Tranche 4 (Sheets) — PARTIAL. Safe subset merged to `staging` 2026-08-31; the other
seven are blocked on decisions.** Gates: 2036 backend tests (was 2031).

| Finding | Status | Commit |
|---|---|---|
| M20 `delete_hunt` deleted every tab, then the files anyway | fixed | `509a055` |
| M22 unassigned/evidence refreshes raced on their tab | fixed (per-tab `rollup_lock`) | `509a055` |
| M14, M16, M17, M18, M19, M21, C5 | **blocked** — see `.ai/active/open-questions.md` Q1–Q3 | — |

A research pass (`.ai/active/sheets-tranche-research.md`) re-confirmed all nine against
current source and **corrected this review in three places**:

- **M16's prescribed fix does not work.** Guarding on `select_table(...)` does not close it:
  `select_table` does not short-circuit on a name miss, it falls through to
  "if there is exactly one table, use it" — and M16's scenario is exactly one solver table.
  **M16 and M17 are one fix**, needing a strict name match. The existing test uses a lone
  *unnamed* table, so it would have passed under both the old guard and the "fixed" one.
- **M14's fix is not a copy of its sibling.** `ensure_evidence_table` does a `_values_clear`
  over the whole tab (`ensure_unassigned_table` does not), so adopting a pre-existing
  "Evidence" tab by name then blanks it — correct for a bot leftover, silent data loss if a
  human made it. That is Q1.
- **Narrowing `select_table` is a regression risk, not a corruption risk.** Git archaeology
  found three contribution-table naming eras (unnamed → `"Contributions"` → the tab's name →
  `Contributions {page_id}`), so the legacy population is mostly *named*. Narrowing to
  "unnamed only" would reclassify legacy tables as not-ours and blank those rollups
  permanently, with only a log line. That is Q2.

Also established: the tranche is **three clusters plus three singletons**, not the one
shared seam this review assumed — a generic "tolerate-already-done" wrapper would have to
know each operation's idempotency probe, which is the whole content of each fix. And
**four of the nine rest on unverified Google Sheets API assumptions** (does `addSheet` 400
on a duplicate title; does deleting a Table's columns remove the Table). The entire Sheets
test surface asserts request shapes and call ordering only — nothing verifies what Google
actually does. That is Q3.

Two further items found while fixing M22, not previously logged:

- **`_ROLLUP_LOCKS` is a latent cross-test hazard.** It is a module-global
  `defaultdict(asyncio.Lock)` that outlives each test's event loop, so two tests contending
  the *same key* fail with `RuntimeError: Lock is bound to a different event loop`
  regardless of the behaviour under test. The new tests avoid it with per-test hunt ids;
  `tests/test_rollup_lock.py` contends key `5` and is safe only because nothing else does.
  A trap for the next person adding a lock test.
- **`ensure_evidence_table` poisons its own retry guard**: it swallows an `addTable` failure
  and returns `page_id` as a sentinel, which is committed to `hunt.evidence_table_id`, so
  the `is None` guard never fires again and the native Table is never retried.
  `ensure_unassigned_table` carries the identical sentinel. Degrades rather than wedges.

### Decisions D5–D9 (2026-08-31) unblocked the rest

All five open questions were answered; reasoning is preserved in
`.ai/active/open-questions.md`. The two that changed the work materially:

- **D8 — no hunt has used an archive spreadsheet since the new bot.** So no
  `additional_sheets` row is archived today, and backfilling migration `045` to "main" is
  *correct*, not merely safe. Tranche 2 (H3/M15) is unblocked.
- **D5 — M14's collision is directional.** A puzzle named "Evidence" created *after* the
  hunt's Evidence tab is harmless (puzzle tabs uniquify via `get_unique_tab_name`); a
  puzzle taking the title *first* is what wedges, because the hunt-level `create_blank_tab`
  passes a literal title. The fix decides by *ownership*: if the existing tab belongs to a
  puzzle, create under a unique name; if it belongs to no puzzle it is a bot leftover and
  may be adopted. **This also fixes a latent corruption in `refresh_unassigned`**, whose
  adopt-by-name fallback would otherwise adopt a puzzle's tab and write rollup rows into a
  live solving grid — the very sibling this review told us to copy.

**Second Sheets batch, merged 2026-08-31.** Gates: 2062 backend tests (was 2036),
52 vitest, ruff clean, basedpyright 0 errors.

| Finding | Status | Commit |
|---|---|---|
| M16 demote guard matched any Table | fixed | `0d0b911` |
| M17 `select_table`'s lone-table fallback | fixed (strict name match) | `0d0b911` |
| — `convert_to_puzzle` demoted with no guard at all | fixed (third site, found in passing) | `0d0b911` |
| M31 six mutations never pushed to the board | fixed | `a8c70db` |
| M32 dead clue-logging twin | deleted | `a8c70db` |

Notes:

- **M16 and M17 are one fix, and the review's prescribed fix for M16 would not have
  worked.** Guarding on `select_table` alone still matched a lone solver table. Only the
  strict name match closes it — which also closes C5's reachable path.
- **A third unguarded site turned up**: `convert_to_puzzle` called `demote_tab_to_puzzle`
  unconditionally, reachable because `convert_to_meta` commits `is_target=True` *before*
  `promote_tab_to_group`, so a Sheets failure leaves a promoted flag on an unpromoted tab.
- **M32's deletion was verified, not assumed.** The concern was that `log_clue` held
  `puzzle_lock` while its replacement does not. It is not a real gap: `log_clues` has no
  read-modify-write; `!clue`/`!clues` already call it lock-free, so `log_clue` was the
  outlier; the one affected race is benign and hand-correctable, and an insert racing a
  delete is handled by `ON DELETE CASCADE`; and the omission was a documented decision in
  the image-evidence plan.
- **The C8 forward guard caught its own subject on first use** — deleting the `/clues`
  route made `test_every_route_is_accounted_for` fail naming the stale inventory entry.
- **Behaviour change worth knowing:** on a minimeta tab holding only a solver's table,
  `render_minimeta_contribution` now promotes (inserting the A–D band, shifting their grid
  right) rather than writing the rollup into their table. Disruptive but correct and
  visible, where the old path corrupted silently.

**Third Sheets batch, merged 2026-08-31.** Gates: 2068 backend tests (was 2062),
ruff clean, basedpyright 0 errors.

| Finding | Status | Commit |
|---|---|---|
| M14 `refresh_evidence` wedged on an existing "Evidence" tab | fixed (`_ensure_bot_tab`) | `b5595ba` |
| — `refresh_unassigned`'s adopt-by-name fallback | fixed (same seam, D5's latent corruption) | `b5595ba` |

Notes:

- **The `_values_clear` asymmetry this review flagged as Q1 is what set the shape of the
  fix.** Both refreshes now go through one `_ensure_bot_tab` helper on the service, which
  adopts a tab only when nothing claims it. Two indexed lookups decide that, and they run
  only on the duplicate-name branch.
- **The ownership test went wider than D5 specified.** D5 says "belongs to a puzzle";
  the implementation also rejects a tab claimed by any *additional sheet*, via a new
  `SheetsJsonDb.get_by_page_id`. `get_by_hunt` could not serve — it deliberately excludes
  puzzle-owned sheets — and a puzzle's extra working tab is exactly as destroyable as its
  main one.
- **A non-duplicate create failure is no longer swallowed.** If nothing carries the name,
  the `create_blank_tab` failure was a quota trip or a dead token, and it now surfaces
  instead of being converted into a missing page id.
- **`.ai/active/current-spec.md` still carried the uncorrected instruction** to copy
  `refresh_unassigned`'s fallback (its Phase 5 M14 line), even though this review had
  already corrected it at the Q1 note. The spec has been amended; worth knowing that the
  two documents drifted apart on a point that mattered.
- Still open in Tranche 4: **M18 + C5** (gated on D7's live scratch-spreadsheet exercise,
  not yet run), **M19** (ships with H3), and **M21** (last — it rewrites M17/M18's lines).


### D7 live probe, 2026-08-31 — what Google actually does

Run against a throwaway spreadsheet owned by the bot's service account (no hunt, no
production sheet, no DB). This is the first verification in the Sheets tranche that is
not a request-shape assertion. Four results, two of which change the work:

1. **`insertDimension` (COLUMNS 0-4, `inheritFromBefore: False`) shifts by exactly 4.**
   A tab whose A1 held `"Puzzle Title:"` has it at **E1** afterwards, A1 blank. As assumed.
2. **A blank cell returns NO `values` key at all** — the response is just
   `{"majorDimension": "ROWS"}`. So `resp.get("values") or []` is correct, and the
   existing `a1_value` idiom is safe. As assumed.
3. **A1 ALONE CANNOT TELL A HALF-PROMOTED TAB FROM A FULLY-PROMOTED ONE.** Both return
   no `values` key for A1. This **refutes M18's stated direction**, which says to detect
   already-shifted geometry with "A1 blank + B1 label ... the same check
   `_ensure_contribution_table` uses" — those are two different checks, and the one
   `_ensure_contribution_table` actually performs reads **only A1**
   (`puzzles_gsheet.py`, the `_values_get(... '!A1')` call). Copying it would not
   distinguish the states, and promote would insert four more columns exactly as M18
   describes. **The probe must read `A1:B1` and key on B1.**
4. **A duplicate `addTable` fails 400 — with an unrelated-sounding message.** Not "table
   exists" but *"You cannot add alternating background colors to a range that already has
   alternating background colors."* Any tolerate-already-done handling that sniffs the
   error string must match THAT text. 400 is non-retryable, so the retry machinery will
   not paper over it.
5. **A second `deleteDimension` of the same range succeeds silently**, eating four more
   real columns. There is **no error to catch** — C5's damage cannot be handled
   defensively after the fact, only prevented by probing geometry first. This is what
   makes C5 a correctness fix rather than an error-handling one.

Consequence for the fix: extract a named geometry probe that reads `A1:B1` and returns
which layout the tab is in (plain / target / indeterminate), and have promote, demote and
`_ensure_contribution_table` all consult it. A1-only is not sufficient for any of them.


**Fourth batch, 2026-08-31 — Tranche 2 and the rest of Tranche 4.** Gates: 2090 backend
tests (was 2068), ruff clean, basedpyright 0 errors. Migration `045` applied to
`puzzlebot_test`.

| Finding | Status | Commit |
|---|---|---|
| Tranche 8 — `failed_round_ids` split (D9) | fixed | `59253a9`, `7c3ddd9` |
| H3 — target's extras archived but unflagged | fixed (migration `045`) | `79191aa` |
| M15 — extra-sheet links keyed on `puzzle.solved` | fixed (same column) | `79191aa` |
| M19 — `move_puzzle_sheet` had no compensation | fixed (delete goes last) | `d71a22b` |
| M18 — promote not idempotent | fixed (`_tab_layout` probe) | `96de637` |
| C5 — demote destructively non-idempotent | fixed (same probe) | `96de637` |

Notes:

- **Migration `045`'s pre-flight did not come back empty.** Six `additional_sheets` rows
  on `puzzlebot_test` matched the "might be archived" query. Checked against the live
  Sheets API rather than assumed: every one of the six page ids exists on **neither** the
  main nor the solved spreadsheet — dangling rows pointing at deleted tabs. The backfill
  to 0 is correct, and those six are direct evidence for M19, whose failure window
  produces exactly that. **Re-run the pre-flight before applying `045` to production.**
- **The Tranche 8 split briefly rebased `SheetRefreshError` off `RuntimeError`.** A dozen
  sites catch a bare `except RuntimeError` around service calls and convert it to a 400;
  only four list `except SheetRefreshError` first. Off `RuntimeError` the rest would have
  turned into unhandled 500s, and **the whole suite passed either way** — nothing asserted
  the inheritance. It does now, and that test is the only one of 2090 that goes red on the
  change.
- **This review's advice has now been wrong three times in the Sheets tranche**, each time
  in the same direction: it names a sibling to copy without checking that the sibling does
  what the fix needs. M16's `select_table` guard, M14's `refresh_unassigned` fallback, and
  M18's "the same check `_ensure_contribution_table` uses". Treat "mirror X" in this
  document as a lead, not an instruction.
- Still open in Tranche 4: **M21** only, and it is unblocked now that M17 and M18 have
  landed.


## Decisions taken — 2026-08-30

Four questions gated the fix planning. All four are now settled; the fix plan lives in
`.ai/active/current-spec.md` (8 tranches, grouped by shared fix shape).

- **D1 — canvas route tiers (M7, C8): containers organiser, content member.** The gating
  *map* is wrong, not the code. Canvas create/update/delete stay organiser; region
  create/update/delete, `retile`, `ocr-preview` and `create-puzzle-from-region` stay
  member-tier — the design the three route/test docstrings already state. The fix is to
  correct `EXPECTED` in `tests/test_web_route_gating.py` and to remove the resolver's
  condition-blindness, **not** to change any route's tier.
  `test_a_non_organiser_can_draw` stands.
- **D2 — M29 approved.** Replace the hand-rolled route prologue with a shared helper that
  makes an archived-check exemption explicit and named rather than an omission.
- **D3 — the `failed_round_ids` rename is approved**, with a design step first: see the
  Naming section — the field carries two id spaces, so a rename alone leaves it false.
- **D4 — non-finding.** Serving hunt-site credentials to guild members on archived hunts
  (`api.py:155`, `auth.py:303-309`) is **intended** under the 2026-06-21 visibility model.
  Confirmed by the user; not to be "fixed". Struck from Uncertainties below.

## Overall assessment

The codebase is in good health and clearly improving. Every quality gate is green, the
previous review's findings are almost entirely fixed, and several subsystems carry
defensive patterns well above hobby-project standard: a documented and consistently
applied sorted-lock discipline, magic-byte media sniffing with stored-XSS exclusions,
complete Sheets retry/backoff on the bot path, mutation-proved test guards, and a
carefully verified deploy/shutdown/migration ops layer.

No critical findings. The four Highs are one crash-class concurrency bug (H1), one
authorisation gap (H2), one data-corruption pair in the Sheets solved/unsolve lifecycle
(H3), and one wrong-target destructive flow in the dashboard (H4). The dominant
medium-severity theme is the **Google Sheets tab lifecycle**: non-idempotent multi-step
operations (promote/demote/move/create) that assume the previous step succeeded, where
one transient API failure strands or corrupts sheet state permanently. The second theme
is **seam completion**: the service-layer collapse is done for business logic (no
model-mutating `web_*` twin survives), but ~30 older web routes still hand-roll the
route prologue, and a handful of operations sit outside the lock regime their siblings
adopted.

## Commands run

| Check | Command | Result |
|---|---|---|
| Backend tests | `pipenv run python -m pytest tests/ -q` | **1894 passed**, 28 warnings, 16.2s |
| Lint | `pipenv run ruff check` | All checks passed |
| Types | `pipenv run basedpyright bot/` | 0 errors, 0 warnings |
| Frontend build | `npm run build` (tsc --noEmit + vite) | clean, 3.2s |
| Frontend tests | `npm test` (vitest) | 39 passed (7 files) |

Caveats: the shared `puzzlebot_test` DB has migrations 043/044 applied from another
worktree's branch, so the live-DB test family ran against a schema slightly ahead of
this branch (M8). The 28 warnings are one class: naive `datetime.utcnow()` deprecations
from four sites in `mysqldb.py`. Workstreams additionally ran read-only inspection
(grep/AST sweeps over all 180 test files, read-only `SHOW CREATE TABLE` against
`puzzlebot_test`, git history checks); nothing was mutated.

## Critical

None found.

## High

### H1 — bridge_channels_conn is used concurrently from multiple threads with no lock (same class as the previously-diagnosed SIGABRT crash)

**Store & migrations** · `bot/cogs/bridge.py:111` · confirmed by adversarial trace

*Independently re-found by the second sweep.*

**Evidence.** `_is_puzzle_channel` (bridge.py:104-113) runs `await asyncio.to_thread(self._lookup_puzzle_channel, ...)` directly from `on_message`, and discord.py schedules each listener invocation as its own task, so two messages in uncached channels yield two simultaneous worker threads on the same `bridge_channels_conn` (bridge.py:94-101). The refresher task (`_refresh_loop`, bridge.py:83-89) adds a third concurrent user of the same connection every `_REFRESH_SECONDS = 60` (bridge.py:23) — and it clears `_non_puzzle_channels` on every refresh, so after each refresh every message in every non-puzzle channel re-queries, making concurrent lookups routine in an active guild. `MySQLReconnector` has no locking (bot/store/__init__.py:39-132; grep confirms no `threading.Lock` in either file), and the repo's own comment at bot/store/__init__.py:178-183 records that concurrent cross-thread use of one mysql-connector connection previously caused a SIGABRT heap-corruption crash. The dedicated connection fixed sharing with the event loop's `mydb`, but not sharing of `bridge_channels_conn` between its own threads. (By contrast, `activity_db` and `message_db_conn` are safe: each is drained by a single worker task — activity.py:90-99, bridge.py:188-196.)

**Impact.** Corrupted protocol state or a hard process crash (SIGABRT) under ordinary message load; the window recurs every 60 seconds when the negative cache is cleared.

**Direction.** Serialise access: hold one `threading.Lock` inside `_load_puzzle_channels`/`_lookup_puzzle_channel` (both already run off-loop, so blocking briefly is fine), or route the lazy lookup through the existing single `_drain` worker, or use the web layer's pooled-connection pattern (bot/web/data.py) for these reads.

### H2 — Canvas region link accepts a cross-hunt (even cross-guild) puzzle_id — the one id in the media family with no ownership check

**Media & canvas** · `bot/web/actions.py:2715` · confirmed by adversarial trace

*Found independently by three workstreams (media-canvas, both sweeps; web-input).*

**Evidence.** create_region (actions.py:2713-2737) and update_region (actions.py:2740-2788) pass the client-supplied body puzzle_id straight into bot/core/canvas.py create_region/update_region, whose only validation is _check_link (canvas.py:27-39) — role validity and both-or-neither. Nothing checks the puzzle belongs to the canvas's hunt. Every sibling id on these routes IS checked: media ids via _require_hunt_media at 4 sites (actions.py:2493-2506, 'cross_hunt_media'), evidence target via the cross_hunt_target guard (actions.py:2376, with the comment 'evidence can't be cross-linked into another hunt's puzzle'), and round_id in create_puzzle_from_region (actions.py:3013-3020). Grep confirms cross_hunt tests exist for media/target but none for region puzzle_id. Consequences traced: (a) an informs link calls _log_informs_evidence (canvas.py:42-63) → EvidenceJsonDb.set_target(item.id, puzzle_id) creates an evidence row in hunt A targeting hunt B's puzzle; (b) a locates link calls sync_region_image (puzzle_images.py:79-109) → PuzzleImageJsonDb.create attaches hunt A's crop media to hunt B's puzzle, so hunt B's members see a broken (403/404) image row in their puzzle strip — a write into another hunt's, potentially another guild's, presentation. Additionally a NONEXISTENT puzzle_id hits the CanvasRegionPuzzleID FK (migrations/033_canvases.sql) and surfaces as an uncaught IntegrityError → 500 instead of a 400.

**Impact.** Any authenticated member of any visible hunt can pollute arbitrary puzzles anywhere in the database (evidence rows targeting them, image rows attached to them) by id, violating the hunt-isolation invariant the codebase enforces everywhere else; also a 500 on a stale/bad id.

**Direction.** In both region routes (and ideally once, in canvas.py by passing the expected hunt_id down), verify PuzzleJsonDb.get_by_attr(id=puzzle_id) exists and its hunt_id == ctx hunt id, returning the same 400 family as cross_hunt_target/cross_hunt_media. Add the web tests mirroring test_evidence_web.py:128.

### H3 — Solved target with additional sheets: extras are archived but never flagged, so unsolve writes their archive-resident sheetIds into the MAIN spreadsheet

**Google Sheets** · `bot/cogs/puzzles_gsheet.py:599` · confirmed by adversarial trace

*Independently re-found by the second sweep ("unsolving becomes impossible once a solved-sheet exists").*

**Evidence.** move_puzzle_to_solved_sheet moves EVERY additional sheet to the archive spreadsheet (lines 599-602, no is_target guard) but only non-targets get on_solved_sheet=True (603-605); AdditionalSheetData has no location flag at all (bot/store/puzzle_data.py:15-27), and solve() commits the new archive-side page ids (bot/core/puzzle_service.py:1359-1361). On unsolve, restore_puzzle_from_solved_sheet keys everything on was_archived=puzzle_data.on_solved_sheet (line 530) — False for a target — so lines 539-541 skip moving the extras back, yet lines 542-549 still queue update_cell/move_sheet_to_index/revert_tab_colour for sheet.google_page_id into a batch executed against the MAIN spreadsheet (batch_update(updates) line 562, archive=False). Those ids exist only in the archive spreadsheet.

**Impact.** Unsolving a solved target (meta/capstone) that has an Extra Sheet sends a batchUpdate referencing a nonexistent sheetId -> HTTP 400 (non-retryable) -> restore raises; unsolve() has no try/except around it (puzzle_service.py:1387), so the whole unsolve fails and the DB commit is skipped — the puzzle is stuck solved, and retrying fails identically (no self-heal). In the unlucky case of a numeric sheetId collision, cells are written onto an unrelated tab instead. Related in the same loop: lines 599-602 lack the len(sheet.google_page_id)>0 guard that line 585 has, so an empty page id raises ValueError from int("") in get_page_name_by_id (line 152) mid-archive, leaving the puzzle green-coloured but not moved (caught at puzzle_service.py:1354-1357 and surfaced as SheetRefreshError).

**Direction.** Either stop archiving a target's additional sheets (mirror the is_target guard from line 603 onto the 599 loop), or record the location per additional sheet (an on_solved_sheet column on additional_sheets, migration first per the migration->deploy->code rule) and key restore on that instead of the parent's flag. Add the empty-page-id guard to the 599 loop either way.

### H4 — Group menu 'Convert to feeder' targets the first meta in the column, not the group's own target

**Frontend** · `frontend/src/components/DragBoard.tsx:673` · confirmed by adversarial trace

**Evidence.** onConvertGroupToFeeder does `group.puzzles.find((p) => p.is_meta)`. group.puzzles is built by data.py build_board in get_hunt_puzzles order (ORDER BY is_target DESC, solved ASC, id ASC), and a group's member list can contain puzzles that are targets of OTHER groups (is_meta=true): every metameta's feeders are such metas, as is any shared feeder that is another group's target. Concrete trace: metameta M has target T (id 50) and feeder A (id 10, target of group GA, feeds M). Both rows have is_target=1 and are unsolved, so id ASC puts A before T in group M's puzzle list; find() returns A. The confirm modal, the typed confirm phrase, AND the orphan preview (useConvertToFeederPreview) all then describe/execute convert-to-feeder on A — demoting GA (deleting its round, orphaning GA's feeders) instead of demoting M's target. Two sibling call sites already do it correctly: DragBoard.tsx:355-357 (Column's targetPuzzle) and BoardGroup.tsx:66-68 both use `group.puzzles.find((p) => p.id === group.target_puzzle_id)`.

**Impact.** An organiser using the group kebab's 'Convert to feeder (keep puzzle)…' on any group containing a foreign-target meta gets a destructive demotion of the WRONG metapuzzle. The dialog is internally consistent about the wrong puzzle (name, typed phrase, orphan preview all show it), so the only tell is the unexpected name — easy to type through when the user believes the UI is describing their group.

**Direction.** Replace the predicate with `group.puzzles.find((p) => p.id === group.target_puzzle_id)` (the pattern the two sibling call sites use), and null-guard on a miss.


## Medium

### M1 — Deleting a minimeta parent (via delete_puzzle or delete_round) silently orphans its mini-puzzles

**Core service** · `bot/core/puzzle_service.py:1622` · confirmed by adversarial trace

**Evidence.** delete_puzzle's only guard is `if puzzle.is_target: raise ValueError(...)` (puzzle_service.py:1620-1624); a minimeta parent is not a target, so it deletes cleanly. delete_round's full-delete branch (`to_delete`, :1697-1703) likewise includes a minimeta member tagged only to that group. Minis are deliberately untagged children keyed by parent_puzzle_id (create_mini, :2501+), and migrations/007_minimeta.sql declares `FOREIGN KEY (parent_puzzle_id) REFERENCES puzzles(id) ON DELETE SET NULL` — so the minis' rows survive with parent NULL. get_unassigned (bot/store/mysqldb.py:496-521) filters only on `parent_puzzle_id IS NULL` + no feeds edge, so the orphaned minis (puzzle_type='Mini') then surface on the Unassigned Feeders tab; their channel_ids are thread snowflakes whose parent channel was just deleted (Discord cascades thread deletion), so they dangle. The tidy paths exist (delete_mini :2467, remove_minimeta :2023) but nothing routes a plain delete through them, and no test covers deleting a minimeta parent (tests/test_delete_safety.py only covers thread resolution for the mini itself).

**Impact.** A hunt that deletes a puzzle which happens to own minis (or deletes its round) is left with phantom 'Mini' rows polluting the Unassigned Feeders sheet and the dashboard board, each with a dangling thread channel_id; the puzzle-keyed rollup row does cascade (migration 008) but the mini rows, their sheet tabs and their DB state need manual cleanup.

**Direction.** In delete_puzzle, treat owning minis like being a target: raise (e.g. ValueError('delete its minis first') or auto-cascade through delete_mini). In delete_round, expand to_delete to include minis of deleted members (they are exclusive children by construction).

### M2 — !sync mutates every member puzzle's edges without holding the members' locks (the documented #27 lost-update class)

**Core service** · `bot/cogs/puzzles.py:1879` · confirmed by adversarial trace

**Evidence.** !sync (puzzles.py:1833-1891) runs under with_puzzle_mutex, which locks only the context puzzle (the group's target, if any — puzzles.py:85-119), then loops `await self.service.tag_edge_only(puz, target_group, default_edge, guild=None)` per member (:1879) — writing each member's tags rows and committing the member (tag_with_edge → PuzzleJsonDb.commit). The repo's own rule for group-wide edge mutations is one sorted puzzles_lock over all members: bulk_re_edge's docstring (puzzle_service.py:965-975, issue #27) explains that a concurrent locked command holding a pre-flip member snapshot commits later and its bidirectional tag-sync (mysqldb.py:661-704, db_pairs − mem_pairs delete) silently reverts the flip; the convert/delete/archive edges all take puzzles_lock(convert_lock_ids(...)) for exactly this reason, and the evidence-link paths were fixed the same way (636bb82). !sync is the one remaining group-wide edge mutation with no member locks.

**Impact.** A !solve/!retag/dashboard action in flight on any member while !sync runs can silently undo that member's just-synced edge (or the sync can clobber the in-flight command's edge write) — no error, wrong membership until re-synced.

**Direction.** Take `puzzles_lock([p.id for member channels] )` (one sorted acquisition, dropping with_puzzle_mutex on this command like the convert commands do) around the sync loop.

### M3 — Reconcile creates replacement channels at guild root for puzzles homed in a channel-home group (service._home_category lacks the parent-category fallback)

**Core service** · `bot/core/puzzle_service.py:416` · confirmed by adversarial trace

**Evidence.** PuzzleService._home_category (:416-425) resolves home_group.category_id only against guild.categories. For a channel-home group (in-place-converted meta: category_id is a text-channel id, e.g. convert_to_type promote sets category_id=puzzle.channel_id at :1026), it returns (None, group). category_layout._home_category (category_layout.py:82-89) has the correct fallback (`guild.get_channel(...).category`) for precisely this case. In _reconcile_channel step 3 (:481-497) a missing channel is then recreated with category=None — i.e. at guild root, outside the hunt's role-gating overwrites — the same M6 failure mode the _hunt_category_ids docstring (:559-573) says was fixed for the relink step.

**Impact.** Running !reconcile apply on a hunt where a channel of a channel-home meta's feeder has gone missing recreates the channel at guild root, visible to @everyone on a gated hunt, and outside the category layout.

**Direction.** Use category_layout._home_category's channel-parent fallback in PuzzleService._home_category (or delegate to it), so step 3 lands the recreated channel in the host category. Also consider renaming one of the two identically-named helpers — they have different contracts.

### M4 — !update_settings can write junk role-id columns, permanently locking every admin out of both surfaces

**Discord cogs** · `bot/cogs/puzzles.py:2634` · confirmed by adversarial trace

*Independently re-found by the web-auth sweep (as the third, unvalidated write path to admin_role_ids).*

**Evidence.** update_settings (puzzles.py:2634-2712) sets ANY existing str attribute on the resolved settings object. In any non-hunt channel get_settings() returns guild_data (GuildSettings), whose admin_role_ids/organiser_role_ids are str fields (bot/store/puzzle_settings.py:23-24), so `!update_settings admin_role_ids 99999` (or `banana`) commits straight through GuildSettingsDb.commit. role_gate deliberately hard-gates configured-but-junk ids: _stored_ids returns a configured frozenset for any non-empty string and never falls back to names (bot/core/role_gate.py:34-52, and the module docstring: "a guild that configured ids and stored nothing but junk gates everyone out"). The two dedicated write paths both guard exactly this lockout — !set_admin_roles refuses ids the caller doesn't hold (puzzles.py:2599-2604) and the web PATCH rejects foreign ids and would_lock_you_out (bot/web/actions.py:1866-1890) — but update_settings bypasses both guards.

**Impact.** One typo'd admin command bricks the admin tier guild-wide: every is_admin() bang command (including !set_admin_roles and !update_settings itself) and the web require_admin gate all resolve through the same role_gate, so nobody can undo it without direct DB access.

**Direction.** Denylist admin_role_ids/organiser_role_ids (and arguably guild_id/id/guild_name, mirroring the web _GUILD_SETTINGS_KEYS whitelist) in update_settings, directing users to !set_admin_roles/!set_organiser_roles which carry the validation.

### M5 — Bang !convert_to_puzzle takes no lock at all — the one convert edge missed by the H4/H5 fixes

**Discord cogs** · `bot/cogs/puzzles.py:2414` · confirmed by adversarial trace

**Evidence.** convert_to_puzzle (puzzles.py:2409-2440) carries neither with_puzzle_mutex nor any puzzles_lock: it calls service.convert_to_puzzle(ctx.guild, hunt, puzzle) at :2429 on the pre-invoke env snapshot, with no re-read. Its sibling _run_convert_to_type (:2213-2237) explicitly acquires puzzles_lock(convert_lock_ids(puzzle_id=...)) and re-reads the puzzle under it, with a comment saying converts "deliberately carry no with_puzzle_mutex ... so this is their only acquisition"; the web twin convert_to_feeder at least holds puzzle_lock(puzzle_id) (bot/web/actions.py:1321). service.convert_to_puzzle is lock-free by design, deletes the tag_group row and commits the full puzzle row (puzzle_service.py:1436-1490).

**Impact.** A concurrent locked write (e.g. !solve or a dashboard status change holding the puzzle lock) can be silently clobbered when the demote commits its stale snapshot — the exact H4 lost-update class the other edges were levelled to fix; a concurrent group-member mutation can also race the group-row delete (the IntegrityError-on-deleted-group case convert_to_puzzle's own comments describe).

**Direction.** Wrap the body in `async with puzzles_lock(convert_lock_ids(puzzle_id=puzzle.id))` and re-read the puzzle under the lock, exactly as _run_convert_to_type does; consider widening the web convert_to_feeder from puzzle_lock to the same convert_lock_ids set for symmetry.

### M6 — !set_hunt_role resolves hunts by name guild-unscoped — an admin in one guild can mutate another guild's hunt row

**Discord cogs** · `bot/cogs/puzzles.py:745` · confirmed by adversarial trace

**Evidence.** set_hunt_role does `hunt = HuntJsonDb.get_by_attr(name=hunt_name)` (:745) with no guild filter — get_by_attr is a bare `WHERE name=%s` (bot/store/mysqldb.py:229-237) and hunts carry a guild_id that is never consulted. service.set_hunt_role then commits `hunt.role_id = team_role.id` using a role created/resolved in ctx.guild (puzzle_service.py:3380-3394) before applying overwrites. The !hunt duplicate check at :678 (HuntJsonDb.check_duplicates) is likewise global (mysqldb.py:175-178), so hunt names are one namespace across every guild the bot serves.

**Impact.** On a multi-guild deployment (the Settings cog explicitly provisions many guilds), an admin of guild B running !set_hunt_role with a name that matches guild A's OPEN hunt writes a guild-B role id onto guild A's hunt row — gating guild A's hunt behind a role none of its members can hold (hidden on the dashboard, overwrites unapplied). Also, hunt-name creation is first-come-first-served across guilds.

**Direction.** Scope the lookup to the invoking guild (join hunts→guilds on ctx.guild.id, or add a get_by_name_for_guild) and make !hunt's duplicate check guild-scoped; refuse with a clear message when the name matches only another guild's hunt.

### M7 — Route-gating test map misclassifies 6 canvas/region routes as organiser; guardrail blind to ungating update_canvas/delete_canvas

**Web routes** · `tests/test_web_route_gating.py:126` · confirmed by adversarial trace

**Evidence.** The AST resolver gate() (126-141) text-matches 'require_organiser' anywhere in a function's call graph. _canvas_ctx (bot/web/actions.py:2474-2490) calls require_organiser only inside `if require_manage:`. Callers passing require_manage=False (create_region, update_region, delete_region, retile_canvas, ocr_canvas_region, create_puzzle_from_region) are UNGATED at runtime, yet the resolver reports them 'organiser' and EXPECTED (lines 63-71) asserts 'organiser'. Proof: tests/test_canvas_web.py:160-173 (test_a_non_organiser_can_draw) asserts POST /canvases/3/regions returns 201 with require_organiser patched to 403 (runtime-ungated), while the gating test asserts the same route is organiser-gated. Both pass. Because the resolver cannot see the require_manage condition it also cannot detect a genuine ungating of update_canvas/delete_canvas (both call _canvas_ctx with require_manage=True): flipping that to False would leave the destructive delete open and keep the test green.

**Impact.** The map meant as the single source of truth for who-can-do-what overstates protection on 6 member-tier routes and is structurally unable to catch a real gate removal on update_canvas/delete_canvas (delete destroys a shared image + its regions/evidence). A reviewer trusting the map is misled; the regression the test exists to prevent can slip through.

**Direction.** Make _canvas_ctx gating unconditional per-route (split into gated and ungated helpers, or move require_organiser out to each managing route) so the resolver reflects reality, and update EXPECTED to list the region/retile/ocr/create-puzzle-from-region routes as ungated (matching test_canvas_web) so the two suites agree.

### M8 — Shared puzzlebot_test DB is ahead of the branch's tracked migrations: 043/044 applied from another branch, files absent on staging

**Store & migrations** · `migrate.py:137` · confirmed by adversarial trace

**Evidence.** `schema_migrations` on the live test DB records `043_feeder_pool.sql` and `044_feeder_tab.sql` as applied, and `puzzles` carries a `feeder_tab_page_id` column (SHOW CREATE TABLE), but `migrations/` on staging ends at 042 and no code or SQL in this tree references `feeder_tab_page_id` (repo-wide grep: zero hits). `git log --all` shows the two files live on the `meta-matching`/`matrix-spike` branches (commits 677014a, ac27d4d). `migrate.py status` on staging reports them as "[applied, file missing]" orphans (migrate.py:137-139).

**Impact.** Staging's live-DB test family runs against a schema staging cannot reproduce from scratch; a fresh DB built from staging's migrations diverges from the DB the tests were green on. A future staging migration that takes the 043/044 numbers with different content will interleave by filename sort against what the DB already recorded — the known parallel-worktree number-collision hazard. The extra nullable column is harmless today, but the migration → deploy → code ordering is now entangled across branches sharing one DB.

**Direction.** **Decided 2026-08-30: leave `meta-matching` alone; 043/044 are permanently reserved and staging's next migration is `045`.** Safe because `migrate.py` tracks by filename, not number — gaps are inert, a lower-numbered file arriving later still applies (no silent skip), and the `[applied, file missing]` orphans are `cmd_status` cosmetics `cmd_migrate` never reads. The residual half — `puzzlebot_test` not being reproducible from staging's migrations alone — is **accepted**: staging references `feeder_pool` and `feeder_tab_page_id` zero times, and the objects are two unused tables plus one nullable column. Rebuilding the test DB from 000–042 remains the option if reproducibility is ever wanted. No code change in this tree.

### M9 — _fetch_one-backed lookups without LIMIT 1 raise InternalError('Unread result found') the moment a de-facto-unique column gains a duplicate

**Store & migrations** · `bot/store/mysqldb.py:233` · confirmed by adversarial trace

**Evidence.** `_fetch_one` (mysqldb.py:151-158) does `fetchone()` then `close()` on an unbuffered dict cursor; mysql-connector's `MySQLCursor.close()` calls `handle_unread_result()`, which raises when rows remain (verified in the installed connector 26.7.0, and the codebase documents exactly this failure at mysqldb.py:1280-1285). Half the DAOs guard with LIMIT 1 (TagGroup get_by_attr :246, Rollup :385, check_duplicates :177, get_by_category :287); the other half do not: MySQLHuntJsonDb.get_by_attr (:233), MySQLPuzzleJsonDb.get (:424) and get_by_attr (:433), MySQLGuildSettingsDb.get (:1296). The unguarded queries filter on columns with no UNIQUE constraint — hunts.name (only the racy app-side check_duplicates, service:3412), puzzles.channel_id (none), guilds.guild_id (none; creation is check-then-insert at cogs/settings.py:98-100).

**Impact.** A single duplicate row — which the missing constraints permit under a create race — converts every subsequent lookup on that value from "picks one row" into an InternalError that aborts the command's before-hook (looks like the bot hanging, per the codebase's own note).

**Direction.** Append LIMIT 1 (with a deterministic ORDER BY, as TagGroup/Rollup already do) to the four unguarded single-row queries, or have _fetch_one enforce it. The knowledge "single-row fetches must LIMIT 1 or close() raises" is currently applied in only half the DAOs.

### M10 — Both OCR routes bypass the shared image-work semaphore, defeating the documented memory bound

**Media & canvas** · `bot/web/actions.py:3116` · confirmed by adversarial trace

**Evidence.** media_tiling.run_bounded_image_work (media_tiling.py:41-52) exists so that every full-decode image operation shares MAX_CONCURRENT_TILING=2: 'Sharing one semaphore is what makes MAX_CONCURRENT_TILING a real bound on the box's footprint rather than a bound on one code path' (media_tiling.py:46-48). _crop_region_media honours it (actions.py:2567-2568). But ocr_canvas_region runs _crop_and_extract_text — crop_bytes, a full decode of an up-to-300MP canvas source (~360MB peak per image_tiler.py:24-27 measurements), plus tesseract — via bare loop.run_in_executor(None, ...) (actions.py:3116-3117), and ocr_evidence_preview does the same with extract_text (api.py:429). Both routes are member-level (not organiser-gated) and the default executor allows min(32, cpus+4) concurrent workers.

**Impact.** A handful of concurrent OCR clicks (or one hostile member looping the endpoint) runs several ~360MB full decodes simultaneously, on top of up to two tiling builds — multi-GB peak on the small Plesk VPS, risking OOM of the whole bot (gateway included). This is precisely the failure mode the semaphore was built to prevent.

**Direction.** Route both OCR executor hops through run_bounded_image_work (it already takes fn,*args). No behaviour change beyond queueing.

### M11 — create_puzzle_from_region regenerates the region crop even when the existing crop carries markup — diverging from update_region's ink guard and destroying the ink via GC

**Media & canvas** · `bot/web/actions.py:3057` · confirmed by adversarial trace

**Evidence.** update_region PATCH guards re-cropping: 'Ink is drawn on one specific image: regenerating the crop underneath it would leave every existing mark pointing at nothing' — it reuses existing.crop_media_id when markup.list_marks(existing.crop_media_id) is non-empty (actions.py:2772-2776). create_puzzle_from_region performs the same link change (via the same canvas_service.update_region) but crops unconditionally: actions.py:3053-3057 has no marks check. Trace of the loss: region previously linked (crop 42 exists, marks drawn on it) → create-puzzle-from-region → new crop row created, canvas.update_region overwrites crop_media_id (canvas.py:113-116), deletes the old evidence row on link change (canvas.py:117-118) → crop 42 is now referenced by nothing → after 24h media_gc sweeps it and media_markup rows CASCADE (034_media_markup.sql), erasing the ink permanently.

**Impact.** A member's annotations on a region's crop are silently destroyed when someone promotes that region to a puzzle — the exact hazard the sibling path documented and defended against.

**Direction.** Apply the same guard before cropping in create_puzzle_from_region: reuse existing.crop_media_id when it exists and carries marks; extract the four-line decision into a helper shared by both routes so the third caller cannot drift again.

### M12 — reconcile() performs one full spreadsheets().get per puzzle — N sequential full-metadata reads for an N-puzzle hunt

**Google Sheets** · `bot/core/puzzle_service.py:512` · confirmed by adversarial trace

**Evidence.** _reconcile_tabs._tab_present (lines 508-517) calls gsheet_cog.get_page_name_by_id(page_id, spreadsheet), which is implemented as a linear scan over sheet_list() (puzzles_gsheet.py:150-153) — and sheet_list() is a full spreadsheets().get returning every tab's properties AND native tables (puzzles_gsheet.py:104-118). reconcile() runs this inside the per-puzzle loop (lines 534-544), so a 100-puzzle hunt issues 100+ sequential full gets even when every tab is present, all serialized through the process-wide _execute_lock and asyncio.to_thread.

**Impact.** Sheets read quota is 60 requests/min/user: a large hunt's !reconcile blows through it, triggering 429 + exponential backoff (up to 60s waits per retry in bot/utils/gsheets.py), turning an admin command into a multi-minute stall — exactly the '50 rounds shouldn't mean 50x sequential calls' failure mode. The data needed (which sheetIds exist) is identical across all N calls.

**Direction.** Fetch sheet_list once per spreadsheet (main + archive) at the top of reconcile(), build a set of present sheetIds, and make _tab_present a set lookup. _collect_orphan_tabs already fetches the same snapshot once (line 621) and could share it.

### M13 — Each target render does two full spreadsheet reads and three sequential batchUpdates; !rebuild multiplies this per group

**Google Sheets** · `bot/cogs/puzzles_gsheet.py:1495` · confirmed by adversarial trace

**Evidence.** render_contribution -> _ensure_contribution_table does a full sheet_list (line 1495) to check the Table exists; reconcile_table then does a second full sheet_list (line 1173) of the same spreadsheet moments later, plus a values().get (1210) and up to THREE sequential batchUpdates: data writes (1276), table resize (1283), and the grey-below formatting (1320) — the last two are always separate calls even though batchUpdate is atomic and could carry all requests at once. !rebuild (bot/cogs/puzzles.py:888-893) runs refresh_target sequentially over every group, so a 50-round hunt is ~250-300 sequential API calls (~100 full-metadata reads, ~150 writes against the 60 writes/min/user quota).

**Impact.** Bulk rebuilds and multi-meta refreshes (retag cascades, convert) run into 429/backoff churn and take minutes; each solve of a multi-tagged feeder already pays ~6 calls per group. The duplicated sheet snapshot per render is pure waste — reconcile_table re-reads what _ensure_contribution_table just fetched.

**Direction.** Pass the sheet snapshot (or at least the located table) from _ensure_contribution_table into reconcile_table, and fold the resize + grey requests into the data batchUpdate (one write instead of three). That alone roughly halves the calls per render without touching behaviour.

### M14 — refresh_evidence lazy tab creation wedges permanently when an 'Evidence' tab already exists — the sibling unassigned path has the fallback, this one doesn't

**Google Sheets** · `bot/core/puzzle_service.py:3048` · confirmed by adversarial trace

*Found independently by the core-service and gsheets workstreams.*

**Evidence.** refresh_unassigned wraps its create_blank_tab in try/except with an explicit reuse-by-name fallback and the comment 'A tab with this name may already exist from an earlier run — reuse it rather than failing (addSheet errors on duplicate names)' (lines 3229-3236). refresh_evidence's identical lazy create (lines 3048-3051) has no such fallback: if hunt.evidence_sheet_page_id is None but a tab titled 'Evidence' exists (crash between tab creation and HuntJsonDb.commit at 3059, two concurrent first-time create_task triggers, or a manually-added tab — 'Evidence' is even listed in RESERVED_TAB_TITLES at line 601), create_blank_tab raises 400 on the duplicate title.

**Impact.** Every subsequent refresh_evidence fails the same way forever (evidence_sheet_page_id stays None), so the Evidence tab silently never renders again — refresh_evidence_safe only logs to discord.log. No self-heal short of manually deleting the tab or hand-editing the DB.

**Direction.** Copy the unassigned fallback: on create failure, get_page_id_by_name('Evidence') and reuse it, re-raising only if that also returns None.

### M15 — Additional-sheet links keyed on puzzle.solved instead of actual sheet location — dead links on hunts without a solved sheet

**Google Sheets** · `bot/cogs/puzzles.py:548` · confirmed by adversarial trace

**Evidence.** get_additional_sheet_spreadsheet (542-551) returns hunt.solved_sheet_id whenever puzzle.solved — but the extras only MOVE there when a solved sheet exists (puzzles_gsheet.py:600 `if self.get_solved_sheet_id()`). HuntData.solved_sheet_id defaults to "" and create_hunt's create_solved_sheet defaults to False (puzzle_service.py:3398), so on a default hunt a solved puzzle's extras stay on the live spreadsheet while create_additional_sheet_list (1801-1812) builds urls.spreadsheet_url("") → 'https://docs.google.com/spreadsheets/d//edit#gid=…'. Contrast get_puzzle_sheet (528-540), which correctly keys the main tab on puzzle.on_solved_sheet. Adding a solved sheet mid-hunt (bang line 802 / web actions.py:1144 do no backfill) makes previously solved puzzles' extras links point at the new empty archive too.

**Impact.** !list_additional_sheets / !info on any solved puzzle with extra sheets shows broken or wrong links in every hunt without an archive sheet (the default configuration), and in hunts that add one mid-hunt.

**Direction.** Key the link on actual location like get_puzzle_sheet does: return solved_sheet_id only when the hunt has one AND the extras were actually moved (simplest faithful proxy today: `puzzle.solved and hunt.solved_sheet_id`, falling back to google_sheet_id; better, a per-sheet location flag shared with the fix above).

### M16 — remove_minimeta's demote guard matches ANY native Table, not the bot's — a solver's table triggers deletion of columns A-D of an unpromoted tab

**Google Sheets** · `bot/core/puzzle_service.py:2054` · confirmed by adversarial trace

**Evidence.** The guard is `has_table = any(bool(s.get("tables")) ...)` — true for any table on the tab. The codebase's own discipline (bot/core/sheet_tables.py:31-48 select_table, and _ensure_contribution_table at puzzles_gsheet.py:1494 which passes contribution_table_name(page_id)) exists precisely because solvers add their own Tables and tables[0]/any-table is a guess. If a minimeta was created but never rendered (no promote, plain layout, comment at 2048-2049 acknowledges this state) and a solver added their own Table to the tab, remove_minimeta runs demote_tab_to_puzzle, whose deleteDimension removes columns A-D (puzzles_gsheet.py:713-719) — real solver-grid columns on an unpromoted tab.

**Impact.** Destructive: four leftmost columns of solver work deleted from a tab that was never in target layout, plus the header regeneration stamps over rows 1-3. Requires a never-rendered minimeta plus a solver-added Table — narrow but the exact failure class select_table was built to prevent.

**Direction.** Guard on the bot's own table: `select_table(s.get("tables"), contribution_table_name(page_id))` (mirroring _ensure_contribution_table), not bool(tables).

### M17 — select_table's lone-table fallback re-opens the write-into-solver's-table bug when contribution-table creation was deferred

**Google Sheets** · `bot/core/sheet_tables.py:46` · confirmed by adversarial trace

**Evidence.** select_table returns a sole unnamed Table on the assumption 'the bot is what put a Table on a target tab in the first place' (lines 42-46). But generate_puzzle_tab explicitly tolerates the contribution-table add failing (puzzles_gsheet.py:1390-1399: 'contribution Table deferred... created on first render'). In that window the target tab has NO bot table; if a solver adds one (the documented real behaviour this module exists for), the next render's _ensure_contribution_table (1487-1494) sees a single table, select_table's len==1 fallback claims it as ours, creation is skipped, and reconcile_table (1185) then writes the target's rows into the solver's table and resizes it over them — the exact incident the module docstring describes.

**Impact.** Feeder/evidence rows overwrite a solver's own table on a meta tab; low probability per tab (needs the deferral + a solver table before first render) but the damage is user-data corruption and it defeats the fix's stated guarantee.

**Direction.** Restrict the lone-table fallback to tabs known to predate table naming (e.g. only when the caller passes name=None, or gate it behind an explicit legacy flag); for the contribution path, treat 'one table, wrong name' on a tab the bot generated as 'not ours' and create the named table alongside.

### M18 — promote_tab_to_target is not idempotent: a retry after partial failure inserts 4 more columns each time

**Google Sheets** · `bot/cogs/puzzles_gsheet.py:1440` · confirmed by adversarial trace

**Evidence.** promote_tab_to_target does insertDimension of CONTRIBUTION_WIDTH columns (1440-1447) then header re-stamp then _add_contribution_table (1477). If step 4 fails (the same transient-500 class generate_puzzle_tab defends against at 1390-1399), the tab is left promoted-but-tableless. render_minimeta_contribution (1530-1536) re-detects only `has_table` by table name and calls promote_tab_to_target AGAIN — inserting another 4 columns and shifting the solver grid right on every failed attempt. The non-minimeta path is safer only by luck: _ensure_contribution_table (1495-1516) checks A1 for the plain-header label before promoting, so a half-promoted tab (blank A1) skips the second insert.

**Impact.** Repeated Sheets flakiness on a minimeta render walks the solver grid rightwards 4 columns per failure and stacks stale header stamps; self-inflicted layout corruption with no self-heal.

**Direction.** Make promote idempotent: detect the already-shifted geometry (e.g. A1 blank + B1 'Puzzle Title:' label at offset 1, the same check _ensure_contribution_table uses) and skip the insertDimension, going straight to _add_contribution_table; use _ensure_contribution_table from render_minimeta_contribution instead of its own weaker has_table check.

### M19 — move_puzzle_sheet (copy → delete → rename) has no compensation; failure after the delete leaves the DB pointing at a deleted tab

**Google Sheets** · `bot/cogs/puzzles_gsheet.py:506` · confirmed by adversarial trace

**Evidence.** Lines 506-527: copyTo to the destination, then delete_sheet of the original, then get_unique_tab_name + rename of the copy — four sequential API calls. The new sheetId only reaches the caller via the return value, and callers assign it on success (e.g. line 601 `sheet.google_page_id = await self.move_puzzle_sheet(...)`, solve commits after — puzzle_service.py:1359-1361). If the rename (or the name lookup) fails permanently after the delete, the exception propagates, the assignment never happens, and the committed google_page_id references a tab that no longer exists anywhere, while an unrenamed 'Copy of X' tab sits on the destination. _reconcile_tabs (puzzle_service.py:499-523) recreates only the MAIN tab, not additional sheets, so a stranded extra-sheet pointer has no repair path.

**Impact.** Every later write addressed at the dangling page id 400s (update_solution, restore, info links); for additional sheets there is no reconcile recovery. Rare (needs a non-retryable failure or 5-retry exhaustion mid-sequence) but permanent when it lands.

**Direction.** Capture the copied sheetId onto the puzzle/sheet object (or return it) immediately after copyTo, before the delete; do the rename before the delete of the original (rename failure then leaves a consistent 'both exist' state), and let the caller commit the new id even when a later step fails.

### M20 — delete_hunt deletes every puzzle's tab individually, then deletes the whole spreadsheet files anyway

**Google Sheets** · `bot/core/puzzle_service.py:3501` · confirmed by adversarial trace

**Evidence.** Lines 3497-3505 loop `for puzzle in puzzles: await gsheet_cog.delete_puzzle_spreadsheet(puzzle)` — one batchUpdate per puzzle, serialised behind the global execute lock. Lines 3553-3558 then delete both Drive files wholesale (`delete_hunt_spreadsheet(hunt.google_sheet_id)` and `...solved_sheet_id`), which removes every tab those N calls just deleted.

**Impact.** A 100-puzzle hunt spends ~100 pointless sequential Sheets write calls (plus retries when they trip the 60-writes/min quota) making delete_hunt take minutes and burning shared quota, for zero effect on the outcome.

**Direction.** Drop the per-puzzle tab deletion in delete_hunt entirely (keep the DB/channel loop); the two files().delete calls already cover it. Keep per-tab deletion only where the spreadsheet survives (delete_round/delete_puzzle).

### M21 — Bulk sheet operations issue one API call per item instead of batching requests — 50 items means 50+ sequential round-trips

**Google Sheets** · `bot/core/puzzle_service.py:1710` · confirmed by adversarial trace

**Evidence.** batchUpdate accepts many requests per call, but: delete_round deletes one tab per batchUpdate per puzzle (1706-1712); !rebuild for all rounds (bot/cogs/puzzles.py:886-893) runs refresh_target per group sequentially, each costing ~7 calls — two full sheet_list GETs (one in _ensure_contribution_table puzzles_gsheet.py:1487, another in reconcile_table:1173) + values get + write batch + resize batch + grey batch; move_puzzle_to_solved_sheet re-fetches sheet_list once per additional sheet just to compute the same end index (puzzles_gsheet.py:578, 586); every sheet_list GET requests the full spreadsheet metadata with no fields= filter (line 106-108).

**Impact.** A 50-round !rebuild is ~350 sequential API calls behind one global lock: minutes of wall time and guaranteed 429 backoff against the default 60-write-requests/min quota, during which every other hunt's Sheets work on the bot queues behind the same threading lock.

**Direction.** Batch homogeneous requests (all deleteSheet ops for a round in one batchUpdate); share one sheet_list snapshot per refresh_target (pass it from _ensure_contribution_table into reconcile_table); compute the end index once per move; add fields='sheets(properties,tables)' to the get.

### M22 — refresh_unassigned / refresh_evidence run concurrently with no lock — read-modify-write races on one shared table

**Google Sheets** · `bot/core/puzzle_service.py:3212` · confirmed by adversarial trace

*First sweep rated this low; promoted after the adversarial trace confirmed the interleaving write race.*

**Evidence.** Meta rollups are serialised via rollup_lock (bot/core/locks.py:158, taken in refresh_target:682) but refresh_unassigned (3212-3266) and refresh_evidence (3035-3080) take no lock, and are fired as fire-and-forget asyncio.create_task from ~20 mutation sites across bot/web/actions.py and bot/cogs/puzzles.py (e.g. actions.py:548,595,668; puzzles.py:855,1917). reconcile_table is read-then-write: two interleaved runs both read the key column (puzzles_gsheet.py:1209), compute the same next_row (1265), and the second write overwrites the first's appended row; interleaved lazy-creation can also double-create the tab/table (the addSheet-duplicate fallback at 3233-3237 catches only part of it, and a lost update between the two `HuntJsonDb.commit(hunt)` calls can persist a page id from one task and a table id from another).

**Impact.** Under rapid mutations (bulk tagging, several solves) the Unassigned/Evidence table can transiently lose or duplicate a row; it self-heals on the next trigger unless the last two reconciles were the ones racing, in which case the table stays wrong until the next mutation. Not data-destroying (column E is untouched) but visibly flaky.

**Direction.** Serialise per hunt exactly as rollups do: wrap refresh_unassigned/refresh_evidence bodies in a keyed asyncio lock (e.g. rollup_lock(("unassigned", hunt.id)) — the key space is already generic).

### M23 — "very high" priority renders and sorts identically to "medium"

**Frontend** · `frontend/src/util.ts:165` · confirmed by adversarial trace

**Evidence.** Canonical PRIORITIES (bot/core/constants.py:37, migration 041) is ["low","medium","high","very high"], and the dashboard's own picker offers all four (frontend/src/components/PuzzleEditCells.tsx:28 PRIORITY_OPTIONS). But util.ts PRIORITY_RANK = { high: 0, low: 2 } (line 165) gives "very high" the ?? 1 default — the same rank as "medium" — so sortPuzzles orders very-high puzzles BELOW high ones; and priorityColor (lines 138-147) has no "very high" case, so it gets the default blue dot, identical to medium.

**Impact.** The highest priority in the vocabulary is visually indistinguishable from medium and sorts as medium on every board/table/arrange view — inverting triage exactly when it matters. Bites whether the priority was set from the web picker or !priority in Discord.

**Direction.** Add "very high" to both maps (rank -1 or shift others; a distinct colour, e.g. a stronger red than "high"). Consider deriving both from one PRIORITIES-ordered list in lib/ with a unit test, so the next vocabulary change cannot half-land again.

### M24 — SolveModal keeps typed answer text across puzzle switches — one click submits puzzle A's answer to puzzle B

**Frontend** · `frontend/src/components/PuzzleEditCells.tsx:214` · confirmed by adversarial trace

**Evidence.** The reset effect (lines 214-219, deps [puzzle?.id]) resets only changeText and advancedOpen. fullText/partialText/addText are cleared only in submit's onSuccess (lines 172-175). PuzzleTable/BoardGroup keep one SolveModal instance and swap the `puzzle` prop (PuzzleTable.tsx:434, BoardGroup.tsx:338).

**Impact.** Type an answer for puzzle A, close without submitting, open Solve for puzzle B: the Answer field still holds A's text and the Solve button is enabled (disabled={!fullText.trim()}, line 264) — one click/Enter posts A's answer as B's solution (SOLVED embed to the channel, sheet archived). Same state-leak family as the fixed CanvasView key bug.

**Direction.** Reset all four text fields in the [puzzle?.id] effect (or key the modal's content by puzzle id).

### M25 — PuzzleDrawer field state (note draft, rename editor, sheet name, link URL) survives switching puzzles

**Frontend** · `frontend/src/components/PuzzleDrawer.tsx:57` · confirmed by adversarial trace

**Evidence.** text, sheetName, linkUrl, renameEditing/renameTemp (lines 57-64) are component state in the single drawer instance (HuntBoard.tsx:219); no effect resets them when `puzzle` changes. E.g. click rename on puzzle A (renameTemp = A's name), close the drawer, open puzzle B: renameEditing is still true and the title renders a TextInput holding A's name — Enter renames B to A's name (line 124-134). A note typed for A is submitted to B the same way.

**Impact.** Cross-entity data corruption from ordinary UI flows: wrong-puzzle renames and notes. Lower blast radius than the solve case (notes/renames are visible and reversible) but the same defect shape.

**Direction.** Reset the drawer's local state on puzzle?.id change, or key an inner content component by puzzle.id so React remounts it.

### M26 — convert-to-feeder demote is the one group-wide mutation still outside the H5 sorted member-lock acquisition (web: single puzzle_lock; bang: no lock at all)

**Cross-cutting** · `bot/web/actions.py:1323` · confirmed by adversarial trace

**Evidence.** Every other group-wide mutation edge takes puzzles_lock(convert_lock_ids(...)) — web convert_to_meta (actions.py:752), _convert_to_type_handler for capstone/runaround/group/metapuzzle (actions.py:1384), change_group_type (actions.py:1733), bang _run_convert_to_type (bot/cogs/puzzles.py:2226), plus delete_round/delete_hunt/archive_hunt (puzzles.py:3885/3976/4076, actions.py:1572/1630). The demote pair is the straggler: web convert_to_feeder holds only `async with puzzle_lock(puzzle_id)` (actions.py:1323, docstring even says "puzzle-locked" citing the solve family), and bang !convert_to_puzzle (puzzles.py:2414-2440) holds no lock and no with_puzzle_mutex. Yet service.convert_to_puzzle (bot/core/puzzle_service.py:1436) deletes the tag_group row — cascading every member's tags rows — and bulk_re_edge's comment (puzzle_service.py, '#27') now pins the invariant "The CALLER holds them -- this method takes no lock of its own" (the old internal-locking exception was removed in the H5 work; the MEMORY note about it is superseded).

**Impact.** A concurrent member retag/solve (holding only that member's lock — retag is member-open on the web) can interleave with a demote: the member's in-memory tags go stale after the cascade delete, and its next commit's tag-sync re-INSERTs a row against the deleted group — the exact IntegrityError mode convert_to_puzzle's own comment describes for the demoted puzzle itself. The bang path additionally races writes to the demoted puzzle's own row.

**Direction.** Move both demote edges onto the sibling pattern: `async with puzzles_lock(convert_lock_ids(puzzle_id=...))` in web convert_to_feeder, and route bang !convert_to_puzzle through _run_convert_to_type-style locking (or give it the same puzzles_lock block). bulk_re_edge no longer locks internally, so there is no deadlock in widening these.

### M27 — unsolve is the odd sibling of the solve family: post-commit sheet-refresh failures are swallowed, the bang's SheetRefreshError catch is dead code, and '!unsolve' reports 'Sheets all restored.' falsely

**Cross-cutting** · `bot/core/puzzle_service.py:1397` · confirmed by adversarial trace

**Evidence.** service.solve collects per-meta refresh failures and raises SheetRefreshError after commit (puzzle_service.py:1319-1331, 1365-1366); both edges surface it as a warning toast (bang puzzles.py:3489-3511, web actions.py:530-537). service.unsolve wraps the same refresh_target loop in try/except Exception with only logger.exception (puzzle_service.py:1397-1403) and raises nothing. Consequently the bang !unsolve's `except SheetRefreshError` + _warn_stale_sheets (puzzles.py:3655-3657) is unreachable, and it unconditionally prints ":white_check_mark: Sheets all restored." (puzzles.py:3663) even when a meta sheet kept the unsolved answer; web unsolve (actions.py:2345-2347) likewise has no sheet_warning path (and no HttpError catch, unlike web solve).

**Impact.** After an unsolve whose meta refresh fails, the meta sheet still shows the retracted answer; the operator is told everything was restored (the durable dashboard badge is recorded, but the toast/warning the solve path deliberately provides is silently absent). The dead catch shows the edges were written expecting parity that the service does not deliver.

**Direction.** Mirror solve in service.unsolve: collect failed group ids in the refresh loop and raise SheetRefreshError at the end (mutation already committed), which makes the existing bang catch live and lets the web route return sheet_warning like its solve sibling.

### M28 — hunt_create_lock duplicate-name critical section exists only on the web edges — bang !puzzle and bang round-create do check-then-create unlocked in the same process

**Cross-cutting** · `bot/cogs/puzzles.py:833` · confirmed by adversarial trace

**Evidence.** Web creates wrap check+create in hunt_create_lock: create_puzzle (actions.py:2114), create_meta (actions.py:2182, comment: "Same per-hunt create lock as create_puzzle"), create_puzzle_from_region (actions.py:3038, comment: "Locking mirrors the /hunts/<uid>/puzzles route exactly"). The bang edges never adopted it: !puzzle does check_duplicates_in_hunt then service.create_puzzle with no lock (puzzles.py:824-855; with_puzzle_mutex no-ops when no puzzle is in context per locks.py), and _round_impl calls service.create_tag_group with no lock (puzzles.py:2126). cogs/puzzles.py:42 imports hunt_sheet_lock but not hunt_create_lock. Both edges run on the same event loop, so the lock registry would cover cross-surface races if taken.

**Impact.** A bang create racing a web create (or two bang creates from a double-send) can both pass the duplicate-name check and create two puzzles/rounds with the same name — the precise race the lock's docstring says it was built to close, currently closed for web-vs-web only.

**Direction.** Take hunt_create_lock(hunt.id) around the check+create in the bang !puzzle path and around service.create_tag_group in _round_impl (or push the lock into the service's create seam so all edges inherit it).

### M29 — actions.py re-rolls the route prologue (resolve→archived→gate→cog→guild) ~30 times by hand while the newer route families already built the ctx-helper seam — and the re-roll has produced concrete divergence (3 admin hunt routes silently lack the archived gate)

**Cross-cutting** · `bot/web/actions.py:985` · confirmed by adversarial trace

**Evidence.** Counts across 61 routes: the `hunt_archived` 403 is re-typed 37x, `cog = puzzles_cog(); if cog is None: 503` 35x, `HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])` 34x, the guild-snowflake resolve 27x. The seam already exists in-tree and is bypassed: _resolve_visible_hunt (actions.py:150) covers only the lookup, while the canvas/media/settings families bundle resolve+archived+gate into ctx helpers (_canvas_ctx actions.py:2474 with require_manage, _media_ctx :2510, _media_gc_ctx :1981, _guild_settings_ctx :1784). Divergence it has already produced: patch_hunt_settings (:985), set_hunt_role (:1020) and post_hunt_solved_sheet (:1115) mutate hunt state with NO archived check, while delete_hunt documents its omission explicitly ("Deliberately no archived 403 guard", :1553) — the other three carry no such marker, so intent is undecidable from the code.

**Impact.** Every new route re-decides five gate/ordering questions; the three unmarked archived-gate omissions are exactly the class of drift a shared prologue prevents (admin-only, so low exploit value, but settings/role edits on archived hunts contradict the 37 sibling routes' contract).

**Direction.** Extend the existing ctx-helper pattern to the puzzle/hunt families (e.g. _puzzle_ctx(puzzle_id, gate=None, archived_ok=False) returning ctx+hunt_obj+cog+guild or an error), migrate handlers mechanically, and make archived-gate omission an explicit parameter so the delete_hunt-style exceptions are declared, not accidental.

### M30 — Web create_meta hand-syncs the bang command_map's type knowledge (meta-name suffix, Group-has-no-target, Runaround→subtree) instead of it living in the service beside TYPE_DEFAULTS

**Cross-cutting** · `bot/web/actions.py:2170` · confirmed by adversarial trace

**Evidence.** actions.py:2170-2178 re-derives meta_puzzle_name per type ("Mirror the bang path's meta naming... See _round_impl") and :2191-2194 re-applies the Runaround source_mode=subtree post-set "like the bang path does"; the originals are the cog's command_map (puzzles.py:2104-2114) and its Runaround post-create (puzzles.py:2132-2135). The service already owns the adjacent knowledge (TYPE_DEFAULTS/GROUP_TYPES/GROUP_TYPE_INPUT_ALIASES/CREATE_META_TYPES, puzzle_service.py:183-196) — the naming suffix and source-mode default are the only per-type facts left outside it, so a new group type today requires editing both edges in lockstep.

**Impact.** Two hand-synced copies of per-type creation defaults; the comments prove authors already treat them as one piece of knowledge maintained twice.

**Direction.** Fold meta-name derivation and the Runaround subtree default into service.create_tag_group (keyed off TYPE_DEFAULTS), leaving both edges to pass just (name, round_type).

### M31 — Board-push gap: canvas CRUD, evidence PATCH/DELETE, and hunt-archive mutate with no board event while every sibling pushes

**Cross-cutting** · `bot/web/actions.py:2589` · confirmed by adversarial trace

**Evidence.** Two push mechanisms exist: @pushes_board on service methods (33 methods, fans out to WebNotifier registered in bot/web/__init__.py:129) and handler-local _notify_board (15 sites). Three canvas routes fall in the gap: create_canvas (actions.py:2589-2618), update_canvas (2623-2640), delete_canvas (2643-2673, which also cascades region+evidence deletions) call CanvasJsonDb directly and never publish, while every sibling in the same section pushes — retile_canvas:2708, create_region:2735, update_region:2784, delete_region:2804, create/update/delete mark:2824/2847/2866, add/reorder/delete puzzle image:2920/2947/2968, create_puzzle_from_region:3066. Likewise update_evidence (2357-2429) and delete_evidence (2432-2470) push only incidentally when an informs edge flips (via tag_with_edge/untag_from, which are @pushes_board); a note edit, media swap, or delete of an edge-less item never pushes — while evidence CREATE (api.py add_evidence -> service.log_clues) is @pushes_board. post_hunt_archive (1089-1112) commits hunts.archived via DAO with no push either. The SPA's board handler explicitly invalidates the affected caches: HuntBoard.tsx:42-53 invalidates ["canvases", uid] and ["evidence", uid] on every board event, and HuntList.tsx:306 invalidates ["hunts"].

**Impact.** Other connected viewers do not see a new/renamed/deleted canvas, an edited/deleted evidence item, or an archive toggle until some unrelated pushed mutation or a manual reload — the exact multi-viewer staleness the push+invalidate machinery was built to prevent (and which the markup routes' comment in HuntBoard.tsx:44-49 says was already fixed once for marks).

**Direction.** Add _notify_board(ctx["hunt"]["uid"], ctx["guild_snowflake"]) to create/update/delete_canvas, update/delete_evidence, and post_hunt_archive — the one-line pattern their siblings already use. Longer term, note in a comment near _notify_board which route families are DAO-direct (canvas/evidence/media/markup/images) so the next such route knows the decorator will not cover it.

### M32 — Dead clue-logging twin: actions.log_clue + useLogClue superseded by api.add_evidence but both kept; two write routes stranded in the read blueprint

**Cross-cutting** · `bot/web/actions.py:602` · confirmed by adversarial trace

**Evidence.** add_evidence's own comment (bot/web/api.py:608-611) says "This route replaces the actions blueprint's /clues endpoint as the dashboard's clue-logging path" — and it has: useLogClue (frontend/src/api.ts:224-234, POST /api/puzzles/<id>/clues) is imported by NO component (grep across frontend/src finds only its definition), while EvidencePanel uses useAddEvidence -> POST /api/evidence. Yet actions.log_clue (actions.py:602-636) remains a live HTTP endpoint. The two twins also disagree: log_clue holds puzzle_lock, add_evidence does not (harmless today — service.log_clues only inserts evidence rows, puzzle_service.py:3006-3033 — but the disagreement is drift waiting to matter). Separately, add_evidence and upload_media (api.py:348) are the only two WRITE routes living in api.py (every other write is in actions.py), and both show the friction: add_evidence imports _audit and _require_hunt_media function-locally to dodge a circular import (api.py:590, 618), upload_media imports _notify_board_soon the same way (api.py:389). Checked dynamic invocation: no test hits the /clues route (tests mock service.log_clues for the /evidence route, tests/test_add_evidence_web.py).

**Impact.** A superseded endpoint with its own (divergent) gate/lock discipline stays maintained and reachable over HTTP; the blueprint convention (reads in api.py, writes in actions.py) now has two undocumented exceptions held together by function-local imports.

**Direction.** Delete useLogClue; then either delete the /clues route or reduce it to a thin alias of the /evidence path. Decide where evidence/media writes live — either move add_evidence/upload_media into actions.py, or hoist _audit/_require_hunt_media/_notify_board into a small bot/web/shared module both blueprints import (killing the circular-import workarounds).

### M33 — Main dashboard read model (get_hunt_puzzles) still tested only by source-string sniffing

**Test quality** · `tests/test_minimeta_service.py:485` · confirmed by adversarial trace

**Evidence.** TestWebDataMinisPayload (tests/test_minimeta_service.py:485-505) asserts `inspect.getsource(data.get_hunt_puzzles)` contains the substrings "parent_puzzle_id IS NULL", "mini_puzzles", "target_puzzle_id IS NOT NULL", "rollup_stale". These are the ONLY tests touching bot/web/data.py:301 get_hunt_puzzles — the ~100-line SQL join + row-folding function that builds the SPA's entire puzzle payload. A substring assert passes if the text appears in a comment, in dead code, or inverted (`NOT (parent_puzzle_id IS NULL)` contains the asserted substring). Downstream consumers (derive_meta_kind, build_board) are tested against hand-built dicts from conftest's make_puzzle_row — duplicated knowledge of the payload shape that can drift from what get_hunt_puzzles actually emits. This is the surviving half of the 2026-08-07 review's TT4, which the status table never marked fixed; OAuth login/callback state validation (bot/web/auth.py:36-74) and migrate.py are the other still-uncovered TT4 items (bulk_update_edge_type has since been fixed properly — test_bulk_edge_atomicity.py now proves it live).

**Impact.** The row-folding logic (multi-round puzzle folded to one entry with a rounds array, edge_type accumulation, activity join) can silently regress — e.g. a puzzle in two rounds appearing twice, or edges dropped — while the suite stays green, because no test executes the function.

**Direction.** Replace the source sniffs with behavioral tests that patch bot.web.data.fetch_all to return crafted rows (at least one puzzle in TWO rounds with mixed feeds/informs edges, per the house falsifiability style) and assert the folded output; assert make_puzzle_row parity against that output so the conftest shape can't drift.

### M34 — Bang-command gating map is sampled while the web gating map is exhaustive

**Test quality** · `tests/test_command_gating.py:72` · confirmed by adversarial trace

**Evidence.** test_command_gating.py:72 says "Not exhaustive — the commands this batch moved, plus anchors"; EXPECTED lists 19 of the 80 registered commands in bot/cogs/puzzles.py. Its web twin, tests/test_web_route_gating.py:170 test_ungated_routes_are_exactly_the_rest, asserts the WHOLE residue, so a web route can neither gain nor lose a gate silently. On the bang side, a new command with a missing/wrong tier decorator, or a tier change to any of the 61 unlisted commands, fails nothing — the docstring itself notes "removing one is silent".

**Impact.** The two surfaces of the same permission model (levelled in commit dc957bd precisely to keep them consistent) have asymmetric regression protection; bang-side tier drift re-enters unnoticed, which is how the pre-audit drift happened.

**Direction.** Enumerate all commands behaviorally (iterate the cog's commands via discord.py introspection, reuse the existing 3-member _passes probe) and assert the full name→tier map, mirroring test_web_route_gating's residue assertion. The probe machinery already exists; only the map is partial.

### M35 — remove-minimeta web route's exception-ordering branch is untested while both sibling routes test the identical trap

**Test quality** · `bot/web/actions.py:712` · confirmed by adversarial trace

**Evidence.** bot/web/actions.py:712-720 carries a comment documenting that SheetRefreshError must be caught before RuntimeError "otherwise a stale-sheet warning would be misreported as not_minimeta", and returns a distinct `sheet_warning` payload. No test posts to /puzzles/<id>/remove-minimeta or /puzzles/<id>/make-minimeta (grep across tests/ finds the strings only in test_web_route_gating's map and service-level tests). The identical SheetRefreshError→sheet_warning contract IS route-tested for the siblings: tests/test_delete_mini_web.py:125-134 and tests/test_change_group_type_web.py:132.

**Impact.** Reordering those two except clauses (a plausible tidy-up, since SheetRefreshError subclasses RuntimeError) converts a benign stale-sheet warning into a 400 not_minimeta for a successful operation, and nothing goes red. The comment is currently the only guard.

**Direction.** Add a remove-minimeta route test mirroring test_delete_mini_web's SheetRefreshError case (assert 200 + sheet_warning, and the RuntimeError case asserting 400 not_minimeta), plus one make-minimeta happy-path test.

### M36 — Admin-tier mutating web routes are covered only by the AST gating map, which proves gate consultation, not behaviour

**Test quality** · `bot/web/actions.py:1020` · confirmed by adversarial trace

**Evidence.** Grepping every tests/ file for route paths and handler names: POST /hunts/<uid>/role (actions.py:1020, a rich RuntimeError-string→status mapping: "already_gated"→409, "existing_role_confirm"→200 needs_confirm, "role_not_applied"→422), POST /hunts/<uid>/archive (:1087, direct HuntJsonDb mutation), POST /hunts/<uid>/solved-sheet (:1115), PATCH /hunts/<uid>/settings (:985, whitelist + bad_keys 400 branch), POST /hunts/<uid>/reconcile (:1905), POST /rollups/<id>/rebuild (:2033), POST /puzzles/<id>/make-minimeta (:640) and /remove-minimeta (:678), PATCH /canvases/<id> (:2621), and POST /puzzles/<id>/link (:563) appear in NO test except tests/test_web_route_gating.py. That map's resolver (test_web_route_gating.py:126-141) only checks whether require_admin/require_organiser appears among the calls in the handler (transitively) — a handler that calls the gate and discards the returned error tuple still resolves to its tier. Underlying services are tested (test_reconcile.py, test_hunt_settings_service.py, test_archive_hunt_service.py), but the route-only logic — body parsing, error mapping, `if perm: return perm`, the settings whitelist — is inspection-only. /link additionally accepts an empty url with no validation (actions.py:575), unpinned by any test.

**Impact.** A regression in any of these handlers (dropped `return perm`, broken exception-string match on set_hunt_role, whitelist typo in _SETTINGS_KEYS) ships green; these are the admin-tier destructive/config surfaces of the dashboard.

**Direction.** Add route tests in the established make_web_app + patch("bot.store.X") pattern (test_solve_puzzle_web_invariants.py is the template). Priority order: set_hunt_role's three RuntimeError branches, post_hunt_archive's commit, patch_hunt_settings' bad_keys 400, and one refused-gate case per route asserting the service was not awaited.

### M37 — Live-DB test family has no test-database guard; running pytest on a prod checkout writes to the production DB

**Docs & ops** · `tests/conftest.py:12` · confirmed by adversarial trace

**Evidence.** live_hunt_id() (tests/conftest.py:12-38) picks the FIRST real hunts row from whatever DB config.json names, and will INSERT a hunt if the table is empty. The ~11 live-DB test files then do real INSERT/DELETE against that DB — e.g. tests/test_hunt_category_cascade.py:15 'INSERT INTO hunts', :41/:59-60 'DELETE FROM hunts WHERE id = %s'. Nothing checks the database name: the file docstrings merely assume puzzlebot_test. Contrast scripts/diagnose_atomicity.py:14-17, which explicitly REFUSES to run unless config.database contains 'test' — the hazard is recognised in this codebase, just not applied to the tests. README.md:826-834 ('Tests') says to run the full suite from the repo root with no warning; the live Plesk checkout's config.json names the production DB.

**Impact.** Anyone debugging on the live host who runs the documented pytest command inserts/deletes rows in the production database. Teardown is in finally blocks, but a timeout-kill (the house style wraps runs in `timeout`) or crash mid-test leaves DIAG-less orphan rows in hunts/hunt_category/tag_group/tags, and any future test bug mutates real hunt data with no guard rail.

**Direction.** Add a session-scoped guard in tests/conftest.py (or a shared helper the live-DB files call) mirroring diagnose_atomicity.py: skip or hard-fail the live-DB tests unless config.database contains 'test'. Optionally add one warning line to README's Tests section.


## Low — confirmed

Verified the same way as the mediums; ranked low for impact or reachability.

### C1 — !start_reminders restart raises RuntimeError, and one global reminder loop is shared across all guilds/hunts

**Discord cogs** · `bot/cogs/puzzles.py:4121` · confirmed by adversarial trace

Re-running !start_reminders while the loop runs (the natural way to repoint it at a new hunt) fails with the generic 'Something went wrong' message; on a multi-guild deployment one guild's organisers silently start/stop another guild's reminders.

**Direction.** Use reminder_loop.restart(channel) (or cancel + start from a done-callback), and key loops per guild (e.g. a dict of Loop/Task per guild id) so start/stop are scoped to the invoking guild.

### C2 — MAX_PIXELS (300MP) ceiling is not enforced on the evidence OCR path — only PIL's incidental ~358MP bomb guard applies

**Media & canvas** · `bot/web/api.py:425` · confirmed by adversarial trace

A crafted small PNG (well under the 25MB cap) forces a ~350MP decode plus a tesseract run per request; combined with the missing semaphore (previous finding) this is the cheapest DoS lever on the box, and even alone it exceeds the pixel budget the ingest paths carefully enforce.

**Direction.** Probe dimensions from the bytes (pyvips new_from_buffer, or PIL .size before any pixel access) and reject with the existing MediaRejected message when exceeds_pixel_limit(w, h), mirroring media_ingest; keep PIL's guard as defence in depth.

### C3 — Media GC sweep TOCTOU: rows referenced mid-sweep are still deleted (evidence silently loses its image; a just-created canvas cascades away)

**Media & canvas** · `bot/core/media_gc.py:91` · confirmed by adversarial trace

Rare but silent data loss: an image detaches from evidence or a fresh canvas/puzzle-image disappears with no error anywhere; the deleting sweep logs only counts.

**Direction.** Make the per-row delete self-verifying: a single DELETE FROM media WHERE id=%s AND NOT EXISTS(...) repeating the four reference subqueries (or re-run get_orphaned per row id), and remove the files only when the DELETE reports 1 row — files after row, not before.

### C4 — delete_canvas removes the tile pyramid but leaves tile_status='ready', bricking any other canvas on that media

**Media & canvas** · `bot/web/actions.py:2667` · confirmed by adversarial trace

Deleting one canvas silently and permanently blanks any other canvas sharing its media (including the common flow of deleting a canvas and re-creating one from the same uploaded image); only a manual DB edit can recover, and the immutable cache headers make the blank tiles sticky in browsers.

**Direction.** In delete_canvas, after the rmtree, reset the row with MediaJsonDb.update_tiles(media.id, 'pending' if it was tiled else 'none', 0) — or skip the rmtree when another hunt_canvases row still references the media and let the GC reclaim it. Either way, make retile accept the resulting state.

### C5 — demote_tab_to_puzzle is destructively non-idempotent: a retry after a mid-operation failure deletes four more columns of solver work

**Google Sheets** · `bot/cogs/puzzles_gsheet.py:711` · confirmed by adversarial trace

A transient failure in the wrong window turns a routine demote retry into silent data loss on a shared solver sheet — the class of damage the select_table/name work was done to prevent. Window is narrow but the failure mode (Sheets 500 mid-operation) is common enough that the codebase builds retry, deferral, and self-heal machinery around it elsewhere.

**Direction.** Probe the tab's current geometry before the column delete (e.g. the A1/B1 label check _ensure_contribution_table already uses, or the presence of the named contribution Table): only delete the contribution columns when the tab is verifiably in target layout, and make a retry after step 2 skip straight to header regeneration.

### C6 — Ctrl+wheel / trackpad-pinch zoom permanently dead on a canvas that transitions pending→ready in place

**Frontend** · `frontend/src/hooks/useZoomPan.ts:193` · confirmed by adversarial trace

The standard flow — upload a large sheet, watch "Preparing image", start working — leaves ctrl+wheel and trackpad-pinch zoom silently inert for that canvas until the user switches canvases or reloads. Buttons and touch pinch still work, masking the cause. MarkupCanvas is unaffected (its frame always renders).

**Direction.** Make attachment react to the element appearing: a callback ref that stores the element in state (and include it in the effect deps), or pass an `enabled`/mounted flag into useWheelZoom's dep list.

### C7 — Solved-sheet creation is a duplicated implementation, not a shared seam — web post_hunt_solved_sheet and bang !use_solved_sheet each hand-roll check→create→commit, and neither is locked

**Cross-cutting** · `bot/web/actions.py:1115` · confirmed by adversarial trace

Two concurrent invocations (double-click, or bang+web) both pass the exists-check and create two Drive spreadsheets, orphaning one; and the operation's knowledge (idempotence check, contextvar setup, commit ordering) lives in two hand-synced copies that have already drifted in small ways.

**Direction.** Extract service.create_hunt_solved_sheet(hunt) doing check→create→commit under hunt_sheet_lock(hunt.id), and have both edges call it — same collapse the rest of the sheet family already received.

### C8 — Web gating map leaves 17 ungated mutating routes to the implicit residue, contradicting its own naming rule

**Test quality** · `tests/test_web_route_gating.py:81` · confirmed by adversarial trace

The map's stated purpose — "the tier is a decision, not an implementation detail" — holds only for gated routes; a new mutating route added ungated by accident, or one of these 17 that was never actually decided, passes silently. (Distinct from the documented WON'T-FIX on rate limiting; this is gate-inventory completeness.)

**Direction.** Move every ungated mutating route into UNGATED_BY_DECISION with its one-line reason, and add an assertion that the residue (routes in neither list) contains only GET/read-only rules.

### C9 — Migration 042's production-deployment status is undocumented and now ambiguous

**Docs & ops** · `docs/reviews/2026-08-07-comprehensive-review.md:55` · confirmed by adversarial trace

Two bad states are indistinguishable from the repo: (a) prod ran 042 via the hook without the header's mandated pre-flight, or (b) prod still lacks the UNIQUE index and the group-code mint race remains open there — while the only written status says 'not yet deployed'. The next person reading the review doc will act on stale information either way.

**Direction.** Run `python migrate.py status` on the prod host, then update the review status-table row (and note whether the pre-flight was run and its result). Generally: when a migration carries a manual pre-flight, record its execution in the migration's ledger row context (a dated note in the review table or docs) at deploy time.


## Low — reported, not adversarially verified

86 items from the finder sweeps (65 from sweep 1, 21 new from sweep 2;
cross-sweep duplicates merged). Treat each as a lead, not a verdict — at the refutation
rate observed for mediums (1 in 52), expect a few of these to be wrong.

**Core service** (8)

- SheetRefreshError.failed_round_ids gets puzzle ids appended in solve/change_solution/add_solution — `bot/core/puzzle_service.py:1357`
- _promote_group_to_target ignores safe_rename's result and can delete the group's general chat (with history) when adoption misses — `bot/core/puzzle_service.py:1176`
- create_mini has no failure cleanup: a thread-create failure strands the mini's sheet tab and DB row — `bot/core/puzzle_service.py:2540`
- create_hunt has no rollback: a failure after the Discord categories are created strands them, and a retry duplicates them — `bot/core/puzzle_service.py:3428`
- Bang !puzzle create runs check-then-create without hunt_create_lock, which the web edge holds for exactly this race — `bot/cogs/puzzles.py:833`
- Bang convert edges lack the post-commit Sheets tolerance the web convert routes have — `bot/cogs/puzzles.py:2226`
- PREVIOUSLY REPORTED (M3), still open — safe_rename's RateLimited deferral is dead code and renames now also block while group-wide puzzle locks are held — `bot/core/puzzle_service.py:224`
- Solve-divider name duplicated as a second constant in PuzzleService — `bot/core/puzzle_service.py:1578`

**Discord cogs** (7)

- !partial / !add_solution with no answer record an empty partial: trailing-slash solution and status flipped — `bot/cogs/puzzles.py:3461`
- !round fails silently when the hunt's template category is missing (unmapped RuntimeError code) — `bot/cogs/puzzles.py:2149`
- Archive-family and reminder commands crash on asserts/None instead of refusing when run outside a hunt context — `bot/cogs/puzzles.py:3715`
- delete_puzzle/delete_round send their success ack to a hunt-general channel that may be None — `bot/cogs/puzzles.py:3923`
- Dead PUZZLE_BUSY branch in on_command_error — nothing raises it — `bot/cogs/puzzles.py:559`
- Help/pin copy still says '(admin only)' for commands levelled to organiser, plus stale !list_puzzles guidance and a hardcoded team URL — `bot/cogs/puzzles.py:3058`
- DM'd ungated puzzles-cog commands crash via assert instead of a clean guild-only refusal — `bot/cogs/puzzles.py:268`

**Web auth** (7)

- hunt.url reaches the SPA as a raw href with any scheme preserved; the existing safeHref sanitizer is not applied at those sinks *(2nd sweep)* — `frontend/src/pages/HuntBoard.tsx:98`
- Session is not rotated at OAuth login and the session cookie lacks a __Host- prefix, within the sibling-host threat model the code itself documents *(2nd sweep)* — `bot/web/auth.py:66`
- Logout does not invalidate the session; a captured cookie replays for the framework-default 31 days *(2nd sweep)* — `bot/web/auth.py:75`
- hunt_detail serves the hunt site username/password to every guild member once a hunt is archived (and for legacy role_id=0 hunts while active) *(2nd sweep)* — `bot/web/api.py:155`
- PATCH /hunts/<uid>/settings skips the archived read-only check every member-level mutation enforces *(2nd sweep)* — `bot/web/actions.py:985`
- Admin PATCH value validation is asymmetric with create_hunt: unvalidated start_timestamp and guild-settings values surface as 500s *(2nd sweep)* — `bot/web/actions.py:1894`
- Residual low-severity nits (folded) *(2nd sweep)* — `bot/web/events.py:14`

**Web routes** (1)

- Several member-accessible write routes store unbounded strings (note / puzzle_type) *(2nd sweep)* — `bot/web/actions.py:614`

**Store & migrations** (9)

- Uniqueness the code relies on is not constraint-backed: guilds.guild_id, hunts.name, tag_group (hunt_id, name) — `migrations/000_base_schema.sql:29`
- puzzles.hunt_id has neither FK nor index; puzzles.channel_id has no index — the one core child edge left out of the 022/037/038 FK sweep (2nd sweep rated this medium: "the last unprotected owning edge") — `migrations/022_delete_cascades.sql:1`
- datetime.utcnow() deprecation (flagged 2026-08-07) still open; line numbers have drifted — `bot/store/mysqldb.py:940`
- Evidence ordering lacks the ', id' tie-break its three sibling DAOs use, and log() returns sub-second precision the column drops — `bot/store/mysqldb.py:864`
- ~~dbtest.py is still present and still broken~~ — **withdrawn 2026-08-30**: `dbtest.py` is gitignored (`.gitignore:2`) and has never been tracked on `staging`, so there is nothing for the repo to remove. The broken copy is a local scratch file on this machine; deleting it is a personal-machine choice, not a repo change.
- TagGroupData.sort_by_tag_group_start is dead and would crash on the NULL start_time rows the promote paths create — `bot/store/tag_group_data.py:196`
- Dashboard connection pool lacks the connect_timeout hardening the #89 fix gave MySQLReconnector *(2nd sweep)* — `bot/web/data.py:36`
- Dead DAO surface: MySQLPuzzleJsonDb.get and .get_all (with a never-used hunt_id parameter), plus two never-raised exception classes *(2nd sweep)* — `bot/store/mysqldb.py:452`
- Cursor cleanup without try/finally on the generic commit path — including the designed duplicate-key retry loop *(2nd sweep)* — `bot/store/mysqldb.py:79`

**Media & canvas** (7)

- Hunt delete runs remove_hunt_media's rmtrees synchronously on the event loop, unlike the sweep path that was deliberately moved off-loop (2nd sweep: delete_canvas has the same shape, actions.py:2667) — `bot/core/puzzle_service.py:3591`
- ingest_image_bytes performs its disk write (and header probe) on the event loop at all three call sites; the crop path writes an uncapped PNG — `bot/core/media_ingest.py:19`
- Markup audit rows carry no identifying detail, and upload_media writes no audit row at all — `bot/web/actions.py:2874`
- No length cap on uploaded filenames: a long name overflows the filesystem's 255-byte limit and turns the upload into a 500 — `bot/core/message_media.py:19`
- Discord !clue attachment path reads the whole attachment into memory before the byte cap is applied — `bot/cogs/puzzles.py:1726`
- update_region's 2026-07-28 resolved-media fix stopped at sync_region_image: the evidence row still gets the RAW media_id, so a suppressed/failed re-crop yields text-only evidence while a valid crop sits on the region — `bot/core/canvas.py:120`
- markup.update_mark re-hardcodes the 500-char text cap instead of using markup_shape.MAX_TEXT, and can blank a text mark — `bot/core/markup.py:58`

**Google Sheets** (7)

- reconcile_table reuses trailing blanked rows across reconciles, violating its own stated row-identity invariant — `bot/cogs/puzzles_gsheet.py:1265`
- Dead legacy rollup renderers: aggregate_to_tab / add_metapuzzle_data / add_metametapuzzle_data have no production callers — `bot/cogs/puzzles_gsheet.py:883`
- get_unique_tab_name treats the puzzle's OWN tab as a collision, so a case-only rename yields a 'name (2)' tab — `bot/cogs/puzzles_gsheet.py:306`
- Minimeta rollup renders assume the main spreadsheet; an archived minimeta parent silently no-ops yet badges the rollup as OK — `bot/cogs/puzzles_gsheet.py:1170`
- Retrying non-idempotent create/copy POSTs on transport errors can strand duplicate Drive files and orphan archive-side tabs — `bot/utils/gsheets.py:19`
- Doc/config drift around the Sheets layer: stale 'never by name' comment, unread puzzle_cell_link key, dead cog attributes, single-char A1 parsers — `bot/cogs/puzzles_gsheet.py:1097`
- rename_puzzle lets a raw Sheets exception escape after the DB commit, unlike every sibling mutation *(2nd sweep)* — `bot/core/puzzle_service.py:2416`

**Frontend** (14)

- Saving guild settings never invalidates the admin-guilds query — the "admin roles are not pinned" banner survives being fixed — `frontend/src/api.ts:784`
- No catch-up refetch after websocket reconnect — an open chat transcript is permanently gapped — `frontend/src/hooks/activityStream.ts:247`
- Puzzle-image reorder race: a second quick drag is computed from the stale order and reverts the first — `frontend/src/components/PuzzleImages.tsx:176`
- DragBoard conflates "the Unassigned bucket" with "any group whose group_code is NULL" — `frontend/src/components/DragBoard.tsx:341`
- Dead exports in api.ts: useLogClue, useConvertToGroup, useConvertToMetapuzzle, useUpdateMark — `frontend/src/api.ts:224`
- HuntSummary.guild_snowflake is served as a JSON number, breaking the codebase's own ids-are-strings rule — `bot/web/data.py:283`
- friendlyError's slug map lags the newer endpoints — raw error slugs reach users as toasts — `frontend/src/api.ts:61`
- MiniPuzzlePanel hard-codes light-theme colours — renders as a light slab in dark mode — `frontend/src/components/MiniPuzzlePanel.tsx:164`
- RegionModal create-and-link retry duplicates the region if puzzle creation fails — `frontend/src/components/RegionModal.tsx:159`
- Group-type list duplicated between the change-type menu and the API-driven create dropdown — `frontend/src/components/DragBoard.tsx:421`
- Aggregated-answers query is never invalidated by solves or board pushes *(2nd sweep)* — `frontend/src/api.ts:645`
- Chat viewer: initial fetch can clobber WS messages, and there is no polling fallback for a dead socket *(2nd sweep)* — `frontend/src/hooks/useChannelMessages.ts:16`
- Pinch-zoom clamp duplicates canvasMath's MIN_SCALE/MAX_SCALE inline *(2nd sweep)* — `frontend/src/components/CanvasView.tsx:213`
- NewHuntModal retains all form state after a successful create *(2nd sweep)* — `frontend/src/pages/HuntList.tsx:121`

**Cross-cutting** (8)

- Status/priority/group-type vocabulary still has hand-synced TS respellings, bypassing the server-served-vocabulary mechanism the repo already built (meta_types) — `frontend/src/components/PuzzleEditCells.tsx:26`
- Unsolve leaves the Unassigned-tab Answer column stale on both surfaces — the solve/unsolve pair is asymmetric about refresh_unassigned_safe — `bot/web/actions.py:2317`
- MarkupCanvas re-implements the extracted zoom-about-anchor arithmetic inline ("Same arithmetic as CanvasView.zoomAt") instead of parameterising lib/canvasMath.zoomAt on clamp bounds — `frontend/src/components/MarkupCanvas.tsx:180`
- Residual solve-family presentation orchestration is duplicated between the bang inline tails and the 4 surviving web_* helpers (the twin collapse's last mile) — `bot/cogs/puzzles.py:1089`
- Minor DAO sibling drifts in mysqldb.py: forked check_duplicates_in_hunt signature (with the repo's only bot/ pyright-ignore outside image_tiler), copy-drifted get_by_attr docstrings, and two coexisting CRUD idioms — `bot/store/mysqldb.py:483`
- Tier-decision sequence implemented twice in bot/web: user_can_manage (feeds the UI flags) vs require_organiser (enforces) *(2nd sweep)* — `bot/web/actions.py:858`
- Store DAO family is split into two idioms, and the pinned add-a-field rule only holds for one of them *(2nd sweep)* — `bot/store/mysqldb.py:1013`
- Six convert mutation hooks (and ~33 identical invalidate-one-key mutations) re-roll the shape the backend already collapsed *(2nd sweep)* — `frontend/src/api.ts:523`

**Test quality** (10)

- Solved-sheet retrofit is untested on both surfaces and the two implementations have already drifted — `bot/cogs/puzzles.py:783`
- Ten bang commands have no test references at all (sampled sweep) — `bot/cogs/puzzles.py:903`
- make_hunt factory accepts arbitrary kwargs via setattr, silently absorbing typos — `tests/conftest.py:153`
- make_hunt defaults every test hunt to having a solved sheet, leaving the no-solved-sheet path opt-in only — `tests/conftest.py:140`
- All 28 pytest warnings are utcnow() deprecations from four sites; none masks a bug, but they will become errors and bury new warnings — `bot/store/mysqldb.py:815`
- test_get_unassigned connects to MySQL at import/collection time via module-level live_hunt_id() — `tests/test_get_unassigned.py:24`
- test_backfill_consistency is a shared-state scan: silently vacuous on an empty DB, failable by unrelated data — `tests/test_backfill_consistency.py:25`
- Frontend behavior above pure lib functions is build-verified only — `frontend/src/lib/reconnect.test.ts:1`
- test_deletes_tagged_round_meta_refresh asserts nothing — cannot fail for the property in its name *(2nd sweep)* — `tests/test_solve_equivalence.py:774`
- Refusal-path web tests assert only the status code, not that nothing mutated behind the refusal *(2nd sweep)* — `tests/test_canvas_web.py:95`

**Docs & ops** (8)

- Log rotation (O2) is partial: bot.log is unrotated and the discord.log/bot.log split is undocumented — `bot/__init__.py:148`
- config.json.dist still templates puzzle_cell_link, which nothing reads — incomplete tail of the dead-config cleanup — `config.json.dist:5`
- docs/dashboard.md architecture section has drifted well behind bot/web — `docs/dashboard.md:11`
- README Usage section still documents the upstream bot's solve/archive behaviour — `README.md:80`
- docs/WORKTREES.md still claims bot/store connects to MySQL at import time (stale since T4 lazy connect) — `docs/WORKTREES.md:70`
- Two user-facing '(admin only)' strings sit on organiser-gated commands, and commands.md understates the organiser name set — `bot/cogs/puzzles.py:1321`
- PLANS.md §0 asserts the pre-M6 schema as 'the data model today'; sampled file:line refs are rotted (known-drift class, degree reported) — `docs/PLANS.md:14`
- Main-checkout virtualenv is Python 3.13.5 while Pipfile requires 3.12 (prod's version) — `Pipfile:32`


## Review coverage

- **Core service:** `puzzle_service.py` read end-to-end (~3,741 lines) plus locks,
  category_layout, reconcile, unassigned, target_extract/glyph, discord_channels,
  hunt_archive, constants, errors. State transitions and lock discipline traced.
- **Discord cogs:** `puzzles.py` in full (4,145 lines) plus utility, settings, ui,
  activity, bridge, role_gate, and `docs/commands.md`.
- **Web (two workstreams):** auth.py, `__init__.py` (CSRF/cookies/secret guard),
  typed_app, events, and api.py + actions.py read route-by-route (all 61 actions routes
  and every api.py route); the object-scoping resolver family traced.
- **Store & migrations:** `mysqldb.py` in full (1,336 lines), all DAO dataclasses, all
  43 migrations, migrate.py; read-only schema cross-check against `puzzlebot_test`.
- **Media & canvas:** all 13 core modules and 5 store files in full, including the GC
  join and path-containment checks on every byte-serving route.
- **Google Sheets:** `puzzles_gsheet.py` in full (1,583 lines), gsheets.py,
  sheet_tables, sheet_layout, contribution; every `.execute()` call site traced for
  retry coverage.
- **Frontend:** all 63 files under `frontend/src`, contract cross-checked against
  api.py/data.py and the actions handlers the SPA calls.
- **Horizontal pass:** explicit cross-sibling brief — `web_*` twin census, 61-route
  prologue shape map, solve/unsolve/delete dual-entrypoint audit, DAO idiom split,
  TS/Python vocabulary duplication.
- **Test quality:** conftest factories in full; mechanical AST falsifiability sweep over
  all 180 test files (56 zero-assert/status-only hits individually classified); ~30
  files read closely, the rest sampled.
- **Docs & ops:** README, commands.md, dashboard.md, playbook, WORKTREES.md,
  config.json.dist vs code-read keys, deploy/start/dev scripts, logging, all 82 Pipfile
  pins, frontend deps, secrets hygiene in git history.

**Not inspected anywhere:** production host state (prod DB schema/data, live Plesk hook
config, prod Python version), the deliberately stale forum-layout paths beyond
shared-state safety, docs/future + archived docs, and runtime behaviour in a browser
(frontend findings are source-traced, not reproduced live).

## Dead or obsolete code

All checked for dynamic invocation (command registration, route decorators, patch
targets, frontend imports) before being reported.

- Legacy rollup renderers `aggregate_to_tab` / `add_metapuzzle_data` /
  `add_metametapuzzle_data` (+ `move_sheet_to_start`) have no production callers
  (`puzzles_gsheet.py:883`).
- The clue-logging twin: `actions.log_clue` route + unused `useLogClue` hook, superseded
  by `api.add_evidence` (M32 — the two survivors also disagree on locking).
- Dead frontend exports: `useConvertToGroup`, `useConvertToMetapuzzle`, `useUpdateMark`
  (`api.ts:224`).
- ~~`dbtest.py` still present and broken~~ — **withdrawn 2026-08-30.** It is gitignored
  (`.gitignore:2`) and never tracked, so the 2026-08-07 removal did not "miss" it: there
  was nothing in the repo to remove. The stale local copy is a personal-machine artifact.
- Dead DAO surface: unused `MySQLPuzzleJsonDb.get/.get_all` variants, two never-raised
  exception classes (`mysqldb.py:452`); `TagGroupData.sort_by_tag_group_start` dead and
  would crash on NULL start_time rows.
- Dead `PUZZLE_BUSY` branch in on_command_error (`puzzles.py:559`); unread
  `puzzle_cell_link` key still templated in `config.json.dist`.

## DRY and refactoring opportunities

The `web_*` twin collapse is effectively complete: no model-mutating twin survives; all
4 survivors are presentation-only on shared seams.

- **Route prologue helper adoption (M29):** measured, not estimated — of 61 routes in
  `actions.py`, 15 already go through a `*_ctx` helper, 40 hand-roll the resolve step, and
  **15 hand-roll the full prologue** (resolve + archived + gate). A further **7 hand-rolled
  routes carry no archived check at all** (`patch_hunt_settings:987`, `set_hunt_role:1022`,
  `post_hunt_archive:1089`, `post_hunt_solved_sheet:1117`, `convert_to_feeder_preview:1273`,
  `delete_hunt:1543`, `get_puzzle_images:2880`) — some legitimately exempt, and today a
  deliberate exemption is indistinguishable from an oversight. That is what makes this a
  correctness fix rather than tidying. Approved as D2.
- **create_meta type knowledge (M30):** the web route re-encodes the bang command_map's
  meta/capstone/runaround rules by hand.
- **Solved-sheet creation (C7)** is duplicated between web and bang and has already
  drifted; same for the residual solve-family presentation tails.
- **Frontend mutation hooks:** ~33 invalidate-one-key mutations re-roll a shape a
  factory could collapse; six convert hooks duplicate the backend's already-collapsed
  convert dispatch.
- **Vocabulary:** status/priority/group-type respelled in TypeScript rather than served
  (low; the server already serves the vocabulary elsewhere).

## Naming

- `SheetRefreshError.failed_round_ids` now also receives *puzzle* ids from
  solve/change_solution/add_solution — the name asserts a type the data no longer has.
  **Approved as D3**, with a design step first: the field carries two id spaces —
  tag_group ids from rollup-refresh failures, and puzzle ids appended when the
  solved-sheet archive fails (`puzzle_service.py:1357`, `failed.append(puzzle.id)`) —
  while the message renders both as `"(round ids: …)"`. A rename alone leaves it false.
  Recommended: split into `failed_group_ids` + `failed_puzzle_ids` with the message
  rendering each kind. Blast radius is 9 references in 3 files (5 in `bot/core/errors.py`,
  4 test assertions); the 8 raise sites pass the list positionally. No schema, no API, no
  serialised data.
- The *clue* → *evidence* vocabulary straddle: the model and newer surfaces say
  evidence; `log_clue(s)`, `!clue` and friends keep the old noun. Best resolved along
  with the dead-twin cleanup (M32) rather than as its own rename.
- Known and already on the books, not re-counted: "pool/pooled" vestige, UI "tag" vs
  internal "edge".

## Test gaps

- The dashboard's main read model `get_hunt_puzzles` is pinned only by source-string
  sniffing (M33); the AST route-gating map proves gates are *consulted*, not
  *enforced*, and several admin mutating routes have no route-level test at all (M36).
- The web gating test misclassifies 6 deliberately-open canvas/region routes as
  organiser and cannot detect ungating of update/delete_canvas (M7) — the guardrail and
  the code disagree and both suites pass. **Resolved by D1: the map is wrong, not the
  code** — correct `EXPECTED` and make the resolver condition-aware; no tier changes.
- The bang-command tier map is a sample (26 gated commands unpinned) while the web map
  is exhaustive (M34); remove-minimeta's exception-ordering branch is untested while
  both siblings test it (M35).
- Notable lows: one test asserts nothing (`test_deletes_tagged_round_meta_refresh`);
  refusal-path tests assert status codes but not "nothing mutated"; factory defaults
  hide the no-solved-sheet path; ~10 bang commands have no tests.
- Strength: falsifiability is house style — mutation-proved guards and anti-vacuity
  comments are widespread; the tests reviewer called this one of the strongest suites
  they had seen.

## Documentation discrepancies

- `docs/dashboard.md` architecture section lags bot/web; README "Usage" still documents
  the upstream bot's solve/archive behaviour; WORKTREES.md still claims import-time DB
  connect (stale since the lazy-connect fix).
- Two user-facing "(admin only)" strings sit on organiser-gated commands, and
  commands.md understates the organiser set after the levelling audit; help/pin copy has
  the same drift.
- PLANS.md §0 asserts the pre-M6 schema as current; sampled file:line refs have rotted
  (a known, self-declared property of that file).
- Migration 042's prod-deployment status is undocumented and now ambiguous (C9).

## Operational and deployment risks

- **M37 is the sharpest:** the live-DB test family has no test-database guard — pytest
  on a checkout configured with prod credentials writes to the production DB.
- Migration numbering: 043/044 exist in another worktree; this branch collides if it
  mints new migrations before merging (check-and-renumber before merge).
- Log rotation is partial (bot.log unrotated) and the discord.log/bot.log split is
  undocumented; web-route tracebacks land in discord.log.
- Main-checkout venv is Python 3.13.5 against a Pipfile requiring 3.12 (prod's version).
- The dashboard connection pool lacks the connect_timeout hardening the reconnector got.
- Strength: deploy.sh/start.sh verification, migration refusal, and graceful shutdown
  all confirmed present and careful.

## Uncertainties and limitations

- **One finding refuted in verification** (and excluded): "!status accepts
  solved/backsolved" — the guard landed in `ad90908`; the finder read pre-fix
  behaviour.
- **Resolved (was: needs verification).** hunt_detail serving the hunt-site
  username/password to any guild member once a hunt is archived (`api.py:155`,
  `auth.py:303-309`) is **intended** under the 2026-06-21 visibility model — confirmed by
  the user, recorded as D4. Not a defect.
- The 86 lows are finder-reported only.
- Production state was unreachable; frontend findings are source-traced, not
  browser-reproduced.
- The review ran across a session interruption; the second sweep re-ran six workstreams
  with fresh agents. Every re-found finding agreed with the first sweep — a good
  convergence signal.

## Prioritised next steps

1. **Fix the four Highs.** Serialise or per-thread the bridge lookup connection (H1);
   add the cross-hunt check to region create/update (H2 — the invariant and error slug
   already exist); flag additional-sheet location so unsolve stops corrupting the main
   spreadsheet (H3); make DragBoard target the group's own target (H4).
2. **Close the admin-lockout side door (M4)** and **guard the live-DB tests against
   non-test databases (M37)** — two cheap changes with worst-case blast radii.
3. **A Sheets tab-lifecycle tranche:** the idempotency/compensation family (M15–M21,
   C5) plus the refresh serialisation race (M22) share one fix shape —
   verify-before-mutate, tolerate-already-done.
4. **Reconcile the route-gating map with reality (M7, M36, C8):** decide the 6 open
   canvas routes' tiers deliberately, name them in the map, add behavioural tests for
   admin mutating routes.
5. **Lock-regime completion:** !sync (M2), !convert_to_puzzle (M5), convert-to-feeder
   demote (M26), bang-side hunt_create_lock (M28).
6. **Then the structural batch:** prologue-helper adoption (M29), create_meta collapse
   (M30), board-push gap (M31), dead-twin/naming cleanup (M32) — each mechanical, each
   its own commit.

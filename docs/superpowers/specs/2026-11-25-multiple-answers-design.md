# Multiple answers and the Meta Matching tab — design

Date: 2026-11-25. Status: draft for approval. Supersedes the three PLANS.md items "Multiple
answers, step 1", "Multiple answers, step 2" and "Redesign Unassigned Feeders into a feeders
worktable".

## Why

Most hunts are one-answer puzzles feeding one meta, and the current model handles that, and
multi-answer puzzles whose answers all feed the same meta, fine. Two things don't work:

1. **Per-answer routing.** A puzzle's answers can't go to different places. Routing is a
   puzzle-level `feeds` tag, and an answer is a fragment of the `/`-joined `puzzles.solution`
   string, so "answer 2 is unassigned while answer 1 feeds Meta A" can't be expressed. Unassigned
   Feeders only knows "this puzzle feeds nothing".
2. **Seeing the matching state.** The thing that actually causes confusion mid-hunt is: which
   answers have been matched, which are still available, and which metas still need answers. Today
   an assigned feeder's row is deleted from Unassigned Feeders, taking its Notes with it, and in a
   PB-style hunt the solver's guess at "how many feeders does this meta need" lives in a row of
   the feeder table, so filtering the feeders hides it.

Situations to model (from the user):

| # | Situation | How it behaves under this design |
|---|---|---|
| 1 | A group whose puzzles' answers all feed its single meta | Puzzle has one feeds target → each new answer is placed there automatically. Unchanged from today. |
| 2 | A group with *n* metas; each puzzle has *n* answers, one per meta | Puzzle has several feeds targets → answers land unplaced and are placed one by one. Working out which answer goes where is part of the solving. Full support (pencilling, round grid) is a later phase. |
| 3 | Pure meta matching (PB): no structure, some puzzles happen to have several answers | Puzzles have no target → every answer is unplaced until matched. The Meta Matching tab is the main tool. |
| 4 | A bonus/secret meta: some puzzles have a second answer feeding a separate meta | Answer 1 auto-places to the round meta; answer 2 also auto-places there (single target) until someone moves it to the bonus meta. Accepted: the bonus meta usually isn't known until the second answer appears. |

Situations 2 and 4 are rare; they should be *modellable*, not optimised.

## Goals

- An answer is a first-class record: fix or remove one answer without touching the others.
- Each answer is placed independently, onto any number of targets (one answer feeding two
  metas must keep working).
- A puzzle knows how many answers it expects; "solved" is derived when that many are in, and
  every surface can say "1 of 3".
- A **Meta Matching** tab replacing Unassigned Feeders: a Metas table (what each meta still
  needs) above a Feeders table (every feeder answer, assigned ones kept but hidden by default),
  with headline totals that show at a glance whether needs and supply match.
- Per-meta **expected feeders** count, and per-answer **No meta** flag, both bot-owned.
- Discord / dashboard parity for every new action.

## Non-goals (this design)

- **Pencilled placements** and the **round grid** for situation 2. The model leaves room (a
  placement gains a `confirmed` flag; a round is a plain Group whose members include its metas),
  but they are a later phase.
- Expected-feeder counts for runarounds, capstones or metametas.
- Minimeta clusters (`parent_puzzle_id`, `!solve N` in a minimeta) — unchanged, still
  puzzle-level.
- Answer history/undo UI beyond what the answer rows naturally record.
- Changing how informs/evidence routing works.

## Anti-goals

- Making the common case (one answer → one meta) any slower or noisier. No extra clicks, no "1?"
  markers on ordinary puzzles.
- Notes that come loose from what they were written against — the reason approach B/C were
  rejected. Sheet rows are keyed by answer from the first release of the new tab.
- Sheet cells that look editable but are silently overwritten — bot-owned values are edited
  through the bot, user-owned columns are never written by the bot.

## Approaches considered

- **A. Answers as rows, staged (chosen).** New answer and placement tables; `puzzles.solution`
  kept as a derived read-only copy so the many existing readers keep working and migrate one at a
  time; Meta Matching tab keyed per answer from day one.
- **B. Keep the `/` string, add an answer index to `tags`.** Much cheaper, but fixing or removing
  an answer renumbers the ones after it, so placements and notes silently move to the wrong
  answer. Rejected.
- **C. Ship the Meta Matching tab first with per-puzzle rows, answers later.** Quickest visible
  win, but switching row keys from puzzle to answer later orphans every note typed meanwhile.
  Rejected.

## Decisions

### Data model

- **`puzzle_answers`** — one row per answer: `id`, `puzzle_id` (FK, cascade delete), `answer`
  (upper-cased text, as today), `position` (display order, 1-based, stable), `no_meta` (bool,
  default false), `created_by` (Discord user id, nullable), `created_at`.
- **`answer_placements`** — `answer_id` (FK, cascade), `tag_group_id` (FK, cascade), unique on
  the pair. Many placements per answer (one answer feeding two metas). Later phase adds
  `confirmed` (bool); everything in this phase is confirmed. *Builder's call whether to add the
  column now defaulted true.*
- **`puzzles.expected_answers`** INT NOT NULL DEFAULT 1, and
  **`puzzles.expected_answers_confirmed`** BOOL NOT NULL DEFAULT TRUE (the default 1 is a known
  value, not a guess).
- **`puzzles.expected_feeders`** INT NULL (NULL = unknown; meaningful on Metapuzzle targets only),
  and **`puzzles.expected_feeders_confirmed`** BOOL NOT NULL DEFAULT TRUE.
- The `*_confirmed` flags are **display only** ("6?" for a guess); they never change whether a
  puzzle counts as solved or a meta as satisfied.
- **`puzzles.solved`** stays a stored column (raw SQL reads it, e.g. `bot/web/data.py`); the
  answer seam keeps it in sync with the derived rule.
- **`puzzles.solution`** stays, rebuilt from the answer rows (`/`-joined in `position` order) on
  every answer write, so existing readers keep working. It is written only by the answer seam.
- **The `feeds` tag stays** and keeps its current meaning for unsolved puzzles ("expected to feed
  this target"; drives the meta table's blank placeholder row) and drives auto-placement.
- **Migration:** split each existing `puzzles.solution` on `/` into answer rows (skip empty
  fragments), set `expected_answers` to the fragment count for solved puzzles (so nothing becomes
  un-solved) and to fragment count + 1, unconfirmed, for partially solved puzzles (so nothing
  becomes solved), and create a placement for every (answer × the puzzle's existing `feeds` targets) —
  i.e. today's behaviour, where every answer goes to every feeds target, is preserved exactly. A
  puzzle whose solution is the ✅ placeholder migrates to **no answer rows and
  `expected_answers = 0`** (user decision), staying solved and out of the unassigned count. Must run on MariaDB 10.11 and MySQL 8.0 (CI) — no MariaDB-only syntax.

### Solving rules

- A puzzle is **solved** when its answer count ≥ `expected_answers`. Reaching the count triggers
  everything a solve does today (channel move, sheet archive, announcements, roll-up refresh).
  Below the count it is shown as "k of n" and nothing solve-related moves.
- `PARTIAL_STATUS` ("partially solved") becomes derived from 0 < k < n rather than set by hand.
Commands (user decisions, 2026-11-25):

- **`!solve ANSWER`** = add this answer *and the puzzle is finished*. If fewer answers than expected
  are in, the expected count is lowered to match — silently (noted in the reply) when the count is
  a guess or the default 1; **with a warning** when the count is confirmed (see below). On a puzzle
  with no answers and the default count it behaves exactly as today.
- **`!partial ANSWER`** = add this answer, *more to come*: adds it without solving. If adding it
  would meet the expected count, the count goes up by one (unconfirmed, shown `2?`) so the puzzle
  stays unsolved; otherwise the count is left alone (confirmed by user). If the count is confirmed and this answer meets it, the puzzle
  solves, since the team has said how many answers there are.
- **`!add_answer ANSWER`** = just add the answer; if that reaches the expected count the puzzle is
  solved.
- **`!answers 3`** = this puzzle has three answers (confirmed). **`!answers 3?`** = it might have
  three (guess). Lowering to ≤ the current count solves; raising above it un-solves.
- **Warning when `!solve` cuts a confirmed count short:** nothing is written yet; the bot replies
  with the existing `ConfirmView` buttons (`bot/cogs/ui.py`), e.g. "This puzzle has 3 answers and
  only 1 is in." → **[Solve with 2 answers]** **[Just add the answer]** **[Cancel]**. The dashboard
  shows the same three choices in its solve modal. Timeout = cancel.
- `!add_solution` / `!change_solution` stay as aliases (add answer / replace one answer); a new
  `!remove_answer` removes one. Exact replace-one-of-several UX is the builder's call.
- **`!mark_as_complete` / `!complete`** sets `expected_answers` to 0 (user decision): the puzzle
  is solved with no answers. Wherever an answer would be displayed (sheet header, meta tables,
  Feeders table, dashboard, Discord) it shows a ✅ instead. A completed puzzle has nothing to
  feed: it never counts as unassigned and isn't counted as Assigned on any meta. Its Feeders-table
  row (if it had a placeholder) stays, shows ✅, and is hidden by the default filter (*my call*),
  so notes written against it survive. The derived `puzzles.solution` copy reads "✅" for such a
  puzzle, so readers not yet migrated keep showing the tick.
- **Backsolved** stays a label on the puzzle, as today.
- **Unsolve** removes all the puzzle's answers (and so their placements). In the Feeders table the
  first answer row re-keys back to the unsolved placeholder, keeping its Notes; further answer rows
  are deleted (*my call*). Removing a single answer is `!remove_answer`.
- Announcements to fed metas (`_announce_to_fed`) go per placement: an answer is announced in the
  channel of each target it is placed on, when it is placed.

### Placement rules

- **Auto-placement:** when an answer is added and the puzzle has exactly one `feeds` target,
  the answer is placed there. Otherwise (zero or several targets) it is unplaced.
- **Tagging an already-solved puzzle** to a meta (`!feeds`, dashboard drag) places its answers
  if that leaves it with exactly one feeds target and its answers are unplaced; otherwise answers
  stay as they are and must be placed individually. *Builder's call on exact edge cases; the rule
  of thumb is "never silently move an answer that a person placed".*
- **Placements and tags:** placing an answer on a target the puzzle isn't tagged to also adds a
  `feeds` tag (*my call*), so every existing tag-driven reader — board columns, contribution-table
  members, the runaround subtree walk — still finds the puzzle, and later answers then see several
  targets and land unplaced (right for situation 4). Unplacing an answer never removes a tag.
- **Removing a puzzle's feeds tag** removes its answers' placements on that target.
- Manual placement: `!place ANSWER META` / `!unplace ANSWER META` from the puzzle's channel (answer
  matched by text, or by position number when ambiguous), and on the dashboard (below).
- **No meta:** `!nometa ANSWER` / dashboard tick. Excludes the answer from the unassigned count
  and feeder lists; does not delete it.
- **Expected feeders:** `!feeders N` / `N?` in a meta's channel, and editable on the dashboard.

### Unassigned / assigned

- An answer is **assigned** if it has at least one placement, **excluded** if `no_meta`, otherwise
  **unassigned**.
- An unsolved puzzle with no feeds target and no answers yet appears as a single placeholder row
  (as today's Unassigned Feeders does). Targets themselves are never feeders. Minimeta feeders are
  excluded as today.
- `PuzzleJsonDb.get_unassigned` becomes answer-level (plus the unsolved placeholders).

### Meta tables (contribution tables)

- One Feeder row **per placed answer** (key `feeds:<pid>:a<answer_id>`), plus one blank
  `feeds:<pid>` placeholder for a feeds-tagged puzzle with no answer placed here yet (unsolved, or
  solved but its answers went elsewhere — *builder's call whether the latter keeps a placeholder*).
- Transitive subtree members (runarounds) keep their current behaviour but render per answer.
- Keys change from `feeds:<pid>` to `feeds:<pid>:a<id>` for solved feeders. Solvers do add note
  columns inside contribution Tables; `plan_rows` (`bot/core/table_reconcile.py`) already re-keys a
  departed row in place to a pending key for the same member, so a puzzle's existing row becomes
  its first answer's row with notes kept. Rows for further answers start empty — acceptable, since
  today they were one row.

### The Meta Matching tab (replaces "Unassigned Feeders")

```
   A                               B     C                            D
1  Feeders still needed by metas:  7     Unassigned feeders:          9      ← sheet formulas
2
3  ┌ Metas ───────────────────────────────────────────────────────────────────┐
4  │ Meta            Answer    Assigned  Expected  Still needed  Notes        │
5  │ Fire meta                 3         5         2             theme=...    │
6  │ Water meta                4         6?        2?                         │
7  │ Earth meta      GRANITE   6         6         0                          │
8  └──────────────────────────────────────────────────────────────────────────┘
9
10 ┌ Feeders ────────────────────────────────────────────────────────────────┐
11 │ Puzzle      Answer    Length  Assigned to         No meta  Notes        │
12 │ Puzzle 4    KIWI      4                                    fire?        │
13 │ Puzzle 7    FIG       3                                                 │
14 │ Puzzle 7    LEMON     5       Fire meta                    (hidden)     │
15 │ Puzzle 9    ✅                                             interaction  │
16 └──────────────────────────────────────────────────────────────────────────┘
```

- **Layout:** the Metas table sits above the Feeders table (so filtering feeders never hides the
  meta counts). Today `reconcile_table` appends below a Table, which would overwrite the Feeders
  table, so the Metas reconcile must **insert sheet rows** (`insertDimension`) to grow, shifting the
  Feeders table down. New capability for the reconciler.
- **Row 1 totals** are sheet formulas over the two tables, written once at creation, not values
  the bot writes on each reconcile. "Feeders still needed" = sum of Still needed (blank Expected
  contributes nothing); "Unassigned feeders" = Feeders rows with blank Assigned to and No meta
  unticked.
- **Metas table:** one row per Metapuzzle-type target in the hunt (not capstones, runarounds or
  metametas — *my call*), key = puzzle id (hidden column, as today). Bot-owned: Meta (name, linked
  to its sheet), Answer (the meta's own answer, blank until solved), Assigned (count of answers
  placed on it), Expected (`expected_feeders`, shown `N?` when unconfirmed, blank when unknown).
  **Still needed** is a sheet formula column (Expected − Assigned, blank if Expected blank).
  **Notes** is user-owned. Solved metas stay listed (no reshuffling as metas fall). Order: the
  board's order for targets — *builder's call*.
- **Feeders table:** one row per feeder answer (key = answer id) plus unsolved placeholders (key =
  puzzle id, distinct namespace), **never pruned on assignment**, only when the answer or puzzle is
  deleted. Bot-owned: Puzzle (linked), Answer, Length, Assigned to (target names, linked, comma
  separated), No meta (shown as a tick; bot-owned, set via command/dashboard — a plain text ✓ or a
  read-only checkbox, *builder's call*, but editing it in the sheet must not be the way to set it).
  **Notes** user-owned.
- **Hidden by default:** the bot sets a basic filter hiding rows with non-empty Assigned to, once
  at creation; it never re-applies it (people's own filter changes are respected). Excluded (No
  meta) rows are also hidden by that default filter — *my call*.
- When an unsolved placeholder's puzzle gets its first answer, the placeholder row becomes that
  answer's row (re-keyed in place, Notes kept); further answers append. This is the one re-key the
  table does — it matters because solvers write notes against unsolved puzzles too.
- Notes stay attached to their row for the row's whole life. Reconcile must therefore update rows
  in place and must **not** use the shared delete-and-shift prune for assignment (see the
  2026-11-07 note on `reconcile_table` in PLANS.md). Deleting an answer/puzzle may delete its row.
- **Existing hunts:** the tab is created fresh under its new name on first reconcile; the old
  "Unassigned Feeders" tab is left in place (renamed "Unassigned Feeders (old)") so nobody's notes
  vanish — *my call*. `!rebuild_unassigned` rebuilds the new tab.
- `show_round` (the existing option adding a Round column) carries over to the Feeders table.

### Dashboard

- **Solve modal:** shows "Answers (k of n)" as a list of answer chips, each removable and
  editable; an Add answer box; an "Expected answers" number with a "guess" toggle. Buttons
  mirror the commands: **Solve** (add + finished), **Partial** (add, more to come), **Add answer**,
  plus per-chip edit/remove, Unsolve and Mark complete.
- **Board:** a feeder card shows one chip per answer. A chip is the drag unit for placement
  (drag a chip onto a meta = place that answer; onto Unassigned = unplace it); dragging the card
  keeps today's puzzle-level behaviour. Each chip shows where it is placed. A No-meta toggle on the
  chip.
- **Meta header:** "Assigned 3 / Expected 5" with inline edit of expected (and guess toggle).
- **Unassigned column:** answer-level, so a puzzle with one placed and one unplaced answer appears
  there with only the unplaced chip.
- A dashboard **Meta Matching view** mirroring the tab (metas with counts; feeder answers with a
  "show assigned" toggle and target filter) — parity with the sheet. *Builder may land this after
  the sheet tab.*
- **Discord:** puzzle info/solve messages say "1 of 3"; feeder announcements per placement.

### Build order (each a reviewable stage)

1. Tables + migration + answer seam in `PuzzleService` (all answer writes go through it; it keeps
   `puzzles.solution` in sync). No behaviour change visible.
2. Expected answer count, derived solved / k-of-n, command and dashboard changes for answers.
3. Per-answer placement: auto-placement, `!place`/`!unplace`/`!nometa`, per-answer meta-table
   rows, dashboard answer chips.
4. Meta Matching tab, `expected_feeders`, dashboard counts and Meta Matching view.
5. *(Later phase)* pencilled placements, round grid for situation 2.

## Constraints

- Both solve entrypoints (Discord bang and web) must go through the shared `PuzzleService` seam
  (see dual solve/delete entrypoints) — the same applies to every new answer/placement action.
- Every mutation pushes to the dashboard (`@pushes_board`).
- Sheets quota: one service account for all teams; per-answer rows must not multiply API calls per
  solve — batch writes as today.
- CI runs MySQL 8.0 and basedpyright on `bot/`; prod is MariaDB 10.11.
- Check migration numbering against other worktrees before merging (latest on staging: 062).
- Native-Table reconcile keeps solver note columns inside the Table; row operations must span the
  full Table width.

## Left to the builder

Everything marked *builder's call* above; exact SQL types and indexes; command aliases and help
text; how the derived `solution` copy is phased out; the shape of the API payloads (adding an
`answers` array alongside `solution` is the expected direction).

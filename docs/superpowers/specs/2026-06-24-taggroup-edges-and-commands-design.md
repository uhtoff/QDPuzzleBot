# TagGroup edges & command vocabulary — design

**Date:** 2026-06-24 (revised 2026-06-25)
**Status:** Design agreed; not yet built. Supersedes the four-edge taxonomy in
`docs/PLANS.md` Part 1 (which is reconciled to this doc in the same change).
**Canonical summary:** the conceptual model is also recorded in `AGENTS.md`
("Domain vocabulary (canonical)"); this spec is the full version + the command surface.

> **Revision 2026-06-25** folded in a working session. Changes from the original:
> `kind` demoted from stored axis to derived (one declared field, `type`); a `type`-keyed
> derivation map (`default_edge` + `default_source_mode`); the `!convert_to_*` family
> redefined as **bulk member re-edge** with **promote-in-place** (moved from "out of scope"
> to in-scope); `!capstone` on an existing round **flips** feeders `feeds → informs` rather
> than orphaning them (**supersedes** the earlier "capstone orphans feeders" note); edge-aware
> `!sync`; `!group` replaces `!rnm`; `!clue`/`!clues` reframed as sugar over internal
> *information*; **informs materialised in `tags`** (resolves an open item); a single
> **generated** unified contribution table (a **real** native Table) replacing the copied
> "Meta Tab template"; "Unassigned Puzzles" → "**Unassigned Feeders**" with a feeds-only
> membership test; the mechanical **schema vocabulary alignment** (`rounds`→`tag_group`,
> `meta_id`→`target_puzzle_id`, … — now **in scope**, not deferred), with "round" reduced to *only* a
> `type` value and `Group` as the base (null/default) type. Later refinements: the "N of M informed"
> counter **dropped** as confusing; runaround subtree harvest is **`feeds`-filtered**; web parity for
> model-changing commands only (`!sync` stays bang-only); a four-milestone build map (A→D).
> AGENTS.md / PLANS.md re-reconciliation is a follow-up, not done in this edit.

## Why this exists

The structural model had grown four edge types (`feeds-meta`, `part-of`, `reveals`,
`aggregates-into`), a `kind`/`type` split, and a capstone framed as a thing that "never
solves". Discussion kept re-litigating "round types" because **`HuntRound` is the name
used for every group**. This spec settles a smaller, sharper model and the bang-command
vocabulary that establishes the relationships, so future work (capstones / M9b, and any
command-rename refactor) builds on agreed language.

## The model

### One entity — TagGroup

A **TagGroup** is a *named, typed group of puzzles, identified by a tag*; puzzles are
**tagged into** it (it is **not** "a group of tags"). It is a row in the existing `rounds`
table — `TagGroup` is the umbrella concept, `rounds` is just the table name. "**round**" is
reserved as a *label* for the everyday case, never the umbrella.

### Target puzzle — and what "solved" means

Every TagGroup is built around one **target puzzle** (`rounds.meta_id`). A **TagGroup is
solved when its target puzzle is solved** — that derived status is the only thing automatic;
the target is an ordinary puzzle a human solves like any other. A TagGroup with **no target**
is a view / holding-pen (a *metaless* group, labelled a **Group**); its members are
*unassigned* (they contribute to nothing).

### Two contribution edges — `feeds` and `informs` — plus `none`

A **tag** is one membership (puzzle ↔ TagGroup). Its **edge** says what the member
contributes to the group's target:

- **`feeds`** — the member contributes *its own answer*; available only once the member is
  **solved**. One answer, shared with every group it feeds.
- **`informs`** — the member contributes *other information*; can arrive **before / without**
  the member being solved or may come **after** solving, and is **distinct per target** (one puzzle can inform several
  capstones differently).
- **`none`** — plain membership: the puzzle is *grouped with* the others but contributes
  nothing to the target. (This is what the old `aggregates-into` edge meant. Needed anyway:
  every member of a *metaless* group is `none`, because there is no target to feed.)

`feeds` / `informs` are **contributions**; being tagged in is **membership**. The `tags`
table conflates the two today, and `none` is the state where a member is tagged in without
contributing.

**One edge per (member, group) pair.** `tags` has `UNIQUE(round_id, puzzle_id)`, so a member
carries exactly one edge to any one group. A member can hold *different* edges to *different*
groups (feed A, inform B, group-with C), but it cannot both feed and inform the **same**
target. A target's "mix of feeds and informs" therefore happens **across its members**, never
within a single member→target pair.

### meta vs capstone — derived, never declared

> **A meta is a puzzle that is *fed*. A capstone is a puzzle that is *informed*.**

A **meta** is a target puzzle that receives `feeds` edges; a **capstone** is a target puzzle
that receives `informs` edges. Same group machinery, solved the same way. Consequences:

- **Informing a puzzle makes it a capstone** — no separate kind, no precondition. Just as a
  puzzle that starts being fed becomes a meta, a puzzle that starts being informed becomes a
  capstone.
- **Derived means reversible** — strip the last `informs` edge and it stops being a capstone.
- A target may take a **mix** of `feeds` and `informs` (across members). What the target's
  sheet shows — an answer list, a dossier of facts, or both — follows from which edges feed it.

### One declared axis — `type` — with structure derived

The original design split two stored fields: `kind` (structure: `metaless / meta / metameta`)
and `type` (label: `Round / Metapuzzle / …`). **We collapse this to one declared field.**

`rounds.type` is the **only declared axis** — the flavour an organiser picks at creation:
`Round / Metapuzzle / Capstone / Runaround / Group`. Everything `kind` stored is **derived**:

- **has a target?** ≡ `meta_id IS NOT NULL`. And `type` already implies it — every flavour
  except `Group` has a target.
- **fed vs informed** (meta vs capstone) ≡ read from the incoming edges, *not* a column.
- **metameta** ≡ a meta whose feeders are themselves metas — a nesting/rollup detail derived
  from structure, never an organiser-facing kind.

Dropping `kind` as authoritative also **dissolves the "meta overload"** the original spec had
to apologise for: there is no longer a `kind=meta` schema value to confuse with "*a* meta"
(= a target that is *fed*). A stored `kind` is a denormalised cache of `meta_id IS NULL` that
can silently drift out of sync; deriving it removes that failure mode.

Physically dropping the `kind` column is part of the **schema vocabulary alignment** (now in
scope — see below); until that migration lands, the live model simply stops trusting it.
"round / metapuzzle / capstone / runaround / group" are labels and scope, and **`type` is where
they live** — "round" is now *only* a `type` value, never a table name or the umbrella.

**`Group` is the base type.** A metaless group (`type='Group'`) is the minimal tag_group: it has
no target, so `default_edge=none` just falls out of having nothing to feed. It is effectively the
*null / default* type — `type='Group'` ⟺ `target_puzzle_id IS NULL` (the renamed `meta_id`) — kept
as an explicit value for clarity rather than stored as `NULL`.

### `type` → derivation map

`type` is not just a label; it answers two behavioural questions, via a small **code-side map**
(the existing create `command_map` tuple, grown — *not* a new column):

| `type` | `default_edge` | `default_source_mode` |
|---|---|---|
| Round / Metapuzzle | `feeds` | `round` |
| Capstone | `informs` | `round` |
| Runaround | `feeds` | `subtree` |
| Group | `none` | `round` |

- **`default_edge`** answers *"what is the default way of tagging a puzzle into this group?"* —
  the edge applied by the **geography/sync** path (puzzles created in the group's category,
  `!sync`). Explicit `!feeds` / `!informs` / `!groups_with` still override per puzzle.
- **`default_source_mode`** answers *"should I traverse the tags of my tags?"* — written to the
  group's rollup (`rollups.source_mode`). Runaround → `subtree`; everything else → `round`.

`default_edge` is a **single source feeding three paths**: (1) tagging a newly-created puzzle,
(2) `!convert_to_<type>` flipping existing members, and (3) edge-aware `!sync`.

## Edge reconciliation (four → two + none)

| old `edge_type` | becomes | notes |
|---|---|---|
| `feeds-meta` | **`feeds`** | the member's answer feeds the target. |
| `reveals` | **`informs`** | information to the target's dossier; capstone is *informed*, and **solves** (not "never solves"). |
| `aggregates-into` | **`none`** | member of the group, not feeding the target. |
| `part-of` | **deleted** | clusters are puzzle→puzzle via `parent_puzzle_id` (M8), not a TagGroup edge. |

There is **no "N of M informed" progress counter** (dropped as confusing) — a capstone is solved
exactly when its **target puzzle** is solved, like any group; "informed enough" is not a concept.
The word **"pool"** is dropped entirely; the curated case is `none` membership +
`source_mode=explicit`, described as "the explicit / curated set".

## Channel placement — feeds owns geography

A puzzle's Discord channel lives in the category of the group it **feeds**. So `!feeds` /
`!feeds_only` set or relocate the channel's home; `!informs` and `!groups_with` **never move
it**. A puzzle that only informs/groups (no `feeds` edge) stays where it was created. A puzzle
that feeds several groups keeps the **current** tiebreak (unchanged) — it only affects `!reconcile`,
which reshuffles Discord regardless.

## Command vocabulary (target design — not yet built)

All membership commands take a `<groupcode>` — the **group's** handle. (The column is
`rounds.meta_code` → renamed to `tag_group.group_code` in the schema alignment; it exists on
metaless groups too, so it is really the *group code*. The vocabulary term is **groupcode**.)

**Web parity.** Every command that **changes the model** has a dashboard equivalent (create,
membership/`_only`, `!remove_from`, the convert family, `!clue`/`!clues`/routed `!informs`) — per
the project principle. The exception is **Discord-ergonomics** commands that only exist to work
around Discord's lack of bulk selection — `!sync` and friends — which are **bang-only**; the
dashboard selects directly, so they have no web analogue.

### Create a TagGroup

Creation makes a **brand-new group from a name** (with a fresh target puzzle, except `!group`).
Flavour sets the label/welcome, whether a target puzzle is made, and the derivation map row:

| command | aliases | makes target | `default_edge` | label (`type`) |
|---|---|---|---|---|
| `!r <name>` | — | `"name - meta"` | `feeds` | Round |
| `!mp <name>` | — | `"name"` | `feeds` | Metapuzzle |
| `!capstone <name>` | `!cs` | a capstone puzzle | `informs` | Capstone |
| `!runaround <name>` | `!ra` | the endgame | `feeds` (+ subtree) | Runaround |
| `!group <name>` | `!rnm` | — (unlinked group) | `none` | Group |

`!r` vs `!mp` differ *only* in the welcome message. `!group` **replaces** `!rnm`; `!rnm` is kept
as a back-compat alias that calls `!group` internally, and the label moves from "Metaless Round"
to "**Group**" (old rows keep their label until migrated). `!capstone` is the explicit
"declare it upfront" twin of the emergent rule that *informing a puzzle makes it a capstone*.

All create commands exist in **both** the web dashboard and as bang commands.

### Membership / contribution

Each is an **upsert on one edge-partition**: `!<edge> X` sets/adds that edge to `X` and
leaves other memberships alone; `!<edge>_only X` makes `X` the sole group within *that edge*
(removing other edges of the same type, leaving the other partitions untouched).

| edge | add | only |
|---|---|---|
| feeds | `!feeds <groupcode>` | `!feeds_only <groupcode>` |
| informs | `!informs <groupcode>` | `!informs_only <groupcode>` |
| none | `!groups_with <groupcode>` | `!groups_only_with <groupcode>` |
| remove (edge-agnostic) | `!remove_from <groupcode>` | — |

Guard preserved: a metapuzzle cannot be left with zero memberships (that would orphan its
round), so `!remove_from` / `_only` keep today's protection.

### Convert — bulk member re-edge (in scope; redefined)

The `!convert_to_*` family operates on **the puzzle you are standing on as a target**, and
**bulk re-edges its current members**. Each maps to a single `UPDATE tags … WHERE round_id = X`,
plus setting `type` and re-deriving `default_edge` / `default_source_mode`.

| command | effect on members | note |
|---|---|---|
| `!convert_to_meta` | all edges → `feeds` | |
| `!convert_to_capstone` | all edges → `informs` | the Puzzmon case — releases answers (see below) |
| `!convert_to_group` | all edges → `none` | |
| `!convert_to_runaround` | **no edge change** | sets `source_mode=subtree`; harvest descends the nesting subtree, **`feeds` members only** |
| `!convert_to_puzzle` | **removes all member tags** | destructive → **requires confirmation** ("are you sure"); replaces `!convert_to_feeder` |

Two behaviours are mandatory, not optional:

- **Promote-in-place.** Run on a puzzle with **no group yet** → create a group around the
  current puzzle, then apply the edge default. So convert always acts on "here"; create makes a
  new target "there". This preserves today's "wrap this puzzle as a meta" capability and makes
  `!convert_to_puzzle` its clean inverse (dissolve back to a bare puzzle). On a bare puzzle the
  member flip is a harmless no-op — promote establishes *what the puzzle is*, and membership is a
  deliberate second step (`!sync`, or hand-tagging). Idempotent on an already-correct group.
  **Geography-neutral:** promote does not create or move Discord channels/categories — the existing
  channel→category code resolution already tags a puzzle to the meta of its channel (and if it
  ever doesn't, it should, independent of any category tag).
- **Report what it swept.** A blunt flip sweeps heterogeneous edges (e.g. members that were
  `none` as well as `feeds`). The command **must report what it changed** ("flipped 7 `feeds`
  + 2 `none` → `informs`"), turning "automation can only go so far" into something the organiser
  can see and reverse, rather than a silent reinterpretation.

**The capstone flip (supersedes "capstone orphans feeders").** `!convert_to_capstone` on an
existing round flips its feeders `feeds → informs` — it does **not** orphan them. The members
keep a **materialised `informs` edge with zero evidence** ("informing, nothing logged yet"),
*and* because they no longer **feed** anything, their answers resurface on the **Unassigned
Feeders** sheet ready to metamatch. (2026 MIT Puzzmon: drop `!capstone` on the existing round
meta; every tagged puzzle becomes an informer with a blank dossier row, and its answer is freed
for matching elsewhere. "A load of puzzles informing with no information logged — that's okay.")

### Informs data

The informs edge is the *relationship*; its information is logged separately as **information**
(internally), because info is richer than a single answer (and per-target):

- `!informs <groupcode>` — establish the relationship (a bare `informs` edge, no datum).
- `!informs <groupcode> <Information>` — establish it **and** log one **routed** datum in one
  go (sets the evidence row's `target` at insert).
- `!clue <text>` / `!clues <a> <b> …` — sugar over the same internal *information* logger:
  log one (or, split via `shlex`, several) **unrouted** datum(s), target bound later (matches
  shipped M9 "log now, bind later"). `!clue`/`!clues` are the entry points; "information" is the
  internal concept.

### Bulk & folded-away

- `!sync` — **edge-aware**: runs `!<default_edge>_only <this group>` on every puzzle in the
  current Discord category, reading `default_edge` from the group's `type`. So `!sync` on a Round
  feeds the category, on a Capstone **informs** it, on a Group groups it — which is exactly what
  makes `!convert_to_capstone` + `!sync` do the obviously-right thing (and stops sync from undoing
  a conversion). Like convert, it **reports what it swept**. Aliases: `!sync_feeders`,
  `!sync_meta`, `!sync_round`. **Bang-only** (no web parity): `!sync` exists to work around
  Discord's lack of bulk selection — it has no meaning on the dashboard, which selects directly.
- `!nest` and `!source_mode` are **folded away** — internal machinery, not organiser-facing.
  `!runaround` sets `source_mode=subtree` (+ nesting) for you; `!groups_with` covers the
  curated `source_mode=explicit` case.

### Legacy aliases (kept for muscle memory)

- `!feeds` ← `add_to_meta`, `add_to_round`
- `!feeds_only` ← `move_to_meta`, `move_to_round`
- `!remove_from` ← `remove_from_meta`, `remove_from_round`
- `!sync` ← `sync_feeders`, `sync_meta`, `sync_round`
- `!group` ← `rnm`
- `!convert_to_puzzle` ← `convert_to_feeder`

`!pool` is **not** kept — dropped completely (no legacy users to protect).

### Untouched (out of scope here)

`!rename_meta` / `!rename_puzzle`.

## Informs ↔ evidence

The shipped M9 evidence board already stores facts keyed by **`(source_puzzle, target_puzzle,
content)`** with auto-stamped provenance, target bound later, `!clue`/`!clues` to log. That
maps onto this model directly:

- An **`informs` edge** is the puzzle→group **relationship** (membership; drives which rows the
  dossier shows). No progress counter is derived from it.
- The **evidence rows** are the **per-target content** the edge carries — exactly symmetric
  with `feeds` (edge) + the solution string (payload), except informs payload is **per
  target**, so one source can inform several capstones with different content.
- A capstone's **dossier** = the roll-up of the informs content routed to it (see the unified
  contribution table below).

**Storage — resolved: informs is materialised in `tags`.** The original spec left open whether
the `informs` edge is materialised (an explicit `informs` row in `tags`) or *derived* from
`evidence_items` (a puzzle informs G iff it has a fact targeting G's target). The **capstone
flip with zero evidence** settles it: a flipped feeder informs the capstone but has logged
nothing, so there is nothing to derive — the bare edge must be a real row. Therefore:

- `tags.edge_type = 'informs'` is the **relationship** (one row per source→group; `UNIQUE`
  guarantees one).
- `evidence_items` is the **payload**: many rows per `(source, target)` allowed (the table has
  no uniqueness on that pair, only its own surrogate `id`). **Two pieces of information from one
  source to one capstone = two evidence rows, one informs edge.** Not two tags.
- An informs edge with **no** evidence rows is valid and expected (the flip case): it shows as a
  blank-content row in the dossier.

## Runaround & aggregation

A runaround is a meta group whose membership is "the whole hunt", harvested by the existing
M7 machinery:

- **Whole branch** — `!nest` the rounds under the runaround and run `source_mode=subtree`
  (`collect_subtree_puzzles`), which descends the nesting tree and harvests every solved
  **`feeds` member** (their answers), deduped — **edge-filtered to `feeds`**, not edge-agnostic.
  `!runaround` is the front-end that sets this up. (If today's subtree harvest is edge-agnostic,
  adding the `feeds` filter is part of this work.)
- **Curated set** — tag the chosen puzzles `!groups_with` the runaround and run
  `source_mode=explicit`.
- **Metas only** — `feeds` each round-meta's result puzzle into the runaround.

All three are the same aggregation engine pointed at different membership.

## Sheet surface — the unified contribution table

### One generated table per target

A target's sheet shows **one unified contribution table** — feeders, informers, and grouped
members in a single list — replacing the two flat renderers (answer-list vs dossier). It is a
**real** Google Sheets **native Table** — its stored range **covers the data body**, not just
the header. (Today's Unassigned tab is a *header-only, cosmetic* table — the range is row 0 and
the data rows are managed by hand; that gives none of the real Table behaviour.) A real table
gives banding / header styling / column types **and genuine interactive filter & sort on the
`Type` column** for free, built **in code**, with a **hidden key column** and a **visible,
filterable type column**. It grows via `appendCells(tableId)` — the table-aware, data-safe path
(see *Promote without losing user data*).

| column | role |
|---|---|
| **A** (hidden) | row key — see below |
| **B** `Type` | `Feeder` / `Information` / `Group` (visible, filterable) |
| C… | source puzzle, content/answer, etc. |

**Row key** (`A`, hidden) — anchored on the **edge type** (stable; *not* the volatile `tag.id`):

| key | `Type` | one row per |
|---|---|---|
| `feeds:<puzzle_id>` | Feeder | feeding member (one — `UNIQUE`) |
| `none:<puzzle_id>` | Group | grouped member |
| `informs:<puzzle_id>` | Information | bare informs edge, nothing logged yet |
| `informs:<puzzle_id>:<evidence_id>` | Information | each logged datum |

The `:<evidence_id>` segment **appears only when needed** — exactly the "evidence only when
necessary" instinct, keyed on stable surrogates so the incremental reconcile (column-A diff →
appends / updates / prunes) survives re-tagging.

### Generated, not copied

Today the per-meta roll-up tab is the meta puzzle's own tab, **copied** from a hidden
"Meta Tab template" (`duplicateSheet`), with the roll-up dumped as raw values from `A5`.
**Promote-in-place breaks the copy model**: a promoted puzzle's tab was copied from the
*regular* "Tab template" and you cannot `duplicateSheet` a fresh meta template over a tab that
already has the solver's work without destroying it. So the contribution table **must be
generated in code, onto whatever tab the target already has** — which is also what lets a
round↔capstone flip restyle the same region uniformly.

Because the templates are simple, this lets us **retire "Meta Tab template"**: target-ness just
*adds* the generated table, so **create and promote become the same path**. (Going further —
retiring "Tab template" too and generating *every* puzzle tab in code — is enabled but is a
**separable follow-up milestone**, not part of this spec.)

### Promote without losing user data

The transform from a regular puzzle tab to a target tab is **all inserts, never sized
overwrites**, because the answer to "row added → pushed down or overwritten?" is *request-type
dependent*:

- `insertDimension` (ROWS/COLUMNS) and `appendCells(tableId)` **shift / push down** — data
  preserved.
- `updateCells` / `values.update` **overwrite in place** — destroys anything in the range.

Our current Unassigned reconcile *overwrites* (`updateCells`) and survives only because that tab
is **dedicated** (nothing below the table) and its native Table is **header-only** (cosmetic).
That reconcile **must not be copied verbatim onto a shared target tab** — and the contribution
table is a **real** table anyway (range covers the body), so it grows the table-aware way.
Instead:

- **Header:** `insertDimension(COLUMNS)` at `A` shifts the existing 3-line header (name/url,
  flavour, progress) right to `B`; the native Table's column range tracks the shift. Hide `A`
  (the key column). Regenerate only the **bot-owned** cells (name / url / answer); **flavour and
  progress ride the shift untouched** (and may additionally be read-and-restored as belt-and-
  suspenders if create vs regular templates place them on different rows).
- **Table growth:** the table is a **real** native Table (range covers the body), so it grows via
  `appendCells(tableId)` — the table-aware path that fills the first free body row, is footer-aware,
  and inserts (never overwrites). On promote, that pushes the solver's existing body content
  **below** the table rather than eating it. (`insertDimension`-then-write, insert-ordered first,
  is the equivalent fallback if a table op is unavailable.) Updates are in-place `updateCells` on
  matched body rows; prunes are `deleteDimension` (the table range shrinks with the row).
- **Grey region:** mark the table's columns below the current last row grey — the **social
  contract** ("bot-managed, don't type here"), mirroring the meta template's existing
  "leave these for the bot". It is *not* the safety mechanism (a user can still type in a grey
  cell); the guarantee comes from insert-based growth. Belt (grey) + suspenders (insert).
- Bot/user column split is by **range-limiting** writes (as the Unassigned `Notes` column already
  is): the bot writes only its columns and never the user's.

## Unassigned Feeders (rename + semantic test)

Rename the **"Unassigned Puzzles"** sheet to **"Unassigned Feeders"** — and change what qualifies,
not just the label. Today `get_unassigned` excludes any puzzle *tagged into a meta-bearing round*
(mere membership disqualifies). The new test is **"has no `feeds` edge to any meta"**: a puzzle
whose *answer* feeds nothing is an unassigned **feeder**, regardless of `informs` / `none`
membership. This is what makes flipped Puzzmon feeders (now informers) resurface here for
metamatch while still informing their capstone. The tab stays a generated native Table with a
hidden id column and a user-owned `Notes` column — and shares the generator with the contribution
table, so it is **retrofitted from its current header-only (cosmetic) form to a real native Table**
(range over the body, grown via `appendCells(tableId)`), gaining the same real filter/sort.

## Current state vs target

**Built (under the old names):** the `feeds-meta` and `aggregates-into` edges; `parent_group_id`
nesting + `source_mode=subtree`/`explicit` (M7); minimeta clusters via `parent_puzzle_id`
(M8); the `evidence_items` store + `!clue`/`!clues` (M9); create commands `!r`/`!mp`/`!rnm`;
`add`/`move`/`remove_*_meta`, `!pool`, `!nest`, `!source_mode`, `!sync_meta`,
`!convert_to_meta`/`_feeder`; the generated **Unassigned Puzzles** native Table; per-meta roll-up
tabs **copied** from "Meta Tab template".

**Designed, not built (this spec):** the `informs` edge (the `reveals` rename + capstone =
informed-puzzle, **materialised in `tags`**); the renamed command surface
(`!feeds`/`!informs`/`!groups_with` + `_only`, `!remove_from`, edge-aware `!sync`); `!capstone`
(`!cs`) / `!runaround` (`!ra`) / `!group` create commands; the **`!convert_to_*` family**
(bulk re-edge + promote-in-place + report-what-it-swept); the per-edge `_only` semantics;
the `type` derivation map (`default_edge` / `default_source_mode`); channel-placement-follows-
`feeds`; folding `!nest`/`!source_mode` away; the **generated unified contribution table** (and
retiring "Meta Tab template"); **Unassigned Feeders** rename + feeds-only test; the **schema
vocabulary alignment** (`rounds`→`tag_group`, `meta_id`→`target_puzzle_id`, `meta_code`→`group_code`,
`edge_type` value renames, drop `kind`) — see its own section below.

## Schema vocabulary alignment (in scope)

These mechanical renames are **in scope for this work, not deferred**. They are *not*
behaviour-modifying — but leaving the schema speaking the old language (a `rounds` table that is
really every kind of group, a `meta_id` that is really "the target puzzle", a `kind=meta` that
overloads "a meta") is exactly what kept re-summoning the "what's a round?" confusion this
session had to untangle. Making the tables say what the model means is what stops the drift
recurring; doing it *now*, before more code is written against the old names, is the cheap moment.

| current | →  | aligned | note |
|---|---|---|---|
| table `rounds` | → | `tag_group` | the umbrella entity |
| `rounds.meta_id` | → | `tag_group.target_puzzle_id` | the target puzzle; `NULL` for a base Group |
| `rounds.meta_code` | → | `tag_group.group_code` | the **groupcode** vocabulary term |
| `rounds.kind` | → | *dropped* | now derived (has-target / fed-vs-informed / metameta) |
| `tags.round_id` | → | `tags.tag_group_id` | FK follows the table rename |
| `rollups.round_id` | → | `rollups.tag_group_id` | FK follows the table rename |
| `tags.edge_type='feeds-meta'` | → | `'feeds'` | value rename |
| `tags.edge_type='aggregates-into'` | → | `'none'` | value rename |

(`tag_group.parent_group_id` already names "group" correctly and is unchanged. The new
`'informs'` edge value is *created* correctly — no rename.) Code identifiers follow mechanically:
SQL, the DAL, and the `RoundData` dataclass (→ `TagGroupData`, with `meta_id`→`target_puzzle_id`,
`meta_code`→`group_code`, `kind` removed), plus web-dashboard and test references. How far to
carry the deeper symbol renames (the `HuntRound` class, `create_round`/`refresh_meta` handler
names) is a sweep-size decision for the implementation plan.

**Execution discipline.** Run the alignment as **discrete, behaviour-preserving commits, never
mixed with the behavioural changes** (per "don't mix refactors with behaviour changes"). The pure
structural renames (table, `meta_id`/`meta_code`, FK columns) can **lead** — rename-first, so the
feature work below is written against the agreed names from the start. The value renames
(`edge_type`) and the `kind`-drop pair naturally with the edge/`informs` phase. Migration
mechanics are `RENAME TABLE` / `ALTER … CHANGE COLUMN` / `UPDATE … SET edge_type` / drop column;
check migration-number collisions against parallel worktrees before merging.

A table rename is a **breaking** schema change (old code can't query a renamed table), but that is
**not** a deploy hazard here: this whole body of work stays **in dev** and lands as a unit — it is
never incrementally deployed to a running bot — so the rename needs no backward-compat shim,
expand/contract dance, or maintenance window. Migration and code change ship together by virtue of
landing together.

## Implementation milestones

Too large for one plan; build as four sequenced pieces, each its own spec → plan → build. Each
later milestone depends on the data layer from **A → B**.

1. **A — Schema vocabulary alignment** (rename-first; dev-only breaking change). `rounds`→`tag_group`,
   `meta_id`→`target_puzzle_id`, `meta_code`→`group_code`, FK `round_id`→`tag_group_id`, drop `kind`
   (derive); sweep SQL / DAL / `RoundData`→`TagGroupData` / web / tests. Pure mechanical, no behaviour.
2. **B — Edge model & storage.** `informs` materialised in `tags`; `feeds`/`informs`/`none`
   partitions; `edge_type` value renames; `default_edge`/`default_source_mode` derivation map;
   `kind` derived; feeds-filtered subtree harvest.
3. **C — Command surface** (bang **+ web**). Create (`!capstone`/`!cs`, `!runaround`/`!ra`, `!group`);
   membership (`!feeds`/`!informs`/`!groups_with` + `_only`, `!remove_from`); the convert family
   (bulk re-edge + promote-in-place + report-what-it-swept); edge-aware `!sync` (bang-only);
   `!clue`/`!clues` = information + routed `!informs`. Web parity for the model-changing ones.
4. **D — Sheet generation.** Unified contribution table as a **real** generated native Table
   (retire "Meta Tab template"); insert-based promote-preservation; **Unassigned Feeders** rename +
   feeds-only test + real-table retrofit.

## Open items (decide at build time, not here)

- **clue-routing ↔ informs-edge coupling** — when an unrouted clue is routed (`set_target`), it
  should **ensure the `informs` tag edge** exists (routing a fact at a target *is* informing it);
  confirm the exact coupling when B/C are built.
- **deeper code-symbol rename sweep size** — how far past the schema columns to carry renames (the
  `HuntRound` class, `create_round`/`refresh_meta` handler names); decide when planning A.

## Out of scope

Actually building any of the milestones below (this is the design, not the build); retiring
"Tab template" / generating every puzzle tab in code (separable follow-up); the M9b evidence/clue
template detail (the PB10 "Detectives" structured-clue specialization remains as designed in
PLANS Part 1, now expressed in `informs` terms); re-reconciling `AGENTS.md` / `docs/PLANS.md` to
this revision (follow-up).

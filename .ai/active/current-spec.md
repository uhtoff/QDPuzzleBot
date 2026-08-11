# Current Spec

## Goal

Bring command/route permission tiers into line after the 2026-08-11 gating audit.

**APPLIED 2026-08-11.** All six decisions are in the tree; the edit-site lists below are
kept as the record of what moved and why. Guarded by `tests/test_command_gating.py` (probes
each command's tier through its real checks) and `tests/test_web_route_gating.py` (asserts
the whole route→tier map, resolved through delegation). Full suite 1894 passed.

## Background

Full audit of 84 bang commands and 78 web routes at `ce05d00`. Tiers nest:
`ORGANISER_ROLES = ADMIN_ROLES + ORGANISER_ROLE_NAMES` (`bot/core/constants.py:12`), so an
admin passes every organiser check. `organiser_gate_active()` returns false on a guild with
no Organiser role configured, and every organiser check then passes for everyone — admin is
never bypassed.

Published table: https://claude.ai/code/artifact/f1a824cd-6dfe-44c4-a556-bb5eef0fe3b6

## Decisions taken (pending implementation)

### D1 — `!absorb_category`: admin → organiser

It is Discord category-layout plumbing, the same family as `!consolidate_to` and
`!extract_target_to_category`, which are organiser. Admin was a third setting for the same
kind of operation.

Edit sites:
- `bot/cogs/puzzles.py:3666` — `@is_admin()` → `@is_organiser()`
- same docstring (`:3677`) — "Admins/mods only." → "Organisers/admins only."
- `docs/commands.md:209` — Who column "admins/mods" → "organisers/admins"
- `tests/test_absorb_category.py` — check for a gate assertion; add one if absent

### D2 — tag-group creation becomes organiser on both edges

`!round` and its aliases (`!r`, `!mp`, `!metapuzzle`, `!capstone`, `!cs`, `!runaround`,
`!ra`, `!group`) are open today, while renaming, deleting, retyping, converting and syncing
the same group are all organiser. Gate the creator to match. Puzzle creation (`!puzzle`/`!p`,
`POST /hunts/<uid>/puzzles`) stays open — adding a puzzle is a solver action.

Deliberate behaviour change: non-organisers lose all nine aliases. Bounded by the
unconfigured-guild bypass (a guild with no Organiser role sees no change).

Edit sites:
- `bot/cogs/puzzles.py:2154` — add `@is_organiser()` to the `round` command (covers every
  alias; no per-alias predicate extraction needed)
- each of the five affected `docs/commands.md` rows (45–48) — "anyone" → "organisers/admins"
- `bot/web/actions.py:2125` — `require_organiser` on `POST /hunts/<uid>/metas`
- `frontend/src/components/CreateControls.tsx` — hide the "New Group" button when
  `!detail.hunt.can_manage` (the signal `BoardGroup.tsx:49` already uses); leave "New puzzle"
- web test: non-organiser gets 403 from `POST /hunts/<uid>/metas`, and is still allowed on
  `POST /hunts/<uid>/puzzles`

Also written up as phases 3–5 of
`docs/superpowers/specs/2026-08-11-in-place-target-creation-design.md`; that spec is
otherwise deferred, and D2 is independent of the feature it was designed alongside.

### D3 — drop `has_permissions(manage_channels=True)` from all six commands

The layout trio becomes plain organiser; the settings trio becomes plain admin.

Rationale: it predates the role tiers (it was *the* gate in the earliest commits, before
admin/organiser existed) and never gated anything real — discord.py checks the *invoking
user's* permission, while the bot does the channel work with its own, so it only decides who
may ask. The settings trio doesn't touch channels at all. Precedent is already set:
`0551c9b` (2026-08-08) dropped it from the six `!convert_to_*` commands and explicitly
parked the rest ("Note `!update_settings` keeps its admin + Manage Channels bar; only the
converts moved"). No test asserts it.

Edit sites — remove the decorator line at `bot/cogs/puzzles.py` 1008, 2328, 2366, 2545,
2558, 2634:
- organiser + MC → organiser: `!backfill_glyphs`, `!extract_target_to_category`,
  `!consolidate_to`
- admin + MC → admin: `!show_settings`, `!show_puzzle_settings`, `!update_settings`

Docs — drop the `†` from `docs/commands.md` rows 52, 53, 208, 216, 220, 221, and delete the
now-unused footnote defining it at line 22. Check each command docstring for a Manage
Channels mention too.

### D4 — the organising principle: tier follows the area of the operation

The areas in `docs/commands.md` § Permissions at a glance — Create, Solve, Tag, Annotate,
Read, Rename & delete, Restructure, Category layout, Maintenance, Hunt lifecycle, Settings,
Roles, Archive — are the unit that carries a tier. An operation's gate follows the area it
belongs to, on whichever surface it is invoked from.

**Discord is not the authority here** (an earlier draft of this file said it was; the user
corrected it). The Discord side currently *happens* to match the principle almost everywhere,
which is why the table falls out cleanly — but where it doesn't, Discord is the one that
moves: D2 gates `!round` because creating a tag group belongs with the rest of the tag-group
lifecycle, not because the web said so.

Consequence worth noting: when an operation's tier changes, its area usually changes with it.
D6 moves `!remove_sheet` out of *Annotate* and into *Rename & delete*.

### D5 — web tagging and minimeta creation open up to match their areas

- **Tag area → anyone.** `POST /puzzles/<id>/meta` (retag: move/add/remove) becomes
  ungated, matching `!feeds` / `!feeds_only` / `!informs` / `!informs_only` /
  `!groups_with` / `!groups_only_with` / `!remove_from`. Explicitly wanted for the remove
  case: a solver should be able to tidy a meta up when no organiser is around.
- **Create area → anyone.** `POST /puzzles/<id>/make-minimeta` and
  `POST /puzzles/<id>/minis` become ungated, matching `!make_minimeta` and `!add_mini`.
  Removal (`remove-minimeta`, `DELETE minis`) stays organiser — that is Rename & delete.

Safe because the populations already match: every route runs `@login_required` then
`_resolve_visible_hunt`, which resolves Discord guild membership and 403s unless
`hunt_visible` passes. "Anyone" on the web is a guild member who can already see that hunt.

This is the only decision in the batch that *widens* access, so it was confirmed explicitly
rather than taken as following from D4.

Edit sites:
- `bot/web/actions.py:1165` — drop `require_organiser` from `retag_puzzle`
- `bot/web/actions.py` — drop it from `make_minimeta` and `create_mini` (the POST routes
  only; leave the DELETE/remove ones)
- `docs/commands.md` — update the "the dashboard doesn't agree everywhere" note; once D5 and
  D6 land, tagging and minimeta drop out of it

### D6 — `!remove_sheet` becomes organiser on both edges

Deleting a working sheet is a Rename & delete operation, not an annotation — and in practice
organisers are the only ones who would want to. `!add_sheet` stays open (Create).

Edit sites:
- `bot/cogs/puzzles.py:1392` — add `@is_organiser()` to `remove_sheet` (alias
  `!delete_sheet`)
- `bot/web/actions.py:348` — `require_organiser` on
  `DELETE /puzzles/<id>/sheets/<sheet_id>`
- `bot/web/actions.py:428` — same on `DELETE /hunts/<uid>/sheets/<sheet_id>`
- `docs/commands.md:147` — the sheets table has no *Who* column; add the role note there or
  in the section intro above it
- `docs/commands.md` § Permissions at a glance — move `!remove_sheet` from Anyone/Annotate to
  Organisers/Rename & delete

## Rulings that change nothing (recorded so they aren't re-litigated)

- **`!move_to_top` / `!move_to_bottom` stay organiser.** Not a trust judgement — regular
  users have no reason to reach for them, so the gate costs nothing. Do not "level" these
  down on the grounds that they are less structural than `!feeds_only`.
- **`!erase_note` stays open.** It belongs to Annotate, and the whole area is open.
- **`!remove_from` stays open.** Tag area, and deliberately so: it lets solvers tidy up a
  meta's membership when no organiser is available.

## Constraints

- Apply as one batch, not piecemeal.
- Docs move with the code: `docs/commands.md`'s Who column currently matches the decorators
  for all 47 rows it documents — do not let this batch be what breaks that.
- Do not touch `!puzzle`/`!p` or `POST /hunts/<uid>/puzzles`.

## Acceptance criteria

1. Every decision above is applied on both edges where the operation exists on both.
2. `docs/commands.md`'s Who column still matches the decorators for every documented row,
   AND the § Permissions at a glance table matches too — it was added ahead of the batch
   reflecting the CURRENT tiers, so the batch must move `!round`, `!absorb_category` and
   `!remove_sheet` between its rows and strip the six `†` markers.
3. Gate changes carry a test that fails if the decorator is removed.
4. The full suite passes (`timeout 300 pipenv run python -m pytest tests/ -q`).

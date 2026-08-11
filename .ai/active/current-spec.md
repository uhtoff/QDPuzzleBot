# Current Spec

## Goal

Bring command/route permission tiers into line after the 2026-08-11 gating audit.

**Collecting decisions now; applying them in one batch later.** No code changes yet — this
file is the record. Each decision below lists the exact edit sites so the batch is
mechanical.

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

## Open — audit findings with no decision yet

- **Tagging.** `!feeds`, `!feeds_only`, `!informs`, `!informs_only`, `!groups_with`,
  `!groups_only_with`, `!remove_from` are open to anyone; the web equivalent
  `POST /puzzles/<id>/meta` requires organiser. Same operation, opposite answers, and it is
  the most-used operation in a live hunt. Needs a direction: open the route, or gate the
  commands.
- **Minimeta clusters.** `!make_minimeta` and `!add_mini` are open; `POST make-minimeta` and
  `POST minis` are organiser. Removal is organiser on both.
- **Destructive but open.** `!remove_sheet` and `!erase_note` delete things while every
  `!delete_*` is organiser. Consistent across both edges, so a question about the rule
  rather than drift.

## Constraints

- Apply as one batch, not piecemeal.
- Docs move with the code: `docs/commands.md`'s Who column currently matches the decorators
  for all 47 rows it documents — do not let this batch be what breaks that.
- Do not touch `!puzzle`/`!p` or `POST /hunts/<uid>/puzzles`.

## Acceptance criteria

1. Every decision above is applied on both edges where the operation exists on both.
2. `docs/commands.md`'s Who column still matches the decorators for every documented row.
3. Gate changes carry a test that fails if the decorator is removed.
4. The full suite passes (`timeout 300 pipenv run python -m pytest tests/ -q`).

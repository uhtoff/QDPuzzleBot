# Current Spec

## Goal

Two related improvements to the puzzbot command surface:

1. **Group-code resolver** — make the `meta_code` lookup used by `feeds` / `add_to_meta` /
   `informs` / `groups_with` / `remove_from` (and the web API) forgiving and consistent.
2. **Hybrid conversions** — promote the everyday solver bang-commands to
   `@commands.hybrid_command` so they also work as slash commands.

## In scope

- New pure helper `resolve_group(raw, groups) -> GroupMatch` in
  `bot/store/tag_group_data.py` (exact code → exact name → unique substring; ambiguous /
  none / empty outcomes). No DB import; unit-tested.
- Cog helper `resolve_target_group(ctx, meta_code)` in `bot/cogs/puzzles.py` that scopes to
  the current hunt, calls `resolve_group`, and on miss sends an actionable message
  (disambiguation list / suggestions) instead of the generic `:x: Please send a valid group code`.
- Swap the 7 bang call sites (feeds, feeds_only, remove_from, informs, informs_only,
  groups_with, groups_only_with) onto the helper.
- Apply the same resolver to the web API call sites in `bot/web/actions.py`.
- Stage 2 (separate commit): convert selected solver bang-commands to hybrid
  (solve/unsolve/partial/note/clue/type/link/rename/round + create commands as agreed).

## Out of scope

- Fuzzy/edit-distance matching (explicitly declined — exact + name fallback only).
- Converting owner/destructive/debug commands to hybrid.

## Relevant files

- `bot/store/tag_group_data.py` — resolver + GroupMatch (new code).
- `bot/cogs/puzzles.py` — `group_code_autocomplete` (196), the 7 tag call sites (~1162+),
  solver commands for hybrid conversion.
- `bot/web/actions.py` — group_code lookups (~826, ~1452).
- `tests/test_round_data.py` — style template for new pure test file.

## Required behaviour

- Input normalized (`strip`, case-insensitive). Hunt-scoped.
- Exact code wins; else exact name; else unique substring; multiple → disambiguation;
  none → suggestions. Behaviour identical across bang + web.

## Constraints

- Test cmd: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`.
- Lint: `pipenv run ruff check`.
- Small reviewable commits per stage. Don't touch unrelated code.

## Acceptance criteria

- `resolve_group` unit tests pass (pure, no DB).
- Existing suite still green.
- Bang commands accept a group name or code; ambiguity/miss give helpful replies.

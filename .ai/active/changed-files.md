# Changed Files

## Git status

```
 M bot/cogs/puzzles.py
?? docs/reviews/2026-07-02-staging-full-review.md
```

## Files changed

- `bot/cogs/puzzles.py` — tranche 1 fixes for findings #9, #10, #11, #13, #14, #16, #18,
  #19, #21, #23 from docs/reviews/2026-07-02-staging-full-review.md.

## Diff summary

+72/-21 lines in bot/cogs/puzzles.py:
- `check_available_space`: None-guard category/bot_channel; skip current-category checks
  when the command creates its own new category (required_categories > 0).
- `puzzle()` (!p): guard `hunt is None`.
- `list_tags()`: guard dangling tag-group id, guard empty embed field values.
- `clue()`/`clues()`: guard `puzzle is None`; wrap `shlex.split` in try/except ValueError.
- feeds/feeds_only/informs/informs_only/groups_with/groups_only_with/remove_from: guard
  `puzzle is None` before `resolve_target_group`.
- `_round_impl`: `return None, None` on handled RuntimeError instead of bare `return`.
- `list_puzzles()` (!list): guard `tag_group is not None`.
- `mark_as_complete()` (!complete): added the same not-a-puzzle-channel/already-solved
  guards `!solve` has.
- `archive_solved_manually()`: replaced broken `settings.hunt_settings[hunt_id]` lookup
  with `self.get_hunt(ctx)`; guard hunt/category None; replaced dead
  `channel.history().flatten()` with an async list comprehension.

## Commands run

```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-tranche1.log 2>&1
```

## Tests run

766 passed, 0 failed (full suite).

## Tests not run

None relevant — full suite covers this file.

## Current risks

- `check_available_space` behaviour change (skipping current-category checks for
  !hunt/!round) is a genuine behaviour fix per the finding, but not covered by an existing
  test (no test file references it) — worth eyeballing in a real Discord run if possible,
  though this is a Discord-mocked-only test suite so that's out of scope here.
- Not yet committed.

# Changed Files

## Git status

```
 M bot/cogs/puzzles.py
 M bot/core/notifier.py
 M bot/core/puzzle_service.py
 M bot/web/__init__.py
 M bot/web/actions.py
 M frontend/src/pages/HuntBoard.tsx
 M tests/test_board_push_service.py
 M tests/test_evidence_commands.py
 M tests/test_evidence_service.py
 M tests/test_hunt_settings_service.py
?? docs/reviews/2026-07-02-staging-full-review.md
```

## Files changed

Tranche 3 fixes for findings #63, #65, #66, #67, #68, #85 from
docs/reviews/2026-07-02-staging-full-review.md.

- `bot/core/puzzle_service.py` — `log_clues` and `update_hunt_settings` converted from
  plain sync methods to `async` + `@pushes_board()`.
- `bot/core/notifier.py` — `DiscordNotifier.refresh_info` handles `puzzle=None` as a
  hunt-level pin refresh.
- `bot/cogs/puzzles.py` — `!set_login`/`!set_start` now call `notifier.refresh_info`;
  `!clue`/`!clues` await `log_clues`; `!update_settings` refreshes the pin + pushes the
  board after its commit.
- `bot/web/actions.py` — `patch_hunt_settings` awaits `update_hunt_settings` and calls
  `notifier.refresh_info`; `log_clue` awaits `log_clues`; `set_puzzle_link` calls
  `web_refresh_info` after `set_puzzle_url`.
- `bot/web/__init__.py` — `start_web`'s background task gets a `done_callback` that logs
  (with `exc_info`) if it crashes.
- `frontend/src/pages/HuntBoard.tsx` — the WS board-event handler now also invalidates
  `["evidence", uid]`.
- `tests/test_hunt_settings_service.py`, `tests/test_evidence_service.py`,
  `tests/test_evidence_commands.py` — updated for the two methods going async
  (await calls, AsyncMock instead of MagicMock).
- `tests/test_board_push_service.py` — added `log_clues`/`update_hunt_settings` to the
  `EXPECTED_PUSH_METHODS` allowlist (a guard-rail test; it correctly failed until this was
  updated, confirming the decorator landed).

## Commands run

```
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test-tranche3b.log 2>&1
(cd frontend && npx tsc --noEmit)
pipenv run ruff check bot/cogs/puzzles.py bot/core/notifier.py bot/core/puzzle_service.py \
  bot/web/__init__.py bot/web/actions.py tests/test_evidence_commands.py \
  tests/test_evidence_service.py tests/test_hunt_settings_service.py \
  tests/test_board_push_service.py
```

## Tests run

766 passed, 0 failed (full pytest suite). `tsc --noEmit` clean. `ruff check` clean on all
touched files.

## Tests not run

No live-Discord/live-dashboard manual verification — this is a fully-mocked test suite by
project convention; out of scope here per AGENTS.md.

## Current risks

- #63's fix (`!update_settings` board push + pin refresh) is a narrow behavioural addition
  with no dedicated new test — covered incidentally by the full suite staying green, but
  not directly asserted. Low risk: it mirrors an existing pattern used elsewhere.
- Not yet committed.

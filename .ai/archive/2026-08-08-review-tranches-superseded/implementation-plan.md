# Implementation Plan — clear the remaining review items

Source: `docs/reviews/2026-08-07-comprehensive-review.md`. Everything the review still
lists as open, minus two exclusions.

## Excluded — need the user's sign-off, do NOT start

- **The naming items** (`round_id` et al.). The review holds them as evidence-only pending
  approval; a rename must be its own commit and its own decision.
- **The dead-column DB tidy** (write-only/unread columns awaiting a keep/drop call).

## Tranche A — Ops ✅ DONE (`a07b170`)

O2 (RotatingFileHandler + SIGTERM/SIGINT → `bot.close()`), O3 (`config.json.dist` keys),
O5 (`run.py` debug path guarded, hardcoded IP → `debug_host`/`debug_port`).

## Tranche B — Dead code removal ✅ DONE (`319d9bc`, `52f29ad`)

Done: the cog methods (incl. `update_metapuzzle`/`_update_all_metas` and their
test file), `is_fed`/`is_informed`, `delete_category_with_channels`,
`get_lowest_code_in_hunt`, `check_duplicate_group_code`, the `urls.py` helpers +
their two orphaned regexes, and `Config.store()`. `_install_ratelimit_probe` was
already gone.

`52f29ad` finished it: the orphaned scripts (user approved the deletions), the legacy
ABCs, `HuntSettings`, `dir_path`/`DATA_DIR`/`LADDER_SPOT_DATA_DIR` and the write-only
config keys. **`DB_DSN` was deliberately kept** despite the review listing it — it is a
live env override for `self.database` and removing it would silently change behaviour for
any deployment that sets it.

### Original list

The review's verified-callerless list (AST scan run twice, plus dynamic-dispatch,
decorator and re-export checks). Re-verify each before deleting — the scan is from
2026-08-07 and code has landed since.

- `_install_ratelimit_probe()` (`bot/__init__.py`) — highest value; monkey-patches every
  HTTP request the bot makes, self-labelled TEMPORARY, and the mystery it chased was
  resolved 2026-06-23 (`dc4805e`).
- Dead cog methods: `web_refresh_meta`, `_web_send_welcome`, the `clean_channel_name`
  wrapper, `get_solved_channel`, `update_metapuzzle`/`_update_all_metas` (test-only).
- Dead service/store: `is_fed`/`is_informed`, `delete_category_with_channels`,
  `get_lowest_code_in_hunt` + `check_duplicate_group_code`,
  `MySQLChannelActivityDb.get_for_guild`, `get_guild_activity`, the legacy JSON-storage
  ABCs, `HuntSettings`, `dir_path`/`DATA_DIR`/`LADDER_SPOT_DATA_DIR`.
- Dead utils/config: `docs_url`/`drive_folder_url`/`extract_id_from_url`, `_chunk`,
  commented-out `ping`, `Config.store()` (**hazardous** — would truncate config.json to 3
  keys), config keys `owner_email`/`puzzle_addons_path`/`puzzle_cell_link`/`DB_DSN`.
- Broken scripts: `dbtest.py` (ImportError on run), `backfill_hunt_uids.py`
  (AttributeError), `memes.sh` (runs the bot as root from a hardcoded path),
  `run-bot.sh`/`process.id`. **Deleting files is user-visible — flag before doing it.**

Note the ABCs are named as test patch targets in places; check before removing, and see
the F401 rule in `docs/agents/python.md`.

## Tranche C — DB

- **T4.** `bot/store/__init__.py` opens three `MySQLReconnector`s at import. MySQL down at
  boot → import raises after ~15s and the supervisor restart-loops a recovering DB; also
  forces every script and test to have a live DB. Fix: lazy-connect on first `cursor()`
  (the `_ensure` machinery already supports it). Biggest win in this tranche — it also
  removes the test-import DB constraint.
- **T5.** N+1 hydration: `construct_puzzle` runs 4 queries/row and the list paths loop it,
  so a 100-puzzle hunt is ~400 sequential round-trips on the gateway thread. The dashboard
  path already shows the batched-join alternative.
- **UNIQUE constraints** on `hunts.uid` and `tag_group.group_code` — uniqueness is
  check-then-insert, so a bang+web race can mint a duplicate and make one hunt
  unreachable. Needs a migration; **check both tables for existing duplicates first**, as
  038 did, or the constraint fails to apply.

## Tranche D — Web hardening ⚠️ PARTLY DONE (`100d827`)

Done: the unguarded `int()` casts (now `_client_int` → 400) and the unbounded
`_not_member_cache` (capped at 10k, expired-first eviction, 3 tests).

**Still to do in D:** the WebSocket Origin check, binding the CSRF token to the session,
the ~9 sites echoing internal exception strings to clients, and rate limiting the
member-level channel/sheet/disk-creating routes.

### Original list

- No Origin check on the activity WebSocket (Quart's CSRF guard doesn't cover websockets).
- CSRF token unbound to the session, domain-fixable from a sibling host.
- Unguarded `int()` casts on client JSON → 500 instead of 400 (`api.py:558`,
  `actions.py:2399,2799`).
- Internal exception strings echoed to clients (~9 sites).
- No rate limit on member-level channel/sheet/disk-creating routes.
- Unbounded `_not_member_cache`.

## Tranche E — Frontend

- Activity WebSocket reconnects forever on a 1008 auth-close (no `event.code` check, no
  backoff).
- Mid-session 401 dead-ends in a raw "auth_required" toast — `["me"]` is never invalidated.
- Markup **undo after erase** pops an already-deleted id → 404 toast, undo consumed; the
  eraser never prunes `mine` (`MarkupCanvas.tsx:242`).
- Impure `setPan`-inside-`setZoom` updater double-applies under StrictMode.
- `@tanstack/react-table` is an unused dependency.
- ✅ DONE (`454a57d`) — `ChatViewer` embed URLs now go through `lib/safeHref.ts`
  (scheme allowlist via `URL()`, so obfuscated `java\tscript:` fails too) and carry
  `rel="noopener noreferrer"`. 7 vitest cases.
- Keyboard reachability: colour swatches, click-to-edit cells, sortable headers are
  pointer-only.

Remember: no component/route test infrastructure exists, so this tranche is
`tsc --noEmit` + inspection only. Say so in the report.

## Tranche F — Docs & misc ⚠️ PARTLY DONE (`a560f1b`)

Done: the status vocabulary (`STATUSES` gained `"unsolved"` because `unsolve` writes it;
`"Needs Submission"` added to the slash choices), `info_bot` (title + the DM `shard_id`
crash), and all the docs drift.

**Still to do in F:** the missing audit on markup update/delete, and the
group-chat-channel orphan exposure found during M6.

### Original list

- Docs drift: `!absorb_category` missing from `docs/commands.md`; README's non-existent
  pytest `--compact`/`--parallel` flags; README clone URL points at the wrong remote
  (`uhtoff/QDPuzzleBot` is correct); the Manage-Channels requirement on
  `backfill_glyphs`/`extract`/`consolidate`; the stale `$LADDER_SPOT_DATA_DIR` claim.
  Also: README says `!update_setting`, the command is `update_settings`.
- Status vocabulary: `"needs submission"` in `STATUSES` but missing from the slash choices,
  docstring and docs; `unsolve` writes `"Unsolved"`, which is not in `STATUSES`.
- No audit on markup update/delete (only `create_mark` audits) — any member can silently
  erase another's ink with no trail.
- `info_bot` embed still titled "LadderSpot"; `ctx.guild.shard_id` crashes in DMs.
- The **group-chat-channel orphan exposure** found during M6: a target-less round's chat
  channel is reported as an orphan by `!reconcile`, and `apply-orphans` would delete it.
  Not in the review; file and fix it here.

## Tranche G — Tests (TT4)

Zero-coverage high-risk modules: `migrate.py`, `bot/web/data.py:get_hunt_puzzles` (the main
dashboard read model), `sheet_tables.py`, OAuth `login`/`callback` state validation, and the
`bulk_update_edge_type` SQL (asserted only as a mocked call).

## A trap worth knowing about

Deleting code by "cut from marker A to marker B" removed **2350 lines** of
`bot/cogs/puzzles.py` when the two names turned out not to be adjacent. It was caught by
ruff (a flood of newly-unused imports) and reverted with no harm, but only because the
tree was clean at the time. Cut blocks by **indentation scan** from the `def` line, or by
matching the block verbatim — and check `git diff --stat` line counts against what you
expected before running the gates.

## Verification (every tranche)

```
timeout 400 pipenv run python -m pytest tests/ -q
timeout 60  pipenv run ruff check
timeout 300 pipenv run basedpyright bot/
cd frontend && npm run build          # tranche E
```

Commit per tranche. Mutation-check anything load-bearing.

## Rollback / recovery notes

Each tranche is its own commit on `working-2`, so a single `git revert` undoes one without
touching the others. Migrations are additive; the UNIQUE-constraint one in tranche C is the
only irreversible-ish step and must run its duplicate check first.

# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/message-bridge
Started: 2026-07-06
Status: ready for handoff — feat(bridge): capture and render Discord embeds (solve/partial messages) @ bfb2389

## Remaining before/at merge
- Live end-to-end smoke deferred (needs running bot + DB + archive_media_dir; bridge_enabled=true).
- Apply migration 027 to target DB before enabling.
- Rebase onto updated staging (after user's bug fixes); likely conflicts: api.py, data.py, mysqldb.py, migration 027 number.
- Deferred minor follow-ups: reply-quote rendering (reference_id captured but not shown); send_file filename for non-image downloads; viewer opens own WS vs sharing useActivityFeed.
- DO NOT merge to staging without user confirmation.

## Goal

Discord → web message bridge: forward-only mirror of hunt puzzle-channel messages
into the web DB, rendered in an in-dashboard chat viewer, so hunt archiving yields a
self-contained web transcript for free (the dead Discord badge on archived hunts
becomes a working chat window).

Design: `docs/superpowers/specs/2026-07-06-discord-web-message-bridge-design.md`
Plan:   `docs/superpowers/plans/2026-07-06-discord-web-message-bridge.md` (authoritative; TDD, phases A–D)

## Execution method

Subagent-driven development: fresh implementer per plan task + spec review + code-quality
review. Branch `feat/message-bridge` based off local `staging` HEAD (c2bae4a).
**Do NOT merge to staging without the user's explicit confirmation** (user instruction).
User may land this AFTER their in-flight bug fixes — expect to rebase onto updated staging
at integration time (likely conflict points: bot/web/api.py, bot/web/data.py,
bot/store/mysqldb.py, migration numbering 027).

## Current status (commits on branch, oldest→newest)

Phase A (DONE, reviewed, green):
- 683aee5 migration 027 (messages, message_attachments)
- c384719 config (web_bridge_enabled off by default, archive_media_dir, attachment_max_bytes)
- ac1b241 MessageDb DAO
- 3940d49 message_media helpers (safe_filename, capped downloader)
- 5d87246 bridge cog (capture, puzzle-channel gate, worker) + 94940af/4f35fc5 ruff cleanups
- f19667e fix: listeners inert when bridge disabled (worker None guard) — from code review

Phase B (in progress):
- 3b4d85e read models (get_channel_messages, get_hunt_by_channel_id, get_attachment_with_channel, discord_deep_url)
- 36dcdaf fix: transcript timestamps serialized UTC via _iso() — from code review
- de232cd API routes (/api/channels/<id>/messages, /api/attachments/<id>) + _channel_messages_visible
- a38ae2e ruff cleanup on routes test
- B2 currently under combined spec+quality review (auth/path-traversal focus).

## Next steps (remaining plan tasks)

- B3 frontend types + api client (types.ts ChatMessage/ChatAttachment, api.ts fetchChannelMessages, Puzzle.discord_deep_url)
- B4 useChannelMessages hook (transcript + live tail)
- B5 ChatViewer modal
- B6 PuzzleLinks badge opens viewer (needs huntArchived prop threaded from call sites)
- C1 publish live message/edit/delete events from worker (bridge cog) + tests
- C2 WS dispatch for message events in api.py ws_activity
- D1 regression guard: channel deletion (archive) does not purge messages
- D2 full verification pass (suite + basedpyright + ruff + frontend tsc/build)
- Then finishing-a-development-branch — STOP before merging; ask user.

## Test/verify commands (this worktree)

- `.venv` is a symlink to the main checkout's venv; `pipenv run ...` works here.
- Backend: `timeout 300 pipenv run python -m pytest tests/ -q` (baseline was 1054; now ~1073).
- Types: `pipenv run basedpyright bot/` (must be 0 errors).
- Lint: `pipenv run ruff check` (must be clean). NOTE: my plan's verbatim tests
  over-import; several ruff F401 cleanups were needed on new test files.
- Frontend: `cd frontend && npx tsc --noEmit` (and `npm run build` at D2).
- config.json + google_secrets.json were copied from the main checkout so conftest imports.

## Known deviations / notes

- Injected harness "new-diagnostics" are consistently STALE (report new files/attributes as
  unresolved even when basedpyright + tests pass). Trust actual `pipenv run basedpyright` runs.
- Migration NOT applied to a live DB (no creds in sandbox); apply 027 before live smoke.
- Message ids are serialized as STRINGS (data.py snowflake convention; matches frontend string ids).
- Plan had a few self-inconsistencies implementers correctly reconciled: safe_filename regex
  (didn't strip ..), add_attachment download_status bind param, "6 vs 5" cog test count,
  discord_deep_url lives in get_hunt_puzzles not build_board.

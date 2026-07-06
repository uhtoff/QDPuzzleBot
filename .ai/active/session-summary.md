# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/message-bridge
Started: 2026-07-06
Status: ready for handoff — refactor(bridge): share one activity WebSocket across board + chat viewer @ dd3baa6

## Capture-behavior follow-ups (from live testing) — DONE this session
Design: docs/superpowers/specs/2026-07-06-bridge-capture-followups-design.md
- bfb2389 #3 embed capture: migration 028 (`embeds` TEXT col), normalize+store embeds,
  serve in transcript + live payload, ChatViewer embed block, hook edit-merge fix.
- c0479e2 #1 lazy channel tracking: per-channel DB check + negative cache; no ~60s delay.
- 537b344 #2 welcome exclusion: skip first bot embed-only message in an empty channel.
## Rendering-polish follow-ups (#4-6) — DONE this session
- #5 non-image downloads: as_attachment=True so Content-Disposition carries the original
  filename (images still inline). + route tests. (api.py)
- #4 reply-quote render: quoted line (author + snippet) above a reply, or "replying to an
  earlier message" when the referenced msg is outside the loaded window. (ChatViewer)
- #6 shared WS: activityStream.ts ref-counted reconnecting socket; both useActivityFeed and
  useChannelMessages subscribe to it (was two sockets; chat tail now reconnects too).

Verified (final, all #1-6): 1101 pytest pass; ruff clean; frontend tsc + build pass.
basedpyright has one PRE-EXISTING error (puzzle_service.py:1584, unrelated, at 4b1dba5).

## Remaining before/at merge
- Live end-to-end smoke deferred (needs running bot + DB + archive_media_dir; bridge_enabled=true).
- Apply migrations 027 AND 028 to target DB before enabling.
- Rebase onto updated staging (after user's bug fixes); likely conflicts: api.py, data.py, mysqldb.py, migration 027/028 numbers.
- All planned follow-ups (#1-6) now landed; only live smoke + migration apply remain.
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

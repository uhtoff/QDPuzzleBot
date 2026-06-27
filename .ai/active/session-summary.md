# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-06-27
Status: complete — all phases delivered (tests + Group + forum config + auto-apply tags)

## Goal

Make `Group` the canonical general-channel group type (no target puzzle; general text
channel + welcome), configure forum channels on creation (rules/tags/👍 emoji, auto-applied
and synced), and move all welcome posting into the service. Full design in
`docs/superpowers/specs/2026-06-27-group-channel-and-forum-config-design.md`; the ordered,
test-first execution plan is `.ai/active/current-spec.md` (authoritative).

## Context the executing agent needs (not obvious from files)

- **Classification model:** `get_channel_type` (`bot/store/mysqldb.py:623`) is a SQL UNION;
  a TagGroup is recognized by its `category_id`. Forum-mode rounds already store a *channel*
  id (the forum) in `category_id`, so storing a Group's text-channel id there is consistent.
  `cog_before_invoke` tries `get_channel_type(channel.id)` BEFORE the category fallback —
  that's why a forum-mode group channel classifies by its own id.
- **Latent bug being fixed:** `create_tag_group` generates `group_code` *after*
  `create_puzzle` posts the meta welcome, so today's bang meta welcome shows a blank
  group_code. Phase 2 reorders this.
- **Forum vs text channel:** Groups never become forums (even in forum hunts). Forum config
  + auto-tags (Phases 3–4) apply ONLY to round/meta/capstone/runaround forums, guarded by
  `hunt.layout_mode == "forum"`.
- **Tag precedence (confirmed):** runaround → metameta → meta → minimeta → capstone, plus
  `solved`. `meta` deliberately outranks `minimeta`.
- **Already landed** (do not redo): `912d8e7`, `de6359b`, `4586d3b` (forum classification
  fix, web welcome thread-resolve, web Round-suffix/Runaround parity).

## What NOT to touch

- DB schema / migrations / enum values (no migration needed).
- Membership/move semantics, thread reparenting (Discord can't; copy-delete rejected).
- `web_*` legacy twins; permission-overwrite code (set manually by an admin).
- conftest/pytest.ini infrastructure.

## Test harness facts

- Run: `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1` (single
  statement). `asyncio_mode = auto`. Factories in `tests/conftest.py` (`make_hunt`,
  `make_round`→`TagGroupData`, `make_puzzle`, `make_cog`) — import, not fixtures. Patch DAOs
  at `bot.core.puzzle_service.*`. Mirror `test_forum_layout.py` (service),
  `test_edge_web.py` (web Quart), `test_minimeta_service.py` (cog_before_invoke).

## If you get stuck

Diagnose with `superpowers:systematic-debugging` FIRST. A test failing because a symbol
doesn't exist yet is correct (Phase 1). A runtime/import fatal or wrong kwarg/attr means a
wrong assumption in the TEST — fix the test, don't add scaffolding. Two-strikes: if an
acceptance command fails twice without a clear fix, write `.ai/active/blocker.md` (what you
tried, exact output, hypothesis) and STOP.

## Next steps

1. Phase 1 (Opus 4.8): write all failing tests per `current-spec.md`, confirm right-reason
   failures, commit.
2. Phases 2–4 (Sonnet 4.6): implement Group / forum config / auto-tags, one commit each,
   full suite + ruff green per phase. Bump the Status line above as each lands.

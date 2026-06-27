# Current Spec — Forum-backed hunts (option)

Tool: Claude Code (handoff author)
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model — Phase 1 (tests): **Opus**.  Phases 2–6 (implementation): **Sonnet**.
Started: 2026-06-27
Status: ready for handoff — Phase 1 (tests)

---

## Before you start (executing model)

- **Execute only the current phase.** Do not read ahead and implement later phases early.
- On any **unexpected** failure (not a clean "this assertion fails because the branch isn't built
  yet"), invoke **`superpowers:systematic-debugging`** before changing anything.
- Before claiming a phase done, invoke **`superpowers:verification-before-completion`** and paste
  the acceptance command output into your report. Evidence before assertions.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix,
  write `.ai/active/blocker.md` (what you ran, output, your diagnosis) and **STOP**.
- Commit at each phase boundary with a clear message. Run the focused test, not the whole suite,
  during iteration; run the full suite at phase end.
- Test runner (from `AGENTS.md`), single statement, wrapped in `timeout`, output to a file:
  ```bash
  timeout 180 pipenv run python -m pytest tests/test_forum_layout.py -q > /tmp/test.log 2>&1
  ```
  Full suite at phase end: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
  (a killed run exits 124 — read the log; needs a reachable `puzzlebot_test` DB at import).

---

## Goal

Add **forum-backed hunts** as a per-hunt *option*, selected by a new `HuntData.layout_mode`
(`'channel'` default | `'forum'`). In forum mode: Hunt = one Category, Round (TagGroup) = a
`ForumChannel` under that category, Puzzle = a Thread/forum post. Channel mode is **unchanged**.

## In scope (v1 — channel parity)

- `HuntData.layout_mode` field + migration **015**.
- A `create_forum_post()` helper in `bot/core/discord_channels.py`.
- Forum-mode branches in exactly three service functions: `create_tag_group`, `create_puzzle`,
  `delete_round`.
- A defensive audit of thread-unsafe accesses (notifier lookups; bang-command `ctx.channel.category`).

## Out of scope (do NOT do these)

- **Forum tags** for puzzle status (solved/unsolved as Discord tags). Phase-2 follow-on, not now.
- A `category_layout` strategy-interface refactor. v1 uses explicit `if layout_mode == "forum"`
  branches and leaves `category_layout.py` **untouched** (forum mode simply never calls it).
- Migrating existing channel-backed hunts. Forum mode is opt-in for new hunts only.
- Buttons/modals on posts. Separate track.
- **solve()/unsolve()** changes — the solve/unsolve presentation edges (`web_post_solved` :697,
  `web_post_unsolved` :712 in `bot/cogs/puzzles.py`) **already** early-return on `discord.Thread`,
  so forum puzzles need no change there. Do not touch them.

## Relevant files

- `bot/store/hunt_data.py` — `HuntData` dataclass (add field). **(write)**
- `migrations/` — add `015_hunt_layout_mode.sql`; next free number is 015 (last is
  `014_tag_group_category.sql`). ⚠️ Confirm 015 is still free (parallel worktrees collide). **(write)**
- `bot/core/discord_channels.py` — add `create_forum_post()`. **(write)**
- `bot/core/puzzle_service.py` — branches in `create_tag_group` (~1457), `create_puzzle` (~1365),
  `delete_round` (~993). **(write)**
- `bot/cogs/puzzles.py` — defensive audit only (Phase 6). **(read, then narrow writes)**
- `bot/core/notifier.py` — defensive audit only (Phase 6): `get_channel` → `get_channel_or_thread`
  at lines ~140, ~242. **(read, then narrow writes)**
- `tests/conftest.py` — `make_hunt`, `make_round`, `make_puzzle` factories. **(read only)**
- `tests/test_create_service.py` — canonical service-test style + **the channel-mode regression
  guard** (do not duplicate it). **(read only)**
- `AGENTS.md` "Test harness" section — the mocking/patching rules. **(read only)**

## Constraints

- Do not touch files outside the list. Do not refactor `category_layout.py` or the solve edges.
- Do not change channel-mode behaviour, enum backing values, or existing migration history.
- **migration → deploy → code:** the `015` migration must exist before the field is relied on in a
  live run; the field default keeps it backward-safe. (Unit tests don't hit the column — they
  patch DAOs — so the test suite passes without applying the migration; apply it via
  `pipenv run python migrate.py migrate` before any manual smoke.)
- Reuse the existing `category_id`/`channel_id` snowflake columns to store the forum/thread IDs —
  **no new ID columns.** TagGroup's `category_id` holds the forum channel id in forum mode;
  Puzzle's `channel_id` holds the thread id.
- `create_tag_group`, `create_puzzle`, `delete_round` all already receive `hunt` — branch on
  `hunt.layout_mode`. No signature changes. (Grep `new PuzzleService(` is N/A — it's
  container-resolved; no hand-built call sites to update.)

## Test constraints (inlined — you do not read global instructions)

- **Reuse exact helpers/seams; invent none.** Use `make_hunt/make_round/make_puzzle` from
  `tests.conftest`; construct the service exactly as `tests/test_create_service.py` does
  (`PuzzleService(bot)` with `bot.get_cog.return_value = gsheet`). Define **uniquely-named**
  local helpers in the new file (the parallel gate shards by file; a cross-file helper becomes an
  "undefined function" fatal). The new file must import/define everything it calls.
- **Patch DAOs at the service import site**, e.g. `patch("bot.core.puzzle_service.TagGroupJsonDb")`
  — never `bot.store.*`. Patch `bot.core.puzzle_service.get_or_create_channel`,
  `...category_layout.pick_unsolved_category`, and (new) `...create_forum_post` the same way.
- **Confirm every field exists before use** — `layout_mode` (new), `category_id`, `channel_id`,
  `target_puzzle_id` are real; do not invent others.
- `MagicMock()` for sync, `AsyncMock()` for awaitables. Discord awaitables to mock here:
  `guild.create_forum`, `category.clone`, `channel.edit`, `forum.delete`. Set `.id`/`.name`/
  `.position` explicitly (a bare `MagicMock().name` is a Mock, not a str).
- **Simulate INSERT id:** `rdb.commit.side_effect = lambda r: setattr(r, "id", 5)`.
- **NEVER** write a loop that resolves state "until" a condition; **NEVER** modify test
  infrastructure or stub randomness. If you think you need to, write `blocker.md` and STOP.
- **Wrap every run in `timeout`, output to a file, single statement** (no `; echo` tail).

---

## Phase 1 — write ALL failing tests (Opus)

Create `tests/test_forum_layout.py` with the four tests below (near-verbatim — fill in faithfully).
Commit with no production code. Then confirm they fail for the **right** reason (field/branch
absent), not a bootstrap/undefined-name fatal.

```python
"""Forum-backed hunt option: per-hunt layout_mode + forum create/delete branches.

Mirrors tests/test_create_service.py wiring (service calls the *module* functions
bot.core.puzzle_service.get_or_create_channel / .create_forum_post and the relocated
PuzzleJsonDb / TagGroupJsonDb). Helpers are uniquely named + local to this file.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round


def _forum_svc():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return PuzzleService(bot)


def _forum_hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"
    h.url = None
    h.category_id = 1          # the single hunt category (forum parent)
    h.name = "H"
    h.layout_mode = "forum"    # field added in Phase 2; setattr works pre-field
    return h


def test_hunt_data_defaults_layout_mode_channel():
    # Right-reason failure pre-Phase-2: AttributeError (field does not exist yet).
    assert HuntData().layout_mode == "channel"


@pytest.mark.asyncio
async def test_create_tag_group_forum_creates_forum_under_hunt_category():
    svc = _forum_svc()
    hunt_category = MagicMock(id=1, position=0)
    hunt_category.name = "H"
    hunt_category.clone = AsyncMock(return_value=MagicMock(id=2, channels=[], edit=AsyncMock()))
    guild = MagicMock()
    guild.categories = [hunt_category]
    guild.channels = []
    guild.create_forum = AsyncMock(return_value=MagicMock(id=777))
    notifier = MagicMock(); notifier.welcome = AsyncMock()

    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=333), True))):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "G1"
        # meta_puzzle_name=False → Metaless round, so create_puzzle is not invoked.
        new_round, puzzle = await svc.create_tag_group(
            guild, _forum_hunt(), "New Round", notifier=notifier, meta_puzzle_name=False,
        )

    guild.create_forum.assert_awaited_once()        # forum branch taken
    hunt_category.clone.assert_not_awaited()         # channel-mode clone NOT used
    assert new_round.category_id == 777              # forum id stored in category_id


@pytest.mark.asyncio
async def test_create_puzzle_forum_creates_post_not_text_channel():
    svc = _forum_svc()
    forum = MagicMock(id=777)
    guild = MagicMock()
    guild.channels = []
    guild.categories = []
    guild.get_channel = MagicMock(return_value=forum)   # resolves the round's forum
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=777)        # category_id == forum id

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.create_forum_post",
               AsyncMock(return_value=MagicMock(id=222, mention="<#222>", name="newpuz"))) as mkpost, \
         patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
               AsyncMock()) as pick, \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()) as goc:
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        result = await svc.create_puzzle(
            guild, _forum_hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier,
        )

    mkpost.assert_awaited_once()          # forum post created
    pick.assert_not_awaited()             # category overflow layout NOT used
    goc.assert_not_awaited()              # text-channel creation NOT used
    assert result.channel_id == 222       # thread id stored in channel_id


@pytest.mark.asyncio
async def test_delete_round_forum_deletes_forum_channel():
    svc = _forum_svc()
    forum = MagicMock(id=777); forum.delete = AsyncMock()
    guild = MagicMock()
    guild.categories = []                                  # forum is NOT a category
    guild.get_channel = MagicMock(return_value=forum)
    tag_group = make_round(id=5, category_id=777)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.category_layout.delete_group_categories",
               AsyncMock(return_value=False)):
        pdb.get_all_from_tag_group.return_value = []       # no puzzles
        await svc.delete_round(guild, _forum_hunt(), tag_group)

    forum.delete.assert_awaited_once()                     # forum torn down
```

**Phase 1 acceptance:**
1. `timeout 180 pipenv run python -m pytest tests/test_forum_layout.py -q > /tmp/test.log 2>&1`
   exits **non-zero**, with failures only from:
   - `test_hunt_data_defaults_layout_mode_channel` → `AttributeError` (no `layout_mode` field), and
   - the three async tests → their `create_forum` / `create_forum_post` / `forum.delete` assertions
     (branch not built). These are the **right** reasons. A `NameError`/import error/"undefined
     function"/bootstrap fatal is the **wrong** reason — fix the test, do not add scaffolding.
2. The file runs under the same runner as the final gate without collection/bootstrap fatal
   (it imports cleanly even though assertions fail).
3. Commit: `git commit -m "test(forum): failing tests for forum-backed hunt option"`.

---

## Phase 2 — `layout_mode` field + migration (Sonnet)

1. `bot/store/hunt_data.py`: add `layout_mode: str = "channel"` to `HuntData` (place it with the
   other scalar fields; the generic `commit()` writes it by name).
2. Add `migrations/015_hunt_layout_mode.sql`:
   `ALTER TABLE hunts ADD COLUMN layout_mode VARCHAR(20) NOT NULL DEFAULT 'channel';`
   (Match the style of `migrations/014_tag_group_category.sql`.)
**Acceptance:** `pytest tests/test_forum_layout.py::test_hunt_data_defaults_layout_mode_channel -q`
passes; full suite green. Commit.

## Phase 3 — `create_forum_post` helper + `create_tag_group` forum branch (Sonnet)

1. In `bot/core/discord_channels.py` add
   `async def create_forum_post(forum, name, content="​") -> discord.Thread:` that calls
   `forum.create_thread(name=name, content=content)` and returns the resulting **Thread**. In
   discord.py 2.7.1 `ForumChannel.create_thread` returns a `ThreadWithMessage`; **confirm against
   the installed lib** and return its `.thread` (fall back to the value itself if it's already a
   `Thread`). Import it into `puzzle_service` (`from bot.core.discord_channels import ...,
   create_forum_post`) so the Phase-1 patch target `bot.core.puzzle_service.create_forum_post`
   resolves.
2. In `create_tag_group` (~:1485), before the `ref_category.clone(...)` block, branch:
   `if hunt.layout_mode == "forum":` resolve
   `hunt_cat = discord.utils.get(guild.categories, id=hunt.category_id)`, create
   `forum = await guild.create_forum(name=new_category_name, category=hunt_cat)`, **skip** the
   clone/divider, and commit `TagGroupData(..., category_id=forum.id, type=round_type)`. The
   `else` keeps today's clone path verbatim. The meta-puzzle creation + rollback tail stay shared.
**Acceptance:** `pytest tests/test_forum_layout.py::test_create_tag_group_forum_creates_forum_under_hunt_category -q`
passes; full suite green. Commit.

## Phase 4 — `create_puzzle` forum branch (Sonnet)

In `create_puzzle` (~:1375), when `round_data is not None and hunt.layout_mode == "forum"`:
resolve `forum = guild.get_channel(round_data.category_id)`, set `category = None`, skip the
`category_layout.pick_unsolved_category` call and the guild-channel ceiling guard (threads don't
count toward the 500 cap). At the channel-creation site (~:1418), when in forum mode call
`thread = await create_forum_post(forum, channel_name)` instead of `get_or_create_channel(...)`,
then set `new_puzzle.channel_id = thread.id`, `channel_mention = thread.mention`,
`channel_name = thread.name`, skip the `solve_divider_position` reposition, and proceed to the
shared commit + `notifier.welcome` + meta-refresh tail unchanged. Keep the orphan-cleanup `except`.
**Acceptance:** `pytest tests/test_forum_layout.py::test_create_puzzle_forum_creates_post_not_text_channel -q`
passes; full suite green. Commit.

## Phase 5 — `delete_round` forum branch (Sonnet)

In `delete_round` (~:1015), wrap the category-teardown block: `if hunt.layout_mode == "forum":`
delete the forum channel — `forum = guild.get_channel(tag_group.category_id); if forum is not None:
await forum.delete(reason=self._DISCORD_DELETE_REASON)` (deleting the forum cascades its posts;
the per-puzzle DB deletes above already ran). The `else` keeps the existing
`delete_group_categories` + category teardown verbatim. Mirror the same branch in `delete_hunt`
(~:1791) for the hunt's tag-group categories if it tears them down the same way — **read it first**
and only branch what actually deletes categories.
**Acceptance:** `pytest tests/test_forum_layout.py::test_delete_round_forum_deletes_forum_channel -q`
passes; full suite green. Commit.

## Phase 6 — defensive audit + manual smoke (Sonnet)

No new automated test is required up front; this is verification + narrow fixes.
1. `bot/core/notifier.py` ~:140, ~:242: change `guild.get_channel(puzzle.channel_id)` to
   `guild.get_channel_or_thread(puzzle.channel_id)` (forum puzzles are threads).
2. Audit `bot/cogs/puzzles.py` `ctx.channel.category` accesses (~:147, 153, 168, 204, 367, 377,
   392). **Confirm against discord.py 2.7.1** whether `Thread.category` is safe (it may proxy the
   parent's category). Only add a `isinstance(ctx.channel, discord.Thread)` guard where a real
   break/wrong-behaviour is confirmed; if an access is already safe, leave it and note why. If you
   confirm a break, add a focused test for it before fixing.
3. Apply migration locally (`pipenv run python migrate.py migrate`), then manually smoke a
   `layout_mode='forum'` hunt: create round → create puzzle → solve → unsolve → delete round, and
   confirm the dashboard board reflects it.
**Acceptance:** full suite green; manual smoke notes pasted into your report. Commit.

## Verification
- Per phase: the named filter run passes + full suite green (wrapped in `timeout`, output to file).
- Confirm `category_layout` split/overflow is never invoked for a forum hunt (the Phase-1
  `pick.assert_not_awaited()` guards puzzle creation; eyeball create_tag_group/delete_round).

## Rollback / recovery notes
- `layout_mode` defaults to `'channel'`; with no forum hunts, all new branches are dormant.
- Migration 015 is additive/defaulted — safe to ship ahead of the feature.
- Each phase is an independent revertable commit; channel mode is never modified.

## Open questions (non-blocking — sensible defaults chosen)
- `ForumChannel.create_thread` return type in discord.py 2.7.1 (`ThreadWithMessage` vs `Thread`) —
  Phase 3 confirms against the installed lib; the helper isolates it.
- Forum-mode solve *presentation* (archive solved post / apply a tag) is deferred to phase-2
  forum-tags work; v1 leaves solved posts in place (edges already no-op the category move).
- Mini-puzzles in a forum hunt become messages-in-post (the `solve_mini` master-channel wiring
  exists); not exercised in v1 tests — note for manual smoke if minis are used.

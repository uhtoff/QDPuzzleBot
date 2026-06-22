# M3 — `bot/core/` Service Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse each hand-synced `web_*` / bang-command pair in `bot/cogs/puzzles.py` into one canonical ctx-free method on a new `bot/core/PuzzleService`, delete the twins, and centralise the duplicated role/status constants — with no observable behaviour change on either surface.

**Architecture:** New `bot/core/` package holds `constants.py` (single source for `ADMIN_ROLES`/`ORGANISER_ROLES`/`STATUSES`/`PRIORITIES`) and `puzzle_service.py` (`PuzzleService`, constructed with the `bot` reference). Each operation becomes one core method that does DB + Google Sheets + intrinsic Discord mutations and **raises** on failure; the bang `_impl` and the web action are thin edges that keep their own presentation (embeds) and error-tolerance policy (bang propagates, web swallows best-effort). Migrate one path per commit, strangler-style, with M1's bang-vs-web equivalence suite green between each.

**Tech Stack:** Python 3, discord.py 2.7.1, pytest (`asyncio_mode = auto`), MySQL store layer (`bot/store`), Google Sheets cog. Tests use `unittest.mock` + `asyncio`.

**Design spec:** `docs/superpowers/specs/2026-06-22-m3-service-extraction-design.md`

---

## File Structure

**Created:**
- `bot/core/__init__.py` — empty package marker.
- `bot/core/constants.py` — canonical `ADMIN_ROLES`, `ORGANISER_ROLES`, `STATUSES`, `PRIORITIES`.
- `bot/core/puzzle_service.py` — `class PuzzleService`, holds `self.bot`; ctx-free operations.
- `tests/test_core_constants.py` — guards single-source constants.
- `tests/test_puzzle_service.py` — service-level equivalents of the M1 equivalence assertions.

**Modified:**
- `bot/cogs/puzzles.py` — import constants from `bot/core`; instantiate `self.service` in `__init__`; per migrated path, fold the bang `_impl` onto the service and delete the `web_*` twin.
- `bot/web/auth.py:22-24` — import `ADMIN_ROLES`/`ORGANISER_ROLES` instead of redefining.
- `bot/web/actions.py:29-31` — derive `PUZZLE_STATUSES`/`PUZZLE_PRIORITIES` from imported `STATUSES`/`PRIORITIES`; per migrated path, call `cog.service.<op>` instead of `cog.web_<op>`.
- `tests/test_solve_equivalence.py` — re-point at `cog.service.*` as twins are deleted.

**Test command (use after every step that changes code):**
```bash
timeout 120 python -m pytest tests/ -q > /tmp/m3-test.log 2>&1
```
Read `/tmp/m3-test.log` for results (exit 124 = timeout/hang).

---

## Task 1: Scaffold — `bot/core/` package, constants, empty service

**Files:**
- Create: `bot/core/__init__.py`
- Create: `bot/core/constants.py`
- Create: `bot/core/puzzle_service.py`
- Create: `tests/test_core_constants.py`
- Modify: `bot/cogs/puzzles.py:26-27` (ADMIN/ORGANISER), `:80-81` (STATUSES/PRIORITIES), `:99-107` (`__init__`)
- Modify: `bot/web/auth.py:22-24`
- Modify: `bot/web/actions.py:29-31`

- [ ] **Step 1: Write the failing test for the constants single-source**

Create `tests/test_core_constants.py`:
```python
"""M3: the role/status/priority constants live in exactly one place."""
import bot.core.constants as core
import bot.cogs.puzzles as puzzles_mod
import bot.web.auth as auth_mod
import bot.web.actions as actions_mod


def test_core_defines_canonical_values():
    assert core.ADMIN_ROLES == ('Moderator', 'mod', 'admin')
    assert core.ORGANISER_ROLES == core.ADMIN_ROLES + ('Organisers',)
    assert core.STATUSES == ["unstarted", "in progress", "stuck",
                             "needs extraction", "needs submission",
                             "solved", "backsolved"]
    assert core.PRIORITIES == ["low", "medium", "high", "very high"]


def test_puzzles_cog_uses_core_constants():
    # Same object identity → not a redefined copy that can drift.
    assert puzzles_mod.ADMIN_ROLES is core.ADMIN_ROLES
    assert puzzles_mod.ORGANISER_ROLES is core.ORGANISER_ROLES
    assert puzzles_mod.Puzzles.STATUSES is core.STATUSES
    assert puzzles_mod.Puzzles.PRIORITIES is core.PRIORITIES


def test_auth_uses_core_roles():
    assert auth_mod.ADMIN_ROLES is core.ADMIN_ROLES
    assert auth_mod.ORGANISER_ROLES is core.ORGANISER_ROLES


def test_actions_statuses_match_core():
    # actions uses sets for membership; content must match core exactly.
    assert actions_mod.PUZZLE_STATUSES == set(core.STATUSES)
    assert actions_mod.PUZZLE_PRIORITIES == set(core.PRIORITIES)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `timeout 120 python -m pytest tests/test_core_constants.py -q > /tmp/m3-test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.core'`.

- [ ] **Step 3: Create the package and constants module**

Create `bot/core/__init__.py` (empty file).

Create `bot/core/constants.py`:
```python
"""Canonical authorization-role / puzzle-status / priority constants.

Single source of truth shared by the Discord cog (bot/cogs/puzzles.py) and the
web layer (bot/web/auth.py, bot/web/actions.py). Previously each side defined its
own copy, which had already drifted in type (list vs set). STATUSES / PRIORITIES
are ordered lists (UI enumeration needs order); the web side wraps them in set()
locally for membership checks.
"""

ADMIN_ROLES = ('Moderator', 'mod', 'admin')
ORGANISER_ROLES = ADMIN_ROLES + ('Organisers',)

STATUSES = ["unstarted", "in progress", "stuck", "needs extraction",
            "needs submission", "solved", "backsolved"]
PRIORITIES = ["low", "medium", "high", "very high"]
```

- [ ] **Step 4: Create the empty `PuzzleService`**

Create `bot/core/puzzle_service.py`:
```python
"""Ctx-free service layer for puzzle operations (M3).

The canonical implementation of each puzzle operation lives here. The Discord
bang-commands and the web dashboard are thin edges that call into this service;
the service does DB + Google Sheets + intrinsic Discord mutations and RAISES on
failure. Presentation (embeds, confirmations) and error-tolerance policy stay at
the edges.
"""
import logging

logger = logging.getLogger(__name__)


class PuzzleService:
    def __init__(self, bot):
        self.bot = bot
```

- [ ] **Step 5: Rewire `bot/cogs/puzzles.py` to import the constants**

Replace lines 26-27:
```python
ADMIN_ROLES = ('Moderator', 'mod', 'admin')
ORGANISER_ROLES = ADMIN_ROLES + ('Organisers',)
```
with:
```python
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES, STATUSES, PRIORITIES
```
(Place the import with the other `from bot...` imports near the top if the linter prefers; functionally either location works since it is module-level.)

Replace the class-body lines 80-81:
```python
    STATUSES = ["unstarted", "in progress", "stuck", "needs extraction", "needs submission","solved", "backsolved"]
    PRIORITIES = ["low", "medium", "high", "very high"]
```
with:
```python
    STATUSES = STATUSES
    PRIORITIES = PRIORITIES
```

- [ ] **Step 6: Instantiate the service in the cog `__init__`**

In `Puzzles.__init__` (after `self.bot = bot`, around line 100) add:
```python
        from bot.core.puzzle_service import PuzzleService
        self.service = PuzzleService(self.bot)
```
(Local import avoids any import-cycle risk at module load.)

- [ ] **Step 7: Rewire `bot/web/auth.py`**

Replace lines 22-24:
```python
# Mirror bot/cogs/puzzles.py role gating for write-actions.
ADMIN_ROLES = ("Moderator", "mod", "admin")
ORGANISER_ROLES = ADMIN_ROLES + ("Organisers",)
```
with:
```python
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES
```

- [ ] **Step 8: Rewire `bot/web/actions.py`**

Replace lines 29-31:
```python
# Mirror bot/cogs/puzzles.py STATUSES / PRIORITIES.
PUZZLE_STATUSES = {"unstarted", "in progress", "stuck", "needs extraction", "needs submission", "solved", "backsolved"}
PUZZLE_PRIORITIES = {"low", "medium", "high", "very high"}
```
with:
```python
from bot.core.constants import STATUSES, PRIORITIES

PUZZLE_STATUSES = set(STATUSES)
PUZZLE_PRIORITIES = set(PRIORITIES)
```
(Put the `from bot.core...` line with the other imports at the top of the file.)

- [ ] **Step 9: Run the full suite — everything green, no behaviour change**

Run: `timeout 120 python -m pytest tests/ -q > /tmp/m3-test.log 2>&1`
Expected: PASS, including the new `test_core_constants.py`.

- [ ] **Step 10: Byte-compile check**

Run: `python -m py_compile bot/core/constants.py bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/auth.py bot/web/actions.py`
Expected: no output (success).

- [ ] **Step 11: Commit**

```bash
git add bot/core/ tests/test_core_constants.py bot/cogs/puzzles.py bot/web/auth.py bot/web/actions.py
git commit -m "M3 scaffold: bot/core package + centralise role/status constants

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Migrate the retag path (move/add/remove from meta)

The shared core is the **meta-refresh** (currently duplicated between `update_metapuzzle` and `web_refresh_meta`) plus the **tag mutation**. Per the locked decision: `PuzzleService.refresh_meta` **raises**; the tag-mutation methods do channel-move + tags + commit and return the list of affected round ids; each edge runs the refresh loop with its own error policy (bang propagates via the existing `update_metapuzzle`-style call; web swallows).

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `refresh_meta`, `move_to_meta`, `add_to_meta`, `remove_from_meta`)
- Modify: `bot/cogs/puzzles.py` — delete twins `web_move_to_meta` (630-649), `web_add_to_meta` (651-657), `web_remove_from_meta` (659-668), and `web_refresh_meta` (606-628); point bang `move_to_meta`/`add_to_meta`/`remove_from_meta` and `update_metapuzzle` at the service
- Modify: `bot/web/actions.py` — retag dispatch (~330-335) calls `cog.service.*`
- Modify: `tests/test_solve_equivalence.py` — re-point `TestWebAddToMeta` / `TestWebRemoveFromMeta` / `TestWebMoveToMeta` at `cog.service.*`

- [ ] **Step 1: Write failing service tests (re-point the existing equivalence cases)**

In `tests/test_solve_equivalence.py`, change the three retag helper bodies to call the service. In `TestWebAddToMeta._run`, replace `await cog.web_add_to_meta(puzzle, round_data)` with:
```python
            await cog.service.add_to_meta(puzzle, round_data)
```
In `TestWebRemoveFromMeta._run`, replace `await cog.web_remove_from_meta(puzzle, round_data)` with:
```python
            await cog.service.remove_from_meta(puzzle, round_data)
```
In `TestWebMoveToMeta._run`, replace `await cog.web_move_to_meta(None, puzzle, new_round)` with:
```python
            await cog.service.move_to_meta(None, puzzle, new_round)
```
The mocks patch `bot.cogs.puzzles.PuzzleJsonDb` etc.; add the same patches for the service module — see Step 3's note on patch targets. For now also patch the service module symbols by adding to each `with` block:
```python
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
```
(Reuse the same Mock objects so the existing `commits` assertions still observe them.)

- [ ] **Step 2: Run to verify they fail**

Run: `timeout 120 python -m pytest tests/test_solve_equivalence.py -q > /tmp/m3-test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'add_to_meta'`.

- [ ] **Step 3: Implement the retag methods on the service**

Add to `bot/core/puzzle_service.py` (imports at top of file):
```python
import datetime

import discord

from bot.store import PuzzleJsonDb, RoundJsonDb, HuntJsonDb
```
And the methods on `PuzzleService`:
```python
    def _gsheet_cog(self, hunt):
        """Resolve the GoogleSheets cog and point it at this hunt's spreadsheets.
        Returns None if the cog isn't loaded (e.g. in tests)."""
        cog = self.bot.get_cog("GoogleSheets")
        if cog is not None and hunt is not None:
            cog.set_spreadsheet_id(hunt.google_sheet_id)
            cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
        return cog

    async def refresh_meta(self, hunt, hunt_round):
        """Rebuild a meta (or meta-meta) roll-up sheet. RAISES on a Sheets error;
        the caller decides whether to swallow it."""
        if hunt is None or hunt_round is None or not hunt_round.meta_id:
            return
        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is None:
            return
        metapuzzle = PuzzleJsonDb.get_by_attr(id=hunt_round.meta_id)
        if metapuzzle is None:
            return
        if metapuzzle.metameta:
            all_puzzles = PuzzleJsonDb.get_all_from_hunt(hunt_round.hunt_id)
            await gsheet_cog.add_metametapuzzle_data(metapuzzle, all_puzzles)
        else:
            round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
            await gsheet_cog.add_metapuzzle_data(metapuzzle, round_puzzles)

    async def move_to_meta(self, guild, puzzle, meta_round):
        """Retag a puzzle to meta_round (replacing its tags unless it is itself a
        metapuzzle), move its channel into the meta's category, commit, and refresh
        the affected metas. Sheet refresh errors propagate (web edge swallows)."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        old_tags = list(puzzle.tags)
        if guild is not None and puzzle.channel_id:
            channel = guild.get_channel(puzzle.channel_id)
            new_category = discord.utils.get(guild.categories, id=meta_round.category_id)
            if channel is not None and new_category is not None and channel.category_id != new_category.id:
                await channel.edit(category=new_category, position=2)
        if puzzle.is_metapuzzle() is False:
            puzzle.tags.clear()
        puzzle.tags.append(meta_round.id)
        PuzzleJsonDb.commit(puzzle)
        await self.refresh_meta(hunt, meta_round)
        for old_tag in old_tags:
            await self.refresh_meta(hunt, RoundJsonDb.get_by_attr(id=old_tag))

    async def add_to_meta(self, puzzle, meta_round):
        """Tag a puzzle to an additional meta (idempotent), commit, refresh."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        if meta_round.id not in puzzle.tags:
            puzzle.tags.append(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        await self.refresh_meta(hunt, meta_round)

    async def remove_from_meta(self, puzzle, meta_round):
        """Untag a puzzle from a meta (idempotent), commit, refresh affected metas."""
        hunt = HuntJsonDb.get_by_attr(id=puzzle.hunt_id)
        old_tags = list(puzzle.tags)
        if meta_round.id in puzzle.tags:
            puzzle.tags.remove(meta_round.id)
            PuzzleJsonDb.commit(puzzle)
        await self.refresh_meta(hunt, meta_round)
        for old_tag in old_tags:
            await self.refresh_meta(hunt, RoundJsonDb.get_by_attr(id=old_tag))
```
Note: `refresh_meta` no longer swallows (the `try/except` that was in `web_refresh_meta` is gone). The **web edge** re-adds tolerance (Step 6). The bang path already tolerated nothing here (`update_metapuzzle` propagated), so this matches it.

- [ ] **Step 4: Run the service tests — green**

Run: `timeout 120 python -m pytest tests/test_solve_equivalence.py -q > /tmp/m3-test.log 2>&1`
Expected: PASS (the three retag classes now exercise the service).

- [ ] **Step 5: Point the bang-commands at the service and delete the retag twins**

In `bot/cogs/puzzles.py`, rewrite `update_metapuzzle` (592-599) to delegate:
```python
    async def update_metapuzzle(self, ctx, hunt_round):
        hunt = self.get_hunt(ctx)
        await self.service.refresh_meta(hunt, hunt_round)
```
(Behaviour preserved: `refresh_meta` handles the `meta_id`/`metameta` branching and raises just as the old inline code did; the spreadsheet id is now set by the service from `hunt` rather than `cog_before_invoke`, which set the same values.)

In `add_to_meta` (1234-1248) replace the body's mutation+refresh:
```python
            await self.service.add_to_meta(puzzle, meta_round)
            await ctx.send(
                f":white_check_mark: This puzzle has been tagged to {meta_round.name} and meta sheets are being updated.")
```
(Remove the now-redundant `puzzle.tags.append` / `PuzzleJsonDb.commit` / `update_metapuzzle` lines.)

In `move_to_meta` (1257-1281): keep the `get_channel_type` guard and the `meta_round` lookup; replace the channel-edit + tag + commit + refresh block with:
```python
            await ctx.send(
                f":white_check_mark: This puzzle has been moved to {meta_round.name} and meta sheets are being updated.")
            await self.service.move_to_meta(self.get_guild(ctx), puzzle, meta_round)
```
(The service performs the channel move and the refresh loop; the user-facing message is posted by the edge.)

In `remove_from_meta` (1290-1316): keep the guards (`get_channel_type`, invalid meta code, "can't remove a metapuzzle from its own round"); replace the tag + commit + refresh block with:
```python
            await ctx.send(
                f":white_check_mark: This puzzle has been removed from {meta_round.name} and meta sheets are being updated.")
            await self.service.remove_from_meta(puzzle, meta_round)
```

Delete the three retag twins entirely: `web_move_to_meta` (630-649), `web_add_to_meta` (651-657), `web_remove_from_meta` (659-668), and the "ctx-free helpers" comment block (601-604).

**Do NOT delete `web_refresh_meta` yet** — `web_solve_puzzle` (still present until Task 3) calls it at line 896. Instead convert it to a swallowing wrapper over the service so the duplication is gone now but the best-effort behaviour `web_solve_puzzle` relies on is preserved:
```python
    async def web_refresh_meta(self, hunt, hunt_round):
        """Best-effort wrapper over PuzzleService.refresh_meta, retained until
        web_solve_puzzle is migrated (Task 3). Swallows Sheets errors."""
        try:
            await self.service.refresh_meta(hunt, hunt_round)
        except Exception:
            logger.exception("web_refresh_meta: sheet update failed for round %s",
                             getattr(hunt_round, "id", None))
```

- [ ] **Step 6: Point the web retag dispatch at the service (keep best-effort)**

In `bot/web/actions.py`, the retag handler (~288-338) currently calls `cog.web_move_to_meta` / `cog.web_add_to_meta` / `cog.web_remove_from_meta`. Replace with the service equivalents, wrapping the call so a Sheets hiccup does not fail the action (preserving today's swallow):
```python
        try:
            if mode == "move":
                await cog.service.move_to_meta(guild, puzzle, meta_round)
            elif mode == "add":
                await cog.service.add_to_meta(puzzle, meta_round)
            elif mode == "remove":
                await cog.service.remove_from_meta(puzzle, meta_round)
        except Exception:
            logger.exception("retag: service call failed for puzzle %s", puzzle.id)
```
(If `actions.py` already wraps this dispatch in a try/except returning an error JSON, keep that outer handling; the inner swallow specifically preserves the old `web_refresh_meta` behaviour of not failing the retag on a sheet error. Confirm `logger` is defined in `actions.py`; if not, add `logger = logging.getLogger(__name__)`.)

- [ ] **Step 7: Run the full suite**

Run: `timeout 120 python -m pytest tests/ -q > /tmp/m3-test.log 2>&1`
Expected: PASS.

- [ ] **Step 8: Byte-compile check**

Run: `python -m py_compile bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/actions.py`
Expected: no output.

- [ ] **Step 9: Grep for stale references to the deleted twins**

Run: `grep -rn "web_move_to_meta\|web_add_to_meta\|web_remove_from_meta" bot/ tests/`
Expected: no matches (the three retag twins migrated). `web_refresh_meta` still exists as the swallowing wrapper — deleted in Task 3.

- [ ] **Step 10: Commit**

```bash
git add bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/actions.py tests/test_solve_equivalence.py
git commit -m "M3 retag: extract refresh_meta + move/add/remove_from_meta to PuzzleService

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Migrate the solve path

The highest-divergence path. Core does: status/solution/solve_time mutation, the meta-refresh + round solve_time loop, sheet archive, channel reposition — and **raises**. The empty-solution guard from `_solve_impl` becomes the canonical behaviour (the equivalence test at `test_empty_solution_clears_when_none_existing` is updated to expect the stricter guard). Each edge keeps its own embed text/location and error policy.

**Files:**
- Modify: `bot/core/puzzle_service.py` (add `solve`)
- Modify: `bot/cogs/puzzles.py` — `_solve_impl` (2237-2283) delegates to `self.service.solve`, keeps its ctx embed/confirmation; delete `web_solve_puzzle` (877-937)
- Modify: `bot/web/actions.py` — `/solve` (239-271) calls `cog.service.solve` inside its best-effort wrapper
- Modify: `tests/test_solve_equivalence.py` — `TestWebSolvePuzzleState` calls `cog.service.solve`; update the documented divergence test

- [ ] **Step 1: Re-point the solve equivalence tests at the service**

In `tests/test_solve_equivalence.py`, in `TestWebSolvePuzzleState._run_solve`, replace `await cog.web_solve_puzzle(_no_guild(), hunt, puzzle, solution)` with:
```python
            await cog.service.solve(_no_guild(), hunt, puzzle, solution)
```
Add the service-module patch targets to the `with` block (reusing the same mocks):
```python
             patch("bot.core.puzzle_service.PuzzleJsonDb", mock_pdb), \
             patch("bot.core.puzzle_service.RoundJsonDb", mock_rdb), \
             patch("bot.core.puzzle_service.HuntJsonDb", mock_hdb), \
             patch("bot.core.puzzle_service.GuildSettingsDb", mock_gsdb), \
```
Update `test_empty_solution_clears_when_none_existing` to assert the **canonical stricter guard** (the service adopts `_solve_impl`'s early return, so no mutation happens):
```python
    @pytest.mark.asyncio
    async def test_empty_solution_no_existing_is_rejected(self, cog, hunt):
        # Canonical M3 behaviour: empty solution with no existing solution is a
        # no-op (adopts _solve_impl's guard); the puzzle is NOT marked solved.
        puz = make_puzzle(solution=None, solved=False)
        await self._run_solve(cog, hunt, puz, "")
        assert puz.solution is None
        assert puz.solved is False
```

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 python -m pytest tests/test_solve_equivalence.py::TestWebSolvePuzzleState -q > /tmp/m3-test.log 2>&1`
Expected: FAIL — `AttributeError: 'PuzzleService' object has no attribute 'solve'`.

- [ ] **Step 3: Implement `PuzzleService.solve`**

Add to `bot/core/puzzle_service.py` (extend imports: `import pytz`, and add `GuildSettingsDb`, `SheetsJsonDb` to the `bot.store` import):
```python
    async def solve(self, guild, hunt, puzzle, solution):
        """Mark a puzzle solved: set status/solution/solve_time, refresh metas,
        stamp the round solve_time if this is the round's meta, archive the sheet,
        and reposition the channel to the solved section. RAISES on Sheets/Discord
        failure; edges decide tolerance and presentation.

        Returns True if the solve was applied, False if rejected (empty solution
        with no existing solution — matches _solve_impl's guard)."""
        if solution == "" and puzzle.solution in [None, ""]:
            return False

        puzzle.status = "Solved"
        if puzzle.solution and solution:
            puzzle.solution += "/" + solution
        elif solution:
            puzzle.solution = solution
        puzzle.solved = True
        puzzle.solve_time = datetime.datetime.now(tz=pytz.UTC)
        PuzzleJsonDb.commit(puzzle)

        for tag in puzzle.tags:
            meta_round = RoundJsonDb.get_by_attr(id=tag)
            await self.refresh_meta(hunt, meta_round)
            if meta_round and meta_round.meta_id == puzzle.id:
                meta_round.solve_time = datetime.datetime.now(tz=pytz.UTC)
                RoundJsonDb.commit(meta_round)

        gsheet_cog = self._gsheet_cog(hunt)
        if gsheet_cog is not None:
            await gsheet_cog.archive_puzzle_spreadsheet(puzzle)
            puzzle.archive_time = datetime.datetime.now(tz=pytz.UTC)

        PuzzleJsonDb.commit(puzzle)
        for sheet in puzzle.additional_sheets:
            SheetsJsonDb.commit(sheet)
        return True
```
Note: the **channel reposition** and the **solved embed** are intentionally NOT in core — they are presentation/placement that differs per edge (bang uses `move_to_solved(ctx)` + posts to ctx; web posts to the channel + repositions inline best-effort). They stay at the edges (Steps 5-6). Core archive **raises** (matches `_solve_impl`); the web edge swallows.

- [ ] **Step 4: Run the solve service tests — green**

Run: `timeout 120 python -m pytest tests/test_solve_equivalence.py::TestWebSolvePuzzleState -q > /tmp/m3-test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Fold `_solve_impl` onto the service (bang edge)**

In `bot/cogs/puzzles.py`, rewrite `_solve_impl` (2237-2283) to call the service and keep its ctx presentation:
```python
    async def _solve_impl(self, ctx, *args):
        solution = self._normalize_solution(args)
        puzzle = self.get_puzzle(ctx)
        hunt = self.get_hunt(ctx)

        applied = await self.service.solve(self.get_guild(ctx), hunt, puzzle, solution)
        if not applied:
            await ctx.send(":x: Nice try, but you need to give a solution!")
            return

        emoji = self.get_guild_data(ctx).discord_bot_emoji
        embed = discord.Embed(title="PUZZLE SOLVED!",
                              description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`")
        embed.add_field(
            name="Follow-up",
            value="If the solution was entered incorrectly, please use `!update_solution` to update it, if the puzzle isn't actually solved at all then use `!unsolve`.  \nGive me a sec to tidy up the sheets."
        )
        await ctx.send(embed=embed)
        await self.move_to_solved(ctx)
        await self.info(ctx, update=True)
        await ctx.send(":white_check_mark: Sheets all tidied away.")
```
(The service already archived the sheet and committed; `move_to_solved`, `info`, and both `ctx.send` calls are the bang-specific presentation, preserved verbatim. The archive error still propagates to the bang user exactly as before, because the service raises and this code does not catch it.)

- [ ] **Step 6: Point the web `/solve` at the service and delete `web_solve_puzzle`**

In `bot/web/actions.py`, the `/solve` endpoint (239-271) calls `cog.web_solve_puzzle(guild, hunt_obj, puzzle, solution)`. Replace the side-effecting part so the DB solve is recorded and the Discord/Sheets work stays best-effort:
```python
        try:
            await cog.service.solve(guild, hunt_obj, puzzle, solution)
        except Exception:
            logger.exception("web solve: service raised for puzzle %s", puzzle.id)
        # Best-effort Discord presentation (a hiccup must not lose the solve):
        await cog.web_post_solved_embed(guild, hunt_obj, puzzle)
```
Then add a small ctx-free presentation helper to `bot/cogs/puzzles.py` (the channel embed + reposition + info refresh that used to live in `web_solve_puzzle`, lines 901-937 minus the DB/sheet work now in the service):
```python
    async def web_post_solved_embed(self, guild, hunt, puzzle):
        """Best-effort dashboard-side presentation after service.solve: post the
        SOLVED embed in the puzzle channel, reposition it to the solved section,
        and refresh the pinned info. Never raises."""
        channel = guild.get_channel(puzzle.channel_id) if (guild and puzzle.channel_id) else None
        if channel is not None:
            guild_data = GuildSettingsDb.get(guild.id)
            emoji = guild_data.discord_bot_emoji if guild_data else ""
            embed = discord.Embed(
                title="PUZZLE SOLVED!",
                description=f"{emoji} :partying_face: Great work! Marked the solution as `{puzzle.solution}`",
            )
            embed.add_field(name="Follow-up", value="Solved from the dashboard. Tidying up the sheets…")
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception("web_post_solved_embed: failed to post solved message")
        if channel is not None and channel.category is not None:
            try:
                divider = self.get_solve_divider_position(None, channel.category)
                if divider is not False:
                    async with self.position_lock:
                        await channel.edit(position=divider + 1)
            except Exception:
                logger.exception("web_post_solved_embed: failed to reposition channel")
        await self.web_refresh_info(guild, hunt, puzzle)
```
Delete `web_solve_puzzle` (877-937). Now that its last caller is gone, also delete the `web_refresh_meta` wrapper retained in Task 2 — first `grep -rn "web_refresh_meta" bot/ tests/` to confirm no remaining callers (the bang `update_metapuzzle` already delegates to `service.refresh_meta` directly). (`web_refresh_info` is migrated in Task 6; until then it still exists, so `web_post_solved_embed`'s call to it is safe.)

- [ ] **Step 7: Run the full suite**

Run: `timeout 120 python -m pytest tests/ -q > /tmp/m3-test.log 2>&1`
Expected: PASS.

- [ ] **Step 8: Byte-compile + stale-reference grep**

Run: `python -m py_compile bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/actions.py`
Run: `grep -rn "web_solve_puzzle" bot/ tests/`
Expected: no output from either (compile clean, no stale callers).

- [ ] **Step 9: Commit**

```bash
git add bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/actions.py tests/test_solve_equivalence.py
git commit -m "M3 solve: extract PuzzleService.solve; edges keep presentation + error policy

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Migrate the sheet add/remove path

`web_add_sheet` (961-977) and `web_remove_sheet` (979-985) are already ctx-free and share `_delete_additional_sheet` (939-959). Move all three onto the service; the bang `add_sheet`/`remove_sheet` commands (1089, 1110) delegate.

**Files:** Modify `bot/core/puzzle_service.py`, `bot/cogs/puzzles.py`, `bot/web/actions.py`.

- [ ] **Step 1: Add a failing service test**

Append to `tests/test_puzzle_service.py` (create if absent, mirroring the `_make_cog`/`_make_hunt` helpers from `test_solve_equivalence.py`):
```python
import asyncio
from unittest.mock import patch
import pytest
from tests.conftest import make_puzzle


@pytest.mark.asyncio
async def test_add_sheet_on_solved_raises():
    from bot.cogs.puzzles import Puzzles
    from unittest.mock import MagicMock
    bot = MagicMock(); bot.get_cog.return_value = None
    cog = Puzzles(bot)
    puz = make_puzzle(solved=True)
    with pytest.raises(RuntimeError, match="solved_no_sheets"):
        await cog.service.add_sheet(None, _make_hunt(), puz, "x")
```
(Define `_make_hunt` locally as in `test_solve_equivalence.py`.)

- [ ] **Step 2: Run to verify failure**

Run: `timeout 120 python -m pytest tests/test_puzzle_service.py -q > /tmp/m3-test.log 2>&1`
Expected: FAIL — no attribute `add_sheet`.

- [ ] **Step 3: Move `add_sheet` / `remove_sheet` / `_delete_additional_sheet` to the service**

Copy the bodies of `web_add_sheet`, `web_remove_sheet`, and `_delete_additional_sheet` into `PuzzleService` as `add_sheet`, `remove_sheet`, `_delete_additional_sheet`, replacing `self.bot.get_cog(...)` + `set_spreadsheet_id` with `self._gsheet_cog(hunt)` and `await self.web_refresh_info(...)` with a call the edge will own. Because info-refresh isn't migrated until Task 6, have the service methods **not** call info-refresh; the edges call `web_refresh_info` afterwards (web edge and bang edge both already refresh info). Add `AdditionalSheetData` and `SheetsJsonDb` and `HttpError` to the service imports.

- [ ] **Step 4: Delegate the bang commands + web endpoints; delete twins; run suite**

Point `bot/cogs/puzzles.py` `add_sheet`/`remove_sheet` and `bot/web/actions.py` `/sheets` POST (171-205) + DELETE (208-236) at `cog.service.add_sheet` / `cog.service.remove_sheet`, each followed by its existing info-refresh. Delete `web_add_sheet` (961-977) and `web_remove_sheet` (979-985); move `_delete_additional_sheet` out of the cog if no other cog caller remains (grep first).

Run: `timeout 120 python -m pytest tests/ -q > /tmp/m3-test.log 2>&1` → PASS.
Run: `grep -rn "web_add_sheet\|web_remove_sheet" bot/ tests/` → no matches.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/actions.py tests/test_puzzle_service.py
git commit -m "M3 sheets: extract add_sheet/remove_sheet to PuzzleService

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Migrate the create-puzzle / create-round path

`web_create_puzzle` (736-800) and `web_create_round` (802-870) are the most Discord-heavy twins (category cloning, channel creation, rollback). They have `RuntimeError("<code>")` contracts the web layer maps to JSON. Move both onto the service unchanged in logic; the web endpoints (`/puzzles` 356-389, `/metas` 392-418) call `cog.service.create_puzzle` / `cog.service.create_round`. The bang `_round_impl` (1318) and `create_puzzle` (548) keep their ctx welcome-message behaviour and call the service for the ctx-free creation core where they overlap; if the overlap is not clean, leave the bang path as-is for this task and only relocate the web twins (still removes the duplication that has a divergence risk). Decide per the code at execution time; default to relocating the twins and pointing the web edges at them.

**Files:** Modify `bot/core/puzzle_service.py`, `bot/cogs/puzzles.py`, `bot/web/actions.py`.

- [ ] **Step 1: Add a failing service test** for the `RuntimeError` contract (e.g. creating a round whose name clashes raises `RuntimeError("round_exists")`), mocking the store + a `guild` stub.
- [ ] **Step 2: Run to confirm it fails.**
- [ ] **Step 3: Move `web_create_puzzle` → `service.create_puzzle` and `web_create_round` → `service.create_round`**, swapping `self.bot.get_cog` for `self._gsheet_cog`. Preserve the rollback logic and every `RuntimeError("<code>")` verbatim.
- [ ] **Step 4: Point the web endpoints at the service; delete the twins; run the suite** (`timeout 120 python -m pytest tests/ -q`) → PASS; `grep -rn "web_create_puzzle\|web_create_round" bot/ tests/` → no matches.
- [ ] **Step 5: Commit** with message `M3 create: extract create_puzzle/create_round to PuzzleService`.

---

## Task 6: Migrate the info-refresh path & final sweep

`web_refresh_info` (713-734) and `web_update_field` (872-875) are the last twins. Move both to the service, point the `PATCH /api/puzzles/<id>` handler (124-166) and any caller (`web_post_solved_embed`, the sheet methods) at `cog.service.refresh_info` / `cog.service.update_field`.

- [ ] **Step 1: Failing service test** for `update_field` (sets attr + commits) and `refresh_info` never raising.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Move the two methods to the service**; update the `web_post_solved_embed` helper and the Task 4 sheet edges to call `self.service.refresh_info`.
- [ ] **Step 4: Delete `web_refresh_info`, `web_update_field`; run the full suite; final grep sweep:**
  Run: `grep -rn "def web_" bot/cogs/puzzles.py`
  Expected: no `web_*` operation twins remain (only intentionally-kept edge helpers like `web_post_solved_embed`, if any).
- [ ] **Step 5: Update `docs/PLANS.md`** — mark **M3** ✅ DONE with the merge commit, and update the critical-path line to start at M4.
- [ ] **Step 6: Commit** with message `M3 complete: info-refresh + field extracted; web_* twins gone`.

---

## Self-Review Notes (for the executor)

- **Behaviour-preservation guard:** the M1 `tests/test_solve_equivalence.py` suite is the contract. If a retag/solve assertion goes red, the service diverged from the documented DB-state transition — fix the service, not the test (except the one intentionally-updated empty-solution test in Task 3).
- **One intentional behaviour change** is made and documented: the web solve path now adopts `_solve_impl`'s empty-solution guard (was previously unguarded on the web side). This is the agreed "core is the stricter common contract" outcome; it is the only observable change.
- **Error policy:** core methods raise; web edges wrap in `try/except … logger.exception` to preserve today's best-effort dashboard behaviour; bang edges let errors propagate. Never add a swallow inside the service.
- **Mutex (M4) is out of scope:** do not add `_PUZZLE_LOCKS` to the service. Bang keeps `@with_puzzle_mutex`; web stays unlocked.
- **Spreadsheet-id:** the service sets it from `hunt.google_sheet_id` via `_gsheet_cog`, replacing both `cog_before_invoke`'s set (bang) and the per-twin set (web) — same values, so no behaviour change.
```

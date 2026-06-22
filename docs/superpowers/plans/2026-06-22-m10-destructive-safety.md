# M10 — Destructive-command safety + create-orphan cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate irreversible deletes behind a confirmation, stop half-completed deletes from crashing, and stop failed creates from leaving orphaned DB rows + Google sheets.

**Architecture:** A reusable `discord.ui` confirmation View (new `bot/cogs/ui.py`) gates the three delete commands. A `_safe_delete_channel` helper makes the bulk-delete loops None-safe and rate-limit-tolerant. Orphan cleanup wraps the post-commit channel-create work on both the bang and dashboard create paths, deleting the sheet + DB row on failure. No schema changes.

**Tech Stack:** Python, discord.py 2.7.1 (`discord.ui`), pytest (`asyncio_mode=auto`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-06-22-m10-destructive-safety-design.md`

**Test command (per AGENTS.md):**
- Single file: `timeout 120 pipenv run python -m pytest tests/test_NAME.py -q > /tmp/test.log 2>&1`
- Full suite: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
- `pipenv run python -m pytest` is required (plain `pytest` can't `import bot`). A killed run exits 124 — read `/tmp/test.log`.

---

## File Structure

- **Create** `bot/cogs/ui.py` — `ConfirmView` (reusable confirm/cancel View). One responsibility: a yes/no gate.
- **Modify** `bot/cogs/puzzles.py`:
  - add `_safe_delete_channel` + `_confirm_destructive` helpers
  - `delete_puzzle` (2214), `delete_round` (2241), `delete_hunt` (2277): confirm gate + hardened loops
  - `web_create_puzzle` (721): orphan cleanup
  - `web_create_round` (787, `except` 836-855): extend rollback
  - bang `_puzzle` create site (`create_puzzle_channel` call at 454): orphan cleanup
- **Create** `tests/test_confirm_view.py`, `tests/test_delete_safety.py`, `tests/test_create_orphan_cleanup.py`.

Dependency order: Task 1 (ConfirmView) and Task 2 (safe-delete helper) are independent primitives; Task 3 uses Task 2; Task 4 uses Task 1; Tasks 5–7 (orphan cleanup) are independent of all the above.

---

## Task 1: `ConfirmView` confirm/cancel/timeout/author-check

**Files:**
- Create: `bot/cogs/ui.py`
- Test: `tests/test_confirm_view.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_confirm_view.py
"""Unit tests for the reusable destructive-action confirmation View (M10)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.cogs.ui import ConfirmView


def _interaction(user_id):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_interaction_check_blocks_non_author():
    view = ConfirmView(author_id=42)
    interaction = _interaction(user_id=99)
    allowed = await view.interaction_check(interaction)
    assert allowed is False
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_interaction_check_allows_author():
    view = ConfirmView(author_id=42)
    assert await view.interaction_check(_interaction(user_id=42)) is True


@pytest.mark.asyncio
async def test_confirm_sets_true_and_stops():
    view = ConfirmView(author_id=42)
    interaction = _interaction(user_id=42)
    await view.confirm.callback(interaction)
    assert view.value is True
    assert all(child.disabled for child in view.children)
    assert view.is_finished()
    interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_sets_false_and_stops():
    view = ConfirmView(author_id=42)
    interaction = _interaction(user_id=42)
    await view.cancel.callback(interaction)
    assert view.value is False
    assert view.is_finished()


@pytest.mark.asyncio
async def test_timeout_leaves_value_none():
    view = ConfirmView(author_id=42)
    view.message = SimpleNamespace(edit=AsyncMock())
    await view.on_timeout()
    assert view.value is None
    view.message.edit.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_confirm_view.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.cogs.ui'`.

- [ ] **Step 3: Write `bot/cogs/ui.py`**

```python
"""Reusable discord.ui components for the Puzzles cog (M10)."""
import discord


class ConfirmView(discord.ui.View):
    """A Confirm/Cancel gate for irreversible actions.

    `value` is None until resolved: True on Confirm, False on Cancel, and stays
    None on timeout. Only the invoking user (author_id) may click; others get an
    ephemeral notice. Set `view.message` to the prompt message so the buttons can
    be disabled on timeout.
    """

    def __init__(self, author_id, *, timeout=30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value = None
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    def _disable_all(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Confirm delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button=None):
        self.value = True
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button=None):
        self.value = False
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self._disable_all()
        if self.message is not None:
            await self.message.edit(view=self)
```

Note: discord.py passes `(self, interaction, button)` to button callbacks; the tests call `view.confirm.callback(interaction)` (one positional arg), so `button=None` keeps both call styles working.

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_confirm_view.py -q > /tmp/test.log 2>&1`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/ui.py tests/test_confirm_view.py
git commit -m "M10: reusable ConfirmView for destructive-action confirmation"
```

---

## Task 2: `_safe_delete_channel` — None-guarded, rate-limit-tolerant channel delete

**Files:**
- Modify: `bot/cogs/puzzles.py` (add helper near the other delete methods, ~2273)
- Test: `tests/test_delete_safety.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_delete_safety.py
"""Delete-path hardening tests (M10): None-guard + rate-limit tolerance."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.puzzles import Puzzles


def _cog():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return Puzzles(bot)


def _guild_with_channel(channel_id, channel):
    guild = MagicMock()
    guild.channels = [channel] if channel is not None else []
    return guild


@pytest.mark.asyncio
async def test_safe_delete_missing_channel_returns_false_no_raise():
    cog = _cog()
    guild = _guild_with_channel(5, None)  # channel already gone
    ok = await cog._safe_delete_channel(guild, 5)
    assert ok is False


@pytest.mark.asyncio
async def test_safe_delete_existing_channel_calls_delete():
    cog = _cog()
    channel = SimpleNamespace(id=5, delete=AsyncMock())
    guild = _guild_with_channel(5, channel)
    ok = await cog._safe_delete_channel(guild, 5)
    assert ok is True
    channel.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_delete_swallows_ratelimited():
    cog = _cog()
    channel = SimpleNamespace(
        id=5,
        delete=AsyncMock(side_effect=discord.RateLimited(retry_after=5.0)),
    )
    guild = _guild_with_channel(5, channel)
    ok = await cog._safe_delete_channel(guild, 5)
    assert ok is False  # reported as failure, did not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_safety.py -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: 'Puzzles' object has no attribute '_safe_delete_channel'`.

- [ ] **Step 3: Add the helper to `bot/cogs/puzzles.py`** (place just after `delete_round_data`, ~line 2273)

```python
    async def _safe_delete_channel(self, guild, channel_id):
        """Delete a channel by id, tolerating an already-missing channel and a
        mid-loop rate-limit. Returns True on delete, False if it was missing or
        the delete could not complete (so callers can report what remains)."""
        channel = discord.utils.get(guild.channels, id=channel_id)
        if channel is None:
            return False
        try:
            await channel.delete(reason=self.DELETE_REASON)
            return True
        except (discord.RateLimited, discord.HTTPException):
            logger.exception("delete: could not delete channel %s", channel_id)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_safety.py -q > /tmp/test.log 2>&1`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_delete_safety.py
git commit -m "M10: add _safe_delete_channel (None-guard + rate-limit tolerant)"
```

---

## Task 3: Harden `delete_round` / `delete_hunt` / `delete_puzzle` loops

**Files:**
- Modify: `bot/cogs/puzzles.py` — `delete_round` (2241-2269), `delete_hunt` (2277-2303), `delete_puzzle` (2214-2230)
- Test: `tests/test_delete_safety.py` (append)

Rewire the bulk loops to use `_safe_delete_channel`, collect failures, and report them instead of crashing. Replace `delete_puzzle`'s bare `except`.

- [ ] **Step 1: Append the failing test**

```python
# tests/test_delete_safety.py (append)
@pytest.mark.asyncio
async def test_delete_hunt_continues_past_missing_channel(monkeypatch):
    """delete_hunt must not abort when one puzzle channel is already gone."""
    import bot.cogs.puzzles as puzzles_mod

    cog = _cog()
    # Two puzzles; channel for the first is missing, second present.
    p1 = SimpleNamespace(id=1, channel_id=101)
    p2 = SimpleNamespace(id=2, channel_id=102)
    present = SimpleNamespace(id=102, delete=AsyncMock())

    guild = MagicMock()
    guild.channels = [present]      # 101 missing, 102 present
    guild.categories = []

    hunt_channel = SimpleNamespace(delete=AsyncMock(), send=AsyncMock(),
                                   category=SimpleNamespace(delete=AsyncMock()))
    ctx = SimpleNamespace(message=SimpleNamespace(id=7), channel=hunt_channel)
    cog.environment[7] = {
        "channel_type": "Hunt", "hunt": SimpleNamespace(id=10),
        "hunt_round": None, "puzzle": None, "guild": guild, "guild_data": None,
        "channel": hunt_channel,
    }

    monkeypatch.setattr(puzzles_mod, "PuzzleJsonDb", MagicMock(
        get_all_from_hunt=MagicMock(return_value=[p1, p2]), delete=MagicMock()))
    monkeypatch.setattr(puzzles_mod, "RoundJsonDb", MagicMock(
        get_all=MagicMock(return_value=[])))
    monkeypatch.setattr(puzzles_mod, "HuntJsonDb", MagicMock(delete=MagicMock()))
    cog._confirm_destructive = AsyncMock(return_value=True)
    cog.delete_puzzle_data = AsyncMock(return_value=True)

    # Should complete without raising despite channel 101 missing.
    await cog.delete_hunt.callback(cog, ctx)

    present.delete.assert_awaited_once()        # the present channel was deleted
    hunt_channel.delete.assert_awaited()        # reached the end (did not abort)
```

> Note for the executor: `delete_hunt` is a `commands.Command`; invoke its underlying coroutine via `.callback(cog, ctx)`. Adjust the `cog.environment` dict if accessors read other keys.

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_safety.py::test_delete_hunt_continues_past_missing_channel -q > /tmp/test.log 2>&1`
Expected: FAIL — either an `AttributeError` on the missing channel (pre-fix crash) or `_confirm_destructive` not yet present.

- [ ] **Step 3: Rewire the loops**

In `delete_round`, replace lines 2253-2255:

```python
        round_puzzles = PuzzleJsonDb.get_all_from_round(hunt_round.id)
        failed = []
        for round_puzzle in round_puzzles:
            await self.delete_puzzle_data(ctx, round_puzzle)
            if not await self._safe_delete_channel(self.get_guild(ctx), round_puzzle.channel_id):
                failed.append(round_puzzle.name)
```

In `delete_hunt`, replace the puzzle loop (2285-2288):

```python
        hunt_puzzles = PuzzleJsonDb.get_all_from_hunt(self.get_hunt(ctx).id)
        failed = []
        for hunt_puzzle in hunt_puzzles:
            await self.delete_puzzle_data(ctx, hunt_puzzle)
            if not await self._safe_delete_channel(self.get_guild(ctx), hunt_puzzle.channel_id):
                failed.append(hunt_puzzle.name)
```

After each command's existing success message, append a remainder report when `failed` is non-empty, e.g. in `delete_round` after line 2267 and in `delete_hunt` before `HuntJsonDb.delete`:

```python
        if failed:
            await hunt_general_channel.send(
                ":warning: Could not delete channels for: "
                + ", ".join(failed)
                + ". They may already be gone or were rate-limited — re-run to finish."
            )
```

(For `delete_hunt`, send to `hunt_channel` before it is itself deleted, or log if the hunt channel is already going away.)

In `delete_puzzle`, replace the bare `except:` (2228) with:

```python
        except (discord.RateLimited, discord.HTTPException):
            logger.exception("delete_puzzle: channel delete failed for %s", self.get_puzzle(ctx))
            await ctx.send(":warning: The puzzle data/sheet was removed but the channel could not be deleted (it may already be gone or was rate-limited).")
```

- [ ] **Step 4: Run the targeted test, then the file**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_safety.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_delete_safety.py
git commit -m "M10: harden delete_round/delete_hunt/delete_puzzle (None-guard, pacing, report)"
```

---

## Task 4: Wire the confirmation gate into the three delete commands

**Files:**
- Modify: `bot/cogs/puzzles.py` — add `_confirm_destructive`, gate `delete_puzzle`/`delete_round`/`delete_hunt`
- Test: `tests/test_delete_safety.py` (append)

- [ ] **Step 1: Append the failing test**

```python
# tests/test_delete_safety.py (append)
@pytest.mark.asyncio
async def test_delete_puzzle_aborts_when_not_confirmed(monkeypatch):
    import bot.cogs.puzzles as puzzles_mod
    cog = _cog()
    puzzle = SimpleNamespace(id=1, name="P", channel_id=101)
    chan = SimpleNamespace(delete=AsyncMock())
    ctx = SimpleNamespace(message=SimpleNamespace(id=7), channel=chan, send=AsyncMock(),
                          author=SimpleNamespace(id=42))
    cog.environment[7] = {
        "channel_type": "Puzzle", "hunt": SimpleNamespace(id=10, channel_id=900),
        "hunt_round": None, "puzzle": puzzle, "guild": MagicMock(), "guild_data": None,
        "channel": chan,
    }
    cog._confirm_destructive = AsyncMock(return_value=False)   # user cancels
    cog.delete_puzzle_data = AsyncMock(return_value=True)

    await cog.delete_puzzle.callback(cog, ctx)

    cog.delete_puzzle_data.assert_not_awaited()   # nothing deleted
    chan.delete.assert_not_awaited()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_safety.py::test_delete_puzzle_aborts_when_not_confirmed -q > /tmp/test.log 2>&1`
Expected: FAIL — `delete_puzzle_data` *is* awaited (no gate yet).

- [ ] **Step 3: Add `_confirm_destructive` and gate the commands**

Add the helper (near `_safe_delete_channel`):

```python
    async def _confirm_destructive(self, ctx, description):
        """Send a Confirm/Cancel prompt and return True only if the invoker
        confirms. Returns False on cancel or timeout."""
        from bot.cogs.ui import ConfirmView
        view = ConfirmView(ctx.author.id)
        embed = discord.Embed(
            title="⚠️ Confirm deletion",
            description=description + "\n\n**Google sheets will be permanently deleted (not recoverable).**",
            color=discord.Color.red(),
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await ctx.send(":hourglass: Confirmation timed out — nothing was deleted.")
        return view.value is True
```

Gate each command immediately after its channel-type validation:

- `delete_puzzle` — after the `!= "Puzzle"` check (after line 2219):
  ```python
          if not await self._confirm_destructive(ctx, f"Delete puzzle **{self.get_puzzle(ctx).name}**, its channel and its sheet?"):
              return
  ```
- `delete_round` — after the `PUZZLE_GROUPS` check (after line 2247), before computing `round_puzzles`:
  ```python
          n = len(PuzzleJsonDb.get_all_from_round(self.get_hunt_round(ctx).id))
          if not await self._confirm_destructive(ctx, f"Delete round **{self.get_hunt_round(ctx).name}** and its {n} puzzle(s), channels and sheets?"):
              return
  ```
- `delete_hunt` — after the `== "Hunt"` check (after line 2283):
  ```python
          n = len(PuzzleJsonDb.get_all_from_hunt(self.get_hunt(ctx).id))
          if not await self._confirm_destructive(ctx, f"Delete the ENTIRE hunt **{self.get_hunt(ctx).name}**: {n} puzzle(s), all rounds, channels and sheets?"):
              return
  ```

- [ ] **Step 4: Run the file, then full suite**

Run: `timeout 120 pipenv run python -m pytest tests/test_delete_safety.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_delete_safety.py
git commit -m "M10: confirm-before gate on delete_puzzle/delete_round/delete_hunt"
```

---

## Task 5: Orphan cleanup in `web_create_puzzle`

**Files:**
- Modify: `bot/cogs/puzzles.py` — `web_create_puzzle` (721-785)
- Test: `tests/test_create_orphan_cleanup.py`

When `get_or_create_channel` (764) fails after the sheet (757) + DB commit (759), delete the sheet + row, then re-raise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_create_orphan_cleanup.py
"""Create-orphan cleanup tests (M10): a failed channel-create leaves no DB row + no sheet."""
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.puzzles import Puzzles
from bot.store.hunt_data import HuntData
from tests.conftest import make_round


def _hunt():
    h = HuntData()
    h.id = 10
    h.google_sheet_id = "SHEET1"
    h.archive_google_sheet_id = "ARCH1"
    h.url = None
    return h


def _cog_with_sheets():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock()
    bot.get_cog.return_value = gsheet
    return Puzzles(bot), gsheet


@pytest.mark.asyncio
async def test_web_create_puzzle_cleans_up_on_channel_failure():
    cog, gsheet = _cog_with_sheets()
    guild = MagicMock()
    guild.channels = []
    guild.categories = [MagicMock(id=999)]
    round_data = make_round(id=5, category_id=999)

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch.object(cog, "get_or_create_channel",
                      AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))):
        with pytest.raises(discord.HTTPException):
            await cog.web_create_puzzle(guild, _hunt(), round_data, "Newpuz")

    # The sheet that was created must be deleted, and the committed row removed.
    gsheet.delete_puzzle_spreadsheet.assert_awaited_once()
    assert pdb.delete.called
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_orphan_cleanup.py::test_web_create_puzzle_cleans_up_on_channel_failure -q > /tmp/test.log 2>&1`
Expected: FAIL — neither `delete_puzzle_spreadsheet` nor `PuzzleJsonDb.delete` is called (orphan left).

- [ ] **Step 3: Wrap the post-commit work in `web_create_puzzle`**

Wrap from the channel create (764) to the end in a try/except. After `PuzzleJsonDb.commit(new_puzzle)` (759):

```python
        try:
            channel_name = self.clean_name(new_puzzle.name)
            text_channel, created = await self.get_or_create_channel(
                guild=guild, category=category, channel_name=channel_name,
                channel_type="text", reason=self.PUZZLE_REASON,
            )
            if created:
                if category is not None:
                    divider = self.get_solve_divider_position(None, category)
                    if divider is not False:
                        async with self.position_lock:
                            await text_channel.edit(position=divider)
                new_puzzle.channel_mention = text_channel.mention
                new_puzzle.channel_id = text_channel.id
                new_puzzle.channel_name = text_channel.name
                PuzzleJsonDb.commit(new_puzzle)
                if send_welcome:
                    await self._web_send_welcome(guild, hunt, round_data, new_puzzle, text_channel)
        except Exception:
            # Channel create failed after the sheet + DB row were committed —
            # clean up both so we don't strand an orphan (row + Google tab).
            await self._cleanup_orphan_puzzle(hunt, new_puzzle)
            raise

        for tag in new_puzzle.tags:
            await self.web_refresh_meta(hunt, RoundJsonDb.get_by_attr(id=tag))
        return new_puzzle
```

Add the shared cleanup helper (near `web_create_puzzle`):

```python
    async def _cleanup_orphan_puzzle(self, hunt, puzzle):
        """Best-effort removal of a puzzle's Google sheet + DB row after a failed
        create. Never raises (we are already handling a create failure)."""
        gsheet_cog = self.bot.get_cog("GoogleSheets")
        if gsheet_cog is not None:
            try:
                gsheet_cog.set_spreadsheet_id(hunt.google_sheet_id)
                gsheet_cog.set_archive_spreadsheet_id(hunt.archive_google_sheet_id)
                await gsheet_cog.delete_puzzle_spreadsheet(puzzle)
            except Exception:
                logger.exception("orphan cleanup: failed to delete sheet for %s", puzzle)
        try:
            if getattr(puzzle, "id", 0):
                PuzzleJsonDb.delete(puzzle.id)
        except Exception:
            logger.exception("orphan cleanup: failed to delete DB row for %s", puzzle)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_orphan_cleanup.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_create_orphan_cleanup.py
git commit -m "M10: clean up orphan sheet+row when web_create_puzzle channel create fails"
```

---

## Task 6: Extend `web_create_round` rollback to the nested meta puzzle

**Files:**
- Modify: `bot/cogs/puzzles.py` — `web_create_round` (813-855)
- Test: `tests/test_create_orphan_cleanup.py` (append)

The current `except` (836-855) deletes the round row + category but strands the meta puzzle row + sheet created by `web_create_puzzle` (827) when a step *after* it (828-834) fails.

- [ ] **Step 1: Append the failing test**

```python
# tests/test_create_orphan_cleanup.py (append)
@pytest.mark.asyncio
async def test_web_create_round_cleans_up_meta_puzzle_on_late_failure():
    cog, gsheet = _cog_with_sheets()
    guild = MagicMock()
    guild.channels = []
    ref_cat = MagicMock(id=1, position=0)
    guild.categories = [ref_cat]
    new_cat = MagicMock(id=2, channels=[])
    ref_cat.clone = AsyncMock(return_value=new_cat)
    new_cat.edit = AsyncMock()
    new_cat.delete = AsyncMock()

    hunt = _hunt()
    hunt.category_id = 1
    hunt.parallel_hunt = False
    hunt.name = "H"

    created_puzzle = MagicMock(id=77, channel_id=None)

    with patch("bot.cogs.puzzles.PuzzleJsonDb") as pdb, \
         patch("bot.cogs.puzzles.RoundJsonDb") as rdb, \
         patch.object(cog, "web_create_puzzle", AsyncMock(return_value=created_puzzle)), \
         patch.object(cog, "_cleanup_orphan_puzzle", AsyncMock()), \
         patch.object(cog, "get_or_create_channel", AsyncMock()):
        rdb.check_duplicates_in_hunt.return_value = False
        pdb.check_duplicates_in_hunt.return_value = False
        rdb.generate_uid.side_effect = RuntimeError("late failure")  # fails AFTER web_create_puzzle
        with pytest.raises(RuntimeError):
            await cog.web_create_round(guild, hunt, "New Round")

    cog._cleanup_orphan_puzzle.assert_awaited_once()
    assert cog._cleanup_orphan_puzzle.await_args.args[1] is created_puzzle
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_orphan_cleanup.py::test_web_create_round_cleans_up_meta_puzzle_on_late_failure -q > /tmp/test.log 2>&1`
Expected: FAIL — `_cleanup_orphan_puzzle` is not called (meta puzzle stranded).

- [ ] **Step 3: Track and clean the meta puzzle in `web_create_round`**

Initialise a handle before the `try` (alongside `new_category = None` / `new_round = None`, ~813):

```python
        puzzle = None
```

Assign it from the existing call (827 — it already assigns `puzzle`, just ensure the name matches). In the `except` block (after the round-row rollback, before the category rollback) add:

```python
            if puzzle is not None:
                await self._cleanup_orphan_puzzle(hunt, puzzle)
```

- [ ] **Step 4: Run the file to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_orphan_cleanup.py -q > /tmp/test.log 2>&1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_create_orphan_cleanup.py
git commit -m "M10: web_create_round rollback also removes the orphan meta puzzle row+sheet"
```

---

## Task 7: Orphan cleanup on the bang `_puzzle` create path

**Files:**
- Modify: `bot/cogs/puzzles.py` — bang `_puzzle` command, `create_puzzle_channel` call at 454
- Test: `tests/test_create_orphan_cleanup.py` (append)

`create_puzzle` (461) commits sheet + row, then the command calls `create_puzzle_channel` at 454. A channel failure strands the row + sheet.

- [ ] **Step 1: Append the failing test**

```python
# tests/test_create_orphan_cleanup.py (append)
@pytest.mark.asyncio
async def test_bang_create_puzzle_cleans_up_on_channel_failure(monkeypatch):
    import bot.cogs.puzzles as puzzles_mod
    cog, gsheet = _cog_with_sheets()
    new_puzzle = MagicMock(id=88, tags=[])

    cog._cleanup_orphan_puzzle = AsyncMock()
    monkeypatch.setattr(cog, "create_puzzle", AsyncMock(return_value=new_puzzle))
    monkeypatch.setattr(cog, "create_puzzle_channel",
                        AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom")))

    hunt = _hunt()
    cat = MagicMock()
    chan = MagicMock(category=cat)
    ctx = MagicMock()
    ctx.channel = chan
    ctx.message.id = 7
    cog.environment[7] = {"channel_type": "Puzzle", "hunt": hunt, "hunt_round": None,
                          "puzzle": None, "guild": MagicMock(), "guild_data": None, "channel": chan}
    monkeypatch.setattr(puzzles_mod, "PuzzleJsonDb",
                        MagicMock(check_duplicates_in_hunt=MagicMock(return_value=False)))
    monkeypatch.setattr(cog, "check_available_space", AsyncMock(return_value=True))
    monkeypatch.setattr(cog, "get_tag_from_category", MagicMock(return_value=None))

    with pytest.raises(discord.HTTPException):
        await cog._create_puzzle_with_cleanup(ctx, "Newpuz")   # see Step 3

    cog._cleanup_orphan_puzzle.assert_awaited_once()
```

> Note for the executor: the bang command is a `commands.Command`; the create+channel logic at 452-454 is extracted into `_create_puzzle_with_cleanup` (Step 3) so it is testable and reused by the command body.

- [ ] **Step 2: Run the test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_orphan_cleanup.py::test_bang_create_puzzle_cleans_up_on_channel_failure -q > /tmp/test.log 2>&1`
Expected: FAIL — `AttributeError: ... '_create_puzzle_with_cleanup'`.

- [ ] **Step 3: Extract a cleanup-wrapped helper and call it from the command**

Add:

```python
    async def _create_puzzle_with_cleanup(self, ctx, puzzle_name):
        """create_puzzle (sheet+row) then its channel, removing the orphan
        sheet+row if the channel create fails."""
        new_puzzle = await self.create_puzzle(ctx, puzzle_name, self.get_tag_from_category(ctx.channel.category))
        try:
            await self.create_puzzle_channel(ctx, new_puzzle, position='bottom')
        except Exception:
            await self._cleanup_orphan_puzzle(self.get_hunt(ctx), new_puzzle)
            raise
        return new_puzzle
```

Replace lines 452-454 in the bang `_puzzle` command body with:

```python
        await self._create_puzzle_with_cleanup(ctx, puzzle_name)
```

- [ ] **Step 4: Run the file, then the FULL suite**

Run: `timeout 120 pipenv run python -m pytest tests/test_create_orphan_cleanup.py -q > /tmp/test.log 2>&1`
Expected: PASS.

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`
Expected: PASS (no regressions across the existing suite).

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_create_orphan_cleanup.py
git commit -m "M10: clean up orphan sheet+row when bang !p channel create fails"
```

---

## Verification

- Full suite green: `timeout 180 pipenv run python -m pytest tests/ -q`.
- `python -m py_compile bot/cogs/puzzles.py bot/cogs/ui.py` (syntax check, since not all paths are DB/Discord-exercisable in CI).
- Manual smoke (live bot, optional): `!delete_round` shows a Confirm/Cancel prompt; Cancel deletes nothing; deleting a round whose puzzle channel was manually removed completes with a "could not delete … re-run to finish" notice; a forced channel-create failure on `!p` and on the dashboard create leaves no orphan tab/row.

## Rollback / recovery notes

- All tasks are additive and behind new helpers; reverting a single task's commit restores prior behaviour with no data/schema impact (no migrations in M10).
- The confirmation gate is the only user-visible behaviour change; if it proves annoying it can be removed by dropping the `_confirm_destructive` call from a command without touching the delete logic.
- `bot/cogs/ui.py` is new and self-contained — safe to delete if the confirm UI is abandoned.

## Spec coverage self-check

- A (confirm gate): Tasks 1, 4. — B (None-guard / abort / pacing / delete_puzzle except): Tasks 2, 3. — C (orphan cleanup, web + bang): Tasks 5, 6, 7. All spec acceptance criteria map to a task.

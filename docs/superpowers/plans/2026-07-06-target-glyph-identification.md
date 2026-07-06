# Target Glyph Identification — Implementation Plan (Plan 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefix a target puzzle's Discord channel name with an emoji glyph by structural kind (🏃 runaround, 🧩🧩 metameta, 🧩 meta, 👑 capstone; feeders/minimeta/solved get none) so targets are identifiable in the channel sidebar.

**Architecture:** The glyph is part of the puzzle's **canonical channel name**. A pure `bot/core/target_glyph.py` maps kind→glyph and strips/applies a prefix. A `PuzzleService.desired_channel_name(hunt, puzzle, group=None)` helper computes `glyph + clean_channel_name(puzzle.name)` (or bare, for feeders); the three sites that name a channel from scratch (`create_puzzle`, `rename_puzzle`, `reconcile`) route through it, and the convert seams that change target-ness trigger a rename to the fresh desired name. A `!backfill_glyphs` organiser command applies it to existing targets. The stored `puzzle.name` stays glyph-free.

**Tech Stack:** Python, discord.py, MySQL DAOs, pytest (`pipenv run python -m pytest`), `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-06-extract-target-to-category-design.md` ("Target identification — emoji channel-name prefixes").

**CI gates (run after every task touching `bot/`):**
```bash
timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1   # must be 0 errors
timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1             # must be "All checks passed!"
```
Only `bot/` is type-checked; `tests/` isn't. Ruff (pyflakes `F`) is repo-wide — no unused imports in tests. Wrap pytest in `timeout` and write to a file; a killed run exits 124.

**Key facts (verified against current code):**
- `clean_channel_name` (`bot/core/discord_channels.py:38`) lowercases, spaces→hyphens; it strips only ASCII punctuation, so a leading **emoji passes through unchanged** — a glyph prefix survives it.
- `is_metameta(tag_group, all_tag_groups, puzzles_in_group)` (`bot/core/puzzle_service.py:133`) → True iff a `feeds` member is itself some group's target. `_update_forum_tags` (`~L276-282`) already computes it — reuse that recipe.
- `desired_tags_for` precedence (`bot/core/forum_config.py:100`): runaround > metameta > meta > minimeta > capstone. For a **target**, only runaround/metameta/meta/capstone can apply (minimeta never wins for a target).
- Stored group `type` values: `Metapuzzle`, `Capstone`, `Runaround`, `Group` (a `Group` has no target → never glyphed).
- Renames go through `safe_rename(channel, name)` (`puzzle_service.py:187`), which defers/coalesces on rate-limit — use it for the backfill, not raw `channel.edit`. `max_ratelimit_timeout` is absent (confirmed), so bulk renames degrade gracefully.
- **Trap:** `rename_puzzle` (`puzzle_service.py:1893`) and `reconcile` (`~L327/341`) recompute the name from bare `clean_channel_name(puzzle.name)` — they will *erase* a glyph unless routed through `desired_channel_name`. `convert_to_puzzle` (unconvert, `~L1203`) does NOT rename the channel — a glyph-strip refresh must be added there.

---

## File Structure

- **Create** `bot/core/target_glyph.py` — pure: `GLYPHS`, `glyph_for_kind`, `strip_glyph`, `glyphed_name`. Create `tests/test_target_glyph.py`.
- **Modify** `bot/core/puzzle_service.py` — add `_target_kind` + `desired_channel_name` helpers; route `create_puzzle`/`rename_puzzle`/`reconcile` name computation through it; add a glyph refresh at the convert seams. Create `tests/test_glyph_service.py`.
- **Modify** `bot/cogs/puzzles.py` — add `!backfill_glyphs`. Create `tests/test_backfill_glyphs_command.py`.
- **Modify** `docs/commands.md`, `docs/user-guide.md`.

---

## Task 1: Pure glyph vocabulary

**Files:** Create `bot/core/target_glyph.py`; Test `tests/test_target_glyph.py`.

- [ ] **Step 1: Write the failing tests** — `tests/test_target_glyph.py`:

```python
from bot.core.target_glyph import GLYPHS, glyph_for_kind, glyphed_name, strip_glyph


def test_glyph_for_each_kind():
    assert glyph_for_kind("runaround") == "🏃"
    assert glyph_for_kind("metameta") == "🧩🧩"
    assert glyph_for_kind("meta") == "🧩"
    assert glyph_for_kind("capstone") == "👑"


def test_glyph_for_unglyphed_kinds_is_empty():
    assert glyph_for_kind("minimeta") == ""
    assert glyph_for_kind(None) == ""
    assert glyph_for_kind("feeder") == ""


def test_glyphed_name_prefixes():
    assert glyphed_name("meta", "spooky-meta") == "🧩spooky-meta"
    assert glyphed_name("capstone", "final") == "👑final"
    assert glyphed_name(None, "just-a-feeder") == "just-a-feeder"


def test_strip_glyph_removes_any_known_glyph():
    assert strip_glyph("🧩spooky-meta") == "spooky-meta"
    assert strip_glyph("🧩🧩big-meta") == "big-meta"
    assert strip_glyph("👑final") == "final"
    assert strip_glyph("🏃the-run") == "the-run"


def test_strip_glyph_noop_when_absent():
    assert strip_glyph("plain-feeder") == "plain-feeder"


def test_glyphed_name_is_idempotent_with_strip():
    # applying a glyph to an already-stripped clean name round-trips
    assert strip_glyph(glyphed_name("metameta", "x")) == "x"
```

- [ ] **Step 2: Run, verify FAIL** (ModuleNotFoundError):
`timeout 120 pipenv run python -m pytest tests/test_target_glyph.py -q > /tmp/test.log 2>&1`

- [ ] **Step 3: Implement** `bot/core/target_glyph.py`:

```python
"""Emoji glyphs prefixed to a target puzzle's Discord channel name so targets
are identifiable in the sidebar. Pure string logic — no Discord/DB.

Glyph by structural kind (see the design spec's identification section):
runaround 🏃, metameta 🧩🧩, meta 🧩, capstone 👑. Feeders, minimetas, and
solved status get no glyph. The glyph is a Discord-channel-name-only prefix;
the stored puzzle.name never carries it.
"""

# Ordered longest-glyph-first so strip_glyph matches 🧩🧩 before 🧩.
GLYPHS = {
    "runaround": "🏃",
    "metameta": "🧩🧩",
    "meta": "🧩",
    "capstone": "👑",
}

# Longest first so a metameta ("🧩🧩") isn't half-stripped as a meta ("🧩").
_STRIP_ORDER = sorted(GLYPHS.values(), key=len, reverse=True)


def glyph_for_kind(kind):
    """The glyph for a structural kind, or "" for kinds/None that carry none."""
    return GLYPHS.get(kind, "")


def glyphed_name(kind, clean_name):
    """A channel name with *kind*'s glyph prefixed (or bare *clean_name*)."""
    return glyph_for_kind(kind) + clean_name


def strip_glyph(channel_name):
    """*channel_name* with a leading known glyph removed (or unchanged)."""
    for glyph in _STRIP_ORDER:
        if channel_name.startswith(glyph):
            return channel_name[len(glyph):]
    return channel_name
```

- [ ] **Step 4: Run, verify PASS** (6 passed): same command as Step 2.
- [ ] **Step 5: Lint + commit**
```bash
timeout 120 pipenv run ruff check bot/core/target_glyph.py tests/test_target_glyph.py > /tmp/ruff.log 2>&1
git add bot/core/target_glyph.py tests/test_target_glyph.py
git commit -m "feat(glyph): pure emoji-glyph vocabulary for target channel names

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Service — canonical glyphed channel name + integration

**Files:** Modify `bot/core/puzzle_service.py`; Test `tests/test_glyph_service.py`.

Add two helpers and route the three name-computation sites plus the convert seams through them.

- [ ] **Step 1: Write the failing tests** — `tests/test_glyph_service.py`:

```python
from unittest.mock import MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData


def _svc():
    return PuzzleService(MagicMock())


def _puzzle(pid, name, is_target=False):
    p = PuzzleData(name=name)
    p.id = pid
    p.is_target = is_target
    return p


def _group(gid, gtype, target_id):
    g = TagGroupData(name="grp")
    g.id = gid
    g.type = gtype
    g.target_puzzle_id = target_id
    return g


def test_desired_channel_name_feeder_has_no_glyph():
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    feeder = _puzzle(2, "Plain Feeder")
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb:
        mrdb.get_by_attr.return_value = None  # not a target
        assert svc.desired_channel_name(hunt, feeder) == "plain-feeder"


def test_desired_channel_name_meta_gets_meta_glyph():
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    meta = _puzzle(2, "Spooky Meta", is_target=True)
    grp = _group(10, "Metapuzzle", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "_is_metameta_group", return_value=False):
        mrdb.get_by_attr.return_value = grp
        mrdb.get_all.return_value = [grp]
        mpdb.get_all_from_tag_group.return_value = [meta]
        assert svc.desired_channel_name(hunt, meta) == "🧩spooky-meta"


def test_desired_channel_name_capstone_gets_crown():
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    cap = _puzzle(2, "The End", is_target=True)
    grp = _group(10, "Capstone", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "_is_metameta_group", return_value=False):
        mrdb.get_by_attr.return_value = grp
        mrdb.get_all.return_value = [grp]
        mpdb.get_all_from_tag_group.return_value = [cap]
        assert svc.desired_channel_name(hunt, cap) == "👑the-end"


def test_desired_channel_name_metameta_gets_double_glyph():
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    meta = _puzzle(2, "Big Meta", is_target=True)
    grp = _group(10, "Metapuzzle", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "_is_metameta_group", return_value=True):
        mrdb.get_by_attr.return_value = grp
        mrdb.get_all.return_value = [grp]
        mpdb.get_all_from_tag_group.return_value = [meta]
        assert svc.desired_channel_name(hunt, meta) == "🧩🧩big-meta"


def test_desired_channel_name_uses_passed_group_without_db_lookup():
    # create_puzzle path: group known, target relationship not yet committed.
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    meta = _puzzle(2, "Fresh Meta", is_target=True)
    grp = _group(10, "Runaround", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "_is_metameta_group", return_value=False):
        mpdb.get_all_from_tag_group.return_value = [meta]
        mrdb.get_all.return_value = [grp]
        # group passed in => no get_by_attr(target_puzzle_id=...) needed
        assert svc.desired_channel_name(hunt, meta, group=grp) == "🏃fresh-meta"
        mrdb.get_by_attr.assert_not_called()
```

- [ ] **Step 2: Run, verify FAIL** (AttributeError: no `desired_channel_name`):
`timeout 180 pipenv run python -m pytest tests/test_glyph_service.py -q > /tmp/test.log 2>&1`

- [ ] **Step 3: Add imports + helpers** to `bot/core/puzzle_service.py`.

Add import near the other `from bot.core import ...`:
```python
from bot.core.target_glyph import glyphed_name
```

Add these methods to `PuzzleService` (place them near `_update_forum_tags`, which already computes metameta the same way):

```python
def _is_metameta_group(self, hunt, group):
    """Whether *group*'s feeders include another group's target (a sub-meta)."""
    all_groups = TagGroupJsonDb.get_all(hunt_id=hunt.id)
    members = PuzzleJsonDb.get_all_from_tag_group(group.id)
    return is_metameta(group, all_groups, members)

def _target_kind(self, hunt, puzzle, group=None):
    """The glyph kind for *puzzle* as a target ('runaround'/'metameta'/'meta'/
    'capstone'), or None if it is not a target. *group* may be supplied by the
    create path where the target relationship isn't committed yet."""
    if group is None:
        group = TagGroupJsonDb.get_by_attr(target_puzzle_id=puzzle.id)
        if group is None:
            return None
    # group is either explicitly passed (the create path asserts this puzzle is
    # its target, before target_puzzle_id is committed) or resolved via
    # target_puzzle_id (so it already targets puzzle) — either way, derive by type.
    if group.type == "Runaround":
        return "runaround"
    if self._is_metameta_group(hunt, group):
        return "metameta"
    if group.type in ("Round", "Metapuzzle"):
        return "meta"
    if group.type == "Capstone":
        return "capstone"
    return None

def desired_channel_name(self, hunt, puzzle, group=None):
    """The canonical Discord channel name for *puzzle*: the glyph for its target
    kind (if any) prefixed to clean_channel_name(puzzle.name)."""
    kind = self._target_kind(hunt, puzzle, group=group)
    return glyphed_name(kind, clean_channel_name(puzzle.name))
```

(`is_metameta`, `TagGroupJsonDb`, `PuzzleJsonDb`, `clean_channel_name` are already in scope in this module — verify, don't re-import.)

- [ ] **Step 4: Run, verify the 5 helper tests PASS**: same command as Step 2 (expect 5 passed).

- [ ] **Step 5: Route the name-computation sites through the helper.** Make these edits in `bot/core/puzzle_service.py` (adapt to the exact current lines — the intent is "replace bare `clean_channel_name(puzzle.name)` for a PUZZLE channel with `self.desired_channel_name(hunt, puzzle, group=...)`"):
  - **`create_puzzle`** (~L2145): `channel_name = clean_channel_name(new_puzzle.name)` → `channel_name = self.desired_channel_name(hunt, new_puzzle, group=round_data if is_meta else None)`. (For a feeder `is_meta=False`, `group=None` → no glyph. For a meta, pass `round_data` so the glyph resolves before the target row is committed.)
  - **`rename_puzzle`** (~L1893): `puzzle.channel_name = clean_channel_name(name)` → after setting `puzzle.name = name` (keep the stored name glyph-free), compute `puzzle.channel_name = self.desired_channel_name(hunt, puzzle)`. Ensure a `hunt` is in scope here (fetch via `HuntJsonDb.get_by_attr(id=puzzle.hunt_id)` if the method doesn't already have one).
  - **`reconcile`** (~L327): `want_name = clean_channel_name(puzzle.name)` → `want_name = self.desired_channel_name(hunt, puzzle)` (reconcile already has `hunt`). This makes `reconcile apply` a natural glyph backfill/repair too.
  - Do **NOT** change `create_mini` (L1990), group text-channel create (L2262), or category/group renames — minis and groups are not glyph-carrying targets.

- [ ] **Step 6: Add a glyph refresh at the convert seams.** Add a helper and call it where target-ness changes but no rename otherwise happens:

```python
async def _refresh_channel_glyph(self, guild, hunt, puzzle):
    """Rename *puzzle*'s channel to its desired glyphed name if it differs."""
    if not getattr(puzzle, "channel_id", 0):
        return
    channel = guild.get_channel(puzzle.channel_id)
    if channel is None:
        return
    want = self.desired_channel_name(hunt, puzzle)
    if channel.name != want:
        await self.safe_rename(channel, want)
```

Call `await self._refresh_channel_glyph(guild, hunt, puzzle)` at the end of the target-mutation service methods (right after the existing `_update_forum_tags(...)` call, or where `is_target` is finalised): `convert_to_meta`, `convert_to_type` (promote-in-place + type change), and `convert_to_puzzle`/`convert_to_feeder` (unconvert — here the kind resolves to None, so it strips the glyph). Use the `guild`/`hunt`/`puzzle` already in each method's scope. If `safe_rename` is a module function rather than a method in the real code, call it accordingly.

- [ ] **Step 7: Add integration tests** to `tests/test_glyph_service.py` proving the round-trip on convert:

```python
async def test_refresh_glyph_renames_when_name_differs():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    meta = _puzzle(2, "Spooky Meta", is_target=True); meta.channel_id = 55
    channel = MagicMock(); channel.name = "spooky-meta"
    guild.get_channel.return_value = channel
    svc.safe_rename = AsyncMock()
    with patch.object(PuzzleService, "desired_channel_name", return_value="🧩spooky-meta"):
        await svc._refresh_channel_glyph(guild, hunt, meta)
    svc.safe_rename.assert_awaited_once_with(channel, "🧩spooky-meta")


async def test_refresh_glyph_noop_when_already_correct():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock()
    meta = _puzzle(2, "Spooky Meta", is_target=True); meta.channel_id = 55
    channel = MagicMock(); channel.name = "🧩spooky-meta"
    guild.get_channel.return_value = channel
    svc.safe_rename = AsyncMock()
    with patch.object(PuzzleService, "desired_channel_name", return_value="🧩spooky-meta"):
        await svc._refresh_channel_glyph(guild, hunt, meta)
    svc.safe_rename.assert_not_awaited()
```

Add `from unittest.mock import AsyncMock` to the test imports.

- [ ] **Step 8: Run all glyph tests + a regression on the convert/reconcile suites**
```bash
timeout 300 pipenv run python -m pytest tests/test_glyph_service.py tests/test_target_glyph.py tests/test_convert_service.py -q > /tmp/test.log 2>&1
```
Expect all pass. If `test_convert_service` regresses, a convert seam broke — read `/tmp/test.log`.

- [ ] **Step 9: Type + lint gates**
```bash
timeout 180 pipenv run basedpyright bot/core/puzzle_service.py bot/core/target_glyph.py > /tmp/pyright.log 2>&1
timeout 120 pipenv run ruff check bot/core/puzzle_service.py tests/test_glyph_service.py > /tmp/ruff.log 2>&1
```
Expect basedpyright 0 errors, ruff clean. Watch for `reportOptionalMemberAccess` from `get_by_attr`/`get_channel` returning `... | None` — the helpers already guard `group is None` / `channel is None`; keep those guards.

- [ ] **Step 10: Commit**
```bash
git add bot/core/puzzle_service.py tests/test_glyph_service.py
git commit -m "feat(glyph): canonical glyphed channel names + convert-seam refresh

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `!backfill_glyphs` command

**Files:** Modify `bot/cogs/puzzles.py`; Test `tests/test_backfill_glyphs_command.py`.

A one-time organiser command that renames every target's channel in the current hunt to its desired glyphed name. Idempotent (skips channels already correct).

- [ ] **Step 1: Write the failing test** — `tests/test_backfill_glyphs_command.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from bot.cogs.puzzles import Puzzles


def _cog_and_ctx(hunt):
    cog = MagicMock()
    cog.get_hunt = MagicMock(return_value=hunt)
    cog.service = MagicMock()
    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.send = AsyncMock()
    return cog, ctx


async def test_backfill_calls_service_and_reports():
    hunt = MagicMock()
    cog, ctx = _cog_and_ctx(hunt)
    cog.service.backfill_glyphs = AsyncMock(return_value=3)

    await Puzzles.backfill_glyphs.callback(cog, ctx)

    cog.service.backfill_glyphs.assert_awaited_once_with(ctx.guild, hunt)
    ctx.send.assert_awaited()
    assert "3" in ctx.send.await_args.args[0]


async def test_backfill_refuses_outside_hunt():
    cog, ctx = _cog_and_ctx(None)
    cog.service.backfill_glyphs = AsyncMock()

    await Puzzles.backfill_glyphs.callback(cog, ctx)

    cog.service.backfill_glyphs.assert_not_awaited()
    ctx.send.assert_awaited()
```

Also add a service test to `tests/test_glyph_service.py`:

```python
async def test_backfill_glyphs_renames_each_target():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    g1 = _group(10, "Metapuzzle", 100)
    g2 = _group(11, "Capstone", 101)
    g_notarget = _group(12, "Group", 0)
    t1 = _puzzle(100, "Meta One", is_target=True); t1.channel_id = 500
    t2 = _puzzle(101, "Cap", is_target=True); t2.channel_id = 501
    ch1 = MagicMock(); ch1.name = "meta-one"       # needs glyph
    ch2 = MagicMock(); ch2.name = "👑cap"           # already correct
    guild.get_channel = MagicMock(side_effect=lambda cid: {500: ch1, 501: ch2}.get(cid))
    svc.safe_rename = AsyncMock()
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "desired_channel_name",
                      side_effect=lambda h, p, group=None: {100: "🧩meta-one", 101: "👑cap"}[p.id]):
        mrdb.get_all.return_value = [g1, g2, g_notarget]
        mpdb.get_by_attr.side_effect = lambda id: {100: t1, 101: t2}.get(id)
        renamed = await svc.backfill_glyphs(guild, hunt)
    assert renamed == 1                       # only ch1 needed a rename
    svc.safe_rename.assert_awaited_once_with(ch1, "🧩meta-one")
```

- [ ] **Step 2: Run, verify FAIL**:
`timeout 180 pipenv run python -m pytest tests/test_backfill_glyphs_command.py tests/test_glyph_service.py::test_backfill_glyphs_renames_each_target -q > /tmp/test.log 2>&1`

- [ ] **Step 3: Add the service method** to `PuzzleService` (`bot/core/puzzle_service.py`):

```python
async def backfill_glyphs(self, guild, hunt):
    """Rename every target channel in *hunt* to its desired glyphed name.
    Idempotent; returns the count of channels actually renamed."""
    renamed = 0
    for group in TagGroupJsonDb.get_all(hunt_id=hunt.id):
        if not group.target_puzzle_id:
            continue
        puzzle = PuzzleJsonDb.get_by_attr(id=group.target_puzzle_id)
        if puzzle is None or not getattr(puzzle, "channel_id", 0):
            continue
        channel = guild.get_channel(puzzle.channel_id)
        if channel is None:
            continue
        want = self.desired_channel_name(hunt, puzzle, group=group)
        if channel.name != want:
            await self.safe_rename(channel, want)
            renamed += 1
    return renamed
```

- [ ] **Step 4: Add the command** to the `Puzzles` cog (near `reconcile`, ~L855):

```python
@commands.hybrid_command(
    description="Add/refresh emoji glyphs on every target channel in this hunt")
@commands.has_any_role(*ORGANISER_ROLES)
@commands.has_permissions(manage_channels=True)
async def backfill_glyphs(self, ctx):
    """*One-off: prefix every meta/capstone/runaround channel with its glyph.*

    Usage: `!backfill_glyphs`
    Example: `!backfill_glyphs`
    Run in any channel of the hunt. Organisers/admins only. Idempotent."""
    hunt = self.get_hunt(ctx)
    if hunt is None:
        return await ctx.send(":x: Run this in a hunt channel.")
    renamed = await self.service.backfill_glyphs(ctx.guild, hunt)
    await ctx.send(
        f":white_check_mark: Glyph backfill complete — {renamed} channel(s) renamed.")
```

- [ ] **Step 5: Run tests, verify PASS**; then the type + lint gates:
```bash
timeout 200 pipenv run python -m pytest tests/test_backfill_glyphs_command.py tests/test_glyph_service.py -q > /tmp/test.log 2>&1
timeout 180 pipenv run basedpyright bot/cogs/puzzles.py bot/core/puzzle_service.py > /tmp/pyright.log 2>&1
timeout 120 pipenv run ruff check bot/cogs/puzzles.py bot/core/puzzle_service.py tests/test_backfill_glyphs_command.py > /tmp/ruff.log 2>&1
```
Expect all green.

- [ ] **Step 6: Commit**
```bash
git add bot/cogs/puzzles.py bot/core/puzzle_service.py tests/test_backfill_glyphs_command.py tests/test_glyph_service.py
git commit -m "feat(glyph): !backfill_glyphs organiser command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Documentation

**Files:** Modify `docs/commands.md`, `docs/user-guide.md`.

- [ ] **Step 1: `docs/commands.md`** — add a row to a sensible maintenance/organiser table (e.g. near `reconcile`/`sync`, or the "Sheets"/maintenance area). Use the table's existing column shape. Content:
```markdown
| `!backfill_glyphs` | | — | any hunt channel | organisers/admins | Prefix every target channel (meta 🧩, metameta 🧩🧩, capstone 👑, runaround 🏃) with its emoji glyph. Idempotent one-off; new/converted targets get their glyph automatically. |
```
Also add a short note wherever channel naming/layout is described that target channels carry a leading emoji by kind.

- [ ] **Step 2: `docs/user-guide.md`** — add a short paragraph (near the meta/convert discussion) explaining that meta/capstone/runaround channels show a leading emoji (🧩 / 👑 / 🏃; a meta-of-metas shows 🧩🧩) so you can spot targets in the channel list, and that `!backfill_glyphs` applies them to a hunt created before the feature.

- [ ] **Step 3: Sanity-check + commit**
```bash
git diff --stat docs/commands.md docs/user-guide.md
git add docs/commands.md docs/user-guide.md
git commit -m "docs: document target glyphs + !backfill_glyphs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification

- [ ] Full suite: `timeout 400 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` — no new failures.
- [ ] Type gate: `timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1` — 0 errors.
- [ ] Lint gate: `timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1` — clean.
- [ ] Manual smoke (optional, live): create a meta → its channel shows 🧩; convert it to capstone → 👑; add a sub-meta feeder and run `!backfill_glyphs` (or reconcile) → the parent shows 🧩🧩; convert back to feeder → glyph gone.

## Known limitation (documented, by design)

Metameta status (🧩→🧩🧩) is computed when the channel name is (re)computed — at create, convert, rename, `!backfill_glyphs`, and `reconcile`. It is **not** reactively refreshed on every feeder edge change, so a meta that becomes a metameta purely by gaining a sub-meta feeder shows 🧩 until it's next touched or `!backfill_glyphs`/`reconcile` runs. This keeps the change off the hot edge-mutation path; the design spec treats the glyph as identification, not a live status indicator.

## Deferred follow-up (perf, non-blocking)

`reconcile` calls `desired_channel_name` per target in its loop, and each call re-fetches
`TagGroupJsonDb.get_all(hunt.id)` (+ `get_all_from_tag_group`) inside `_is_metameta_group`
— an N+1 against a group list reconcile already has in hand. For a near-cap hunt (~50
rounds) that's ~100 extra queries on a **manual, infrequent** admin action, so it was left
as-is. If it ever matters, thread an optional precomputed `all_groups` through
`desired_channel_name`/`_target_kind`/`_is_metameta_group` and pass reconcile's `rounds`.

## Out of scope (later plans)

- `!consolidate_to` (Plan 3), auto-extract on overflow (Plan 4).
- No web-dashboard surface for `!backfill_glyphs` (Discord plumbing).
- Solved status is never encoded in the name (positional only).

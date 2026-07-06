# Auto-Extract On Overflow — Implementation Plan (Plan 4 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** When creating a puzzle would overflow a category's soft cap, automatically extract a channel-home meta into its own category (reusing `extract_target_to_category`) to relieve pressure — instead of hard-failing (Case A) or eagerly splitting a mixed category (Case B). Greedy, bounded to one extraction per create, non-recursive.

**Architecture:** A best-effort `PuzzleService._maybe_relieve_overflow(guild, hunt, round_data)` runs at the top of `create_puzzle`'s placement (before the early capacity probe). **Case A** — `round_data` is itself a channel-home meta whose host category is full → extract that meta. **Case B** — `round_data` owns a real, single (non-split) category about to overflow → pick the nested channel-home meta whose extraction frees the most channels (`value = 1 + exclusive plain feeders in this category`) and extract it if `value ≥ EXTRACT_MIN_VALUE`. Any failure falls through to today's behaviour. Two module constants (`AUTO_EXTRACT_ON_OVERFLOW`, `EXTRACT_MIN_VALUE`) gate/tune it.

**Tech Stack:** Python, discord.py, MySQL DAOs, pytest, `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-06-extract-target-to-category-design.md` ("Feature 2 — auto-extract on overflow").

**CI gates (after every task touching `bot/`):**
```bash
timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1   # 0 errors
timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1             # All checks passed!
```
`tests/` isn't type-checked; ruff (pyflakes F) is repo-wide. Wrap pytest in `timeout`, write to a file.

**Verified seams:**
- `create_puzzle` (`bot/core/puzzle_service.py`, sig ~L2257) places via `category_layout.pick_unsolved_category(guild, owner, owner_dao)` at an early fail-fast probe (~L2269-2288, where **Case A hard-fails** today with `primary_category_missing`) and an authoritative re-pick (~L2358-2366). `round_data` is the tag_group (`round_data is None` ⇒ hunt-general).
- **Case A/B discriminator:** `category_layout.primary_category(guild, round_data) is None` ⇔ channel-home meta (Case A); not-None ⇔ owns a real category (Case B).
- `pick_unsolved_category` (`category_layout.py:245`): if `puzzle_count(primary)+1 <= CATEGORY_SOFT_LIMIT` returns primary; else `split_group` (no-op for channel-home) → `_allocate` which raises `primary_category_missing` for channel-home (`category_layout.py:219`) or splits a real-category round (`:255`).
- Reuse `PuzzleService.extract_target_to_category(guild, hunt, meta)` (~L1888) → returns the new category, flips `meta.category_id` to it (so subsequent picks see a real category); raises `already_own_category`/`no_category_space`/`no_template_category`/`no_target_puzzle`. It takes `layout_lock` internally — safe to call from `create_puzzle` (no lock held there).
- `select_extract_puzzles(meta, target_puzzle, members, target_ids)` (`bot/core/target_extract.py:12`) — `result[0]` is the target, `result[1:]` the exclusive plain feeders (the value set).
- Nested-meta enumeration pattern (mirrors `_channels_to_consolidate`): `TagGroupJsonDb.get_all(hunt.id)`, `primary_category(guild, g) is None` and `g.category_id`, `guild.get_channel(g.category_id).category is cat`.
- Tunable convention: module constants next to `CATEGORY_SOFT_LIMIT`/`CONSOLIDATE_MARGIN` in `category_layout.py`; monkeypatched in tests.

---

## File Structure

- **Modify** `bot/core/category_layout.py` — add `AUTO_EXTRACT_ON_OVERFLOW` + `EXTRACT_MIN_VALUE` constants.
- **Modify** `bot/core/puzzle_service.py` — add `_highest_value_nested_meta` + `_maybe_relieve_overflow`; call it once at the top of `create_puzzle`'s placement. Test `tests/test_auto_extract_service.py`.
- **Modify** `docs/commands.md` / `docs/user-guide.md` — brief note on automatic behaviour + tunables.

---

## Task 1: The overflow-relief decision logic

**Files:** Modify `bot/core/category_layout.py` (constants) + `bot/core/puzzle_service.py` (two helpers); Test `tests/test_auto_extract_service.py`.

- [ ] **Step 1: Add the constants** to `bot/core/category_layout.py` (near `CATEGORY_SOFT_LIMIT`/`CONSOLIDATE_MARGIN`, ~L34-47):

```python
# Auto-extract on overflow (Plan 4): when a create would overflow a category,
# extract a channel-home meta into its own category rather than hard-failing
# (Case A) or eagerly splitting a mixed category (Case B). EXTRACT_MIN_VALUE is
# the smallest channel-saving that justifies spending a category on extraction
# (value == 1 means the meta has no solely-attached feeders — not worth it).
AUTO_EXTRACT_ON_OVERFLOW = True
EXTRACT_MIN_VALUE = 2
```

- [ ] **Step 2: Write the failing tests** — `tests/test_auto_extract_service.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData, EDGE_FEEDS, EDGE_TARGET


def _svc():
    return PuzzleService(MagicMock())


def _puzzle(pid, channel_id, edges):
    p = PuzzleData(name=f"p{pid}")
    p.id = pid
    p.channel_id = channel_id
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _group(gid, gtype, target_id, category_id):
    g = TagGroupData(name=f"g{gid}")
    g.id = gid
    g.type = gtype
    g.target_puzzle_id = target_id
    g.category_id = category_id
    return g


async def test_case_a_extracts_channel_home_meta_when_host_full(monkeypatch):
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    meta = _group(10, "Metapuzzle", 1, 801)  # channel-home (category_id is a channel)
    host = MagicMock(); host.id = 900
    svc.extract_target_to_category = AsyncMock(return_value=MagicMock())
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = None          # channel-home
        mcl._home_category.return_value = host
        mcl.puzzle_count.return_value = 48                # host at cap
        await svc._maybe_relieve_overflow(guild, hunt, meta)
    svc.extract_target_to_category.assert_awaited_once_with(guild, hunt, meta)


async def test_case_a_noop_when_host_has_room():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    meta = _group(10, "Metapuzzle", 1, 801)
    svc.extract_target_to_category = AsyncMock()
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = None
        mcl._home_category.return_value = MagicMock()
        mcl.puzzle_count.return_value = 10               # room
        await svc._maybe_relieve_overflow(guild, hunt, meta)
    svc.extract_target_to_category.assert_not_awaited()


async def test_case_b_extracts_highest_value_nested_meta():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    round_group = _group(20, "Metapuzzle", 3, 900)       # owns real category 900
    primary = MagicMock(); primary.id = 900
    big = _group(11, "Metapuzzle", 100, 801)             # nested, value 3
    small = _group(12, "Metapuzzle", 101, 802)           # nested, value 1
    svc.extract_target_to_category = AsyncMock(return_value=MagicMock())
    svc._highest_value_nested_meta = MagicMock(return_value=(big, 3))
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = primary       # owns a category
        mcl.is_split.return_value = False
        mcl.puzzle_count.return_value = 48                # at cap
        await svc._maybe_relieve_overflow(guild, hunt, round_group)
    svc.extract_target_to_category.assert_awaited_once_with(guild, hunt, big)


async def test_case_b_falls_through_when_best_value_is_one():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    round_group = _group(20, "Metapuzzle", 3, 900)
    primary = MagicMock(); primary.id = 900
    lone = _group(12, "Metapuzzle", 101, 802)
    svc.extract_target_to_category = AsyncMock()
    svc._highest_value_nested_meta = MagicMock(return_value=(lone, 1))
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = primary
        mcl.is_split.return_value = False
        mcl.puzzle_count.return_value = 48
        await svc._maybe_relieve_overflow(guild, hunt, round_group)
    svc.extract_target_to_category.assert_not_awaited()


async def test_disabled_flag_is_noop():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock()
    meta = _group(10, "Metapuzzle", 1, 801)
    svc.extract_target_to_category = AsyncMock()
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = False
        await svc._maybe_relieve_overflow(guild, hunt, meta)
    svc.extract_target_to_category.assert_not_awaited()


async def test_extract_failure_is_swallowed():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    meta = _group(10, "Metapuzzle", 1, 801)
    svc.extract_target_to_category = AsyncMock(side_effect=RuntimeError("no_category_space"))
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = None
        mcl._home_category.return_value = MagicMock()
        mcl.puzzle_count.return_value = 48
        await svc._maybe_relieve_overflow(guild, hunt, meta)  # must NOT raise
    svc.extract_target_to_category.assert_awaited_once()


def test_highest_value_nested_meta_picks_bigger():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    cat = MagicMock(); cat.id = 900
    big = _group(11, "Metapuzzle", 100, 801)
    small = _group(12, "Metapuzzle", 101, 802)
    round_group = _group(20, "Metapuzzle", 3, 900)
    # channel homes 801/802 live in cat; feeder channels 811/812/813 also in cat
    ch_map = {801: cat, 802: cat, 811: cat, 812: cat, 813: cat, 900: None}
    def _get_channel(cid):
        c = MagicMock(); c.category = ch_map.get(cid); c.id = cid; return c
    guild.get_channel = MagicMock(side_effect=_get_channel)
    t100 = _puzzle(100, 801, [(11, EDGE_TARGET)])
    t101 = _puzzle(101, 802, [(12, EDGE_TARGET)])
    f_big1 = _puzzle(200, 811, [(11, EDGE_FEEDS)])
    f_big2 = _puzzle(201, 812, [(11, EDGE_FEEDS)])
    f_small = _puzzle(202, 813, [(12, EDGE_FEEDS)])
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.primary_category.side_effect = lambda g, grp: None  # both nested are channel-home
        mrdb.get_all.return_value = [round_group, big, small]
        def _members(gid):
            return {11: [t100, f_big1, f_big2], 12: [t101, f_small]}.get(gid, [])
        mpdb.get_all_from_tag_group.side_effect = _members
        mpdb.get_by_attr.side_effect = lambda **kw: {100: t100, 101: t101}.get(kw.get("id"))
        best = svc._highest_value_nested_meta(guild, hunt, cat)
    assert best is not None
    meta, value = best
    assert meta.id == 11 and value == 3           # 1 target + 2 feeders
```

- [ ] **Step 3: Run, confirm FAIL:** `timeout 180 pipenv run python -m pytest tests/test_auto_extract_service.py -q > /tmp/test.log 2>&1`

- [ ] **Step 4: Implement** the two helpers in `bot/core/puzzle_service.py` (near `extract_target_to_category`). Adapt to real APIs; keep behaviour + assertions.

```python
def _highest_value_nested_meta(self, guild, hunt, cat):
    """The nested channel-home meta inside *cat* whose extraction frees the most
    channels, as (meta, value), or None. value = 1 (target channel) + exclusive
    plain feeders currently in *cat*."""
    all_groups = TagGroupJsonDb.get_all(hunt.id)
    target_ids = {g.target_puzzle_id for g in all_groups if g.target_puzzle_id}
    best = None
    for g in all_groups:
        if category_layout.primary_category(guild, g) is not None or not g.category_id:
            continue
        home = guild.get_channel(g.category_id)
        if home is None or getattr(home, "category", None) is not cat:
            continue
        target_puzzle = PuzzleJsonDb.get_by_attr(id=g.target_puzzle_id)
        if target_puzzle is None:
            continue
        members = PuzzleJsonDb.get_all_from_tag_group(g.id)
        feeders = select_extract_puzzles(g, target_puzzle, members, target_ids)[1:]
        value = 1 + sum(
            1 for p in feeders
            if getattr(guild.get_channel(p.channel_id), "category", None) is cat)
        if best is None or value > best[1]:
            best = (g, value)
    return best

async def _maybe_relieve_overflow(self, guild, hunt, round_data):
    """Best-effort: before a create would overflow, auto-extract a channel-home
    meta to relieve pressure (Plan 4). Any failure falls through to the normal
    split / hard-fail path. At most one extraction; non-recursive."""
    if not category_layout.AUTO_EXTRACT_ON_OVERFLOW or round_data is None:
        return
    primary = category_layout.primary_category(guild, round_data)
    if primary is None:
        # Case A: round_data is itself a channel-home meta — extract it if its
        # host category is at the soft cap (otherwise the create fits).
        host = category_layout._home_category(guild, round_data)
        if (host is None
                or category_layout.puzzle_count(host) + 1
                    <= category_layout.CATEGORY_SOFT_LIMIT):
            return
        try:
            await self.extract_target_to_category(guild, hunt, round_data)
        except RuntimeError:
            logger.info("auto-extract (Case A) skipped for meta %s",
                        getattr(round_data, "id", "?"))
        return
    # Case B: round owns a real category. Relieve only a single (non-split)
    # category about to overflow; a split round already allocates overflow.
    if category_layout.is_split(round_data, TagGroupCategoryDb):
        return
    if (category_layout.puzzle_count(primary) + 1
            <= category_layout.CATEGORY_SOFT_LIMIT):
        return
    best = self._highest_value_nested_meta(guild, hunt, primary)
    if best is None or best[1] < category_layout.EXTRACT_MIN_VALUE:
        return
    try:
        await self.extract_target_to_category(guild, hunt, best[0])
    except RuntimeError:
        logger.info("auto-extract (Case B) skipped for meta %s",
                    getattr(best[0], "id", "?"))
```

(`select_extract_puzzles`, `category_layout`, `PuzzleJsonDb`, `TagGroupJsonDb`, `TagGroupCategoryDb`, `logger` already available — verify.)

- [ ] **Step 5: Run, confirm the 7 tests PASS.** Align mock/assertion details to the real API without weakening assertions.

- [ ] **Step 6: Gates**
```bash
timeout 180 pipenv run basedpyright bot/core/puzzle_service.py bot/core/category_layout.py > /tmp/pyright.log 2>&1
timeout 120 pipenv run ruff check bot/core/puzzle_service.py bot/core/category_layout.py tests/test_auto_extract_service.py > /tmp/ruff.log 2>&1
```
Expect basedpyright 0, ruff clean. `PuzzleJsonDb.get_by_attr(id=...)`/`guild.get_channel(...)` can return None — the `target_puzzle is None` / `home is None` guards handle it; keep them.

- [ ] **Step 7: Commit**
```bash
git add bot/core/category_layout.py bot/core/puzzle_service.py tests/test_auto_extract_service.py
git commit -m "feat(auto-extract): overflow-relief decision logic (Case A/B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Wire it into `create_puzzle`

**Files:** Modify `bot/core/puzzle_service.py`; Test `tests/test_auto_extract_service.py` (add one wiring test).

- [ ] **Step 1: Add the wiring test** to `tests/test_auto_extract_service.py`:

```python
async def test_create_puzzle_calls_relieve_overflow_before_placing(monkeypatch):
    """create_puzzle invokes _maybe_relieve_overflow before the capacity probe."""
    import bot.core.puzzle_service as ps
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1; hunt.layout_mode = "channel"
    hunt.url = None
    round_group = _group(20, "Metapuzzle", 3, 900)
    calls = []
    svc._maybe_relieve_overflow = AsyncMock(side_effect=lambda *a, **k: calls.append("relieve"))
    with patch.object(ps.category_layout, "pick_unsolved_category",
                      AsyncMock(side_effect=lambda *a, **k: calls.append("pick") or MagicMock())), \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(), False))), \
         patch("bot.core.puzzle_service.TagGroupCategoryDb"), \
         patch.object(svc, "_gsheet_cog", return_value=None):
        mpdb.check_duplicates_in_hunt.return_value = False
        mpdb.commit.side_effect = lambda p: setattr(p, "id", 7)
        try:
            await svc.create_puzzle(guild, hunt, round_group, "new-feeder", is_meta=False)
        except Exception:
            pass  # downstream mocking is incomplete; we only assert ordering below
    assert calls and calls[0] == "relieve"
    assert "pick" in calls  # a pick happened after the relief hook
```

Note: `create_puzzle` is heavily mocked; this test only pins that `_maybe_relieve_overflow` runs BEFORE the first `pick_unsolved_category`. If the surrounding mocks make the body raise after that point, the ordering assertion still holds (it's checked post-hoc). Adjust the patched names to match the real `create_puzzle` dependencies you observe.

- [ ] **Step 2: Wire the call** into `create_puzzle` — insert immediately BEFORE the early capacity probe (the first `pick_unsolved_category` block, ~L2269), so both Case A (which otherwise hard-fails at the probe) and Case B are relieved before either pick:

```python
        # Auto-extract a channel-home meta if this create would otherwise
        # overflow (Plan 4). Best-effort; mutates round_data.category_id on a
        # Case-A extraction so the picks below see a real category.
        await self._maybe_relieve_overflow(guild, hunt, round_data)
```

Place it after the guild-ceiling / duplicate checks but before the `if round_data is not None: ... pick_unsolved_category` probe. Do not otherwise change the placement flow.

- [ ] **Step 3: Run tests + gates**
```bash
timeout 300 pipenv run python -m pytest tests/test_auto_extract_service.py tests/test_create_service.py tests/test_extract_service.py -q > /tmp/test.log 2>&1
timeout 180 pipenv run basedpyright bot/core/puzzle_service.py > /tmp/pyright.log 2>&1
timeout 120 pipenv run ruff check bot/core/puzzle_service.py tests/test_auto_extract_service.py > /tmp/ruff.log 2>&1
```
Expect all pass. If `test_create_service` regresses, the inserted call disturbed the flow — read `/tmp/test.log` (the hook is a no-op when the feature can't apply, so existing create tests should be unaffected; if a test asserts exact call sequences, update it to expect the leading relief hook without weakening its intent).

- [ ] **Step 4: Commit**
```bash
git add bot/core/puzzle_service.py tests/test_auto_extract_service.py
git commit -m "feat(auto-extract): relieve overflow before placement in create_puzzle

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Documentation

**Files:** Modify `docs/commands.md` (or the layout/notes section) and `docs/user-guide.md`.

- [ ] **Step 1:** Add a short note (in the maintenance/notes area of `docs/commands.md`, and near the extract/consolidate discussion in `docs/user-guide.md`) explaining that when a category fills up, the bot **automatically** gives a promoted-in-place meta its own category (Case A: the meta you're adding to; Case B: the fullest nested meta in the round) instead of failing or splitting — the automatic counterpart to `!extract_target_to_category`. Mention it's bounded (one category at a time, only when it frees more than one channel) and tunable via the `AUTO_EXTRACT_ON_OVERFLOW` / `EXTRACT_MIN_VALUE` constants in `bot/core/category_layout.py`.

- [ ] **Step 2: Commit**
```bash
git add docs/commands.md docs/user-guide.md
git commit -m "docs: document auto-extract on overflow

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Verification

- [ ] Full suite: `timeout 400 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` — no new failures.
- [ ] Type gate: `timeout 300 pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1` — 0 errors.
- [ ] Lint gate: `timeout 180 pipenv run ruff check > /tmp/ruff.log 2>&1` — clean.
- [ ] Manual smoke (optional, live): promote a puzzle to a meta in a busy category, add feeders until the category is at the cap, add one more to the meta → the meta auto-extracts into its own category (Case A). Fill a round's own category with a fat nested meta present → adding to the round auto-extracts that nested meta (Case B) rather than splitting.

## Design bounds (by construction)

- **One extraction per create**, non-recursive (`_maybe_relieve_overflow` extracts at most once; `extract_target_to_category` never calls `create_puzzle`).
- **Case B only relieves a single, non-split category** — a split round already allocates overflow via the normal machinery; auto-extract targets the mixed-category-at-cap situation.
- **Backstop:** at `MAX_CATEGORIES` the extract raises `no_category_space`, which is caught and swallowed → today's behaviour (Case A hard-fail, Case B split) applies; auto-extract never consumes the last category slot silently (the extract's own guard fails first).
- **`EXTRACT_MIN_VALUE = 2`** — extraction must free >1 channel to be worth a category (value 1 = the meta's own channel only). Tunable.

## Out of scope

- No web surface (automatic behaviour).
- Case B on split rounds (left to the existing split/overflow machinery).
- Reactive re-evaluation across concurrent creates beyond the existing double-pick.

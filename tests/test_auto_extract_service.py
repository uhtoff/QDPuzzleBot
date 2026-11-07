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


async def test_case_a_extracts_channel_home_meta_when_host_full():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    meta = _group(10, "Metapuzzle", 1, 801)
    host = MagicMock(); host.id = 900
    svc.extract_target_to_category = AsyncMock(return_value=MagicMock())
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = None
        mcl._home_category.return_value = host
        mcl.puzzle_count.return_value = 48
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
        mcl.puzzle_count.return_value = 10
        await svc._maybe_relieve_overflow(guild, hunt, meta)
    svc.extract_target_to_category.assert_not_awaited()


async def test_case_b_extracts_highest_value_nested_meta():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    round_group = _group(20, "Metapuzzle", 3, 900)
    primary = MagicMock(); primary.id = 900
    big = _group(11, "Metapuzzle", 100, 801)
    svc.extract_target_to_category = AsyncMock(return_value=MagicMock())
    svc._highest_value_nested_meta = MagicMock(return_value=(big, 3))
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = primary
        mcl.is_split.return_value = False
        mcl.puzzle_count.return_value = 48
        await svc._maybe_relieve_overflow(guild, hunt, round_group)
    svc.extract_target_to_category.assert_awaited_once_with(guild, hunt, big)


async def test_general_overflow_extracts_nested_meta():
    """Unassigned-puzzle overflow (round_data=None) must relieve the hunt's
    general category the same way a round does — extract the highest-value
    nested channel-home meta instead of only ever splitting. Regression: the
    guard used to bail on `round_data is None`, so general never auto-extracted."""
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1; hunt.category_id = 900
    primary = MagicMock(); primary.id = 900
    big = _group(11, "Metapuzzle", 100, 801)
    svc.extract_target_to_category = AsyncMock(return_value=MagicMock())
    svc._highest_value_nested_meta = MagicMock(return_value=(big, 3))
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = primary
        mcl.is_split.return_value = False
        mcl.puzzle_count.return_value = 48
        await svc._maybe_relieve_overflow(guild, hunt, None)
    svc.extract_target_to_category.assert_awaited_once_with(guild, hunt, big)


async def test_general_overflow_noop_when_already_split():
    """If the general category is already split, let the split layout handle it
    (mirrors the round path's is_split short-circuit) — don't also extract."""
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1; hunt.category_id = 900
    primary = MagicMock(); primary.id = 900
    svc.extract_target_to_category = AsyncMock()
    svc._highest_value_nested_meta = MagicMock(return_value=(_group(11, "Metapuzzle", 100, 801), 3))
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.AUTO_EXTRACT_ON_OVERFLOW = True
        mcl.EXTRACT_MIN_VALUE = 2
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.primary_category.return_value = primary
        mcl.is_split.return_value = True
        mcl.puzzle_count.return_value = 48
        await svc._maybe_relieve_overflow(guild, hunt, None)
    svc.extract_target_to_category.assert_not_awaited()


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
        await svc._maybe_relieve_overflow(guild, hunt, meta)
    svc.extract_target_to_category.assert_awaited_once()


def test_highest_value_nested_meta_picks_bigger():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    cat = MagicMock(); cat.id = 900
    big = _group(11, "Metapuzzle", 100, 801)
    small = _group(12, "Metapuzzle", 101, 802)
    round_group = _group(20, "Metapuzzle", 3, 900)
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
        mcl.primary_category.side_effect = lambda g, grp: None
        mrdb.get_all.return_value = [round_group, big, small]
        def _members(gid):
            return {11: [t100, f_big1, f_big2], 12: [t101, f_small]}.get(gid, [])
        mpdb.get_all_from_tag_group.side_effect = _members
        mpdb.get_by_attr.side_effect = lambda **kw: {100: t100, 101: t101}.get(kw.get("id"))
        best = svc._highest_value_nested_meta(guild, hunt, cat)
    assert best is not None
    meta, value = best
    assert meta.id == 11 and value == 3


async def test_create_puzzle_calls_relieve_overflow_before_placing():
    """create_puzzle invokes _maybe_relieve_overflow before the capacity probe."""
    import bot.core.puzzle_service as ps
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    hunt.url = None
    round_group = _group(20, "Metapuzzle", 3, 900)
    calls = []
    svc._maybe_relieve_overflow = AsyncMock(side_effect=lambda *a, **k: calls.append("relieve"))

    def _pick(*a, **k):
        calls.append("pick")
        return MagicMock()

    with patch.object(ps.category_layout, "pick_unsolved_category", AsyncMock(side_effect=_pick)), \
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
            pass  # downstream mocking may be incomplete; we assert ordering post-hoc
    assert calls, "neither hook nor pick ran"
    assert calls[0] == "relieve", f"expected relieve first, got {calls}"
    assert "pick" in calls

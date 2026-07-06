import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData, EDGE_FEEDS, EDGE_TARGET


def _puzzle(pid, channel_id, edges, status="unsolved"):
    p = PuzzleData(name=f"puzzle-{pid}")
    p.id = pid
    p.channel_id = channel_id
    p.status = status
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _make_channel(cid, source_category):
    ch = MagicMock()
    ch.id = cid
    ch.category = source_category
    ch.edit = AsyncMock()
    return ch


def _fresh_lock(monkeypatch):
    import bot.core.category_layout as cl
    monkeypatch.setattr(cl, "layout_lock", asyncio.Lock())


@pytest.fixture
def env(monkeypatch):
    _fresh_lock(monkeypatch)

    hunt = MagicMock()
    hunt.id = 500
    hunt.category_id = 700
    hunt.name = "Test Hunt"

    meta = TagGroupData(name="spooky-meta")
    meta.id = 10
    meta.type = "Metapuzzle"
    meta.target_puzzle_id = 1
    meta.category_id = 801  # channel-home: points at the target puzzle's channel

    # host category the meta currently shares
    host = MagicMock(); host.id = 900; host.position = 3
    template = MagicMock(); template.id = 700; template.position = 0
    template.clone = AsyncMock()
    new_cat = MagicMock(); new_cat.id = 950; new_cat.name = "spooky-meta"
    new_cat.edit = AsyncMock()
    template.clone.return_value = new_cat

    guild = MagicMock()
    guild.categories = [template, host]

    # channels: target (801) + exclusive feeder (802), both currently under host
    target_ch = _make_channel(801, host)
    feeder_ch = _make_channel(802, host)
    guild.get_channel = MagicMock(side_effect=lambda cid: {801: target_ch, 802: feeder_ch}.get(cid))

    target = _puzzle(1, 801, [(10, EDGE_TARGET)])
    feeder = _puzzle(2, 802, [(10, EDGE_FEEDS)])

    svc = PuzzleService(MagicMock())  # __init__(self, bot, notifier=None)
    return dict(svc=svc, guild=guild, hunt=hunt, meta=meta, template=template,
                host=host, new_cat=new_cat, target=target, feeder=feeder,
                target_ch=target_ch, feeder_ch=feeder_ch)


async def test_extract_clones_from_template_and_flips_category_id(env, monkeypatch):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()), \
         patch("bot.core.puzzle_service.solve_divider_position", return_value=False), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.return_value = None  # meta does NOT own a real category
        mcl.maybe_consolidate_category = MagicMock()
        mpdb.get_by_attr.return_value = env["target"]
        mpdb.get_all_from_tag_group.return_value = [env["target"], env["feeder"]]
        mrdb.get_all.return_value = [meta]

        new_cat = await svc.extract_target_to_category(guild, hunt, meta)

    env["template"].clone.assert_awaited_once()
    assert new_cat is env["new_cat"]
    assert meta.category_id == env["new_cat"].id       # flipped channel-home -> category
    mrdb.commit.assert_called_with(meta)
    # both the target channel and the exclusive feeder channel were reparented
    env["target_ch"].edit.assert_awaited()
    env["feeder_ch"].edit.assert_awaited()
    moved_to = {c.kwargs.get("category") for c in env["target_ch"].edit.await_args_list}
    assert env["new_cat"] in moved_to


async def test_extract_triggers_consolidation_on_vacated_source(env):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.get_or_create_channel", AsyncMock()), \
         patch("bot.core.puzzle_service.solve_divider_position", return_value=False), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        # None for the meta's own home; the host category for the vacated-source lookup
        mcl._category_by_id.side_effect = lambda g, cid: None if cid == meta.category_id else env["host"]
        mcl.maybe_consolidate_category = MagicMock()
        mpdb.get_by_attr.return_value = env["target"]
        mpdb.get_all_from_tag_group.return_value = [env["target"], env["feeder"]]
        mrdb.get_all.return_value = [meta]

        await svc.extract_target_to_category(guild, hunt, meta)

    mcl.maybe_consolidate_category.assert_called()  # host category consolidated


async def test_extract_refuses_when_already_owns_category(env):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.return_value = MagicMock()  # already a real category
        with pytest.raises(RuntimeError, match="already_own_category"):
            await svc.extract_target_to_category(guild, hunt, meta)


async def test_extract_refuses_at_category_cap(env):
    svc, guild, hunt, meta = env["svc"], env["guild"], env["hunt"], env["meta"]
    guild.categories = [MagicMock() for _ in range(50)]  # == MAX_CATEGORIES
    with patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.return_value = None
        with pytest.raises(RuntimeError, match="no_category_space"):
            await svc.extract_target_to_category(guild, hunt, meta)

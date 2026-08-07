import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.discord_channels import SOLVE_DIVIDER
from bot.core.puzzle_service import PuzzleService
from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import TagGroupData, EDGE_FEEDS, EDGE_TARGET


def _fresh_lock(monkeypatch):
    import bot.core.category_layout as cl
    monkeypatch.setattr(cl, "layout_lock", asyncio.Lock())


def _puzzle(pid, channel_id, edges, status="unsolved"):
    p = PuzzleData(name=f"p{pid}")
    p.id = pid
    p.channel_id = channel_id
    p.status = status
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _chan(cid):
    ch = MagicMock(); ch.id = cid; ch.edit = AsyncMock(); ch.delete = AsyncMock()
    ch.name = f"chan-{cid}"
    return ch


@pytest.fixture
def env(monkeypatch):
    _fresh_lock(monkeypatch)
    hunt = MagicMock(); hunt.id = 500
    meta = TagGroupData(name="spooky-meta"); meta.id = 10
    meta.type = "Metapuzzle"; meta.target_puzzle_id = 1; meta.category_id = 900
    dest = TagGroupData(name="round-a"); dest.id = 20
    dest.type = "Metapuzzle"; dest.target_puzzle_id = 3; dest.category_id = 800
    owned_cat = MagicMock(); owned_cat.id = 900
    target_ch = _chan(801); feeder_ch = _chan(802)
    owned_cat.channels = [target_ch, feeder_ch]
    owned_cat.delete = AsyncMock()
    dest_cat = MagicMock(); dest_cat.id = 800; dest_cat.channels = []
    guild = MagicMock(); guild.categories = [owned_cat, dest_cat]
    guild.get_channel = MagicMock(side_effect=lambda cid: {801: target_ch, 802: feeder_ch}.get(cid))
    target = _puzzle(1, 801, [(10, EDGE_TARGET)])
    target.is_target = True
    feeder = _puzzle(2, 802, [(10, EDGE_FEEDS)])
    svc = PuzzleService(MagicMock())
    svc._refresh_channel_glyph = AsyncMock()
    return dict(svc=svc, guild=guild, hunt=hunt, meta=meta, dest=dest,
                owned_cat=owned_cat, dest_cat=dest_cat, target=target, feeder=feeder,
                target_ch=target_ch, feeder_ch=feeder_ch)


async def test_consolidate_moves_channels_reverts_to_channel_home_and_deletes_category(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    target_ch, dest_cat = env["target_ch"], env["dest_cat"]

    async def _target_edit(*args, **kwargs):
        # Mirror the real helper: reparenting the target out of the source cat.
        if target_ch in env["owned_cat"].channels:
            env["owned_cat"].channels.remove(target_ch)
    target_ch.edit = AsyncMock(side_effect=_target_edit)

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.side_effect = lambda g, grp: env["owned_cat"] if grp is meta else env["dest_cat"]

        async def _move(g, owner, dao, channel, solved):
            # Mirror the real helper: reparent the channel out of the source cat.
            if channel in env["owned_cat"].channels:
                env["owned_cat"].channels.remove(channel)
            return True
        mcl.move_channel_to_owner = AsyncMock(side_effect=_move)
        mcl.puzzle_count.return_value = 0
        mcl.delete_group_categories = AsyncMock(return_value=False)  # single-category owner
        mcl.CATEGORY_SOFT_LIMIT = 48
        mpdb.get_by_attr.side_effect = lambda **kw: (
            env["target"] if kw.get("id") == 1 or kw.get("channel_id") == 801
            else env["feeder"] if kw.get("channel_id") == 802
            else None)
        await svc.consolidate_to(guild, hunt, meta, dest, force=False)
    # target is a target puzzle: routed via _place_channel_in_category, not
    # move_channel_to_owner — only the feeder goes through the owner mover.
    assert mcl.move_channel_to_owner.await_count == 1
    target_ch.edit.assert_awaited_once_with(category=dest_cat, position=0)
    svc._refresh_channel_glyph.assert_awaited_once_with(guild, hunt, env["target"])
    assert meta.category_id == env["target"].channel_id
    mrdb.commit.assert_any_call(meta)
    env["owned_cat"].delete.assert_awaited()


async def test_consolidate_raises_would_overflow_unless_forced(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    def _patches():
        p_pdb = patch("bot.core.puzzle_service.PuzzleJsonDb")
        p_rdb = patch("bot.core.puzzle_service.TagGroupJsonDb")
        p_cl = patch("bot.core.puzzle_service.category_layout")
        return p_pdb, p_rdb, p_cl
    p_pdb, p_rdb, p_cl = _patches()
    with p_pdb as mpdb, p_rdb, p_cl as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.side_effect = lambda g, grp: env["owned_cat"] if grp is meta else env["dest_cat"]
        mcl.puzzle_count.return_value = 49
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.move_channel_to_owner = AsyncMock(return_value=True)
        mcl.delete_group_categories = AsyncMock(return_value=False)
        mpdb.get_by_attr.side_effect = lambda **kw: env["target"] if kw.get("id") == 1 else None
        with pytest.raises(RuntimeError, match="would_overflow"):
            await svc.consolidate_to(guild, hunt, meta, dest, force=False)
        await svc.consolidate_to(guild, hunt, meta, dest, force=True)  # no raise


async def test_consolidate_channel_home_meta_moves_extract_set(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    meta.category_id = 801  # channel-home
    target_ch, dest_cat = env["target_ch"], env["dest_cat"]
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {800: env["dest_cat"]}.get(cid)
        mcl.primary_category.side_effect = lambda g, grp: None if grp is meta else env["dest_cat"]
        mcl.puzzle_count.return_value = 0
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.is_split.return_value = False
        mcl.move_channel_to_owner = AsyncMock(return_value=True)
        mpdb.get_by_attr.side_effect = lambda **kw: env["target"] if kw.get("id") == 1 else None
        mpdb.get_all_from_tag_group.return_value = [env["target"], env["feeder"]]
        mrdb.get_all.return_value = [meta, dest]
        await svc.consolidate_to(guild, hunt, meta, dest, force=False)
    # target puzzle goes to the destination's primary category directly;
    # only the feeder is routed via move_channel_to_owner.
    assert mcl.move_channel_to_owner.await_count == 1
    target_ch.edit.assert_awaited_once_with(category=dest_cat, position=0)
    env["owned_cat"].delete.assert_not_awaited()


async def test_consolidate_solved_target_placed_below_divider_in_dest_primary(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    target_ch, dest_cat = env["target_ch"], env["dest_cat"]
    # Production state for a solved puzzle, as solve() writes it: the `solved`
    # boolean is the marker, status is title-cased. A lowercase status="solved"
    # here would satisfy a status-string check the real thing never matches.
    env["target"].solved = True
    env["target"].status = "Solved"
    divider_ch = MagicMock(); divider_ch.name = SOLVE_DIVIDER; divider_ch.position = 2
    dest_cat.channels = [divider_ch]

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: dest_cat}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.side_effect = lambda g, grp: env["owned_cat"] if grp is meta else dest_cat
        mcl.puzzle_count.return_value = 0
        mcl.delete_group_categories = AsyncMock(return_value=False)
        mcl.CATEGORY_SOFT_LIMIT = 48

        async def _move(g, owner, dao, channel, solved):
            if channel in env["owned_cat"].channels:
                env["owned_cat"].channels.remove(channel)
            return True
        mcl.move_channel_to_owner = AsyncMock(side_effect=_move)
        mpdb.get_by_attr.side_effect = lambda **kw: (
            env["target"] if kw.get("id") == 1 or kw.get("channel_id") == 801
            else env["feeder"] if kw.get("channel_id") == 802
            else None)
        await svc.consolidate_to(guild, hunt, meta, dest, force=True)
    # solved target still goes to dest's PRIMARY category (below the divider),
    # not scattered off to a separate solved/overflow category.
    target_ch.edit.assert_awaited_once_with(category=dest_cat, position=3)


async def test_consolidate_target_that_fails_to_place_is_kept(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    env["target"].is_target = True
    env["target_ch"].edit = AsyncMock(side_effect=RuntimeError("cap"))  # placement fails
    svc._refresh_channel_glyph = AsyncMock()
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.solve_divider_position", return_value=False), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.side_effect = lambda g, grp: env["owned_cat"] if grp is meta else env["dest_cat"]
        mcl.puzzle_count.return_value = 0
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.move_channel_to_owner = AsyncMock(return_value=True)
        mcl.delete_group_categories = AsyncMock(return_value=False)
        mpdb.get_by_attr.side_effect = lambda **kw: env["target"] if kw.get("id") == 1 else None
        await svc.consolidate_to(guild, hunt, meta, dest, force=True)  # must NOT raise
    env["target_ch"].delete.assert_not_awaited()   # failed-to-place target is kept, not deleted


async def test_consolidate_keeps_channel_that_fails_to_move(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = asyncio.Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: env["owned_cat"], 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.side_effect = lambda g, grp: env["owned_cat"] if grp is meta else env["dest_cat"]
        mcl.puzzle_count.return_value = 0
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.move_channel_to_owner = AsyncMock(side_effect=[True, False])  # 2nd fails
        mcl.delete_group_categories = AsyncMock(return_value=False)       # non-split fallback
        mpdb.get_by_attr.side_effect = lambda **kw: env["target"] if kw.get("id") == 1 else None
        await svc.consolidate_to(guild, hunt, meta, dest, force=True)
    # a channel that failed to move (and the category holding it) is NOT deleted
    env["feeder_ch"].delete.assert_not_awaited()
    env["owned_cat"].delete.assert_not_awaited()


async def test_consolidate_protects_channel_that_did_not_physically_move(env):
    svc, guild, hunt, meta, dest = (env[k] for k in ("svc", "guild", "hunt", "meta", "dest"))
    owned_cat = env["owned_cat"]; feeder_ch = env["feeder_ch"]
    feeder_ch.category_id = owned_cat.id      # sub-meta stays in the source category

    def _move(g, owner, dao, ch, solved):
        if ch is feeder_ch:
            return True                        # "already placed" no-op — did NOT move out
        owned_cat.channels.remove(ch)          # a real move leaves the source category
        ch.category_id = env["dest_cat"].id
        return True

    with patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch("bot.core.puzzle_service.TagGroupJsonDb"), \
         patch("bot.core.puzzle_service.category_layout") as mcl:
        mcl.layout_lock = __import__("asyncio").Lock()
        mcl._category_by_id.side_effect = lambda g, cid: {900: owned_cat, 800: env["dest_cat"]}.get(cid)
        mcl.is_split.return_value = False
        mcl.primary_category.side_effect = lambda g, grp: owned_cat if grp is meta else env["dest_cat"]
        mcl.puzzle_count.return_value = 0
        mcl.CATEGORY_SOFT_LIMIT = 48
        mcl.move_channel_to_owner = AsyncMock(side_effect=_move)
        mcl.delete_group_categories = AsyncMock(return_value=False)
        mpdb.get_by_attr.side_effect = lambda **kw: env["target"] if kw.get("id") == 1 else None
        await svc.consolidate_to(guild, hunt, meta, dest, force=True)
    # the un-moved sub-meta channel is NOT deleted (survives, orphaned at worst)
    feeder_ch.delete.assert_not_awaited()

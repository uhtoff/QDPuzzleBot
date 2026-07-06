from unittest.mock import AsyncMock, MagicMock, patch

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
        mrdb.get_by_attr.return_value = None
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
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    meta = _puzzle(2, "Fresh Meta", is_target=True)
    grp = _group(10, "Runaround", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "_is_metameta_group", return_value=False):
        mpdb.get_all_from_tag_group.return_value = [meta]
        mrdb.get_all.return_value = [grp]
        assert svc.desired_channel_name(hunt, meta, group=grp) == "🏃fresh-meta"
        mrdb.get_by_attr.assert_not_called()


def test_target_kind_metameta_beats_capstone():
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    cap = _puzzle(2, "cap", is_target=True)
    grp = _group(10, "Capstone", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb"), \
         patch.object(PuzzleService, "_is_metameta_group", return_value=True):
        mrdb.get_by_attr.return_value = grp
        assert svc._target_kind(hunt, cap) == "metameta"


def test_target_kind_runaround():
    svc = _svc()
    hunt = MagicMock(); hunt.id = 1
    ra = _puzzle(2, "run", is_target=True)
    grp = _group(10, "Runaround", 2)
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch.object(PuzzleService, "_is_metameta_group", return_value=False):
        mrdb.get_by_attr.return_value = grp
        assert svc._target_kind(hunt, ra) == "runaround"


async def test_refresh_glyph_renames_when_name_differs():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock(); hunt.id = 1
    meta = _puzzle(2, "Spooky Meta", is_target=True); meta.channel_id = 55
    channel = MagicMock(); channel.name = "spooky-meta"
    guild.get_channel_or_thread.return_value = channel
    svc.safe_rename = AsyncMock()
    with patch.object(PuzzleService, "desired_channel_name", return_value="🧩spooky-meta"):
        await svc._refresh_channel_glyph(guild, hunt, meta)
    svc.safe_rename.assert_awaited_once_with(channel, "🧩spooky-meta")


async def test_refresh_glyph_noop_when_already_correct():
    svc = _svc()
    guild = MagicMock(); hunt = MagicMock()
    meta = _puzzle(2, "Spooky Meta", is_target=True); meta.channel_id = 55
    channel = MagicMock(); channel.name = "🧩spooky-meta"
    guild.get_channel_or_thread.return_value = channel
    svc.safe_rename = AsyncMock()
    with patch.object(PuzzleService, "desired_channel_name", return_value="🧩spooky-meta"):
        await svc._refresh_channel_glyph(guild, hunt, meta)
    svc.safe_rename.assert_not_awaited()


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
    guild.get_channel_or_thread = MagicMock(side_effect=lambda cid: {500: ch1, 501: ch2}.get(cid))
    svc.safe_rename = AsyncMock()
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as mrdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as mpdb, \
         patch.object(PuzzleService, "desired_channel_name",
                      side_effect=lambda h, p, group=None: {100: "🧩meta-one", 101: "👑cap"}[p.id]):
        mrdb.get_all.return_value = [g1, g2, g_notarget]
        mpdb.get_by_attr.side_effect = lambda id: {100: t1, 101: t2}.get(id)
        renamed = await svc.backfill_glyphs(guild, hunt)
    assert renamed == 1
    svc.safe_rename.assert_awaited_once_with(ch1, "🧩meta-one")

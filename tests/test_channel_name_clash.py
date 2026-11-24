"""Channel-name clashes: a puzzle must always own its Discord channel.

Discord allows duplicate channel names, but get_or_create_channel reuses by
name, so a puzzle whose cleaned name matched an existing channel (the hunt's
own #general, another puzzle's channel, a group's chat channel) could end up
with no channel and then be relinked onto that channel by !reconcile apply.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.core.puzzle_service as ps
from bot.core.discord_channels import free_suffixed_name, text_channel_named
from bot.core.puzzle_service import PuzzleService, _kept_clash_suffix
from bot.store.puzzle_data import PuzzleData
from tests.conftest import make_hunt


def _cat(cid, *names_ids):
    chans = [SimpleNamespace(id=i, name=n, mention=f"<#{i}>") for n, i in names_ids]
    cat = SimpleNamespace(id=cid, channels=chans, text_channels=chans)
    for ch in chans:
        ch.category = cat
    return cat


# ------------------------------------------------------------------ helpers

def test_text_channel_named_finds_by_exact_name_and_honours_exclude():
    cat = _cat(1, ("general", 11), ("tide", 12))
    assert text_channel_named(cat, "general").id == 11
    assert text_channel_named(cat, "General") is None
    assert text_channel_named(cat, "tide", exclude_id=12) is None
    assert text_channel_named(None, "general") is None


def test_free_suffixed_name_takes_lowest_free_n_and_caps_length():
    cat = _cat(1, ("general", 11), ("general-1", 12), ("general-3", 13))
    assert free_suffixed_name(cat, "general") == "general-2"
    assert free_suffixed_name(cat, "tide") == "tide-1"
    long = "x" * 100
    out = free_suffixed_name(_cat(1), long)
    assert len(out) == 100 and out.endswith("-1")


@pytest.mark.parametrize("stored,clean,want", [
    ("general-1", "general", "-1"),
    ("general-12", "general", "-12"),
    ("⭐🧩-tide-2", "tide", "-2"),          # glyphed target keeps it
    ("🧩🧩-tide-1", "tide", "-1"),
    ("general", "general", ""),             # no suffix
    ("level-1", "level-1", ""),             # a clean name ending in digits
    ("level-1-2", "level-1", "-2"),
    ("barfoo-1", "foo", ""),                # not the clean name
    ("x-foo-1", "foo", ""),                 # prefix that is not a glyph
    (None, "foo", ""),
])
def test_kept_clash_suffix(stored, clean, want):
    assert _kept_clash_suffix(stored, clean) == want


def _service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot, notifier=AsyncMock())


def test_desired_channel_name_keeps_suffix_for_feeder_and_target():
    svc = _service()
    hunt = make_hunt(id=1)
    feeder = PuzzleData(name="General", hunt_id=1, channel_name="general-1")
    with patch.object(svc, "_target_kind", return_value=None):
        assert svc.desired_channel_name(None, hunt, feeder) == "general-1"
    target = PuzzleData(name="Tide", hunt_id=1, channel_name="🧩-tide-1")
    with patch.object(svc, "_target_kind", return_value="meta"), \
         patch.object(ps.category_layout, "primary_category", return_value=object()):
        # Became its category's own target: the ⭐ is added, the -1 survives.
        assert svc.desired_channel_name(None, hunt, target,
                                        group=SimpleNamespace(id=5)) == "⭐🧩-tide-1"


# ---------------------------------------------------------------- reconcile

def _puzzle(**kw):
    p = PuzzleData(name=kw.pop("name", "General"), hunt_id=1)
    p.tags = []
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _guild(*cats):
    g = MagicMock()
    g.categories = list(cats)
    g.channels = [ch for c in cats for ch in c.channels]
    g.get_channel.side_effect = lambda cid: next((c for c in g.channels if c.id == cid), None)
    return g


def test_reconcile_never_relinks_the_hunts_general_channel():
    """The live bug's second step: an unlinked puzzle "General" must not be
    relinked onto the hunt's own #general; it gets a fresh #general-1."""
    hunt_cat = _cat(900, ("general", 111))
    guild = _guild(hunt_cat)
    svc = _service()
    p = _puzzle(channel_id=None, channel_name=None)
    fresh = SimpleNamespace(id=777, name="general-1", mention="<#777>")
    with patch.object(svc, "_home_category_for_puzzle", return_value=(hunt_cat, None)), \
         patch.object(svc, "_target_kind", return_value=None), \
         patch.object(ps, "get_or_create_channel",
                      new=AsyncMock(return_value=(fresh, True))) as goc, \
         patch.object(ps, "solve_divider_position", return_value=False), \
         patch.object(ps.PuzzleJsonDb, "commit"):
        actions = asyncio.run(svc._reconcile_channel(
            guild, make_hunt(id=1, channel_id=111), p, None, apply=True,
            hunt_category_ids={900}, reserved_channel_ids=frozenset({111})))
    kinds = [a.kind for a in actions]
    assert "channel_relink" not in kinds and "channel_create" in kinds
    assert goc.await_args.kwargs["channel_name"] == "general-1"
    assert p.channel_id == 777


def test_reconcile_still_relinks_an_unused_same_named_channel():
    """The guard only skips channels in use; a genuinely stray channel of the
    right name is still relinked (today's recovery path)."""
    cat = _cat(500, ("tide", 888))
    guild = _guild(cat)
    svc = _service()
    p = _puzzle(name="Tide", channel_id=None, channel_name="tide")
    with patch.object(svc, "_home_category_for_puzzle", return_value=(cat, None)), \
         patch.object(svc, "_target_kind", return_value=None), \
         patch.object(ps.PuzzleJsonDb, "commit"):
        actions = asyncio.run(svc._reconcile_channel(
            guild, make_hunt(id=1), p, None, apply=True,
            hunt_category_ids={500}, reserved_channel_ids=frozenset({111})))
    assert [a.kind for a in actions] == ["channel_relink"]
    assert p.channel_id == 888


def test_reserved_channel_ids_covers_puzzles_hunt_general_and_group_chat():
    chat = SimpleNamespace(id=333)
    groups = [SimpleNamespace(id=5, category_id=600, has_target=False),
              SimpleNamespace(id=6, category_id=0, has_target=True)]
    puzzles = [_puzzle(channel_id=222), _puzzle(channel_id=None)]
    with patch.object(ps.category_layout, "group_chat_channel",
                      side_effect=lambda g, grp: chat if grp.id == 5 else None):
        ids = PuzzleService._reserved_channel_ids(
            MagicMock(), make_hunt(id=1, channel_id=111), puzzles, groups)
    assert {111, 222, 333}.issubset(ids)


# ------------------------------------------------------- create / rename modes

from bot.core.errors import ChannelNameClash  # noqa: E402
from bot.store.hunt_data import HuntData  # noqa: E402
from tests.conftest import make_puzzle  # noqa: E402


def _hunt_obj():
    h = HuntData()
    h.id = 10; h.google_sheet_id = "S1"; h.solved_sheet_id = "A1"
    h.url = None; h.category_id = 900; h.name = "H"; h.channel_id = 111
    return h


def _create_env(cat):
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    bot = MagicMock(); bot.get_cog.return_value = gsheet
    svc = PuzzleService(bot)
    guild = MagicMock(); guild.channels = []; guild.categories = [cat]
    goc = AsyncMock(return_value=(SimpleNamespace(id=222, mention="<#222>", name="x",
                                                  edit=AsyncMock()), True))
    return svc, gsheet, guild, goc


async def _create(svc, guild, goc, cat, mode):
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    with patch.object(ps, "PuzzleJsonDb") as pdb, \
         patch.object(ps.category_layout, "pick_unsolved_category", AsyncMock(return_value=cat)), \
         patch.object(ps, "get_or_create_channel", goc), \
         patch.object(ps, "solve_divider_position", return_value=False):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, _hunt_obj(), None, "General",
                                notifier=notifier, on_channel_clash=mode)
        return pdb


async def test_create_ask_raises_before_any_sheet_or_db_work():
    cat = _cat(900, ("general", 111))
    svc, gsheet, guild, goc = _create_env(cat)
    with pytest.raises(ChannelNameClash) as exc:
        await _create(svc, guild, goc, cat, "ask")
    assert (exc.value.channel_name, exc.value.suggested) == ("general", "general-1")
    gsheet.create_puzzle_spreadsheet.assert_not_awaited()
    goc.assert_not_awaited()


async def test_create_suffix_makes_a_fresh_suffixed_channel():
    cat = _cat(900, ("general", 111), ("general-1", 112))
    svc, gsheet, guild, goc = _create_env(cat)
    await _create(svc, guild, goc, cat, "suffix")
    assert goc.await_args.kwargs["channel_name"] == "general-2"


async def test_create_ask_without_clash_creates_normally():
    cat = _cat(900, ("tide", 5))
    svc, gsheet, guild, goc = _create_env(cat)
    await _create(svc, guild, goc, cat, "ask")
    assert goc.await_args.kwargs["channel_name"] == "general"


async def test_create_adopt_keeps_the_name_for_group_promotion():
    cat = _cat(900, ("general", 111))
    svc, gsheet, guild, goc = _create_env(cat)
    await _create(svc, guild, goc, cat, "adopt")
    assert goc.await_args.kwargs["channel_name"] == "general"


def _rename_env(*names_ids, own_id=50):
    cat = _cat(900, *names_ids)
    own = next(ch for ch in cat.text_channels if ch.id == own_id)
    guild = MagicMock(); guild.get_channel.side_effect = lambda cid: own if cid == own_id else None
    bot = MagicMock(); bot.get_cog.return_value = None
    return PuzzleService(bot, notifier=AsyncMock()), guild, own


async def test_rename_ask_raises_and_changes_nothing():
    svc, guild, own = _rename_env(("general", 111), ("tide", 50))
    puz = make_puzzle(id=70, name="Tide", channel_id=50)
    puz.channel_name = "tide"
    with patch.object(ps, "PuzzleJsonDb") as pdb, \
         patch.object(svc, "safe_rename", new_callable=AsyncMock) as rn:
        with pytest.raises(ChannelNameClash) as exc:
            await svc.rename_puzzle(guild, _hunt_obj(), puz, "General", on_channel_clash="ask")
    assert exc.value.suggested == "general-1"
    assert (puz.name, puz.channel_name) == ("Tide", "tide")
    rn.assert_not_awaited(); pdb.commit.assert_not_called()


async def test_rename_suffix_renames_channel_to_free_name():
    svc, guild, own = _rename_env(("general", 111), ("tide", 50))
    puz = make_puzzle(id=70, name="Tide", channel_id=50)
    puz.channel_name = "tide"
    with patch.object(ps, "PuzzleJsonDb"), \
         patch.object(svc, "safe_rename", new_callable=AsyncMock) as rn:
        await svc.rename_puzzle(guild, _hunt_obj(), puz, "General", on_channel_clash="suffix")
    rn.assert_awaited_once_with(own, "general-1")
    assert (puz.name, puz.channel_name) == ("General", "general-1")


async def test_rename_does_not_clash_with_its_own_channel():
    """'General' -> 'General!' cleans to the same name: the only #general-1 is
    the puzzle's own channel, so no prompt and the suffix is kept."""
    svc, guild, own = _rename_env(("general", 111), ("general-1", 50))
    puz = make_puzzle(id=70, name="General", channel_id=50)
    puz.channel_name = "general-1"
    with patch.object(ps, "PuzzleJsonDb"), \
         patch.object(svc, "safe_rename", new_callable=AsyncMock) as rn:
        await svc.rename_puzzle(guild, _hunt_obj(), puz, "General!", on_channel_clash="ask")
    rn.assert_not_awaited()
    assert puz.channel_name == "general-1"

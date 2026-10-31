"""PuzzleService.archive_hunt_to_hub: history to a new hub channel, row re-homed
to the hub, bot leaves the emptied team server. Per-channel archiving is patched
out (see test_archive_hunt_service.py for the same arrangement on archive_hunt)."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.core import hunt_archive
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _service():
    bot = MagicMock(); bot.application_id = 999
    return PuzzleService(bot)


def _hunt():
    h = HuntData()
    h.id = 7; h.uid = "abc"; h.name = "H"
    h.guild_id = 5            # the TEAM server's guilds row id
    h.category_id = 50; h.channel_id = 60
    h.role_id = 0; h.start_time = None
    return h


def _text(id):
    ch = MagicMock(spec=discord.TextChannel); ch.id = id; ch.name = f"text{id}"
    ch.threads = []; ch.edit = AsyncMock()
    return ch


def _voice(id):
    ch = MagicMock(spec=discord.VoiceChannel); ch.id = id; ch.name = f"voice{id}"
    return ch


def _team():
    """general(60) in main_cat(50); puzzle(71) + voice(72) in round_cat(70)."""
    general, puzzle, voice = _text(60), _text(71), _voice(72)
    main_cat = MagicMock(); main_cat.id = 50; main_cat.channels = [general]; main_cat.delete = AsyncMock()
    general.category = main_cat
    round_cat = MagicMock(); round_cat.id = 70; round_cat.channels = [puzzle, voice]; round_cat.delete = AsyncMock()
    puzzle.category = round_cat; voice.category = round_cat
    g = MagicMock(); g.id = 2000; g.categories = [main_cat, round_cat]
    g.get_channel.side_effect = {60: general, 71: puzzle}.get
    g.leave = AsyncMock()
    return SimpleNamespace(guild=g, general=general, puzzle=puzzle, voice=voice,
                           main_cat=main_cat, round_cat=round_cat)


@contextmanager
def _patched(svc, *, archive_side_effect=None, count=0):
    hub_dest = _text(3000)
    tg = MagicMock(); tg.id = 3; tg.category_id = 70
    with ExitStack() as st:
        p = lambda *a, **k: st.enter_context(patch(*a, **k))
        m = SimpleNamespace(
            hub=MagicMock(), hub_row=MagicMock(id=9), hub_dest=hub_dest,
            ensure_hub=p("bot.core.hunt_archive.ensure_hub_archive_channel",
                         new=AsyncMock(return_value=hub_dest)),
            tgdb=p("bot.core.puzzle_service.TagGroupJsonDb"),
            hdb=p("bot.core.puzzle_service.HuntJsonDb"),
            tgcdb=p("bot.core.puzzle_service.TagGroupCategoryDb"),
            pdb=p("bot.core.hunt_archive.PuzzleJsonDb"),
            svc_pdb=p("bot.core.puzzle_service.PuzzleJsonDb"),
            hook=p("bot.core.hunt_archive.ensure_webhook", new=AsyncMock(return_value=MagicMock())),
            archive=p("bot.core.hunt_archive.archive_and_delete_channel",
                      new=AsyncMock(side_effect=archive_side_effect,
                                    return_value=hunt_archive.ChannelArchiveResult(messages_moved=2))),
            safe_del=st.enter_context(patch.object(svc, "_safe_delete_channel", new=AsyncMock(return_value=True))),
            rename=st.enter_context(patch.object(svc, "safe_rename", new=AsyncMock())),
        )
        m.tgdb.get_all.return_value = [tg]
        m.tgcdb.get_for_group.return_value = []
        m.pdb.get_by_attr.return_value = None
        m.svc_pdb.get_all_from_hunt.return_value = [MagicMock(channel_id=71)]
        m.hdb.count_for_guild.return_value = count
        yield m


def _invoker():
    i = MagicMock(); i.display_name = "me"
    return i


async def _run(svc, hunt, t, m):
    return await svc.archive_hunt_to_hub(t.guild, hunt, _invoker(), m.hub, m.hub_row)


async def test_archives_every_channel_to_the_hub_rehomes_and_leaves():
    svc, hunt, t = _service(), _hunt(), _team()
    with _patched(svc) as m:
        report = await _run(svc, hunt, t, m)

    m.ensure_hub.assert_awaited_once_with(m.hub, hunt, "H - me")
    m.hook.assert_awaited_once_with(m.hub_dest)
    # Every text channel lands on the hub, the general one included and last.
    calls = m.archive.await_args_list
    assert [c.args[3] for c in calls] == [t.puzzle, t.general]
    assert all(c.args[1] is m.hub_dest for c in calls)
    m.safe_del.assert_any_await(t.guild, 72)
    t.round_cat.delete.assert_awaited_once()
    t.main_cat.delete.assert_awaited_once()
    t.general.edit.assert_not_awaited()      # nothing is detached and kept
    m.rename.assert_not_awaited()            # the hub channel is born with its name
    # Re-homed onto the hub; role_id untouched.
    assert (hunt.guild_id, hunt.channel_id, hunt.archived, hunt.role_id) == (9, 3000, True, 0)
    m.hdb.commit.assert_called_once_with(hunt)
    # Counted against the TEAM guild row, after the re-homing commit.
    m.hdb.count_for_guild.assert_called_once_with(5)
    names = [c[0] for c in m.hdb.mock_calls]
    assert names.index("commit") < names.index("count_for_guild")
    t.guild.leave.assert_awaited_once()
    assert report.left_guild is True
    assert report.hub_channel_id == 3000
    assert report.final_name == "H - me"
    assert report.channels_archived == 2


async def test_a_partial_run_moves_nothing_and_keeps_failed_categories():
    svc, hunt, t = _service(), _hunt(), _team()
    boom = [RuntimeError("boom"), hunt_archive.ChannelArchiveResult(messages_moved=1)]
    with _patched(svc, archive_side_effect=boom) as m:
        report = await _run(svc, hunt, t, m)
    assert report.failures == ["text71"]
    # Nothing commits: the hunt still resolves on the team server for a re-run.
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (5, 60, False)
    m.hdb.commit.assert_not_called()
    m.hdb.count_for_guild.assert_not_called()
    t.guild.leave.assert_not_awaited()       # an admin must be able to re-run
    assert report.left_guild is False
    # The failed channel's category survives, so the channel isn't orphaned.
    t.round_cat.delete.assert_not_awaited()
    # General (and so its category) is kept for the re-run: it goes only last,
    # once everything else is clean.
    t.main_cat.delete.assert_not_awaited()
    assert t.general not in [c.args[3] for c in m.archive.await_args_list]


async def test_a_failed_voice_delete_keeps_general_for_the_rerun():
    svc, hunt, t = _service(), _hunt(), _team()
    with _patched(svc) as m:
        m.safe_del.side_effect = lambda guild, cid: {72: False}.get(cid, True)
        report = await _run(svc, hunt, t, m)
    assert report.failures == ["voice72"]
    assert [c.args[3] for c in m.archive.await_args_list] == [t.puzzle]
    t.main_cat.delete.assert_not_awaited()
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (5, 60, False)
    m.hdb.commit.assert_not_called()
    t.guild.leave.assert_not_awaited()


async def test_a_failed_general_archive_commits_nothing():
    svc, hunt, t = _service(), _hunt(), _team()

    async def archive(bot, dest, webhook, ch, **kw):
        if ch is t.general:
            raise RuntimeError("boom")
        return hunt_archive.ChannelArchiveResult(messages_moved=1)

    with _patched(svc, archive_side_effect=archive) as m:
        report = await _run(svc, hunt, t, m)
    assert report.failures == ["text60"]
    m.hdb.commit.assert_not_called()
    t.guild.leave.assert_not_awaited()
    t.main_cat.delete.assert_not_awaited()


async def test_a_failed_general_category_delete_does_not_block_the_rehome():
    svc, hunt, t = _service(), _hunt(), _team()
    t.main_cat.delete = AsyncMock(side_effect=discord.HTTPException(
        MagicMock(status=500, reason="x"), "boom"))
    with _patched(svc) as m:
        report = await _run(svc, hunt, t, m)
    assert report.failures == []
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (9, 3000, True)
    t.guild.leave.assert_awaited_once()


async def test_a_rerun_archives_an_orphaned_puzzle_channel_then_rehomes():
    svc, hunt, t = _service(), _hunt(), _team()
    t.guild.categories = []
    t.puzzle.category = None
    t.guild.get_channel.side_effect = {71: t.puzzle}.get   # general already gone
    with _patched(svc) as m:
        m.tgdb.get_all.return_value = []
        report = await _run(svc, hunt, t, m)
    assert [c.args[3] for c in m.archive.await_args_list] == [t.puzzle]
    assert report.failures == []
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (9, 3000, True)
    t.guild.leave.assert_awaited_once()


async def test_an_uncategorised_general_channel_is_archived():
    svc, hunt, t = _service(), _hunt(), _team()
    t.general.category = None
    t.main_cat.channels = []
    with _patched(svc) as m:
        await _run(svc, hunt, t, m)
    assert t.general in [c.args[3] for c in m.archive.await_args_list]


async def test_another_hunt_on_the_team_server_keeps_the_bot_there():
    svc, hunt, t = _service(), _hunt(), _team()
    with _patched(svc, count=1) as m:
        report = await _run(svc, hunt, t, m)
    t.guild.leave.assert_not_awaited()
    assert report.left_guild is False


async def _resume(complete):
    """_archive_channel_into on a channel whose thread already exists on dest."""
    svc, t = _service(), _team()
    dest = _text(3000)
    thread = MagicMock(); thread.name = "text71 (round)"
    report = hunt_archive.HuntArchiveReport()
    with _patched(svc) as m, \
         patch("bot.core.hunt_archive.archive_thread_state",
               new=AsyncMock(return_value=(thread, complete))) as state, \
         patch("bot.core.hunt_archive.discard_partial_thread", new=AsyncMock()) as discard, \
         patch("bot.core.hunt_archive.thread_name_for_channel", return_value="text71 (round)"):
        await svc._archive_channel_into(t.guild, dest, MagicMock(), t.puzzle, report)
    state.assert_awaited_once_with(dest, "text71 (round)")
    return SimpleNamespace(m=m, t=t, report=report, discard=discard, thread=thread, dest=dest)


async def test_resume_finishes_a_completely_archived_channel_without_recopying():
    r = await _resume(complete=True)
    r.m.safe_del.assert_awaited_once_with(r.t.guild, 71)
    r.m.archive.assert_not_awaited()
    r.discard.assert_not_awaited()
    assert (r.report.channels_skipped, r.report.channels_archived, r.report.failures) == (1, 0, [])


async def test_resume_discards_a_partial_thread_and_recopies_the_channel():
    r = await _resume(complete=False)
    r.discard.assert_awaited_once_with(r.dest, r.thread)
    r.m.archive.assert_awaited_once()
    assert r.m.archive.await_args.args[3] is r.t.puzzle
    r.m.safe_del.assert_not_awaited()
    assert (r.report.channels_skipped, r.report.channels_archived, r.report.failures) == (0, 1, [])


async def test_general_in_a_non_hunt_category_leaves_that_category_alone():
    svc, hunt, t = _service(), _hunt(), _team()
    other = MagicMock(); other.id = 99; other.channels = [t.general]; other.delete = AsyncMock()
    t.general.category = other
    t.main_cat.channels = []
    # Resolvable on the guild (so only the guard can spare it), but not a hunt category.
    t.guild.categories = [t.main_cat, t.round_cat, other]
    with _patched(svc) as m:
        report = await _run(svc, hunt, t, m)
    assert t.general in [c.args[3] for c in m.archive.await_args_list]
    other.delete.assert_not_awaited()
    assert report.failures == []
    assert (hunt.guild_id, hunt.channel_id, hunt.archived) == (9, 3000, True)
    t.guild.leave.assert_awaited_once()


async def test_archive_to_hub_deletes_the_mod_invite_before_leaving():
    svc, hunt, t = _service(), _hunt(), _team()
    order = []
    t.guild.leave = AsyncMock(side_effect=lambda: order.append("leave"))
    with _patched(svc) as m, patch(
            "bot.core.puzzle_service.mod_invite.delete",
            new=AsyncMock(side_effect=lambda *a: order.append("delete"))) as delete:
        await _run(svc, hunt, t, m)
    delete.assert_awaited_once_with(t.guild, hunt)
    assert order == ["delete", "leave"]


async def test_archive_to_hub_keeps_the_mod_invite_when_the_bot_stays():
    svc, hunt, t = _service(), _hunt(), _team()
    with _patched(svc, count=1) as m, patch(
            "bot.core.puzzle_service.mod_invite.delete", new=AsyncMock()) as delete:
        await _run(svc, hunt, t, m)
    delete.assert_not_awaited()

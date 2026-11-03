"""Multi-team hunts: condensing the hub channels at the end of the hunt
(on_team_archived, archive_multi_team) and the Archive now entrypoints."""
import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.core import hub_archive_run, hunt_archive, multi_team
from bot.core.multi_team import MultiTeamRefused
from tests.conftest import make_cog, make_ctx, make_web_app, start_patches, stop_patches
from tests.test_multi_team_core import HUB_ID, MT_ID, FakeMtDb, _hub, _member, _role

NS_ID, FIN_ID = 801, 802
CREATED = datetime.datetime(2027, 1, 15)


def _live(cid, topic):
    ch = MagicMock()
    ch.id = cid
    ch.name = f"live-{cid}"
    ch.topic = topic
    ch.delete = AsyncMock()
    return ch


class ArchDb(FakeMtDb):
    def __init__(self, members, archived_time=None):
        super().__init__(members=members)
        self.archived_time = archived_time
        self.marked = []

    def get(self, mt_id):
        row = super().get(mt_id)
        row.update(created_at=CREATED, archived_time=self.archived_time,
                   no_spoiler_channel_id=NS_ID, finishers_channel_id=FIN_ID)
        return row

    def for_hunt(self, hunt_id):
        return {"multi_team_id": MT_ID, "name": self.name, "hub_guild_id": HUB_ID,
                "archived_time": self.archived_time, "completed_time": None}

    def mark_archived(self, mt_id):
        self.marked.append(mt_id)


def _hunt(**kw):
    d = dict(id=1, name="Team A Hunt", roster_role_id=10, archived=True)
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture
def env():
    ns = _live(NS_ID, multi_team.no_spoiler_marker(MT_ID))
    fin = _live(FIN_ID, multi_team.finishers_marker(MT_ID))
    role = _role(10, "Team A")
    hub = _hub({10: role}, existing=[ns, fin])
    hub.get_channel.side_effect = lambda cid: {NS_ID: ns, FIN_ID: fin}.get(cid)
    role.guild = hub
    dest = MagicMock()
    dest.name = "2027-01 Big Hunt - all teams"
    calls = []

    async def archive_and_delete(bot, d, webhook, source, *, thread_name):
        calls.append((d, source, thread_name))
        await source.delete()
        return hunt_archive.ChannelArchiveResult(messages_moved=5)

    db = ArchDb([{**_member(1, 10), "archived": 1}, {**_member(2, 11), "archived": 1}])
    patches = [
        patch("bot.core.multi_team.MultiTeamDb", db),
        patch("bot.core.multi_team.hunt_archive.ensure_tagged_channel",
              AsyncMock(return_value=dest)),
        patch("bot.core.multi_team.hunt_archive.ensure_webhook", AsyncMock(return_value="hook")),
        patch("bot.core.multi_team.hunt_archive.archive_and_delete_channel",
              side_effect=archive_and_delete),
        patch("bot.core.multi_team.role_panel.find_roster_role", return_value=role),
        patch("bot.core.multi_team.hub_channel.post", AsyncMock()),
        patch("bot.core.multi_team.set_complete", AsyncMock()),
    ]
    mocks = start_patches(patches)
    try:
        yield SimpleNamespace(hub=hub, db=db, ns=ns, fin=fin, dest=dest, calls=calls,
                              bot=MagicMock(), ensure=mocks[1], post=mocks[5], complete=mocks[6])
    finally:
        stop_patches(patches)


# ------------------------------------------------------------ archive_multi_team
async def test_archive_condenses_both_channels_into_one(env):
    summary = await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert env.ensure.await_args.args == (
        env.hub, multi_team.archive_marker(MT_ID), "2027-01 Big Hunt - all teams")
    # no-spoiler first, finishers second, into the same destination
    assert [(d, s, t) for d, s, t in env.calls] == [
        (env.dest, env.ns, "No spoilers"), (env.dest, env.fin, "Finishers")]
    env.ns.delete.assert_awaited_once()
    env.fin.delete.assert_awaited_once()
    assert env.db.marked == [MT_ID]
    assert summary == (":white_check_mark: Archived the multi-team hunt **Big Hunt** into "
                       "**2027-01 Big Hunt - all teams** — 2 channel(s), 10 message(s) moved.")


async def test_archive_skips_a_hand_deleted_live_channel(env):
    env.hub.text_channels.remove(env.fin)
    env.hub.get_channel.side_effect = lambda cid: {NS_ID: env.ns}.get(cid)
    summary = await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert [t for _, _, t in env.calls] == ["No spoilers"]
    assert env.db.marked == [MT_ID]
    assert "1 channel(s), 5 message(s)" in summary


async def test_archive_finds_a_channel_by_marker_when_the_stored_id_is_stale(env):
    env.hub.get_channel.side_effect = lambda cid: None
    await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert [t for _, _, t in env.calls] == ["No spoilers", "Finishers"]


async def test_archive_with_both_channels_gone_still_marks_archived(env):
    env.hub.text_channels.clear()
    env.hub.get_channel.side_effect = lambda cid: None
    summary = await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert env.calls == []
    env.ensure.assert_not_awaited()
    assert env.db.marked == [MT_ID]
    assert summary.startswith(":white_check_mark:")


async def test_archive_of_an_already_archived_row_does_nothing(env):
    env.db.archived_time = datetime.datetime(2027, 2, 1)
    summary = await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert summary == "The multi-team hunt **Big Hunt** is already archived."
    assert env.calls == []
    env.ensure.assert_not_awaited()
    assert env.db.marked == []


async def test_archive_failure_is_logged_returned_and_not_marked(env):
    with patch("bot.core.multi_team.hunt_archive.archive_and_delete_channel",
               AsyncMock(side_effect=RuntimeError("boom secret"))):
        summary = await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert env.db.marked == []
    assert summary == (":x: Archiving the multi-team hunt **Big Hunt** failed — the error "
                       "has been logged. Use Archive now to retry.")
    assert "boom" not in summary


async def test_archive_holds_the_ensure_lock(env):
    lock = multi_team._ENSURE_LOCKS.setdefault(MT_ID, asyncio.Lock())
    seen = []

    async def spy(*a, **k):
        seen.append(lock.locked())
        return env.dest
    env.ensure.side_effect = spy
    await multi_team.archive_multi_team(env.bot, env.hub, MT_ID)
    assert seen == [True]
    assert not lock.locked()


# ------------------------------------------------------------ recreation guards
async def test_ensure_channels_refuses_an_archived_multi_team_hunt(env):
    env.db.archived_time = datetime.datetime(2027, 2, 1)
    with pytest.raises(MultiTeamRefused, match="already archived"):
        await multi_team.ensure_channels(env.hub, MT_ID)
    env.hub.create_text_channel.assert_not_awaited()


async def test_setup_already_linked_skips_ensure_channels_when_archived():
    from tests.test_multi_team_setup import MT
    mt = MT(linked={"multi_team_id": 3, "name": "GPH 2026",
                    "archived_time": datetime.datetime(2027, 2, 1)})
    report = await mt.run(multi_team=False)
    assert report.error == ""
    assert "Already part of multi-team hunt **GPH 2026**." in report.steps
    mt.ensure.assert_not_awaited()
    mt.gdb.mark_setup_complete.assert_called_once()


async def test_setup_already_linked_ensures_channels_when_running():
    from tests.test_multi_team_setup import MT
    mt = MT(linked={"multi_team_id": 3, "name": "GPH 2026", "archived_time": None})
    await mt.run(multi_team=False)
    mt.ensure.assert_awaited_once_with(mt.hub, 3)


# ----------------------------------------------------------------- on_team_archived
@pytest.fixture
def team(env):
    """on_team_archived with the real archive_multi_team replaced by a spy."""
    spy = AsyncMock(return_value="SUMMARY")
    with patch("bot.core.multi_team.archive_multi_team", spy):
        env.archive = spy
        yield env


async def test_last_team_archived_marks_complete_archives_and_posts(team):
    await multi_team.on_team_archived(team.bot, _hunt())
    team.complete.assert_awaited_once()
    assert team.complete.await_args.args[2] is True
    team.archive.assert_awaited_once_with(team.bot, team.hub, MT_ID)
    team.post.assert_awaited_once()
    assert team.post.await_args.args == (team.hub, "SUMMARY")
    assert team.post.await_args.kwargs["allowed_mentions"].everyone is False


async def test_last_team_archived_condenses_channels_end_to_end(env):
    await multi_team.on_team_archived(env.bot, _hunt())
    assert [t for _, _, t in env.calls] == ["No spoilers", "Finishers"]
    assert env.db.marked == [MT_ID]
    assert "Archived the multi-team hunt" in env.post.await_args.args[1]


async def test_not_last_team_only_marks_complete(team):
    team.db.members_list[1]["archived"] = 0
    await multi_team.on_team_archived(team.bot, _hunt())
    team.complete.assert_awaited_once()
    team.archive.assert_not_awaited()
    team.post.assert_not_awaited()


async def test_unlinked_hunt_does_nothing(team):
    team.db.for_hunt = lambda hunt_id: None
    await multi_team.on_team_archived(team.bot, _hunt())
    team.complete.assert_not_awaited()
    team.archive.assert_not_awaited()


async def test_already_archived_multi_team_hunt_does_nothing(team):
    team.db.archived_time = datetime.datetime(2027, 2, 1)
    await multi_team.on_team_archived(team.bot, _hunt())
    team.complete.assert_not_awaited()
    team.archive.assert_not_awaited()


async def test_refused_complete_is_swallowed_and_archiving_continues(team):
    team.complete.side_effect = MultiTeamRefused("no role")
    await multi_team.on_team_archived(team.bot, _hunt())
    team.archive.assert_awaited_once()


async def test_hub_resolved_from_the_bot_when_the_role_is_gone(team):
    other_hub = MagicMock()
    team.bot.get_guild.return_value = other_hub
    with patch("bot.core.multi_team.role_panel.find_roster_role", return_value=None):
        await multi_team.on_team_archived(team.bot, _hunt())
    team.bot.get_guild.assert_called_once_with(HUB_ID)
    team.archive.assert_awaited_once_with(team.bot, other_hub, MT_ID)


async def test_nothing_propagates(team):
    team.archive.side_effect = RuntimeError("boom")
    await multi_team.on_team_archived(team.bot, _hunt())  # must not raise


# ------------------------------------------------- run_archive_to_hub integration
def _runner_args():
    h = MagicMock()
    h.id = 10; h.name = "H"; h.uid = "U1"
    report = hunt_archive.HuntArchiveReport(channels_archived=4, final_name="H - me",
                                            hub_channel_id=3000)
    svc = MagicMock(); svc.archive_hunt_to_hub = AsyncMock(return_value=report)
    ch = MagicMock(); ch.send = AsyncMock()
    hub = MagicMock(); hub.get_channel.return_value = MagicMock(send=AsyncMock())
    return h, report, svc, ch, hub


async def _run(h, svc, ch, hub, **patches):
    with patch.object(hub_archive_run, "PuzzleJsonDb") as Db, \
         patch.object(hub_archive_run.multi_team, "on_team_archived", **patches) as hook:
        Db.get_all_from_hunt.return_value = []
        bot = MagicMock()
        result = await hub_archive_run.run_archive_to_hub(
            bot, svc, MagicMock(), h, MagicMock(), hub, MagicMock(), status_channel=ch)
    return result, hook, bot


async def test_runner_calls_the_hook_after_a_clean_report():
    h, report, svc, ch, hub = _runner_args()
    result, hook, bot = await _run(h, svc, ch, hub, new=AsyncMock())
    assert result is report
    hook.assert_awaited_once_with(bot, h)


async def test_runner_skips_the_hook_when_the_report_has_failures():
    h, report, svc, ch, hub = _runner_args()
    report.failures.append("chan")
    result, hook, _ = await _run(h, svc, ch, hub, new=AsyncMock())
    assert result is report
    hook.assert_not_awaited()


async def test_a_raising_hook_leaves_the_runner_result_and_posts_unchanged():
    h, report, svc, ch, hub = _runner_args()
    hub_dest = hub.get_channel.return_value
    result, hook, _ = await _run(h, svc, ch, hub, new=AsyncMock(side_effect=RuntimeError("boom")))
    assert result is report
    hook.assert_awaited_once()
    assert "H - me" in hub_dest.send.await_args.args[0]
    assert all("failed" not in c.args[0] for c in ch.send.await_args_list)


# ------------------------------------------------------------- Archive now: command
def _no_mentions(call):
    am = call.kwargs.get("allowed_mentions")
    return isinstance(am, discord.AllowedMentions) and (am.everyone, am.users, am.roles) == (False, False, False)


ROW = {"id": MT_ID, "name": "Big Hunt", "hub_guild_id": HUB_ID, "archived_time": None}


async def test_command_archives_and_replies():
    from bot.cogs.puzzles import Puzzles
    cog, ctx = make_cog(), make_ctx()
    cog.bot = MagicMock()
    cog.check_is_bot_channel = AsyncMock(return_value=True)
    ctx.guild.id = HUB_ID
    with patch("bot.cogs.puzzles.MultiTeamDb") as db, \
         patch("bot.cogs.puzzles.multi_team.archive_multi_team",
               AsyncMock(return_value="DONE")) as arch:
        db.running_by_name.return_value = ROW
        await Puzzles.archive_multi_team.callback(cog, ctx, name="big hunt")
    db.running_by_name.assert_called_once_with(HUB_ID, "big hunt")
    arch.assert_awaited_once_with(cog.bot, ctx.guild, MT_ID)
    assert [c.args[0] for c in ctx.send.await_args_list] == ["⏳ Archiving…", "DONE"]
    assert all(_no_mentions(c) for c in ctx.send.await_args_list)


async def test_command_refuses_an_unknown_or_archived_hunt():
    from bot.cogs.puzzles import Puzzles
    cog, ctx = make_cog(), make_ctx()
    cog.check_is_bot_channel = AsyncMock(return_value=True)
    with patch("bot.cogs.puzzles.MultiTeamDb") as db, \
         patch("bot.cogs.puzzles.multi_team.archive_multi_team", AsyncMock()) as arch:
        db.running_by_name.return_value = None
        await Puzzles.archive_multi_team.callback(cog, ctx, name="Nope")
    arch.assert_not_awaited()
    assert "No running multi-team hunt called **Nope**" in ctx.send.await_args.args[0]
    assert _no_mentions(ctx.send.await_args)


async def test_command_outside_the_bot_channel_does_nothing():
    from bot.cogs.puzzles import Puzzles
    cog, ctx = make_cog(), make_ctx()
    cog.check_is_bot_channel = AsyncMock(return_value=False)
    with patch("bot.cogs.puzzles.MultiTeamDb") as db:
        await Puzzles.archive_multi_team.callback(cog, ctx, name="x")
    db.running_by_name.assert_not_called()


# --------------------------------------------------------------- Archive now: route
HUB_ROW = 1
URL = f"/api/guilds/{HUB_ROW}/multi-team/{MT_ID}/archive"


@pytest.fixture
def web():
    hub = MagicMock()
    hub.id = HUB_ID
    bot = MagicMock()
    bot.get_guild.return_value = hub
    db = MagicMock()
    db.get.return_value = dict(ROW)
    state = SimpleNamespace(require_admin=AsyncMock(return_value=None), hub=hub, db=db,
                            audit=AsyncMock(), post=AsyncMock(),
                            archive=AsyncMock(return_value="DONE"), bot=bot)
    app = make_web_app("actions", cog=MagicMock())
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.actions.require_admin", state.require_admin),
        patch("bot.web.actions.web_data.get_guilds_map", AsyncMock(return_value={
            HUB_ROW: {"guild_snowflake": HUB_ID, "guild_name": "Hub"}})),
        patch("bot.web.actions.GuildSettingsDb", MagicMock(get=MagicMock(return_value=object()))),
        patch("bot.web.actions.bot_app", return_value=SimpleNamespace(bot=bot)),
        patch("bot.web.actions.MultiTeamDb", db),
        patch("bot.web.actions._audit", state.audit),
        patch("bot.web.actions.multi_team.archive_multi_team", state.archive),
        patch("bot.core.hub_channel.post", state.post),
    ]
    start_patches(patches)
    try:
        state.client = app.test_client()
        yield state
    finally:
        stop_patches(patches)


async def _drain():
    from bot.web import actions
    await asyncio.gather(*list(actions._ARCHIVE_TASKS))


async def test_route_requires_admin(web):
    web.require_admin.return_value = ({"error": "forbidden"}, 403)
    resp = await web.client.post(URL, json={"confirm_name": "Big Hunt"})
    assert resp.status_code == 403
    web.archive.assert_not_awaited()


@pytest.mark.parametrize("row", [None, {**ROW, "hub_guild_id": HUB_ID + 1},
                                 {**ROW, "archived_time": datetime.datetime(2027, 2, 1)}])
async def test_route_unknown_foreign_or_archived_is_404(web, row):
    web.db.get.return_value = row
    resp = await web.client.post(URL, json={"confirm_name": "Big Hunt"})
    assert resp.status_code == 404
    assert (await resp.get_json())["error"] == "multi_team_not_found"
    web.archive.assert_not_awaited()


@pytest.mark.parametrize("body", [{}, {"confirm_name": "big hunt"}, {"confirm_name": "Other"}])
async def test_route_needs_the_exact_name(web, body):
    resp = await web.client.post(URL, json=body)
    assert resp.status_code == 400
    assert (await resp.get_json())["error"] == "confirm_mismatch"
    web.archive.assert_not_awaited()


async def test_route_starts_the_archive_in_the_background_and_posts_the_summary(web):
    resp = await web.client.post(URL, json={"confirm_name": "Big Hunt"})
    assert resp.status_code == 202
    assert await resp.get_json() == {"ok": True, "started": True}
    await _drain()
    web.archive.assert_awaited_once_with(web.bot, web.hub, MT_ID)
    web.post.assert_awaited_once()
    assert web.post.await_args.args == (web.hub, "DONE")
    am = web.post.await_args.kwargs["allowed_mentions"]
    assert (am.everyone, am.users, am.roles) == (False, False, False)
    assert web.audit.await_args.args[0] == "multi_team_archive"

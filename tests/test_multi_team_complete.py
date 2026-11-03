"""Multi-team hunts: marking a team complete / not complete."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import multi_team
from bot.core.multi_team import MultiTeamRefused
from tests.test_multi_team_core import (HUB_ID, MT_ID, FakeMtDb, _actual, _expect,
                                        _hub, _member, _role)

TEAM_ROLE_ID = 10


class CompleteDb(FakeMtDb):
    """FakeMtDb plus the link/completion calls set_complete makes."""

    def __init__(self, link="default", **kw):
        super().__init__(members=[_member(1, 10), _member(2, 11)], **kw)
        self.link = ({"multi_team_id": MT_ID, "completed_time": None, "name": self.name,
                      "hub_guild_id": HUB_ID, "archived_time": None}
                     if link == "default" else link)
        self.set_calls = []

    def for_hunt(self, hunt_id):
        return self.link

    def set_completed(self, hunt_id, completed):
        self.set_calls.append((hunt_id, completed))
        now_done = self.members_list[0]["completed_time"] is not None
        if now_done == completed:
            return False
        self.members_list[0]["completed_time"] = "2026-01-01" if completed else None
        return True


def _hunt(**kw):
    d = dict(id=1, name="Team A Hunt", roster_role_id=TEAM_ROLE_ID, archived=False)
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture
def env():
    roles = {10: _role(10, "Team A"), 11: _role(11, "Team B"), 90: _role(90, "Admin")}
    hub = _hub(roles)
    for r in roles.values():
        r.guild = hub
    db = CompleteDb()
    bot = MagicMock()
    with patch("bot.core.multi_team.MultiTeamDb", db), \
         patch("bot.core.multi_team.role_gate.tier_role_ids", return_value=frozenset({90})), \
         patch("bot.core.multi_team.role_panel.find_roster_role",
               side_effect=lambda b, rid: roles.get(rid)) as find:
        yield SimpleNamespace(hub=hub, db=db, bot=bot, roles=roles, find=find)


def _wire_send(env):
    orig = env.hub.create_text_channel.side_effect

    async def create(name, **kw):
        ch = await orig(name, **kw)
        ch.send = AsyncMock()
        return ch

    env.hub.create_text_channel.side_effect = create


async def test_complete_posts_once_and_adds_overwrite(env):
    _wire_send(env)
    out = await multi_team.set_complete(env.bot, _hunt(), True)
    assert out.changed is True
    fin = env.hub.created[1][0]
    assert out.message == (":white_check_mark: **Team A** marked complete — "
                           f"they can now see #{fin.name}.")
    fin.send.assert_awaited_once()
    assert fin.send.await_args.args == ("🏁 **Team A** has finished!",)
    assert fin.send.await_args.kwargs["allowed_mentions"].everyone is False
    assert fin.send.await_args.kwargs["allowed_mentions"].roles is False
    assert fin.send.await_args.kwargs["allowed_mentions"].users is False
    assert _actual(env.hub.created[1][1]["overwrites"]) == _expect(env.hub, [10])
    assert env.db.set_calls == [(1, True)]


async def test_repeat_complete_is_noop(env):
    _wire_send(env)
    await multi_team.set_complete(env.bot, _hunt(), True)
    fin = env.hub.created[1][0]
    fin.send.reset_mock()
    fin.edit.reset_mock()
    out = await multi_team.set_complete(env.bot, _hunt(), True)
    assert out.changed is False
    assert out.message == "**Team A** is already complete."
    fin.send.assert_not_awaited()
    # a retry still repairs the overwrites (an earlier hub failure may have lost them)
    assert _actual(fin.edit.await_args.kwargs["overwrites"]) == _expect(env.hub, [10])


async def test_repeat_complete_repairs_a_missing_finishers_overwrite(env):
    _wire_send(env)
    await multi_team.set_complete(env.bot, _hunt(), True)
    fin = env.hub.created[1][0]
    fin.send.reset_mock()
    fin.edit.reset_mock()
    out = await multi_team.set_complete(env.bot, _hunt(), True)
    assert out.changed is False
    fin.send.assert_not_awaited()
    fin.edit.assert_awaited_once()
    assert _actual(fin.edit.await_args.kwargs["overwrites"]) == _expect(env.hub, [10])


async def test_repeat_undo_removes_a_stale_overwrite(env):
    _wire_send(env)
    await multi_team.set_complete(env.bot, _hunt(), True)
    await multi_team.set_complete(env.bot, _hunt(), False)
    fin = env.hub.created[1][0]
    fin.send.reset_mock()
    fin.edit.reset_mock()
    out = await multi_team.set_complete(env.bot, _hunt(), False)
    assert out.changed is False
    assert out.message == "**Team A** isn't marked complete."
    fin.send.assert_not_awaited()
    assert _actual(fin.edit.await_args.kwargs["overwrites"]) == _expect(env.hub, [])


async def test_undo_removes_overwrite_without_post(env):
    _wire_send(env)
    await multi_team.set_complete(env.bot, _hunt(), True)
    fin = env.hub.created[1][0]
    fin.send.reset_mock()
    out = await multi_team.set_complete(env.bot, _hunt(), False)
    assert out.changed is True
    assert out.message == ":white_check_mark: **Team A** marked not complete."
    fin.send.assert_not_awaited()
    assert _actual(fin.edit.await_args.kwargs["overwrites"]) == _expect(env.hub, [])


async def test_undo_when_not_complete_is_noop(env):
    _wire_send(env)
    out = await multi_team.set_complete(env.bot, _hunt(), False)
    assert out.changed is False
    assert out.message == "**Team A** isn't marked complete."
    fin = env.hub.created[1][0]
    fin.send.assert_not_awaited()


async def test_recomplete_posts_again(env):
    _wire_send(env)
    await multi_team.set_complete(env.bot, _hunt(), True)
    await multi_team.set_complete(env.bot, _hunt(), False)
    await multi_team.set_complete(env.bot, _hunt(), True)
    fin = env.hub.created[1][0]
    assert fin.send.await_count == 2
    assert _actual(fin.edit.await_args.kwargs["overwrites"]) == _expect(env.hub, [10])


async def _refused(env, hunt, complete, message):
    with pytest.raises(MultiTeamRefused) as exc:
        await multi_team.set_complete(env.bot, hunt, complete)
    assert str(exc.value) == message
    assert env.db.set_calls == []
    env.hub.create_text_channel.assert_not_awaited()


async def test_refuses_unlinked_hunt(env):
    env.db.link = None
    await _refused(env, _hunt(), True, "**Team A Hunt** isn't part of a multi-team hunt.")


async def test_refuses_without_roster_role_id(env):
    await _refused(env, _hunt(roster_role_id=None), True,
                   "**Team A Hunt** has no hub role, so it can't be marked complete.")


async def test_refuses_when_role_not_found(env):
    await _refused(env, _hunt(roster_role_id=999), True,
                   "I can't find **Team A Hunt**'s hub role — is the bot still on the hub?")


async def test_refuses_when_multi_team_hunt_archived(env):
    env.db.link["archived_time"] = "2026-02-02"
    await _refused(env, _hunt(), True, "The multi-team hunt **Big Hunt** is already archived.")


async def test_refuses_undo_on_archived_hunt(env):
    await _refused(env, _hunt(archived=True), False,
                   "**Team A Hunt** is archived, so it stays complete.")

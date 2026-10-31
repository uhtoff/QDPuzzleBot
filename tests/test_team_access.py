"""Pure assembly for the Team servers page (no Quart)."""
import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.web import team_access

MOD, TEAM = 50, 60


def _role(i):
    return SimpleNamespace(id=i)


def _member(i, name, roles=(), bot=False):
    return SimpleNamespace(id=i, display_name=name, bot=bot, roles=[_role(r) for r in roles])


def _guild(members, team_role_exists=True):
    g = MagicMock()
    g.members = members
    g.get_role.side_effect = lambda i: (
        _role(i) if (i == MOD or (i == TEAM and team_role_exists)) else None)
    return g


def _members_now(guild, hunt=None):
    hunt = hunt or SimpleNamespace(role_id=TEAM)
    with patch("bot.core.role_gate.tier_role_ids", return_value={MOD}):
        return team_access.members_now(guild, hunt)


def test_members_now_sorts_mod_then_team_then_neither_and_skips_bots():
    g = _guild([
        _member(1, "zed"), _member(2, "bea", [TEAM]), _member(3, "yan", [MOD]),
        _member(4, "robo", [MOD], bot=True), _member(5, "amy"), _member(6, "cat", [TEAM]),
    ])
    out = _members_now(g)
    assert [(m["name"], m["team_role"], m["mod"]) for m in out] == [
        ("yan", False, True), ("bea", True, False), ("cat", True, False),
        ("amy", False, False), ("zed", False, False)]
    assert out[0]["user_id"] == "3"


def test_members_now_team_role_missing():
    g = _guild([_member(1, "a", [TEAM]), _member(2, "b", [MOD, TEAM])], team_role_exists=False)
    assert [m["team_role"] for m in _members_now(g)] == [False, False]


def _inv(uid, day, joined=False):
    t = datetime.datetime(2026, 10, day, 12, 0, 0)
    return {"user_id": uid, "created_at": t, "joined_at": t if joined else None}


def test_waiting_excludes_arrived_and_present_and_dedupes():
    rows = [_inv(1, 1), _inv(1, 2),            # two pending for 1 -> one entry (latest)
            _inv(2, 1, joined=True),           # arrived
            _inv(3, 3),                        # present on server
            _inv(4, 1), _inv(4, 5, joined=True)]  # re-invited then arrived: latest joined
    out = team_access.waiting(rows, here_ids={3})
    assert out == [{"user_id": "1", "invited_at": "2026-10-02T12:00:00Z"}]


def _log(i, action, actor=None, day=1):
    return {"id": i, "created_at": datetime.datetime(2026, 10, day, 9, 0, 0), "hunt_id": 1,
            "user_id": 10 + i, "username": f"u{i}", "action": action, "via": "hand",
            "actor_id": actor, "detail": None}


def test_history_newest_first_with_actor_names():
    out = team_access.history(
        [_log(1, "grant", day=1), _log(2, "elevate", actor=777, day=2)],
        {777: "Moddy"}.get)
    assert [h["action"] for h in out] == ["elevate", "grant"]
    assert out[0]["actor_name"] == "Moddy" and out[0]["actor_id"] == "777"
    assert out[0]["at"] == "2026-10-02T09:00:00Z"
    assert out[1]["actor_id"] is None and out[1]["actor_name"] is None


def test_history_actor_not_on_hub_falls_back_to_id():
    out = team_access.history([_log(1, "elevate", actor=888)], lambda _i: None)
    assert out[0]["actor_name"] == "888"


def test_hub_team_hunts_keeps_only_this_hubs():
    hub = MagicMock()
    hub.get_role.side_effect = lambda i: _role(i) if i == 100 else None
    mine = SimpleNamespace(id=1, roster_role_id=100)
    other = SimpleNamespace(id=2, roster_role_id=200)
    none = SimpleNamespace(id=3, roster_role_id=0)
    assert team_access.hub_team_hunts(hub, [(mine, 5), (other, 6), (none, 7)]) == [(mine, 5)]

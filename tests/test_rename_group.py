from types import SimpleNamespace

import pytest

from bot.core import category_layout
from tests.conftest import make_round


class _FakeCat:
    def __init__(self, id, name, text_channels=()):
        self.id = id
        self.name = name
        self.text_channels = list(text_channels)


class _FakeChan:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class _FakeGuild:
    def __init__(self, categories=(), channels=()):
        self.categories = list(categories)
        self._channels = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self._channels.get(cid)


class _FakeDao:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def get_for_group(self, group_id):
        return self._rows


def test_plan_meta_single_category_renames_only_category():
    cat = _FakeCat(id=999, name="old-round")
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=1, name="old round", target_puzzle_id=7, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Round", _FakeDao())
    assert plan == [(cat, "new-round")]


def test_plan_metaless_renames_category_and_text_channel():
    chan = _FakeChan(id=500, name="old-group")
    cat = _FakeCat(id=999, name="old-group", text_channels=[chan])
    guild = _FakeGuild(categories=[cat])
    group = make_round(id=2, name="old group", target_puzzle_id=0, category_id=999)
    plan = category_layout.group_rename_plan(guild, group, "New Group", _FakeDao())
    assert plan == [(cat, "new-group"), (chan, "new-group")]


def test_plan_split_round_renames_all_categories():
    from bot.store.tag_group_category_data import ROLE_SOLVED, ROLE_UNSOLVED
    unsolved = _FakeCat(id=10, name="old-round")
    solved = _FakeCat(id=11, name="old-round — solved")
    guild = _FakeGuild(categories=[unsolved, solved])
    group = make_round(id=3, name="old round", target_puzzle_id=7, category_id=10)
    rows = [
        SimpleNamespace(category_id=10, role=ROLE_UNSOLVED, seq=0),
        SimpleNamespace(category_id=11, role=ROLE_SOLVED, seq=0),
    ]
    plan = category_layout.group_rename_plan(guild, group, "New Round", _FakeDao(rows))
    assert (unsolved, "new-round") in plan
    assert (solved, "new-round — solved") in plan
    assert len(plan) == 2

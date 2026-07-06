"""Regression cover for the category fold/un-split *trigger* — the seam between
maybe_consolidate() and the real consolidate() background run.

Two bugs lived here (neither in the fold logic itself, which its own unit tests
in test_category_layout.py cover):

  (c) maybe_consolidate() spawned consolidate() with a bare asyncio.create_task
      whose return every caller discarded. asyncio holds only a WEAK reference to
      such a task, so it could be garbage-collected before running — silently
      dropping the fold for BOTH round and general owners. Fixed by holding a
      strong reference until the task completes.

The end-to-end test uses a faithful guild model where a category's channel list
is DERIVED from each channel's current category_id, so channel.edit()/delete()
actually change what puzzle_count() sees (the mocked category_layout unit tests
don't exercise the real trigger→create_task→consolidate chain).
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from bot.core import category_layout as layout
from bot.store.tag_group_category_data import (
    TagGroupCategoryData, ROLE_UNSOLVED, ROLE_SOLVED,
)


# --------------------------------------------------------------------------
# Faithful guild model: category membership derives from channel.category_id
# --------------------------------------------------------------------------
class _Channel:
    def __init__(self, guild, cid, name, category_id, position):
        self.guild = guild
        self.id = cid
        self.name = name
        self.category_id = category_id
        self.position = position

    async def edit(self, category=None, position=None, **kw):
        if category is not None:
            self.category_id = category.id
        if position is not None:
            self.position = position

    async def delete(self, reason=None):
        self.guild._channels.pop(self.id, None)


class _Category:
    def __init__(self, guild, cid, name, position):
        self.guild = guild
        self.id = cid
        self.name = name
        self.position = position
        self.deleted = False

    @property
    def channels(self):
        return [c for c in self.guild._channels.values() if c.category_id == self.id]

    text_channels = channels

    async def edit(self, position=None, **kw):
        if position is not None:
            self.position = position

    async def clone(self, name=None):
        return self.guild._new_category(name or self.name, self.position)

    async def delete(self, reason=None):
        self.deleted = True
        self.guild._categories.pop(self.id, None)


class _Guild:
    def __init__(self):
        self._channels = {}
        self._categories = {}
        self._next_id = 5000

    def _mint(self):
        self._next_id += 1
        return self._next_id

    @property
    def categories(self):
        return [c for c in self._categories.values() if not c.deleted]

    def get_channel(self, cid):
        return self._channels.get(cid) or self._categories.get(cid)

    def add_category(self, cid, name, position):
        cat = _Category(self, cid, name, position)
        self._categories[cid] = cat
        return cat

    def _new_category(self, name, position):
        return self.add_category(self._mint(), name, position)

    def add_channel(self, cid, name, category_id, position):
        self._channels[cid] = _Channel(self, cid, name, category_id, position)
        return self._channels[cid]


class _Dao:
    def __init__(self, rows):
        self._rows = list(rows)

    def get_for_group(self, gid):
        return [r for r in self._rows if r.group_id == gid]

    def get_by_category(self, category_id):
        return next((r for r in self._rows if r.category_id == category_id), None)

    def delete(self, rid):
        self._rows = [r for r in self._rows if r.id != rid]

    def delete_for_group(self, gid):
        self._rows = [r for r in self._rows if r.group_id != gid]


class _MissDao:
    def get_by_category(self, category_id):
        return None


@pytest.fixture(autouse=True)
def _fresh_lock(monkeypatch):
    monkeypatch.setattr(layout, "layout_lock", asyncio.Lock())


def _install_dao(monkeypatch, dao):
    import bot.store as store
    monkeypatch.setattr(store, "TagGroupCategoryDb", dao, raising=False)
    monkeypatch.setattr(store, "HuntCategoryDb", _MissDao(), raising=False)


def _patch_divider_create(monkeypatch):
    async def fake_goc(guild=None, category=None, channel_name=None,
                       channel_type=None, position=None, **kw):
        ch = guild.add_channel(guild._mint(), channel_name, category.id, position or 0)
        return ch, True
    monkeypatch.setattr(layout, "get_or_create_channel", fake_goc)


# --------------------------------------------------------------------------
# (c) The spawned task must be strongly referenced until it completes.
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_maybe_consolidate_holds_strong_reference_until_done(monkeypatch):
    monkeypatch.setattr(layout, "layout_lock", asyncio.Lock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(guild, owner_id, dao):
        started.set()
        await release.wait()

    monkeypatch.setattr(layout, "_consolidate_safely", slow)
    # A seq>=1 row makes _consolidation_candidate True via its count-independent
    # shortcut, so no guild cache is needed to spawn the task.
    dao = MagicMock()
    dao.get_for_group.return_value = [
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role=ROLE_UNSOLVED, seq=1),
    ]
    layout._pending_consolidations.clear()

    task = layout.maybe_consolidate(MagicMock(), 5, dao)

    assert task is not None
    assert task in layout._pending_consolidations, "task not strongly referenced — GC could drop it"
    await started.wait()
    release.set()
    await task
    assert task not in layout._pending_consolidations, "done-callback did not release the reference"


# --------------------------------------------------------------------------
# End-to-end: the reported extract state folds + un-splits through the REAL
# trigger (maybe_consolidate_category → background task → consolidate).
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extract_leftover_split_folds_and_unsplits_via_real_trigger(monkeypatch):
    guild = _Guild()
    u0 = guild.add_category(100, "round-1", 5)             # unsolved seq0
    u1 = guild.add_category(101, "round-1 (2)", 6)         # unsolved seq1 (overflow)
    guild.add_category(102, "round-1 — solved", 7)         # solved seq0 (empty)
    for i in range(5):                                     # 5 in primary
        guild.add_channel(10 + i, f"p0-{i}", 100, i)
    guild.add_channel(20, "p1-0", 101, 0)                 # 1 in overflow

    dao = _Dao([
        TagGroupCategoryData(id=1, group_id=5, category_id=100, role=ROLE_UNSOLVED, seq=0),
        TagGroupCategoryData(id=2, group_id=5, category_id=101, role=ROLE_UNSOLVED, seq=1),
        TagGroupCategoryData(id=3, group_id=5, category_id=102, role=ROLE_SOLVED, seq=0),
    ])
    _install_dao(monkeypatch, dao)
    _patch_divider_create(monkeypatch)

    task = layout.maybe_consolidate_category(guild, u0)
    assert task is not None, "candidate returned False — no task spawned"
    await task

    assert u1.deleted, "overflow category was not folded away"
    assert dao._rows == [], "did not un-split back to a single-category owner"

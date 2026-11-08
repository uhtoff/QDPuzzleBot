"""M28: the bang create edges hold hunt_create_lock around check-then-create.

`create_puzzle`/`create_tag_group` do NOT lock internally (bot/core/ is lock-free
by convention; callers lock), and both creates await Discord/Sheets for seconds
between the duplicate check and the row insert. Without hunt_create_lock two
near-simultaneous creates therefore both pass the check and both insert — which is
exactly why the web routes (POST /hunts/<uid>/puzzles, /hunts/<uid>/metas) hold it.

LIVE MySQL (puzzlebot_test) — the duplicate check and the INSERT it guards are both
real here, because a mocked DAO cannot show one create's row being invisible to the
other's check. Rows are prefixed ``LockM28_`` and deleted in a ``finally``.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.store import HuntJsonDb, mydb
from tests.conftest import live_hunt_id

PUZZLE_NAME = "LockM28_dup_puzzle"
GROUP_NAME = "LockM28_dup_group"


# --------------------------------------------------------------- live helpers
def _count_named(table, name):
    """How many rows of OUR test name exist in the live hunt. Scoped to the name
    and hunt on purpose — never a whole-table scan."""
    cur = mydb.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM `{table}` WHERE hunt_id = %s AND name = %s",
        (live_hunt_id(), name),
    )
    count = cur.fetchone()[0]
    cur.close()
    return count


def _delete_named(table, name):
    cur = mydb.cursor()
    cur.execute(
        f"DELETE FROM `{table}` WHERE hunt_id = %s AND name = %s",
        (live_hunt_id(), name),
    )
    cur.close()


def _live_hunt():
    """The real hunts row, with the two fields these paths branch on pinned in
    memory (never committed) so the test doesn't depend on the row's contents."""
    hunt = HuntJsonDb.get_by_attr(id=live_hunt_id())
    hunt.url = None
    hunt.category_id = 990001
    return hunt


def _make_ctx(message_id, guild):
    ctx = MagicMock()
    ctx.message.id = message_id
    ctx.send = AsyncMock()
    ctx.guild = guild
    ctx.env = None
    return ctx


def _seed_environment(cog, ctx, hunt):
    cog.environment[ctx.message.id] = {
        "channel_type": "Hunt",
        "hunt": hunt,
        "tag_group": None,     # no round in context -> an unassigned puzzle
        "puzzle": None,        # a hunt channel has no puzzle, so no puzzle lock
        "guild": ctx.guild,
        "guild_data": None,
        "gsheet_cog": None,
    }


def _make_cog():
    cog = Puzzles(MagicMock())
    cog.check_available_space = AsyncMock(return_value=True)
    cog.service.refresh_unassigned_safe = AsyncMock()
    cog.service.refresh_target = AsyncMock()
    # The auto-extract-on-overflow probe walks real Discord categories; it is not
    # part of the check-then-create window under test.
    cog.service._maybe_relieve_overflow = AsyncMock()
    return cog


def _null_notifier():
    """Patch target for the DiscordNotifier both create paths build themselves."""
    notifier = MagicMock()
    notifier.welcome = AsyncMock()
    notifier.board_changed = AsyncMock()
    return patch("bot.core.notifier.DiscordNotifier", MagicMock(return_value=notifier))


async def _wait_for(event, task, what):
    """Wait for the create to reach its parking point, surfacing the task's own
    exception instead of hanging forever if it blew up on the way there."""
    done, _pending = await asyncio.wait(
        [asyncio.create_task(event.wait()), task],
        timeout=5, return_when=asyncio.FIRST_COMPLETED,
    )
    if event.is_set():
        return
    if task in done:
        task.result()          # re-raises whatever killed it
    raise AssertionError(f"create never reached {what}")


# ------------------------------------------------------------------ !puzzle
@pytest.mark.asyncio
async def test_two_concurrent_bang_puzzle_creates_make_one_puzzle():
    """The check and the INSERT are separated by the Sheets await, so two !p's
    with the same name both pass the check unless the edge serializes them."""
    hunt = _live_hunt()
    cog = _make_cog()

    # Park both creates inside the Sheets call — the real seconds-long await that
    # sits between check_duplicates_in_hunt and PuzzleJsonDb.commit.
    in_sheets = asyncio.Event()
    may_finish = asyncio.Event()

    async def slow_sheet(*args, **kwargs):
        in_sheets.set()
        await may_finish.wait()

    gsheet = MagicMock()
    gsheet.compute_insert_index = AsyncMock(return_value=0)
    gsheet.create_puzzle_spreadsheet = AsyncMock(side_effect=slow_sheet)
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    cog.bot.get_cog.return_value = gsheet

    guild = MagicMock()
    guild.channels = []
    guild.categories = []
    category = MagicMock(id=990002)
    category.channels = []

    def _channel(_n=[0]):
        _n[0] += 1
        channel = MagicMock(id=990100 + _n[0], mention=f"<#{990100 + _n[0]}>")
        # MagicMock(name=...) sets the mock's repr, not the attribute the code
        # stores as channel_name (docs/agents/python.md).
        channel.name = f"lockm28-{_n[0]}"
        return channel

    try:
        with _null_notifier(), \
             patch("bot.core.puzzle_service.category_layout.pick_unsolved_category",
                   AsyncMock(return_value=category)), \
             patch("bot.core.puzzle_service.get_or_create_channel",
                   AsyncMock(side_effect=lambda **kw: (_channel(), True))):
            ctx_a = _make_ctx(660001, guild)
            ctx_b = _make_ctx(660002, guild)
            _seed_environment(cog, ctx_a, hunt)
            _seed_environment(cog, ctx_b, hunt)

            a = asyncio.create_task(Puzzles.puzzle.callback(cog, ctx_a, arg=PUZZLE_NAME))
            await _wait_for(in_sheets, a, "the sheet call")  # A is past its dup check
            b = asyncio.create_task(Puzzles.puzzle.callback(cog, ctx_b, arg=PUZZLE_NAME))
            # Let B run as far as it can get: past its own duplicate check
            # (unserialised) or up to the create lock (serialised).
            for _ in range(10):
                await asyncio.sleep(0)
            may_finish.set()
            await asyncio.gather(a, b)

        assert _count_named("puzzles", PUZZLE_NAME) == 1, (
            "both !puzzle creates passed the duplicate check and inserted a row: "
            "the check and the create are not one critical section"
        )
    finally:
        _delete_named("puzzles", PUZZLE_NAME)


# -------------------------------------------------------------------- !group
@pytest.mark.asyncio
async def test_two_concurrent_bang_group_creates_make_one_group():
    """create_tag_group runs its own duplicate check internally, so the WHOLE
    call has to be the critical section (as the web /metas route documents)."""
    hunt = _live_hunt()
    cog = _make_cog()

    in_clone = asyncio.Event()
    may_finish = asyncio.Event()

    async def slow_clone(*args, **kwargs):
        # The category clone is the first Discord await after the duplicate
        # check inside create_tag_group.
        in_clone.set()
        await may_finish.wait()
        new_category = MagicMock(id=990003)
        new_category.edit = AsyncMock()
        new_category.channels = []
        return new_category

    def _group_channel(_n=[0]):
        _n[0] += 1
        channel = MagicMock(id=990200 + _n[0], mention=f"<#{990200 + _n[0]}>")
        channel.name = f"lockm28-group-{_n[0]}"
        return channel

    ref_category = MagicMock(id=990001, position=0)
    ref_category.name = "LockM28_ref"
    ref_category.clone = AsyncMock(side_effect=slow_clone)
    guild = MagicMock()
    guild.channels = []
    guild.categories = [ref_category]

    try:
        with _null_notifier(), \
             patch("bot.core.puzzle_service.get_or_create_channel",
                   AsyncMock(side_effect=lambda **kw: (_group_channel(), True))):
            ctx_a = _make_ctx(660011, guild)
            ctx_b = _make_ctx(660012, guild)
            ctx_a.invoked_with = "group"
            ctx_b.invoked_with = "group"
            _seed_environment(cog, ctx_a, hunt)
            _seed_environment(cog, ctx_b, hunt)

            a = asyncio.create_task(Puzzles.round.callback(cog, ctx_a, arg=GROUP_NAME))
            await _wait_for(in_clone, a, "the category clone")  # A is past its dup check
            b = asyncio.create_task(Puzzles.round.callback(cog, ctx_b, arg=GROUP_NAME))
            for _ in range(10):
                await asyncio.sleep(0)
            may_finish.set()
            await asyncio.gather(a, b)

        assert _count_named("tag_group", GROUP_NAME) == 1, (
            "both !group creates passed create_tag_group's duplicate check and "
            "inserted a row: the check and the create are not one critical section"
        )
    finally:
        _delete_named("tag_group", GROUP_NAME)

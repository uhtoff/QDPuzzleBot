"""M6: hunt lookups and the hunt-name namespace must be guild-scoped.

`!set_hunt_role` used to resolve its hunt with a bare `WHERE name = %s` (no
guild filter), so an admin in one guild could silently write a Discord role
onto another guild's same-named hunt. `check_duplicates` had the same bug for
`!hunt`'s duplicate guard, making hunt names one namespace across every guild
the bot serves.

`hunts.guild_id` FKs `guilds.id` (the guilds table's own row id), NOT the
Discord snowflake -- see bot/web/notifier.py's board_changed docstring for the
same confusion causing a silent failure elsewhere. Scoping by the invoking
guild's snowflake therefore needs a join through `guilds`, which is what
MySQLHuntJsonDb.get_by_name_for_guild / check_duplicates(guild_id=...) do.

LIVE MySQL (puzzlebot_test), following the live-DB convention of
tests/test_hunt_category_cascade.py -- the point is proving the SQL join
resolves the *right* row, which a mocked cursor can't demonstrate.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs.puzzles import Puzzles
from bot.store import HuntJsonDb, mydb
from tests.conftest import make_cog, make_ctx


def _insert_guild(guild_snowflake):
    cur = mydb.cursor()
    cur.execute("INSERT INTO guilds (guild_id) VALUES (%s)", (guild_snowflake,))
    guild_row_id = cur.lastrowid
    cur.close()
    return guild_row_id


def _insert_hunt(name, guild_row_id):
    cur = mydb.cursor()
    cur.execute("INSERT INTO hunts (name, guild_id) VALUES (%s, %s)", (name, guild_row_id))
    hunt_id = cur.lastrowid
    cur.close()
    return hunt_id


def _delete_hunt(hunt_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM hunts WHERE id = %s", (hunt_id,))
    cur.close()


def _delete_guild(guild_row_id):
    cur = mydb.cursor()
    cur.execute("DELETE FROM guilds WHERE id = %s", (guild_row_id,))
    cur.close()


@pytest.mark.asyncio
async def test_set_hunt_role_resolves_the_inviting_guilds_hunt():
    """LOAD-BEARING: two guilds both hold a hunt named 'Mystery'. Guild A's row
    is inserted first (lower id), so an unscoped `ORDER BY id ASC LIMIT 1`
    lookup (the old `get_by_attr(name=...)`) would always resolve guild A's
    hunt regardless of who asked -- including when guild B is the one running
    the command. Only a guild-scoped lookup tells the two apart; with just one
    guild holding the name, the scoped and unscoped queries agree and this
    would prove nothing.
    """
    guild_a = _insert_guild(910001)
    guild_b = _insert_guild(910002)
    hunt_a = _insert_hunt("Mystery", guild_a)
    hunt_b = _insert_hunt("Mystery", guild_b)
    try:
        cog = make_cog()
        cog.check_is_bot_channel = AsyncMock(return_value=True)
        cog.service.set_hunt_role = AsyncMock(return_value=[])
        ctx = make_ctx()
        ctx.guild.id = 910002  # guild B is the one invoking the command

        await Puzzles.set_hunt_role.callback(cog, ctx, arg="Mystery, TeamA")

        cog.service.set_hunt_role.assert_awaited_once()
        resolved_hunt = cog.service.set_hunt_role.await_args.args[1]
        assert resolved_hunt.id == hunt_b, (
            "resolved guild A's hunt instead of the invoking guild's (guild B's) hunt"
        )
    finally:
        _delete_hunt(hunt_a)
        _delete_hunt(hunt_b)
        _delete_guild(guild_a)
        _delete_guild(guild_b)


@pytest.mark.asyncio
async def test_set_hunt_role_refuses_when_no_hunt_in_this_guild():
    """No silent fallback to a global lookup: a name that exists only in
    another guild must not resolve here."""
    guild_a = _insert_guild(910003)
    guild_b = _insert_guild(910004)
    hunt_a = _insert_hunt("Odyssey", guild_a)
    try:
        cog = make_cog()
        cog.check_is_bot_channel = AsyncMock(return_value=True)
        cog.service.set_hunt_role = AsyncMock()
        ctx = make_ctx()
        ctx.guild.id = 910004  # guild B has no hunt named "Odyssey"

        await Puzzles.set_hunt_role.callback(cog, ctx, arg="Odyssey, TeamA")

        cog.service.set_hunt_role.assert_not_awaited()
        ctx.send.assert_awaited_once()
        assert "Odyssey" in ctx.send.await_args.args[0]
    finally:
        _delete_hunt(hunt_a)
        _delete_guild(guild_a)
        _delete_guild(guild_b)


def test_check_duplicates_allows_the_same_name_in_two_guilds():
    guild_a = _insert_guild(910005)
    guild_b = _insert_guild(910006)
    hunt_a = _insert_hunt("Nightfall", guild_a)
    try:
        assert HuntJsonDb.check_duplicates("Nightfall", guild_id=910005) is True
        assert HuntJsonDb.check_duplicates("Nightfall", guild_id=910006) is False, (
            "a name used in one guild must not block the same name in another"
        )
    finally:
        _delete_hunt(hunt_a)
        _delete_guild(guild_a)
        _delete_guild(guild_b)


def test_check_duplicates_still_refuses_a_duplicate_within_one_guild():
    """The keeper: without this, a check_duplicates that always returns False
    would also make the cross-guild test above pass."""
    guild_a = _insert_guild(910007)
    hunt_a = _insert_hunt("Nightfall", guild_a)
    try:
        assert HuntJsonDb.check_duplicates("Nightfall", guild_id=910007) is True
    finally:
        _delete_hunt(hunt_a)
        _delete_guild(guild_a)


# ---------------------------------------------------------------------------
# The service-layer half. Mocked rather than live: create_hunt drives Discord
# category/channel creation, so the DAO is the only part worth exercising here.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("invoking_snowflake, expected", [
    (222, "existing_role_confirm"),   # name taken in ANOTHER guild -> proceeds
    (111, "hunt_exists"),             # name taken in THIS guild    -> refused
])
async def test_create_hunt_scopes_its_duplicate_check_to_the_invoking_guild(
        invoking_snowflake, expected):
    """The cog's !hunt pre-check was scoped, but service.create_hunt still ran
    the GLOBAL check -- so the command would accept a cross-guild duplicate and
    the service would then refuse it with hunt_exists. Half-applied is worse
    than not applied: the two layers disagreed.

    The fake mirrors the real DAO: with no guild_id it answers globally (the old
    behaviour), with one it answers per guild. So the cross-guild case goes red
    against an unscoped call site, which is exactly the regression.

    "existing_role_confirm" is the sentinel because it is the one RuntimeError
    create_hunt re-raises rather than swallowing -- reaching it proves the
    duplicate check let us past.
    """
    from bot.core.puzzle_service import PuzzleService

    OWNER = 111
    TAKEN = "Mystery Hunt"

    def fake_check(value, field="name", guild_id=None):
        if guild_id is None:
            return value == TAKEN                      # old global behaviour
        return guild_id == OWNER and value == TAKEN

    bot = MagicMock()
    bot.get_cog.return_value = None
    svc = PuzzleService(bot)
    guild = MagicMock()
    guild.id = invoking_snowflake

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch.object(svc, "_resolve_gating_role",
                      new=AsyncMock(side_effect=RuntimeError("existing_role_confirm"))):
        hdb.check_duplicates.side_effect = fake_check
        with pytest.raises(RuntimeError) as exc:
            await svc.create_hunt(guild, name=TAKEN, url=None, start_timestamp=0)

    assert expected in str(exc.value), str(exc.value)

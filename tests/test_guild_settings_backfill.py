"""Regression tests for missing guild settings rows.

A `guilds` row is only created in `Settings.on_guild_join`, which fires solely
at the moment the bot joins a server. Guilds the bot was already a member of
when the code/DB was deployed therefore had no row, so
`GuildSettingsDb.get_cached(guild_id)` returned None and the first caller to
dereference it crashed:

    AttributeError: 'NoneType' object has no attribute 'discord_bot_channel'
    (bot/cogs/puzzles.py check_is_bot_channel)

Fix: backfill rows for guilds the bot is already in at startup, and keep the
settings cache coherent after a commit so a freshly-created row is not shadowed
by a previously cached None.
"""

from unittest.mock import MagicMock, patch

from bot.cogs.settings import Settings
from bot.store.mysqldb import MySQLGuildSettingsDb
from bot.store.puzzle_settings import GuildSettings


def _make_guild(gid, name):
    g = MagicMock()
    g.id = gid
    g.name = name
    return g


async def test_on_guild_join_creates_row_when_missing():
    cog = Settings(MagicMock())
    guild = _make_guild(5, "Hunt HQ")
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    db.commit.assert_called_once()
    committed = db.commit.call_args[0][0]
    assert committed.guild_id == 5
    assert committed.guild_name == "Hunt HQ"


async def test_on_guild_join_is_idempotent_when_row_exists():
    cog = Settings(MagicMock())
    guild = _make_guild(5, "Hunt HQ")
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = GuildSettings(guild_id=5)
        await cog.on_guild_join(guild)
    db.commit.assert_not_called()


async def test_on_ready_backfills_only_guilds_missing_a_row():
    bot = MagicMock()
    bot.guilds = [_make_guild(1, "a"), _make_guild(2, "b")]
    cog = Settings(bot)
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.side_effect = lambda gid: GuildSettings(guild_id=2) if gid == 2 else None
        await cog.on_ready()
    db.commit.assert_called_once()
    assert db.commit.call_args[0][0].guild_id == 1


def test_commit_refreshes_cache_so_new_row_not_shadowed_by_none():
    mydb = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 7
    mydb.cursor.return_value = cursor
    db = MySQLGuildSettingsDb(mydb=mydb)
    # A prior get_cached() found no row and cached None.
    db.cached_settings[42] = None

    g = GuildSettings(guild_id=42, guild_name="late join")
    db.commit(g)

    # get_cached must now see the committed row, not the stale None.
    assert db.get_cached(42) is g


# --- The setup message on join ------------------------------------------------
# A brand-new guild is UNPINNED by definition (admin_role_ids is NULL on the row
# we have just created), so it is in the name-matching state where anyone with
# Manage Roles can mint a role called `admin` and self-grant. Nothing surfaced
# that: the playbook is a doc, !show_auth_roles only answers if you think to ask.
# The join message is the one moment whoever added the bot is definitely looking.

def _guild_with_channels(gid=5, name="Hunt HQ", system=True, writable=True):
    guild = _make_guild(gid, name)
    guild.me = MagicMock()
    channel = MagicMock()
    channel.send = _AsyncNoop()
    perms = MagicMock()
    perms.send_messages = writable
    channel.permissions_for.return_value = perms
    guild.system_channel = channel if system else None
    guild.text_channels = [channel]
    return guild, channel


class _AsyncNoop:
    """An awaitable stub that records its calls (AsyncMock in disguise, kept
    local so this file needs no extra import)."""

    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


async def test_on_guild_join_posts_the_setup_message():
    cog = Settings(MagicMock())
    guild, channel = _guild_with_channels()
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    assert channel.send.calls, "expected a setup message in the system channel"
    text = channel.send.calls[0][0][0]
    assert "!set_admin_roles" in text


async def test_setup_message_names_the_two_manual_steps():
    """Pinning the admin roles and the hunt-category role overwrite are the two
    steps nothing in the code performs."""
    cog = Settings(MagicMock())
    guild, channel = _guild_with_channels()
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    text = channel.send.calls[0][0][0].lower()
    assert "overwrite" in text
    assert "manage roles" in text          # the risk being described


async def test_setup_message_falls_back_when_there_is_no_system_channel():
    cog = Settings(MagicMock())
    guild, channel = _guild_with_channels(system=False)
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    assert channel.send.calls, "expected the fallback channel to be used"


async def test_join_never_raises_when_the_bot_cannot_post():
    """A guild where the bot can write nowhere must still get its settings row —
    the message is best-effort and must not abort the join handler."""
    cog = Settings(MagicMock())
    guild, channel = _guild_with_channels(system=False, writable=False)
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    db.commit.assert_called_once()
    assert not channel.send.calls


async def test_send_failure_does_not_abort_the_join():
    cog = Settings(MagicMock())
    guild, channel = _guild_with_channels()

    async def boom(*_a, **_kw):
        raise RuntimeError("missing permissions")

    channel.send = boom
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = None
        await cog.on_guild_join(guild)
    db.commit.assert_called_once()


async def test_existing_guild_is_not_re_greeted():
    """on_ready backfills rows for guilds we were already in; a restart must not
    spam an established server with the setup message."""
    cog = Settings(MagicMock())
    guild, channel = _guild_with_channels()
    with patch("bot.cogs.settings.GuildSettingsDb") as db:
        db.get.return_value = GuildSettings()   # row already exists
        await cog.on_guild_join(guild)
    assert not channel.send.calls
